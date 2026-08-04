# Scaling to many adapters — repository, deployment, and ownership

**Status: DESIGN NOTE.** Not implemented — the prototype is deliberately a
single repository with one `ods_ingest` component. This records what changes at
a couple of dozen sources, and what the prototype already got right or wrong for
that future.

Two questions this answers:

1. *With ~24 legacy adapters, is the right pattern one repo per source — one
   repo = one deployment = one owner?*
2. *The ODS team builds an adapter, but ownership should end up with the
   application team that owns the source system, where it really belongs. Is
   that right, and how does it work?*

Short answers: **granular deployments yes, granular repositories mostly no**;
and **seed-and-hand-back is the right intent, but it must be conditional on
capability, mandate, and incentive, and the thing handed over must be far
smaller than "the pipeline for source X"** — see
[Who *should* own an adapter](#who-should-own-an-adapter--seed-and-hand-back)
for both sides of that argument.

The formulation "one repo = one deployment = one owner" welds together three
decisions that have different right answers.

---

## Three axes, decided separately

| Axis | Right granularity | Why |
|---|---|---|
| **Deployment unit** | **Per adapter** | A stuck file watcher must not block a CDC connector; feeds have different schedules, restart profiles, and blast radii. Containers make this nearly free. |
| **Repository** | **Per owning team** | Repos exist to bound *coordination*, not to enumerate sources. 24 repos across 3 teams creates 21 boundaries nobody needed. |
| **Ownership** | **Per source relationship, and it changes** | The intended direction is *towards* the application team that owns the source — but whether a given source gets there depends on that team, not on the architecture. Design so it can move; do not assume it will. |

The trap in collapsing them: because deployment *should* be granular, it feels
like the repository should be too. But deployment granularity costs almost
nothing and repository granularity costs a lot — cross-repo version bumps, N CI
pipelines, N dependency streams, and drift.

---

## What must stay central, whoever owns the adapter

This is the part that actually determines the architecture. Everything below is
what makes an adapter safely devolvable — and each row is a specific failure if
it is devolved instead.

| Component | Must stay central because | What breaks if a source team owns it |
|---|---|---|
| **Wire contracts** (`.avsc` + raw-tier models) | They are the promise the ODS makes to its consumers | The source team can change the shape of a served collection unilaterally. "One contract" is dead. |
| **The generic sink** | It defines what landing *means* — idempotency, validation, DLQ | 24 landing behaviours; the raw tier stops being uniform and reconciliation stops being comparable across feeds |
| **Curation** | It writes the semantic tier; it is ODS domain logic | ODS semantics fragment per source. A "closed account" starts meaning different things. |
| **Bus conventions** | Envelope, topic naming, retention, DLQ topology, partitioning | Consumers must special-case each feed — exactly what the bus exists to prevent |
| **Delivery semantics** (producer/consumer library) | At-least-once + idempotent upsert is a *system* property | One adapter's at-most-once bug becomes silent data loss nobody else can see |

### What is genuinely devolvable

Only one thing: **the adapter itself** — the code that speaks the source's
protocol and emits canonical records, plus its schedule, credentials, and
network path to the source.

That is a much smaller unit than "the ingestion pipeline for source X", and it
is deliberately so. The adapter is where source-specific knowledge lives, which
is exactly the knowledge the source system's team has and the ODS team does not.

**The prototype already has this boundary in the right place**, verifiable
mechanically:

```text
$ grep -r "from bank_ods" src/ods_ingest/
  topics.py            ← the contract layer (raw models)
  schemas/__init__.py  ← the contract layer
  curation/*.py        ← ODS domain logic (Account, Position, Security…)
  (adapters/**         ← ZERO matches)
```

The file and REST adapters import nothing from the ODS. They depend only on the
bus library and their own parser. They are extractable today. Curation, by
contrast, imports ODS domain models everywhere — as it should, and which is
precisely why it cannot go with the adapter.

That happened by following the "adapters are mechanical, curation makes the
judgements" rule, and it turns out to be the same boundary ownership transfer
needs. Worth preserving deliberately rather than by luck: **an adapter that
starts importing ODS models has quietly become unextractable.**

`tests/test_ingest_boundaries.py` now enforces both directions statically, in
the core suite, with no infrastructure: `bank_ods` may not import `ods_ingest`
(the read side stays independent of how data arrives), and
`ods_ingest.adapters.*` may not import `bank_ods` (adapters stay extractable).
Curation is explicitly exempt and asserted to *keep* depending on ODS models —
if it ever stopped, the domain mapping would have leaked somewhere it does not
belong.

---

## Recommended topology

```text
ods                       ODS team
  ├── read side (services, MCP/REST/GraphQL)
  ├── raw-tier models + .avsc contracts     ← governed centrally, PR-based
  └── curation                              ← ODS domain logic

ods-ingest-core           Platform team — versioned library + sink image
  ├── envelope, producer, consumer loop, DLQ
  ├── generic sink (deployed as a SERVICE, not vendored into adapters)
  └── adapter conformance test kit

ods-adapters-incubator    ODS team — where adapters are BUILT, and where the
                            ones nobody adopts continue to live (Tier 2/3)
ods-connector-configs     Platform — CDC is configuration, not code
<source-team>/their-repo  Tier 1 — the adapter moves INTO the source team's
                            existing repo, not into a new ODS-named one
```

Note what the last line implies. If a source team genuinely takes ownership, the
adapter should move into **their** repository, beside the application it reads —
that is the entire point of the change-coupling argument, and it is defeated by
parking it in an ODS-branded repo they have to visit. Which in turn means the
adapter must be a well-behaved guest: a small package with a versioned
dependency on `ods-ingest-core`, no build-system demands, and a test suite that
runs without the ODS.

The incubator repo is where adapters start, and where the Tier 2 and Tier 3 ones
stay permanently. Grouping *within* it should follow owning team where more than
one exists — eight mainframe feeds owned by one team share a copybook parser, a
landing-zone convention, and an on-call rotation, and splitting them into eight
repos buys nothing.

Three further properties worth noting:

1. **CDC "adapters" are config.** The Debezium connector in this prototype is
   ~120 lines of JSON-shaped configuration and no adapter code at all. Twenty
   CDC sources is twenty config files, which belongs in one repo with review,
   not twenty repos. It also makes CDC the *easiest* thing to hand back: a
   source team reviewing a connector config is a far smaller ask than a source
   team adopting a Python package.
2. **The sink is a service, not a library.** This matters more than it looks —
   see the upgrade problem below — and it matters most under devolution, where
   a fix that needs no adapter redeploy is the difference between shipping and
   chasing 24 teams.
3. **Repos are extracted on a trigger, not preemptively.** Start where the
   prototype is. Split when a specific team actually takes ownership, or when
   two feeds genuinely need different release cadences. Premature splitting is
   expensive and hard to reverse; late splitting is cheap because the boundary
   was maintained.

---

## Why not repo-per-source

**The shared-library upgrade problem — the strongest argument.** With 24 repos
pinned to `ods-ingest-core==1.4`, a delivery-semantics bug fix needs 24
upgrades, 24 test runs, 24 deployments, and produces a long tail of adapters
still running the broken version. In a monorepo it is one change. This is not
hypothetical: two of the bugs found while building this prototype (the consumer
closing a shared Mongo client, the sink double-counting re-delivered records)
were exactly this class — one-line fixes in shared code that every feed needed.

The mitigation shapes the design: **push as much as possible into the sink
*service* rather than the adapter *library*.** A service is upgraded once,
centrally, with no adapter redeploys. The residual library should be thin and
very stable — envelope, producer, and conformance helpers — so version skew
across 24 adapters is tolerable. This prototype already lands data in a
standalone sink process, which is the right shape.

**CI cost.** Every adapter repo that wants end-to-end tests needs Kafka, a
registry, and a database in CI. Twenty-four of those is slow and expensive. The
answer is a **conformance test kit** shipped by core: an adapter proves it emits
schema-valid records with a correct envelope against an in-memory or
single-broker harness, and the platform separately proves the sink lands
anything schema-valid. Full end-to-end runs stay in the platform repo, where
this prototype already keeps them.

**Divergence.** Twenty-four repos will acquire twenty-four logging setups,
retry policies, and dependency versions unless something prevents it. A template
repo plus the conformance kit is the minimum; observability coming *from the
core library* rather than from convention is better.

**Most sources share an owner anyway.** The premise that 24 sources implies 24
owners is usually false. Sizing the repo count to the source count optimises for
a case that mostly does not exist.

---

## Who *should* own an adapter — seed-and-hand-back

The intended model is worth stating explicitly, because it changes what "good"
looks like: **the ODS team builds the adapter, then hands ownership to the
application team that owns the source system**, on the argument that this is
where it really belongs.

That instinct is right in direction and needs preconditions to survive contact
with a real organisation. Both sides, honestly.

### The case for handing it back

1. **The people who break it should be the people who own it.** This is the
   strongest argument by some distance. When the CRM team adds a column,
   retires a code value, or changes a date format, they currently have no idea
   they have affected anything — the failure surfaces days later as somebody
   else's dead-letter queue. If they own the adapter, the source change and the
   adapter change are one commit by one team, reviewed together.
2. **Conway's Law.** The adapter is a piece of source-system knowledge wearing a
   Kafka costume. Housing it away from that knowledge guarantees a permanent
   translation cost, paid by whoever is furthest from the facts.
3. **The ODS team does not scale to 24 systems.** Central ownership makes one
   team the on-call route for two dozen applications they did not write and do
   not use. That is worse than linear: each incident starts with a team
   rebuilding context they never had.
4. **A system's data output is part of its product surface.** An application team
   that owns its API is already accountable for an interface; the feed is the
   same obligation in a different shape. "You build it, you run it" applied to
   data is the data-as-a-product / data-mesh position, and it is mainstream for
   good reasons.
5. **Incentives.** If the ODS team absorbs every messy feed, the mess is a free
   externality for the source team — there is no pressure to improve it, ever.
   Ownership internalises the cost where the fix is cheapest.

### The case against — or at least, the reasons it fails

1. **The premise cuts the other way.** A legacy adapter exists *because* the
   source cannot produce Kafka and Avro. That is usually a statement about the
   team's capability, mandate, or staffing — not just its tech stack. Expecting
   that same team to own a Kafka producer is in tension with the reason the
   adapter was needed at all.
2. **Some receiving teams do not exist in any useful sense.** Mainframe systems
   on a support contract, vendor-operated platforms, outsourced teams billing
   per change request, or a system with one part-time maintainer. Handing an
   adapter to a team like that is handing it to nobody, and the failure is
   silent until it is an incident.
3. **The hardest knowledge is not source knowledge.** The most expensive part of
   this build was not any parser — it was three attempts at Debezium/registry
   wire-format alignment, where a plausible-looking configuration worked until
   the first schema change and then dead-lettered everything
   ([FINDINGS-cdc-operations.md](FINDINGS-cdc-operations.md) §1). No source team
   should ever have to learn that, which is an argument for keeping the library
   and sink central — not for keeping the adapter central.
4. **Asymmetric incentives.** The source team receives no benefit from the ODS.
   Their reward for accepting the adapter is on-call burden for someone else's
   consumer. Without an organisational mandate this decays predictably: the
   adapter becomes the least-loved thing they own, and quietly rots.
5. **Divergence and version skew.** Twenty-four owners with no shared incentive
   to upgrade makes the shared-library problem materially worse, and quality
   bars drift apart.
6. **Debugging crosses a team boundary at the worst possible moment.** "The
   position data looks wrong" now needs two teams in the room before anyone can
   even localise the fault.

### What actually decides it

Three things, none of them technical: **capability** (can they run it?),
**mandate** (has someone with authority made this their job?), and **incentive**
(do they experience any consequence of it working?). Where all three hold,
hand it over — the arguments for are genuinely strong. Where any is missing,
transfer is a way of losing the adapter rather than placing it.

Since those vary per source, **the ownership model should be per-source, not a
global policy**:

| Tier | Code | Operations | Contract | Fits |
|---|---|---|---|---|
| **1 — Devolved** | Source team | Source team | ODS (they propose) | Active, capable, mandated teams |
| **2 — Shared** | ODS | ODS | ODS, *with the source team on the change notice* | Capable team, no mandate or appetite |
| **3 — Custodial** | ODS | ODS | ODS | Frozen, vendor, or outsourced systems |

Tier 2 is the underrated one. Most of the benefit of devolution comes from the
source team simply *knowing* their change affects a contract — the notification,
not the code ownership. That is achievable without asking them to run anything.

### Ownership is four rights, not one

The word bundles things that can and should be split:

| Right | Question | Sensible default |
|---|---|---|
| **Code** | Who merges changes? | Whoever knows the source format |
| **Operational** | Who gets paged when it stops? | Whoever can restart it and reach the source |
| **Contract** | Who may change the schema? | **Always the ODS/platform side**, by PR |
| **Roadmap** | Who decides it changes at all? | Jointly — a source change forces an adapter change |

Splitting these is usually the practical answer. A common and workable
arrangement: **source team owns code and roadmap, ODS owns operations and
contract** — they change it, we run it, nobody can break the schema
unilaterally. That captures the change-coupling benefit without requiring the
source team to hold a pager for a Kafka producer.

### The reframe: three end-states, not two

The question assumes ownership must land somewhere permanently. There are
actually three destinations, and the architecture should support all of them:

1. **ODS keeps it** — the source is frozen and will outlive nobody's patience.
   Custodial ownership, indefinitely.
2. **The source team takes it** — capability, mandate, and incentive all present.
3. **It gets deleted** — the source modernises and produces to Kafka natively.

The third is the one worth naming, because it changes what an adapter *is*. If
the goal is for the source to eventually speak the bus directly (Pattern 0 in
[ARCHITECTURE-ingestion.md](ARCHITECTURE-ingestion.md)), then the adapter is
**transitional scaffolding with an exit**, not a permanent asset in search of an
owner. "We built you an adapter; here is the contract it satisfies; when you can
produce these records yourself, we delete it" is a far easier conversation than
"please adopt this component", and it ends with less software rather than more.

The measurements support treating adapters as disposable: ~65 lines and no
dependency on the ODS. Cheap to transfer *and* cheap to throw away.

### Building for transfer changes how you build

If an adapter may be handed over, that is a design constraint from day one, and
it is one that improves the code regardless of whether transfer happens:

- **Boring and self-contained.** No clever shared internals, no reaching into
  ODS models — already enforced by `tests/test_ingest_boundaries.py`.
- **A conformance kit, not a code review.** The receiving team needs to answer
  "is my adapter still correct?" with a command, not by asking the ODS team.
  This is the single most important artefact to build *before* the first
  handover.
- **A runbook, not tribal knowledge.** Where files land, what a stuck watermark
  looks like, how to replay — written down, because the person who knew is now
  in a different org.
- **A contract that is genuinely readable.** The `.avsc` plus the raw-model
  docstring is the interface. It must stand alone.
- **Small enough to hold in one head.** The measured ~65 lines is the point.
  A team can adopt 65 lines and a config file; nobody adopts 1,300 lines of bus
  machinery, which is exactly why that stays central.

### Recommendation

Build centrally, design for transfer, transfer selectively, and prefer deletion
to transfer where the source can be modernised.

Concretely: default new adapters to **Tier 2** (ODS runs it, source team is on
the contract-change notice). Promote to **Tier 1** only where capability,
mandate, and incentive are all demonstrably present — and treat the first such
handover as the forcing function for building the conformance kit and runbook,
because those are what make every subsequent one cheap. Accept that some sources
stay **Tier 3** permanently, and do not treat that as failure.

What must not happen is transfer as a *disposal* strategy — handing an adapter
to a team without capability or mandate in order to move it off the ODS backlog.
That does not relocate the work; it relocates the failure, and the ODS still
owns the consequence because it is the ODS's data that goes stale.

---

## When a source team does take ownership

What transfers, and what conspicuously does not:

**Handed over:** the adapter package, its deployment manifest and schedule, its
credentials/network path to the source, a versioned dependency on
`ods-ingest-core`, and the conformance test suite it must keep passing.

**Not handed over:** the wire contract (they propose changes by PR against the
contracts repo — the schema registry's BACKWARD compatibility is the runtime
backstop, not the governance), the sink, curation, or the raw-tier models.

**Also needed, and easy to forget:**

- **A write ACL scoped to their topics only.** An adapter identity that can write
  `ods.raw.custody.*` must not be able to write `ods.raw.crm.*`. With one team
  this never comes up; with a dozen external owners it is the security model.
- **Alert routing that matches the split.** "Feed is late" pages the adapter
  owner; "curation is failing" or "DLQ is filling with validation errors" pages
  the ODS team. The prototype's ops tools already separate these signals —
  `get_ingestion_status` (did it arrive?) versus `get_dlq_summary` (was it
  rejected?) — and that distinction becomes an on-call routing rule.
- **A contract change process with teeth.** BACKWARD compatibility stops a
  breaking schema; it does not stop a well-meaning source team adding a field
  the ODS never adopts. That is fine and already handled — the sink's model
  validation drops undeclared fields — but the *process* for adopting a new
  field should be written down, because the default outcome is silence.

---

## What else is easy to miss

**The long tail is a power law.** Of 24 sources, perhaps 4 are high-volume and
business-critical and 20 are a weekly CSV of a few hundred rows. The framework
must make the tail genuinely trivial (measured here: ~65 lines for a new file
feed), because if a small feed is expensive, someone will bypass the bus for it
— and that is how the "one contract" architecture acquires its first exception.
The line-count measurement in
[FINDINGS-file-ingest-benchmark.md](FINDINGS-file-ingest-benchmark.md) exists
partly to prove the tail is cheap.

**Bus resources multiply.** Twenty-four sources means topics, partitions,
consumer groups, registry subjects, DLQ topics, and ACL entries multiplying by
roughly the same factor. Partition counts chosen per feed (6 for custody, 3 for
the rest here) need a sizing convention, or the cluster acquires thousands of
partitions nobody planned.

**Envelope versioning.** A change to the canonical header set touches every
adapter. Version the envelope explicitly and make consumers tolerant of both,
or accept a flag-day across 24 deployables.

**Curation is the real scaling risk, not adapters.** Adapters are mechanical and
cheap. Curation is domain logic, and 24 sources feeding 6 semantic entities
means multiple curators writing the same collections with different rules — the
account master fed by CRM *and* by a custody file, say. That is a semantic
conflict-resolution problem (last-write-wins? source precedence? per-field
ownership?) that no amount of repository structure solves, and it is where the
genuine architectural difficulty lives at scale. This prototype has one writer
per semantic entity and therefore never had to confront it.

**Not every source deserves an adapter.** Some "sources" are one-off migrations
or a report someone emails. A standing adapter implies a standing operational
commitment — monitoring, on-call, upgrades. A decommissioning path matters as
much as an onboarding one.

---

## Summary

| Question | Answer |
|---|---|
| One deployment per adapter? | **Yes** — cheap, and isolates failure and schedule |
| One repo per source? | **No** — group by owning team; most sources share owners |
| One owner per source? | **Per source, negotiated** — design for transfer, don't assume it |
| Should adapters go back to the application owners? | **Yes as intent** — they cause the breakage, so they should own it. But conditional on capability, mandate, and incentive; absent any of those it is disposal, not delegation |
| Where does a devolved adapter live? | In the **source team's own repo**, next to the application — an ODS-branded repo they must visit defeats the purpose |
| What must stay central? | Contracts, sink, curation, bus conventions, delivery semantics — regardless of who owns the adapter |
| What can be devolved? | The adapter only (~65 lines, zero ODS imports — already dependency-clean and enforced by a test) |
| Is ownership all-or-nothing? | **No** — code, operational, contract, and roadmap rights split separately. "They change it, we run it, nobody breaks the schema alone" is often the practical landing zone |
| Best end-state for an adapter? | **Deletion** — the source produces to the bus natively. Treat adapters as scaffolding with an exit, not assets needing an owner |
| Biggest poly-repo risk? | Shared-library upgrades across N repos; mitigate by keeping logic in the sink *service*. Devolution makes this materially worse |
| Biggest scaling risk overall? | Not repos, not ownership — **multiple curators writing the same semantic entity** |
| When to split? | On a real ownership transfer or cadence conflict; never preemptively |
