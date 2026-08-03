"""Generate a full-population securities master snapshot.

Stands in for the vendor's start-of-day extract. Deterministic, sorted by
Vendor_Ref (the snapshot adapter's sort-merge requires it), with a trailer
carrying the record count.

    python scripts/generate_securities_snapshot.py                     # baseline
    python scripts/generate_securities_snapshot.py --change-rate 0.01  # 1% moved
    python scripts/generate_securities_snapshot.py --drop 3            # 3 removed
    python scripts/generate_securities_snapshot.py --records 1000000   # scale test

Records are derived from the stub SaaS dataset so the snapshot and the intraday
REST feed describe the same universe — which is what makes the two-channel
ordering behaviour testable.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ods_ingest import config
from ods_ingest.adapters.snapshot.securities import COLUMNS, TRAILER_PREFIX

DATASET = (Path(__file__).resolve().parents[1] / "src" / "ods_ingest" / "stub_saas"
           / "vendor_securities.json")


def base_rows() -> list[dict]:
    with open(DATASET, encoding="utf-8") as f:
        rows = json.load(f)
    return [{c: (r.get(c) or "") for c in COLUMNS} for r in rows]


def synthesise(rows: list[dict], target: int, rng: random.Random) -> list[dict]:
    """Pad the dataset out to `target` records for scale testing."""
    if target <= len(rows):
        return rows[:target]
    out = list(rows)
    for i in range(len(rows), target):
        template = rows[i % len(rows)]
        clone = dict(template)
        clone["Vendor_Ref"] = f"VND-{i + 1:06d}"
        clone["SecurityDesc"] = f"{template['SecurityDesc']} SERIES {i}"
        out.append(clone)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=0,
                        help="pad to this many records (default: dataset size)")
    parser.add_argument("--change-rate", type=float, default=0.0,
                        help="fraction of records given a newer LAST_UPD_TS and a changed field")
    parser.add_argument("--drop", type=int, default=0,
                        help="omit this many records, to exercise delete-by-absence")
    parser.add_argument("--add", type=int, default=0, help="append this many brand-new records")
    parser.add_argument("--date", default=None, help="CCYYMMDD (default: today UTC)")
    parser.add_argument("--stamp", default=None,
                        help="ISO timestamp for changed records (default: now)")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    rows = base_rows()
    if args.records:
        rows = synthesise(rows, args.records, rng)

    if args.drop:
        # Drop from the end so the retained keys stay stable across runs.
        rows = rows[: max(0, len(rows) - args.drop)]

    changed_stamp = args.stamp or datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    if args.change_rate > 0:
        every = max(1, int(1 / args.change_rate))
        for i, row in enumerate(rows):
            if i % every == 0:
                row["LAST_UPD_TS"] = changed_stamp
                row["SecurityDesc"] = (row["SecurityDesc"] or "")[:40] + " *"

    for j in range(args.add):
        rows.append({
            **{c: "" for c in COLUMNS},
            "Vendor_Ref": f"VND-NEW-{j + 1:04d}",
            "SecurityDesc": f"NEWLY LISTED INSTRUMENT {j + 1}",
            "ASSET_CLS": "EQ", "CCY": "USD", "CNTRY_DOM": "US",
            "ISSUE_STATUS": "ACT",
            "LAST_UPD_TS": changed_stamp,
        })

    # The adapter's sort-merge requires key order; the vendor delivers sorted,
    # and so must the generator.
    rows.sort(key=lambda r: r["Vendor_Ref"])

    file_date = args.date or datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    out_dir = Path(args.out_dir or config.INGEST_LANDING_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"SECMASTER_{file_date}.csv"
    tmp = out_dir / f"SECMASTER_{file_date}.csv.tmp"

    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        f.write(f"{TRAILER_PREFIX},{len(rows)}\n")

    os.replace(tmp, final)
    print(f"wrote {final} — {len(rows)} records, trailer count {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
