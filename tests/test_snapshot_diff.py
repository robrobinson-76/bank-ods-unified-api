"""Sort-merge delta and source-timestamp normalisation.

The delta is what makes a full-population true-up affordable — a 40M-record
snapshot carrying a few hundred thousand real changes must produce a few
hundred thousand records, not 40 million. Getting it wrong is expensive in one
direction (volume) and dangerous in the other: a diff that wrongly reports
removals would soft-delete a live population.

Core suite: pure functions, no infrastructure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ods_ingest.adapters.snapshot.diff import (
    Change,
    DiffStats,
    IndexEntry,
    content_hash,
    read_index,
    sort_merge,
    write_index,
)
from ods_ingest.curation.decode import source_timestamp, source_timestamp_iso


def rec(key: str, desc: str = "ACME CORP", status: str = "ACT") -> dict:
    return {"Vendor_Ref": key, "SecurityDesc": desc, "ISSUE_STATUS": status}


def incoming(*records: tuple[str, dict], ts: str = "2026-07-30T00:00:00+00:00"):
    for key, record in records:
        yield key, ts, record


def run(previous: list[IndexEntry], records, stats: DiffStats | None = None):
    """Collect (deltas, new index) from a merge."""
    stats = stats or DiffStats()
    deltas, index = [], []
    for delta, entry in sort_merge(iter(previous), records, stats):
        if delta is not None:
            deltas.append(delta)
        if entry is not None:
            index.append(entry)
    return deltas, index, stats


# ── the case the pattern exists for ──────────────────────────────────────────

def test_unchanged_records_produce_nothing():
    """The whole point: a snapshot where nothing moved emits zero records."""
    records = [(f"VND-{i:03d}", rec(f"VND-{i:03d}")) for i in range(50)]
    previous = [
        IndexEntry(key, "2026-07-29T00:00:00+00:00", content_hash(r)) for key, r in records
    ]

    deltas, index, stats = run(previous, incoming(*records))

    assert deltas == []
    assert stats.unchanged == 50
    assert stats.emitted == 0
    # The index still describes the whole population, not just the changes.
    assert len(index) == 50
    assert stats.as_dict()["suppressionRatio"] == 1.0


def test_only_the_changed_records_are_emitted():
    keys = [f"VND-{i:03d}" for i in range(100)]
    previous = [
        IndexEntry(k, "2026-07-29T00:00:00+00:00", content_hash(rec(k))) for k in keys
    ]
    # Two securities genuinely moved.
    records = [(k, rec(k, desc="RENAMED" if k in ("VND-005", "VND-042") else "ACME CORP"))
               for k in keys]

    deltas, index, stats = run(previous, incoming(*records))

    assert {d.key for d in deltas} == {"VND-005", "VND-042"}
    assert all(d.change is Change.CHANGED for d in deltas)
    assert stats.changed == 2
    assert stats.unchanged == 98
    assert len(index) == 100


# ── absence is information ───────────────────────────────────────────────────

def test_a_record_missing_from_the_snapshot_is_a_removal():
    """In a full-population snapshot, absence is the delete signal.

    Content hashing alone cannot find these — only knowing the full previous
    key set can, which is what the merge gives for free.
    """
    previous = [
        IndexEntry("VND-001", "2026-07-29T00:00:00+00:00", content_hash(rec("VND-001"))),
        IndexEntry("VND-002", "2026-07-29T00:00:00+00:00", content_hash(rec("VND-002"))),
        IndexEntry("VND-003", "2026-07-29T00:00:00+00:00", content_hash(rec("VND-003"))),
    ]
    # VND-002 has left the vendor's universe.
    records = [("VND-001", rec("VND-001")), ("VND-003", rec("VND-003"))]

    deltas, index, stats = run(previous, incoming(*records))

    assert [(d.change, d.key) for d in deltas] == [(Change.REMOVED, "VND-002")]
    assert stats.removed == 1
    # A removed key must not survive into the new index, or it would be
    # reported as removed again on every subsequent snapshot.
    assert [e.key for e in index] == ["VND-001", "VND-003"]


def test_removals_at_the_end_of_the_previous_index_are_found():
    """The merge must drain the left stream after the right is exhausted."""
    previous = [
        IndexEntry(f"VND-{i:03d}", "2026-07-29T00:00:00+00:00", content_hash(rec(f"VND-{i:03d}")))
        for i in range(5)
    ]
    records = [("VND-000", rec("VND-000"))]

    deltas, _, stats = run(previous, incoming(*records))

    assert stats.removed == 4
    assert {d.key for d in deltas} == {"VND-001", "VND-002", "VND-003", "VND-004"}


def test_new_records_at_the_end_are_found():
    """And drain the right stream after the left is exhausted."""
    previous = [IndexEntry("VND-000", "2026-07-29T00:00:00+00:00", content_hash(rec("VND-000")))]
    records = [(f"VND-{i:03d}", rec(f"VND-{i:03d}")) for i in range(4)]

    deltas, index, stats = run(previous, incoming(*records))

    assert stats.added == 3
    assert {d.change for d in deltas} == {Change.ADDED}
    assert len(index) == 4


def test_first_ever_snapshot_is_all_additions():
    """With no retained index the whole population is new — and nothing is removed."""
    records = [(f"VND-{i:03d}", rec(f"VND-{i:03d}")) for i in range(10)]

    deltas, index, stats = run([], incoming(*records))

    assert stats.added == 10
    assert stats.removed == 0
    assert len(index) == 10


# ── correctness guards on the merge itself ───────────────────────────────────

@pytest.mark.parametrize("bad_side", ["previous", "incoming"])
def test_unsorted_input_is_rejected(bad_side):
    """Sortedness is the precondition that makes this linear.

    Silently accepting unsorted input would produce spurious ADDED and REMOVED
    pairs — i.e. it would delete live securities and re-add them.
    """
    good = [("VND-001", rec("VND-001")), ("VND-002", rec("VND-002"))]
    previous = [
        IndexEntry("VND-001", "t", content_hash(rec("VND-001"))),
        IndexEntry("VND-002", "t", content_hash(rec("VND-002"))),
    ]
    if bad_side == "previous":
        previous = list(reversed(previous))
    else:
        good = list(reversed(good))

    with pytest.raises(ValueError, match="not sorted"):
        run(previous, incoming(*good))


def test_content_hash_ignores_per_delivery_fields():
    """Fields that change every delivery must not make every record look changed."""
    a = {"Vendor_Ref": "VND-001", "SecurityDesc": "ACME", "batchId": "file-A"}
    b = {"Vendor_Ref": "VND-001", "SecurityDesc": "ACME", "batchId": "file-B"}
    assert content_hash(a, exclude=["batchId"]) == content_hash(b, exclude=["batchId"])
    assert content_hash(a) != content_hash(b)


def test_content_hash_is_order_independent():
    assert content_hash({"a": "1", "b": "2"}) == content_hash({"b": "2", "a": "1"})


# ── the retained index ───────────────────────────────────────────────────────

def test_index_round_trips(tmp_path: Path):
    entries = [
        IndexEntry("VND-001", "2026-07-30T10:00:00+00:00", "abc123"),
        IndexEntry("VND-002", "2026-07-30T11:00:00+00:00", "def456"),
    ]
    path = tmp_path / "securities.index"
    assert write_index(path, entries) == 2
    assert list(read_index(path)) == entries


def test_missing_index_reads_as_empty(tmp_path: Path):
    """A lost index must degrade to 'everything is new', not crash."""
    assert list(read_index(tmp_path / "absent.index")) == []


def test_index_write_is_atomic(tmp_path: Path):
    """A truncated index would report its missing tail as mass deletion.

    The write goes to a temp file and is renamed, so a crash leaves the previous
    index intact rather than a half-written one.
    """
    path = tmp_path / "securities.index"
    write_index(path, [IndexEntry("VND-001", "t", "h1")])
    original = path.read_text(encoding="utf-8")

    def exploding():
        yield IndexEntry("VND-001", "t", "h1")
        raise RuntimeError("crash mid-write")

    with pytest.raises(RuntimeError):
        write_index(path, exploding())

    assert path.read_text(encoding="utf-8") == original


# ── source timestamp normalisation ───────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026-01-30 04:12:44", datetime(2026, 1, 30, 4, 12, 44, tzinfo=timezone.utc)),
    ("2026-01-30T04:12:44", datetime(2026, 1, 30, 4, 12, 44, tzinfo=timezone.utc)),
    ("2026-01-30", datetime(2026, 1, 30, tzinfo=timezone.utc)),
    ("14-FEB-25", datetime(2025, 2, 14, tzinfo=timezone.utc)),
    ("02/14/2025", datetime(2025, 2, 14, tzinfo=timezone.utc)),
    ("20260130", datetime(2026, 1, 30, tzinfo=timezone.utc)),
])
def test_vendor_timestamp_formats_normalise(raw, expected):
    """The feed stamps updates in whichever format the delivering system used."""
    assert source_timestamp(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "N/A", "not-a-date", "99991231999"])
def test_unparseable_timestamps_return_none(raw):
    """None rather than a guess.

    Inventing now() would clobber newer data; inventing epoch would make the
    record permanently ignored; and "" would compare as older than every real
    timestamp, quietly turning "unknown" into "ancient". Writers must be able to
    see the absence.
    """
    assert source_timestamp(raw) is None
    assert source_timestamp_iso(raw) is None


def test_normalised_timestamps_sort_as_strings():
    """The ordering guard is a plain `$lt` on a string field, so the ISO form
    must sort identically as text and as an instant."""
    raw = ("14-FEB-25", "2026-01-30", "02/14/2025")
    values = [source_timestamp_iso(v) for v in raw]
    assert all(v is not None for v in values)
    ordered = sorted(v for v in values if v is not None)
    assert ordered == [
        source_timestamp_iso("14-FEB-25"),
        source_timestamp_iso("02/14/2025"),
        source_timestamp_iso("2026-01-30"),
    ]
    # And a fixed UTC offset, so two equal instants can never compare unequal.
    assert all(v.endswith("+00:00") for v in ordered)
