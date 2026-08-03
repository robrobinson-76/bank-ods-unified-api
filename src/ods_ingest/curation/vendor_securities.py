"""Curation: vendor security-master rows -> curated `securities`.

The vendor feed is the messiest source in the prototype, and deliberately so —
its documented inconsistencies (three generations of asset-class codes, country
and currency casing drift, "#N/A" from failed lookups, sentinel dates) are
exactly the kind of thing that must be normalized in one reviewable place
rather than smeared across consumers.

This curator only ever ENRICHES securities the ODS already knows. It does not
create instruments from a vendor feed: the security master is authoritative
elsewhere, and a vendor row that matches nothing is a reconciliation finding,
not licence to invent a security.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pymongo import UpdateOne

from bank_ods.models.security import Security
from bank_ods.services._common import cusip_from_isin

from ods_ingest import state
from ods_ingest.bus.consumer import BatchConsumer, ConsumedRecord
from ods_ingest.curation.base import CurationStats, utc_now

log = logging.getLogger("ods_ingest.curation.vendorsec")

TOPIC = "ods.raw.vendorsec.securities"
GROUP_ID = "ods-curation-vendor-securities"

# Values that mean "the vendor had nothing", spelled several ways.
NULL_SENTINELS = {"", "N/A", "#N/A", "NA", "NULL", "NONE", "-"}

# The vendor's status vocabulary has drifted across three generations.
STATUS_ACTIVE = {"A", "ACT", "ACTIVE"}
STATUS_MATURED = {"M", "MAT", "MATURED", "MAT'D"}
STATUS_DELISTED = {"D", "DEL", "DELISTED", "INACTIVE", "I"}


def clean(value: Optional[str]) -> Optional[str]:
    """Vendor string to a real value, or None for any spelling of 'nothing'."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.upper() in NULL_SENTINELS else text


def map_status(raw: Optional[str]) -> Optional[str]:
    """Vendor issue status -> the ODS status enum.

    Returns None when the vendor says something we do not recognise: leaving
    the curated status untouched is safer than guessing DELISTED, which would
    remove an instrument from view.
    """
    text = (clean(raw) or "").upper()
    if text in STATUS_ACTIVE:
        return "ACTIVE"
    if text in STATUS_MATURED:
        return "MATURED"
    if text in STATUS_DELISTED:
        return "DELISTED"
    return None


class SecurityIndex:
    """Resolves a vendor row to a curated securityId."""

    def __init__(self) -> None:
        self.by_cusip: dict[str, str] = {}
        self.by_isin: dict[str, str] = {}
        self.by_sedol: dict[str, str] = {}

    @classmethod
    def load(cls) -> "SecurityIndex":
        index = cls()
        for doc in state.get_db()["securities"].find(
            {}, {"securityId": 1, "cusip": 1, "isin": 1, "listings.sedol": 1}
        ):
            sid = doc["securityId"]
            if doc.get("cusip"):
                index.by_cusip[doc["cusip"]] = sid
            if doc.get("isin"):
                index.by_isin[doc["isin"]] = sid
                embedded = cusip_from_isin(doc["isin"])
                if embedded:
                    index.by_cusip.setdefault(embedded, sid)
            for listing in doc.get("listings") or []:
                if listing.get("sedol"):
                    index.by_sedol[listing["sedol"]] = sid
        return index

    def resolve(self, row: dict) -> Optional[str]:
        """ISIN, then CUSIP, then SEDOL — most to least globally unique.

        The vendor's CUSIP may have lost a leading zero in a spreadsheet, so a
        zero-padded retry is worth one lookup.
        """
        isin = clean(row.get("ISIN_CODE"))
        if isin and isin in self.by_isin:
            return self.by_isin[isin]

        cusip = clean(row.get("Cusip"))
        if cusip:
            if cusip in self.by_cusip:
                return self.by_cusip[cusip]
            padded = cusip.zfill(9)
            if padded in self.by_cusip:
                return self.by_cusip[padded]

        sedol = clean(row.get("sedol"))
        if sedol and sedol in self.by_sedol:
            return self.by_sedol[sedol]
        return None


def curate_record(row: dict, index: SecurityIndex, stats: CurationStats) -> Optional[UpdateOne]:
    vendor_ref = row.get("Vendor_Ref")
    security_id = index.resolve(row)
    if security_id is None:
        # A real vendor row for an instrument the ODS does not carry. Not an
        # error — reconciliation reports these.
        stats.skip("UNMATCHED_VENDOR_RECORD")
        return None

    updates: dict[str, Any] = {"updatedAt": utc_now()}

    # Only fields the vendor is authoritative for, and only when it actually
    # supplied one. A blank from the vendor must never blank the master.
    issuer = clean(row.get("Issuer_Name"))
    if issuer:
        updates["issuer"] = issuer

    status = map_status(row.get("ISSUE_STATUS"))
    if status:
        updates["status"] = status

    # This entity is fed by two channels — the intraday poll and the
    # start-of-day snapshot — with no ordering guarantee between them. Without
    # the guard below, a stale snapshot record arriving after a fresh intraday
    # update would silently overwrite it, and the slower the true-up runs the
    # wider that window gets. The guard makes the outcome depend on the source's
    # own timestamp rather than on arrival order.
    incoming = row.get("SRC_UPDATED_AT")
    if not incoming:
        # Unorderable: apply only if the security has never been stamped, so an
        # undated record can never displace a dated one.
        stats.curated += 1
        return UpdateOne(
            {"securityId": security_id, "vendorUpdatedAt": {"$exists": False}},
            {"$set": updates},
        )

    updates["vendorUpdatedAt"] = incoming
    stats.curated += 1
    log.debug("vendor %s enriched %s @ %s", vendor_ref, security_id, incoming)
    return UpdateOne(
        {
            "securityId": security_id,
            "$or": [
                {"vendorUpdatedAt": {"$lt": incoming}},
                {"vendorUpdatedAt": {"$exists": False}},
            ],
        },
        {"$set": updates},
    )


def curate_batch(records: list[ConsumedRecord], index: SecurityIndex,
                 stats: CurationStats) -> int:
    operations = []
    for record in records:
        stats.seen += 1
        op = curate_record(record.value, index, stats)
        if op is not None:
            operations.append(op)
    if not operations:
        return 0
    result = state.get_db()[Security.COLLECTION].bulk_write(operations, ordered=False)
    return result.matched_count


def run(once: bool = True, idle_timeout: Optional[float] = None,
        group_id: str = GROUP_ID) -> CurationStats:
    index = SecurityIndex.load()
    stats = CurationStats()

    def handler(records: list[ConsumedRecord]) -> int:
        return curate_batch(records, index, stats)

    consumer = BatchConsumer([TOPIC], group_id=group_id, handler=handler, stage="curation")
    try:
        if once:
            consumer.run_until_idle(idle_timeout)
        else:
            consumer.run_forever()
    finally:
        consumer.close()
    log.info("vendor securities curation: %s", stats.as_dict())
    return stats
