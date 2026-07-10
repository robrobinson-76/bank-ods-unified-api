# Agents Guide — Bank ODS MCP Server

## Overview

This guide covers how AI agents (Claude Code or any MCP-capable client) should interact with the bank ODS MCP servers: tool naming conventions, parameter formats, query patterns, error handling, and pagination.

There are **two MCP servers with distinct personas**, both read-only, both delegating to `bank_ods.services.*` — the same functions called by the REST and GraphQL APIs:

- **`bank-ods` (consumer persona)** — 18 clean semantic domain tools across six domains, for AI agents, chatbot integrations, and downstream application teams. Productionized alongside REST and GraphQL.
- **`bank-ods-ops` (operations persona)** — for engineers, QA, support, and release-monitoring agents: 8 operational tools (health, collection stats, recent-document inspection, raw-record field search, raw-vs-curated reconciliation, in-process logs, composite release checks) plus 4 registry-generated raw-tier tools (12 tools total). Raw inspection is this server's purpose, so its raw tools are always present regardless of `EXPOSE_RAW_TIER` (that flag governs the consumer transports). Internal-only by intent — never wired into consumer-facing clients.

Pick the server by task: answering business questions about accounts/positions/settlements → `bank-ods`; investigating *why the data looks wrong*, feed drift, or verifying a release → `bank-ods-ops`.

Two contract conventions apply everywhere:

- **Cursor pagination, no totals.** List results return `{"data": [<one page>], "page_info": {"has_more": bool, "next_cursor": str|null}}`. There is no total count. If `has_more` is true, call the same tool again passing `next_cursor` back VERBATIM as `cursor` — it is an opaque token, never build or edit one.
- **Monetary values are exact strings.** Amounts, quantities, and rates are stored as Decimal128 and serialized as strings (e.g. `"18550.00"`), never floats. Timestamps are ISO 8601 with an explicit UTC offset (`2026-05-31T16:00:00+00:00`).

---

## MCP Server Identity

| | Consumer | Operations |
|---|---|---|
| **Server name** | `bank-ods` | `bank-ods-ops` |
| **Start command** | `python -m bank_ods.mcp` | `python -m bank_ods.mcp_ops` |
| **Enable flag** | `TRANSPORT_MCP_ENABLED` | `TRANSPORT_MCP_OPS_ENABLED` |
| **Audience** | AI agents, downstream teams | engineers, QA, support, release monitors |
| **Exposure** | consumer path, governed | internal-only |

**Transport (both):** `MCP_TRANSPORT` env var — `stdio` (default, Claude Desktop / VS Code) or `sse` (chatbot / K8s; for the ops server, internal endpoints only)

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
- `limit: int` *(optional, default 50, max 200)*
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted by accountId

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
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted by securityId

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
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted by tradeDate descending

---

#### `get_transaction_summary`

Aggregate transaction counts and net amounts grouped by type and status. Use this instead of fetching all transactions and counting client-side.

**Parameters:**
- `account_id: str`
- `from_date: str`
- `to_date: str`

**Returns:** `{"data": [{transactionType, status, count, totalNetAmount}]}` — not paginated; data holds every group

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
- `limit: int` *(optional, default 50, max 200)*
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted by securityId

---

#### `get_position_history`

Return EOD position snapshots for one security over a date range.

**Parameters:**
- `account_id: str`
- `security_id: str`
- `from_date: str`
- `to_date: str`
- `limit: int` *(optional, default 50, max 200)*
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted ascending by `asOfDate`

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
- `limit: int` *(optional, default 50, max 200)*
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted by settlementId

---

#### `get_settlement_fails`

Find all failed settlements within a date window. Use for operational monitoring and reconciliation.

**Parameters:**
- `from_date: str`
- `to_date: str`
- `account_id: str` *(optional)* — scope to one account
- `limit: int` *(optional, default 50, max 200)*
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` sorted by settlementDate descending

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
- `limit: int` *(optional, default 50, max 200)*
- `cursor: str` *(optional)* — `next_cursor` from the previous page

**Returns:** `{"data": [...], "page_info": {...}}` — one entry per currency, sorted by currency

---

#### `get_projected_balance`

**Parameters:**
- `account_id: str`
- `currency: str`
- `as_of_date: str`

**Returns:** `{accountId, currency, asOfDate, closingBalance, pendingCredits, pendingDebits, projectedBalance}`

`projectedBalance = closingBalance + pendingCredits − pendingDebits`

---

## Operations Server Tool Reference (`bank-ods-ops`)

Everything below lives on the **operations server**, not the consumer server. All tools are read-only; collection-name parameters accept only collections in the entity registry (unknown names return `UNKNOWN_COLLECTION` with the registered list).

### Operational tools

#### `ping_database`

No parameters. Returns `{ok, version, uptimeSeconds, connections}` — the first call to make when anything looks wrong.

#### `list_collections`

No parameters. Returns every registered collection with `tier` (semantic/raw), `count`, and `lastInsertAt` (derived from the newest ObjectId). The starting point for a health sweep.

#### `get_collection_stats`

**Parameters:** `collection: str` — a registered collection name

**Returns:** `{collection, tier, count, sizeBytes, storageSizeBytes, indexes: [{name, keys}], lastInsertAt}`

#### `query_recent`

**Parameters:** `collection: str`, `limit: int` *(optional, default 10, max 50)*

**Returns:** the N most recently inserted documents, newest first, each with an added `_insertedAt` — use to inspect what a loader just wrote.

#### `find_raw_records`

**Parameters:** `collection: str` *(raw-tier only)*, `field: str` *(any field of the raw model)*, `value: str`, `limit: int` *(optional, default 20, max 50)*

Exact-match search over one raw collection — "show me the raw records for account X". The match is against the **stored wire-format value**: custody accounts are zero-filled 12-char (`POS_ACCT_NBR: "000000000001"` for account 1), CUSIPs are 9-char or empty. Field names are validated against the model — an unknown field returns `UNKNOWN_FIELD` with the valid field list; semantic collections return `NOT_RAW_COLLECTION` (use the domain tools on `bank-ods` instead).

**Returns:** the standard `{data: [...], page_info: {has_more, next_cursor}}` page — follow `next_cursor` to walk every match for a busy account.

#### `reconcile_custody_feed`

**Parameters:** `cycle_date: str` *(optional, CCYYMMDD; defaults to the latest cycle in the feed)*

Traces one raw custody batch cycle into the curated `positions` collection — the "why didn't this record appear?" tool. Every raw record is resolved (`POS_ACCT_NBR` → accountId, `POS_CUSIP_NBR`/`POS_ISIN_NBR` → securityId) and checked for a curated position.

**Returns:** `{cycleDate, records, matched, unmatched, issues: [{recId, reason, accountId, securityId, cusip}]}` where `reason` is `UNKNOWN_ACCOUNT`, `UNKNOWN_SECURITY`, or `NO_CURATED_POSITION` (first 20 issues listed).

#### `get_recent_logs`

**Parameters:** `level: str` *(optional, default "INFO" — DEBUG/INFO/WARNING/ERROR)*, `limit: int` *(optional, default 50)*

Newest-first entries from this process's in-memory log ring. Process-local by design: it answers "what just happened in this process"; cross-process search belongs to the platform log aggregator.

#### `run_release_checks`

No parameters. Composite post-release verification: database reachability, per-collection population, raw feed freshness, and custody reconciliation, rolled up to `{status: PASS|WARN|FAIL, checks: [{name, status, detail}]}`.

**For release-monitoring agents:** poll after a deployment until `PASS`; alert on `FAIL`, or on a `WARN` that persists across polls. Use the per-check `detail` to decide which investigation tool to call next (`reconcile_custody_feed` for reconciliation warnings, `list_collections` for population failures, `get_recent_logs` for anything unexplained).

### Raw tier (registry-generated tool group)

These tools are registered on the ops server when `EXPOSE_RAW_TIER=true` (the dev default). They expose feed records **as received** — field names and values keep the source system's conventions, so read the conventions below before interpreting values. Tool names, GraphQL fields, and REST routes for these entities are generated from the entity registry and always match.

#### `list_raw_custody_positions` / `get_raw_custody_position`

Nightly mainframe custody position extract (fixed-width batch feed, record type 03), one document per detail record.

**Parameters:** `list_…` takes `limit` / `cursor` (standard pagination contract); `get_…` takes `record_id: str` — the `REC_ID` field, `"<POS_BUS_DATE>-<sequence>"`.

**Value conventions (mainframe wire format):**

- Numerics are zoned decimal strings with an **implied decimal point**: `POS_SHR_QTY` `"0000000008505000"` with PIC 9(12)V9(4) means 850.5. Scales: `POS_SHR_QTY`/`POS_SHR_QTY_PEND` 4, `POS_MKT_PRICE` 12, `POS_MKT_VALUE` 2.
- `POS_ACCR_INT` is **signed**: the last character is a sign overpunch (`{`, `A`–`I` positive; `}`, `J`–`R` negative, each also encoding the final digit). `"0000017463A"` = +1746.31.
- `POS_BUS_DATE` / `POS_LAST_ACTVY_DT` are `CCYYMMDD`; `POS_PRICE_DT` is julian `CCYYDDD`.
- `POS_CUSIP_NBR` is 9 characters or `""` (all-or-nothing fill); `POS_ACCT_NBR` is zero-filled 12.

#### `list_raw_vendor_securities` / `get_raw_vendor_security`

Bespoke third-party instrument reference feed, one document per delivered row.

**Parameters:** `list_…` takes `limit` / `cursor`; `get_…` takes `record_id: str` — the `Vendor_Ref` field, e.g. `"VND-000117"`.

**Value conventions (as-delivered, unnormalized):** identifiers may be missing, `"N/A"`, or `"#N/A"`; `Cusip` may have lost a leading zero; `ASSET_CLS` mixes code-list generations (`"EQ"`, `"Equity"`, `"COM"`, `"1"`); numbers are string-encoded; dates mix `CCYYMMDD`, `MM/DD/YYYY`, and sentinels (`"99991231"` perpetual, `"00000000"`); country/currency codes drift (`"US"`/`"USA"`/`"UNITED STATES"`, `"usd"`). Only `Vendor_Ref` is guaranteed unique — treat everything else as needing validation before use.

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
    # result["code"] is "NOT_FOUND", "INVALID_DATE", "INVALID_CURSOR", or "MONGO_ERROR"
    # result["error"] has a description
else:
    account_name = result["accountName"]
```

`INVALID_CURSOR` means the `cursor` value was malformed or came from a different tool/query — restart from the first page (no `cursor`) instead of retrying it.

An empty list result is not an error: `{"data": [], "page_info": {"has_more": false, "next_cursor": null}}` means no records matched.

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

### Release monitoring (ops server — for an AI agent watching a deployment)

```
1. run_release_checks()                        → PASS: done. FAIL: alert now.
2. On WARN → read checks[].detail:
   - custody_reconciliation → reconcile_custody_feed()   → per-record reasons
   - custody_feed_freshness → list_collections()          → which loads are stale
3. get_recent_logs(level="ERROR")              → anything the process just logged
4. Re-poll run_release_checks() on an interval; alert if WARN persists or
   any check degrades between polls.
```

### Feed investigation: "why is this record missing?" (ops server)

```
1. reconcile_custody_feed(cycle_date="20260708")
   → find the record's REC_ID among issues[]; reason tells you where it broke:
     UNKNOWN_ACCOUNT     → account mapping problem (check POS_ACCT_NBR)
     UNKNOWN_SECURITY    → identifier not in the security master
                           (get_raw_custody_position → check CUSIP/ISIN fill)
     NO_CURATED_POSITION → feed record arrived but curation didn't produce a position
2. query_recent("raw_custody_positions")       → confirm what the loader wrote and when
3. get_recent_logs(level="WARNING")            → loader/service errors in this process
```

### Raw records for a specific account (ops server)

```
1. get_account(account_id="ACC-000007")        → confirm the account (consumer server)
2. find_raw_records("raw_custody_positions", "POS_ACCT_NBR", "000000000007")
   → all raw feed records for that account, wire-format values
   → remember the raw key is zero-filled 12-char, NOT "ACC-000007"
3. Same pattern for any feed field:
   find_raw_records("raw_custody_positions", "POS_CUSIP_NBR", "037833100")
   find_raw_records("raw_vendor_securities", "Cusip", "37833100")   ← as-delivered value
```

---

## Pagination

All 8 list tools use keyset cursor pagination with a uniform `limit: int = 50` (max 200) and `cursor: str | None`:

```
# Page 1
page = get_transactions(account_id, from_date, to_date, limit=50)

# Next pages — pass next_cursor back verbatim until has_more is false
while page["page_info"]["has_more"]:
    page = get_transactions(account_id, from_date, to_date, limit=50,
                            cursor=page["page_info"]["next_cursor"])
```

Rules:

- The cursor is an **opaque token**. Pass it back exactly as returned; never construct, decode, or modify one. A cursor only works with the same tool it came from.
- **There is no total count.** To count records, page until `has_more` is false and sum `len(data)` — or prefer an aggregate tool (`get_transaction_summary`) when one exists.
- Filters may change between pages (the cursor only marks a position in the sort order), but keep them identical for a coherent listing.
- Results are deterministic: each tool has a fixed sort order (documented per tool above) with a unique tie-breaker, so pages never overlap or drop records — even if data changes mid-iteration.

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

Register in `claude_desktop_config.json` (`%APPDATA%\Claude\` on Windows). Register the consumer server for everyone; add the ops server only for internal engineering/support environments (it exposes raw feed data and operational internals):

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
    },
    "bank-ods-ops": {
      "command": "uv",
      "args": ["run", "python", "-m", "bank_ods.mcp_ops"],
      "cwd": "C:/dev/clio-git/mongo-mcp-test",
      "env": {
        "MONGODB_URI": "mongodb://localhost:27017",
        "MONGODB_DB": "bank_ods"
      }
    }
  }
}
```

Tools appear in Claude Code as `mcp__bank-ods__<tool_name>` and `mcp__bank-ods-ops__<tool_name>`.

---

## Extending the Servers

**A new semantic consumer tool:**

1. Add `async def` service function to `bank_ods/services/<domain>.py`
2. Add a one-line `@mcp.tool()` wrapper in `bank_ods/mcp/tools.py`
3. Add a REST endpoint in `bank_ods/rest/routers/<domain>.py`
4. Add a resolver in `bank_ods/graphql/resolvers.py` and its field to `_SEMANTIC_QUERY_FIELDS` in `bank_ods/graphql/sdl.py`
5. Add service tests in `tests/test_services.py` and a parity assertion in `tests/test_parity.py`

**A new raw-tier entity:** add the model to `ENTITIES_RAW` (with `ID_FIELD`, `DEFAULT_SORT`, `UNFILTERED_LIST`) and seed it. Indexes, SDL get/list fields, GraphQL resolvers, REST routes, the ops raw tool group, and baseline parity tests are all generated from the registry — no per-surface wiring.

**A new operational tool** (health, introspection, reconciliation, release checks): add an `async def` to `bank_ods/services/ops.py` and a one-line `@mcp.tool()` wrapper in `bank_ods/mcp_ops/tools.py`, then a test in `tests/test_mcp_ops.py`. Ops tools are read-only and belong only on `bank-ods-ops`, never on the consumer server.

Do not add MongoDB query logic outside `bank_ods/services/*`. Do not add mutation tools to either server. Do not add new collections without discussion (see CLAUDE.md).
