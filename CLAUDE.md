# mongo-mcp-test — Claude Code Project Context

## Purpose

Prototype exploring how a single MongoDB database can be exposed through three distinct interfaces — MCP, REST, and GraphQL — sharing one common data model (Pydantic v2) and one service layer.

The domain is a simplified custodian bank ODS (accounts, positions, transactions, settlements, cash balances). The domain is illustrative, not the point. The point is validating that a single async service core can drive all three transports with identical semantics, enforced by a cross-layer parity test harness.

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

# MCP server (stdio — Claude Code / VS Code)
python -m bank_ods.mcp

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

---

## MCP integration

Server name: `bank-ods`. Transport: `stdio` (default for Claude Desktop / VS Code) or `sse` (K8s chatbot deployment — set `MCP_TRANSPORT=sse`). See [docs/AGENTS.md](docs/AGENTS.md) for the full tool reference and `claude_desktop_config.json` registration block.

---

## Constraints — what Claude Code must not do

- Do not add MongoDB authentication — local-only prototype, no auth needed.
- Do not create collections beyond the six defined (`accounts`, `securities`, `transactions`, `positions`, `settlements`, `cash_balances`) without discussion.
- Do not add MongoDB query logic outside `bank_ods/services/*` — all three transport layers must call the service layer.
- Do not add mutation tools to the MCP server — this is a read-only ODS view.
