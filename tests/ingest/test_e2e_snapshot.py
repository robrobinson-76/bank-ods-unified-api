"""End-to-end: start-of-day true-up alongside the intraday stream.

Two delivery channels feed one latest-state entity — the intraday vendor poll
and the start-of-day snapshot file — with no ordering guarantee between them.
These tests prove the two properties that makes safe:

  * **Ordering.** A stale snapshot record delivered after a fresher intraday
    update must not overwrite it, in the raw tier or the semantic tier. This is
    a correctness property, not a performance one, and the window it protects
    widens with the duration of the load.
  * **Volume.** A full-population snapshot in which nothing changed must produce
    nothing. At 40M records that is the difference between fitting the batch
    window and not.

See docs/PATTERN-snapshot-and-stream.md.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from ods_ingest import config, state
from ods_ingest.adapters.snapshot import securities as snapshot
from ods_ingest.adapters.snapshot.diff import IndexEntry, content_hash, write_index
from ods_ingest.bus.consumer import BatchConsumer
from ods_ingest.bus.producer import TopicProducer
from ods_ingest.curation import vendor_securities
from ods_ingest.envelope import build_headers
from ods_ingest.sink import mapping, writer

import scripts.generate_securities_snapshot as gen

pytestmark = pytest.mark.ingest

TOPIC = "ods.raw.vendorsec.securities"

OLD = "2020-01-01T00:00:00+00:00"
NEW = "2099-01-01T00:00:00+00:00"


@pytest.fixture
def snapshot_dirs(tmp_path, monkeypatch) -> Path:
    """Isolated landing/archive plus a private retained index per test."""
    landing = tmp_path / "landing"
    archive = tmp_path / "archive"
    index_dir = tmp_path / "state"
    for d in (landing, archive, index_dir):
        d.mkdir(parents=True)
    monkeypatch.setattr(config, "INGEST_LANDING_DIR", str(landing))
    monkeypatch.setattr(config, "INGEST_ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(config, "INGEST_STATE_DIR", str(index_dir))
    return landing


def _run_sink(group: str) -> None:
    consumer = BatchConsumer(
        mapping.sink_topics(), group_id=group, handler=writer.handle, stage="sink"
    )
    try:
        consumer.run_until_idle(idle_timeout=8)
    finally:
        consumer.close()


def _produce(record: dict) -> None:
    """Put one vendor record on the topic, as either channel would."""
    with TopicProducer(TOPIC, adapter_id="test") as producer:
        producer.produce(record, headers=build_headers(
            source_system="VENDORSEC_SAAS", adapter_id="test", adapter_version="1.0.0"))


def _vendor_record(ref: str, *, updated_at: str, desc: str, status: str = "ACT") -> dict:
    return {
        **{c: "" for c in snapshot.COLUMNS},
        "Vendor_Ref": ref,
        "SecurityDesc": desc,
        "ISSUE_STATUS": status,
        "LAST_UPD_TS": updated_at,
        "SRC_UPDATED_AT": updated_at,
    }


# ── ordering: the correctness property ───────────────────────────────────────

def test_a_stale_record_cannot_overwrite_a_newer_one_in_the_raw_tier(db):
    """The hazard this whole pattern exists to prevent.

    A snapshot taken at time T is delivered while intraday updates from after T
    are already applied. Arrival order must not decide the outcome — the
    source's own timestamp must.
    """
    ref = f"VND-ORD-{uuid.uuid4().hex[:8].upper()}"
    group = f"test-{uuid.uuid4().hex[:8]}"
    try:
        # Fresh intraday update lands first.
        _produce(_vendor_record(ref, updated_at=NEW, desc="CURRENT NAME"))
        _run_sink(group)
        assert db["raw_vendor_securities"].find_one(
            {"Vendor_Ref": ref})["SecurityDesc"] == "CURRENT NAME"

        # Stale snapshot record for the same security arrives afterwards.
        _produce(_vendor_record(ref, updated_at=OLD, desc="STALE SNAPSHOT NAME"))
        _run_sink(f"{group}-b")

        landed = db["raw_vendor_securities"].find_one({"Vendor_Ref": ref})
        assert landed["SecurityDesc"] == "CURRENT NAME", (
            "a stale snapshot record overwrote a newer intraday update"
        )
        assert landed["SRC_UPDATED_AT"] == NEW
    finally:
        db["raw_vendor_securities"].delete_many({"Vendor_Ref": ref})


def test_a_newer_record_does_apply(db):
    """The guard must not be so strict that real updates are lost."""
    ref = f"VND-ORD-{uuid.uuid4().hex[:8].upper()}"
    group = f"test-{uuid.uuid4().hex[:8]}"
    try:
        _produce(_vendor_record(ref, updated_at=OLD, desc="ORIGINAL"))
        _run_sink(group)
        _produce(_vendor_record(ref, updated_at=NEW, desc="UPDATED"))
        _run_sink(f"{group}-b")

        landed = db["raw_vendor_securities"].find_one({"Vendor_Ref": ref})
        assert landed["SecurityDesc"] == "UPDATED"
        assert landed["SRC_UPDATED_AT"] == NEW
    finally:
        db["raw_vendor_securities"].delete_many({"Vendor_Ref": ref})


def test_replaying_the_same_record_is_a_no_op(db):
    """Equal timestamps mean equal state — replay must converge, not thrash."""
    ref = f"VND-ORD-{uuid.uuid4().hex[:8].upper()}"
    group = f"test-{uuid.uuid4().hex[:8]}"
    try:
        record = _vendor_record(ref, updated_at=NEW, desc="STABLE")
        _produce(record)
        _run_sink(group)
        before = db["raw_vendor_securities"].find_one({"Vendor_Ref": ref})

        _produce(record)
        _run_sink(f"{group}-b")

        after = db["raw_vendor_securities"].find_one({"Vendor_Ref": ref})
        assert after["SecurityDesc"] == before["SecurityDesc"]
        assert db["raw_vendor_securities"].count_documents({"Vendor_Ref": ref}) == 1
    finally:
        db["raw_vendor_securities"].delete_many({"Vendor_Ref": ref})


def test_curation_also_refuses_a_stale_record(db):
    """The guard is needed in both tiers.

    The raw tier and the semantic tier are written by different components with
    different keys; protecting only one leaves the other exposed.
    """
    security = db["securities"].find_one({"cusip": {"$ne": None}})
    assert security is not None, "no seeded security to enrich"
    security_id = security["securityId"]
    original_issuer = security.get("issuer")
    ref = f"VND-CUR-{uuid.uuid4().hex[:8].upper()}"

    try:
        # A newer vendor record sets the issuer.
        newer = _vendor_record(ref, updated_at=NEW, desc="X")
        newer["Cusip"] = security["cusip"]
        newer["Issuer_Name"] = "NEWER ISSUER NAME"
        _produce(newer)
        _run_sink(f"test-{uuid.uuid4().hex[:8]}")
        vendor_securities.run(once=True, idle_timeout=8,
                              group_id=f"test-cur-{uuid.uuid4().hex[:8]}")
        assert db["securities"].find_one(
            {"securityId": security_id})["issuer"] == "NEWER ISSUER NAME"

        # A stale one must not undo it.
        stale = _vendor_record(f"{ref}-OLD", updated_at=OLD, desc="X")
        stale["Cusip"] = security["cusip"]
        stale["Issuer_Name"] = "STALE ISSUER NAME"
        _produce(stale)
        _run_sink(f"test-{uuid.uuid4().hex[:8]}")
        vendor_securities.run(once=True, idle_timeout=8,
                              group_id=f"test-cur-{uuid.uuid4().hex[:8]}")

        assert db["securities"].find_one(
            {"securityId": security_id})["issuer"] == "NEWER ISSUER NAME", (
            "curation applied a stale vendor record over a newer one"
        )
    finally:
        db["raw_vendor_securities"].delete_many({"Vendor_Ref": {"$regex": f"^{ref}"}})
        db["securities"].update_one(
            {"securityId": security_id},
            {"$set": {"issuer": original_issuer}, "$unset": {"vendorUpdatedAt": ""}},
        )


# ── volume: the reason the delta exists ──────────────────────────────────────

def test_an_unchanged_snapshot_produces_nothing(db, snapshot_dirs):
    """A true-up where nothing moved must emit zero records.

    This is the property that makes a 40M-record daily file affordable: the
    cost tracks what changed, not what was delivered.
    """
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990101"]) == 0
    first = snapshot.run_once(landing=snapshot_dirs)
    assert len(first) == 1
    assert first[0].stats.added > 0, "the first snapshot should land as all-new"
    state.clear(f"batch:SECMASTER")

    # The identical population again.
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990102"]) == 0
    second = snapshot.run_once(landing=snapshot_dirs)

    assert len(second) == 1
    assert second[0].produced == 0, "an unchanged snapshot produced records"
    assert second[0].stats.unchanged == first[0].stats.added
    assert second[0].stats.as_dict()["suppressionRatio"] == 1.0
    state.clear(f"batch:SECMASTER")


def test_only_changed_records_reach_the_bus(db, snapshot_dirs):
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990103"]) == 0
    baseline = snapshot.run_once(landing=snapshot_dirs)[0]
    state.clear(f"batch:SECMASTER")

    # ~10% of the population moved, plus two brand-new listings.
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990104",
                     "--change-rate", "0.1", "--add", "2"]) == 0
    delta = snapshot.run_once(landing=snapshot_dirs)[0]

    assert delta.stats.changed > 0
    assert delta.stats.added == 2
    assert delta.produced == delta.stats.changed + delta.stats.added
    assert delta.produced < baseline.stats.added, "the delta was not smaller than the population"
    state.clear(f"batch:SECMASTER")


def test_a_security_missing_from_the_snapshot_becomes_a_soft_delete(db, snapshot_dirs):
    """Absence is the delete signal, and the ODS never removes documents."""
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990105"]) == 0
    snapshot.run_once(landing=snapshot_dirs)
    state.clear(f"batch:SECMASTER")

    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990106", "--drop", "2"]) == 0
    result = snapshot.run_once(landing=snapshot_dirs)[0]

    assert result.stats.removed == 2
    assert result.produced == 2

    _run_sink(f"test-{uuid.uuid4().hex[:8]}")
    # The removal lands as a status transition, not a deletion.
    removed = list(db["raw_vendor_securities"].find({"ISSUE_STATUS": snapshot.REMOVED_STATUS}))
    assert removed, "no removal record landed"
    state.clear(f"batch:SECMASTER")


# ── safety rails on the snapshot itself ──────────────────────────────────────

def test_a_truncated_snapshot_is_rejected_before_diffing(db, snapshot_dirs):
    """A partial file would read as mass deletion — it must never be diffed.

    This is the highest-consequence failure in the whole pattern: silently
    accepting a half-delivered snapshot would soft-delete a live population.
    """
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990107"]) == 0
    path = snapshot_dirs / "SECMASTER_20990107.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    # Drop rows but leave the trailer's declared count intact.
    path.write_text("\n".join(lines[:5] + [lines[-1]]) + "\n", encoding="utf-8")

    with pytest.raises(snapshot.SnapshotError, match="declares"):
        snapshot.process_file(path)


def test_a_snapshot_with_no_trailer_is_rejected(db, snapshot_dirs):
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990108"]) == 0
    path = snapshot_dirs / "SECMASTER_20990108.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    with pytest.raises(snapshot.SnapshotError, match="TRAILER"):
        snapshot.process_file(path)


def test_the_index_is_only_advanced_after_delivery(db, snapshot_dirs):
    """A dry run reports the delta without moving the retained index.

    If the index advanced before the records were delivered, those changes
    would be lost permanently — the next snapshot would consider them applied.
    """
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990109"]) == 0
    path = snapshot_dirs / "SECMASTER_20990109.csv"

    dry = snapshot.process_file(path, dry_run=True)
    assert dry.stats.added > 0
    assert not snapshot.index_path().exists(), "a dry run advanced the retained index"

    real = snapshot.process_file(path)
    assert snapshot.index_path().exists()
    assert real.stats.added == dry.stats.added
    state.clear(f"batch:SECMASTER")


def test_a_lost_index_degrades_to_all_new_not_mass_deletion(db, snapshot_dirs):
    """Losing the retained index must be recoverable, and must never delete."""
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990110"]) == 0
    snapshot.run_once(landing=snapshot_dirs)
    state.clear(f"batch:SECMASTER")

    snapshot.index_path().unlink()

    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990111"]) == 0
    result = snapshot.run_once(landing=snapshot_dirs)[0]

    assert result.stats.removed == 0, "a lost index caused spurious deletions"
    assert result.stats.added == result.stats.total_seen
    state.clear(f"batch:SECMASTER")


def test_index_entries_are_written_sorted(db, snapshot_dirs):
    """The next run's merge depends on it, and an unsorted index is rejected."""
    assert gen.main(["--out-dir", str(snapshot_dirs), "--date", "20990112"]) == 0
    snapshot.run_once(landing=snapshot_dirs)

    keys = [line.split("\t")[0] for line in
            snapshot.index_path().read_text(encoding="utf-8").splitlines()]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    state.clear(f"batch:SECMASTER")
