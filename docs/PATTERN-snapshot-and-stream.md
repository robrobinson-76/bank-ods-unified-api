# Pattern — start-of-day snapshot plus intraday stream

**Status: IMPLEMENTED.** Built in `ods_ingest/adapters/snapshot/`, with the
ordering guard in the sink and curation. Covered by 26 unit tests
(`tests/test_snapshot_diff.py`) and 12 end-to-end tests
(`tests/ingest/test_e2e_snapshot.py`).

**When to use this.** A source delivers its entire population periodically — a
vendor reference master, tens of millions of records — *and* streams changes
between deliveries. The snapshot must be applied "update if newer, insert if
absent". Ordering is carried by a monotonic per-record update timestamp.

**The two problems it solves**, in order of importance:

1. **Ordering.** Two channels feed one entity with no ordering guarantee
   between them. Without a guard, a stale snapshot record silently overwrites a
   fresher streamed update. That is data corruption, and the window widens with
   the duration of the load.
2. **Volume.** A full-population snapshot is almost entirely unchanged records.
   Applying all of them is the difference between fitting the batch window and
   not — and conditional writes at the database do *not* fix it.

---

## The shape

```text
  VENDOR                     ODS INGEST                          ODS
  ──────                     ──────────                          ───

  intraday API ──▶ REST poller ──┐
   (changes)      (watermark)    │
                                 ├──▶  ods.raw.vendorsec.securities
  start-of-day ──▶ snapshot ─────┘            (one topic)
  full extract     adapter                          │
  (40M records)    │                                ▼
                   │                        ┌──────────────┐
                   │   ONLY THE DELTA       │ generic sink │  guarded write:
                   │   reaches the bus      │              │  apply iff newer
                   │                        └──────┬───────┘
          ┌────────▼─────────┐                     ▼
          │ retained index   │              raw_vendor_securities
          │ key → (ts, hash) │                     │
          │ ~40 bytes/key    │                     ▼
          └──────────────────┘              ┌──────────────┐
                                            │  curation    │  guarded write:
          sort-merge, one linear pass       │              │  apply iff newer
          ADDED / CHANGED / REMOVED         └──────┬───────┘
                                                   ▼
                                                securities
```

Downstream of the topic, nothing can tell the two channels apart — which is the
property that keeps the rest of the architecture unchanged.

---

## Problem 1: ordering

A snapshot is the source's state at time **T**. Updates after T are strictly
newer. The load takes minutes. Throughout it, records from both channels are in
flight for the same securities, and the bus guarantees ordering only within a
partition of one topic.

```text
                      WITHOUT A GUARD                    WITH A GUARD

  t0  stream   ISIN-42 "New Name"    ─▶ applied          ─▶ applied
              (source ts 10:15)         ✓                   ✓  stored ts 10:15

  t1  snapshot ISIN-42 "Old Name"    ─▶ applied          ─▶ REJECTED
              (source ts 09:00,          ✗ CORRUPTION       ✓  09:00 < 10:15
               extracted before t0)      newer update
                                         silently lost
```

The fix is a write conditional on the source's own timestamp increasing:

```python
UpdateOne(
    {"Vendor_Ref": key, "$or": [
        {"SRC_UPDATED_AT": {"$lt": incoming}},
        {"SRC_UPDATED_AT": {"$exists": False}},
    ]},
    {"$set": doc},
    upsert=True,
)
```

**It is measurably cheaper than the unconditional write it replaces** — see
Problem 2 — so there is no throughput argument against correctness here.

### Where the guard has to go

Both tiers, because they are written by different components with different
keys. This is registry-driven: a model declares `ORDERING_FIELD` and the sink
guards its writes automatically.

| Entity shape | Natural key | Exposed? | Guard |
|---|---|---|---|
| **Event / append** — `raw_custody_positions`, `raw_crm_*_events` | embeds the delivery position (`<cycle>-<seq>`, `<lsn>-<table>-<pk>`) | **No** — every delivery is a distinct immutable document, nothing can overwrite | not needed |
| **Latest-state** — `raw_vendor_securities` | the entity's own stable id (`Vendor_Ref`) | **Yes** — a redelivery replaces the document | `ORDERING_FIELD = "SRC_UPDATED_AT"` |
| **Semantic tier** — `securities` | `securityId` | **Yes** — curation is a second writer | `vendorUpdatedAt` predicate in the curator |

### Three details that bite

**The timestamp must be comparable.** The vendor stamps `LAST_UPD_TS` in
whatever format the delivering system used — `2026-01-30 04:12:44`, `14-FEB-25`,
`02/14/2025`, `20260130`. Those do not sort against each other as strings. The
adapter normalises once at capture into `SRC_UPDATED_AT` (ISO 8601, fixed UTC
offset, so string order equals instant order) and keeps the original verbatim.

**Unorderable records must be visible as such.** A missing or unparseable
timestamp yields `None`, never `""` or `epoch` or `now()`. An empty string sorts
below every real timestamp — quietly turning "unknown" into "ancient" — and
`now()` would clobber everything. Writers see the absence and fall back to
insert-if-absent, so an unstamped record can never displace a stamped one.

**A guarded upsert collides on purpose.** When the ordering predicate fails, the
filter matches nothing and `upsert=True` attempts an *insert*, which hits the
unique key. That is the correct outcome — a newer record is already there — so
the sink treats duplicate-key errors on guarded writes as benign and lets the
rest of the batch through. Any other write error still raises.

---

## Problem 2: volume

Measured at 1M records, isolating the write strategy from transport
(`scripts/benchmark_trueup_writes.py`):

| Strategy | 1M records | vs blind insert |
|---|---|---|
| `insert_many`, empty collection | 7.94 s | 1.00× |
| `ReplaceOne(upsert)`, 0% changed — naive true-up | 17.66 s | 2.22× |
| `UpdateOne` + guard, 0% changed | 15.20 s | **1.91×** |
| `UpdateOne` + guard, 1% changed | 15.84 s | 1.99× |

**The finding that decides the design: the guard writes zero documents and still
costs 86% of what rewriting everything costs.** One index lookup per record
dominates, and you pay it whether or not a write follows.

> Conditional writes do not solve a volume problem. If 40M records arrive and
> 200K changed, you still pay 40M lookups.

The only lever with real leverage is upstream: **do not send the unchanged
records.**

### Sort-merge delta

The previous snapshot is retained as a sorted key index — key, timestamp,
content hash — roughly 40 bytes per key, so ~1.6 GB at 40M. Both sides are
consumed as sorted streams and merged in one linear pass:

```text
   previous index          incoming snapshot         outcome
   (sorted by key)         (sorted by key)
   ───────────────         ─────────────────         ───────
   VND-001  hash:a1  ◀───▶ VND-001  hash:a1          unchanged  → emit nothing
   VND-002  hash:b2  ◀───▶ VND-002  hash:XX          CHANGED    → emit record
   VND-003  hash:c3        (absent)                  REMOVED    → emit soft delete
   (absent)                VND-004  hash:d4          ADDED      → emit record

   two sequential scans · no random access · no database
```

At a 0.5% daily change rate, 40M records contain ~200K differences — **~21
seconds** through the bus at the measured 9,437 rec/s. The problem disappears
rather than being routed around.

**Absence is the delete signal.** In a full-population snapshot, a security that
vanishes has left the vendor's universe. Content hashing alone finds changed and
new records; only knowing the complete previous key set finds the disappeared
ones, which the merge yields for free. They become soft deletes (`DELISTED`),
never document removals.

That is also why the whole file is still required even though only deltas are
sent: you need the complete key set to detect absence, and the trailer to prove
the file was whole.

### Two rails that must not be removed

**Verify the file before diffing it.** A truncated snapshot reads as mass
deletion — the highest-consequence failure in the pattern. The adapter checks
the trailer's declared count against the rows parsed and refuses the file
outright if they disagree. Nothing is produced from a snapshot that is not whole.

**Advance the retained index only after delivery is confirmed.** Writing it
first would lose those changes permanently: the next snapshot would consider
them already applied. The index write is atomic (temp file plus rename), because
a half-written index would report its missing tail as deletions.

---

## What 40M costs

Extrapolated linearly from measurements at 1M. An **optimistic floor** for two
reasons: a real securities master at 40M is plausibly 30–40 GB with indexes, so
random upserts run beyond RAM and the curve bends upward; and the target
platform puts MongoDB on separate servers, adding a network round trip per bulk
write (negligible at batch 5000, material at batch 500 — see
[FINDINGS-file-ingest-benchmark.md](FINDINGS-file-ingest-benchmark.md) →
*What changes on a real platform*).

| Approach | 40M | |
|---|---|---|
| Every record through the bus, untuned single sink | ~71 min | produce ~12 min + consume ~58 min |
| Every record, **6 sink instances, batch 5000** | ~18 min | measured 2.96× consumer scaling |
| Bulk loader, update-if-newer, bypassing the bus | ~10 min | no lineage, replay, fan-out, or DLQ |
| **Sort-merge delta through the bus** | **~21 s** | plus a linear scan of the file |

Tuning the bus is real and worth doing — 71 down to 18 minutes — but it is still
an hour-scale problem being reduced to a quarter-hour one. The delta is two
orders of magnitude better than every alternative, and the only one that keeps
the architecture intact.

The tuning detail matters for a different reason: it shows *where* the cost is.
Producing 1M records to Kafka (16.7 s) is faster than bulk-inserting them into
MongoDB (19.7 s), and of the consume leg, MongoDB writes are 73% of the
identified cost against 14% for Avro decode. **The bus is not the bottleneck —
the database is** — which is also why 6 sink instances stop scaling: they have
reached the write floor. Full breakdown in
[FINDINGS-file-ingest-benchmark.md](FINDINGS-file-ingest-benchmark.md) →
*Making the bus faster*.

The corollary for this pattern: no amount of transport tuning fixes a 40M-record
true-up, because the cost is 40M writes. Only sending fewer records does.

---

## Recommendation

1. **Compute the delta in the adapter, by sort-merge.** Not in the database, not
   with conditional writes, not by claim-check. It is the only option that both
   fits the existing architecture unchanged and reduces volume by ~200×.
2. **Guard every write to a latest-state entity with more than one channel**, in
   both the raw and semantic tiers. It costs less than the unconditional write
   it replaces.
3. **Normalise the source timestamp once, at capture**, and keep the original
   verbatim. Treat unparseable as `None`, never as a sentinel.
4. **Verify the file's control totals before diffing**, and advance the retained
   index only after delivery confirms.
5. **Use log compaction for reference-master topics.** Time retention is right
   for event feeds and wrong here: with `cleanup.policy=compact` the topic *is*
   the current master, a new consumer rebuilds full state by replaying it, and
   the replay window stops applying to the entity that most needs unlimited
   replay. *(Not implemented — `TopicSpec` has no retention field yet.)*
6. **Split archival from landing at this volume.** "Keep everything as received"
   at 40M/day is ~15 billion records a year. Deltas belong in the raw tier, whole
   files in object storage, with the batch manifest tying them together — it
   already records file name, control totals, and counts.

### Ask the source first

Two questions, in order, and both are cheaper than any engineering:

**Can the source emit changes directly?** Then the snapshot becomes a
reconciliation safety net rather than the primary path, and the delta machinery
becomes belt-and-braces.

**Does the snapshot carry each record's own last-modified timestamp, or the
extraction time?** This is the one that can invalidate the design. If the file
re-stamps all 40M rows with this morning's extract time, every row appears newer
than the replica, the guard passes universally, and the true-up overwrites
fresher data on every record it touches — worse than having no guard at all.

It is a ten-minute check against a real file: find securities that demonstrably
have not changed in months and confirm their timestamp is old. A "no" means
ordering falls back to content comparison, and the stream must be trusted over
the snapshot for anything it has touched since the extract.

---

## Replication framing

Treating this as *replicating an authoritative dataset* rather than *ingesting a
feed* simplifies it and suggests the right operational metric.

There is one authoritative writer — the vendor. The two channels are delivery
paths for one source's state, not systems with competing opinions. So there is
**no merge semantics to design**: last-write-wins by source timestamp is not a
compromise, it is correct. And because the target state is defined externally,
convergence is checkable.

**The snapshot is anti-entropy.** If the stream is reliable, the true-up repairs
drift from dropped updates. That makes the useful move obvious: process it as a
reconciliation, and **treat the repair count as a health metric for the stream.**
Consistently near-zero repairs means the intraday path is healthy; a spike is
the alarm. The daily 40M file stops being something to survive and becomes the
check that proves the pipeline works.

`--dry-run` on the snapshot adapter reports exactly what a true-up would change
without touching the bus, which is how a delivery is sanity-checked before it is
trusted — a sudden spike in `REMOVED` is what a truncated file looks like.

---

## Verdict against the wider proposal

Nothing here required a change to the bus, the sink's structure, the transports,
or the tiering. Four additions, all local:

| Aspect | Outcome |
|---|---|
| One bus, one contract | **Holds** — the snapshot uses the same topic as the stream |
| Generic registry-driven sink | **Holds** — the guard is model metadata, not a special case |
| Adapters mechanical, curation judges | **Holds, and vindicated** — "is this newer?" is exactly a curation judgement |
| Idempotent upsert on natural keys | **Was insufficient** — idempotency is not ordering safety; guards added |
| Raw tier keeps everything as received | **Bends at 40M/day** — deltas landed, files archived, manifest links them |
| Time retention on all topics | **Wrong for reference masters** — compaction (not yet implemented) |
| Records-on-the-bus by default | **Holds, with delta** — the bus carries change, not volume |
| Claim-check escalation | **Not needed** — the delta beats it and detects deletions as a side effect |

### A note on the schema contract

Adding `SRC_UPDATED_AT` to the raw model was initially written as a **required**
field, and the schema registry rejected the registration outright: a required
field with no default is not BACKWARD compatible. The governance caught a
breaking change before it could reach a topic — and the fix (optional, defaulting
to null) is also the semantically correct choice, since a record with no
parseable timestamp genuinely has no ordering value.

---

*Write-strategy measurements:* `python scripts/benchmark_trueup_writes.py --records 1000000`
*Try the flow:* `python scripts/generate_securities_snapshot.py --change-rate 0.01 --add 2 --drop 1`
then `python -m ods_ingest.adapters.snapshot --once --dry-run`
