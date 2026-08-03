"""End-to-end: flat-file drop -> Kafka -> raw tier -> curated positions.

Drives the real components against the real stack. Each test uses its own
cycle date and consumer groups so it is independent of both the other tests and
any pipeline that happens to be running.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from bank_ods.models.raw_custody_position import RawCustodyPosition

from ods_ingest import state
from ods_ingest.adapters.file import batches
from ods_ingest.adapters.file.watcher import CustodyFileAdapter
from ods_ingest.bus.consumer import BatchConsumer
from ods_ingest.curation import custody_positions
from ods_ingest.curation.decode import ccyymmdd_to_datetime
from ods_ingest.sink import mapping, writer

import scripts.generate_custody_file as gen

pytestmark = pytest.mark.ingest

# Far-future cycles, one per test. They must be distinct: topics persist across
# tests and a fresh consumer group re-reads from the beginning, so assertions
# are always scoped to a cycle no other test writes.
CYCLE_HAPPY = "20991201"
CYCLE_DUPLICATE = "20991202"
CYCLE_CORRUPT = "20991203"
CYCLE_UNKNOWN = "20991204"
CYCLE_DECODE = "20991205"
CYCLE_REPLAY = "20991206"


def _generate(landing_dir, cycle: str, records: int, *, unknown_rate: float = 0.0,
              corrupt: bool = False) -> None:
    argv = ["--records", str(records), "--cycle-date", cycle,
            "--out-dir", str(landing_dir), "--seed", "7"]
    if unknown_rate:
        argv += ["--unknown-rate", str(unknown_rate)]
    if corrupt:
        argv += ["--corrupt-trailer"]
    assert gen.main(argv) == 0


def _run_sink(group: str) -> int:
    consumer = BatchConsumer(
        mapping.sink_topics(), group_id=group, handler=writer.handle, stage="sink"
    )
    try:
        return consumer.run_until_idle(idle_timeout=8)
    finally:
        consumer.close()


def _cleanup(db, cycle: str) -> None:
    """Reset everything this cycle wrote, including the batch ledger.

    The ledger matters: the generator is deterministic, so a re-run produces
    byte-identical files with the same content-addressed batch id, which the
    adapter would correctly refuse as an already-processed re-delivery.
    """
    db["raw_custody_positions"].delete_many({"POS_BUS_DATE": cycle})
    db["positions"].delete_many({"asOfDate": ccyymmdd_to_datetime(cycle)})
    state.clear(f"batch:CUSTPOS_{cycle}")


# ── the happy path, all the way through ──────────────────────────────────────

def test_file_lands_in_raw_tier_and_curates_to_positions(db, landing_dir, archive_dir,
                                                         unique_group):
    """The full path: file -> adapter -> topic -> raw tier -> curated positions."""
    _cleanup(db, CYCLE_HAPPY)
    _generate(landing_dir, CYCLE_HAPPY, 60)

    # 1. Adapter: verify control totals, produce records, emit the manifest.
    manifests = CustodyFileAdapter().run_once()
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["status"] == batches.STATUS_COMPLETE
    assert manifest["recordCount"] == 60
    assert manifest["trailerRecordCount"] == 60
    assert manifest["failReason"] is None
    # The file leaves the landing directory only once it is fully processed.
    assert not list(landing_dir.glob("CUSTPOS_*.dat"))
    assert (archive_dir / f"CUSTPOS_{CYCLE_HAPPY}.dat").exists()

    # 2. Sink: land the raw tier.
    _run_sink(unique_group)
    raw = list(db["raw_custody_positions"].find({"POS_BUS_DATE": CYCLE_HAPPY}))
    assert len(raw) == 60

    # REC_IDs follow the documented loader convention, contiguously.
    rec_ids = sorted(r["REC_ID"] for r in raw)
    assert rec_ids[0] == batches.rec_id_for(CYCLE_HAPPY, 1)
    assert rec_ids[-1] == batches.rec_id_for(CYCLE_HAPPY, 60)

    # Landed documents satisfy the model the ODS serves them through.
    for doc in raw:
        RawCustodyPosition.model_validate({k: v for k, v in doc.items() if k != "_id"})

    # 3. The batch ledger records the closed cycle.
    ledger = state.get_batch(manifest["batchId"])
    assert ledger is not None
    assert ledger["manifest"]["status"] == batches.STATUS_COMPLETE

    # 4. Curation: raw -> curated positions.
    stats = custody_positions.run(once=True, idle_timeout=8)
    assert stats.curated > 0
    positions = list(db["positions"].find({"asOfDate": ccyymmdd_to_datetime(CYCLE_HAPPY)}))
    assert positions, "curation produced no positions"
    for pos in positions:
        assert pos["snapshotType"] == "EOD"
        assert pos["accountId"].startswith("ACC-")
        assert pos["securityId"].startswith("SEC-")

    _cleanup(db, CYCLE_HAPPY)


def test_curated_values_decode_exactly(db, landing_dir, unique_group):
    """A curated position carries the raw record's value exactly, not a float."""
    _cleanup(db, CYCLE_DECODE)
    _generate(landing_dir, CYCLE_DECODE, 20)
    CustodyFileAdapter().run_once()
    _run_sink(unique_group)
    custody_positions.run(once=True, idle_timeout=8)

    raw = db["raw_custody_positions"].find_one({"POS_BUS_DATE": CYCLE_DECODE})
    from ods_ingest.curation.resolve import ReferenceIndex
    index = ReferenceIndex.load()
    account_id = index.account(raw["POS_ACCT_NBR"])
    security_id = index.security(raw.get("POS_CUSIP_NBR"), raw.get("POS_ISIN_NBR"))
    if account_id is None or security_id is None:
        pytest.skip("sampled record is not resolvable; nothing to compare")

    pos = db["positions"].find_one({
        "accountId": account_id, "securityId": security_id,
        "asOfDate": ccyymmdd_to_datetime(CYCLE_DECODE),
    })
    assert pos is not None
    # The zoned decimal decodes to exactly the stored Decimal128 — no float
    # ever touches a monetary value.
    expected_qty = Decimal(raw["POS_SHR_QTY"]).scaleb(-4)
    assert pos["quantity"].to_decimal() == expected_qty

    _cleanup(db, CYCLE_DECODE)


# ── idempotency ──────────────────────────────────────────────────────────────

def test_redelivering_the_same_file_produces_nothing_new(db, landing_dir, archive_dir,
                                                         unique_group):
    """Re-dropping identical bytes is a re-delivery, not new data."""
    _cleanup(db, CYCLE_DUPLICATE)
    _generate(landing_dir, CYCLE_DUPLICATE, 30)
    adapter = CustodyFileAdapter()
    first = adapter.run_once()
    assert len(first) == 1

    _run_sink(unique_group)
    before = db["raw_custody_positions"].count_documents({"POS_BUS_DATE": CYCLE_DUPLICATE})
    assert before == 30

    # Same file again: the content-addressed batch id makes it recognisable.
    _generate(landing_dir, CYCLE_DUPLICATE, 30)
    second = adapter.run_once()
    assert second == [], "a re-delivered batch must not be produced again"
    assert (archive_dir / f"CUSTPOS_{CYCLE_DUPLICATE}.dat").exists()

    after = db["raw_custody_positions"].count_documents({"POS_BUS_DATE": CYCLE_DUPLICATE})
    assert after == before

    _cleanup(db, CYCLE_DUPLICATE)


def test_replaying_the_topic_converges(db, landing_dir, unique_group):
    """A second sink pass over the same records changes nothing.

    At-least-once delivery means this WILL happen in production; the upsert on
    REC_ID is what makes it harmless.
    """
    _cleanup(db, CYCLE_REPLAY)
    _generate(landing_dir, CYCLE_REPLAY, 25)
    CustodyFileAdapter().run_once()

    _run_sink(unique_group)
    first = db["raw_custody_positions"].count_documents({"POS_BUS_DATE": CYCLE_REPLAY})

    # A brand-new group re-reads the same topic from the beginning.
    _run_sink(f"{unique_group}-replay")
    second = db["raw_custody_positions"].count_documents({"POS_BUS_DATE": CYCLE_REPLAY})
    assert second == first == 25

    _cleanup(db, CYCLE_REPLAY)


# ── failure handling ─────────────────────────────────────────────────────────

def test_control_total_mismatch_quarantines_the_whole_file(db, landing_dir, quarantine_dir,
                                                           unique_group):
    """A batch that does not reconcile must never reach the bus.

    Half a cycle is indistinguishable downstream from a complete one, so the
    file is failed whole rather than partially trusted.
    """
    _cleanup(db, CYCLE_CORRUPT)
    _generate(landing_dir, CYCLE_CORRUPT, 40, corrupt=True)

    manifests = CustodyFileAdapter().run_once()
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["status"] == batches.STATUS_FAILED
    assert manifest["recordCount"] == 0, "a failed batch must produce no records"
    assert "market value total" in (manifest["failReason"] or "")

    assert (quarantine_dir / f"CUSTPOS_{CYCLE_CORRUPT}.dat").exists()
    assert not list(landing_dir.glob("CUSTPOS_*.dat"))

    _run_sink(unique_group)
    assert db["raw_custody_positions"].count_documents({"POS_BUS_DATE": CYCLE_CORRUPT}) == 0

    _cleanup(db, CYCLE_CORRUPT)


def test_partial_file_is_ignored_until_complete(db, landing_dir):
    """A file still being written must not be read.

    The writer renames on completion; the watcher keys on the final name.
    """
    partial = landing_dir / f"CUSTPOS_{CYCLE_HAPPY}.dat.tmp"
    partial.write_text("01 partial content still being written\n", encoding="ascii")

    assert CustodyFileAdapter().run_once() == []
    assert partial.exists(), "the in-progress file must be left alone"


# ── unresolvable records ─────────────────────────────────────────────────────

def test_unknown_accounts_are_skipped_not_dead_lettered(db, landing_dir, unique_group):
    """Records for accounts the ODS doesn't carry are valid raw data.

    They must land in the raw tier and be reported by reconciliation, not be
    treated as poison.
    """
    _cleanup(db, CYCLE_UNKNOWN)
    _generate(landing_dir, CYCLE_UNKNOWN, 80, unknown_rate=0.25)

    manifests = CustodyFileAdapter().run_once()
    assert manifests[0]["status"] == batches.STATUS_COMPLETE
    _run_sink(unique_group)

    landed = db["raw_custody_positions"].count_documents({"POS_BUS_DATE": CYCLE_UNKNOWN})
    assert landed == 80, "every record lands in the raw tier, resolvable or not"

    stats = custody_positions.run(once=True, idle_timeout=8)
    assert stats.skipped_unknown_account > 0
    # Every record the curator saw is accounted for — nothing vanishes silently.
    # (The curator reads the whole topic, so `seen` spans other cycles too.)
    assert stats.curated + stats.skipped == stats.seen
    assert stats.seen >= 80

    # The planted unknowns are reported by the ops reconciler with the same
    # classification curation used, from the same resolution rules.
    import asyncio
    from bank_ods.services import ops

    report = asyncio.run(ops.reconcile_custody_feed(CYCLE_UNKNOWN))
    assert report["records"] == 80
    assert report["unmatched"] > 0
    assert {i["reason"] for i in report["issues"]} == {"UNKNOWN_ACCOUNT"}

    _cleanup(db, CYCLE_UNKNOWN)
