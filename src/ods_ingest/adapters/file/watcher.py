"""Landing-directory watcher — the file adapter's control loop.

Responsibilities, in order:

  1. notice completed files (never a partially-written one)
  2. skip files already processed, by content-addressed batch id
  3. parse and verify control totals BEFORE producing anything
  4. produce detail records, then a batch manifest, then archive the file
  5. quarantine files that fail verification, with a FAILED manifest

Step 3 is the important one: a batch that does not reconcile never reaches the
bus, so a downstream consumer never sees a partial cycle it cannot distinguish
from a complete one.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from ods_ingest import config, state, topics
from ods_ingest.adapters.file import batches
from ods_ingest.bus.dlq import DlqPublisher
from ods_ingest.bus.producer import TopicProducer
from ods_ingest.envelope import build_headers, utc_now_iso

log = logging.getLogger("ods_ingest.file_adapter")

ADAPTER_ID = "file-adapter"
ADAPTER_VERSION = "1.0.0"

CUSTODY_TOPIC = "ods.raw.custody.positions"
MANIFEST_TOPIC = "ods.raw.custody.batches"

# Files still being written carry this suffix; the writer renames on completion.
INCOMPLETE_SUFFIX = ".tmp"


def _dirs() -> tuple[Path, Path, Path]:
    landing = Path(config.INGEST_LANDING_DIR)
    archive = Path(config.INGEST_ARCHIVE_DIR)
    quarantine = Path(config.INGEST_QUARANTINE_DIR)
    for d in (landing, archive, quarantine):
        d.mkdir(parents=True, exist_ok=True)
    return landing, archive, quarantine


def discover(landing: Path, pattern: str) -> list[Path]:
    """Completed files awaiting processing, oldest first.

    Anything still carrying the in-progress suffix is ignored: the writer
    renames only once every byte is on disk, so the final name IS the
    completeness signal.
    """
    found = [
        p for p in sorted(landing.glob(pattern))
        if p.is_file() and not p.name.endswith(INCOMPLETE_SUFFIX)
    ]
    return found


class CustodyFileAdapter:
    """Processes custody extract drops into the raw custody topic."""

    def __init__(self, producer_overrides: Optional[dict] = None) -> None:
        self.landing, self.archive, self.quarantine = _dirs()
        self.dlq = DlqPublisher()
        # Tuning hook for scripts/benchmark_bus_tuning.py. Production runs pass
        # nothing and take the configured producer defaults.
        self.producer_overrides = producer_overrides or {}

    # ── one file ─────────────────────────────────────────────────────────────

    def process_file(self, path: Path) -> Optional[dict]:
        """Process one custody file. Returns its manifest, or None if skipped."""
        batch_id = batches.batch_id_for(path)

        if state.batch_seen(batch_id):
            # Same name AND same bytes: a re-delivery, not new data. Archive it
            # so the landing directory drains, but produce nothing.
            log.info("skipping already-processed batch %s", batch_id)
            self._move(path, self.archive)
            return None

        log.info("processing %s (batch %s)", path.name, batch_id)
        batch = batches.parse_custody_file(path, batch_id)

        if not batch.ok:
            return self._quarantine(path, batch)
        return self._produce(path, batch)

    def _produce(self, path: Path, batch: batches.ParsedBatch) -> dict:
        produced = 0
        with TopicProducer(CUSTODY_TOPIC, adapter_id=ADAPTER_ID,
                           adapter_version=ADAPTER_VERSION,
                           overrides=self.producer_overrides) as producer:
            extracted_at = utc_now_iso()
            for seq, record in enumerate(batch.details, 1):
                # REC_ID is the loader's contribution: the record's position in
                # its cycle, which is what makes re-delivery idempotent.
                doc = dict(record)
                doc["REC_ID"] = batches.rec_id_for(batch.cycle_date, seq)
                producer.produce(doc, headers=build_headers(
                    source_system=topics.get(CUSTODY_TOPIC).source_system,
                    adapter_id=ADAPTER_ID,
                    adapter_version=ADAPTER_VERSION,
                    batch_id=batch.batch_id,
                    record_seq=seq,
                    extracted_at=extracted_at,
                ))
                produced += 1
            # Exiting the context flushes and raises on any delivery failure,
            # so the manifest below is only reached if every record landed.

        manifest = batches.build_manifest(batch, produced=produced, extracted_at=utc_now_iso())
        self._emit_manifest(manifest)
        state.record_batch(batch.batch_id, manifest)
        self._move(path, self.archive)
        log.info("batch %s complete: %d records", batch.batch_id, produced)
        return manifest

    def _quarantine(self, path: Path, batch: batches.ParsedBatch) -> dict:
        """A file that failed verification: nothing is produced from it."""
        log.error("batch %s failed verification: %s", batch.batch_id, batch.control_error)
        manifest = batches.build_manifest(batch, produced=0, extracted_at=utc_now_iso())
        self._emit_manifest(manifest)
        state.record_batch(batch.batch_id, manifest)

        # The individual bad lines go to the DLQ so they are recoverable.
        for reject in batch.rejects[:100]:
            self.dlq.publish(
                topics.get(CUSTODY_TOPIC).dlq,
                source_topic=CUSTODY_TOPIC,
                error=f"unparseable line {reject.get('lineNo')}: {reject.get('error')}",
                stage="adapter",
                raw_value=reject.get("raw", "").encode(),
                sample={"batchId": batch.batch_id, **reject},
            )
        self.dlq.flush()
        self._move(path, self.quarantine)
        return manifest

    def _emit_manifest(self, manifest: dict) -> None:
        with TopicProducer(MANIFEST_TOPIC, adapter_id=ADAPTER_ID,
                           adapter_version=ADAPTER_VERSION) as producer:
            producer.produce(manifest, headers=build_headers(
                source_system=topics.get(MANIFEST_TOPIC).source_system,
                adapter_id=ADAPTER_ID,
                adapter_version=ADAPTER_VERSION,
                batch_id=manifest["batchId"],
            ))

    def _move(self, path: Path, dest_dir: Path) -> None:
        target = dest_dir / path.name
        if target.exists():
            # Keep both rather than overwrite evidence of an earlier delivery.
            target = dest_dir / f"{path.stem}.{int(time.time())}{path.suffix}"
        shutil.move(str(path), str(target))

    # ── loops ────────────────────────────────────────────────────────────────

    def run_once(self, pattern: str = "CUSTPOS_*.dat") -> list[dict]:
        """Process every completed file currently in the landing directory."""
        manifests = []
        for path in discover(self.landing, pattern):
            manifest = self.process_file(path)
            if manifest:
                manifests.append(manifest)
        return manifests

    def run_forever(self, pattern: str = "CUSTPOS_*.dat") -> None:
        log.info("watching %s for %s", self.landing, pattern)
        try:
            while True:
                if not self.run_once(pattern):
                    time.sleep(config.FILE_POLL_INTERVAL_S)
        except KeyboardInterrupt:
            log.info("file adapter stopping")
        finally:
            state.close()


def run_generic_file_adapter(
    *,
    pattern: str,
    topic: str,
    parse: Callable[[Path, str], Iterable[dict]],
    landing: Optional[Path] = None,
) -> int:
    """Process delimited drops through the same batch machinery.

    Used by the intraday cash feed: different parser, identical batch identity,
    idempotency, and archiving behaviour.
    """
    landing_dir, archive_dir, _ = _dirs()
    landing = landing or landing_dir
    spec = topics.get(topic)
    total = 0

    for path in discover(landing, pattern):
        batch_id = batches.batch_id_for(path)
        if state.batch_seen(batch_id):
            log.info("skipping already-processed batch %s", batch_id)
            shutil.move(str(path), str(archive_dir / path.name))
            continue

        records = list(parse(path, batch_id))
        extracted_at = utc_now_iso()
        with TopicProducer(topic, adapter_id=ADAPTER_ID,
                           adapter_version=ADAPTER_VERSION) as producer:
            for seq, record in enumerate(records, 1):
                producer.produce(record, headers=build_headers(
                    source_system=spec.source_system,
                    adapter_id=ADAPTER_ID,
                    adapter_version=ADAPTER_VERSION,
                    batch_id=batch_id,
                    record_seq=seq,
                    extracted_at=extracted_at,
                ))

        state.record_batch(batch_id, {
            "batchId": batch_id, "fileName": path.name, "topic": topic,
            "recordCount": len(records), "status": batches.STATUS_COMPLETE,
            "extractedAt": extracted_at,
        })
        shutil.move(str(path), str(archive_dir / path.name))
        log.info("batch %s complete: %d records -> %s", batch_id, len(records), topic)
        total += len(records)

    return total
