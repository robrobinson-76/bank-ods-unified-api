"""End-to-end: intraday cash drops -> Kafka -> raw tier -> cash_balances.

Two things are being proved here beyond "the data arrives":

  * the file adapter's batch machinery is feed-agnostic — a delimited intraday
    drop goes through the same identity, idempotency, and archiving path as the
    enormous fixed-width EOD extract, with only the parser swapped.
  * an AGGREGATING curator can still be idempotent, by recomputing the day's
    balance from all landed movements rather than incrementing by the batch in
    hand.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from bank_ods.services._common import account_id_from_custody

from ods_ingest import state
from ods_ingest.adapters.file.cash_csv import CASH_PATTERN, CASH_TOPIC, parse_cash_file
from ods_ingest.adapters.file.watcher import run_generic_file_adapter
from ods_ingest.bus.consumer import BatchConsumer
from ods_ingest.curation import cash_movements
from ods_ingest.curation.decode import ccyymmdd_to_datetime
from ods_ingest.sink import mapping, writer

import scripts.generate_cash_movements as gen

pytestmark = pytest.mark.ingest

# One business date per test, and never reused. Topics persist across runs (7-day
# retention) and a fresh consumer group re-reads from the beginning, so a date
# used by two tests — or by an earlier version of a test with different content —
# stays polluted. Generation is deterministic, so re-running a test re-produces
# byte-identical records that simply upsert onto themselves.
DATE_BASIC = "20991201"
DATE_ACCUMULATE = "20991202"
DATE_REPLAY = "20991203"
DATE_DUPLICATE = "20991204"


def _generate(landing_dir, date: str, time_of_day: str, rows: int, seed: int = 42) -> None:
    assert gen.main([
        "--date", date, "--time", time_of_day, "--rows", str(rows),
        "--out-dir", str(landing_dir), "--seed", str(seed),
    ]) == 0


def _run_adapter() -> int:
    return run_generic_file_adapter(
        pattern=CASH_PATTERN, topic=CASH_TOPIC, parse=parse_cash_file
    )


def _run_sink(group: str) -> None:
    consumer = BatchConsumer(
        mapping.sink_topics(), group_id=group, handler=writer.handle, stage="sink"
    )
    try:
        consumer.run_until_idle(idle_timeout=8)
    finally:
        consumer.close()


def _curate(group: str):
    return cash_movements.run(once=True, idle_timeout=8, group_id=group)


def _cleanup(db, date: str) -> None:
    db["raw_cash_movements"].delete_many({"MOV_FILE_DATE": date})
    db["cash_balances"].delete_many({"asOfDate": ccyymmdd_to_datetime(date)})
    state.clear(f"batch:CASHMOV_{date}")


def _dec(value) -> Decimal:
    return value.to_decimal() if hasattr(value, "to_decimal") else Decimal(str(value))


# ── the path ─────────────────────────────────────────────────────────────────

def test_intraday_drop_lands_and_becomes_a_balance(db, landing_dir):
    _cleanup(db, DATE_BASIC)
    token = uuid.uuid4().hex[:8]
    _generate(landing_dir, DATE_BASIC, "1030", 40)

    produced = _run_adapter()
    assert produced == 40
    assert not list(landing_dir.glob(CASH_PATTERN)), "the drop should be archived"

    _run_sink(f"test-{token}")
    raw = list(db["raw_cash_movements"].find({"MOV_FILE_DATE": DATE_BASIC}))
    assert len(raw) == 40
    # The file name is the only place the source states the drop's date/time.
    assert {r["MOV_FILE_TIME"] for r in raw} == {"1030"}

    stats = _curate(f"test-cur-{token}")
    assert stats.curated > 0

    balances = list(db["cash_balances"].find({"asOfDate": ccyymmdd_to_datetime(DATE_BASIC)}))
    assert balances
    for balance in balances:
        assert balance["snapshotType"] == "INTRADAY"
        # The arithmetic must be exact — these are Decimal128, never floats.
        opening = _dec(balance["openingBalance"])
        credits = _dec(balance["credits"])
        debits = _dec(balance["debits"])
        assert opening + credits - debits == _dec(balance["closingBalance"])
        assert _dec(balance["projectedBalance"]) == _dec(balance["closingBalance"])

    _cleanup(db, DATE_BASIC)


def test_a_second_drop_accumulates_into_the_same_day(db, landing_dir):
    """Several drops land per day and must all be reflected, not overwrite.

    Asserted against an independent recomputation from the raw tier rather than
    a before/after comparison: topics persist, so a fresh consumer group may
    see both drops on the very first pass, and a "did the number grow?" check
    would be true only on the first ever run.
    """
    _cleanup(db, DATE_ACCUMULATE)
    token = uuid.uuid4().hex[:8]

    _generate(landing_dir, DATE_ACCUMULATE, "1030", 30, seed=1)
    _run_adapter()
    _generate(landing_dir, DATE_ACCUMULATE, "1445", 30, seed=2)
    _run_adapter()

    _run_sink(f"test-{token}")
    _curate(f"test-cur-{token}")

    movements = list(db["raw_cash_movements"].find({"MOV_FILE_DATE": DATE_ACCUMULATE}))
    assert len(movements) == 60, "both drops must be present in the raw tier"
    assert {m["MOV_FILE_TIME"] for m in movements} == {"1030", "1445"}

    # Independently total the movements per (account, currency) and check each
    # curated balance carries the whole day, not just the last drop.
    expected: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for movement in movements:
        account_id = account_id_from_custody(movement["MOV_ACCT_NBR"])
        if account_id is None:
            continue
        key = (account_id, movement["MOV_CCY_CD"])
        credits, debits = expected.get(key, (Decimal(0), Decimal(0)))
        amount = Decimal(movement["MOV_AMT"])
        if amount >= 0:
            credits += amount
        else:
            debits += -amount
        expected[key] = (credits, debits)

    as_of = ccyymmdd_to_datetime(DATE_ACCUMULATE)
    balances = list(db["cash_balances"].find({"asOfDate": as_of}))
    assert balances

    for balance in balances:
        key = (balance["accountId"], balance["currency"])
        assert key in expected, f"balance for {key} has no movements behind it"
        expected_credits, expected_debits = expected[key]
        assert _dec(balance["credits"]) == expected_credits
        assert _dec(balance["debits"]) == expected_debits

    # At least one account genuinely received movements in both drops — otherwise
    # the accumulation claim would be vacuous.
    both_drops = {
        account_id_from_custody(m["MOV_ACCT_NBR"]) for m in movements if m["MOV_FILE_TIME"] == "1030"
    } & {
        account_id_from_custody(m["MOV_ACCT_NBR"]) for m in movements if m["MOV_FILE_TIME"] == "1445"
    }
    assert both_drops, "no account appeared in both drops; the test proves nothing"

    _cleanup(db, DATE_ACCUMULATE)


# ── idempotency ──────────────────────────────────────────────────────────────

def test_recuration_is_idempotent(db, landing_dir):
    """Re-curating the same movements must not double-count.

    This is the aggregating curator's defining risk: incrementing a running
    total by the batch in hand would inflate the balance on every replay, and
    at-least-once delivery makes replay routine.
    """
    _cleanup(db, DATE_REPLAY)
    token = uuid.uuid4().hex[:8]
    _generate(landing_dir, DATE_REPLAY, "0900", 25)
    _run_adapter()
    _run_sink(f"test-{token}")
    _curate(f"test-cur-{token}-first")

    as_of = ccyymmdd_to_datetime(DATE_REPLAY)
    before = {b["balanceId"]: (_dec(b["credits"]), _dec(b["debits"]),
                               _dec(b["closingBalance"]))
              for b in db["cash_balances"].find({"asOfDate": as_of})}
    assert before

    # A fresh consumer group re-reads every movement from the beginning.
    _curate(f"test-cur-{token}-replay")

    after = {b["balanceId"]: (_dec(b["credits"]), _dec(b["debits"]),
                             _dec(b["closingBalance"]))
             for b in db["cash_balances"].find({"asOfDate": as_of})}
    assert after == before, "replaying the movements changed the balances"

    _cleanup(db, DATE_REPLAY)


def test_redelivering_the_same_drop_produces_nothing_new(db, landing_dir, archive_dir):
    """The batch ledger recognises identical bytes, exactly as for custody."""
    _cleanup(db, DATE_DUPLICATE)
    _generate(landing_dir, DATE_DUPLICATE, "1030", 20)
    assert _run_adapter() == 20

    _generate(landing_dir, DATE_DUPLICATE, "1030", 20)
    assert _run_adapter() == 0, "a re-delivered drop must not be produced again"
    assert (archive_dir / f"CASHMOV_{DATE_DUPLICATE}_1030.csv").exists()

    _cleanup(db, DATE_DUPLICATE)


# ── parser ───────────────────────────────────────────────────────────────────

def test_the_drop_filename_carries_the_business_date_and_time(tmp_path):
    """The rows themselves never state which drop they came from."""
    from ods_ingest.adapters.file.cash_csv import CashParseError, file_metadata

    assert file_metadata(tmp_path / "CASHMOV_20260730_1445.csv") == ("20260730", "1445")
    with pytest.raises(CashParseError):
        file_metadata(tmp_path / "cash_movements.csv")
