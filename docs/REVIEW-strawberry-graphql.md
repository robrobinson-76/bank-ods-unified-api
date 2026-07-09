# Review — GraphQL library choice: Ariadne vs Strawberry vs Graphene

**Date:** 2026-07-09 (Graphene evaluation and library-landscape facts added same day)
**Status:** Evaluation complete — validated by working side-by-side implementations of **both** alternatives
**Verdict:** The claim that Strawberry "will better support native Pydantic models" is **not borne out** for this codebase, and Graphene — the other obvious candidate — is **effectively dormant**. Recommendation: **keep Ariadne**.

---

## The claim under review

The application team proposed replacing Ariadne with Strawberry for the GraphQL layer, on the grounds that Strawberry has better, native support for Pydantic models than the current solution. Graphene was subsequently evaluated as the third mainstream option.

Both alternatives were tested by building complete, contract-identical implementations side by side with the existing Ariadne layer and running all three through an extended parity harness.

## Library landscape — versions verified against PyPI on 2026-07-09

**This table is key context: two of the three candidate stacks have not been updated in a long time.**

| Package | Latest version | Last release | Maintenance signal |
|---|---|---|---|
| `ariadne` (current solution) | **1.0.1** | active | Stable 1.0 major release shipped; actively maintained |
| `strawberry-graphql` | **0.320.4** | active (multiple releases/month) | Never left 0.x — **no v1 or v2 exists**; Pydantic integration explicitly in `strawberry.experimental` namespace |
| `graphene` | 3.4.3 | **December 2024 — ~19 months ago** | Effectively dormant; predates modern typing idioms |
| `graphene-pydantic` (required for Pydantic support) | 0.6.1 | **February 2024 — ~2.5 years ago** | Dormant third-party bridge, still 0.x |

Two common misconceptions this table settles:

- **There is no "Strawberry v2" with more capabilities.** Strawberry has never shipped 1.0; the high minor number (0.320) reflects release frequency, not maturity guarantees. This evaluation used the newest release that exists.
- **Graphene is the oldest and historically most popular library, but its cadence has stalled** — no core release in ~19 months, and the Pydantic bridge it depends on has been untouched for ~2.5 years. Betting a new transport layer on packages this quiet is a maintenance risk regardless of technical fit.

## Other contenders — considered but not built, and why

For completeness ("did you look at X?"), the rest of the field, with release dates verified against PyPI on 2026-07-09:

| Contender | Status | Why no side-by-side twin was built |
|---|---|---|
| `tartiflette` (Dailymotion) | Last release **Nov 2021** — abandoned | Was the fourth major Python option; four-plus years dead. Nothing to evaluate |
| `magql` (pallets-eco) | 1.1.1, Aug 2024 | Niche graphql-core wrapper, minimal adoption, SQLAlchemy-oriented; ~2 years quiet |
| `graphql-server`, `aiohttp-graphql`, `flask-graphql` | Dormant | Serving shims for Graphene, inheriting its dormancy |
| `graphql-core` used directly | Active — it underpins all three evaluated libraries | Viable "no framework" option, but Ariadne *is* graphql-core plus the executable-schema and ASGI conveniences we'd otherwise hand-write. Strictly less than the incumbent, so nothing new to learn from building it |
| Node.js stacks (Apollo Server, GraphQL Yoga, Pothos) | Very active — the most mature GraphQL ecosystem | Architecturally disqualified: requires either duplicating Mongo query logic in a second language or having Node call the Python services over HTTP. Both break the prototype's invariants (one service layer, one Mongo access point, Pydantic as single schema source) and remove the GraphQL layer from the in-process parity harness |
| Auto-GraphQL gateways (Hasura, WunderGraph, Grafbase) | Active products | Generate GraphQL directly over the database — query logic would live outside `bank_ods.services.*` (explicitly forbidden), losing the shared pagination/error/date semantics. Mostly Postgres-first; Mongo via connectors of varying maturity |

Related datapoint: **MongoDB launched and then retired its own native Atlas GraphQL API** (deprecated 2023, end-of-life March 2025), directing customers to third-party solutions. The vendor exiting the auto-generated-GraphQL-over-Mongo category supports the choice this prototype makes: own the GraphQL layer inside your own service tier.

## What was built to validate it

| Artifact | Purpose |
|---|---|
| `src/bank_ods/graphql_strawberry/` | Strawberry twin — same 15 queries, snake_case names, identical types. `uvicorn bank_ods.graphql_strawberry:app --port 8002` |
| `src/bank_ods/graphql_graphene/` | Graphene twin — same contract. `uvicorn bank_ods.graphql_graphene:app --port 8003` |
| `tests/test_strawberry_parity.py` | 12 tests: 4-way parity (service == REST == Ariadne == Strawberry), introspection-based schema comparison, behavioral diffs |
| `tests/test_graphene_parity.py` | 12 tests: same harness against the Graphene twin |
| Latency benchmark | 100-run medians on identical queries, same event loop, same MongoDB |

Results: **all 73 tests pass** (49 original + 24 evaluation). Introspection tests prove all three schemas are **field-for-field identical** across the 18 contract types — this is a pure cost/benefit comparison; no capability gap exists for this read-only contract.

Versions evaluated: `strawberry-graphql 0.320.4`, `graphene 3.4.3` + `graphene-pydantic 0.6.1`, `ariadne 1.0.1`, `pydantic 2.13.3`, `graphql-core 3.2.8`.

---

## Findings — Strawberry

### 1. The integration is officially experimental

Strawberry's Pydantic support lives in `strawberry.experimental.pydantic` — the namespace itself signals no stability guarantee, and the library as a whole is still 0.x. Ariadne meanwhile shipped its 1.0 stable release.

### 2. `Literal[...]` fields break it — and every entity model uses them (validated)

`@strawberry.experimental.pydantic.type(model=Account, all_fields=True)` **fails at schema build** with:

```
TypeError: fields cannot be resolved. Unexpected type 'typing.Literal['DVP', 'FOP', 'RVP', 'RFP']'
```

All six entity models use `Literal` for enum-like fields (13 fields total). Consequence: `all_fields=True` — the one feature that would deliver "the model is the schema" — is unusable. Every model needs an explicit field list where every non-Literal field is declared `strawberry.auto` and every Literal field is manually overridden as `str` (see `graphql_strawberry/types.py`). **A field added to a Pydantic model does not appear in the Strawberry schema until it is also added there.**

The current Ariadne generator (`graphql/sdl.py`) maps `Literal` → `String!` automatically and picks up new fields and new entities from the `ENTITIES` registry with zero code changes. **Switching to Strawberry as documented would break the prototype's core invariant — "models are the schema; drift is structurally impossible."**

### 3. Resolvers cannot return service-layer dicts (validated)

The service layer returns JSON-safe dicts. Ariadne resolves dict keys natively, so every Ariadne resolver is one line. Strawberry raises `Expected value of type '...' but got: {...}`. Every Strawberry resolver must do `dict → Model.model_validate() → Type.from_pydantic()` — a double conversion per request (see benchmark).

### 4. The error envelope doesn't fit typed resolvers (validated)

Services return `{"error", "code"}` envelopes and never raise. Strawberry's typed resolvers must intercept the envelope explicitly. The resulting not-found response differs: **Ariadne returns `null` + a non-null-violation entry in `errors`; Strawberry returns a clean `null` with no errors** (`test_not_found_shape_differs`). Arguably nicer, but a contract change any error-inspecting client would feel.

---

## Findings — Graphene

### 1. Dormant maintenance is the headline risk (verified)

Graphene core: last release **December 2024**. graphene-pydantic (mandatory for the team's "native Pydantic" criterion): last release **February 2024**, still 0.x, declaring support for `graphene>=2.1.8` — a constraint written before graphene 3 idioms settled. Graphene also ships **no ASGI integration and no GraphiQL** — the twin's `/graphql` endpoint had to be hand-rolled around `schema.execute_async()` (`graphql_graphene/app.py`), because the third-party ASGI bridges (e.g. starlette-graphene3) are as dormant as the bridge itself.

### 2. Technically it fits *better* than Strawberry (validated — a surprise)

- **`Literal` works.** `PydanticObjectType` maps every model field automatically, including `Literal` → `String!`. No per-field declarations needed — `graphql_graphene/types.py` is 77 lines vs Strawberry's 176.
- **Performance is at parity with Ariadne** (1.03× — see benchmark). The single `model_validate()` pass is cheap; it is Strawberry's *second* conversion (`from_pydantic` building a parallel object graph) that costs.

### 3. But the same structural taxes apply (validated)

- **Plain dicts are rejected** — `PydanticObjectType` installs an `is_type_of` check requiring Pydantic model instances, so every resolver still re-validates (`Expected value of type 'Settlement' but got: {...}`).
- **The Query root is declared twice** — each field needs a `graphene.Field(...)` declaration *and* a `resolve_*` method with manually repeated argument lists (`graphql_graphene/resolvers.py`, 159 lines).
- **Registry-driven schema generation is lost**, same as Strawberry: unreferenced entities need `types=[...]` force-inclusion, and one contract deviation had to be patched by hand (`statusHistory` needed an explicit override to produce `[StatusHistoryEntry!]!`).
- **Not-found shape changes** identically to Strawberry: clean `null`, no `errors` entry (`test_gr_not_found_shape`).

---

## Benchmark — all three layers, same queries, same DB

Median request latency, 100 runs, in-process ASGI (no network). List query: `get_transactions` limit 200 → 105 rows × 20 fields.

| Layer | List query | vs Ariadne | Single item | vs Ariadne |
|---|---|---|---|---|
| Ariadne (dict pass-through) | 7.7 ms | 1.00× | 1.60 ms | 1.00× |
| Graphene (`model_validate` only) | 7.9 ms | **1.03×** | 1.63 ms | 1.02× |
| Strawberry (`model_validate` + `from_pydantic`) | 12.9 ms | **1.68×** | 1.91 ms | 1.19× |

At the stated load (~10 req/sec EOD peak) even Strawberry's overhead is tolerable in absolute terms — but it buys nothing.

## Code size and drift surface

| | Ariadne | Strawberry | Graphene |
|---|---|---|---|
| Type definitions | 144 (registry-driven, write-once) | 176 (per-field, hand-maintained) | 77 (auto-mapped, but overrides needed) |
| Resolvers / Query root | 57 (one-line delegates) | 136 (conversion + envelope) | 159 (double declaration + conversion) |
| App wiring | 35 | 41 | 50 (hand-rolled endpoint, no GraphiQL) |
| **Total** | **236** | **353** | **288** |

Raw counts understate the difference: Ariadne's 144-line generator is never touched when models change; both alternatives accumulate hand-written declarations that must track the models forever.

---

## Pros and cons

### Strawberry

**Pros:** typed code-first resolvers checked at schema build; IDE/mypy support; cleaner not-found nulls; first-class FastAPI/ASGI integration with GraphiQL; DataLoader and subscriptions built in; `pydantic.input` types would reuse validation for mutations; very active project.

**Cons (all validated):** Pydantic integration experimental in a 0.x library; breaks on `Literal` → hand-maintained field lists → destroys the "models are the schema" invariant; can't consume service dicts → double conversion → 1.68× list-query latency; +50% transport code; error envelope reinvented per resolver; migration ripples (either `Literal`→`Enum` model rewrites affecting MCP/REST/seeds, or a custom type factory rebuilding what `sdl.py` already does).

### Graphene

**Pros (validated):** `PydanticObjectType` genuinely maps all fields including `Literal` — the most complete Pydantic auto-mapping of the three; performance at parity with Ariadne; smallest hand-written type file; huge historical ecosystem (graphene-django, Relay support).

**Cons:** **core dormant since Dec 2024 and the required Pydantic bridge since Feb 2024 — this is the decisive issue**; no ASGI integration or GraphiQL (hand-rolled endpoint, or another stale third-party bridge); rejects service dicts just like Strawberry; verbose double declaration of every query field; pre-typing API design; registry-driven schema generation lost; adopting it would move the prototype from a stable 1.0 library onto two quiet dependencies to gain nothing measurable.

### Ariadne — why it wins *here*

- The existing 144-line generator already delivers exactly what the team wants from "native Pydantic support": schema derived from the models automatically — `Literal`, nested types, optionality, and every future registry entity included.
- Dict pass-through matches the service-layer contract with zero conversion cost — fastest of the three.
- Stable 1.0, actively maintained — the only candidate stack with a live release cadence across every package it needs.
- Its real weakness — no static typing between SDL and resolvers — is already mitigated by the parity harness, the project's actual contract-enforcement mechanism.

---

## Recommendation

**Keep Ariadne.** Ranking for this codebase: **Ariadne > Strawberry > Graphene.**

- The Strawberry claim inverts reality: the *current* solution is the one whose schema is natively driven by the Pydantic models; Strawberry (latest available — no v2 exists) requires manual per-field declarations, breaks on `Literal`, re-validates every response, and is ~1.7× slower on list queries, with its Pydantic integration still experimental.
- Graphene is the better technical fit of the two alternatives (full `Literal` support, parity-level performance) but **its stack has not been updated in a long time** — core ~19 months, Pydantic bridge ~2.5 years — and it still loses the registry-driven invariant while adding hand-rolled serving code. Dormant dependencies are the wrong foundation for a new transport layer.

Both twins (`ports 8002/8003`) and their 24-test harness are kept in-tree as evidence; each can be deleted without touching anything else.

---

## Fairness notes and open questions for the application team

Points that cut against the recommendation, or that could change it — stated here so they are addressed head-on rather than omitted:

1. **Community momentum favors Strawberry.** This review uses maintenance cadence to disqualify Graphene; the same lens applied to Ariadne shows a stable, active 1.0 library — but one maintained primarily by a single company (Mirumee) with a smaller contributor base than Strawberry, which has the largest community and release velocity of the three. If the deciding question is "which library is most certain to be healthy in five years," Strawberry has the strongest claim. The counterweights: Ariadne's surface area in this codebase is tiny (236 lines, mostly our own generator), graphql-core does the heavy lifting in all cases, and a future migration would be cheap — the parity harness built for this evaluation *is* the migration safety net.
2. **What is the team's actual roadmap?** If the request for Strawberry is really driven by unstated plans — **mutations with validated input types**, **subscriptions** (e.g., pushing settlement-status changes), or **Apollo Federation** into an enterprise gateway — the calculus changes: Strawberry has first-class support for all three; Ariadne supports them less ergonomically; Graphene effectively not at all. Today's read-only ODS constraint makes these moot, but the team should be asked directly before this review is treated as final.
3. **Pydantic-version coupling.** Strawberry's experimental integration and graphene-pydantic both reach into Pydantic v2 internals. When Pydantic 3 arrives, the experimental namespace carries no stability promise and the dormant bridge will almost certainly break. Ariadne's only Pydantic coupling is our own 144-line generator, which we control and can fix in an afternoon. This asymmetry strengthens the recommendation.
4. **Not-found error shape should be a decision, not an accident.** Ariadne's current `null` + non-null-violation entry in `errors` is a leak of the service error envelope, not a designed contract; both twins return a clean `null`. Whichever library stays, the team should pick the canonical behavior and pin it with a test — clients may already depend on the current shape.

---

## Design gap addressed: query protection

The evaluation surfaced a gap that belongs to the *design*, not to any library: none of the layers limited query cost, and introspection was unconditionally open — while the K8s deployment targets 40–50K requests/day from real clients. This has now been **engineered into the Ariadne layer** (`graphql/protection.py`), proving no product swap is needed to close it:

| Protection | Mechanism | Default |
|---|---|---|
| Query depth limit | graphql-core `ValidationRule` — rejects at validation, before any resolver or MongoDB call; follows fragments | `GRAPHQL_MAX_DEPTH=10` |
| Root-field / alias cap | Same mechanism; blocks alias amplification (`{ a1: get_settlement_fails(...) ... a200: ... }` = 200 Mongo queries from one request) — the realistic attack vector for this flat schema | `GRAPHQL_MAX_ROOT_FIELDS=10` |
| Introspection kill-switch | graphql-core's built-in `NoSchemaIntrospectionCustomRule` | `GRAPHQL_INTROSPECTION=true` (set `false` in production) |
| Schema-drift guard | `tests/test_protection.py::test_schema_matches_snapshot` — generated SDL must match the checked-in `tests/schema.snapshot.graphql`, so any contract change appears as a reviewable diff in the PR that caused it | always on |

Covered by 9 tests in `tests/test_protection.py` (validation-rule units, live alias-amplification rejection through the app, introspection default). Verified manually that `GRAPHQL_INTROSPECTION=false` blocks `__schema` while normal queries pass.

Deliberately out of scope for the prototype, but required before production exposure: rate limiting at the ingress/gateway, per-field cost analysis (depth × breadth weighting) if the schema ever gains nested relations, persisted-query allow-lists for known clients, and disabling GraphiQL/playground UIs in production. Note for fairness: Strawberry ships `QueryDepthLimiter`/`MaxTokensLimiter` extensions out of the box — the one place its batteries-included approach genuinely leads; the equivalent here cost ~90 lines against graphql-core, and identical rules would drop into the other twins unchanged since all three execute through graphql-core validation.

## Reproducing the evaluation

```bash
# MongoDB up + seeded, then:
pytest tests/test_strawberry_parity.py tests/test_graphene_parity.py -v   # 24 evaluation tests
pytest tests/ -q                                                          # full suite: 73 passing
uvicorn bank_ods.graphql:app --port 8001              # Ariadne (current)
uvicorn bank_ods.graphql_strawberry:app --port 8002   # Strawberry twin (GraphiQL at /graphql/)
uvicorn bank_ods.graphql_graphene:app --port 8003     # Graphene twin (no GraphiQL — none ships)
```
