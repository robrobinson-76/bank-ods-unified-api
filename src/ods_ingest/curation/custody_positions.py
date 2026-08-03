"""Curation: raw custody extract records -> curated `positions`.

Consumes ods.raw.custody.positions in its own consumer group, independently of
the sink. This is where the mainframe's wire conventions become typed values
and its identifiers become ODS identifiers.

Records that cannot be resolved are counted and skipped, not dead-lettered:
they are perfectly valid raw records describing an account or instrument the
ODS does not carry. `reconcile_custody_feed` on the ops MCP server is the tool
that explains them, and it applies the same rules (see curation/resolve.py).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from pymongo import UpdateOne

from bank_ods.models.position import Position

from ods_ingest.bus.consumer import BatchConsumer, ConsumedRecord
from ods_ingest.curation.base import (
    CurationStats,
    bulk_upsert,
    set_on_insert_timestamps,
    to_decimal128,
    utc_now,
)
from ods_ingest.curation.decode import DecodeError, ccyymmdd_to_datetime, zoned_to_decimal
from ods_ingest.curation.resolve import ReferenceIndex

log = logging.getLogger("ods_ingest.curation.custody")

TOPIC = "ods.raw.custody.positions"
GROUP_ID = "ods-curation-custody-positions"


def position_id(account_id: str, security_id: str, cycle_date: str) -> str:
    """Matches the seeded convention: POS-<acct digits>-<sec digits>-<cycle>."""
    acct = account_id.replace("-", "")
    sec = security_id.replace("-", "")
    return f"POS-{acct}-{sec}-{cycle_date}"


def curate_record(record: dict, index: ReferenceIndex, stats: CurationStats) -> Optional[UpdateOne]:
    """Turn one raw custody record into a positions upsert, or skip it."""
    account_id = index.account(record.get("POS_ACCT_NBR", ""))
    if account_id is None:
        stats.skip("UNKNOWN_ACCOUNT")
        return None

    security_id = index.security(record.get("POS_CUSIP_NBR"), record.get("POS_ISIN_NBR"))
    if security_id is None:
        stats.skip("UNKNOWN_SECURITY")
        return None

    try:
        as_of = ccyymmdd_to_datetime(record["POS_BUS_DATE"])
        quantity = zoned_to_decimal(record["POS_SHR_QTY"], 4)
        price = zoned_to_decimal(record["POS_MKT_PRICE"], 12)
        market_value = zoned_to_decimal(record["POS_MKT_VALUE"], 2)
    except (DecodeError, KeyError) as exc:
        # A malformed value in an otherwise-landed record. The raw tier keeps
        # it; the curated tier cannot represent it.
        log.warning("undecodable custody record %s: %s", record.get("REC_ID"), exc)
        stats.skip("MALFORMED_VALUE")
        return None

    # The extract carries no cost basis (the mainframe holds it in a different
    # file), so market value stands in and unrealized P&L is reported as zero
    # rather than invented. POS_ACCR_INT is landed in the raw tier but has no
    # home on the Position model, so it is deliberately not carried forward.
    cost_basis = market_value
    unrealized = Decimal("0.00")

    doc = {
        "positionId": position_id(account_id, security_id, record["POS_BUS_DATE"]),
        "accountId": account_id,
        "securityId": security_id,
        "asOfDate": as_of,
        "quantity": to_decimal128(quantity),
        "currency": record.get("POS_CCY_CD") or "USD",
        "costBasis": to_decimal128(cost_basis),
        "marketPrice": to_decimal128(price),
        "marketValue": to_decimal128(market_value),
        "unrealizedPnL": to_decimal128(unrealized),
        # Short positions are represented by a negative quantity upstream.
        "positionType": "SHORT" if quantity < 0 else "LONG",
        "snapshotType": "EOD",
    }

    set_part, insert_part = set_on_insert_timestamps(doc, utc_now())
    stats.curated += 1
    # Upsert on the compound natural key the unique index enforces, so a
    # replayed cycle updates the same snapshot instead of colliding.
    return UpdateOne(
        {"accountId": account_id, "securityId": security_id, "asOfDate": as_of},
        {"$set": set_part, "$setOnInsert": insert_part},
        upsert=True,
    )


def curate_batch(records: list[ConsumedRecord], index: ReferenceIndex,
                 stats: CurationStats) -> int:
    operations = []
    for record in records:
        stats.seen += 1
        op = curate_record(record.value, index, stats)
        if op is not None:
            operations.append(op)
    return bulk_upsert(Position.COLLECTION, operations)


def run(once: bool = True, idle_timeout: Optional[float] = None) -> CurationStats:
    """Curate the custody topic. Returns what the run did."""
    index = ReferenceIndex.load()
    stats = CurationStats()

    def handler(records: list[ConsumedRecord]) -> int:
        return curate_batch(records, index, stats)

    consumer = BatchConsumer([TOPIC], group_id=GROUP_ID, handler=handler, stage="curation")
    try:
        if once:
            consumer.run_until_idle(idle_timeout)
        else:
            consumer.run_forever()
    finally:
        consumer.close()
    log.info("custody curation: %s", stats.as_dict())
    return stats
