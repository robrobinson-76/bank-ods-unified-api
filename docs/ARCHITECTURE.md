# Architecture — Bank ODS Prototype

## Purpose

This prototype demonstrates a single architectural pattern: **one Pydantic model registry drives a shared service layer that is exposed identically via three transports — MCP, REST, and GraphQL.**

The domain (custodian bank ODS: accounts, positions, transactions, settlements, cash balances) is illustrative. The point is the pattern: models defined once, a schema that propagates automatically to indexes and SDL, and a service layer that any transport can call without knowing anything about the others. A cross-layer parity test harness enforces that all three transports return identical results for identical inputs.

This is a local development prototype, not a production system.

---

## Core Pattern

```
┌─────────────────────────────────────────────────────────────┐
│  Pydantic Models  (bank_ods/models/)                        │
│                                                             │
│  Single source of truth for field names, types, and         │
│  collection configuration.                                  │
│                                                             │
│  ENTITIES registry propagates to:                           │
│    → MongoDB index creation  (db/indexes.py)                │
│    → GraphQL SDL generation  (graphql/sdl.py)               │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │     Service Layer       │
              │  bank_ods.services.*    │
              │  entity + generic +     │
              │  raw + ops helpers      │
              │  Single MongoDB access  │
              │  point for all layers   │
              └────────────┬────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
  ┌──────▼──────┐  ┌───────▼──────┐  ┌──────▼──────────┐
  │  MCP Tools  │  │   REST API   │  │  GraphQL API    │
  │  (fastmcp)  │  │  (FastAPI)   │  │  (Ariadne)      │
  │  stdio/sse  │  │  port 8000   │  │  port 8001      │
  └─────────────┘  └──────────────┘  └─────────────────┘
```

**Three invariants this prototype enforces:**

1. **Models are the schema.** Pydantic field definitions drive MongoDB indexes and the GraphQL SDL. There is no separate schema file or index migration script.
2. **One access point.** MongoDB is only touched through `bank_ods.services.*`. No transport layer contains query logic.
3. **Parity.** All three transports return identical data for identical inputs. `tests/test_parity.py` enforces this automatically.

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                             Transport Layer                                  │
│                                                                              │
│  consumer path (governed)                          internal-only            │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐   ┌────────────────────┐   │
│  │ MCP Server │ │  REST API  │ │ GraphQL API  │   │ Ops MCP Server     │   │
│  │ bank-ods   │ │  (FastAPI) │ │ (Ariadne)    │   │ bank-ods-ops       │   │
│  │ (fastmcp)  │ │  port 8000 │ │ port 8001    │   │ raw + ops tooling  │   │
│  │ stdio/sse  │ │            │ │              │   │ stdio/internal sse │   │
│  └─────┬──────┘ └─────┬──────┘ └──────┬───────┘   └─────────┬──────────┘   │
└────────┼──────────────┼──────────────┼─────────────────────┼──────────────┘
         │              │              │                     │
         └──────────────┴──────────────┴─────────────────────┘
                                │
                       ┌────────▼────────┐
                       │  Service Layer  │
                       │  bank_ods.      │
                       │  services.*     │
                       │  entity + generic│
                       │  + raw + ops    │
                       └────────┬────────┘
                                │
                       ┌────────▼────────┐
                       │  DB Layer       │
                       │  motor (async)  │
                       │  + index mgmt   │
                       └────────┬────────┘
                                │
                       ┌────────▼────────┐
                       │   MongoDB 7.0   │
                       │  8 collections  │
                       │  (6 semantic +  │
                       │   2 raw tier)   │
                       └─────────────────┘
```

---

## Tech Stack

| Layer | Library | Version |
|---|---|---|
| MCP server | fastmcp | ≥0.4 |
| REST framework | FastAPI + uvicorn | ≥0.110 / ≥0.29 |
| GraphQL | Ariadne | ≥0.23 |
| MongoDB driver | motor (async) | ≥3.4 |
| Data models | Pydantic v2 | via fastmcp/fastapi |
| Seed data | pymongo + faker | ≥4.6 / ≥24.0 |
| HTTP test client | httpx | ≥0.27 |
| Package manager | uv (preferred) | — |
| Database | MongoDB | 7.0 (Docker) |
| Runtime | Python | ≥3.11 |

---

## Project Layout

```
mongo-mcp-test/
├── docs/
│   ├── ARCHITECTURE.md             ← this file
│   ├── AGENTS.md                   ← MCP tool reference for AI agents
│   ├── PLAN.md                     ← original phased implementation plan (reference)
│   ├── PLAN-multilayer.md          ← unified MCP/REST/GraphQL plan (reference)
│   └── PLAN-k8s-scalability.md    ← K8s scalability implementation plan (reference)
│
├── Dockerfile.rest                 ← multi-stage build; uvicorn on :8000
├── Dockerfile.graphql              ← multi-stage build; uvicorn on :8001
├── Dockerfile.mcp                  ← multi-stage build; stdio or sse via MCP_TRANSPORT
├── .dockerignore
├── docker-compose.yml              ← MongoDB + REST + GraphQL services
│
├── k8s/                            ← Kubernetes manifests (see K8s Deployment section)
│
├── src/
│   └── bank_ods/
│       ├── __init__.py
│       ├── config.py               ← MONGODB_URI, MONGODB_DB, DEBUG, LOG_LEVEL, MONGO_TIMEOUT_MS
│       ├── logging_config.py       ← JSON formatter, configure_logging(), RequestLoggingMiddleware
│       │
│       ├── models/                 ← Pydantic v2 entity models (single source of truth)
│       │   ├── base.py             ← BankDocument, IndexSpec, access metadata (ID_FIELD, DEFAULT_SORT, UNFILTERED_LIST)
│       │   ├── account.py
│       │   ├── security.py
│       │   ├── transaction.py
│       │   ├── position.py
│       │   ├── settlement.py
│       │   ├── cash_balance.py
│       │   ├── raw_custody_position.py ← raw tier: mainframe batch extract (copybook conventions)
│       │   ├── raw_vendor_security.py  ← raw tier: as-received vendor reference feed
│       │   └── registry.py         ← tiered registry: ENTITIES_SEMANTIC/RAW, active_entities(), operation-name derivation
│       │
│       ├── db/
│       │   ├── client.py           ← Motor client per event loop, connection timeouts
│       │   └── indexes.py          ← ensure_indexes() — idempotent, indexes every registered collection (ENTITIES)
│       │
│       ├── services/               ← business logic (only MongoDB access here)
│       │   ├── _common.py          ← date helpers (day_range/date_window), serialize_doc()
│       │   ├── pagination.py       ← keyset cursor pagination: paginate(), cursor codec, seek predicate
│       │   ├── generic.py          ← get_one/get_many — shared envelope + pagination base for every entity service
│       │   ├── raw.py              ← raw-tier access driven purely by model metadata
│       │   ├── ops.py              ← operational introspection: health, stats, recent docs, reconciliation, release checks
│       │   ├── accounts.py         ← get_account, list_accounts (thin wrappers over generic)
│       │   ├── securities.py       ← get_security, get_security_by_sedol, list_securities
│       │   ├── transactions.py     ← get_transaction, get_transactions, get_transaction_summary
│       │   ├── positions.py        ← get_position, get_positions, get_position_history
│       │   ├── settlements.py      ← get_settlement, get_settlement_status, get_settlements, get_settlement_fails
│       │   └── balances.py         ← get_cash_balance, get_cash_balances, get_projected_balance
│       │
│       ├── mcp/                    ← consumer MCP server (semantic persona)
│       │   ├── server.py           ← FastMCP("bank-ods"), lifespan → ensure_indexes()
│       │   ├── tools.py            ← 18 @mcp.tool() wrappers; each delegates to services
│       │   ├── raw_tools.py        ← registry-generated raw tool group; registered on the OPS server
│       │   └── __main__.py         ← TRANSPORT_MCP_ENABLED gate; MCP_TRANSPORT; mcp.run()
│       │
│       ├── mcp_ops/                ← operations MCP server (ops/debug persona, internal-only)
│       │   ├── server.py           ← FastMCP("bank-ods-ops"); attaches log ring; registers raw group
│       │   ├── tools.py            ← ping/list_collections/stats/query_recent/reconcile/logs/release checks
│       │   └── __main__.py         ← TRANSPORT_MCP_OPS_ENABLED gate; MCP_TRANSPORT; mcp.run()
│       │
│       ├── rest/
│       │   ├── app.py              ← FastAPI app, /health + /ready, tier-gated router mounting, lifespan
│       │   ├── errors.py           ← check() — maps service error envelopes to HTTP 400/404/500
│       │   └── routers/
│       │       ├── accounts.py
│       │       ├── securities.py
│       │       ├── transactions.py
│       │       ├── positions.py
│       │       ├── settlements.py
│       │       ├── balances.py
│       │       └── raw.py          ← registry-generated raw routers (one per raw collection)
│       │
│       └── graphql/
│           ├── app.py              ← Ariadne + FastAPI; /health; debug from env; logging middleware
│           ├── sdl.py              ← Dynamic SDL from active_entities(); raw query fields generated from metadata
│           └── resolvers.py        ← semantic QueryType resolvers + registry-generated raw resolvers
│
├── scripts/
│   └── seed_data.py                ← Loads ~5,200 realistic documents using faker (seed=42)
│
├── tests/
│   ├── conftest.py                 ← Session-scoped fixtures: db, first_account, rest_client, gql_client
│   ├── test_pagination.py          ← Keyset cursor helper: round-trip, rejection, walks, stability
│   ├── test_services.py            ← Direct service function tests (happy path, NOT_FOUND, filters, pagination)
│   ├── test_rest.py                ← REST endpoint tests (status codes, response shapes, health, cursors)
│   ├── test_graphql.py             ← GraphQL query validation (health, cursors)
│   ├── test_parity.py              ← Entity-specific parity: REST == GraphQL == service (incl. cursor strings)
│   ├── test_parity_registry.py     ← Registry-driven parity baseline (list/get/not-found per exposed entity)
│   ├── test_mcp.py                 ← Consumer MCP surface + tool==service parity
│   └── test_mcp_ops.py             ← Ops MCP surface, raw tool parity, operational tools, release checks
│
├── pyproject.toml
├── .env.example
└── CLAUDE.md
```

---

## Data-Driven Model Layer

This is the mechanism that ties all three transports together. Models are defined once in `bank_ods/models/`; everything downstream derives from them automatically.

### `BankDocument` Base Class

All entity models inherit from `BankDocument`:

```python
class BankDocument(BaseModel):
    COLLECTION: ClassVar[str]           # MongoDB collection name
    INDEXES: ClassVar[list[IndexSpec]]  # Index specifications

    @classmethod
    def from_mongo(cls, doc: dict) -> "BankDocument": ...
    def to_response(self) -> dict: ...  # JSON-safe dict
```

`IndexSpec = tuple[str | list[tuple[str, int]], dict[str, Any]]`

### Tiered Entity Registry

`bank_ods.models.registry` partitions entities into two tiers:

- **Semantic tier** (`ENTITIES_SEMANTIC`) — the six curated models: normalized camelCase fields, typed values, entity-specific service functions with filter parameters.
- **Raw tier** (`ENTITIES_RAW`) — as-received feed records loaded verbatim: `RawCustodyPosition` (fixed-width mainframe batch extract; copybook field names, zoned-decimal values, julian dates) and `RawVendorSecurity` (third-party reference feed with its natural inconsistencies).

`active_entities()` applies the `EXPOSE_SEMANTIC_TIER` / `EXPOSE_RAW_TIER` config flags and is what the **consumer** surfaces iterate:

- `graphql/sdl.py` → generates type blocks and list wrappers for every active entity, plus get/list query fields for raw entities from their access metadata
- `rest/routers/raw.py` → builds a REST router per raw entity, mounted at `/<collection>` (only when the raw tier is active)
- `tests/test_parity_registry.py` → parametrizes baseline cross-transport parity over the exposed entities

Two things deliberately iterate the full `ENTITIES` list, independent of the tier flags:

- `db/indexes.py` → `ensure_indexes()` creates indexes for every registered collection — indexes track the physical collections (which the loader always writes), not what a deployment exposes; gating them would leave written-and-queried collections unindexed
- `mcp_ops/server.py` → the operations MCP server always registers the raw tool group (`mcp/raw_tools.py`) and its operational tools read every registered collection — raw feed inspection is that server's purpose

Models carry access metadata as class variables: `ID_FIELD` (natural key for get-by-id), `DEFAULT_SORT` (stable unfiltered listing order), and `UNFILTERED_LIST` (whether listing without filters is supported). Operation names derive from one place — `get_field_name()` / `list_field_name()` — so the same entity carries the same names in SDL, MCP, REST, and tests.

Adding a raw entity only requires adding the model to `ENTITIES_RAW` and seeding it: indexes, SDL fields, resolvers, REST routes, MCP tools, and baseline parity tests all pick it up automatically. Semantic entities keep curated query surfaces (filter arguments, composite keys) in their per-entity service/transport modules.

### Generic Service Base

`services/generic.py` provides `get_one(collection, query)` and `get_many(collection, query, sort, limit, cursor)` — the shared mechanics (error envelopes, serialization, keyset pagination) that every entity service is a thin typed wrapper over. `services/raw.py` drives raw-tier access purely from model metadata. Query construction never leaves `bank_ods/services/`.

### Transport & Tier Feature Flags

Each transport checks its enable flag at startup and refuses to serve when disabled (`TRANSPORT_REST_ENABLED`, `TRANSPORT_GRAPHQL_ENABLED`, `TRANSPORT_MCP_ENABLED` for the consumer MCP server, `TRANSPORT_MCP_OPS_ENABLED` for the operations MCP server). The tier flags govern the **consumer** surfaces: `EXPOSE_SEMANTIC_TIER` gates the semantic MCP tool group / SDL fields / REST routers, `EXPOSE_RAW_TIER` gates the raw ones — a consumer deployment that withholds a tier never advertises its tools or fields. To keep SDL and resolvers in sync, both GraphQL tiers' resolver registration is gated the same way the SDL fields are (a tier-off deployment that left both flags false would have no query fields, so GraphQL refuses to build with a clear error rather than emitting invalid SDL). The operations server is exempt: it always exposes the raw tier because inspecting the raw feed is its reason to exist. The persona split is a *server* boundary (see MCP dual-persona design); the tier flags work *within* the consumer surfaces. Everything is on by default in dev; the flags are the interim access control — a per-caller permission model can later map onto the same grouping without reshaping the servers.

### Automatic SDL Generation

`graphql/sdl.py` introspects Pydantic field annotations and generates the SDL at process startup — there is no static `.graphql` file. Python → GraphQL type mapping:

| Python | GraphQL |
|---|---|
| `str` | `String!` |
| `int` | `Int!` |
| `float` | `Float!` |
| `bool` | `Boolean!` |
| `datetime` | `DateTime!` |
| `Optional[T]` | `T` (nullable) |
| `list[T]` | `[T!]!` |
| `Literal[...]` | `String!` |

This ensures the GraphQL schema is always consistent with the Python models. Schema drift is structurally impossible.

---

## Domain Model

Eight MongoDB collections model a simplified custodian bank ODS: six semantic-tier collections (camelCase fields; dates stored as MongoDB `Date` objects and serialized to ISO 8601 strings at the service boundary) plus two raw-tier collections (`raw_custody_positions`, `raw_vendor_securities`) that keep their source feeds' field names and wire-format string values verbatim — see the model docstrings in `bank_ods/models/raw_*.py` for the field conventions.

### Collections

#### `accounts` — Account master (with embedded client master)

Client reference data is **denormalized onto each account** as a required nested `client` sub-document (`ClientMaster` model). There is no separate clients collection; the standard external linkage key is the **LEI (ISO 17442**, 20-char with ISO 7064 MOD 97-10 check pair), and `client.clientId` is the internal key. All accounts of the same client embed an identical snapshot (enforced by `tests/test_master_data.py`).

```json
{
  "accountId":       "ACC-000123",
  "accountName":     "Maple Pension Fund - Equity",
  "accountType":     "CUSTODY",
  "client": {
    "clientId":               "CLT-000042",
    "clientName":             "Maple Pension Fund",
    "lei":                    "549300ABCDEF12345678",
    "countryOfDomicile":      "CA",
    "countryOfIncorporation": "CA",
    "taxResidencies":         ["CA", "US"],
    "classification":         "PROFESSIONAL",
    "kycStatus":              "APPROVED",
    "riskRating":             "LOW",
    "legalEntityType":        "FUND",
    "parentClientId":         null
  },
  "baseCurrency":    "CAD",
  "status":          "ACTIVE",
  "openDate":        "2018-03-01T00:00:00",
  "closeDate":       null,
  "custodianBranch": "Toronto"
}
```

`accountType`: CUSTODY | PROPRIETARY | OMNIBUS  
`status`: ACTIVE | SUSPENDED | CLOSED  
`client.classification` (MiFID-style): RETAIL | PROFESSIONAL | ELIGIBLE_COUNTERPARTY  
`client.kycStatus`: APPROVED | PENDING_REVIEW | EXPIRED  
`client.riskRating`: LOW | MEDIUM | HIGH  
`client.legalEntityType`: CORPORATION | PARTNERSHIP | FUND | TRUST | GOVERNMENT | INDIVIDUAL  
Country fields are ISO 3166-1 alpha-2; `taxResidencies` is the FATCA/CRS jurisdiction list (always contains the domicile). `parentClientId` links a client to its parent in the relationship hierarchy (nullable).  
Indexes: `accountId` (unique), `client.clientId`, `client.lei`, `status`

#### `securities` — Security master (with market-level listings)

Instruments are identified at two levels, mirroring the standard industry hierarchy (SEDOL Masterfile / FIGI):

- **Issue level** — `isin` (ISO 6166) identifies the security itself; the optional `figi` is the OpenFIGI *share-class* FIGI (1:1 with ISIN).
- **Market level** — the nested `listings` array (`Listing` model) carries one record per market of listing, each with its own **SEDOL**. SEDOLs are allocated one per country of official listing and, since 2008, one per traded currency on the same venue — so a dual-listed or multi-currency security has multiple listings. Existing mainframes key on SEDOL and match on settlement location; `settlementLocation` carries the market's CSD BIC (DTC / CDS / CREST) for that purpose.

Bonds have `listings: []` (not exchange-listed in this model). Exactly one listing per security is `primaryListing: true`; the top-level `exchange` field remains as a primary-listing display convenience.

```json
{
  "securityId":  "SEC-000016",
  "isin":        "CA7800871021",
  "cusip":       "780087102",
  "ticker":      "RY.TO",
  "figi":        "BBG00XKY2GD5",
  "description": "Royal Bank of Canada",
  "assetClass":  "EQUITY",
  "subType":     "COMMON_STOCK",
  "currency":    "CAD",
  "exchange":    "TSX",
  "issuer":      "Royal Bank of Canada",
  "country":     "CA",
  "maturityDate": null,
  "couponRate":   null,
  "status":      "ACTIVE",
  "listings": [
    {
      "sedol":              "B1WXR90",
      "micCode":            "XTSE",
      "operatingMic":       "XTSE",
      "exchangeName":       "TSX",
      "tradedCurrency":     "CAD",
      "countryOfListing":   "CA",
      "settlementLocation": "CDSLCATT",
      "localCode":          "RY",
      "primaryListing":     true,
      "status":             "ACTIVE"
    },
    {
      "sedol":              "B54HW23",
      "micCode":            "XNYS",
      "operatingMic":       "XNYS",
      "exchangeName":       "NYSE",
      "tradedCurrency":     "USD",
      "countryOfListing":   "US",
      "settlementLocation": "DTCYUS33",
      "localCode":          "RY",
      "primaryListing":     false,
      "status":             "ACTIVE"
    }
  ]
}
```

`assetClass`: EQUITY | GOVT_BOND | CORP_BOND | FUND | CASH  
`status`: ACTIVE | MATURED | DELISTED  
`listings[].status`: ACTIVE | SUSPENDED | DELISTED  
`listings[].sedol` is the 7-char LSEG SEDOL (6 alphanumeric excluding vowels + weighted mod-10 check digit). `micCode`/`operatingMic` are ISO 10383 segment and operating MICs (e.g. NASDAQ Global Select = segment `XNGS` under operating `XNAS`). `tradedCurrency` is ISO 4217; `countryOfListing` is ISO 3166-1 alpha-2.  
Indexes: `securityId` (unique), `isin` (unique, sparse), `ticker`, `assetClass`, `listings.sedol` (unique, partial multikey — see Index Strategy)

#### `transactions` — Trade and cash movements (highest volume)

```json
{
  "transactionId":   "TXN-20240115-001234",
  "transactionType": "BUY",
  "tradeDate":       "2024-01-15T00:00:00",
  "settlementDate":  "2024-01-17T00:00:00",
  "accountId":       "ACC-000123",
  "securityId":      "SEC-000001",
  "quantity":        100.0,
  "price":           185.50,
  "currency":        "USD",
  "grossAmount":     18550.00,
  "fees":            25.00,
  "netAmount":       18575.00,
  "fxRate":          1.3450,
  "counterpartyId":  "CPTY-GOLDM",
  "status":          "SETTLED",
  "settlementRef":   "STL-20240117-000789",
  "sourceSystem":    "ORDER_MGMT"
}
```

`transactionType`: BUY | SELL | DEPOSIT | WITHDRAWAL | TRANSFER_IN | TRANSFER_OUT | DIVIDEND | FX  
`status`: PENDING | MATCHED | SETTLED | FAILED | CANCELLED  
Indexes: `transactionId` (unique), `(accountId, tradeDate)` desc, `status`, `settlementDate`, `securityId`

#### `positions` — EOD security holdings (append-only snapshots)

```json
{
  "positionId":    "POS-ACC000123-SEC000001-20240115",
  "accountId":     "ACC-000123",
  "securityId":    "SEC-000001",
  "asOfDate":      "2024-01-15T00:00:00",
  "quantity":      500.0,
  "currency":      "USD",
  "costBasis":     89750.00,
  "marketPrice":   185.50,
  "marketValue":   92750.00,
  "unrealizedPnL": 3000.00,
  "positionType":  "LONG",
  "snapshotType":  "EOD"
}
```

`positionType`: LONG | SHORT  
`snapshotType`: EOD | INTRADAY | SETTLEMENT  
Indexes: `(accountId, securityId, asOfDate)` compound unique, `asOfDate`, `accountId`

#### `settlements` — Settlement instruction lifecycle

```json
{
  "settlementId":       "STL-20240117-000789",
  "transactionId":      "TXN-20240115-001234",
  "accountId":          "ACC-000123",
  "securityId":         "SEC-000001",
  "settlementDate":     "2024-01-17T00:00:00",
  "deliveryType":       "DVP",
  "quantity":           100.0,
  "currency":           "USD",
  "settlementAmount":   18575.00,
  "counterpartyId":     "CPTY-GOLDM",
  "status":             "SETTLED",
  "statusHistory": [
    { "status": "PENDING",    "timestamp": "2024-01-15T14:00:00Z" },
    { "status": "INSTRUCTED", "timestamp": "2024-01-15T16:00:00Z" },
    { "status": "MATCHED",    "timestamp": "2024-01-16T09:00:00Z" },
    { "status": "SETTLED",    "timestamp": "2024-01-17T10:23:00Z" }
  ],
  "failReason": null,
  "csdRef":     "DTCC-2024-XYZ",
  "swiftRef":   "MT54X-REF"
}
```

`deliveryType`: DVP | FOP | RVP | RFP  
`status`: PENDING | INSTRUCTED | MATCHED | SETTLED | FAILED | CANCELLED | RECYCLED  
Indexes: `settlementId` (unique), `transactionId`, `(accountId, settlementDate)`, `status`

#### `cash_balances` — Daily cash positions (append-only snapshots)

```json
{
  "balanceId":       "BAL-ACC000123-USD-20240115",
  "accountId":       "ACC-000123",
  "currency":        "USD",
  "asOfDate":        "2024-01-15T00:00:00",
  "openingBalance":  1250000.00,
  "credits":           18575.00,
  "debits":                0.00,
  "closingBalance":  1268575.00,
  "pendingCredits":      0.00,
  "pendingDebits":   18575.00,
  "projectedBalance": 1250000.00,
  "snapshotType":    "EOD"
}
```

`snapshotType`: EOD | INTRADAY  
Indexes: `(accountId, currency, asOfDate)` compound unique, `asOfDate`

### Temporal Data Pattern

Positions and cash balances are **append-only snapshots**, not in-place updates. Each EOD creates a new document. This preserves history and makes time-range queries straightforward.

---

## Service Layer — Function Reference

All service functions are `async def`. All accept and return plain Python dicts (JSON-safe after `serialize_doc()`). Dates are passed as ISO 8601 strings (`"YYYY-MM-DD"`).

### Error Envelope

Every service function returns one of:
- `{...data fields...}` — success (single item)
- `{"data": [...], "page_info": {"has_more": bool, "next_cursor": str|null}}` — success (list); data is one page, page_info carries the keyset cursor (see Pagination)
- `{"data": [...]}` — success (non-paginated aggregation: `get_transaction_summary`)
- `{"error": "...", "code": "NOT_FOUND"}` — item not found
- `{"error": "...", "code": "INVALID_DATE"}` — malformed date parameter (REST maps to 400)
- `{"error": "...", "code": "INVALID_CURSOR"}` — malformed, tampered, or foreign pagination cursor (REST maps to 400)
- `{"error": "Database error", "code": "MONGO_ERROR"}` — database error (details are logged server-side, never sent to clients)

Functions never raise exceptions to callers. Transport layers translate these envelopes to appropriate protocol-level errors.

### Date Semantics

A date parameter (`as_of_date`, `settlement_date`) means the **whole calendar day (UTC)** — documents stamped at any intraday time (e.g. 16:00 EOD snapshots) match. Date ranges (`from_date`/`to_date`) are inclusive of both end days, implemented as `$gte day_start(from), $lt day_start(to)+1d` (see `services/_common.py`).

### Value Serialization

- **Money/quantities/rates:** stored as MongoDB `Decimal128`, typed `decimal.Decimal` in the Pydantic models, and serialized as **exact strings** (`"18550.00"`) at the service boundary. GraphQL exposes them via a `Decimal` scalar; floats are never used for monetary values.
- **Timestamps:** ISO 8601 with explicit UTC offset (`2026-05-31T16:00:00+00:00`).

### Accounts

```python
get_account(account_id: str) → dict
list_accounts(client_id=None, status=None, lei=None, domicile=None, limit=50, cursor=None) → dict
# client_id/lei/domicile match the embedded client-master snapshot
```

### Securities

```python
get_security(security_id: str) → dict
get_security_by_sedol(sedol: str) → dict   # matches any listing's market-level SEDOL
list_securities(asset_class=None, ticker=None, status=None, sedol=None, limit=50, cursor=None) → dict
```

### Transactions

```python
get_transaction(transaction_id: str) → dict
get_transactions(account_id, from_date, to_date, status=None, transaction_type=None, limit=50, cursor=None) → dict
get_transaction_summary(account_id, from_date, to_date) → dict
# summary is not paginated; returns: {data: [{transactionType, status, count, totalNetAmount}]}
```

### Positions

```python
get_position(account_id, security_id, as_of_date) → dict
get_positions(account_id, as_of_date, limit=50, cursor=None) → dict
get_position_history(account_id, security_id, from_date, to_date, limit=50, cursor=None) → dict
# history is sorted ascending by asOfDate
```

### Settlements

```python
get_settlement(settlement_id) → dict
get_settlement_status(transaction_id) → dict   # lookup by transaction, not settlement ID
get_settlements(account_id, settlement_date, status=None, limit=50, cursor=None) → dict
get_settlement_fails(from_date, to_date, account_id=None, limit=50, cursor=None) → dict
```

### Balances

```python
get_cash_balance(account_id, currency, as_of_date) → dict
get_cash_balances(account_id, as_of_date, limit=50, cursor=None) → dict
get_projected_balance(account_id, currency, as_of_date) → dict
# projected returns: {accountId, currency, asOfDate, closingBalance, pendingCredits, pendingDebits, projectedBalance}
```

### Pagination — keyset cursors

All 8 list operations take a uniform `limit: int = 50` (clamped to `[1, 200]`) and `cursor: str | None = None`. There is no offset and no total count. Every page returns `page_info`: while `has_more` is true, pass `next_cursor` back verbatim as `cursor` to fetch the next page.

The implementation is a single shared helper, `services/pagination.py`:

- **Keyset (seek), not offset.** Each list method declares a deterministic sort; `paginate()` appends an `("_id", 1)` tie-breaker so the total order is always unique, fetches `limit + 1` documents to compute `has_more`, and encodes the last row's sort values into the cursor. The next page resumes with a range predicate (`$gt`/`$lt` `$or`-expansion), so page N+1 costs the same as page 1 regardless of depth, and rows are never duplicated or dropped when documents are inserted behind the current position.
- **Opaque cursor.** base64url(JSON) `{v, f, k}`: a format version, an 8-hex fingerprint of (collection, sort spec) that rejects cursors replayed against a different query shape, and the type-tagged sort-key values (datetime/ObjectId/Decimal128 round-trip losslessly). Cursors are deterministic — the same page boundary yields a byte-identical cursor in every transport, which the parity tests assert. Clients must treat cursors as opaque tokens; any malformed or foreign cursor yields `INVALID_CURSOR`.
- **Sort orders** (before the `_id` tie-breaker): accounts `accountId ↑`, securities `securityId ↑`, transactions `tradeDate ↓`, positions `securityId ↑`, position history `asOfDate ↑`, settlements `settlementId ↑`, settlement fails `settlementDate ↓`, cash balances `currency ↑`.

---

## Transport Layers

Each transport is a thin adapter. It receives a protocol-specific request, calls the appropriate service function, and translates the result to the protocol's response format. No transport contains business logic or database queries.

### MCP dual-persona design — `bank_ods.mcp` and `bank_ods.mcp_ops`

The MCP surface splits into two servers with different audiences, tool sets, and security postures. The split is deliberate: feature flags decide *what data* a surface exposes, but the two personas need *different tools*, a different trust boundary, and independent release cadences — that is a server boundary, not a flag.

**Consumer MCP — `bank-ods` (semantic persona)**

- Audience: AI agents, chatbot integrations, downstream application teams
- Entry point: `python -m bank_ods.mcp` (gated by `TRANSPORT_MCP_ENABLED`)
- Tools: 18 `@mcp.tool()` functions in `mcp/tools.py`, clean domain queries over semantic collections, each a single-line delegate to services
- Security: read-only, no raw feeds, no operational internals; productionized alongside REST and GraphQL in the same governance cycle

**Operations MCP — `bank-ods-ops` (ops/debug persona)**

- Audience: engineers debugging feed issues, QA, platform/support teams, and release-monitoring agents
- Entry point: `python -m bank_ods.mcp_ops` (gated by `TRANSPORT_MCP_OPS_ENABLED`)
- Security: internal-only by intent — deploy behind the platform boundary (stdio locally, internal SSE for ops tooling), never on the consumer path; releasable earlier and on its own cadence
- Tools (all read-only; Mongo logic in `services/ops.py`):
  - `ping_database` — reachability, server version, uptime, connections
  - `list_collections` — every registered collection with tier, count, last insert time
  - `get_collection_stats(collection)` — count, sizes, index inventory (registry-scoped names only)
  - `query_recent(collection, limit)` — newest documents with `_insertedAt` (from ObjectId)
  - `find_raw_records(collection, field, value)` — exact-match search over a raw collection by any feed field ("show me the raw records for account X"); field names validated against the model, raw-tier collections only
  - `reconcile_custody_feed(cycle_date?)` — traces a raw batch cycle into curated positions; classifies every unmatched record as UNKNOWN_ACCOUNT / UNKNOWN_SECURITY / NO_CURATED_POSITION ("why didn't this record appear?")
  - `get_recent_logs(level, limit)` — in-process log ring buffer (`logging_config.RingBufferHandler`); process-local by design, the platform log aggregator owns cross-process search
  - `run_release_checks` — composite PASS/WARN/FAIL rollup (reachability, population, feed freshness, reconciliation) designed for an AI agent monitoring a release to poll until PASS
  - Plus the registry-generated raw tool group (`get_raw_custody_position`, `list_raw_custody_positions`, `get_raw_vendor_security`, `list_raw_vendor_securities`), always registered here — raw feed inspection is an engineering activity, not a consumer one, so it is not subject to `EXPOSE_RAW_TIER` (which governs the consumer transports)
  - Full ops tool surface: 8 operational tools + 4 raw tools = 12

Both servers share the same service layer; the ops server adds operational tools that have no consumer equivalent. Transport for both: `MCP_TRANSPORT` env var (`stdio` default; `sse` for K8s). Startup: `ensure_indexes()` via lifespan; the ops server also attaches the log ring.

### REST — `bank_ods.rest`

- Framework: FastAPI
- Entry point: `uvicorn bank_ods.rest:app --port 8000`
- Docs: `http://localhost:8000/docs` (Swagger UI)
- Health: `GET /health` (liveness) → `{"status": "ok"}`; `GET /ready` (readiness, pings MongoDB) → `{"status": "ready"}` or 503
- Error mapping: HTTP 400 (INVALID_DATE, INVALID_CURSOR), 404 (NOT_FOUND), 500 (MONGO_ERROR) via `rest/errors.py check()`
- 6 hand-written semantic routers (accounts, securities, transactions, positions, settlements, balances) plus registry-generated raw routers (`rest/routers/raw.py`, mounted per raw collection when `EXPOSE_RAW_TIER` is on)

**Endpoint summary:**

| Prefix | Endpoints |
|---|---|
| `/accounts` | GET `/{id}`, GET `?client_id&status&lei&domicile&limit&cursor` |
| `/securities` | GET `/{id}`, GET `/sedol/{sedol}`, GET `?asset_class&ticker&status&sedol&limit&cursor` |
| `/transactions` | GET `/{id}`, GET `?account_id&from_date&to_date&status&transaction_type&limit&cursor`, GET `/summary?...` |
| `/positions` | GET `/{account_id}?as_of_date&limit&cursor`, GET `/{account_id}/{security_id}?as_of_date`, GET `/{account_id}/{security_id}/history?from_date&to_date&limit&cursor` |
| `/settlements` | GET `/{id}`, GET `/by-transaction/{txn_id}`, GET `?account_id&settlement_date&status&limit&cursor`, GET `/fails?from_date&to_date&account_id&limit&cursor` |
| `/balances` | GET `/{account_id}?as_of_date&limit&cursor`, GET `/{account_id}/{currency}?as_of_date`, GET `/{account_id}/{currency}/projected?as_of_date` |
| `/raw_custody_positions` | GET `?limit&cursor`, GET `/{record_id}` (REC_ID) — registry-generated, raw tier |
| `/raw_vendor_securities` | GET `?limit&cursor`, GET `/{record_id}` (Vendor_Ref) — registry-generated, raw tier |
| `/health` | GET |

### GraphQL — `bank_ods.graphql`

- Framework: Ariadne (ASGI)
- Entry point: `uvicorn bank_ods.graphql:app --port 8001`
- Endpoint: `POST http://localhost:8001/graphql`
- Health: `GET /health` → `{"status": "ok"}`
- SDL generated at runtime from the ENTITIES registry by `sdl.py`
- 22 query fields when both tiers are exposed (18 curated semantic fields + 4 registry-generated raw get/list fields); `limit: Int, cursor: String` on all list operations, list wrappers carry `pageInfo: PageInfo! { hasMore, nextCursor }`
- `DateTime` custom scalar serializes datetime to ISO string (UTC offset included); `Decimal` custom scalar serializes monetary values as exact strings
- Health: `GET /health` (liveness); `GET /ready` (readiness, pings MongoDB)
- Parameter names: camelCase in SDL (`fromDate`, `asOfDate`); resolvers map to service snake_case
- Query protection via graphql-core validation rules (`graphql/protection.py`): depth limit, root-field/alias cap, and an introspection kill-switch — all env-configurable (see Environment Variables); rejected queries never reach resolvers or MongoDB
- Contract governance: `tests/test_protection.py` asserts the generated SDL matches the checked-in `tests/schema.snapshot.graphql`, so any schema change shows up as a reviewable diff

#### Side-by-side library evaluation twins — `bank_ods.graphql_strawberry`, `bank_ods.graphql_graphene`

Two additional implementations of the identical GraphQL contract, built to evaluate proposed library swaps: Strawberry's experimental Pydantic integration (`uvicorn bank_ods.graphql_strawberry:app --port 8002`, GraphiQL included) and graphene + graphene-pydantic (`uvicorn bank_ods.graphql_graphene:app --port 8003`, no GraphiQL — graphene ships no ASGI integration). All three schemas are introspection-identical; `tests/test_strawberry_parity.py` and `tests/test_graphene_parity.py` enforce parity against service, REST, and Ariadne. Findings, benchmarks, and the version-landscape facts (graphene core last released 2024-12; graphene-pydantic 2024-02): [REVIEW-strawberry-graphql.md](REVIEW-strawberry-graphql.md). These packages are evaluation evidence, not part of the core pattern — each can be deleted without touching anything else.

---

## Index Strategy

| Collection | Indexes |
|---|---|
| accounts | accountId (unique), client.clientId, client.lei, status |
| securities | securityId (unique), isin (unique sparse), ticker, assetClass, listings.sedol (unique partial multikey) |
| transactions | transactionId (unique), (accountId, tradeDate desc, _id), status, settlementDate, securityId |
| positions | (accountId, securityId, asOfDate) compound unique, asOfDate, accountId |
| settlements | settlementId (unique), transactionId, (accountId, settlementDate), (status, settlementDate desc, _id) |
| cash_balances | (accountId, currency, asOfDate) compound unique, asOfDate |
| raw_custody_positions | REC_ID (unique), (POS_BUS_DATE, POS_ACCT_NBR), POS_CUSIP_NBR |
| raw_vendor_securities | Vendor_Ref (unique), Cusip (sparse) |

The compound unique index on `positions` and `cash_balances` enforces the append-only snapshot invariant: only one document per (account, security/currency, date).

The `listings.sedol` index is a **unique partial multikey index** (`partialFilterExpression: {"listings.sedol": {"$exists": true}}`) so securities with no listings (bonds) are excluded cleanly. Note that MongoDB multikey unique indexes enforce uniqueness *across* documents but not *within* one document's array — global SEDOL uniqueness is guaranteed by the seed generator and asserted by `tests/test_master_data.py`.

---

## Testing Strategy

Tests require a running MongoDB with seeded data (`python scripts/seed_data.py`).

| File | What it tests |
|---|---|
| `test_pagination.py` | Keyset cursor helper — cursor round-trip per BSON type, fingerprint/garbage rejection, seek-predicate shape, full cursor walks, mid-iteration insert stability |
| `test_services.py` | Service functions directly — happy paths, NOT_FOUND, filters, aggregation, cursor pagination |
| `test_rest.py` | REST endpoint HTTP status codes, response shapes, `/health`, 404s, cursor pagination, INVALID_CURSOR→400 |
| `test_graphql.py` | GraphQL query structure, `/health`, cursor pagination, INVALID_CURSOR error extension |
| `test_parity.py` | **Cross-layer equivalence** — REST == GraphQL == service for every operation, including byte-identical cursor strings |
| `test_parity_registry.py` | Registry-driven parity baseline — list/get/not-found parametrized over every exposed entity, so a new entity gets baseline coverage without a new test file |
| `test_mcp.py` | Consumer MCP parity leg — 18-tool surface + MCP tool results == service results, cursor follow |
| `test_mcp_ops.py` | Ops MCP server — 12-tool surface, raw tool parity, operational tools (health, stats, recent docs, reconciliation, logs, release checks) |
| `test_fixes.py` | Regressions: whole-day date windows, INVALID_DATE→400, securities parity, Decimal strings, UTC offsets, `/ready` |
| `test_protection.py` | Query protection (depth/alias limits, introspection toggle) + SDL snapshot guard |
| `test_master_data.py` | Reference-data integrity — SEDOL/LEI format + check digits, global SEDOL uniqueness, one primary listing per security, identical client-master snapshot across a client's accounts, parentClientId links resolve |
| `test_strawberry_parity.py`, `test_graphene_parity.py` | Evaluation twins — see REVIEW-strawberry-graphql.md |

172 tests total. All must pass before merge.

### Parity Test Pattern

```python
async def test_parity_get_account(rest_client, gql_client, first_account):
    account_id = first_account["accountId"]
    service = await svc_accounts.get_account(account_id)
    rest    = (await rest_client.get(f"/accounts/{account_id}")).json()
    gql     = (await gql_query(gql_client, f'{{ get_account(accountId: "{account_id}") {{ accountId }} }}'))["data"]["get_account"]
    assert service["accountId"] == rest["accountId"] == gql["accountId"]
```

The parity harness is the primary contract enforcement mechanism.

### Session-Scoped Fixtures

`conftest.py` establishes session-scoped fixtures:
- `db` — Motor database handle; triggers `ensure_indexes()`
- `first_account`, `first_security`, `dual_listed_security`, `first_balance`, `first_settled_txn` — fetched from seeded DB; known-good test anchors (`dual_listed_security` guarantees ≥2 listings)
- `rest_client` — `httpx.AsyncClient` with ASGI transport (no network)
- `gql_client` — same for GraphQL app

---

## Seed Data

`scripts/seed_data.py` uses sync `pymongo` with `faker` (seed=42 for reproducibility).

| Collection | Count | Notes |
|---|---|---|
| accounts | 20 | 10 clients, each with a consistent embedded client master (format-valid LEI, CA/US/GB/IE domiciles, 2 parent-linked clients); CUSTODY/PROPRIETARY/OMNIBUS mix; weighted ACTIVE |
| securities | 50 | 30 equities, 15 bonds, 5 ETFs; real-ish tickers; every equity/fund gets ≥1 listing with format-valid SEDOL + MIC + CSD; RY/TD/BNS dual-listed XTSE+XNYS; one XLON ETF listed in USD and GBP (per-currency SEDOL case); bonds have no listings |
| transactions | 2,000 | Last 90 days; 70% BUY/SELL; 80% SETTLED |
| settlements | 1,800 | One per trade transaction; full statusHistory |
| positions | 1,000 | EOD snapshots; account × security × date |
| cash_balances | 400 | EOD snapshots; account × currency (CAD/USD) × 10 days |

---

## Design Decisions

**Models as schema source of truth.** Pydantic field definitions are the only schema definition in the codebase. The ENTITIES registry propagates them to MongoDB index creation and GraphQL SDL generation. This makes schema drift structurally impossible: adding a field to a model automatically updates indexes and the SDL at next startup.

**Single service layer.** Every transport — the consumer trio (REST, GraphQL, `bank-ods` MCP) and the operations `bank-ods-ops` MCP server — calls `bank_ods.services.*`: the 18 curated entity functions, the `generic` get_one/get_many base they wrap, the metadata-driven `raw` helpers, and the `ops` introspection functions. No transport contains query logic. This makes transports interchangeable, independently deployable, and parity-testable.

**Error envelope, never raise.** Service functions return `{"error": ..., "code": ...}` dicts on failure. REST maps these to HTTP status codes via `check()` in `rest/errors.py`. GraphQL resolvers pass through to null-propagation. This keeps error handling explicit and consistent at each transport boundary without using exceptions as control flow.

**Append-only snapshots for temporal data.** Positions and balances write new documents per date rather than updating in place. This preserves full history without a change-log pattern and makes time-range queries O(index scan), not O(changelog replay).

**Securities identified at issue and market level.** ISIN (ISO 6166) identifies the issue; the nested `listings` array identifies each market line with its own SEDOL, MIC (ISO 10383), traded currency, and settlement CSD — mirroring the LSEG SEDOL Masterfile / FIGI hierarchy. This lets SEDOL-keyed upstream systems (the existing mainframes, which match on settlement location) resolve an instrument by market-level identifier while ISIN-keyed systems use the issue level, against the same document. Lookups traverse the multikey `listings.sedol` index; no join or second collection is needed.

**Client master embedded, not referenced.** Client reference data (LEI, domicile, tax residencies, classification, KYC status, risk rating, legal entity type, parent link) is denormalized into a `client` sub-document on every account rather than kept in a separate collection. One read of an account returns full client context — "get account details" needs no join — and the ODS stays at six collections. The linkage discipline that makes denormalization safe: `client.lei` (ISO 17442) is the standard external key and `client.clientId` the internal one, both indexed via dot-paths, and `tests/test_master_data.py` asserts every account of a client embeds an identical snapshot.

**ISO 8601 at boundaries.** External APIs send and receive `"YYYY-MM-DD"` strings. Services parse to `datetime` internally. Serialization back to strings happens in `serialize_doc()` at the return boundary, always with an explicit UTC offset (`+00:00`). A date parameter means the whole calendar day; ranges are inclusive of both end days.

**Decimal128 for money.** Monetary amounts, quantities, and rates are stored as MongoDB `Decimal128`, typed `decimal.Decimal` in the models, and serialized as exact strings on the wire (GraphQL `Decimal` scalar). IEEE-754 floats are never used for money.

**Keyset cursors, no totals.** List envelopes return one page plus `page_info: {has_more, next_cursor}`. Keyset (seek) pagination with a uniform `_id` tie-breaker makes every page deterministic and O(1) regardless of depth, and stays consistent under concurrent inserts — properties offset/`skip` pagination cannot provide. Dropping the total count removes a second `count_documents` query per request; clients page until `has_more` is false. Cursors are opaque, deterministic, and fingerprinted to the (collection, sort) they came from.

**SDL at runtime.** The GraphQL schema is generated from Pydantic models at process startup, not from a static `.graphql` file. The schema is always consistent with the Python models.

**Why fastmcp over the raw MCP SDK?** fastmcp removes boilerplate: JSON schema generation from type hints, transport setup, lifespan management.

**No MongoDB auth.** Local-only prototype. Do not add auth — it is unnecessary and complicates local setup.

---

## Logging

Structured JSON logging via `configure_logging(LOG_LEVEL)` in `logging_config.py`. All output to stdout.

Each HTTP request produces:
```json
{"level": "INFO", "logger": "bank_ods.http", "msg": "{\"method\": \"GET\", \"path\": \"/accounts\", \"status\": 200, \"duration_ms\": 12.3}"}
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DB` | `bank_ods` | Database name |
| `MONGO_TIMEOUT_MS` | `10000` | Server selection, connect, socket timeout (ms) |
| `DEBUG` | `false` | GraphQL debug mode; `true` exposes stack traces |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` (desktop) or `sse` (chatbot/K8s); applies to both MCP servers |
| `GRAPHQL_MAX_DEPTH` | `10` | Max query selection depth (validation rule; rejected before resolvers run) |
| `GRAPHQL_MAX_ROOT_FIELDS` | `10` | Max root fields per operation incl. aliases (blocks alias amplification) |
| `GRAPHQL_INTROSPECTION` | `true` | Set `false` in production to block `__schema`/`__type` queries |
| `TRANSPORT_REST_ENABLED` | `true` | Consumer REST transport startup gate |
| `TRANSPORT_GRAPHQL_ENABLED` | `true` | Consumer GraphQL transport startup gate |
| `TRANSPORT_MCP_ENABLED` | `true` | Consumer MCP server (`bank-ods`) startup gate |
| `TRANSPORT_MCP_OPS_ENABLED` | `true` | Operations MCP server (`bank-ods-ops`) startup gate |
| `EXPOSE_SEMANTIC_TIER` | `true` | Expose semantic-tier entities on the consumer transports (SDL fields, REST routers, semantic MCP tools) |
| `EXPOSE_RAW_TIER` | `true` | Expose raw-tier entities on the consumer transports; the ops server exposes raw regardless |

Copy `.env.example` to `.env` before running locally.

---

## Running Locally

```bash
# 1. Start MongoDB
docker compose up -d mongodb

# 2. Install dependencies
uv sync

# 3. Seed sample data
python scripts/seed_data.py

# 4. Run full test suite
pytest tests/ -v

# 5a. MCP server (stdio — for Claude Desktop / VS Code)
python -m bank_ods.mcp

# 5b. REST API
uvicorn bank_ods.rest:app --port 8000

# 5c. GraphQL API
uvicorn bank_ods.graphql:app --port 8001

# Or run everything via Docker Compose (REST + GraphQL + MongoDB)
docker compose up
```

### VS Code / Claude Desktop MCP Registration

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

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

The server name `bank-ods` corresponds to `FastMCP("bank-ods")` in `server.py`. Tools appear in Claude Code as `mcp__bank-ods__<tool_name>`.

---

## Kubernetes Deployment

The codebase includes K8s manifests for running the three transports as separate services, hardened for 40–50K GraphQL requests/day with EOD burst peaks (~10 req/sec).

### Load Profile

| Interface | Volume | Peak |
|---|---|---|
| GraphQL | 40–50K req/day | ~10 req/sec EOD burst |
| REST | ~10K req/day | lower |
| MCP | dev team usage | variable |

### Manifests (`k8s/`)

| Manifest | Description |
|---|---|
| `namespace.yaml` | `bank-ods` namespace |
| `configmap.yaml` | `MONGODB_DB`, `LOG_LEVEL`, `DEBUG`, `MONGO_TIMEOUT_MS` + MongoDB URI secret |
| `rest-deployment.yaml` | 2 replicas; `/health` liveness, `/ready` readiness (pings MongoDB) |
| `rest-service.yaml` | ClusterIP |
| `graphql-deployment.yaml` | 2 replicas baseline |
| `graphql-service.yaml` | ClusterIP |
| `graphql-hpa.yaml` | HPA: min 2, max 8 replicas at 60% CPU |
| `mcp-deployment.yaml` | 1 replica; `MCP_TRANSPORT=stdio` default |
| `mcp-service.yaml` | ClusterIP (active when `MCP_TRANSPORT=sse`) |

One uvicorn worker per pod; Motor manages its own async connection pool per process. K8s replicas and HPA provide horizontal scale. MongoDB URI is in a K8s Secret.

---

## Constraints

- Do not add collections beyond the six defined without discussion.
- Do not add MongoDB authentication.
- All new data access must go through `bank_ods.services.*`.
- Do not add mutation tools to the MCP server — this is a read-only ODS view.
