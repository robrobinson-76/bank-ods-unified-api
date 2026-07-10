# mongo-mcp-test — Claude Code Project Context

## Purpose

Prototype exploring how a single MongoDB database can be exposed through three distinct interfaces — MCP, REST, and GraphQL — sharing one common data model (Pydantic v2) and one service layer.

The domain is a simplified custodian bank ODS (accounts, positions, transactions, settlements, cash balances). The domain is illustrative, not the point. The point is validating that a single async service core can drive all three transports with identical semantics, enforced by a cross-layer parity test harness.

The data model is tiered: a curated **semantic tier** plus a **raw tier** of as-received feed records (a fixed-width mainframe custody position extract and a bespoke vendor security master), registered in one entity registry and individually exposable per deployment via feature flags.

This is a self-contained local development prototype. It is **not** a production system.

---

## Documentation

| Doc | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current-state architecture: layers, domain model, service API, indexes, K8s deployment, design decisions |
| [docs/AGENTS.md](docs/AGENTS.md) | MCP tool reference, parameter formats, pagination, query patterns, naming conventions, best practices |
| [docs/PLAN.md](docs/PLAN.md) | Original phased implementation plan — **reference only, do not modify** |
| [docs/PLAN-multilayer.md](docs/PLAN-multilayer.md) | Unified MCP/REST/GraphQL plan — **reference only, do not modify** |
| [docs/PLAN-k8s-scalability.md](docs/PLAN-k8s-scalability.md) | K8s scalability implementation plan — **reference only, do not modify** |
| [docs/REVIEW-strawberry-graphql.md](docs/REVIEW-strawberry-graphql.md) | GraphQL library evaluation (Ariadne vs Strawberry vs Graphene); the twins in `bank_ods/graphql_strawberry` (8002) and `bank_ods/graphql_graphene` (8003) are its living evidence |

Read `ARCHITECTURE.md` for codebase orientation. Read `AGENTS.md` before writing queries or extending the MCP tool surface.

---

## Target directory

```
C:\dev\clio-git\mongo-mcp-test\
```

---

## Quick start

```bash
docker compose up -d
uv sync
python scripts/seed_data.py
pytest tests/ -v

# Consumer MCP server (semantic domain tools; stdio — Claude Code / VS Code)
python -m bank_ods.mcp

# Operations MCP server (raw feed inspection + ops tooling; internal-only)
python -m bank_ods.mcp_ops

# REST API
uvicorn bank_ods.rest:app --port 8000

# GraphQL API (Ariadne — the current solution)
uvicorn bank_ods.graphql:app --port 8001

# GraphQL API (Strawberry — side-by-side evaluation twin, same contract)
uvicorn bank_ods.graphql_strawberry:app --port 8002

# GraphQL API (Graphene — side-by-side evaluation twin, same contract)
uvicorn bank_ods.graphql_graphene:app --port 8003
```

Environment: copy `.env.example` to `.env`. See `ARCHITECTURE.md` → Environment Variables.

GraphQL query protection: the Ariadne layer enforces depth, root-field/alias, and introspection limits via `graphql/protection.py`, configured by `GRAPHQL_MAX_DEPTH` / `GRAPHQL_MAX_ROOT_FIELDS` / `GRAPHQL_INTROSPECTION` (introspection should be `false` in production). The generated SDL is snapshot-tested against `tests/schema.snapshot.graphql` — if a model change alters the schema intentionally, regenerate the snapshot in the same commit (command in `tests/test_protection.py`).

---

## MCP integration

Two MCP servers, two personas (see `ARCHITECTURE.md` → MCP dual-persona design):

- **`bank-ods`** (`python -m bank_ods.mcp`) — consumer persona: 18 semantic domain tools for AI agents and downstream teams. Productionized with REST/GraphQL.
- **`bank-ods-ops`** (`python -m bank_ods.mcp_ops`) — operations persona: raw feed inspection (registry-generated), collection health/stats, recent-document inspection, raw-vs-curated reconciliation, in-process logs, and `run_release_checks` for release-monitoring agents. Internal-only; never on the consumer path.

Transport for both: `stdio` (default for Claude Desktop / VS Code) or `sse` (`MCP_TRANSPORT=sse`). See [docs/AGENTS.md](docs/AGENTS.md) for the full tool reference of both servers and the `claude_desktop_config.json` registration block.

---

## Constraints — what Claude Code must not do

- Do not add MongoDB authentication — local-only prototype, no auth needed.
- Do not create collections beyond those in the entity registry (`bank_ods/models/registry.py`: six semantic-tier + two raw-tier collections) without discussion. New collections go through the registry — a model declares `COLLECTION` / `INDEXES` / access metadata, and indexes, SDL fields, routes, MCP tools, and baseline parity tests derive from it.
- Do not add MongoDB query logic outside `bank_ods/services/*` — all three transport layers must call the service layer (entity services, or the generic/raw helpers).
- Do not add mutation tools to either MCP server — this is a read-only ODS view. Ops tools may introspect (counts, stats, logs, reconciliation) but never write.
- Do not put raw-tier or operational tools on the consumer `bank-ods` MCP server — they belong on `bank-ods-ops`. The persona split (audience + security posture) is the design, not an accident.
- Keep new transport surfaces behind the existing feature gates (`TRANSPORT_*_ENABLED`, `EXPOSE_SEMANTIC_TIER` / `EXPOSE_RAW_TIER`) — everything on by default in dev, individually deniable in a deployment.
