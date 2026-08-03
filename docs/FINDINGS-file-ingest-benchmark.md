# Findings — file ingestion: bus vs. direct bulk load

The committed measurement from the architecture review. The design review put
one objection at the centre of the exercise: *for a large end-of-day file it
would obviously be faster to parse it and bulk-load straight into Mongo, so
what does routing it through Kafka actually cost?*

This answers it with numbers rather than opinion, and then says what the cost
buys.

Companion to [ARCHITECTURE-ingestion.md](ARCHITECTURE-ingestion.md) → Risk 1,
which framed the trade-off and committed to measuring it.

---

## Method

One generated fixed-width EOD custody position extract — **1,000,000 detail
records, 191.7 MB** — loaded two ways, three times each, medians reported.

> **Scope — read this before quoting the 6.3×.** It is a *floor*, measured on
> one untuned, unscaled bus configuration, and two later sections revise it:
>
> - **Tuning and scaling** take it to roughly **1.5–2.2×** — six sink instances
>   are worth 2.96×, and producing to Kafka turns out to be faster than bulk
>   loading into Mongo. See *Making the bus faster* below.
> - **Path B is a blind `insert_many`**, correct for an append-style feed where
>   every record is new, and wrong for a **start-of-day true-up** that must
>   upsert-if-newer against a populated collection. That case, and the ordering
>   hazard a long-running snapshot creates, is in
>   [PATTERN-snapshot-and-stream.md](PATTERN-snapshot-and-stream.md).

| | Path A — the bus | Path B — bulk load |
|---|---|---|
| Route | file adapter → Kafka (Avro, 6 partitions) → generic sink → raw tier | parse → `pymongo.insert_many` |
| Code | `src/ods_ingest/` (the production path) | `scripts/bulk_load_custody.py` (a deliberate one-off) |
| Parser | `ods_ingest.adapters.file.fixed_width` | the same module |
| Target | `raw_custody_positions` | the same collection, same indexes |
| Validation | every document through the raw Pydantic model | none |
| Also produces | batch manifest, DLQ on bad records, lineage headers, a topic any consumer can subscribe to | nothing |

Fairness controls: the file is generated once and reused (generation took 11.9s
and is excluded from both paths); the benchmark cycle is deleted before each
run so both write into an identically-indexed collection from the same starting
state; the custody topic is recreated before each Path A run so the sink reads
exactly these records; and the bus timing stops at the **last batch actually
written**, not when the consumer finishes waiting for more — counting the
consumer's idle timeout as work would have overstated Path A by a flat 15
seconds (an early version of the harness did exactly that and reported a
misleading 40×).

Both paths landed all 1,000,000 records on every run.

**Environment:** one workstation — AMD Ryzen 9 9950X (16 cores / 32 threads),
31 GB RAM, NVMe SSD, Windows 11 + Docker Desktop.

The split matters for reading the numbers:

- **Infrastructure in containers**, sharing Docker's 12-CPU / 9.7 GB VM:
  MongoDB 7.0, single-broker Kafka 4.1 (KRaft), Apicurio registry, Connect.
- **Adapters and sinks on the host**, with the remaining ~20 threads available.

So the sink processes were **not** CPU-starved — six of them had ample cores.
MongoDB, by contrast, was a container co-tenanted with a Kafka broker inside a
12-CPU VM. That is the constraint the consume leg runs into, and it is why the
write floor below should be read as *"a containerised MongoDB sharing a VM with
a broker"*, not as MongoDB's ceiling.

Treat the absolute rates as environment-specific and the *ratios* and *shapes*
as the findings.

Reproduce with:

```bash
# the headline bulk-vs-bus comparison
python scripts/benchmark_file_ingest.py --records 1000000 --runs 3

# the tuning breakdown: producer configs, parallel sinks, consumer batch size
python scripts/benchmark_bus_tuning.py --records 1000000

# write strategies for a true-up (insert vs upsert vs update-if-newer)
python scripts/benchmark_trueup_writes.py --records 1000000
```

Two operational notes learned by running it, both now handled by the script:

- **It cleans the topic afterwards, not just the collection.** Leaving a million
  benchmark records on `ods.raw.custody.positions` is not harmless — every later
  consumer group that starts from the beginning replays them first, which turned
  the 90-second ingest test suite into a 13-minute one before this was fixed.
- **Sustained load can wedge Docker Desktop.** The engine returned
  500s after a full 3-run pass here and needed a restart. If the stack becomes
  unreachable mid-run, that is the likely cause; note that the in-memory schema
  registry starts empty afterwards and needs `python -m ods_ingest.bus.admin`
  plus a connector reset.

---

## Results

Median of 3 runs, 1,000,000 records:

| | Bulk load | Bus | Ratio |
|---|---|---|---|
| **Wall clock** | **16.8 s** | **106.0 s** | **6.3× slower** |
| Throughput | 59,492 rec/s | 9,437 rec/s | |
| Time to first queryable record | 0.32 s | 20.3 s | 64× slower |
| — of which: parse + produce to Kafka | — | 18.7 s | |
| — of which: consume + validate + write | — | 87.2 s | |

Per-run figures, showing the spread:

| Run | Bulk | Bus | Bus produce | Bus consume |
|---|---|---|---|---|
| 1 | 18.2 s | 94.1 s | 17.1 s | 77.0 s |
| 2 | 16.8 s | 106.0 s | 18.7 s | 87.2 s |
| 3 | 16.6 s | 142.3 s | 27.9 s | 114.4 s |

Bulk is stable (16.6–18.2 s); the bus is not (94–142 s). The variance is on the
bus path, and it grew across runs on a machine hosting the broker, the
consumer, and MongoDB simultaneously. Peak broker CPU sampled during a produce
phase was ~91% of one core, with the broker holding ~1.0 GB.

### Where the bus time actually goes

Only **18%** of the bus path is producing to Kafka. The remaining **82%** is the
consume side, and that is not broker latency — it is per-record work the bulk
path never does:

- Avro decode of each record
- **Pydantic validation against the raw-tier model** (the landing contract)
- upsert-by-`REC_ID` rather than a blind insert
- offset commits per batch

That distinction matters for anyone reading the 6.3× as "Kafka is slow". Kafka
moved 1M records in 18.7 s (~53,000 rec/s) while the file was still being
parsed. The cost is concentrated in the *guarantees* on the landing side —
idempotency and contract validation — most of which you would have to add back
to a bulk loader before it was safe to run twice.

---

## Making the bus faster: what each lever is actually worth

The 6.3× above is one bus configuration — a single sink, stock producer
settings, produce and consume timed sequentially. That is the *floor*, not the
architecture's capability. Measured separately
(`scripts/benchmark_bus_tuning.py`, same 1M-record file, same collection):

### Produce leg — file to Kafka

| Producer configuration | 1M records | rec/s |
|---|---|---|
| snappy | **16.4 s** | 60,903 |
| stock (lz4, linger 20 ms, 512 KB, acks=all, idempotent) | 16.7 s | 59,857 |
| no compression | 18.7 s | 53,411 |
| zstd | 19.9 s | 50,121 |
| `acks=1`, idempotence off (**unsafe** — reference only) | 20.4 s | 48,943 |
| bigger batches (linger 100 ms, 4 MB) | 23.6 s | 42,361 |

Three results worth stating plainly:

- **The produce leg is already optimal.** Nothing beats the stock settings by
  more than noise; snappy and lz4 are indistinguishable.
- **Turning compression off makes it slower** (18.7 s vs 16.7 s). The constraint
  is broker I/O, not CPU, so paying a little CPU to move fewer bytes wins.
- **Weakening the delivery guarantees buys nothing.** `acks=1` with idempotence
  disabled — the configuration people reach for when a feed is slow — was
  *slower* (20.4 s), while giving up the property that makes redelivery the
  sink's problem to absorb rather than the bus's to create. There is no trade to
  make here; the safe configuration is also the fast one.

**And the headline from this table: producing 1M records to Kafka (16.7 s) is
faster than bulk-inserting them into MongoDB (19.7 s).** Kafka is not the
bottleneck. It never was.

### Consume leg — Kafka to validated documents in Mongo

Parallel sinks are separate **processes** in one consumer group, which is how
they deploy (one pod each) and the only way to get real parallelism out of a
CPU-bound decode-and-validate path in Python.

| Sink instances | batch 500 | batch 5000 |
|---|---|---|
| 1 | 80.7 s | 79.1 s |
| 2 | 56.4 s | 50.1 s |
| 3 | 49.9 s | 40.5 s |
| 6 (= partition count) | 31.6 s | **27.2 s** |

- **Consumer scaling works: 2.96× from 1 to 6 instances.** Sublinear, and for a
  good reason — see the decomposition below.
- **Consumer batch size is worth ~14% at high parallelism** and almost nothing
  at one instance. It matters because it sets the MongoDB bulk-write size.
- The topic has 6 partitions, so 6 is the ceiling here. More partitions at
  creation time would raise it, at the cost of more consumer-group churn.

### Where the consume leg actually goes

Per-record costs measured in isolation, extrapolated to 1M:

| Stage | per 1M | share of identified work |
|---|---|---|
| Avro decode | 4.6 s | 14% |
| Pydantic validation + dump | 4.5 s | 13% |
| **MongoDB `ReplaceOne` upsert** | **24.7 s** | **73%** |

**MongoDB writes dominate — decode and validation together are under a fifth of
the identified cost.** This is why consumer scaling is sublinear: at 6 instances
the consume leg is 27.2 s against a 24.7 s MongoDB write floor, so the sinks
have essentially stopped being the constraint and are queueing behind the
database.

It also explains the residual gap. The bulk loader does `insert_many`; the sink
does `ReplaceOne(upsert)`, because at-least-once delivery makes idempotency
mandatory. **The remaining difference between a tuned bus and a bulk load is
upsert-versus-insert — a correctness requirement, not a transport cost.**

### The corrected comparison

| | 1M records |
|---|---|
| pymongo bulk write, no bus | **19.7 s** |
| bus, tuned: produce + consume **overlapped** (production shape) | **~30 s** |
| bus, tuned: produce + consume sequential (16.4 + 27.2) | 43.6 s |
| bus, untuned single sink, sequential (the original headline) | 106 s |

Tuning and scaling take the bus from **6.3× to roughly 1.5–2.2×** a direct bulk
load, depending on whether you count the legs as overlapped (they are, in a
running pipeline — the sink consumes while the adapter produces) or sequential.

### What changes on a real platform

The target deployment is OpenShift, with the sinks as pods and MongoDB on
separate servers in the same location. That differs from the test environment in
ways worth predicting explicitly, because one of them cuts against the bus.

**CPU contention was not the limiter here, so don't expect that to be the win.**
Six sink processes had ~20 host threads available; they were not starved. The
2.96× ceiling is the MongoDB write floor, and it was reached with cores to
spare. Giving the sinks their own pods does not by itself move it.

**A dedicated MongoDB should raise the floor — the one genuine upside.** Here
MongoDB shared a 12-CPU VM with a Kafka broker. Proper database servers should
absorb writes faster, which raises the ceiling on useful consumer scaling: the
2.96× measured here is a floor for that number, not a maximum.

**Remote MongoDB adds per-batch network latency — and this is the part that gets
worse, not better.** Every bulk write becomes a round trip. Batching amortises
it, but only if the batches are large:

| Consumer batch size | Round trips per 1M records | Added at 1 ms RTT | at 5 ms RTT |
|---|---|---|---|
| 500 | 2,000 | ~2 s | ~10 s |
| 5,000 | 200 | ~0.2 s | ~1 s |

So **consumer batch size matters considerably more remotely than it did here**,
where it was worth ~14%. Same-location latency is typically sub-millisecond, so
with batch 5000 the network cost is close to noise — but the setting stops being
a minor tuning knob and becomes the thing that keeps it that way.

**Both paths pay the same MongoDB tax**, so the bus-versus-bulk *ratio* should
roughly hold. A bulk loader run from a pod against the same remote database
inherits the same round trips.

**Kafka becoming remote is unlikely to matter.** The produce leg was already
faster than the bulk write, and Kafka batches aggressively by design. It has
room to absorb network latency before it becomes the constraint.

**The one thing to measure in the target environment** is the MongoDB write
floor, because it is the binding constraint and everything else follows from it:
it sets the useful number of sink replicas, and past that point more replicas
and more partitions buy nothing.

**One environment-specific hazard worth recording.** `confluent-kafka`'s Avro
path uses `fastavro`, whose codec is a compiled extension. On a machine where
application-control policy (WDAC/AppLocker) blocks unsigned native modules,
fastavro silently falls back to pure Python and decode drops from ~135k to ~54k
rec/s — **2.5× slower**. It degrades rather than fails, so it is easy to miss.
Verified in use here (`fastavro._read.__file__` ends in `.pyd`), including in
spawned sink subprocesses; worth checking on any locked-down host before
concluding the bus is slow.

---

## What the gap buys

The bulk loader is not simply "the same thing, faster". It produces less:

| | Bus | Bulk load |
|---|---|---|
| Lineage — what arrived, when, from which adapter | Uniform headers on every record | Nothing |
| Replay after a curation bug | Re-consume the topic | Re-request the file from the source, if it still exists |
| Downstream fan-out | A second consumer subscribes; source untouched | Build a second extract path |
| Contract enforcement | Schema registry + model validation, automatic | Whatever the loader happens to check |
| Bad-record handling | DLQ; the batch continues | Aborts, or silently skips |
| Idempotent re-delivery | Upsert on `REC_ID` | Duplicates (the script inserts) |
| Batch completeness signal | Manifest event; consumers know the cycle closed | Nothing |
| Monitoring | One set of lag/DLQ/freshness metrics for every feed | Bespoke per loader |

The honest framing: **~10 seconds per nightly million-record cycle once the sink
is scaled, or 90 seconds if you never tune it at all.** Either is a rounding
error against a batch window measured in hours. Spending it to get replay,
lineage, fan-out, and contract enforcement is a straightforward trade at this
volume, and stays straightforward an order of magnitude higher.

---

## When the trade stops being obvious

The bus is the default. Two properties are worth watching, because they are
what would change the answer:

**1. Time-to-queryable, not throughput.** The bus took 20.3 s to make the first
record visible versus 0.32 s for bulk — a 64× gap, far larger than the gap on
total time. Nothing here needed sub-second visibility of an EOD cycle, but a
feed with a tight "must be queryable by" deadline is constrained by this number,
not by throughput. It is also the number that improves least from tuning.

**2. The MongoDB write floor.** Consumer scaling now measured at 2.96× (1 → 6
instances), and it stops there because 6 sinks already sit at the database's
upsert throughput. Beyond that point, more partitions and more consumers buy
nothing — the next lever is the write itself (batch size, index count, sharding,
or not upserting at all), not the bus.

### The escape hatch, if it is ever needed

For a file large enough that record-by-record delivery genuinely does not fit
the window, the pattern that preserves the architecture is **claim-check**: the
adapter publishes only the *batch manifest* to the bus (file reference, counts,
control totals) and the sink bulk-loads from the referenced file. Governance,
lineage, monitoring, and triggering stay on the bus; the bulk bytes stay off it.
Everything in the table above is retained except per-record DLQ and per-record
fan-out.

This was **not** implemented or measured, and the tuning results make it look
even less necessary than before: a scaled sink lands 1M records in 27 s against
a 24.7 s MongoDB write floor, so claim-check would save the ~2 s of transport
and none of the write cost that actually dominates. Building an escape hatch
nobody needs is how a "one contract" architecture acquires its first exception.
It is documented so the option is understood, with a clear trigger: implement it
when the measured bus path, *after* scaling consumers, no longer fits the
delivery window — and note that if MongoDB is the constraint, claim-check will
not help, because it bulk-loads into the same database.

What must never happen is the third option — quietly bulk-loading outside the
bus with no manifest event. That is indistinguishable from the claim-check
pattern on a whiteboard and completely different in practice: no lineage, no
completeness signal, no monitoring, and no way for a downstream consumer to
learn the data exists.

---

## The other cost: how much code is a new file source?

Runtime was only half the objection. The other half is build effort — if every
feed needs a bespoke adapter, a one-off loader starts to look attractive on
those grounds instead. Measured from this repository (code lines, excluding
blanks, comments, and docstrings):

### Fixed cost — the framework, built once

| Component | Code lines |
|---|---|
| Bus core (envelope, producer, consumer loop, DLQ, admin, topic map, state) | 967 |
| File-adapter framework (watcher, batch identity, control totals, manifests, quarantine) | 324 |
| **Total shared** | **1,291** |

This does not grow when feeds are added.

### Marginal cost — the second file source

The custody extract was built alongside the framework, so it is not a clean
measurement. The intraday cash feed is: it arrived after the framework existed
and reused all of it, changing only the parser.

| What the cash feed needed | Code lines |
|---|---|
| Parser (`cash_csv.py`) | 34 |
| Raw-tier model | 23 |
| `TopicSpec` row | ~8 |
| **To land it on the bus** | **~65** |
| `.avsc` wire contract | 48 *(generated by script — no hand-authoring)* |
| Curation to the semantic tier | 123 *(needed in any architecture)* |
| End-to-end tests | 138 |

### The comparison that matters

The one-off bulk loader is **85 code lines** — and that is with the parser and
`REC_ID` convention already provided by the framework it was benchmarked
against. Stripping out its argparse shell and metrics, the actual glue —
connect, buffer, flush, count — is roughly **25 lines**.

So, per new file source, ignoring the parser (which both approaches need
identically) and curation (which both approaches need identically):

| | Glue per new feed | Fixed cost |
|---|---|---|
| Bus | ~31 lines (model + topic row) | 1,291 lines |
| One-off loaders | ~25 lines (+ a shared write helper, ~150) | ~150 lines |

**The marginal cost is a wash.** Roughly 25–30 lines of glue either way, with
the parser dominating both and being unavoidable in both. The bus does not get
more expensive per feed, and the loaders never get cheaper.

That means the bus's cost is **entirely front-loaded**. Spread the 1,141-line
difference in fixed cost across 24 sources and it works out at about **48 extra
lines per source** — the price of lineage, replay, DLQ, contract enforcement,
uniform monitoring, and downstream fan-out.

Two honest caveats on this measurement:

- **On line count alone the framework does not pay for itself.** At the measured
  marginal rates it would take ~60 feeds for the bus to become cheaper in raw
  lines. It is justified by the properties, not the arithmetic. What the
  arithmetic does establish is that the framework is not a *per-feed* tax —
  the "24 bespoke adapters" fear is unfounded.
- **Lines are not effort.** The dominant per-feed cost was understanding the
  source's wire format (zoned decimals, overpunch signs, julian dates), and that
  is identical either way. The largest one-time cost in this build was not code
  at all — it was three attempts at the Debezium/registry wire-format alignment
  ([FINDINGS-cdc-operations.md](FINDINGS-cdc-operations.md) §1), which a
  bulk-load architecture would have avoided entirely because it has no wire.

### Where the effort actually concentrates

Per feed, in descending order: **understanding the source format** (unavoidable),
**curation** (~120 lines, unavoidable — it is the domain mapping), **tests**
(~140 lines), then **landing glue** (~30 lines). Only the last of those is
attributable to the bus, and it is the smallest item.

The corollary for planning: budget per-feed effort against *format complexity
and domain mapping*, not against transport. A feed whose file is a clean CSV
mapping 1:1 onto an existing entity is a day; the fixed-width custody extract
with a compound curation rule is a week. The bus adds roughly nothing to either.

---

## Recommendation

1. **Keep records-on-the-bus as the default for file feeds.** Tuned, 1M records
   cost ~30 s against a batch window measured in hours, and buy the properties
   the architecture exists to provide.
2. **Do not read the gap as a Kafka tax.** Producing 1M records to Kafka (16.7 s)
   is *faster* than bulk-inserting them into MongoDB (19.7 s). Of the consume
   leg, MongoDB writes are 73% of the identified cost and Avro decode is 14%.
   The bus is not the bottleneck; the database is.
3. **Scale sink instances first — it is worth 2.96×** (1 → 6 processes, one per
   partition), and consumer batch size is worth a further ~14% at that
   parallelism. Both are configuration, not architecture.
4. **Do not weaken delivery guarantees for speed.** `acks=1` with idempotence
   disabled measured *slower* than the safe default. Leave compression on for
   the same reason — turning it off was slower, because the constraint is broker
   I/O rather than CPU.
5. **Stop scaling consumers once they reach the write floor.** At 6 instances the
   consume leg (27.2 s) is already at MongoDB's upsert throughput (24.7 s). More
   partitions past that point buy nothing; the next lever is the write itself.
6. **Track time-to-first-queryable separately from throughput** for any feed with
   a freshness deadline. It is the metric that degrades most on the bus path and
   improves least from tuning.
7. **Never bulk-load outside the bus without a manifest event.** If the bytes must
   bypass the bus, the *delivery* still has to be announced on it.
8. **On locked-down hosts, verify `fastavro` loaded its compiled codec.** An
   application-control policy that blocks native modules silently halves decode
   throughput rather than failing.
9. **Do not budget the bus as a per-feed tax.** Its cost is the ~1,300-line
   framework, paid once. Adding the second file source cost ~65 lines of
   production code; a one-off loader for the same feed would have cost about the
   same and delivered none of the guarantees.

For how this scales to dozens of sources — repository layout, deployment
granularity, and what happens when a source system's own team takes ownership of
an adapter — see [ARCHITECTURE-adapter-scale.md](ARCHITECTURE-adapter-scale.md).

---

*Raw results: `benchmark_results.json` (regenerated by the command above).*
