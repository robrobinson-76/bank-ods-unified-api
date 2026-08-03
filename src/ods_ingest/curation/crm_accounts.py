"""Curation: CRM change events -> curated `accounts`.

This is the hardest curator, for two reasons the architecture chose deliberately:

**Convergence.** Clients and accounts arrive on separate topics with no
cross-entity ordering guarantee. A client update may be curated before the
account it belongs to exists, or long after. So neither leg assumes the other
has run: an account event embeds whatever client state is known (falling back
to a placeholder), and a client event fans its snapshot out to every account
already carrying that clientId. Whichever order they arrive in, the end state
is the same.

**Soft delete.** The ODS is a read-only, append-flavoured view; documents are
never physically removed. A source delete becomes a status transition —
`CLOSED` with a closeDate — and the delete event itself survives in the raw
tier for audit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import UpdateMany, UpdateOne

from bank_ods.models.account import Account

from ods_ingest import state
from ods_ingest.bus.consumer import BatchConsumer, ConsumedRecord
from ods_ingest.curation.base import CurationStats, utc_now
from ods_ingest.sink.extractors import ExtractError, debezium

log = logging.getLogger("ods_ingest.curation.crm")

TOPICS = ["ods.raw.crm.clients", "ods.raw.crm.accounts"]
GROUP_ID = "ods-curation-crm-accounts"

# Source code lists -> ODS enums. The CRM has its own vocabulary; mapping it is
# curation's job, and an unrecognised value must never produce an invalid
# document — each mapping has an explicit fallback.
CLASSIFICATION = {
    "retail": "RETAIL",
    "professional": "PROFESSIONAL",
    "eligible_counterparty": "ELIGIBLE_COUNTERPARTY",
}
KYC_STATUS = {
    "approved": "APPROVED",
    "pending_review": "PENDING_REVIEW",
    "expired": "EXPIRED",
}
RISK_RATING = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
LEGAL_ENTITY_TYPE = {
    "corporation": "CORPORATION", "partnership": "PARTNERSHIP", "fund": "FUND",
    "trust": "TRUST", "government": "GOVERNMENT", "individual": "INDIVIDUAL",
}
ACCOUNT_TYPE = {"custody": "CUSTODY", "proprietary": "PROPRIETARY", "omnibus": "OMNIBUS"}
ACCOUNT_STATUS = {"active": "ACTIVE", "suspended": "SUSPENDED", "closed": "CLOSED"}

OP_DELETE = "d"


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """CRM dates arrive as the source's own 'CCYY-MM-DD', sometimes with a time.

    Unparseable values return None rather than raising: a malformed date on one
    account must not stop the batch, and the raw tier keeps whatever the source
    actually sent.
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        # Handles both the plain date and the ISO forms, with or without a zone.
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_time(event: dict) -> datetime:
    """Source commit time, used as the effective time of a soft delete."""
    try:
        return datetime.fromtimestamp(int(event["TS_MS"]) / 1000, tz=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return utc_now()


def client_master_from(state_row: dict[str, Any]) -> dict:
    """CRM client row -> the embedded ClientMaster snapshot.

    Every account of a client carries an identical copy (an invariant
    tests/test_master_data.py asserts), which is why a client change has to fan
    out rather than update one document.
    """
    residencies = [
        r.strip() for r in (state_row.get("tax_residencies") or "").split(",") if r.strip()
    ]
    domicile = (state_row.get("country_domicile") or "").upper()
    if domicile and domicile not in residencies:
        # The domicile is always a tax residency; the source does not enforce it.
        residencies.insert(0, domicile)

    return {
        "clientId": state_row["client_id"],
        "clientName": state_row.get("client_name") or state_row["client_id"],
        "lei": state_row.get("lei") or "",
        "countryOfDomicile": domicile,
        "countryOfIncorporation": (state_row.get("country_incorp") or domicile).upper(),
        "taxResidencies": residencies or [domicile],
        "classification": CLASSIFICATION.get(
            (state_row.get("classification") or "").lower(), "RETAIL"),
        "kycStatus": KYC_STATUS.get((state_row.get("kyc_status") or "").lower(), "PENDING_REVIEW"),
        "riskRating": RISK_RATING.get((state_row.get("risk_rating") or "").lower(), "MEDIUM"),
        "legalEntityType": LEGAL_ENTITY_TYPE.get(
            (state_row.get("legal_entity_type") or "").lower(), "CORPORATION"),
        "parentClientId": state_row.get("parent_client_id"),
    }


def _placeholder_client(client_id: str) -> dict:
    """Minimal client snapshot for an account whose client hasn't arrived yet.

    Deliberately valid-but-obviously-incomplete: the account is queryable
    immediately, and the client event's fan-out completes it whenever it lands.
    """
    return {
        "clientId": client_id,
        "clientName": client_id,
        "lei": "",
        "countryOfDomicile": "",
        "countryOfIncorporation": "",
        "taxResidencies": [],
        "classification": "RETAIL",
        "kycStatus": "PENDING_REVIEW",
        "riskRating": "MEDIUM",
        "legalEntityType": "CORPORATION",
        "parentClientId": None,
    }


def latest_client_state(client_id: str) -> Optional[dict]:
    """Newest known state for a client, from the raw change-event log.

    Reading the raw tier rather than a cache keeps the curator convergent: if
    the client event landed in an earlier run, or lands between batches, the
    account leg still finds it.
    """
    doc = state.get_db()["raw_crm_client_events"].find_one(
        {"PK": client_id, "AFTER": {"$ne": None}},
        sort=[("LSN", -1)],
    )
    return doc["AFTER"] if doc else None


def curate_client_event(event: dict, stats: CurationStats) -> list[Any]:
    """A client change fans out to every account embedding that client."""
    client_id = event.get("PK")
    if not client_id:
        stats.skip("MISSING_PK")
        return []

    if event.get("OP") == OP_DELETE:
        # Offboarding: close every account of the client. The embedded snapshot
        # is retained deliberately — it is the record of who the client was.
        stats.curated += 1
        return [UpdateMany(
            {"client.clientId": client_id},
            {"$set": {"status": "CLOSED", "closeDate": _event_time(event),
                      "updatedAt": utc_now()}},
        )]

    after = event.get("AFTER")
    if not after:
        stats.skip("NO_AFTER_IMAGE")
        return []

    snapshot = client_master_from(after)
    stats.curated += 1
    return [UpdateMany(
        {"client.clientId": client_id},
        {"$set": {"client": snapshot, "updatedAt": utc_now()}},
    )]


def curate_account_event(event: dict, stats: CurationStats) -> list[Any]:
    """An account change upserts the account, embedding the best-known client."""
    account_nbr = event.get("PK")
    if not account_nbr:
        stats.skip("MISSING_PK")
        return []

    if event.get("OP") == OP_DELETE:
        stats.curated += 1
        return [UpdateOne(
            {"accountId": account_nbr},
            {"$set": {"status": "CLOSED", "closeDate": _event_time(event),
                      "updatedAt": utc_now()}},
        )]

    after = event.get("AFTER")
    if not after:
        stats.skip("NO_AFTER_IMAGE")
        return []

    client_id = after.get("client_id")
    if not client_id:
        stats.skip("MISSING_CLIENT")
        return []

    known = latest_client_state(client_id)
    client_snapshot = client_master_from(known) if known else _placeholder_client(client_id)

    open_date = _parse_date(after.get("open_date")) or utc_now()
    close_date = _parse_date(after.get("close_date"))
    status = ACCOUNT_STATUS.get((after.get("status") or "").lower(), "ACTIVE")

    doc = {
        "accountId": account_nbr,
        "accountName": after.get("account_name") or account_nbr,
        "accountType": ACCOUNT_TYPE.get((after.get("account_type") or "").lower(), "CUSTODY"),
        "client": client_snapshot,
        "baseCurrency": after.get("base_ccy") or "USD",
        "status": status,
        "openDate": open_date,
        "closeDate": close_date,
        "custodianBranch": after.get("branch") or "",
        "updatedAt": utc_now(),
    }

    stats.curated += 1
    return [UpdateOne(
        {"accountId": account_nbr},
        {"$set": doc, "$setOnInsert": {"createdAt": utc_now()}},
        upsert=True,
    )]


def curate_batch(records: list[ConsumedRecord], stats: CurationStats) -> int:
    """Apply a batch of CRM events.

    Curators consume the raw TOPIC, so the payload here is Debezium's change
    envelope — the same bytes the sink lands. Both therefore run the same
    extractor, so "what was landed" and "what was curated" can never be built
    from different readings of the same record.

    Account events are applied before client events within a batch so that a
    client fan-out in the same batch reaches accounts created by it. Across
    batches order is not guaranteed, which is exactly why the client leg is a
    fan-out over whatever accounts exist rather than a one-shot join.
    """
    account_ops: list[Any] = []
    client_ops: list[Any] = []

    for record in records:
        stats.seen += 1
        table = record.topic.rsplit(".", 1)[-1]
        try:
            event = debezium(record, table=table)
        except ExtractError as exc:
            log.warning("undecodable CRM change event on %s: %s", record.topic, exc)
            stats.skip("MALFORMED_EVENT")
            continue

        if table == "accounts":
            account_ops.extend(curate_account_event(event, stats))
        elif table == "clients":
            client_ops.extend(curate_client_event(event, stats))
        else:
            stats.skip("UNKNOWN_TABLE")

    affected = 0
    db = state.get_db()
    for operations in (account_ops, client_ops):
        if operations:
            result = db[Account.COLLECTION].bulk_write(operations, ordered=True)
            affected += result.upserted_count + result.matched_count
    return affected


def run(once: bool = True, idle_timeout: Optional[float] = None,
        group_id: str = GROUP_ID) -> CurationStats:
    stats = CurationStats()

    def handler(records: list[ConsumedRecord]) -> int:
        return curate_batch(records, stats)

    consumer = BatchConsumer(TOPICS, group_id=group_id, handler=handler, stage="curation")
    try:
        if once:
            consumer.run_until_idle(idle_timeout)
        else:
            consumer.run_forever()
    finally:
        consumer.close()
    log.info("crm curation: %s", stats.as_dict())
    return stats
