# mongo-mcp-test — Claude Code Project Context

## Purpose

Prototype exploring how a single MongoDB database can be exposed through three distinct interfaces — MCP, REST, and GraphQL — sharing one common data model (Pydantic v2) and one service layer.

The domain is a simplified custodian bank ODS (accounts, positions, transactions, settlements, cash balances). The domain is illustrative, not the point. The point is validating that a single async service core can drive all three transports with identical semantics, enforced by a cross-layer parity test harness.

The data model is tiered: a curated **semantic tier** plus a **raw tier** of as-received feed records (a fixed-width mainframe custody position extract, a bespoke vendor security master, intraday cash drops, and two CRM change-event logs), registered in one entity registry and individually exposable per deployment via feature flags.

The **write side** is a separate component, `src/ods_ingest` — legacy source adapters (flat file, Debezium CDC, REST polling) feeding a Kafka/Avro bus, one registry-driven sink landing the raw tier, and per-entity curation into the semantic tier. It is not part of the ODS proper; it adapts sources *into* it. See [docs/ARCHITECTURE-ingestion.md](docs/ARCHITECTURE-ingestion.md).

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
| [docs/ARCHITECTURE-ingestion.md](docs/ARCHITECTURE-ingestion.md) | ODS Ingest design: Kafka/Avro bus, legacy adapters (file, CDC, REST-poll), generic sink, curation, risks, resolved review decisions |
| [docs/PLAN-ingestion.md](docs/PLAN-ingestion.md) | ODS Ingest implementation plan (Phases 0–4) — **reference only, do not modify** |
| [docs/FINDINGS-cdc-operations.md](docs/FINDINGS-cdc-operations.md) | Measured CDC behaviour: converter/registry version alignment, replication-slot WAL growth while stopped, DDL drift, connector reset |
| [docs/FINDINGS-file-ingest-benchmark.md](docs/FINDINGS-file-ingest-benchmark.md) | Bus vs. direct bulk load on a 1M-record EOD file — timings, code-cost per new source, and where the claim-check escalation applies |
| [docs/ARCHITECTURE-adapter-scale.md](docs/ARCHITECTURE-adapter-scale.md) | Design note: repository / deployment / ownership topology at ~24 adapters, what must stay centrally owned, and how an adapter is handed to a source team |
| [docs/PATTERN-snapshot-and-stream.md](docs/PATTERN-snapshot-and-stream.md) | Start-of-day full-population true-up alongside an intraday stream (e.g. a 40M securities master): write-strategy measurements, the SOD-vs-intraday ordering hazard, and why the answer is upstream delta computation |

Read `ARCHITECTURE.md` for codebase orientation. Read `AGENTS.md` before writing queries or extending the MCP tool surface.

---

## Target directory

```text
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

### ODS Ingest (src/ods_ingest — the write side)

```bash
# Infrastructure overlay: kafka, schema registry, kafka connect, postgres CRM
docker compose -f docker-compose.yml -f docker-compose.ingest.yml up -d
python -m ods_ingest.bus.admin              # create topics, register schemas
python scripts/crm_seed.py                  # load the legacy CRM from seeded accounts
python scripts/register_cdc_connector.py    # start Debezium capture

# The schema registry is the in-memory Apicurio image, so it starts EMPTY after
# any restart of that container. Re-run bus.admin to re-register the authored
# schemas, and reset the connector so Debezium re-registers its own — otherwise
# consumers dead-letter every record with "No content with id ...".
python -m ods_ingest.bus.admin
python scripts/register_cdc_connector.py --reset

# Adapters (each also takes --once for a single pass)
python scripts/generate_custody_file.py --records 5000
python -m ods_ingest.adapters.file --once             # EOD custody extract
python -m ods_ingest.adapters.file --feed cash --once # intraday cash drops
uvicorn ods_ingest.stub_saas:app --port 8010          # stand-in vendor SaaS
python -m ods_ingest.adapters.rest_poll --once        # vendor security master

# Start-of-day full-population true-up: diffs against the retained key index
# and produces only what changed. --dry-run reports the delta without producing.
python scripts/generate_securities_snapshot.py --change-rate 0.01 --add 2 --drop 1
python -m ods_ingest.adapters.snapshot --once --dry-run
python -m ods_ingest.adapters.snapshot --once

# Land the raw tier, then curate into the semantic tier
python -m ods_ingest.sink --once
python -m ods_ingest.curation --once

# Ingestion tests need the stack up; the core suite never does
pytest tests/ -m "not ingest"     # core suite (Mongo only) — the merge gate
pytest tests/ -m ingest           # end-to-end, auto-skips if the stack is down
```

Environment: copy `.env.example` to `.env`. See `ARCHITECTURE.md` → Environment Variables.

GraphQL query protection: the Ariadne layer enforces depth, root-field/alias, and introspection limits via `graphql/protection.py`, configured by `GRAPHQL_MAX_DEPTH` / `GRAPHQL_MAX_ROOT_FIELDS` / `GRAPHQL_INTROSPECTION` (introspection should be `false` in production). The generated SDL is snapshot-tested against `tests/schema.snapshot.graphql` — if a model change alters the schema intentionally, regenerate the snapshot in the same commit (command in `tests/test_protection.py`).

---

## MCP integration

Two MCP servers, two personas (see `ARCHITECTURE.md` → MCP dual-persona design):

- **`bank-ods`** (`python -m bank_ods.mcp`) — consumer persona: 18 semantic domain tools for AI agents and downstream teams. Productionized with REST/GraphQL.
- **`bank-ods-ops`** (`python -m bank_ods.mcp_ops`) — operations persona: raw feed inspection (registry-generated), collection health/stats, recent-document inspection, raw-vs-curated reconciliation, ingestion status / DLQ / batch history, in-process logs, and `run_release_checks` for release-monitoring agents. Internal-only; never on the consumer path.

Transport for both: `stdio` (default for Claude Desktop / VS Code) or `sse` (`MCP_TRANSPORT=sse`). See [docs/AGENTS.md](docs/AGENTS.md) for the full tool reference of both servers and the `claude_desktop_config.json` registration block.

---

## Constraints — what Claude Code must not do

- Do not add MongoDB authentication — local-only prototype, no auth needed.
- Do not create collections beyond those in the entity registry (`bank_ods/models/registry.py`: six semantic-tier + five raw-tier collections) without discussion. New collections go through the registry — a model declares `COLLECTION` / `INDEXES` / access metadata, and indexes, SDL fields, routes, MCP tools, and baseline parity tests derive from it. The one approved exception is `ingest_state`, ODS Ingest's own operational bookkeeping (watermarks, batch ledger, sink heartbeats, DLQ counters), which no transport serves.
- Do not add MongoDB query logic outside `bank_ods/services/*` — all three transport layers must call the service layer (entity services, or the generic/raw helpers).
- Do not add mutation tools to either MCP server — this is a read-only ODS view. Ops tools may introspect (counts, stats, logs, reconciliation) but never write.
- Do not put raw-tier or operational tools on the consumer `bank-ods` MCP server — they belong on `bank-ods-ops`. The persona split (audience + security posture) is the design, not an accident.
- Keep new transport surfaces behind the existing feature gates (`TRANSPORT_*_ENABLED`, `EXPOSE_SEMANTIC_TIER` / `EXPOSE_RAW_TIER`) — everything on by default in dev, individually deniable in a deployment.

### ODS Ingest constraints

- `ods_ingest` may import `bank_ods` (models, registry, logging); `bank_ods` must **never** import `ods_ingest`. The ODS read side stays independent of how data arrives.
- `ods_ingest` is the sanctioned **writer** of the fed collections; the ODS transports stay read-only. It uses its own sync `pymongo` access, like `scripts/seed_data.py`.
- Adapters stay mechanical — capture what the source said, verbatim, into a typed record. Every judgement call (decoding, key resolution, code-list mapping, delete policy) belongs in `ods_ingest/curation/*`, where it is replayable against landed raw data.
- The ODS never connects to Kafka. Ingestion ops tools read the `ingest_state` collection; live broker metrics are the platform's concern.
- Every raw-tier model needs a deterministic `ID_FIELD` derivable from source content. Delivery is at-least-once, so idempotent upsert on a natural key is the only thing preventing duplicates.
- Source deletes are **soft**: status transitions (`CLOSED`, `DELISTED`), never document removal. The delete event itself is retained in the raw tier.
- A `.avsc` contract change and its raw-model change land in the same commit — `tests/test_schema_contract.py` fails otherwise (regenerate with `python scripts/regen_avro_schemas.py`).
