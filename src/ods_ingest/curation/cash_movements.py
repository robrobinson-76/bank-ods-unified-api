"""Curation: intraday cash movements -> curated `cash_balances`.

The one curator that aggregates rather than maps one-to-one, which makes
idempotency the interesting problem. Several drops land per business day, each
carrying only that window's movements, while the target is a running daily
balance.

The rule that makes replay safe: the day's balance is **recomputed from every
landed raw movement**, never incremented by the batch in hand. Incrementing
would double-count the moment a drop was re-delivered or a consumer group was
replayed — both of which are normal events on an at-least-once bus.

Opening balance comes from the most recent prior EOD snapshot, so an intraday
balance is a real continuation of the account's position rather than a figure
starting from zero.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from pymongo import UpdateOne

from bank_ods.models.cash_balance import CashBalance
from bank_ods.services._common import account_id_from_custody

from ods_ingest import state
from ods_ingest.bus.consumer import BatchConsumer, ConsumedRecord
from ods_ingest.curation.base import CurationStats, to_decimal128, utc_now
from ods_ingest.curation.decode import DecodeError, ccyymmdd_to_datetime, plain_decimal

log = logging.getLogger("ods_ingest.curation.cash")

TOPIC = "ods.raw.cash.movements"
GROUP_ID = "ods-curation-cash-movements"

ZERO = Decimal("0.00")


def balance_id(account_id: str, currency: str, file_date: str) -> str:
    """Matches the seeded convention: BAL-<acct digits>-<ccy>-<date>."""
    return f"BAL-{account_id.replace('-', '')}-{currency}-{file_date}"


def _opening_balance(account_id: str, currency: str, as_of) -> Decimal:
    """Closing balance of the most recent prior EOD snapshot, or zero.

    Deliberately restricted to EOD snapshots. Reading *any* prior balance would
    include intraday ones this same curator rewrites, so the opening figure
    would depend on the order days happened to be processed in — two replays of
    the identical movements could then produce different balances. EOD
    snapshots come from a different feed and are never rewritten here, which
    makes this lookup stable and the curator genuinely convergent.
    """
    previous = state.get_db()[CashBalance.COLLECTION].find_one(
        {
            "accountId": account_id,
            "currency": currency,
            "asOfDate": {"$lt": as_of},
            "snapshotType": "EOD",
        },
        sort=[("asOfDate", -1)],
    )
    if not previous:
        return ZERO
    closing = previous.get("closingBalance")
    return closing.to_decimal() if hasattr(closing, "to_decimal") else Decimal(str(closing or 0))


def _recompute(account_id: str, currency: str, file_date: str) -> Optional[UpdateOne]:
    """Rebuild one (account, currency, day) balance from all landed movements.

    Reading the raw tier rather than the batch is what makes this idempotent:
    the result depends only on what has been landed, not on how many times it
    was delivered.
    """
    as_of = ccyymmdd_to_datetime(file_date)
    custody_nbr = None
    credits = ZERO
    debits = ZERO

    cursor = state.get_db()["raw_cash_movements"].find(
        {"MOV_FILE_DATE": file_date, "MOV_CCY_CD": currency},
        {"MOV_ACCT_NBR": 1, "MOV_AMT": 1},
    )
    for movement in cursor:
        if account_id_from_custody(movement.get("MOV_ACCT_NBR", "")) != account_id:
            continue
        custody_nbr = movement.get("MOV_ACCT_NBR")
        try:
            amount = plain_decimal(movement.get("MOV_AMT"))
        except DecodeError:
            continue  # the raw record stands; it just cannot be summed
        if amount is None:
            continue
        if amount >= 0:
            credits += amount
        else:
            debits += -amount

    if custody_nbr is None:
        return None  # nothing landed for this key after all

    opening = _opening_balance(account_id, currency, as_of)
    closing = opening + credits - debits

    doc = {
        "balanceId": balance_id(account_id, currency, file_date),
        "accountId": account_id,
        "currency": currency,
        "asOfDate": as_of,
        "openingBalance": to_decimal128(opening),
        "credits": to_decimal128(credits),
        "debits": to_decimal128(debits),
        "closingBalance": to_decimal128(closing),
        # Intraday drops carry settled movements only; nothing is pending, so
        # the projected balance equals the closing balance.
        "pendingCredits": to_decimal128(ZERO),
        "pendingDebits": to_decimal128(ZERO),
        "projectedBalance": to_decimal128(closing),
        "snapshotType": "INTRADAY",
        "updatedAt": utc_now(),
    }
    return UpdateOne(
        {"accountId": account_id, "currency": currency, "asOfDate": as_of},
        {"$set": doc, "$setOnInsert": {"createdAt": utc_now()}},
        upsert=True,
    )


def curate_batch(records: list[ConsumedRecord], stats: CurationStats) -> int:
    """Recompute every (account, currency, day) the batch touched.

    Note this curator aggregates, so `stats.curated` counts BALANCES rebuilt
    while `stats.seen` counts movements consumed — unlike the one-to-one
    curators, the two are not expected to add up.
    """
    touched: set[tuple[str, str, str]] = set()

    for record in records:
        stats.seen += 1
        row = record.value
        account_id = account_id_from_custody(row.get("MOV_ACCT_NBR", ""))
        currency = row.get("MOV_CCY_CD")
        file_date = row.get("MOV_FILE_DATE")
        if account_id is None:
            stats.skip("UNKNOWN_ACCOUNT")
            continue
        if not currency or not file_date:
            stats.skip("MALFORMED_VALUE")
            continue
        touched.add((account_id, currency, file_date))

    operations = []
    for account_id, currency, file_date in sorted(touched):
        try:
            op = _recompute(account_id, currency, file_date)
        except DecodeError as exc:
            log.warning("cannot recompute %s/%s/%s: %s", account_id, currency, file_date, exc)
            stats.skip("MALFORMED_VALUE")
            continue
        if op is not None:
            operations.append(op)
            stats.curated += 1

    if not operations:
        return 0
    result = state.get_db()[CashBalance.COLLECTION].bulk_write(operations, ordered=False)
    return result.upserted_count + result.matched_count


def run(once: bool = True, idle_timeout: Optional[float] = None,
        group_id: str = GROUP_ID) -> CurationStats:
    stats = CurationStats()

    def handler(records: list[ConsumedRecord]) -> int:
        return curate_batch(records, stats)

    consumer = BatchConsumer([TOPIC], group_id=group_id, handler=handler, stage="curation")
    try:
        if once:
            consumer.run_until_idle(idle_timeout)
        else:
            consumer.run_forever()
    finally:
        consumer.close()
    log.info("cash movements curation: %s", stats.as_dict())
    return stats
