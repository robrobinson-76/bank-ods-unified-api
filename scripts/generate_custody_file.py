"""Generate a fixed-width EOD custody position extract.

Stands in for the mainframe. Writes from the same layout table the adapter
reads (ods_ingest.adapters.file.fixed_width), so the two cannot drift.

    python scripts/generate_custody_file.py                      # 5,000 records
    python scripts/generate_custody_file.py --records 1000000    # benchmark cycle
    python scripts/generate_custody_file.py --unknown-rate 0.02  # plant reconcile misses

Accounts and securities are drawn from the seeded Mongo data so most records
resolve during curation; --unknown-rate plants records referencing entities the
ODS does not know, which is what exercises the reconciliation classifications.

The file is written as <name>.tmp and renamed on completion — the completeness
convention the watcher relies on to never read a half-written file.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pymongo
from dotenv import load_dotenv

from ods_ingest import config
from ods_ingest.adapters.file import fixed_width as fw
from ods_ingest.curation.decode import datetime_to_julian, decimal_to_zoned

load_dotenv()

ASSET_CLS_CD = {"EQUITY": "EQ", "GOVT_BOND": "FI", "CORP_BOND": "FI", "FUND": "FND"}
ACCT_TYPE_CD = {"CUSTODY": "CU", "PROPRIETARY": "PR", "OMNIBUS": "OM"}
LOC_BY_COUNTRY = {"US": "DTC", "CA": "CDS"}


def load_reference() -> tuple[list[dict], list[dict]]:
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB]
    # Only accounts whose id fits the mainframe's 12-digit numeric key. Accounts
    # from other sources (the CRM feed mints ids like ACC-NEW-<token>) simply
    # do not exist in this custody system, so a real extract would never
    # mention them.
    accounts = list(db["accounts"].find(
        {"accountId": {"$regex": r"^ACC-\d+$"}},
        {"accountId": 1, "accountType": 1, "custodianBranch": 1}))
    securities = list(db["securities"].find(
        {}, {"securityId": 1, "cusip": 1, "isin": 1, "description": 1,
             "assetClass": 1, "currency": 1, "country": 1}))
    client.close()
    if not accounts or not securities:
        raise SystemExit("No seeded accounts/securities — run scripts/seed_data.py first")
    return accounts, securities


def custody_acct_nbr(account_id: str) -> str:
    return f"{int(account_id.removeprefix('ACC-')):012d}"


def cusip_from_isin(isin: str | None) -> str | None:
    if isin and isin[:2] in ("US", "CA"):
        return isin[2:11]
    return None


def build_detail(rng: random.Random, acct: dict, sec: dict, cycle: str,
                 cycle_dt: datetime, unknown: bool) -> dict[str, str]:
    qty = Decimal(str(round(rng.uniform(100, 10000), 4)))
    price = Decimal(str(round(rng.uniform(5, 800), 12)))
    # Value is derived exactly from the two quantities, so the control totals
    # a consumer recomputes always tie back.
    value = (qty * price).quantize(Decimal("0.01"))
    is_fi = sec["assetClass"] in ("GOVT_BOND", "CORP_BOND")

    if unknown:
        # An account number the ODS has never heard of — the UNKNOWN_ACCOUNT case.
        acct_nbr = f"{rng.randint(900000, 999999):012d}"
        cusip, isin = "", ""
    else:
        acct_nbr = custody_acct_nbr(acct["accountId"])
        cusip = sec.get("cusip") or cusip_from_isin(sec.get("isin")) or ""
        isin = sec.get("isin") or ""

    return {
        "POS_REC_TYPE": fw.REC_TYPE_DETAIL,
        "POS_BUS_DATE": cycle,
        "POS_BANK_NBR": "003",
        "POS_BRANCH_CD": (acct.get("custodianBranch") or "TOR")[:4].upper(),
        "POS_ACCT_NBR": acct_nbr,
        "POS_ACCT_TYPE_CD": ACCT_TYPE_CD.get(acct.get("accountType", ""), "CU"),
        "POS_CUSIP_NBR": cusip,
        "POS_ISIN_NBR": isin,
        "POS_SEC_DESC": sec["description"].upper()[:40],
        "POS_ASSET_CLS_CD": ASSET_CLS_CD.get(sec["assetClass"], "EQ"),
        "POS_REG_TYPE_CD": rng.choice(["S", "S", "S", "N"]),
        "POS_LOC_CD": rng.choice(
            [LOC_BY_COUNTRY.get(sec.get("country", ""), "PHYS")] * 8 + ["SEG", "PHYS"]),
        "POS_SHR_QTY": decimal_to_zoned(qty, 12, 4),
        "POS_SHR_QTY_PEND": decimal_to_zoned(
            Decimal(str(round(rng.choice([0, 0, 0, rng.uniform(0, 200)]), 4))), 12, 4),
        "POS_MKT_PRICE": decimal_to_zoned(price, 3, 12),
        "POS_MKT_VALUE": decimal_to_zoned(value, 13, 2),
        "POS_ACCR_INT": decimal_to_zoned(
            Decimal(str(round(rng.uniform(-500, 3000) if is_fi else 0, 2))), 9, 2, signed=True),
        "POS_PRICE_DT": datetime_to_julian(cycle_dt),
        "POS_LAST_ACTVY_DT": cycle,
        "POS_PLEDGE_IND": rng.choice(["N"] * 8 + ["Y", ""]),
        "POS_CCY_CD": sec["currency"],
        "POS_SRC_SYS_ID": "TRSTACCT",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=5000)
    parser.add_argument("--cycle-date", default=None, help="CCYYMMDD (default: today UTC)")
    parser.add_argument("--unknown-rate", type=float, default=0.0,
                        help="fraction of records referencing unknown accounts")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default=None, help="default: the landing directory")
    parser.add_argument("--corrupt-trailer", action="store_true",
                        help="write a wrong control total, to exercise quarantine")
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    cycle_dt = (datetime.strptime(args.cycle_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                if args.cycle_date else datetime.now(tz=timezone.utc))
    cycle = cycle_dt.strftime("%Y%m%d")

    accounts, securities = load_reference()
    out_dir = Path(args.out_dir or config.INGEST_LANDING_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"CUSTPOS_{cycle}.dat"
    tmp_path = out_dir / f"CUSTPOS_{cycle}.dat.tmp"

    total_qty = Decimal(0)
    total_value = Decimal(0)

    with open(tmp_path, "w", encoding="ascii", newline="\n") as f:
        f.write(fw.pack(fw.HEADER_FIELDS, {
            "HDR_REC_TYPE": fw.REC_TYPE_HEADER,
            "HDR_BUS_DATE": cycle,
            "HDR_SRC_SYS_ID": "TRSTACCT",
            "HDR_CREATED_TS": cycle_dt.strftime("%Y%m%d%H%M%S"),
        }) + "\n")

        for _ in range(args.records):
            acct = rng.choice(accounts)
            sec = rng.choice(securities)
            unknown = rng.random() < args.unknown_rate
            rec = build_detail(rng, acct, sec, cycle, cycle_dt, unknown)
            total_qty += Decimal(rec["POS_SHR_QTY"]).scaleb(-4)
            total_value += Decimal(rec["POS_MKT_VALUE"]).scaleb(-2)
            f.write(fw.pack(fw.DETAIL_FIELDS, rec) + "\n")

        if args.corrupt_trailer:
            total_value += Decimal("1.00")  # a total that will not reconcile

        f.write(fw.pack(fw.TRAILER_FIELDS, {
            "TRL_REC_TYPE": fw.REC_TYPE_TRAILER,
            "TRL_BUS_DATE": cycle,
            "TRL_REC_COUNT": f"{args.records:09d}",
            "TRL_TOT_SHR_QTY": decimal_to_zoned(total_qty, 14, 4),
            "TRL_TOT_MKT_VALUE": decimal_to_zoned(total_value, 16, 2),
        }) + "\n")

    # Rename only once the bytes are all on disk: the watcher treats the
    # presence of the final name as the "file is complete" signal.
    os.replace(tmp_path, final_path)
    size_mb = final_path.stat().st_size / (1024 * 1024)
    print(f"wrote {final_path} — {args.records} records, {size_mb:.1f} MB, cycle {cycle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
