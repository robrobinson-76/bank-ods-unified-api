# Review — Strawberry vs Ariadne for the GraphQL layer

**Date:** 2026-07-09
**Status:** Evaluation complete — validated by a working side-by-side implementation
**Verdict:** The claim that Strawberry "will better support native Pydantic models" is **not borne out** for this codebase. Recommendation: **keep Ariadne**.

---

## The claim under review

The application team proposed replacing Ariadne with Strawberry for the GraphQL layer, on the grounds that Strawberry has better, native support for Pydantic models than the current solution.

This was tested by building a complete, contract-identical Strawberry implementation side by side with the existing Ariadne layer and running both through an extended parity harness.

## What was built to validate it

| Artifact | Purpose |
|---|---|
| `src/bank_ods/graphql_strawberry/` | Full Strawberry twin of `bank_ods.graphql` — same 15 queries, same snake_case names, same types. Run: `uvicorn bank_ods.graphql_strawberry:app --port 8002` |
| `tests/test_strawberry_parity.py` | 12 tests: 4-way parity (service == REST == Ariadne == Strawberry), full introspection-based schema comparison, documented behavioral differences |
| Latency benchmark | 100-run median comparison on identical queries, same event loop, same MongoDB |

Results: **all 61 tests pass** (49 original + 12 new). The introspection test proves the two schemas are **field-for-field identical** across all 18 contract types — so this is a pure cost/benefit question; there is no capability gap either way for this read-only contract.

Versions evaluated: `strawberry-graphql 0.320.4`, `ariadne 1.0.1`, `pydantic 2.13.3`, `graphql-core 3.2.8`.

---

## Findings — what "native Pydantic support" actually means in practice

### 1. The integration is officially experimental

Strawberry's Pydantic support lives in `strawberry.experimental.pydantic` — the namespace itself signals no stability guarantee. Ariadne meanwhile shipped its 1.0 stable release. Strawberry is still versioned 0.x.

### 2. `Literal[...]` fields break it — and every entity model uses them (validated)

`@strawberry.experimental.pydantic.type(model=Account, all_fields=True)` **fails at schema build** with:

```
TypeError: fields cannot be resolved. Unexpected type 'typing.Literal['DVP', 'FOP', 'RVP', 'RFP']'
```

All six entity models use `Literal` for enum-like fields (13 fields total). Consequence: `all_fields=True` — the one feature that would deliver "the model is the schema" — is unusable. Every model needs an explicit field list where every non-Literal field is declared `strawberry.auto` and every Literal field is manually overridden as `str` (see `graphql_strawberry/types.py`). **A field added to a Pydantic model does not appear in the Strawberry schema until it is also added there.**

The current Ariadne generator (`graphql/sdl.py`) maps `Literal` → `String!` automatically and picks up new fields and new entities from the `ENTITIES` registry with zero code changes. **Switching to Strawberry as documented would break the prototype's core invariant — "models are the schema; drift is structurally impossible."** (Restoring the invariant would mean writing a custom Strawberry type factory — i.e., re-creating the generator that already exists, just targeting a different API. Alternatively, converting all `Literal`s to Python `Enum`s would satisfy Strawberry but changes the shared model layer that MCP, REST, and the seed scripts also consume, and would surface GraphQL enum types instead of `String`, changing the wire contract.)

### 3. Resolvers cannot return service-layer dicts (validated)

The architecture's service layer returns JSON-safe dicts. Ariadne resolves dict keys natively, so every Ariadne resolver is one line. Strawberry raises:

```
Expected value of type 'SHE' but got: {'status': ..., 'timestamp': ...}
```

Every Strawberry resolver must therefore do `dict → Model.model_validate() → Type.from_pydantic()` — re-validating data that already came out of Pydantic-shaped services. This is boilerplate *and* a per-request tax (see §5).

### 4. The error envelope doesn't fit typed resolvers (validated)

Services return `{"error", "code"}` envelopes and never raise. Ariadne passes the envelope into field resolution (yielding `null` + a non-null-violation entry in `errors`). Strawberry's typed resolvers must intercept the envelope explicitly — return `None` for single items, raise for lists. The resulting not-found response differs: **Ariadne returns `null` + an `errors` entry; Strawberry returns a clean `null` with no errors** (`test_not_found_shape_differs`). Strawberry's shape is arguably nicer, but it is a contract change any error-inspecting client would feel.

### 5. Performance: measurable conversion overhead (validated)

Median request latency, identical query, 100 runs, in-process ASGI (no network):

| Query | Ariadne | Strawberry | Ratio |
|---|---|---|---|
| `get_transactions` limit 200 (105 rows × 20 fields) | 8.6 ms | 13.2 ms | **1.54×** |
| `get_account` single item | 1.6 ms | 1.9 ms | 1.17× |

The overhead is the double conversion in §3. At the stated load (~10 req/sec EOD peak) this is absolutely tolerable — but it buys nothing.

### 6. Code size and drift surface

| | Ariadne | Strawberry |
|---|---|---|
| Type definitions | 144 lines (`sdl.py`) — generic, registry-driven, zero per-model code | 176 lines (`types.py`) — per-model, per-field, must be maintained by hand |
| Resolvers | 57 lines (one-line delegates) | 136 lines (conversion + envelope handling) |
| App wiring | 35 lines | 41 lines |
| **Total** | **236** | **353 (+50%)** |

More importantly than raw count: the Ariadne 144 lines are written once and never touched when models change; the Strawberry 176 lines grow with every field.

### 7. Smaller contract details (validated)

- Strawberry auto-camelCases schema names (`get_account` → `getAccount`); matching the existing contract required `StrawberryConfig(auto_camel_case=False)`.
- Strawberry only emits types reachable from `Query`. `Security`/`SecurityList` (defined but not yet queried) had to be force-included via `types=[...]`. The Ariadne generator emits every registry entity automatically.
- camelCase Pydantic field names (`accountId`) pass through both libraries unchanged. ✓
- `datetime` → ISO-8601 serialization is identical in both (`DateTime` scalar). ✓
- Nested models (`Settlement.statusHistory`) convert correctly via `from_pydantic`. ✓

---

## Pros and cons

### Strawberry — genuine pros

- **Typed, code-first resolvers.** Return types are checked at schema build; IDE autocomplete and mypy/pyright understand the whole layer. Ariadne's string-SDL + untyped resolvers only fail at runtime (though the parity harness covers that here).
- **Cleaner not-found semantics** — a typed `Optional` return produces `null` without a spurious `errors` entry (§4).
- **First-class FastAPI/ASGI integration** (`strawberry.fastapi.GraphQLRouter`) with GraphiQL built in; subscriptions and DataLoader batteries included if this ever grows past a read-only facade.
- **Better fit for mutations/inputs**: `pydantic.input` types would reuse validation on the way in — irrelevant to this read-only ODS, but relevant if scope changed.
- Active, popular project with frequent releases.

### Strawberry — cons (all validated by the build)

- Pydantic integration is **experimental** by its own naming, in a 0.x library (§1).
- **Breaks on `Literal`** → no `all_fields=True` → hand-maintained field lists → **destroys the "models are the schema" invariant** this prototype exists to demonstrate (§2).
- **Can't consume the service layer's dicts** → double conversion boilerplate in all 15 resolvers (§3) and **~1.5× latency** on list-heavy queries (§5).
- ~50% more transport-layer code, all of it drift-prone (§6).
- Error-envelope handling must be reinvented per resolver; not-found shape changes for clients (§4).
- Migration touches would ripple: either `Literal`→`Enum` model rewrites (affecting MCP/REST/seeds and the wire contract) or a custom type factory (rebuilding what `sdl.py` already does).

### Ariadne — why it wins *here*

- The existing 144-line generator already delivers exactly what the team wants from "native Pydantic support": the schema is derived from the models automatically, including `Literal`, nested types, optionality, and every future entity added to the registry.
- Dict pass-through matches the service-layer contract with zero conversion cost.
- Now a stable 1.0 library.
- Its real weakness — no static typing between SDL and resolvers — is already mitigated by the parity harness, which is the project's actual contract-enforcement mechanism.

---

## Recommendation

**Keep Ariadne.** The team's claim inverts the reality for this codebase: it is the *current* solution whose schema is natively driven by the Pydantic models, while Strawberry — as officially documented — requires per-field manual declarations, breaks on `Literal`, re-validates every response, and is ~1.5× slower on list queries, all while its Pydantic integration remains experimental.

Strawberry would become the better choice only if several of these change at once: the models adopt real `Enum`s, the service layer returns Pydantic instances instead of dicts, the contract can move to idiomatic camelCase, and the API grows mutations/subscriptions where code-first typing and input validation pay for themselves. None of these apply to the current read-only ODS facade.

The side-by-side implementation (`bank_ods.graphql_strawberry`, port 8002) and its 12-test harness are kept in-tree as evidence; they can be deleted without touching anything else, or retained as a reference if the question resurfaces.

## Reproducing the evaluation

```bash
# MongoDB up + seeded, then:
pytest tests/test_strawberry_parity.py -v          # 12 tests: parity + schema + behavior
pytest tests/ -q                                   # full suite: 61 passing
uvicorn bank_ods.graphql_strawberry:app --port 8002  # live twin (GraphiQL at /graphql/)
```
