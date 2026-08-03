"""File adapter entry point.

    python -m ods_ingest.adapters.file              # watch continuously
    python -m ods_ingest.adapters.file --once       # drain the landing dir, exit
    python -m ods_ingest.adapters.file --feed cash  # intraday cash drops
"""
from __future__ import annotations

import argparse
import logging
import sys

from ods_ingest import config, state
from ods_ingest.adapters.file.watcher import CustodyFileAdapter, run_generic_file_adapter

FEEDS = ("custody", "cash")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", choices=FEEDS, default="custody")
    parser.add_argument("--once", action="store_true", help="process what is there, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    if args.feed == "custody":
        adapter = CustodyFileAdapter()
        if args.once:
            manifests = adapter.run_once()
            for m in manifests:
                print(f"{m['batchId']}: {m['status']} — {m['recordCount']} records")
            state.close()
            return 0
        adapter.run_forever()
        return 0

    # Intraday cash: same batch machinery, delimited parser.
    from ods_ingest.adapters.file.cash_csv import CASH_PATTERN, CASH_TOPIC, parse_cash_file

    if args.once:
        count = run_generic_file_adapter(
            pattern=CASH_PATTERN, topic=CASH_TOPIC, parse=parse_cash_file
        )
        print(f"produced {count} cash movement(s)")
        state.close()
        return 0

    import time
    try:
        while True:
            if not run_generic_file_adapter(
                pattern=CASH_PATTERN, topic=CASH_TOPIC, parse=parse_cash_file
            ):
                time.sleep(config.FILE_POLL_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        state.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
