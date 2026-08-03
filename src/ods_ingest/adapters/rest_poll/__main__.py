"""REST poll adapter entry point.

    python -m ods_ingest.adapters.rest_poll                  # poll continuously
    python -m ods_ingest.adapters.rest_poll --once           # one sweep, exit
    python -m ods_ingest.adapters.rest_poll --full-resync    # backfill everything
    python -m ods_ingest.adapters.rest_poll --show-watermark
"""
from __future__ import annotations

import argparse
import logging
import sys

from ods_ingest import config, state
from ods_ingest.adapters.rest_poll import poller


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--full-resync", action="store_true",
                        help="reset the watermark and re-read the whole source")
    parser.add_argument("--show-watermark", action="store_true")
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    if args.show_watermark:
        print(state.get_watermark(poller.SOURCE) or "(none — next poll reads everything)")
        state.close()
        return 0

    if args.once or args.full_resync:
        result = poller.poll_once(args.base_url, full_resync=args.full_resync)
        print(f"{result.records} record(s) in {result.pages} page(s); "
              f"watermark now {result.watermark}")
        state.close()
        return 0

    poller.run_forever(args.base_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
