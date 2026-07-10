# Agents Guide — Bank ODS MCP Server

## Overview

This guide covers how AI agents (Claude Code or any MCP-capable client) should interact with the `bank-ods` MCP server: tool naming conventions, parameter formats, query patterns, error handling, and pagination.

The MCP server is one of three transports sharing a single service layer. All tools delegate to `bank_ods.services.*` — the same functions called by the REST and GraphQL APIs. There are 18 read-only tools across six domains.

Two contract conventions apply everywhere:

- **`count` is the total.** List results return `{"count": <total matching>, "data": [<one page>]}`. More records exist while `skip + len(data) < count`.
- **Monetary values are exact strings.** Amounts, quantities, and rates are stored as Decimal128 and serialized as strings (e.g. `"18550.00"`), never floats. Timestamps are ISO 8601 with an explicit UTC offset (`2026-05-31T16:00:00+00:00`).

---

## MCP Server Identity

**Server name:** `bank-ods`  
**Transport:** `MCP_TRANSPORT` env var — `stdio` (default, Claude Desktop / VS Code) or `sse` (chatbot / K8s)  
**Start command:** `python -m bank_ods.mcp`

---

## Tool Naming Convention

All tools follow snake_case verb-noun patterns:

| Pattern | Examples |
|---|---|
| `get_<entity>` | `get_account`, `get_security`, `get_transaction`, `get_settlement` |
| `list_<entities>` | `list_accounts`, `list_securities` |
| `get_<entities>` | `get_transactions`, `get_positions`, `get_settlements` |
| `get_<entity>_<qualifier>` | `get_settlement_status`, `get_settlement_fails`, `get_transaction_summary`, `get_position_history`, `get_projected_balance` |

Singular (`get_account`) takes an ID and returns one record. Plural (`get_transactions`) takes filter parameters and returns a list.

---

## Complete Tool Reference

### Accounts

#### `get_account`

Fetch a single account by its account ID.

**Parameters:**
- `account_id: str` — e.g., `"ACC-0001"`

**Returns:** Full account document or `{"error": ..., "code": "NOT_FOUND"}`

---

#### `list_accounts`

List accounts with optional filters. Every account embeds its client-master snapshot under `client` (clientId, clientName, LEI, KYC status, risk rating, tax residencies).

**Parameters:**
- `client_id: str` *(optional)* — matches `client.clientId`
- `status: str` *(optional)* — `"ACTIVE"`, `"SUSPENDED"`, or `"CLOSED"`
- `lei: str` *(optional)* — 20-char ISO 17442 Legal Entity Identifier (`client.lei`)
- `domicile: str` *(optional)* — client's ISO 3166-1 alpha-2 country of domicile, e.g. `"CA"`
- `limit: int` *(optional, default 20, max 200)*
- `skip: int` *(optional, default 0)*

**Returns:** `{"count": N, "data": [...]}`

---

### Securities

#### `get_security`

Fetch a single security (instrument master record) by its security ID.

**Parameters:**
- `security_id: str` — e.g., `"SEC-000001"`

**Returns:** Full security document (including market-level `listings`) or `{"error": ..., "code": "NOT_FOUND"}`

---

#### `get_security_by_sedol`

Fetch the security carrying a given market-level SEDOL. SEDOLs are allocated one per listing market (and per traded currency), so any listing's SEDOL — primary or secondary — resolves to the same parent security.

**Parameters:**
- `sedol: str` — 7-char LSEG SEDOL, e.g., `"B1WXR90"`

**Returns:** Full security document with all its listings, or NOT_FOUND

---

#### `list_securities`

List securities with optional filters. Use this to resolve a `securityId` seen on positions/transactions into its full instrument details.

**Parameters:**
- `asset_class: str` *(optional)* — `"EQUITY"`, `"GOVT_BOND"`, `"CORP_BOND"`, `"FUND"`, `"CASH"`
- `ticker: str` *(optional)* — e.g., `"AAPL"`
- `status: str` *(optional)* — `"ACTIVE"`, `"MATURED"`, `"DELISTED"`
- `sedol: str` *(optional)* — matches any listing's SEDOL
- `limit: int` *(optional, default 50, max 200)*
- `skip: int` *(optional, default 0)*

**Returns:** `{"count": N, "data": [...]}` sorted by securityId

---

### Transactions

#### `get_transaction`

**Parameters:**
- `transaction_id: str`

**Returns:** Full transaction document or NOT_FOUND

---

#### `get_transactions`

Query transactions for an account over a date range.

**Parameters:**
- `account_id: str` — required
- `from_date: str` — `"YYYY-MM-DD"`
- `to_date: str` — `"YYYY-MM-DD"`
- `status: str` *(optional)* — `"PENDING"`, `"MATCHED"`, `"SETTLED"`, `"FAILED"`, `"CANCELLED"`
- `transaction_type: str` *(optional)* — `"BUY"`, `"SELL"`, `"DEPOSIT"`, `"WITHDRAWAL"`, `"TRANSFER_IN"`, `"TRANSFER_OUT"`, `"DIVIDEND"`, `"FX"`
- `limit: int` *(optional, default 50, max 200)*
- `skip: int` *(optional, default 0)*

**Returns:** `{"count": N, "data": [...]}` sorted by tradeDate descending

---

#### `get_transaction_summary`

Aggregate transaction counts and net amounts grouped by type and status. Use this instead of fetching all transactions and counting client-side.

**Parameters:**
- `account_id: str`
- `from_date: str`
- `to_date: str`

**Returns:** `{"count": N, "data": [{transactionType, status, count, totalNetAmount}]}`

---

### Positions

#### `get_position`

Fetch one position for a specific account, security, and date.

**Parameters:**
- `account_id: str`
- `security_id: str`
- `as_of_date: str` — `"YYYY-MM-DD"`

---

#### `get_positions`

Fetch all security holdings for an account on a given date.

**Parameters:**
- `account_id: str`
- `as_of_date: str` — `"YYYY-MM-DD"`

**Returns:** `{"count": N, "data": [...]}`

---

#### `get_position_history`

Return EOD position snapshots for one security over a date range.

**Parameters:**
- `account_id: str`
- `security_id: str`
- `from_date: str`
- `to_date: str`

**Returns:** `{"count": N, "data": [...]}` sorted ascending by `asOfDate`

---

### Settlements

#### `get_settlement`

**Parameters:**
- `settlement_id: str`

---

#### `get_settlement_status`

Look up the settlement linked to a transaction. Use this when you have a transaction ID and want to know its settlement outcome — do not construct settlement IDs manually.

**Parameters:**
- `transaction_id: str`

**Returns:** Full settlement document (including statusHistory) or NOT_FOUND

---

#### `get_settlements`

Query settlements for an account on a specific settlement date.

**Parameters:**
- `account_id: str`
- `settlement_date: str` — `"YYYY-MM-DD"`
- `status: str` *(optional)* — `"PENDING"`, `"INSTRUCTED"`, `"MATCHED"`, `"SETTLED"`, `"FAILED"`, `"CANCELLED"`, `"RECYCLED"`

---

#### `get_settlement_fails`

Find all failed settlements within a date window. Use for operational monitoring and reconciliation.

**Parameters:**
- `from_date: str`
- `to_date: str`
- `account_id: str` *(optional)* — scope to one account

**Returns:** `{"count": N, "data": [...]}` sorted by settlementDate descending

---

### Balances

#### `get_cash_balance`

**Parameters:**
- `account_id: str`
- `currency: str` — ISO 4217, e.g., `"USD"`, `"CAD"`
- `as_of_date: str` — `"YYYY-MM-DD"`

**Returns:** Full balance document including opening, closing, pending, and projected amounts

---

#### `get_cash_balances`

Fetch all currency balances for an account on a given date.

**Parameters:**
- `account_id: str`
- `as_of_date: str`

**Returns:** `{"count": N, "data": [...]}` — one entry per currency

---

#### `get_projected_balance`

**Parameters:**
- `account_id: str`
- `currency: str`
- `as_of_date: str`

**Returns:** `{accountId, currency, asOfDate, closingBalance, pendingCredits, pendingDebits, projectedBalance}`

`projectedBalance = closingBalance + pendingCredits − pendingDebits`

---

## Parameter Formats

### Dates

Always use ISO 8601 full date format:

```
✓  "2025-03-31"
✗  "31/03/2025"
✗  "March 31, 2025"
✗  "2025-3-31"
```

A date parameter means the **whole calendar day (UTC)** — documents stamped at any time on that day match. Date ranges are inclusive of both `from_date` and `to_date`. A malformed date returns `{"error": ..., "code": "INVALID_DATE"}`.

### IDs

IDs are opaque strings from seed data. Do not construct or guess them. Always discover IDs from list/query results before fetching a specific record.

Seed data patterns: `ACC-XXXX`, `TXN-XXXXXXXX`, `SEC-XXXX`, `SET-XXXXXXXX`

### Reference-Data Identifier Formats

| Identifier | Standard | Format | Where it lives |
|---|---|---|---|
| ISIN | ISO 6166 | 12 chars (2-letter country + 9 alnum + check digit) | `security.isin` — issue level |
| SEDOL | LSEG | 7 chars (6 alnum, no vowels + weighted mod-10 check digit) | `security.listings[].sedol` — one per market of listing and traded currency |
| FIGI | OpenFIGI | 12 chars, `BBG` prefix | `security.figi` — share-class level (1:1 with ISIN) |
| MIC | ISO 10383 | 4 alnum (e.g. `XNYS`, `XTSE`; segment `XNGS` under operating `XNAS`) | `security.listings[].micCode` / `operatingMic` |
| LEI | ISO 17442 | 20 chars (18 alnum + 2 check digits) | `account.client.lei` — standard client linkage key |
| Country | ISO 3166-1 alpha-2 | 2 letters (`CA`, `US`, `GB`) | `client.countryOfDomicile`, `listings[].countryOfListing`, `taxResidencies` |
| Settlement location | BIC of the market CSD | e.g. `DTCYUS33` (DTC), `CDSLCATT` (CDS), `CRSTGB22` (CREST) | `security.listings[].settlementLocation` |

To resolve an instrument the way SEDOL-keyed upstream systems do, use `get_security_by_sedol(sedol=...)` — any listing's SEDOL (primary or secondary) returns the same parent security.

### Status Values

| Domain | Valid Values |
|---|---|
| Account | ACTIVE, SUSPENDED, CLOSED |
| Security | ACTIVE, MATURED, DELISTED |
| Listing | ACTIVE, SUSPENDED, DELISTED |
| Transaction | PENDING, MATCHED, SETTLED, FAILED, CANCELLED |
| Settlement | PENDING, INSTRUCTED, MATCHED, SETTLED, FAILED, CANCELLED, RECYCLED |
| Client classification | RETAIL, PROFESSIONAL, ELIGIBLE_COUNTERPARTY |
| Client kycStatus | APPROVED, PENDING_REVIEW, EXPIRED |
| Client riskRating | LOW, MEDIUM, HIGH |
| Client legalEntityType | CORPORATION, PARTNERSHIP, FUND, TRUST, GOVERNMENT, INDIVIDUAL |

### Transaction Types

`BUY`, `SELL`, `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_IN`, `TRANSFER_OUT`, `DIVIDEND`, `FX`

---

## Error Handling

All tools return errors as plain dicts, never as exceptions.

```python
result = get_account(account_id="ACC-0001")
if "error" in result:
    # result["code"] is "NOT_FOUND", "INVALID_DATE", or "MONGO_ERROR"
    # result["error"] has a description
else:
    account_name = result["accountName"]
```

An empty list result is not an error: `{"count": 0, "data": []}` means no records matched.

---

## Common Query Patterns

### Discover then fetch

```
1. list_accounts(status="ACTIVE", limit=5)
   → pick an accountId from result["data"][0]["accountId"]

2. get_account(account_id="ACC-0001")
```

### Transaction investigation

```
1. get_transactions(account_id, from_date, to_date, status="FAILED")
2. get_settlement_status(transaction_id=txn["transactionId"])
   → inspect statusHistory
```

### Portfolio snapshot

```
1. get_positions(account_id, as_of_date)       → securities held
2. get_cash_balances(account_id, as_of_date)   → cash across currencies
```

### Cash flow analysis

```
1. get_transaction_summary(account_id, from_date, to_date)   → aggregated by type/status
2. get_projected_balance(account_id, currency, as_of_date)   → forward-looking cash
```

### Settlement risk

```
1. get_settlement_fails(from_date, to_date)                          → all accounts
2. get_settlement_fails(from_date, to_date, account_id=target)       → scoped
```

### Resolve a mainframe SEDOL to a security

```
1. get_security_by_sedol(sedol="B1WXR90")
   → parent security with all listings; find the matching listing for
     settlement location / traded currency
```

### Client-scoped account discovery

```
1. list_accounts(lei="549300...")          → all accounts of a legal entity
2. list_accounts(domicile="CA")            → accounts of Canadian-domiciled clients
   → client-master details (domicile, tax residencies, KYC) are embedded
     in every returned account under "client" — no second call needed
```

---

## Pagination

All list tools support `skip: int = 0` for offset-based pagination:

```
# Page 1
get_transactions(account_id, from_date, to_date, limit=50, skip=0)

# Page 2
get_transactions(account_id, from_date, to_date, limit=50, skip=50)
```

`count` is always the total number of matching records. More pages exist while `skip + len(data) < count`.

| Tool | Default Limit | Max | Supports skip |
|---|---|---|---|
| list_accounts | 20 | 200 | yes |
| list_securities | 50 | 200 | yes |
| get_transactions | 50 | 200 | yes |
| get_positions | 200 | 200 | yes |
| get_position_history | 200 | 200 | yes |
| get_settlements | 200 | 200 | yes |
| get_settlement_fails | 200 | 200 | yes |
| get_cash_balances | 50 | 50 | yes |

---

## What This Server Does Not Do

- **No mutations.** Read-only ODS view — no create, update, or delete tools.
- **No cross-account aggregation.** No "all accounts" summary; iterate `list_accounts` if needed.
- **No free-text security search.** `list_securities` filters by asset class/ticker/status/SEDOL (exact match) only; exact-identifier resolution is available via `get_security_by_sedol`.
- **No real-time prices.** All prices are EOD snapshots from seed data.
- **No authentication.** Connects to a local MongoDB instance with no credentials.

---

## Module Layout

| Module path | Role |
|---|---|
| `bank_ods.config` | Env loading only |
| `bank_ods.models.*` | Pydantic models + `COLLECTION` + `INDEXES` constants |
| `bank_ods.models.registry` | `ENTITIES` list — import to iterate all models |
| `bank_ods.db.client` | `get_client()`, `get_db()`, `get_collection(name)` |
| `bank_ods.db.indexes` | `ensure_indexes()` — idempotent, call once on startup |
| `bank_ods.services.*` | All async business logic — single source of truth |
| `bank_ods.mcp.server` | `mcp = FastMCP("bank-ods")` instance |
| `bank_ods.mcp.tools` | `@mcp.tool()` decorators — thin wrappers only |
| `bank_ods.rest.app` | `app = FastAPI(...)` — uvicorn import target |
| `bank_ods.rest.routers.*` | `APIRouter` instances — no business logic |
| `bank_ods.graphql.app` | `app = create_app()` — uvicorn import target |
| `bank_ods.graphql.sdl` | `generate_sdl()` — called once at startup |
| `bank_ods.graphql.resolvers` | `query = QueryType()` — thin resolvers only |

### Naming Conventions

| Context | Convention | Example |
|---|---|---|
| Service functions | snake_case | `get_account`, `list_accounts` |
| Model classes | PascalCase | `Account`, `CashBalance` |
| Collection names | snake_case plural | `accounts`, `cash_balances` |
| Model fields | camelCase | `accountId`, `asOfDate` |
| MCP tool parameters | snake_case | `account_id`, `from_date` |
| GraphQL arguments | camelCase | `accountId`, `fromDate` |
| REST paths | kebab-case where needed | `/by-transaction/{id}` |

---

## MCP Configuration

Register in `claude_desktop_config.json` (`%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "bank-ods": {
      "command": "uv",
      "args": ["run", "python", "-m", "bank_ods.mcp"],
      "cwd": "C:/dev/clio-git/mongo-mcp-test",
      "env": {
        "MONGODB_URI": "mongodb://localhost:27017",
        "MONGODB_DB": "bank_ods"
      }
    }
  }
}
```

Tools appear in Claude Code as `mcp__bank-ods__<tool_name>`.

---

## Extending the Server

To add a new tool:

1. Add `async def` service function to `bank_ods/services/<domain>.py`
2. Add a one-line `@mcp.tool()` wrapper in `bank_ods/mcp/tools.py`
3. Add a REST endpoint in `bank_ods/rest/routers/<domain>.py`
4. Add a GraphQL resolver in `bank_ods/graphql/resolvers.py` (SDL updates automatically)
5. Add service tests in `tests/test_services.py` and a parity assertion in `tests/test_parity.py`

Do not add MongoDB query logic outside `bank_ods/services/*`. Do not add new collections without discussion (see CLAUDE.md).
