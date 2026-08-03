"""Build the stub SaaS's fixed dataset from the seeded securities.

Run once; the resulting JSON is checked in and is the stub's only data source,
so the stub has no database dependency and the REST adapter tests are fully
deterministic.

    python scripts/generate_vendor_dataset.py

The rows deliberately carry the vendor's own inconsistencies (mixed asset-class
code generations, drifting country/currency casing, sentinel dates, "#N/A"
lookup failures) — the same conventions RawVendorSecurity documents. Curation
is what normalizes them; the feed never does.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymongo

from ods_ingest import config

OUT = Path(__file__).resolve().parents[1] / "src" / "ods_ingest" / "stub_saas" / \
    "vendor_securities.json"

ASSET_CLS = {
    "EQUITY": ["EQ", "Equity", "COM"],
    "GOVT_BOND": ["FI", "Bond", "GOVT"],
    "CORP_BOND": ["FI", "Bond", "CORP"],
    "FUND": ["FND", "Fund", "40ACT"],
}
COUNTRY = {
    "US": ["US", "USA", "UNITED STATES"],
    "CA": ["CA", "CAN", "CANADA"],
    "IE": ["IE", "IRL", "IRELAND"],
    "GB": ["GB", "GBR", "UNITED KINGDOM"],
}


def main() -> int:
    rng = random.Random(42)
    client: pymongo.MongoClient = pymongo.MongoClient(config.MONGODB_URI)
    securities = list(client[config.MONGODB_DB]["securities"].find({}))
    client.close()
    if not securities:
        raise SystemExit("No seeded securities — run scripts/seed_data.py first")

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i, sec in enumerate(securities, 1):
        listings = sec.get("listings") or []
        is_bond = sec["assetClass"] in ("GOVT_BOND", "CORP_BOND")
        cusip = sec.get("cusip")
        if cusip and cusip.startswith("0") and rng.random() < 0.3:
            cusip = cusip.lstrip("0")  # spreadsheet round-trip casualty

        coupon = sec.get("couponRate")
        maturity = None
        if is_bond and sec.get("maturityDate"):
            m = sec["maturityDate"]
            maturity = rng.choice([m.strftime("%Y%m%d"), m.strftime("%m/%d/%Y")])
        elif rng.random() < 0.3:
            maturity = rng.choice(["00000000", "99991231"])

        rows.append({
            "Vendor_Ref": f"VND-{i:06d}",
            "Cusip": cusip,
            "ISIN_CODE": sec.get("isin") or rng.choice(["N/A", None]),
            "sedol": listings[0]["sedol"] if listings else rng.choice(["#N/A", None]),
            "TICKER": sec.get("ticker"),
            "SecurityDesc": rng.choice([sec["description"], sec["description"].upper()]),
            "Issuer_Name": (sec.get("issuer") or "").upper() or None,
            "ASSET_CLS": rng.choice(ASSET_CLS.get(sec["assetClass"], ["OTH"])),
            "CPN_RATE": (rng.choice([f"{float(str(coupon)):06.3f}", str(coupon)])
                         if coupon is not None else ("0" if rng.random() < 0.5 else None)),
            "MATURITY_DT": maturity,
            "CCY": rng.choice([sec["currency"], sec["currency"], sec["currency"].lower()]),
            "CNTRY_DOM": rng.choice(COUNTRY.get(sec.get("country", ""), [sec.get("country", "")])),
            "CALLABLE_FLG": rng.choice(["Y", "N", "N", "U", None]) if is_bond else "N",
            "ISSUE_STATUS": rng.choice(["A", "ACT", "Active"]),
            "EXCH_CD": sec.get("exchange") or (listings[0]["micCode"] if listings else None),
            "LAST_UPD_TS": (base - timedelta(days=rng.randint(1, 60))).strftime(
                "%Y-%m-%d %H:%M:%S"),
            # The stub's own change-tracking column, not part of the raw model.
            "updated_at": (base + timedelta(minutes=i)).isoformat(),
        })

    # Vendor-only instruments the curated master does not carry — these exercise
    # the "unmatched vendor record" path in curation and reconciliation.
    for j, extra in enumerate([
        {"SecurityDesc": "EUROCLEAR ELIGIBLE MTN 1.85% 2033", "ISIN_CODE": "XS2010028699",
         "Issuer_Name": "KOMMUNINVEST I SVERIGE AB", "ASSET_CLS": "Bond", "CPN_RATE": "01.850",
         "CCY": "EUR", "CNTRY_DOM": "SWEDEN", "MATURITY_DT": "20330901", "sedol": "#N/A"},
        {"SecurityDesc": "First Amer Govt Oblig Fd Cl X", "Cusip": "31846V567", "TICKER": "FGXXX",
         "Issuer_Name": "FIRST AMERICAN FUNDS", "ASSET_CLS": "1", "CPN_RATE": "0", "CCY": "usd",
         "CNTRY_DOM": "USA", "MATURITY_DT": "99991231", "ISSUE_STATUS": "MAT'D"},
    ], 1):
        row = {
            "Vendor_Ref": f"VND-{len(securities) + j:06d}", "Cusip": None, "ISIN_CODE": None,
            "sedol": None, "TICKER": None, "Issuer_Name": None, "ASSET_CLS": "OTH",
            "CPN_RATE": None, "MATURITY_DT": None, "CCY": None, "CNTRY_DOM": None,
            "CALLABLE_FLG": None, "ISSUE_STATUS": "ACT", "EXCH_CD": None,
            "LAST_UPD_TS": "2025-11-30 04:12:44",
            "updated_at": (base + timedelta(minutes=len(securities) + j)).isoformat(),
        }
        row.update(extra)
        rows.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")
    print(f"wrote {OUT} — {len(rows)} vendor rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
