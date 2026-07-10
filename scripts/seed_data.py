"""Seed script — drops and repopulates all six bank_ods collections."""
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bson.decimal128 import Decimal128
from dotenv import load_dotenv
from faker import Faker
import pymongo

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "bank_ods")

fake = Faker()
rng = random.Random(42)

TODAY = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def date_offset(days: int) -> datetime:
    return TODAY - timedelta(days=days)


def eod(dt: datetime) -> datetime:
    return dt.replace(hour=16, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Securities master
# ---------------------------------------------------------------------------

EQUITY_SPECS = [
    ("AAPL",  "US0378331005", "037833100", "Apple Inc Common Stock",        "US",  "NASDAQ", "Apple Inc"),
    ("MSFT",  "US5949181045", "594918104", "Microsoft Corporation",          "US",  "NASDAQ", "Microsoft Corporation"),
    ("GOOGL", "US02079K3059", "02079K305", "Alphabet Inc Class A",           "US",  "NASDAQ", "Alphabet Inc"),
    ("AMZN",  "US0231351067", "023135106", "Amazon.com Inc",                 "US",  "NASDAQ", "Amazon.com Inc"),
    ("META",  "US30303M1027", "30303M102", "Meta Platforms Inc",             "US",  "NASDAQ", "Meta Platforms Inc"),
    ("TSLA",  "US88160R1014", "88160R101", "Tesla Inc",                      "US",  "NASDAQ", "Tesla Inc"),
    ("NVDA",  "US67066G1040", "67066G104", "NVIDIA Corporation",             "US",  "NASDAQ", "NVIDIA Corporation"),
    ("JPM",   "US46625H1005", "46625H100", "JPMorgan Chase & Co",            "US",  "NYSE",   "JPMorgan Chase"),
    ("GS",    "US38141G1040", "38141G104", "Goldman Sachs Group Inc",        "US",  "NYSE",   "Goldman Sachs"),
    ("BAC",   "US0605051046", "060505104", "Bank of America Corporation",    "US",  "NYSE",   "Bank of America"),
    ("WMT",   "US9311421039", "931142103", "Walmart Inc",                    "US",  "NYSE",   "Walmart Inc"),
    ("JNJ",   "US4781601046", "478160104", "Johnson & Johnson",              "US",  "NYSE",   "Johnson & Johnson"),
    ("PG",    "US7427181091", "742718109", "Procter & Gamble Co",            "US",  "NYSE",   "Procter & Gamble"),
    ("UNH",   "US91324P1021", "91324P102", "UnitedHealth Group Inc",         "US",  "NYSE",   "UnitedHealth Group"),
    ("XOM",   "US30231G1022", "30231G102", "Exxon Mobil Corporation",        "US",  "NYSE",   "Exxon Mobil"),
    ("RY.TO", "CA7800871021", "780087102", "Royal Bank of Canada",           "CA",  "TSX",    "Royal Bank of Canada"),
    ("TD.TO", "CA8911605092", "891160509", "Toronto-Dominion Bank",          "CA",  "TSX",    "TD Bank"),
    ("BNS.TO","CA0641491075", "064149107", "Bank of Nova Scotia",            "CA",  "TSX",    "Scotiabank"),
    ("CNR.TO","CA2041124169", "204112416", "Canadian National Railway",      "CA",  "TSX",    "Canadian National Railway"),
    ("SU.TO", "CA8672241079", "867224107", "Suncor Energy Inc",              "CA",  "TSX",    "Suncor Energy"),
    ("ENB.TO","CA29250N1050", "29250N105", "Enbridge Inc",                   "CA",  "TSX",    "Enbridge Inc"),
    ("BCE.TO","CA05534B7604", "05534B760", "BCE Inc",                        "CA",  "TSX",    "BCE Inc"),
    ("T.TO",  "CA8911021050", "891102105", "TELUS Corporation",              "CA",  "TSX",    "TELUS"),
    ("CP.TO", "CA13645T1003", "13645T100", "Canadian Pacific Kansas City",   "CA",  "TSX",    "CPKC"),
    ("MFC.TO","CA56501R1064", "56501R106", "Manulife Financial Corporation", "CA",  "TSX",    "Manulife"),
    ("ABX.TO","CA0679011084", "067901108", "Barrick Gold Corporation",       "CA",  "TSX",    "Barrick Gold"),
    ("CCO.TO","CA1348541091", "134854109", "Cameco Corporation",             "CA",  "TSX",    "Cameco"),
    ("POW.TO","CA7392391016", "739239101", "Power Corporation of Canada",    "CA",  "TSX",    "Power Corporation"),
    ("EMA.TO","CA2908761018", "290876101", "Emera Inc",                      "CA",  "TSX",    "Emera Inc"),
    ("FTS.TO","CA3359711011", "335971101", "Fortis Inc",                     "CA",  "TSX",    "Fortis Inc"),
]

BOND_SPECS = [
    ("US912828YV68", None, "US TREASURY 2.5% 2027",  "US",  "USD", 2.5,  "2027-02-15"),
    ("US912828Z377", None, "US TREASURY 3.0% 2029",  "US",  "USD", 3.0,  "2029-08-15"),
    ("US912828ZL72", None, "US TREASURY 1.75% 2031", "US",  "USD", 1.75, "2031-01-31"),
    ("CA135087G753", None, "CANADA 2.75% 2028",      "CA",  "CAD", 2.75, "2028-06-01"),
    ("CA135087H660", None, "CANADA 3.25% 2030",      "CA",  "CAD", 3.25, "2030-12-01"),
    ("CA135087J229", None, "CANADA 1.50% 2031",      "CA",  "CAD", 1.5,  "2031-03-01"),
    ("CA135087K267", None, "CANADA 2.00% 2032",      "CA",  "CAD", 2.0,  "2032-09-01"),
    ("US38141GXZ07", None, "GS CORP BOND 4.0% 2028", "US",  "USD", 4.0,  "2028-10-26"),
    ("CA056501RB29", None, "MFC CORP BOND 3.5% 2030","CA",  "CAD", 3.5,  "2030-05-19"),
    ("CA46625HBC41", None, "JPM CORP BOND 3.8% 2029","US",  "USD", 3.8,  "2029-07-23"),
    ("CA80928KAC68", None, "SCB CORP BOND 3.1% 2027","CA",  "CAD", 3.1,  "2027-11-01"),
    ("US594918BN21", None, "MSFT CORP BOND 2.9% 2052","US", "USD", 2.9,  "2052-03-17"),
    ("US0231350AK69",None, "AMZN CORP BOND 4.1% 2031","US", "USD", 4.1,  "2031-08-01"),
    ("CA89114QBP86", None, "TD CORP BOND 3.6% 2029", "CA",  "CAD", 3.6,  "2029-04-02"),
    ("CA0641491GL05",None, "BNS CORP BOND 3.4% 2028","CA",  "CAD", 3.4,  "2028-01-23"),
]

FUND_SPECS = [
    ("IE00B4L5Y983", None, "iShares Core MSCI World ETF",    "IE", "USD"),
    ("IE00B3XXRP09", None, "Vanguard FTSE All-World ETF",    "IE", "USD"),
    ("CA46432F1018", None, "iShares S&P/TSX 60 Index ETF",  "CA", "CAD"),
    ("CA0641532049", None, "TD Canadian Bond Index ETF",     "CA", "CAD"),
    ("CA46432F1117", None, "iShares US Equity Index ETF CAD","CA", "CAD"),
]


def build_securities() -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    secs = []
    for i, (ticker, isin, cusip, desc, country, exchange, issuer) in enumerate(EQUITY_SPECS, 1):
        currency = "CAD" if country == "CA" else "USD"
        secs.append({
            "securityId": f"SEC-{i:06d}",
            "isin": isin,
            "cusip": cusip,
            "ticker": ticker,
            "description": desc,
            "assetClass": "EQUITY",
            "subType": "COMMON_STOCK",
            "currency": currency,
            "exchange": exchange,
            "issuer": issuer,
            "country": country,
            "maturityDate": None,
            "couponRate": None,
            "status": "ACTIVE",
            "createdAt": now,
            "updatedAt": now,
        })
    base = len(EQUITY_SPECS)
    for i, (isin, cusip, desc, country, currency, coupon, mat) in enumerate(BOND_SPECS, 1):
        secs.append({
            "securityId": f"SEC-{base + i:06d}",
            "isin": isin,
            "cusip": cusip,
            "ticker": None,
            "description": desc,
            "assetClass": "GOVT_BOND" if "TREASURY" in desc or "CANADA" in desc else "CORP_BOND",
            "subType": "FIXED_RATE",
            "currency": currency,
            "exchange": None,
            "issuer": desc.split()[0],
            "country": country,
            "maturityDate": datetime.fromisoformat(mat).replace(tzinfo=timezone.utc),
            "couponRate": coupon,
            "status": "ACTIVE",
            "createdAt": now,
            "updatedAt": now,
        })
    base2 = base + len(BOND_SPECS)
    for i, (isin, cusip, desc, country, currency) in enumerate(FUND_SPECS, 1):
        secs.append({
            "securityId": f"SEC-{base2 + i:06d}",
            "isin": isin,
            "cusip": cusip,
            "ticker": None,
            "description": desc,
            "assetClass": "FUND",
            "subType": "ETF",
            "currency": currency,
            "exchange": "TSX" if country == "CA" else "LSE",
            "issuer": desc.split()[0],
            "country": country,
            "maturityDate": None,
            "couponRate": None,
            "status": "ACTIVE",
            "createdAt": now,
            "updatedAt": now,
        })
    return secs


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

ACCOUNT_TYPES = ["CUSTODY", "CUSTODY", "CUSTODY", "PROPRIETARY", "OMNIBUS"]
STATUSES = ["ACTIVE"] * 17 + ["SUSPENDED"] * 2 + ["CLOSED"] * 1
BRANCHES = ["Toronto", "Toronto", "Toronto", "Montreal", "Vancouver", "New York", "London"]


def build_accounts() -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    accounts = []
    client_ids = [f"CLT-{i:06d}" for i in range(1, 11)]
    client_names = [fake.company() for _ in client_ids]
    for i in range(20):
        clt_idx = i % 10
        acc_type = rng.choice(ACCOUNT_TYPES)
        status = STATUSES[i]
        open_date = date_offset(rng.randint(365 * 2, 365 * 8))
        accounts.append({
            "accountId": f"ACC-{i + 1:06d}",
            "accountName": f"{client_names[clt_idx]} - {acc_type.title()}",
            "accountType": acc_type,
            "clientId": client_ids[clt_idx],
            "clientName": client_names[clt_idx],
            "baseCurrency": rng.choice(["CAD", "USD"]),
            "status": status,
            "openDate": open_date,
            "closeDate": date_offset(30) if status == "CLOSED" else None,
            "custodianBranch": rng.choice(BRANCHES),
            "createdAt": now,
            "updatedAt": now,
        })
    return accounts


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

TXN_TYPE_WEIGHTS = (
    ["BUY"] * 35 + ["SELL"] * 35
    + ["DIVIDEND"] * 10 + ["FX"] * 10
    + ["DEPOSIT"] * 5 + ["WITHDRAWAL"] * 5
)
TXN_STATUS_WEIGHTS = ["SETTLED"] * 80 + ["PENDING"] * 10 + ["FAILED"] * 5 + ["CANCELLED"] * 5
CPTY_IDS = ["CPTY-GOLDM", "CPTY-MSTANL", "CPTY-CIBC", "CPTY-BMO", "CPTY-RBC", "CPTY-TD", "CPTY-Scotia"]


def build_transactions(accounts: list[dict], securities: list[dict]) -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    equity_secs = [s for s in securities if s["assetClass"] == "EQUITY"]
    txns = []
    for i in range(2000):
        acct = rng.choice(accounts)
        txn_type = rng.choice(TXN_TYPE_WEIGHTS)
        status = rng.choice(TXN_STATUS_WEIGHTS)
        trade_days_ago = rng.randint(0, 89)
        trade_date = eod(date_offset(trade_days_ago))
        settle_days = 2
        settlement_date = eod(trade_date + timedelta(days=settle_days))

        is_security_txn = txn_type in ("BUY", "SELL", "DIVIDEND")
        sec = rng.choice(equity_secs) if is_security_txn else None
        quantity = round(rng.uniform(10, 5000), 0) if is_security_txn else None
        price = round(rng.uniform(5, 1000), 2) if is_security_txn else None
        currency = sec["currency"] if sec else acct["baseCurrency"]
        gross = round(quantity * price, 2) if (quantity and price) else round(rng.uniform(1000, 500000), 2)
        fees = round(gross * rng.uniform(0.0005, 0.002), 2)
        net = round(gross + fees if txn_type == "BUY" else gross - fees, 2)
        fx_rate = round(rng.uniform(1.30, 1.40), 4) if currency == "USD" and acct["baseCurrency"] == "CAD" else 1.0

        stl_ref = f"STL-{settlement_date.strftime('%Y%m%d')}-{i:06d}" if status in ("SETTLED", "PENDING", "FAILED") else None

        txns.append({
            "transactionId": f"TXN-{trade_date.strftime('%Y%m%d')}-{i:06d}",
            "transactionType": txn_type,
            "tradeDate": trade_date,
            "settlementDate": settlement_date,
            "accountId": acct["accountId"],
            "securityId": sec["securityId"] if sec else None,
            "quantity": quantity,
            "price": price,
            "currency": currency,
            "grossAmount": gross,
            "fees": fees,
            "netAmount": net,
            "fxRate": fx_rate,
            "counterpartyId": rng.choice(CPTY_IDS),
            "status": status,
            "settlementRef": stl_ref,
            "sourceSystem": rng.choice(["ORDER_MGMT", "SWIFT", "MANUAL"]),
            "internalRef": f"ORD-{trade_date.year}-{i:06d}",
            "createdAt": now,
            "updatedAt": now,
        })
    return txns


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------

FAIL_REASONS = [
    "Insufficient securities",
    "Account suspended",
    "Counterparty failed",
    "Standing settlement instruction mismatch",
    "Late instruction",
]


def build_settlements(transactions: list[dict], securities: list[dict]) -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    sec_by_id = {s["securityId"]: s for s in securities}
    settlements = []
    eligible = [t for t in transactions if t["status"] in ("SETTLED", "PENDING", "FAILED")]
    for txn in eligible[:1800]:
        stl_date = txn["settlementDate"]
        status = txn["status"]
        sec = sec_by_id.get(txn["securityId"])

        history = [{"status": "PENDING", "timestamp": txn["tradeDate"]}]
        if status in ("SETTLED", "FAILED", "PENDING"):
            history.append({"status": "INSTRUCTED", "timestamp": txn["tradeDate"] + timedelta(hours=2)})
        if status in ("SETTLED", "FAILED"):
            history.append({"status": "MATCHED", "timestamp": txn["tradeDate"] + timedelta(days=1)})
        if status == "SETTLED":
            history.append({"status": "SETTLED", "timestamp": stl_date})
        if status == "FAILED":
            history.append({"status": "FAILED", "timestamp": stl_date})

        delivery_type = "DVP" if txn["transactionType"] in ("BUY", "SELL") else "FOP"

        settlements.append({
            "settlementId": txn["settlementRef"],
            "transactionId": txn["transactionId"],
            "accountId": txn["accountId"],
            "securityId": txn["securityId"],
            "settlementDate": stl_date,
            "deliveryType": delivery_type,
            "quantity": txn["quantity"],
            "currency": txn["currency"],
            "settlementAmount": txn["netAmount"],
            "counterpartyId": txn["counterpartyId"],
            "counterpartyAccount": fake.iban(),
            "custodianAccount": fake.iban(),
            "status": status,
            "statusHistory": history,
            "failReason": rng.choice(FAIL_REASONS) if status == "FAILED" else None,
            "csdRef": f"DTCC-{stl_date.strftime('%Y')}-{fake.bothify('???###')}",
            "swiftRef": f"MT54X-{fake.bothify('?????????')}",
            "createdAt": now,
            "updatedAt": now,
        })
    return settlements


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def build_positions(accounts: list[dict], securities: list[dict]) -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    equity_secs = [s for s in securities if s["assetClass"] == "EQUITY"]
    positions = []
    seen = set()
    for acct in accounts:
        active_secs = rng.sample(equity_secs, min(10, len(equity_secs)))
        for sec in active_secs:
            base_qty = rng.uniform(100, 10000)
            cost_per = rng.uniform(5, 800)
            for day in range(45):
                as_of = date_offset(day)
                key = (acct["accountId"], sec["securityId"], as_of.date())
                if key in seen:
                    continue
                seen.add(key)
                qty = round(base_qty + rng.uniform(-50, 50), 2)
                price = round(cost_per * rng.uniform(0.9, 1.1), 2)
                market_val = round(qty * price, 2)
                cost_basis = round(qty * cost_per, 2)
                positions.append({
                    "positionId": f"POS-{acct['accountId']}-{sec['securityId']}-{as_of.strftime('%Y%m%d')}",
                    "accountId": acct["accountId"],
                    "securityId": sec["securityId"],
                    "asOfDate": as_of,
                    "quantity": qty,
                    "currency": sec["currency"],
                    "costBasis": cost_basis,
                    "marketPrice": price,
                    "marketValue": market_val,
                    "unrealizedPnL": round(market_val - cost_basis, 2),
                    "positionType": "LONG",
                    "snapshotType": "EOD",
                    "createdAt": now,
                    "updatedAt": now,
                })
                if len(positions) >= 1000:
                    return positions
    return positions


# ---------------------------------------------------------------------------
# Cash balances
# ---------------------------------------------------------------------------

def build_cash_balances(accounts: list[dict]) -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    balances = []
    for acct in accounts:
        for currency in ("CAD", "USD"):
            opening = round(rng.uniform(100_000, 5_000_000), 2)
            for day in range(10):
                as_of = date_offset(day)
                credits = round(rng.uniform(0, 50_000), 2)
                debits = round(rng.uniform(0, 50_000), 2)
                closing = round(opening + credits - debits, 2)
                pending_credits = round(rng.uniform(0, 20_000), 2)
                pending_debits = round(rng.uniform(0, 20_000), 2)
                projected = round(closing + pending_credits - pending_debits, 2)
                balances.append({
                    "balanceId": f"BAL-{acct['accountId']}-{currency}-{as_of.strftime('%Y%m%d')}",
                    "accountId": acct["accountId"],
                    "currency": currency,
                    "asOfDate": as_of,
                    "openingBalance": opening,
                    "credits": credits,
                    "debits": debits,
                    "closingBalance": closing,
                    "pendingCredits": pending_credits,
                    "pendingDebits": pending_debits,
                    "projectedBalance": projected,
                    "snapshotType": "EOD",
                    "createdAt": now,
                    "updatedAt": now,
                })
                opening = closing
    return balances


# ---------------------------------------------------------------------------
# Decimal128 conversion — monetary/quantity/rate fields are stored as
# MongoDB Decimal128 (exact precision), never as IEEE-754 doubles.
# ---------------------------------------------------------------------------

DECIMAL_FIELDS: dict[str, set[str]] = {
    "securities": {"couponRate"},
    "transactions": {"quantity", "price", "grossAmount", "fees", "netAmount", "fxRate"},
    "settlements": {"quantity", "settlementAmount"},
    "positions": {"quantity", "costBasis", "marketPrice", "marketValue", "unrealizedPnL"},
    "cash_balances": {
        "openingBalance", "credits", "debits", "closingBalance",
        "pendingCredits", "pendingDebits", "projectedBalance",
    },
}


def convert_decimals(docs: list[dict], fields: set[str]) -> list[dict]:
    for d in docs:
        for f in fields:
            v = d.get(f)
            if v is not None:
                d[f] = Decimal128(Decimal(str(v)))
    return docs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client = pymongo.MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]

    print("Building securities...")
    securities = build_securities()
    print("Building accounts...")
    accounts = build_accounts()
    print("Building transactions...")
    transactions = build_transactions(accounts, securities)
    print("Building settlements...")
    settlements = build_settlements(transactions, securities)
    print("Building positions...")
    positions = build_positions(accounts, securities)
    print("Building cash balances...")
    cash_balances = build_cash_balances(accounts)

    collections = [
        ("accounts", accounts),
        ("securities", securities),
        ("transactions", transactions),
        ("settlements", settlements),
        ("positions", positions),
        ("cash_balances", cash_balances),
    ]

    for name, docs in collections:
        convert_decimals(docs, DECIMAL_FIELDS.get(name, set()))
        db[name].drop()
        db[name].insert_many(docs)
        print(f"Inserted {len(docs)} {name}")

    print("\nFinal counts:")
    for name, _ in collections:
        print(f"  {name}: {db[name].count_documents({})}")

    client.close()


if __name__ == "__main__":
    main()
