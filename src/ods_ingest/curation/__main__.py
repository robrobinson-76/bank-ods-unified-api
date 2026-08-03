"""Curation entry point.

    python -m ods_ingest.curation --once             # run every curator once
    python -m ods_ingest.curation --only custody     # just one
    python -m ods_ingest.curation --list

Each curator is an independent consumer group, so they can be run together or
separately and each replays without affecting the others.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys

from ods_ingest import config, state

# Curator name -> module. Each module exposes run(once, idle_timeout) -> CurationStats.
CURATOR_MODULES = {
    "custody": "ods_ingest.curation.custody_positions",
    "crm": "ods_ingest.curation.crm_accounts",
    "vendorsec": "ods_ingest.curation.vendor_securities",
    "cash": "ods_ingest.curation.cash_movements",
}
CURATORS = tuple(CURATOR_MODULES)


def _run(name: str, once: bool, idle_timeout: float | None) -> dict:
    module = importlib.import_module(CURATOR_MODULES[name])
    return module.run(once=once, idle_timeout=idle_timeout).as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=CURATORS, default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--idle-timeout", type=float, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(levelname)s %(name)s: %(message)s")

    if args.list:
        for name in CURATORS:
            print(f"  {name}")
        return 0

    names = [args.only] if args.only else list(CURATORS)
    if not args.once and len(names) > 1:
        parser.error("continuous mode runs one curator per process; use --only")

    for name in names:
        result = _run(name, once=args.once or len(names) > 1, idle_timeout=args.idle_timeout)
        print(f"{name}: {result}")

    state.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
