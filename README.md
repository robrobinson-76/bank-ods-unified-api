# Bank ODS — Multi-Transport Prototype

A Python prototype demonstrating one architectural pattern: **a single Pydantic model registry drives a shared service layer that is exposed identically via three transports — MCP, REST, and GraphQL.**

The domain is a simplified custodian bank ODS (accounts, positions, transactions, settlements, cash balances). The domain is illustrative. The pattern is the point.

---

## The Pattern

```
┌──────────────────────────────────────────────────┐
│  Pydantic Models  (models/)                      │
│  Single source of truth for fields and types     │
│                                                  │
│  ENTITIES registry propagates to:                │
│    → MongoDB index creation  (db/indexes.py)     │
│    → GraphQL SDL generation  (graphql/sdl.py)    │
└──────────────────┬───────────────────────────────┘
                   │
      ┌────────────▼────────────┐
      │     Service Layer       │
      │  bank_ods.services.*   │
      │  15 async functions     │
      │  One MongoDB access     │
      │  point for all layers   │
      └────────────┬────────────┘
                   │
   ┌───────────────┼───────────────┐
   │               │               │
┌──▼──────┐  ┌────▼──────┐  ┌────▼──────────┐
│   MCP   │  │   REST    │  │   GraphQL     │
│ Tools   │  │  FastAPI  │  │   Ariadne     │
│ stdio/  │  │  :8000    │  │   :8001       │
│   sse   │  │           │  │               │
└─────────┘  └───────────┘  └───────────────┘
```

**Three invariants enforced by the codebase:**

1. **Models are the schema.** Pydantic field definitions drive MongoDB indexes and the GraphQL SDL. No separate schema files, no migration scripts.
2. **One access point.** MongoDB is only touched through `bank_ods.services.*`. No transport contains query logic.
3. **Parity.** All three transports return identical data for identical inputs. `tests/test_parity.py` enforces this automatically.

---

## Why This Pattern

| Problem | Solution |
|---|---|
| Schema drift between transports | Pydantic models generate both the MongoDB indexes and the GraphQL SDL at startup |
| Business logic duplicated across transports | Single async service layer; each transport is a thin adapter |
| "Works in REST but not GraphQL" bugs | Cross-layer parity tests run against all three transports for every operation |
| Adding a new field | Update the model once; indexes and SDL update automatically on next startup |

---

## Quick Start

**Prerequisites:** Docker, Python 3.11+, [uv](https://github.com/astral-sh/uv)

```bash
# 1. Start MongoDB
docker compose up -d mongodb

# 2. Install dependencies
uv sync

# 3. Seed sample data (~5,200 documents)
python scripts/seed_data.py

# 4. Run the test suite (requires seeded data)
pytest tests/ -v

# 5. Start the transports
python -m bank_ods.mcp                          # MCP (stdio)
uvicorn bank_ods.rest:app --port 8000           # REST
uvicorn bank_ods.graphql:app --port 8001        # GraphQL (Ariadne)
uvicorn bank_ods.graphql_strawberry:app --port 8002  # GraphQL (Strawberry evaluation twin)
uvicorn bank_ods.graphql_graphene:app --port 8003    # GraphQL (Graphene evaluation twin)
```

Copy `.env.example` to `.env` before running.

---

## Project Structure

```
src/bank_ods/
├── models/          Pydantic v2 entity models + ENTITIES registry
├── db/              Motor client + ensure_indexes()
├── services/        15 async functions — all MongoDB access lives here
├── mcp/             @mcp.tool() wrappers → services (fastmcp)
├── rest/            FastAPI routers → services
├── graphql/         Ariadne resolvers → services; SDL generated from models
├── graphql_strawberry/  Strawberry evaluation twin — same contract, port 8002
└── graphql_graphene/    Graphene evaluation twin — same contract, port 8003
```

---

## Domain

Six MongoDB collections covering a read-only custodian bank ODS view:

| Collection | Description |
|---|---|
| `accounts` | Account master (20 seeded) |
| `securities` | Security master — equities, bonds, ETFs (50 seeded) |
| `transactions` | Trade and cash movements (2,000 seeded) |
| `positions` | EOD security holdings, append-only snapshots (1,000 seeded) |
| `settlements` | Settlement instruction lifecycle with status history (1,800 seeded) |
| `cash_balances` | Daily cash positions per currency, append-only (400 seeded) |

---

## Transports

### MCP

Exposes 15 read-only tools to any MCP-capable client (Claude Desktop, VS Code, etc.).

Register in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bank-ods": {
      "command": "uv",
      "args": ["run", "python", "-m", "bank_ods.mcp"],
      "cwd": "/path/to/mongo-mcp-test",
      "env": {
        "MONGODB_URI": "mongodb://localhost:27017",
        "MONGODB_DB": "bank_ods"
      }
    }
  }
}
```

Tools appear as `mcp__bank-ods__get_account`, `mcp__bank-ods__get_transactions`, etc. See [docs/AGENTS.md](docs/AGENTS.md) for the full tool reference.

### REST

```
GET /accounts/{id}
GET /transactions?account_id=&from_date=&to_date=
GET /positions/{account_id}?as_of_date=
GET /settlements/fails?from_date=&to_date=
GET /balances/{account_id}/{currency}/projected?as_of_date=
# ... and more
```

Swagger UI at `http://localhost:8000/docs`.

### GraphQL

```graphql
query {
  get_transactions(accountId: "ACC-0001", fromDate: "2025-01-01", toDate: "2025-03-31") {
    count
    data { transactionId transactionType netAmount status }
  }
}
```

Endpoint: `POST http://localhost:8001/graphql`. SDL is generated at startup from the Pydantic models.

**Query protection** (`bank_ods/graphql/protection.py`): graphql-core validation rules reject abusive queries before any resolver or MongoDB call runs —

| Protection | Env var | Default |
|---|---|---|
| Max selection depth (follows fragments) | `GRAPHQL_MAX_DEPTH` | `10` |
| Max root fields per operation, aliases included (blocks alias amplification — N aliased fields = N MongoDB queries) | `GRAPHQL_MAX_ROOT_FIELDS` | `10` |
| Introspection (`__schema`/`__type`) — set `false` in production | `GRAPHQL_INTROSPECTION` | `true` |

**Schema governance:** the generated SDL is snapshot-tested against `tests/schema.snapshot.graphql`, so any contract change — usually a model edit — fails CI until the snapshot is deliberately regenerated and reviewed in the same PR.

**Side-by-side library evaluation:** two alternative implementations of the same GraphQL contract exist for comparison — `bank_ods/graphql_strawberry` (Strawberry's experimental Pydantic integration, port 8002, GraphiQL included) and `bank_ods/graphql_graphene` (graphene + graphene-pydantic, port 8003). All three schemas are introspection-identical and answer the same queries — compare them live, or read the findings, benchmarks, and version-landscape facts in [docs/REVIEW-strawberry-graphql.md](docs/REVIEW-strawberry-graphql.md). `tests/test_strawberry_parity.py` and `tests/test_graphene_parity.py` enforce parity against service, REST, and Ariadne.

---

## How Data-Driven Schema Works

Each entity model declares its collection name and indexes as class variables:

```python
class Transaction(BankDocument):
    COLLECTION = "transactions"
    INDEXES = [
        ("transactionId", {"unique": True}),
        ([("accountId", 1), ("tradeDate", -1)], {}),
        ("status", {}),
    ]
    transactionId: str
    accountId: str
    tradeDate: datetime
    netAmount: float
    status: Literal["PENDING", "MATCHED", "SETTLED", "FAILED", "CANCELLED"]
    # ...
```

`ENTITIES = [Account, Security, Transaction, Position, Settlement, CashBalance]`

At startup:
- `db/indexes.py` iterates `ENTITIES` and calls `ensure_indexes()` — idempotent
- `graphql/sdl.py` introspects field annotations and generates the full SDL — no `.graphql` file needed

Adding a new entity: add the model, add it to `ENTITIES`. Done.

---

## Service Layer

All business logic lives in `bank_ods/services/`. Functions are `async def`, accept plain Python types, and return plain dicts:

```python
# Success (single)
{"accountId": "ACC-0001", "accountName": "...", ...}

# Success (list)
{"count": 42, "data": [...]}

# Error
{"error": "Account not found", "code": "NOT_FOUND"}
```

Transport layers translate these envelopes to protocol-level responses (HTTP 404, GraphQL null-propagation) via thin adapter code. Functions never raise to callers.

---

## Testing

```bash
pytest tests/ -v
```

| File | Coverage |
|---|---|
| `test_services.py` | Service functions directly |
| `test_rest.py` | REST status codes and response shapes |
| `test_graphql.py` | GraphQL query structure |
| `test_parity.py` | Cross-layer equivalence — REST == GraphQL == service |
| `test_strawberry_parity.py` | Strawberry twin — 4-way parity, schema introspection match, behavioral diffs |
| `test_graphene_parity.py` | Graphene twin — same harness against port-8003 implementation |
| `test_protection.py` | Query protection (depth/alias limits, introspection toggle) + SDL snapshot guard |

82 tests. The parity tests are the primary contract: if all transports return the same result, the pattern is working.

---

## Tech Stack

| Layer | Library |
|---|---|
| MCP | fastmcp |
| REST | FastAPI + uvicorn |
| GraphQL | Ariadne |
| GraphQL (evaluation twins) | Strawberry, Graphene + graphene-pydantic |
| MongoDB driver | motor (async) |
| Models | Pydantic v2 |
| Python | ≥ 3.11 |
| Database | MongoDB 7.0 (Docker) |

---

## Documentation

| Doc | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full architecture, domain model, service API, design decisions |
| [docs/AGENTS.md](docs/AGENTS.md) | MCP tool reference, query patterns, pagination, best practices |
| [docs/REVIEW-strawberry-graphql.md](docs/REVIEW-strawberry-graphql.md) | GraphQL library evaluation (Ariadne vs Strawberry vs Graphene) — findings, benchmarks, recommendation |

---

## Constraints

- Read-only — no mutation tools on any transport
- No MongoDB auth — local prototype only
- All MongoDB access via `bank_ods.services.*` — no query logic in transport layers
- Six collections defined; do not add more without discussion
