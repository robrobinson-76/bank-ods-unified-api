# Plan — ODS Ingest: Kafka/Avro bus with legacy source adapters

**Status: APPROVED FOR BUILD.** Design authority: [ARCHITECTURE-ingestion.md](ARCHITECTURE-ingestion.md) — read it first; this plan implements that design and does not restate its rationale.

---

## Context

The ODS serving side is complete (registry → services → MCP/REST/GraphQL, 172 tests). Both data tiers are currently populated only by `scripts/seed_data.py`. This plan builds the ingestion side: the **ODS Ingest** component — a Kafka/Avro bus, three legacy adapters (file, CDC, REST-poll), one generic sink, and per-entity curation — landing every flow in the raw tier and curating it into the semantic tier, served unchanged by the existing transports.

### Decisions locked at design review (do not relitigate during build)

| Decision | Value |
|---|---|
| Deletes | Soft delete — status transitions in semantic tier; raw tier keeps the delete event |
| Topics | One per (source, entity); cross-entity ordering NOT guaranteed; curation must be convergent |
| Retention | 7 days on raw topics; Mongo raw tier is system of record beyond that |
| REST source | Local stub SaaS, FastAPI backed by hard-coded JSON in the repo |
| Benchmark | ~1M-record EOD file; bus path vs. one-off pymongo bulk loader; results → `docs/FINDINGS-file-ingest-benchmark.md` |
| Packaging | `src/ods_ingest/` in this repo; import direction `ods_ingest → bank_ods` only, never the reverse |
| Kafka stack | Apache Kafka (KRaft, single broker) + Kafka Connect w/ Debezium + Apicurio Registry (Confluent-compat mode) |
| CDC landing | Append-only change-event log (deterministic `EVENT_ID`), not a latest-state mirror |
| Test gating | Ingestion e2e tests marked `@pytest.mark.ingest`, auto-skipped when the stack is down; core suite stays Mongo-only |

### Constraint call-outs (this plan is the required "discussion")

- **New registry entities (sanctioned path):** `RawCrmClientEvent`, `RawCrmAccountEvent`, `RawCashMovement` join `ENTITIES_RAW` — serving surfaces derive automatically.
- **One non-registry collection:** `ingest_state` — watermarks, processed-file ledger, sink heartbeats, DLQ summaries. Owned and written by `ods_ingest`, never served by any transport, deliberately outside the registry (it is operational state, not feed data). This is an explicit, approved exception to the "collections only via registry" rule.
- **Writer boundary:** `ods_ingest` writes Mongo with its own `pymongo` access (like `seed_data.py` today). The `bank_ods.services.*` read-only rule governs the *serving* layers and is untouched.

---

## Target structure

```text
docker-compose.ingest.yml        ← infra overlay: kafka, apicurio, connect, postgres-crm, stub-saas
infra/
├── crm/init.sql                 ← legacy CRM schema + seed rows (wal_level=logical via command flag)
└── connect/                     ← Dockerfile adding Confluent Avro converter to quay.io/debezium/connect

src/ods_ingest/
├── __init__.py
├── config.py                    ← env: KAFKA_BOOTSTRAP_SERVERS, SCHEMA_REGISTRY_URL, INGEST_LANDING_DIR,
│                                   INGEST_ARCHIVE_DIR, INGEST_QUARANTINE_DIR, SAAS_BASE_URL, POLL_INTERVAL_S…
├── envelope.py                  ← canonical Kafka header names + encode/decode helpers
├── schemas/                     ← authored .avsc wire contracts (one per non-CDC topic + manifest)
├── bus/
│   ├── admin.py                 ← python -m ods_ingest.bus.admin — creates topics (retention.ms=7d) + DLQs
│   ├── producer.py              ← Avro serializing producer (confluent-kafka + registry client)
│   ├── consumer.py              ← consume→batch→write→commit loop base; DLQ publish on poison records
│   └── dlq.py                   ← DLQ record shape (original bytes + error headers) + ingest_state summary
├── sink/
│   ├── __main__.py              ← python -m ods_ingest.sink
│   ├── mapping.py               ← topic → (raw model, extractor) map, derived from the bank_ods registry
│   ├── extractors.py            ← canonical (identity) and debezium (envelope → event doc) extractors
│   └── writer.py                ← idempotent bulk upsert by ID_FIELD; heartbeats to ingest_state
├── adapters/
│   ├── file/
│   │   ├── __main__.py          ← python -m ods_ingest.adapters.file
│   │   ├── watcher.py           ← landing-dir poll, rename-on-complete, archive/quarantine, dedup ledger
│   │   ├── fixed_width.py       ← copybook layout table + custody extract parser
│   │   ├── cash_csv.py          ← intraday cash movements parser
│   │   └── batches.py           ← header/trailer control totals, batchId, manifest emission
│   └── rest_poll/
│       ├── __main__.py          ← python -m ods_ingest.adapters.rest_poll
│       ├── poller.py            ← watermark loop, paging, 429/5xx backoff, overlap-window dedup
│       └── state.py             ← watermark persistence in ingest_state
├── curation/
│   ├── __main__.py              ← python -m ods_ingest.curation (runs all curators; --only <name>)
│   ├── decode.py                ← zoned decimal, overpunch sign, julian/CCYYMMDD → Decimal/datetime
│   ├── custody_positions.py     ← ods.raw.custody.positions → positions
│   ├── crm_accounts.py          ← ods.raw.crm.* → accounts (client-master fan-out, soft delete)
│   ├── vendor_securities.py     ← ods.raw.vendorsec.securities → securities
│   └── cash_movements.py        ← ods.raw.cash.movements → cash_balances (INTRADAY)
└── stub_saas/
    ├── app.py                   ← FastAPI: GET /securities?updated_since&page, fault injection, /_admin/touch
    └── vendor_securities.json   ← hard-coded dataset (~200 records)

src/bank_ods/models/             ← additions only
├── raw_crm_client_event.py      ← RawCrmClientEvent  (registry: ENTITIES_RAW)
├── raw_crm_account_event.py     ← RawCrmAccountEvent (registry: ENTITIES_RAW)
└── raw_cash_movement.py         ← RawCashMovement    (registry: ENTITIES_RAW)

scripts/
├── generate_custody_file.py     ← fixed-width EOD file generator (deterministic, --records up to 1M)
├── generate_cash_movements.py   ← intraday CSV generator
├── crm_mutate.py                ← scripted CRM change traffic (inserts/updates/deletes; named scenarios)
├── register_cdc_connector.py    ← POSTs Debezium connector config to Connect REST
├── bulk_load_custody.py         ← the one-off pymongo bulk loader (benchmark path B)
└── benchmark_file_ingest.py     ← orchestrates both benchmark paths, captures metrics, emits results

tests/
├── test_schema_contract.py      ← .avsc ↔ raw Pydantic model consistency (core suite, no infra)
├── test_envelope.py             ← header codec unit tests (core suite)
├── test_fixed_width.py          ← parser unit tests incl. zoned/overpunch/julian decode (core suite)
├── ingest/                      ← everything below @pytest.mark.ingest, auto-skip if stack down
│   ├── conftest.py              ← stack-reachability fixture (kafka+registry+connect+postgres+saas)
│   ├── test_e2e_file.py
│   ├── test_e2e_cdc.py
│   ├── test_e2e_rest.py
│   └── test_e2e_cash.py
└── (existing suites unchanged; test_parity_registry auto-covers the 3 new raw entities)
```

### Topic map

| Topic | Key | Partitions | Producer | Payload schema |
|---|---|---|---|---|
| `ods.raw.custody.positions` | `POS_ACCT_NBR` | 6 | file adapter | `raw_custody_position.avsc` |
| `ods.raw.custody.batches` | `batchId` | 1 | file adapter | `custody_batch_manifest.avsc` |
| `ods.raw.cash.movements` | account nbr | 3 | file adapter | `raw_cash_movement.avsc` |
| `ods.raw.vendorsec.securities` | `Vendor_Ref` | 3 | REST poller | `raw_vendor_security.avsc` |
| `ods.raw.crm.clients` | source PK | 3 | Debezium (RegexRouter SMT from `crm.public.clients`) | Debezium-managed |
| `ods.raw.crm.accounts` | source PK | 3 | Debezium (SMT) | Debezium-managed |
| `ods.dlq.<topic-suffix>` | — | 1 each | sink/curation | original bytes + error headers |

All topics `retention.ms` = 7 days, `cleanup.policy=delete`. Schema registry compatibility `BACKWARD` on every subject.

---

## Phase 0 — Foundations & infrastructure

### 0.1 Compose overlay — `docker-compose.ingest.yml`

Designed to run *with* the existing file (`docker compose -f docker-compose.yml -f docker-compose.ingest.yml up -d`) so all services share one network; the ODS compose file is not modified.

| Service | Image | Ports | Notes |
|---|---|---|---|
| `kafka` | `apache/kafka:4.x` | 9092 (host), 29092 (internal) | KRaft single node, dual listeners (host + docker network), healthcheck via `kafka-broker-api-versions` |
| `schema-registry` | `apicurio/apicurio-registry:3.x` | 8081 | in-memory storage; Confluent-compat API at `/apis/ccompat/v7` — that URL is what every client uses |
| `connect` | build `infra/connect/` (base `quay.io/debezium/connect:3.x`) | 8083 | Dockerfile layers in the Confluent Avro converter jars so the wire format is standard Confluent (magic byte + schema id) and Python `confluent_kafka` decodes it directly. Fallback if jar wrangling fights back: `ENABLE_APICURIO_CONVERTERS=true` with Apicurio serdes in ccompat mode — decide once, in this phase, and record it |
| `postgres-crm` | `postgres:17` | 5433 (host) | `command: -c wal_level=logical`; mounts `infra/crm/init.sql` |
| `stub-saas` | build (uvicorn `ods_ingest.stub_saas:app`) | 8010 | built in Phase 3; service entry added now, `profiles: [saas]` so it's inert until then |

Pin exact image tags at implementation time (verify current stable: Kafka 4.x, Debezium 3.x, Apicurio 3.x).

**Adapters/sink/curation run as local Python processes**, not containers — fast dev loop, and the landing directory stays a plain local path. Containerizing them is an explicit non-goal for the prototype (noted in ARCHITECTURE Phase table as ops-hardening).

### 0.2 Package skeleton + dependencies

- `src/ods_ingest/` skeleton per the tree above; `config.py` mirrors `bank_ods.config` style (env + `_flag`).
- `pyproject.toml`: add `confluent-kafka>=2.5`, `fastavro>=1.9`; dev group adds `psycopg[binary]>=3.2` (CRM mutation/tests). Add `"src/ods_ingest"` to `[tool.hatch.build.targets.wheel] packages`. Add `[tool.pytest.ini_options] markers = ["ingest: requires full ingest compose stack"]`.
- **Windows wheel check (do first):** `confluent-kafka` must install into the active interpreter (project currently runs Python 3.14 via pip — cp314 wheels may lag). If no wheel: prefer pinning the project venv to 3.12/3.13; fallback library `aiokafka`+`python-schema-registry-client` only if a venv change is impossible. Resolve before writing any bus code.
- Logging: `ods_ingest` reuses `bank_ods.logging_config.configure_logging` (import direction is allowed this way).

**Verify:** `python -m uv sync` clean; `python -c "import ods_ingest, confluent_kafka"`; `mypy` clean; existing 172 tests untouched and green.

### 0.3 Wire contracts — `.avsc` + consistency test

- Author `raw_custody_position.avsc`, `raw_vendor_security.avsc`, `raw_cash_movement.avsc`, `custody_batch_manifest.avsc` (manifest: `batchId`, `cycleDate`, `fileName`, `recordCount`, `controlTotals{shrQty, mktValue}`, `status: COMPLETE|FAILED`).
- `tests/test_schema_contract.py` (core suite): for each (`.avsc`, raw model) pair — field names match 1:1, Avro types map to the model's annotations (all raw fields are `str` today → Avro `string`; manifest is schema-only, no model), the model's `ID_FIELD` exists in the schema, schema is parseable by `fastavro`. CDC subjects are Debezium-managed and exempt (asserted by listing which subjects the test owns).

### 0.4 Bus utilities + topic provisioning

- `bus/admin.py`: idempotently create all topics + DLQs with the config in the topic map; `--dry-run` prints the diff. Registers the authored schemas under `<topic>-value` subjects and sets `BACKWARD` compatibility via the ccompat API.
- `bus/producer.py`: thin wrapper — Avro serializer bound to a subject, canonical headers from `envelope.py`, delivery-report error logging, flush-on-close.
- `bus/consumer.py`: the one consume loop everything reuses — poll batch → handler → Mongo write → commit offsets **after** write (at-least-once), poison record → `bus/dlq.py` (publish original bytes + `error`, `errorAt`, `sourceTopic`, `sourceOffset` headers; increment DLQ summary doc in `ingest_state`).
- `envelope.py`: header constants (`sourceSystem`, `adapterId`, `adapterVersion`, `batchId`, `recordSeq`, `extractedAt`) + typed encode/decode; unit tests.

**Phase 0 acceptance:** stack up healthy; `bus.admin` creates topics + registers schemas (visible in Apicurio UI); core suite green including the two new unit test files.

---

## Phase 1 — File adapter, generic sink, custody curation (full path #1)

The adapter is hand-written Python, not a Kafka Connect file connector and not Debezium (which has no file source at all). The evaluation and reasoning — copybook parsing, control totals, batch manifests, per-record DLQ — are in `ARCHITECTURE-ingestion.md` → Pattern 3 → "Why not Debezium or a generic file connector". Connect stays confined to Phase 2.

### 1.1 EOD file generator — `scripts/generate_custody_file.py`

- Deterministic (`--seed`, default 42), `--records N` (default 5,000; benchmark uses 1,000,000), `--cycle-date`, `--unknown-rate` (fraction of records referencing accounts/securities the ODS doesn't know — feeds `reconcile_custody_feed`'s UNKNOWN_* classifications).
- Reads seeded `accounts`/`securities` from Mongo so most records resolve during curation; encodes the copybook conventions exactly as `RawCustodyPosition`'s docstring specifies (zoned decimals, overpunch sign on `POS_ACCR_INT`, julian `POS_PRICE_DT`, CCYYMMDD dates, right-justified zero-filled account numbers).
- File shape: `01` header record (cycle date), `03` detail records, `99` trailer (record count + control totals: sum of `POS_SHR_QTY`, sum of `POS_MKT_VALUE`). Written as `<name>.tmp` then renamed to `CUSTPOS_<CCYYMMDD>.dat` (the completeness convention the watcher relies on).

### 1.2 Parser — `adapters/file/fixed_width.py` (+ `curation/decode.py`)

- Layout table (field name, offset, length) for header/detail/trailer; parser yields dicts with verbatim string values (adapter stays mechanical — no numeric decoding here).
- `curation/decode.py` owns the *interpretation*: zoned decimal → `Decimal`, overpunch sign, julian + CCYYMMDD → `datetime`. Unit tests in `tests/test_fixed_width.py` cover both, including the documented examples (`"0000000008505000"` → 850.5; `"0000012345}"` → -1234.50).

### 1.3 Watcher + batch processing — `watcher.py`, `batches.py`

- Poll `INGEST_LANDING_DIR` (stdlib loop, interval configurable); pick up only completed names (never `*.tmp`).
- `batchId = <fileName>:<sha256[:12] of file>` — re-dropping the same file is detected via the processed-file ledger in `ingest_state` and skipped (idempotent re-delivery); same name with different content is a new batch.
- Verify trailer control totals against parsed detail records **before** producing; mismatch → whole file to `INGEST_QUARANTINE_DIR` + a `FAILED` manifest. Per-record parse failure → that record to DLQ, batch continues (per-record error semantics).
- Produce detail records with `batchId`/`recordSeq` headers, `REC_ID = "<POS_BUS_DATE>-<recordSeq>"` (matches the existing loader convention); emit `COMPLETE` manifest to `ods.raw.custody.batches` after the last record's delivery is confirmed; archive the file.

### 1.4 Generic sink — `sink/`

- `mapping.py` builds topic→(model, extractor, collection) from `bank_ods.models.registry` plus a small per-source table (topic name, extractor kind). Adding a feed = one row.
- `writer.py`: batch `ReplaceOne(upsert=True)` keyed on the model's `ID_FIELD`, `ordered=False`; validates each doc through the raw Pydantic model before write (validation failure → DLQ, not a crash); commits offsets after the bulk write returns; writes a heartbeat doc per topic to `ingest_state` (`lastLandedAt`, `lastOffset`, counts) every batch.
- Manifest topic lands in `ingest_state` (batch ledger), not a raw collection.

### 1.5 Custody curation — `curation/custody_positions.py`

- Consumes `ods.raw.custody.positions` (own consumer group). Per record: decode wire values; resolve `POS_ACCT_NBR` → `accountId` and CUSIP/ISIN → `securityId` (reuse/extract the mapping logic `services/ops.py:reconcile_custody_feed` already implements — refactor it to a shared helper rather than duplicating); build a `positions` document (`snapshotType: "EOD"`, `asOfDate` from `POS_BUS_DATE`) and upsert by the compound key `(accountId, securityId, asOfDate)`.
- Unresolvable records: skip + count (they are *supposed* to exist — `reconcile_custody_feed` classifies them); no DLQ (they are valid raw records, just uncurated).

### 1.6 E2E test — `tests/ingest/test_e2e_file.py`

Generate a 500-record file (2% unknown-rate) into the landing dir → run adapter + sink + curation (in-process invocations of their run-once entry points, not subprocesses — each `__main__` exposes a `run_once()`/`run_until_idle()` used by tests) → assert: raw count, byte-exact `REC_ID`s, manifest ledger `COMPLETE` with matching control totals, curated `positions` present, `reconcile_custody_feed()` classifies exactly the planted unknowns, duplicate re-drop of the same file lands zero new documents.

**Phase 1 acceptance:** full path proven once; core suite + `-m ingest` file tests green; `mypy` clean.

---

## Phase 2 — CDC adapter (full path #2)

### 2.1 Legacy CRM — `infra/crm/init.sql` + `scripts/crm_mutate.py`

- Tables in deliberately source-flavored shape (lowercase snake, its own conventions): `clients(client_id pk, client_name, lei, country_domicile, country_incorp, tax_residencies text, classification, kyc_status, risk_rating, legal_entity_type, parent_client_id, updated_at)`, `accounts(account_nbr pk, client_id fk, account_name, account_type, base_ccy, status, open_date, close_date, branch, updated_at)`. Seed rows correspond to the ODS seed's 10 clients / 20 accounts so curation converges with existing data.
- `crm_mutate.py`: named, deterministic scenarios — `new-client` (insert client + 2 accounts), `kyc-flip` (update client kyc_status), `close-account` (update status), `delete-account` (SQL DELETE), `offboard-client` (DELETE client), `churn --n N` (mixed volume). Each prints what it did for test assertions.

### 2.2 Debezium connector — `scripts/register_cdc_connector.py`

POST to Connect REST (`:8083`): `debezium-connector-postgres`, `plugin.name=pgoutput`, publication auto-created for the two tables, `snapshot.mode=initial`, `topic.prefix=crm`, `tombstones.on.delete=false`, Avro key+value converters → registry ccompat URL, RegexRouter SMT `crm.public.(.*)` → `ods.raw.crm.$1`. Script is idempotent (PUT config) and has `--delete`.

### 2.3 Raw models — `RawCrmClientEvent`, `RawCrmAccountEvent`

- Append-only event log, one doc per change event. Shared shape: `EVENT_ID` (ID_FIELD, `"<source.lsn>-<table>-<pk>"`), `OP` (`r|c|u|d`), `TS_MS`, `LSN`, `PK`, and typed nested `before`/`after` state models (`CrmClientState` / `CrmAccountState`, all-string fields per raw-tier convention; nested models already proven by `ClientMaster` in SDL generation).
- Register in `ENTITIES_RAW`; indexes: `EVENT_ID` unique, `(PK, LSN)`, `OP`. Regenerate `tests/schema.snapshot.graphql` in the same commit (SDL grows) and let `test_parity_registry` pick the entities up.

### 2.4 Sink extractor — `extractors.py::debezium`

Unwraps the Debezium envelope (`payload.op/before/after/source/ts_ms`) into the event-doc shape; snapshot reads (`op=r`) land like inserts. Dedup is the `EVENT_ID` upsert (connector restarts re-emit — idempotent by construction).

### 2.5 Curation — `curation/crm_accounts.py`

Consumes both CRM topics; convergence rules (the cross-entity out-of-order decision made concrete):

| Event | Action on `accounts` |
|---|---|
| account `c`/`u`/`r` | Upsert account doc by `accountId` (mapped from `account_nbr`); embed the latest known client state — looked up from the raw client-event collection (latest `LSN` per `PK`); if the client is not yet known, embed a minimal placeholder and let the client event's fan-out complete it |
| client `c`/`u`/`r` | Fan-out: update the embedded `client` sub-document on **every** account with that `client.clientId` |
| account `d` | Soft delete: `status: "CLOSED"`, `closeDate` = event ts |
| client `d` | Offboarding: all that client's accounts → `status: "CLOSED"`, `closeDate` = event ts (embedded snapshot retained for audit) |

Field mapping table (CRM → `ClientMaster`/`Account`) lives in this module as data, unit-testable without Kafka.

### 2.6 Operational exercises (documented, not just run)

With churn traffic flowing: (a) stop the connector 10 min, watch `pg_replication_slots` WAL retention grow, restart, verify resume-without-loss; (b) `ALTER TABLE clients ADD COLUMN` mid-stream, verify BACKWARD evolution lands and curation ignores the new column; (c) delete + recreate the connector, verify snapshot re-run is absorbed idempotently. Findings → `docs/FINDINGS-cdc-operations.md` (the "real behavior, not the brochure" deliverable).

### 2.7 E2E — `tests/ingest/test_e2e_cdc.py`

Snapshot lands seed rows as `op=r` events; each mutation scenario asserts its raw events and curated outcome (kyc-flip fans out to all the client's accounts; offboard closes them; out-of-order tolerance: run `new-client` and assert convergence regardless of which topic's events curate first — inject by pausing one curator leg).

**Phase 2 acceptance:** snapshot + streaming + soft deletes proven; SDL snapshot regenerated; core + ingest suites green; CDC findings doc drafted.

---

## Phase 3 — REST adapter + intraday cash file (full paths #3, #4)

### 3.1 Stub SaaS — `ods_ingest/stub_saas/`

FastAPI over `vendor_securities.json` (~200 records in the vendor's bespoke shape, matching `RawVendorSecurity` fields, each with `updated_at`). Endpoints: `GET /securities?updated_since=<iso>&page=<n>&page_size=<n>` (sorted by `updated_at`, page envelope with `has_more`); fault injection via env (`SAAS_429_EVERY=7`, `SAAS_500_RATE=0.02`); `POST /_admin/touch {vendor_ref}` bumps a record's `updated_at` (how tests create "changes"); `POST /_admin/reset`. Compose service (profile `saas`) + runnable locally via uvicorn.

### 3.2 Poller — `adapters/rest_poll/`

- Loop every `POLL_INTERVAL_S` (default 30): read watermark from `ingest_state`, GET with `updated_since = watermark − overlap` (default overlap 60 s), walk pages, produce one Avro record per item to `ods.raw.vendorsec.securities`, advance watermark to max `updated_at` seen **after** all deliveries confirm.
- Overlap re-fetches are absorbed by the sink's `Vendor_Ref` upsert (this feed is latest-state — the SaaS presents current state, unlike CDC's event log; the sink mapping row simply keys on `ID_FIELD` as always). 429 → honor `Retry-After`; 5xx → exponential backoff; watermark never advances past an error.
- `--full-resync` flag: reset watermark to epoch (the "backfill = reset the watermark" claim, proven).

### 3.3 Curation — `curation/vendor_securities.py`

Vendor shape → `securities` upsert by resolved `securityId` (match on Cusip/ISIN against existing securities; unmatched vendor records are counted + skipped for `reconcile_vendor_securities` to report). Vendor "inactive/delisted" status → `status: "DELISTED"` (soft delete rule).

### 3.4 Intraday cash movements

- `scripts/generate_cash_movements.py`: small CSV drops (`CASHMOV_<CCYYMMDD>_<HHMM>.csv`, ~50 rows: account, ccy, amount signed, movement type, value ts).
- `RawCashMovement` model (`MOVEMENT_ID` = file batchId + seq as ID_FIELD) → `ENTITIES_RAW` (+ SDL snapshot regen); `adapters/file/cash_csv.py` parser plugs into the same watcher/batch framework (proving the framework carries a second format).
- `curation/cash_movements.py`: accumulate the day's movements into the current day's `cash_balances` doc — upsert by `(accountId, currency, asOfDate)` with `snapshotType: "INTRADAY"`, recomputing `credits`/`debits`/`closingBalance` from `openingBalance` (yesterday's close) + all movements landed so far (idempotent recompute from raw, not increment — replays converge). Note: the day's doc flips EOD→INTRADAY semantics if both feeds write the same key; acceptable for the prototype and called out in the findings.

### 3.5 E2E — `test_e2e_rest.py`, `test_e2e_cash.py`

REST: initial sync lands all records; `_admin/touch` + next poll lands exactly the touched record; kill/restart poller mid-walk loses nothing (watermark discipline); 429 injection slows but completes. Cash: two intraday drops accumulate correctly; re-dropping a file changes nothing.

**Phase 3 acceptance:** all four flows land and curate; registry-derived surfaces show all 5 raw entities (parity tests auto-cover); core + ingest suites green.

---

## Phase 4 — Ops tooling, benchmark, documentation

### 4.1 Ops service functions + MCP tools (Mongo-only, per the boundary: the ODS never talks to Kafka)

In `services/ops.py`, reading `ingest_state` + raw collections:

- `get_ingestion_status()` — per source: last landed at, records landed today, last batch/manifest status, watermark position, heartbeat age.
- `get_dlq_summary()` — per topic: DLQ count + latest error samples (from the sink's `ingest_state` DLQ summaries; live topic lag is explicitly out of scope for ops MCP).
- `reconcile_crm_accounts()` — latest CRM event state vs. curated accounts (missing, stale-embed, status mismatch).
- `reconcile_vendor_securities()` — vendor records vs. curated securities (unmatched, stale).
- Extend `run_release_checks()` — feed-freshness per source (threshold per feed cadence) + DLQ-empty checks, keeping the PASS/WARN/FAIL rollup contract.

Register on `bank-ods-ops` only (`mcp_ops/tools.py`); extend `tests/test_mcp_ops.py` (core suite — these read Mongo only; fixtures write synthetic `ingest_state` docs). Ops tool surface grows 12 → 16 + 4 raw tools from the registry additions.

### 4.2 Benchmark — the committed deliverable

- `scripts/bulk_load_custody.py` (path B): parse the same fixed-width file with the same parser, `insert_many` in 5,000-doc batches, `ordered=False`, into an empty identically-indexed `raw_custody_positions`; prints wall clock + records/sec. Deliberately one-off: no bus, no manifest, no lineage — that asymmetry is part of the finding.
- `scripts/benchmark_file_ingest.py`: orchestrates — generate the 1M-record file once; **Path A** (adapter → Kafka → sink; timed from file pickup to sink heartbeat count = 1M; also records time-to-first-queryable) vs. **Path B** (bulk loader). Samples `docker stats` for broker CPU/mem during A; 3 runs each, medians reported; emits `benchmark_results.json`.
- Write `docs/FINDINGS-file-ingest-benchmark.md`: methodology, hardware note, results table, records/sec + wall-clock + time-to-first-queryable comparison, what Path B gives up (lineage/replay/fan-out/monitoring/contract), and the recommendation incl. where the claim-check crossover sits. *(Optional stretch if time allows: measure a claim-check hybrid — manifest on bus, sink bulk-loads from file — as a third row.)*

### 4.3 Documentation consolidation

- `ARCHITECTURE.md`: add the ingestion component to the system overview + project layout; new collections in the domain/index tables; ops tool list update.
- `AGENTS.md`: new ops tools + the 3 new raw entities' generated tools.
- `CLAUDE.md`: doc table row for `PLAN-ingestion.md` + `ARCHITECTURE-ingestion.md`; update quick start (compose overlay + ingest process commands); amend the constraints block ("eleven registered collections; `ingest_state` is the approved non-registry exception; `ods_ingest` is the sanctioned writer").
- `ARCHITECTURE-ingestion.md`: status → IMPLEMENTED, link findings docs.
- `.env.example`: new ingest variables.

**Phase 4 acceptance:** ops tools live on `bank-ods-ops` with tests; benchmark run at 1M records with results doc committed; all docs consistent; core suite green (grown by the new core-suite tests), `-m ingest` suite green against the running stack.

---

## Test matrix

| Tier | Marker | Needs | Runs |
|---|---|---|---|
| Unit (parsers, decode, envelope, schema contract, curation mapping tables) | none | nothing | always — part of core suite |
| Core (existing 172 + registry parity for new entities + ops tools) | none | Mongo + seed | always — the merge gate |
| Ingest e2e (per-flow) | `ingest` | full compose stack | when touching `ods_ingest`; auto-skip with a clear reason when the stack is down |

Merge rule: core suite green always; ingest suite green for any PR touching `src/ods_ingest/`, `infra/`, or the compose overlay.

## Build order & session sizing

Phases are strictly sequential (each proves infrastructure the next assumes). Within a phase, tasks are ordered for one-session increments with a green suite at each boundary. Suggested commits: `0.1–0.2`, `0.3–0.5`, `1.1–1.2`, `1.3–1.4`, `1.5–1.6`, `2.1–2.2`, `2.3–2.4`, `2.5`, `2.6–2.7`, `3.1–3.2`, `3.3`, `3.4–3.5`, `4.1`, `4.2`, `4.3`.

## Known build risks (watch during implementation)

1. **`confluent-kafka` wheel availability on the current Python** — resolved first thing in 0.2; mitigation documented there.
2. **Debezium + Apicurio converter wiring** — the one genuinely fiddly infra task; time-boxed in 0.1 with the documented fallback. Everything downstream only assumes "Confluent wire format on the topic."
3. **Kafka-in-Docker dual listeners on Windows** — host-vs-container advertised listeners are a classic footgun; 0.1's healthcheck + a smoke script (`bus/admin.py --dry-run` from the host) proves it before any adapter work.
4. **1M-record benchmark on a laptop** — Path A may take tens of minutes; the benchmark script supports `--records` so the pipeline is developed at 10K and only the final measured runs use 1M.
5. **SDL snapshot churn** — three raw-entity additions each regenerate `tests/schema.snapshot.graphql`; keep each regen in the same commit as its model per the standing rule.
