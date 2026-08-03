# Ingestion Architecture — Legacy Source Adapters (Draft for Review)

**Status: IMPLEMENTED.** Built as designed — see [PLAN-ingestion.md](PLAN-ingestion.md) for the phased plan and `src/ods_ingest/` for the code. All four flows (EOD custody file, CDC, REST poll, intraday cash) run end to end against the real stack, covered by 29 tests under `tests/ingest/`.

What the build learned, measured rather than assumed:

- [FINDINGS-cdc-operations.md](FINDINGS-cdc-operations.md) — converter/registry version alignment (the single most expensive problem in the build), replication-slot WAL growth on the source database while the connector is stopped, DDL drift absorption, and why connector reset is a four-step procedure.
- [FINDINGS-file-ingest-benchmark.md](FINDINGS-file-ingest-benchmark.md) — the committed bus-vs-bulk-load comparison on a 1M-record EOD extract.

Deviations from the plan, all recorded in place below or in the findings: the schema registry is pinned to Apicurio 2.6.x rather than 3.x (§ wire contract), the CRM database is on host port 5434 rather than 5433 (a native PostgreSQL install shadows it), the stub SaaS runs as a local process rather than a compose service, and the three new raw models were added in one pass during Phase 0 rather than one per phase to avoid three separate SDL snapshot regenerations.

---

## Purpose

The ODS prototype currently proves the *serving* side: one model registry → one service layer → four transports, with parity enforced by tests. Nothing proves the *ingestion* side — today both tiers are populated by `scripts/seed_data.py`.

This document designs the ingestion side: a new **ODS Ingest** component that is **not part of the ODS proper** — it adapts external sources *into* the ODS. It is expected to be owned by the same group, but it is a separate component with its own deployables, its own failure modes, and a deliberately narrow contract with the ODS (the raw-tier collections and the entity registry).

### The strategic requirement

> All sources feed the ODS via **Kafka**, carrying **Avro**-encoded records under a governed schema contract.

Some source systems cannot meet that requirement. For those, **legacy adapters** bridge the source onto the same Kafka/Avro bus, so that everything downstream of the bus — landing, curation, lineage, replay, monitoring, reconciliation — is identical regardless of how the data originally arrived. The adapter is the *only* thing that knows the source is legacy.

### What this prototype must prove

1. Two or three genuinely different legacy adapter patterns, running for real against real local infrastructure (not mocks).
2. One generic, registry-driven Kafka→Mongo sink that lands everything into the raw tier.
3. The **full path** for every flow: source → adapter → Kafka (Avro) → raw tier → curation → semantic tier → served identically by MCP/REST/GraphQL (per existing parity harness).
4. An honest account of where the pattern hurts (e.g., large files forced through a record-oriented bus) and what the alternatives cost.

---

## Target-State Pattern

```text
 SOURCES                      ADAPTERS (ODS Ingest)            BUS                    ODS INGEST (write side)          ODS (read side, existing)
┌──────────────────┐
│ Kafka-native app │──────────── native producer ──────┐
└──────────────────┘                                    │
┌──────────────────┐   ┌───────────────────────────┐   │   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────────────────┐
│ Legacy app,      │──▶│ CDC adapter               │──▶├──▶│ Kafka topics │──▶│ Generic sink │──▶│ Mongo        │   │ services → MCP / REST │
│ DB only          │   │ (Debezium / Postgres)     │   │   │ Avro + schema│   │ (registry-   │   │ raw tier     │   │ / GraphQL / ops MCP   │
└──────────────────┘   └───────────────────────────┘   │   │ registry     │   │ driven,      │   └──────┬───────┘   └───────────▲───────────┘
┌──────────────────┐   ┌───────────────────────────┐   │   │              │   │ idempotent)  │          │                       │
│ Legacy SaaS,     │──▶│ REST polling adapter      │──▶│   │ + DLQ topics │   └──────────────┘   ┌──────▼───────┐               │
│ REST API only    │   │ (watermark, incremental)  │   │   └──────┬───────┘                      │ Curation     │──▶ semantic ──┘
└──────────────────┘   └───────────────────────────┘   │          │       (curation consumes    │ consumers    │    tier
┌──────────────────┐   ┌───────────────────────────┐   │          └──────▶ the raw topics)      │ (per entity) │
│ Legacy system,   │──▶│ File adapter              │──▶│                                        └──────────────┘
│ flat files       │   │ (EOD batch + intraday)    │───┘
└──────────────────┘   └───────────────────────────┘
```

Key structural decisions:

- **One bus, one contract.** Every source — native or adapted — lands on Kafka topics carrying Avro records validated against a schema registry. The bus is the governance point: contract enforcement, retention (= replay window), dead-letter queues.
- **One generic sink.** A single registry-driven consumer lands every raw topic into its raw-tier Mongo collection, exactly the way `services/raw.py`, the raw REST routers, and the raw MCP tools are generated today. Adding a feed = adding a raw model + a topic mapping, not writing a new loader.
- **Curation consumes the raw *topics*, not the raw collections.** The Mongo raw tier is the durable, queryable audit/landing copy (what `bank-ods-ops` inspects and reconciles). Curation consumers subscribe to the same raw topics in their own consumer groups and upsert into the semantic tier. This keeps curation streaming (no batch re-scan of Mongo), keeps the raw collection a pure write-behind of the topic, and lets curation be replayed independently of landing.
- **The ODS serving layer does not change shape.** New raw entities register in `bank_ods/models/registry.py` and every consumer/ops surface derives automatically — that mechanism is already proven. Curation writes to the existing semantic collections.

---

## Component Boundary and Separation of Concerns

**ODS Ingest** is a sibling component to the ODS (proposed package `src/ods_ingest/`, separate deployables/containers), sharing only:

1. **The entity registry / raw-tier Pydantic models** — the landing contract. The sink validates what it writes against the same models that generate the ODS raw surface, so "what was landed" and "what is served" cannot drift.
2. **MongoDB** — Ingest is the *sanctioned writer*; the ODS transports remain strictly read-only. (Today the seed script plays this role; Ingest replaces it for fed collections.)

Hard rules, in the spirit of the existing "no query logic outside services" invariant:

| Layer | Knows about | Must never contain |
|---|---|---|
| **Adapter** (one per source) | Its source's protocol and wire format; the canonical envelope; its output Avro schema; its own watermark/offset state | Domain mapping, Mongo, anything about other sources, anything about curation |
| **Bus** (Kafka + schema registry) | Topics, schemas, compatibility rules, retention, DLQs | Business logic of any kind |
| **Generic sink** | Topic → raw-collection mapping (from the registry); idempotent upsert by the raw model's `ID_FIELD` | Per-source logic, transformation beyond envelope unwrap |
| **Curation consumer** (one per semantic entity fed) | Raw record conventions → semantic model mapping; the domain rules | Source protocols, files, HTTP — it only ever sees topic records |
| **ODS serving layer** | Unchanged | Unchanged |

The ownership risk of "same group owns both" is that domain logic leaks into adapters ("just fix the date format while we're parsing"). The rule that keeps the pattern intact: **adapters are mechanical** — they capture what the source said, verbatim, into a typed record; every judgement call lives in curation, where it is replayable and testable against landed raw data.

---

## The Wire Contract — how Avro relates to the existing ODS models

(Answering the review question directly.) The existing ODS has **no wire contract today** because it has no wire — the seed script writes Python dicts straight into Mongo. What it does have is a strong *landing* contract: the raw-tier Pydantic models are the single source of truth for what a landed record looks like, and everything (indexes, SDL, routes, tools, parity tests) derives from them.

The bus adds a second contract surface — the Avro schema on the topic — and the design question is how to stop the two from drifting. Proposal, mirroring the existing SDL-snapshot pattern:

- **`.avsc` files live in the repo** (`ods_ingest/schemas/`), one per raw topic. They are the authored wire contract, code-reviewed like the GraphQL snapshot.
- **A consistency test binds each `.avsc` to its raw-tier Pydantic model** (field names and types must correspond, the model's `ID_FIELD` must exist in the schema). Change a raw model without updating the schema — or vice versa — and CI fails, exactly like `tests/test_protection.py` does for SDL today.
- **A real schema registry runs in the prototype** (Apicurio in Confluent-compatible mode, in the compose stack). This is not optional gold-plating: **Debezium's Avro converter requires a registry** — CDC events cannot be Avro on the wire without one. The hand-built Python adapters register/resolve the same repo-authored schemas through it (`confluent-kafka` + `fastavro`).

  *As built:* Apicurio **2.6.11**, pinned to match the 2.6.2 converter the Debezium image ships. A 3.x registry issues content ids the 2.x converter writes but the ccompat API cannot resolve, and the converter must be told `use-id=contentId` — with the default the pipeline works until the first schema change and then dead-letters everything on the new version. Full account in [FINDINGS-cdc-operations.md](FINDINGS-cdc-operations.md) §1.
- **Compatibility mode: `BACKWARD`** on all raw topics (new schema can read old data → consumers upgrade first, fields can be added with defaults, never removed/retyped). Evolution governance is a first-class risk — see Risks.

So: Pydantic stays the truth for *what lands and is served*; Avro is the truth for *what travels*; a test makes them one truth.

### Canonical ingestion envelope

Transport metadata travels in **Kafka headers**, keeping the Avro payload a pure source record:

| Header | Meaning |
|---|---|
| `sourceSystem` | e.g. `MAINFRAME_CUSTODY`, `CRM_PG`, `VENDORSEC_SAAS` |
| `adapterId` / `adapterVersion` | which adapter produced it |
| `batchId` | batch/cycle identity for batch-born records (file name + business date); absent for pure streams |
| `recordSeq` | record's position within its batch (ties to control-total checks) |
| `extractedAt` | when the adapter captured it (distinct from Kafka's broker timestamp) |
| `schemaId` | registry ID (also embedded in the Confluent wire format) |

CDC is the exception: Debezium's own change-event envelope (`before`/`after`/`op`/`source`) *is* the payload, because op-type and source position are data, not transport metadata. The CDC curation consumer understands that envelope; nothing else does.

Partitioning: by the record's natural/business key (account number, security identifier) so all events for one entity are ordered within a partition. Delivery is **at-least-once everywhere**; correctness comes from idempotent upserts keyed on `ID_FIELD` (sink) and natural keys (curation) — we do not attempt exactly-once semantics.

**Topic granularity (decided):** one topic per (source, entity) — `ods.raw.crm.clients`, `ods.raw.crm.accounts`, etc. Cross-entity ordering within a source is **explicitly not guaranteed and accepted** (reviewed decision: out-of-order across entities is fine). The consequence lands on curation: it must be commutative / convergent — e.g. the CRM curation upserts the account and the embedded client-master independently, and a client event arriving before or after its account events converges to the same document. **Raw topic retention: 7 days** — the replay window for re-curation; beyond that, the Mongo raw tier is the durable audit copy and system of record for landed data.

---

## Adapter Patterns

### Pattern 0 — Native Kafka producer (the strategic case, reference only)

A modern source that produces Avro to its topic directly. Not built in this prototype (the seed script's successor could eventually be one), but the architecture must make the legacy adapters *indistinguishable from this case* downstream of the bus. That is the acceptance test for every pattern below.

### Pattern 1 — CDC adapter: legacy app that only has a database

**Prototype:** a "legacy CRM / account-master" application simulated as a local **Postgres** database (tables: `clients`, `accounts`), captured by **Debezium** (Kafka Connect + `debezium-connector-postgres`, `pgoutput` plugin) in the compose stack. A small script mutates the CRM data (new clients, KYC-status changes, account closures) to generate realistic change traffic.

**Scope of Debezium (asked at review):** Debezium is a family of *database* CDC source connectors only — Postgres, MySQL, SQL Server, Oracle, Db2, MongoDB, Cassandra, Vitess, Informix, Spanner, plus a JDBC *sink*. Its unit of work is a transaction-log record. It has **no file source connector**; files appear in Debezium only as internal state (`FileOffsetBackingStore`, file-based schema history). Debezium is therefore the right tool for Pattern 1 and is not a candidate for Pattern 3 — see "Why not Debezium or a generic file connector" there.

- **Flow:** Postgres WAL → Debezium (initial snapshot, then streaming) → `ods.raw.crm.clients` / `ods.raw.crm.accounts` → sink → new raw entities (`RawCrmClient`, `RawCrmAccount`) → curation → **`accounts`** (including the embedded `ClientMaster` sub-document — CDC updates to a client fan out to every account embedding it, which is a genuinely interesting curation case the existing model demands).
- **What it proves:** snapshot + streaming bootstrap; update and delete semantics arriving as change events; Debezium's envelope coexisting with the canonical envelope; zero application changes to the legacy system.
- **Pattern-specific concerns:** initial snapshot load on the source DB; **replication-slot WAL retention** (a stopped connector makes the source database's disk grow — an operational risk on someone else's legacy DB); source schema drift (DDL on the legacy app breaks the contract silently unless compatibility checking catches it); tombstones/deletes vs. the ODS's append-only leanings — the delete *policy* belongs to curation (see Risks); Debezium/Connect is a JVM operational estate the team must run.

### Pattern 2 — REST polling adapter: legacy SaaS with only an API

**Prototype:** a vendor **security-master SaaS**, simulated by a local stub (decided at review: no live external API). The stub is a small FastAPI app in compose backed by a **hard-coded JSON dataset** in the repo, exposing `GET /securities?updated_since=…` with paging, rate limits, and occasional 429/500s to make the adapter earn its keep — fully deterministic and hermetic for tests. The existing `raw_vendor_securities` entity — currently seeded — becomes genuinely fed by this adapter.

- **Flow:** scheduled poll with a persisted **watermark** (`updated_since`), pagination, retry/backoff → one Avro record per changed entity → `ods.raw.vendorsec.securities` → sink → `raw_vendor_securities` → curation → **`securities`** (mapping the vendor's bespoke conventions to the curated ISIN/listings model).
- **What it proves:** pull-based incremental capture converted to push-style events; watermark state management and restart correctness; dedup when the API returns overlapping windows; full backfill as just "reset the watermark".
- **Pattern-specific concerns:** polling **cannot see deletes** unless the API exposes them (usually it doesn't — mitigation: periodic full-snapshot sweep + differencing, which is expensive and must be scheduled); watermark trust (server clock skew, records updated during a page walk); rate limits vs. freshness SLA; API contract drift is discovered at runtime, not deploy time.

### Pattern 3 — File adapter: flat-file drops, EOD and intraday

**Prototype:** two feeds through one adapter framework watching a landing directory:

1. **EOD fixed-width custody position extract** — the format already faithfully modeled by `RawCustodyPosition` (copybook names, zoned decimals, overpunch signs, julian dates). A generator script produces realistic files, up to **~1M detail records** for the benchmark cycle (decided at review — large enough for a measurable bus-vs-bulk-load comparison); the adapter parses header/detail/trailer, verifies **control totals**, emits one Avro record per detail record with `batchId`/`recordSeq` headers, then a **batch-manifest event** (`ods.raw.custody.batches`: cycle date, record count, control totals, status) marking the cycle complete → sink → `raw_custody_positions` → curation → **`positions`**. The existing ops tool `reconcile_custody_feed` already traces exactly this raw→curated path — it becomes the built-in verification of the whole pipeline.
2. **Intraday cash-movements CSV** — small files arriving several times a day, same adapter framework, different parser → a new raw entity → curation → **`cash_balances`** (intraday snapshots). Proves the framework handles "many small intraday" and "one big EOD" with the same machinery.

- **What it proves:** batch semantics on a streaming bus — cycle identity, completeness signaling, control-total verification, idempotent re-delivery of the same file, quarantine of malformed records to the DLQ without failing the batch.
- **Pattern-specific concerns:** this is where the **"why not just bulk-load?"** question lives — treated fully in Risks below; also: partial-file detection (file still being written — use rename-on-complete or marker files), duplicate file delivery, out-of-order intraday vs. EOD arrival, and per-record vs. per-batch error semantics (one bad record must not poison a 2M-record cycle).

#### Why not Debezium or a generic file connector? (asked at review)

Debezium cannot do this at all — it has no file source (see Pattern 1). The connectors people reach for instead are **Kafka Connect** connectors, and none of them fit this feed:

| Option | What it is | Why it does not fit |
|---|---|---|
| `FileStreamSourceConnector` | Built into Kafka Connect | Explicitly a demo: one file, one task, line-oriented, no directory watching, no rotation or completeness handling. Not production-usable by its own documentation. |
| `kafka-connect-spooldir` | Directory-watching connector for delimited/JSON files, moves files to finished/error directories | Delimited and schema-inferred formats only; no fixed-width copybook semantics, no batch-completion concept. |
| `kafka-connect-file-pulse` | The most capable OSS file connector: directory scanning, grok/regex/row parsers, filter chains, per-file state | Closest fit, but still per-record only — the batch semantics below have to be rebuilt around it in SMTs and a sidecar. |

The feed's requirements are precisely the parts a generic connector does not have:

- fixed-width **copybook** parsing — zoned decimals, overpunch sign characters, julian dates — as faithfully modelled by `RawCustodyPosition`;
- **header/detail/trailer** structure with control totals verified across the whole file;
- a **batch-manifest event** on `ods.raw.custody.batches` emitted after the last detail record (§ Batch semantics on a streaming bus);
- `batchId` / `recordSeq` headers tying every record to its cycle and its position within it;
- per-record **DLQ quarantine** that does not fail the surrounding ~1M-record cycle.

Adopting FilePulse would cover the first bullet and leave the rest to custom Java SMTs plus a sidecar to emit manifests — more moving parts than the Python adapter, the interesting logic split between connector config and code, and a second JVM/Connect estate to operate (Risk 6) for a source that needs no Connect runtime. **Decision: hand-written Python file adapter in `src/ods_ingest/`.** It also keeps Pattern 3 genuinely distinct from Pattern 1 rather than "Connect again with a different plugin", which is part of what this prototype is meant to demonstrate.

**The staging-table anti-pattern.** There *is* a way to make Debezium consume a file: bulk-load it into a staging table and CDC that table. It should be named so it can be rejected deliberately. It buys nothing here — a Postgres round-trip, a replication slot, and a Connect JVM to reach the same topic the adapter writes directly — and it *loses* the batch and control-total semantics unless they are rebuilt anyway. It also inverts Risk 6's trade-off: the WAL-retention and DDL-drift risks are taken on for a source that had no database to begin with. The legitimate cousin of the idea is the **claim-check pattern** in Risk 1 — manifest on the bus, bulk bytes off it — which solves the volume problem without pretending a file is a database.

### Patterns catalogued but not built

Named so the architecture is judged against them; each maps cleanly onto the same adapter shape:

- **Message-queue bridge** (IBM MQ / JMS → Kafka) — near-trivial adapter; the interesting part is transactional hand-off.
- **Webhook/push receiver** — inverted control: an HTTPS endpoint the SaaS calls; adds availability and authentication obligations on *our* side.
- **SFTP / managed file transfer pull** — the file adapter with a remote landing zone; adds polling + partial-transfer detection.
- **JDBC polling** (query the legacy DB on a timer) — the fallback when CDC is impossible (no WAL access); misses deletes and intermediate states; strictly worse than CDC, sometimes the only option.
- **Kafka Connect file connectors** (`spooldir`, `file-pulse`) — the off-the-shelf alternative to Pattern 3's hand-written adapter; evaluated and rejected for this feed, with reasoning, under Pattern 3.
- **Email/report extraction, vendor SDKs, screen-scrape** — acknowledged as real, out of scope; they reduce to "something produces records → envelope → topic".

---

## Observability and Operations

The `bank-ods-ops` MCP server is the natural home for ingestion operations — it already owns raw-feed inspection and reconciliation. Additions (all read-only, per its constraints):

- `get_ingestion_status` — per source: last event landed, consumer-group lag, watermark/offset position, last completed batch.
- `get_dlq_summary` — DLQ depth per topic, newest poisoned records with error context.
- Per-flow reconciliation tools alongside `reconcile_custody_feed` (CRM→accounts, vendor→securities).
- `run_release_checks` extended with per-source **feed freshness** and **DLQ empty** checks, so the existing release-monitoring-agent pattern covers ingestion.

Every adapter emits the same structured-JSON logging as the ODS (`logging_config`), plus per-batch/per-poll summary events.

---

## Risks, Issues, and Concerns

### 1. Files: the bus tax vs. bulk loading (the headline trade-off)

For a large EOD file it is unarguably **faster and cheaper to parse and bulk-write straight into Mongo** (`insert_many`/`mongoimport`-style) than to serialize N records through Kafka and consume them back one partition at a time. What the bypass actually costs:

| Concern | Via the bus | Direct bulk load |
|---|---|---|
| Lineage / audit ("what arrived, when, from whom") | Uniform — topic is the record | Per-loader bespoke logging |
| Replay (re-curate after a mapping bug) | Re-consume the topic, no source re-pull | Re-request the file from the source, if it still exists |
| Downstream fan-out (tomorrow a second consumer wants the feed) | Subscribe — zero source changes | Build a second extract path |
| Monitoring/alerting | One set of lag/DLQ metrics | Bespoke per loader |
| Contract enforcement | Schema registry, automatic | Whatever the loader validates |
| Throughput on a 5–50M-record file | Real cost: serialize, broker I/O, consume | Fastest possible |
| Latency to queryable | Minutes at scale | Best case |

**Position:** records-through-the-bus is the default and is entirely adequate at ODS-prototype volumes (an EOD custody extract in the low millions of records is tens of minutes of single-consumer throughput at worst, parallelizable by partition). For genuinely huge files, the escape hatch that preserves the architecture is the **claim-check pattern**: the file adapter publishes a *batch-manifest event* (file reference, counts, control totals) to the bus, and the sink bulk-loads from the referenced file — governance, lineage, and triggering stay on the bus; bulk bytes stay off it. What we should **not** do is quietly bulk-load outside the bus with no manifest event — that is how the "one contract" architecture dies one exception at a time.

**Measured result:** at 1,000,000 records (191.7 MB), the bus path took a median **106 s** against **16.8 s** for a direct bulk load — **6.3× slower**, of which only 18% is producing to Kafka and 82% is contract validation and idempotent writes on the landing side. Full method, per-run spread, and the recommendation: [FINDINGS-file-ingest-benchmark.md](FINDINGS-file-ingest-benchmark.md). The conclusion is that ~90 seconds per nightly cycle is a rounding error against a batch window measured in hours, so records-on-the-bus stays the default and claim-check remains a documented, unimplemented escalation.

**Committed benchmark (decided at review):** the prototype settles this with numbers, not opinion. The same generated ~1M-record EOD file is loaded two ways —

1. **Standard pattern:** file adapter → Kafka (Avro, per-record) → generic sink → `raw_custody_positions`.
2. **One-off bulk loader:** a standalone pymongo tool that parses the same file and bulk-writes directly into the collection (`insert_many` / bulk upsert batches), bypassing the bus entirely.

Measured on identical hardware and an identically indexed empty collection: wall-clock to fully landed, records/sec, time-to-first-queryable-record, and resource notes (broker/consumer/Mongo CPU + I/O). Results, methodology, and the resulting recommendation (including where the claim-check crossover sits) are written up as a standalone findings document — **`docs/FINDINGS-file-ingest-benchmark.md`** — one of the named outcomes of this exercise.

### 2. Batch semantics on a streaming bus

Kafka has no native notion of "the batch is complete." Consumers that care about cycles (curation of EOD positions; reconciliation) need the batch-manifest convention above, and must handle: manifests arriving before the last detail records (partition interleaving), incomplete cycles (file truncated upstream), and re-sent cycles (idempotent replace-by-`batchId`, which the raw tier's loader-assigned `REC_ID = <cycle>-<seq>` convention already anticipates).

### 3. Delivery semantics and duplication

Everything is **at-least-once**: Debezium after a crash re-emits from its last committed offset; the file adapter may re-emit a batch; pollers re-fetch overlapping windows. Correctness therefore rests entirely on **idempotent writes keyed on natural identity** — the sink upserts by `ID_FIELD`, curation upserts by semantic natural keys (the compound unique indexes on `positions`/`cash_balances` already enforce this). Any new raw entity *must* define a deterministic `ID_FIELD` derivable from source content, or duplication is unresolvable. This is a registry-level design rule, not an implementation detail.

### 4. Ordering

Per-key ordering holds only within a partition of a single topic. Cross-topic order (a CRM client update vs. its account update) and cross-source order (intraday file vs. CDC event) are **not guaranteed and must not be assumed** — curation has to be commutative-or-versioned (e.g., last-write-wins on source timestamps, or as-of dating like the existing snapshot pattern). This is the subtlest correctness risk in the whole design and deserves explicit tests.

### 5. Deletes vs. an append-only ODS

CDC delivers deletes; polling mostly can't see them; files imply them by absence. The ODS semantic tier is append-only/snapshot-flavored and its transports are read-only views. **Decided: soft delete.** Source deletes become **status transitions** in the semantic tier (account `CLOSED`, security `DELISTED` — the models already carry these), with curation owning the mapping from source-delete event to status. Documents are never physically removed from the ODS; the raw tier retains the delete *event* itself for audit.

### 6. CDC operational estate

Debezium is powerful and heavy: a Connect runtime (JVM) to operate, connector config lifecycle, **replication slots that retain WAL on the *source* database when the connector stops** (a risk we impose on a legacy system's disk), snapshot load windows, and DDL drift on a database owned by someone who has never heard of us. The prototype should deliberately exercise stop/restart and a source-schema change to document the real behavior, not the brochure.

This cost is worth paying **only where the source genuinely is a database**. Debezium has no file source connector, and the workaround of staging a file into a table purely so Debezium can read it takes on every cost in this section for a source that never needed a database — rejected explicitly under Pattern 3. Confining Connect to Pattern 1 also keeps the JVM estate to one deployable in the prototype.

### 7. Schema evolution governance

The registry enforces compatibility mechanically, but *someone* owns each `.avsc` — for adapted sources that owner is the adapter team (same group), which means source-system changes arrive as *our* schema changes. The repo-authored schemas + consistency tests + `BACKWARD` compatibility give a governed path; the residual risk is upstream systems changing without telling anyone, which lands as adapter parse failures → DLQ → alert (i.e., detected, not prevented).

### 8. Raw-tier data governance

The raw tier keeps everything the source sent, verbatim, and CDC snapshots entire tables — that will include fields nobody asked for (PII in a CRM). The tier flags (`EXPOSE_RAW_TIER`) already gate consumer exposure, but landing itself is a governance decision per feed: adapters should support column/field exclusion at capture for known-toxic fields, and the doc for each feed must state what is deliberately not captured.

### 9. Prototype ≠ production (stated per project convention)

Single-broker Kafka, no bus authn/authz/encryption, no DR, hand-rolled scheduling for pollers, compose-scale sizing. The architecture is what is being proved; the hardening list (SASL/ACLs, multi-broker, Connect clustering, secrets management, schema-registry HA) belongs to the detailed plan's "productionization" section, not this prototype.

---

## Prototype Scope and Phasing

Local infrastructure via **docker compose** (now available on the dev machine): Kafka (KRaft, single broker), Kafka Connect + Debezium Postgres connector, Apicurio registry (Confluent-compat mode), Postgres 16 ("legacy CRM"), stub SaaS container. MongoDB stays as-is (standalone local or compose — verify what's currently running before adding a second instance).

| Phase | Delivers | Proves |
|---|---|---|
| **0 — Foundations** | Compose stack; topic + schema conventions; `.avsc` authoring + Pydantic-consistency test; canonical envelope; `src/ods_ingest/` skeleton | The contract machinery, before any source exists |
| **1 — File adapter + generic sink** | EOD fixed-width custody extract end-to-end into `raw_custody_positions`; curation → `positions`; batch manifests + control totals; DLQ | Full path once; batch-on-bus semantics; existing `reconcile_custody_feed` validates it |
| **2 — CDC adapter** | Postgres CRM + Debezium; snapshot + streaming into new raw entities; curation → `accounts` (embedded client-master fan-out); stop/restart + DDL-drift exercises | Change-event capture; the heaviest infrastructure pattern |
| **3 — REST adapter + intraday file** | Stub SaaS + polling adapter → `raw_vendor_securities`; curation → `securities`; intraday cash CSV → `cash_balances` | Pull-to-push conversion; watermarks; intraday cadence |
| **4 — Operations + findings** | Ops MCP ingestion tools; `run_release_checks` extension; e2e test harness per flow (source fixture → assert raw → assert curated → existing parity covers serving); **bus-vs-bulk-load benchmark** on the ~1M-record EOD file (standard Kafka path vs. one-off pymongo bulk loader) with results in `docs/FINDINGS-file-ingest-benchmark.md` | The operability story; the performance evidence and documented outcomes this exercise exists to produce |

Every phase ends green on the existing 172-test suite plus its own e2e tests. New raw entities go through the registry, per the standing constraint.

### Explicitly out of scope

Production hardening (security, HA, DR), the catalogued-but-not-built patterns (MQ, webhook, SFTP, JDBC-polling), exactly-once semantics, physical deletes, any change to the ODS serving contract.

---

## Resolved Decisions (review, 2026-07-30)

| # | Question | Decision |
|---|---|---|
| 1 | Delete policy | **Soft delete.** Source deletes become status transitions in the semantic tier (`CLOSED`, `DELISTED`); no physical removal; raw tier keeps the delete event (Risk 5) |
| 2 | Topic granularity | **One topic per (source, entity).** Cross-entity ordering within a source is not required — out-of-order across entities is accepted; curation must be commutative/convergent |
| 3 | Retention/replay window | **7 days** on raw topics; beyond that the Mongo raw tier is the system of record for landed data |
| 4 | REST source realism | **Local stub SaaS only** — simple REST API backed by hard-coded JSON data in the repo; no live external API dependency |
| 5 | Benchmark volume | **~1M-record EOD file** — large enough for a measurable performance difference between the two load paths |
| 6 | Component packaging | **`src/ods_ingest/` in this repo**, boundary enforced by import direction (ODS never imports `ods_ingest`) |
| 7 | Can Debezium ingest the files (Pattern 3)? | **No — Debezium is database-CDC only, it has no file source.** The Kafka Connect alternatives (`FileStreamSource`, `spooldir`, `file-pulse`) were evaluated and rejected: none carry copybook parsing, control totals, or batch-manifest semantics. **Hand-written Python file adapter**; the stage-file-into-Postgres-then-CDC workaround is an explicit anti-pattern (Pattern 3, Risk 6) |

Additional review outcome: the file-ingest comparison is a **committed deliverable**, not an option — the standard file→Kafka→sink pattern and a one-off pymongo bulk loader are both built, run against the same ~1M-record file, and the measured results are documented in `docs/FINDINGS-file-ingest-benchmark.md` (see Risk 1 and Phase 4).
