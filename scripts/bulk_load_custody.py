"""Path B of the ingest benchmark: load a custody extract straight into Mongo.

The deliberate counterfactual to the bus. Same file, same parser, same target
collection and indexes — but no Kafka, no schema registry, no batch manifest,
no DLQ, no lineage, and nothing any other consumer could subscribe to. It is
the fastest way to get the rows in, and the honest comparison for
docs/FINDINGS-file-ingest-benchmark.md.

    python scripts/bulk_load_custody.py data/ingest/landing/CUSTPOS_20260730.dat

This is intentionally a one-off script, not part of src/ods_ingest: shipping it
as a supported path would be the first of the exceptions that dissolve the
"one contract" architecture.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pymongo

from bank_ods.models.raw_custody_position import RawCustodyPosition
from ods_ingest import config
from ods_ingest.adapters.file import batches
from ods_ingest.adapters.file import fixed_width as fw

DEFAULT_BATCH_SIZE = 5_000


def load(path: Path, *, batch_size: int = DEFAULT_BATCH_SIZE,
         verify_totals: bool = True, quiet: bool = False) -> dict:
    """Parse and bulk-write a custody extract. Returns timing and counts."""
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    collection = client[config.MONGODB_DB][RawCustodyPosition.COLLECTION]

    started = time.perf_counter()
    parsed = 0
    written = 0
    rejected = 0
    cycle_date = ""
    first_write_at = None
    buffer: list[dict] = []

    def flush() -> None:
        nonlocal written, first_write_at
        if not buffer:
            return
        # ordered=False so one duplicate does not abort the batch — the same
        # tolerance the sink's bulk_write has.
        collection.insert_many(buffer, ordered=False)
        if first_write_at is None:
            first_write_at = time.perf_counter()
        written += len(buffer)
        buffer.clear()

    with open(path, "r", encoding="ascii", errors="replace") as f:
        for rec_type, line_no, line in fw.iter_records(f):
            if rec_type == fw.REC_TYPE_HEADER:
                cycle_date = fw.parse_header(line)["HDR_BUS_DATE"]
                continue
            if rec_type == fw.REC_TYPE_TRAILER:
                continue
            if rec_type != fw.REC_TYPE_DETAIL:
                rejected += 1
                continue
            try:
                record = fw.parse_detail(line)
            except fw.ParseError:
                rejected += 1
                continue
            parsed += 1
            record["REC_ID"] = batches.rec_id_for(cycle_date, parsed)
            buffer.append(record)
            if len(buffer) >= batch_size:
                flush()
    flush()
    client.close()

    elapsed = time.perf_counter() - started
    result = {
        "path": "bulk",
        "file": path.name,
        "cycleDate": cycle_date,
        "recordsParsed": parsed,
        "recordsWritten": written,
        "rejected": rejected,
        "elapsedSeconds": round(elapsed, 3),
        "recordsPerSecond": round(written / elapsed, 1) if elapsed else 0,
        "timeToFirstQueryableSeconds": (
            round(first_write_at - started, 3) if first_write_at else None
        ),
        "batchSize": batch_size,
    }
    if not quiet:
        print(f"bulk-loaded {written:,} records in {elapsed:.1f}s "
              f"({result['recordsPerSecond']:,.0f} rec/s)")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 1
    load(args.file, batch_size=args.batch_size, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
