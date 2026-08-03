"""Generate an intraday cash-movement drop.

Small delimited files arriving several times a business day — the other half of
the file adapter's remit alongside the one enormous EOD extract.

    python scripts/generate_cash_movements.py --time 1030
    python scripts/generate_cash_movements.py --time 1445 --rows 60

Written as <name>.tmp and renamed on completion, the same completeness
convention the watcher relies on.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymongo

from ods_ingest import config
from ods_ingest.adapters.file.cash_csv import COLUMNS

MOVEMENT_TYPES = ["DEP", "WDL", "FEE", "INT", "DIV", "FX"]
NARRATIVES = [
    "CLIENT SUBSCRIPTION", "CUSTODY FEE Q3", "COUPON RECEIPT",
    "FX SETTLEMENT", "CASH SWEEP", "DIVIDEND CREDIT", "",
]


def custody_acct_nbr(account_id: str) -> str:
    return f"{int(account_id.removeprefix('ACC-')):012d}"


def load_accounts() -> list[dict]:
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    # Only accounts the cash system can key on — see generate_custody_file.py.
    accounts = list(client[config.MONGODB_DB]["accounts"].find(
        {"accountId": {"$regex": r"^ACC-\d+$"}},
        {"accountId": 1, "baseCurrency": 1}))
    client.close()
    if not accounts:
        raise SystemExit("No seeded accounts — run scripts/seed_data.py first")
    return accounts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=50)
    parser.add_argument("--date", default=None, help="CCYYMMDD (default: today UTC)")
    parser.add_argument("--time", default="1030", help="HHMM of the drop")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    file_date = args.date or datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    accounts = load_accounts()

    out_dir = Path(args.out_dir or config.INGEST_LANDING_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"CASHMOV_{file_date}_{args.time}.csv"
    tmp_path = out_dir / f"CASHMOV_{file_date}_{args.time}.csv.tmp"

    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for _ in range(args.rows):
            account = rng.choice(accounts)
            movement_type = rng.choice(MOVEMENT_TYPES)
            amount = rng.uniform(500, 250_000)
            # Withdrawals and fees leave the account: a leading minus, not an
            # overpunch — this feed is delimited, not zoned.
            if movement_type in ("WDL", "FEE"):
                amount = -amount
            writer.writerow({
                "MOV_ACCT_NBR": custody_acct_nbr(account["accountId"]),
                "MOV_CCY_CD": account.get("baseCurrency") or "USD",
                "MOV_AMT": f"{amount:.2f}",
                "MOV_TYPE_CD": movement_type,
                "MOV_VALUE_TS": f"{file_date} {args.time[:2]}:{args.time[2:]}:00",
                "MOV_NARRATIVE": rng.choice(NARRATIVES),
                "MOV_SRC_SYS_ID": "CASHMGMT",
            })

    os.replace(tmp_path, final_path)
    print(f"wrote {final_path} — {args.rows} movements, {file_date} {args.time}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
