"""Start-of-day securities master true-up.

Reads the vendor's full-population extract, diffs it against the previous
snapshot's key index, and produces only the differences to the same topic the
intraday REST poller feeds. Downstream — sink, raw tier, curation — is
unchanged and cannot tell the two channels apart, which is the point.

File shape: a header row, then one row per security sorted by Vendor_Ref, then
a trailer carrying the record count. The trailer is verified before anything is
produced, exactly as the custody extract is: a snapshot that is not whole must
not be diffed, because every missing row would look like a deletion.
"""
from __future__ import annotations

import csv
import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from ods_ingest import config, state, topics
from ods_ingest.adapters.file import batches
from ods_ingest.adapters.snapshot.diff import (
    Change,
    Delta,
    DiffStats,
    IndexEntry,
    read_index,
    sort_merge,
    write_index,
)
from ods_ingest.bus.producer import TopicProducer
from ods_ingest.curation.decode import source_timestamp_iso
from ods_ingest.envelope import build_headers, utc_now_iso

log = logging.getLogger("ods_ingest.snapshot")

TOPIC = "ods.raw.vendorsec.securities"
SNAPSHOT_PATTERN = "SECMASTER_*.csv"
ADAPTER_ID = "snapshot-adapter"
ADAPTER_VERSION = "1.0.0"

KEY_FIELD = "Vendor_Ref"
TRAILER_PREFIX = "TRAILER"

# The vendor's own columns, in delivery order.
COLUMNS = [
    "Vendor_Ref", "Cusip", "ISIN_CODE", "sedol", "TICKER", "SecurityDesc",
    "Issuer_Name", "ASSET_CLS", "CPN_RATE", "MATURITY_DT", "CCY", "CNTRY_DOM",
    "CALLABLE_FLG", "ISSUE_STATUS", "EXCH_CD", "LAST_UPD_TS",
]

# Status the vendor's removals become. The ODS never deletes; a security absent
# from the snapshot has left the vendor's universe, which is a status change.
REMOVED_STATUS = "DELISTED"


class SnapshotError(ValueError):
    """The snapshot is not usable — it must not be diffed."""


@dataclass
class SnapshotResult:
    batch_id: str
    file_name: str
    stats: DiffStats = field(default_factory=DiffStats)
    produced: int = 0
    declared_count: int = 0

    def as_dict(self) -> dict:
        return {"batchId": self.batch_id, "fileName": self.file_name,
                "produced": self.produced, "declaredCount": self.declared_count,
                **self.stats.as_dict()}


def index_path() -> Path:
    """Where the previous snapshot's key index lives.

    Local durable state owned by the adapter — the equivalent of a CDC
    connector's offsets. Losing it is recoverable (the next run treats every
    record as ADDED) but expensive, so it lives outside the landing directory.
    """
    return Path(config.INGEST_STATE_DIR) / "securities.index"


def read_snapshot(path: Path) -> tuple[Iterator[tuple[str, str, dict]], int]:
    """Stream (key, source timestamp, record) from the file, plus the trailer count.

    The trailer is read first — it is the last line — so the count is known
    before any record is produced.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    if not lines:
        raise SnapshotError(f"{path.name} is empty")
    trailer = lines[-1]
    if not trailer.startswith(TRAILER_PREFIX):
        raise SnapshotError(f"{path.name} has no {TRAILER_PREFIX} record — file may be truncated")
    try:
        declared = int(trailer.split(",")[1])
    except (IndexError, ValueError) as exc:
        raise SnapshotError(f"{path.name} has an unreadable trailer count: {trailer!r}") from exc

    body = lines[:-1]
    reader = csv.DictReader(body)
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise SnapshotError(f"{path.name} is missing column(s): {missing}")

    rows = list(reader)
    if len(rows) != declared:
        raise SnapshotError(
            f"{path.name} declares {declared} records but contains {len(rows)} — "
            f"a partial snapshot would read as mass deletion"
        )

    def _iter() -> Iterator[tuple[str, str, dict]]:
        for row in rows:
            record = {c: (row.get(c) or "").strip() for c in COLUMNS}
            record["SRC_UPDATED_AT"] = source_timestamp_iso(record.get("LAST_UPD_TS"))
            yield record[KEY_FIELD], record["SRC_UPDATED_AT"], record

    return _iter(), declared


def _removal_record(delta: Delta) -> dict:
    """A removal, expressed as the record the vendor would have sent.

    The ODS never deletes documents, so a disappearance becomes a status
    transition. The record is minimal by necessity — the source told us nothing
    except that it is gone.
    """
    return {
        **{c: "" for c in COLUMNS},
        KEY_FIELD: delta.key,
        "ISSUE_STATUS": REMOVED_STATUS,
        # Stamped now: the removal is newer than whatever state preceded it, so
        # the ordering guard must let it through.
        "SRC_UPDATED_AT": utc_now_iso(),
    }


def process_file(path: Path, *, dry_run: bool = False) -> SnapshotResult:
    """Diff one snapshot against the retained index and produce the differences."""
    batch_id = batches.batch_id_for(path)
    result = SnapshotResult(batch_id=batch_id, file_name=path.name)

    if state.batch_seen(batch_id):
        log.info("skipping already-processed snapshot %s", batch_id)
        return result

    incoming, declared = read_snapshot(path)
    result.declared_count = declared

    spec = topics.get(TOPIC)
    new_index: list[IndexEntry] = []
    extracted_at = utc_now_iso()

    # Exiting the producer context flushes and raises on any delivery failure,
    # so the index below is only rewritten once every delta has landed.
    producer_ctx: Any = nullcontext(None) if dry_run else TopicProducer(
        TOPIC, adapter_id=ADAPTER_ID, adapter_version=ADAPTER_VERSION)
    with producer_ctx as producer:
        for seq, (delta, entry) in enumerate(
            sort_merge(read_index(index_path()), incoming, result.stats), 1
        ):
            if entry is not None:
                new_index.append(entry)
            if delta is None:
                continue  # unchanged — the whole point is that this produces nothing

            record = delta.record if delta.change is not Change.REMOVED else _removal_record(delta)
            if producer is not None:
                producer.produce(record, headers=build_headers(
                    source_system=spec.source_system,
                    adapter_id=ADAPTER_ID,
                    adapter_version=ADAPTER_VERSION,
                    batch_id=batch_id,
                    record_seq=seq,
                    extracted_at=extracted_at,
                ))
            result.produced += 1

    if dry_run:
        log.info("dry run: %s", result.as_dict())
        return result

    # The index is replaced only after every delta is confirmed delivered.
    # Advancing it first would lose those changes permanently — the next
    # snapshot would consider them already applied.
    write_index(index_path(), new_index)
    state.record_batch(batch_id, {
        "batchId": batch_id, "fileName": path.name, "topic": TOPIC,
        "recordCount": result.produced, "declaredCount": declared,
        "status": batches.STATUS_COMPLETE, "extractedAt": extracted_at,
        **result.stats.as_dict(),
    })
    log.info("snapshot %s: %s", batch_id, result.as_dict())
    return result


def run_once(landing: Optional[Path] = None, *, dry_run: bool = False) -> list[SnapshotResult]:
    """Process every completed snapshot in the landing directory."""
    landing_dir = landing or Path(config.INGEST_LANDING_DIR)
    landing_dir.mkdir(parents=True, exist_ok=True)
    archive = Path(config.INGEST_ARCHIVE_DIR)
    archive.mkdir(parents=True, exist_ok=True)

    results = []
    for path in sorted(landing_dir.glob(SNAPSHOT_PATTERN)):
        if path.name.endswith(".tmp"):
            continue
        results.append(process_file(path, dry_run=dry_run))
        if not dry_run:
            path.rename(archive / path.name)
    return results
