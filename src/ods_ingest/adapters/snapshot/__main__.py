"""Snapshot adapter entry point.

    python -m ods_ingest.adapters.snapshot --once
    python -m ods_ingest.adapters.snapshot --once --dry-run   # report the diff, produce nothing
    python -m ods_ingest.adapters.snapshot --show-index

--dry-run is the one worth knowing: it reports exactly what a true-up would
change without touching the bus, which is how you check a snapshot before
trusting it — a sudden spike in REMOVED is what a truncated delivery looks like.
"""
from __future__ import annotations

import argparse
import logging
import sys

from ods_ingest import config, state
from ods_ingest.adapters.snapshot import securities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="process what is there, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the delta without producing or advancing the index")
    parser.add_argument("--show-index", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    if args.show_index:
        path = securities.index_path()
        if not path.exists():
            print(f"no retained index at {path} — the next snapshot lands as all-new")
            return 0
        lines = sum(1 for _ in open(path, encoding="utf-8"))
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"{path}: {lines:,} keys, {size_mb:.1f} MB")
        return 0

    results = securities.run_once(dry_run=args.dry_run)
    if not results:
        print(f"no snapshots matching {securities.SNAPSHOT_PATTERN} in "
              f"{config.INGEST_LANDING_DIR}")
    for result in results:
        stats = result.stats
        print(f"{result.file_name}: {stats.total_seen:,} records in snapshot — "
              f"{stats.added:,} added, {stats.changed:,} changed, "
              f"{stats.removed:,} removed, {stats.unchanged:,} unchanged "
              f"({stats.as_dict()['suppressionRatio']:.1%} suppressed)")
    state.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
