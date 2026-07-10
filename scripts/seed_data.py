"""Seed script — drops and repopulates all bank_ods collections (both tiers).

Semantic tier: accounts, securities, transactions, settlements, positions,
cash_balances. Raw tier: raw_custody_positions (fixed-width mainframe batch
extract conventions) and raw_vendor_securities (as-received vendor feed with
its natural inconsistencies).
"""
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128
from dotenv import load_dotenv
from faker import Faker
import pymongo

from bank_ods.services._common import custody_acct_nbr, cusip_from_isin

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
# Reference-data identifier generators (format-valid fakes)
# ---------------------------------------------------------------------------

_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9)
_SEDOL_CHARS = "0123456789BCDFGHJKLMNPQRSTVWXYZ"  # SEDOLs exclude vowels
_used_sedols: set[str] = set()


def make_sedol() -> str:
    """7-char LSEG SEDOL: 6 alphanumeric (no vowels) + weighted mod-10 check digit.

    The multikey unique index only enforces uniqueness across documents, so the
    generator guarantees global uniqueness itself via _used_sedols.
    """
    while True:
        body = "B" + "".join(rng.choice(_SEDOL_CHARS) for _ in range(5))
        check = (10 - sum(w * int(c, 36) for w, c in zip(_SEDOL_WEIGHTS, body)) % 10) % 10
        sedol = f"{body}{check}"
        if sedol not in _used_sedols:
            _used_sedols.add(sedol)
            return sedol


_LEI_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def make_lei() -> str:
    """20-char ISO 17442 LEI: LOU-prefixed 18-char base + ISO 7064 MOD 97-10 check pair."""
    base = "549300" + "".join(rng.choice(_LEI_CHARS) for _ in range(12))
    n = int("".join(str(int(ch, 36)) for ch in base + "00"))
    return base + f"{98 - n % 97:02d}"


def make_figi() -> str:
    """12-char fake OpenFIGI share-class FIGI."""
    return "BBG00" + "".join(rng.choice(_SEDOL_CHARS) for _ in range(7))


# exchange -> (operating MIC, segment MIC, country of listing, settlement CSD BIC)
# MICs per ISO 10383; settlement location is the market's CSD (DTC / CDS / CREST).
EXCHANGE_MICS = {
    "NASDAQ": ("XNAS", "XNGS", "US", "DTCYUS33"),
    "NYSE": ("XNYS", "XNYS", "US", "DTCYUS33"),
    "TSX": ("XTSE", "XTSE", "CA", "CDSLCATT"),
    "LSE": ("XLON", "XLON", "GB", "CRSTGB22"),
}


def make_listing(exchange: str, currency: str, local_code: str | None = None,
                 primary: bool = True) -> dict:
    op_mic, mic, country, csd = EXCHANGE_MICS[exchange]
    return {
        "sedol": make_sedol(),
        "micCode": mic,
        "operatingMic": op_mic,
        "exchangeName": exchange,
        "tradedCurrency": currency,
        "countryOfListing": country,
        "settlementLocation": csd,
        "localCode": local_code,
        "primaryListing": primary,
        "status": "ACTIVE",
    }


# Genuinely dual-listed equities: ticker -> secondary listings (exchange, currency, localCode).
DUAL_LISTINGS = {
    "RY.TO": [("NYSE", "USD", "RY")],
    "TD.TO": [("NYSE", "USD", "TD")],
    "BNS.TO": [("NYSE", "USD", "BNS")],
}

# Funds with an extra traded-currency line on the same venue (one SEDOL per
# traded currency since 2008): ISIN -> extra currency.
FUND_EXTRA_CURRENCY = {"IE00B4L5Y983": "GBP"}


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
    secs: list[dict[str, Any]] = []
    for i, (ticker, isin, cusip, desc, country, exchange, issuer) in enumerate(EQUITY_SPECS, 1):
        currency = "CAD" if country == "CA" else "USD"
        local_code = ticker.removesuffix(".TO")
        listings = [make_listing(exchange, currency, local_code)]
        for ex2, cur2, code2 in DUAL_LISTINGS.get(ticker, []):
            listings.append(make_listing(ex2, cur2, code2, primary=False))
        secs.append({
            "securityId": f"SEC-{i:06d}",
            "isin": isin,
            "cusip": cusip,
            "ticker": ticker,
            "figi": make_figi(),
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
            "listings": listings,
            "createdAt": now,
            "updatedAt": now,
        })
    base = len(EQUITY_SPECS)
    for i, (b_isin, b_cusip, b_desc, b_country, b_currency, coupon, mat) in enumerate(BOND_SPECS, 1):
        secs.append({
            "securityId": f"SEC-{base + i:06d}",
            "isin": b_isin,
            "cusip": b_cusip,
            "ticker": None,
            "figi": None,
            "description": b_desc,
            "assetClass": "GOVT_BOND" if "TREASURY" in b_desc or "CANADA" in b_desc else "CORP_BOND",
            "subType": "FIXED_RATE",
            "currency": b_currency,
            "exchange": None,
            "issuer": b_desc.split()[0],
            "country": b_country,
            "maturityDate": datetime.fromisoformat(mat).replace(tzinfo=timezone.utc),
            "couponRate": coupon,
            "status": "ACTIVE",
            "listings": [],
            "createdAt": now,
            "updatedAt": now,
        })
    base2 = base + len(BOND_SPECS)
    for i, (f_isin, f_cusip, f_desc, f_country, f_currency) in enumerate(FUND_SPECS, 1):
        fund_exchange = "TSX" if f_country == "CA" else "LSE"
        listings = [make_listing(fund_exchange, f_currency)]
        extra_currency = FUND_EXTRA_CURRENCY.get(f_isin)
        if extra_currency:
            listings.append(make_listing(fund_exchange, extra_currency, primary=False))
        secs.append({
            "securityId": f"SEC-{base2 + i:06d}",
            "isin": f_isin,
            "cusip": f_cusip,
            "ticker": None,
            "figi": make_figi(),
            "description": f_desc,
            "assetClass": "FUND",
            "subType": "ETF",
            "currency": f_currency,
            "exchange": fund_exchange,
            "issuer": f_desc.split()[0],
            "country": f_country,
            "maturityDate": None,
            "couponRate": None,
            "status": "ACTIVE",
            "listings": listings,
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

# One value per client (index-aligned with client_ids) — deterministic spread.
CLIENT_DOMICILES = ["CA", "CA", "CA", "CA", "US", "US", "US", "GB", "GB", "IE"]
CLIENT_CLASSIFICATIONS = ["PROFESSIONAL"] * 6 + ["ELIGIBLE_COUNTERPARTY"] * 3 + ["RETAIL"]
CLIENT_ENTITY_TYPES = [
    "CORPORATION", "CORPORATION", "FUND", "PARTNERSHIP", "CORPORATION",
    "FUND", "TRUST", "CORPORATION", "GOVERNMENT", "INDIVIDUAL",
]
CLIENT_KYC = ["APPROVED"] * 8 + ["PENDING_REVIEW", "EXPIRED"]
CLIENT_RISK = ["LOW"] * 5 + ["MEDIUM"] * 4 + ["HIGH"]


def build_client_masters(client_ids: list[str], client_names: list[str]) -> dict[str, dict]:
    """One client-master snapshot per client so every account of a client
    embeds identical values (linkage key: LEI, ISO 17442)."""
    masters = {}
    for idx, cid in enumerate(client_ids):
        domicile = CLIENT_DOMICILES[idx]
        extra_tax = {"US"} if idx in (2, 7) else set()
        masters[cid] = {
            "clientId": cid,
            "clientName": client_names[idx],
            "lei": make_lei(),
            "countryOfDomicile": domicile,
            "countryOfIncorporation": domicile,
            "taxResidencies": sorted({domicile} | extra_tax),
            "classification": CLIENT_CLASSIFICATIONS[idx],
            "kycStatus": CLIENT_KYC[idx],
            "riskRating": CLIENT_RISK[idx],
            "legalEntityType": CLIENT_ENTITY_TYPES[idx],
            "parentClientId": client_ids[0] if idx in (1, 2) else None,
        }
    return masters


def build_accounts() -> list[dict]:
    now = datetime.now(tz=timezone.utc)
    accounts = []
    client_ids = [f"CLT-{i:06d}" for i in range(1, 11)]
    client_names = [fake.company() for _ in client_ids]
    client_masters = build_client_masters(client_ids, client_names)
    for i in range(20):
        clt_idx = i % 10
        acc_type = rng.choice(ACCOUNT_TYPES)
        status = STATUSES[i]
        open_date = date_offset(rng.randint(365 * 2, 365 * 8))
        accounts.append({
            "accountId": f"ACC-{i + 1:06d}",
            "accountName": f"{client_names[clt_idx]} - {acc_type.title()}",
            "accountType": acc_type,
            "client": dict(client_masters[client_ids[clt_idx]]),
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
# Raw tier — records kept in their source wire format (see the raw_* model
# docstrings in bank_ods/models). Everything is a string on purpose.
# ---------------------------------------------------------------------------

# Zoned-decimal sign overpunch: the last digit's zone nibble carries the sign.
_OVERPUNCH_POS = "{ABCDEFGHI"  # 0-9 positive
_OVERPUNCH_NEG = "}JKLMNOPQR"  # 0-9 negative


def zoned(value: float, digits: int, scale: int, signed: bool = False) -> str:
    """Render a number as display (zoned) decimal: implied decimal point,
    right-justified, zero-filled — e.g. zoned(850.5, 12, 4) == "0000000008505000".
    With signed=True the last character carries the sign as an overpunch."""
    raw = abs(round(value * 10**scale))
    s = f"{raw:0{digits + scale}d}"
    if not signed:
        return s
    table = _OVERPUNCH_NEG if value < 0 else _OVERPUNCH_POS
    return s[:-1] + table[int(s[-1])]


def julian(dt: datetime) -> str:
    """CCYYDDD julian date used by batch systems."""
    return f"{dt.year:04d}{dt.timetuple().tm_yday:03d}"


_ASSET_CLS_CD = {"EQUITY": "EQ", "GOVT_BOND": "FI", "CORP_BOND": "FI", "FUND": "FND"}
_ACCT_TYPE_CD = {"CUSTODY": "CU", "PROPRIETARY": "PR", "OMNIBUS": "OM"}
_LOC_BY_COUNTRY = {"US": "DTC", "CA": "CDS"}


def build_raw_custody_positions(accounts: list[dict], securities: list[dict]) -> list[dict]:
    """Two batch cycles of fixed-width position detail records (record type 03)."""
    cycles = [date_offset(2), date_offset(1)]  # two most recent nightly runs
    records = []
    for cycle_dt in cycles:
        cycle = cycle_dt.strftime("%Y%m%d")
        seq = 0
        for acct in accounts:
            held = rng.sample(securities, 3)
            for sec in held:
                seq += 1
                qty = rng.uniform(100, 10000)
                price = rng.uniform(5, 800)
                is_fi = sec["assetClass"] in ("GOVT_BOND", "CORP_BOND")
                # CUSIP is all-or-nothing: 9 chars or empty (partial fill is a reject)
                cusip = sec.get("cusip") or cusip_from_isin(sec.get("isin")) or ""
                records.append({
                    "REC_ID": f"{cycle}-{seq:06d}",
                    "POS_REC_TYPE": "03",
                    "POS_BUS_DATE": cycle,
                    "POS_BANK_NBR": "003",
                    "POS_BRANCH_CD": acct["custodianBranch"][:4].upper(),
                    "POS_ACCT_NBR": custody_acct_nbr(acct["accountId"]),
                    "POS_ACCT_TYPE_CD": _ACCT_TYPE_CD.get(acct["accountType"], "CU"),
                    "POS_CUSIP_NBR": cusip,
                    "POS_ISIN_NBR": sec.get("isin") or "",
                    "POS_SEC_DESC": sec["description"].upper()[:40],
                    "POS_ASSET_CLS_CD": _ASSET_CLS_CD.get(sec["assetClass"], "EQ"),
                    "POS_REG_TYPE_CD": rng.choice(["S", "S", "S", "N"]),
                    "POS_LOC_CD": rng.choice(
                        [_LOC_BY_COUNTRY.get(sec["country"], "PHYS")] * 8 + ["SEG", "PHYS"]
                    ),
                    "POS_SHR_QTY": zoned(qty, 12, 4),
                    "POS_SHR_QTY_PEND": zoned(rng.choice([0, 0, 0, rng.uniform(0, 200)]), 12, 4),
                    "POS_MKT_PRICE": zoned(price, 3, 12),
                    "POS_MKT_VALUE": zoned(qty * price, 13, 2),
                    "POS_ACCR_INT": zoned(rng.uniform(-500, 3000) if is_fi else 0, 9, 2, signed=True),
                    "POS_PRICE_DT": julian(cycle_dt),
                    "POS_LAST_ACTVY_DT": date_offset(rng.randint(2, 30)).strftime("%Y%m%d"),
                    "POS_PLEDGE_IND": rng.choice(["N"] * 8 + ["Y", ""]),
                    "POS_CCY_CD": sec["currency"],
                    "POS_SRC_SYS_ID": "TRSTACCT",
                })
    return records


# The vendor's asset-class code list has changed twice; deliveries mix all
# three generations. Same story for country, currency case, and date formats.
_VENDOR_ASSET_CLS = {
    "EQUITY": ["EQ", "Equity", "COM"],
    "GOVT_BOND": ["FI", "Bond", "GOVT"],
    "CORP_BOND": ["FI", "Bond", "CORP"],
    "FUND": ["FND", "Fund", "40ACT"],
}
_VENDOR_COUNTRY = {"US": ["US", "USA", "UNITED STATES"], "CA": ["CA", "CAN", "CANADA"],
                   "IE": ["IE", "IRL", "IRELAND"]}


def build_raw_vendor_securities(securities: list[dict]) -> list[dict]:
    """One as-received vendor row per instrument, plus a few vendor-only rows."""
    rows = []
    for i, sec in enumerate(securities, 1):
        cusip = sec.get("cusip")
        if cusip and cusip.startswith("0") and rng.random() < 0.3:
            cusip = cusip.lstrip("0")  # classic spreadsheet round-trip casualty
        ticker = sec.get("ticker")
        if ticker:
            ticker = rng.choice([ticker, ticker, f"{ticker.removesuffix('.TO')} "
                                 + ("CN" if sec["country"] == "CA" else "US"), ticker.lower() + " "])
        listings = sec.get("listings") or []
        sedol = listings[0]["sedol"] if listings else rng.choice(["#N/A", None])
        is_bond = sec["assetClass"] in ("GOVT_BOND", "CORP_BOND")
        maturity = None
        if is_bond and sec.get("maturityDate"):
            m = sec["maturityDate"]
            maturity = rng.choice([m.strftime("%Y%m%d"), m.strftime("%m/%d/%Y")])
        elif rng.random() < 0.4:
            maturity = rng.choice(["00000000", "99991231"])
        coupon = sec.get("couponRate")
        rows.append({
            "Vendor_Ref": f"VND-{i:06d}",
            "Cusip": cusip,
            "ISIN_CODE": sec.get("isin") or rng.choice(["N/A", None]),
            "sedol": sedol,
            "TICKER": ticker,
            "SecurityDesc": rng.choice([sec["description"], sec["description"].upper()]),
            "Issuer_Name": (sec.get("issuer") or "").upper() or None,
            "ASSET_CLS": rng.choice(_VENDOR_ASSET_CLS.get(sec["assetClass"], ["OTH"])),
            "CPN_RATE": rng.choice([f"{coupon:06.3f}", str(coupon)]) if coupon is not None else
                        ("0" if rng.random() < 0.5 else None),
            "CCY": rng.choice([sec["currency"], sec["currency"], sec["currency"].lower()]),
            "CNTRY_DOM": rng.choice(_VENDOR_COUNTRY.get(sec["country"], [sec["country"]])),
            "MATURITY_DT": maturity,
            "CALLABLE_FLG": rng.choice(["Y", "N", "N", "U", None]) if is_bond else "N",
            "ISSUE_STATUS": rng.choice(["A", "ACT", "Active"]),
            "EXCH_CD": rng.choice([sec.get("exchange"), listings[0]["micCode"] if listings else None]),
            "LAST_UPD_TS": rng.choice([
                (TODAY - timedelta(days=rng.randint(1, 20))).strftime("%Y-%m-%d %H:%M:%S"),
                (TODAY - timedelta(days=rng.randint(1, 20))).strftime("%d-%b-%y").upper(),
            ]),
        })
    # Vendor-only rows — instruments the curated master doesn't carry
    rows.append({
        "Vendor_Ref": f"VND-{len(securities) + 1:06d}",
        "Cusip": None, "ISIN_CODE": "XS2010028699", "sedol": "#N/A",
        "TICKER": None, "SecurityDesc": "EUROCLEAR ELIGIBLE MTN 1.85% 2033",
        "Issuer_Name": "KOMMUNINVEST I SVERIGE AB", "ASSET_CLS": "Bond",
        "CPN_RATE": "01.850", "CCY": "EUR", "CNTRY_DOM": "SWEDEN",
        "MATURITY_DT": "20330901", "CALLABLE_FLG": "N", "ISSUE_STATUS": "ACT",
        "EXCH_CD": None, "LAST_UPD_TS": "14-FEB-25",
    })
    rows.append({
        "Vendor_Ref": f"VND-{len(securities) + 2:06d}",
        "Cusip": "31846V567", "ISIN_CODE": None, "sedol": None,
        "TICKER": "FGXXX", "SecurityDesc": "First Amer Govt Oblig Fd Cl X",
        "Issuer_Name": "FIRST AMERICAN FUNDS", "ASSET_CLS": "1",
        "CPN_RATE": "0", "CCY": "usd", "CNTRY_DOM": "USA",
        "MATURITY_DT": "99991231", "CALLABLE_FLG": None, "ISSUE_STATUS": "MAT'D",
        "EXCH_CD": "N", "LAST_UPD_TS": "2025-11-30 04:12:44",
    })
    return rows


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
    client: pymongo.MongoClient = pymongo.MongoClient(MONGODB_URI)
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
    print("Building raw custody positions...")
    raw_custody_positions = build_raw_custody_positions(accounts, securities)
    print("Building raw vendor securities...")
    raw_vendor_securities = build_raw_vendor_securities(securities)

    collections = [
        ("accounts", accounts),
        ("securities", securities),
        ("transactions", transactions),
        ("settlements", settlements),
        ("positions", positions),
        ("cash_balances", cash_balances),
        ("raw_custody_positions", raw_custody_positions),
        ("raw_vendor_securities", raw_vendor_securities),
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
