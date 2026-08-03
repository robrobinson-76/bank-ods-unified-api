# Findings — CDC operational behaviour

What the Debezium/Postgres CDC adapter actually does when you stop it, change
the source schema underneath it, or reset it. Every number below was measured
against the prototype stack (`docker-compose.ingest.yml`), not taken from
documentation.

Companion to [ARCHITECTURE-ingestion.md](ARCHITECTURE-ingestion.md) → Risk 6
("CDC operational estate"), which predicted these risks; this records what
they cost in practice.

**Stack:** Apache Kafka 4.1.0 (KRaft, single broker) · Kafka Connect from
`quay.io/debezium/connect:3.1.0.Final` · Apicurio Registry 2.6.11 (mem) ·
PostgreSQL 17 (`wal_level=logical`, `REPLICA IDENTITY FULL`) · pgoutput plugin ·
one workstation (AMD Ryzen 9 9950X, 31 GB RAM, NVMe SSD) running Windows 11 +
Docker Desktop, all infrastructure in containers.

---

## 1. The wire format took three attempts to get right

This was the most expensive single problem in the build, and it is entirely a
*version-alignment* problem — nothing to do with the data.

The requirement is that Debezium and the Python consumers share one wire
format and one registry, so a CDC record is indistinguishable downstream from a
record our own adapters produced. That means Debezium must emit the Confluent
wire format (magic byte `0x00` + 4-byte schema id + Avro body) and register into
the same registry the Python clients read.

Three distinct failures, in order:

| Attempt | Configuration | Result |
|---|---|---|
| 1 | Apicurio Registry **3.0.6** + the Apicurio **2.6.2** converter Debezium ships | Every CDC record dead-lettered: `No content with ID '34881859' was found`. The 2.x converter registers through the v2 API; the 3.x registry issues ids the ccompat API cannot resolve. |
| 2 | Registry downgraded to **2.6.11**, converter default id mode | Same failure, same id — the id is written by the converter, and its default (`contentId`) was resolving against a registry that had been replaced. Restarting Connect did not help; the id was structurally wrong, not cached. |
| 3 | Registry **2.6.11** + `apicurio.registry.use-id=contentId` + `as-confluent=true` | Works. Zero dead-letters. |

**The trap worth writing down:** with `use-id=globalId` the pipeline appears to
work perfectly — the initial snapshot decodes, every test passes — because for
the *first* schema version Apicurio's globalId and contentId happened to
coincide. The moment a second schema version was registered (see §3) the two id
spaces diverged and every record on the new schema dead-lettered with
`No content with id/hash 'contentId-9'`. A latent misconfiguration that only
surfaces on the first upstream schema change is considerably worse than one that
fails immediately.

Verified directly against the registry: for subject
`ods.raw.crm.clients-value`, ccompat reports schema id **6** for version 1 and
**48** for version 2 — those are contentIds. The globalId the converter was
writing for version 2 was **9**, which is not a valid contentId, hence the 404.

**Recommendation.** Pin the registry version to the converter version the
Debezium image ships, and set `use-id` explicitly rather than relying on the
default. Add a post-deployment check that produces a change on *each* captured
table and asserts the consumer decodes it — a smoke test that only exercises
the snapshot will not catch this.

---

## 2. A stopped connector grows the source database's disk

The risk the architecture flagged: the replication slot retains WAL on the
*legacy system's* disk while the connector is down. Measured by stopping the
connector and generating change traffic:

| State | WAL retained by `dbz_crm_slot` |
|---|---|
| Connector running, caught up | 2.7 kB |
| Stopped, after 400 changes | 139 kB |
| Stopped, after 2,400 changes | 987 kB |
| Resumed, fully drained | 25 kB |

That is roughly **420 bytes of retained WAL per changed row**. Extrapolating at
the same shape: a million changes accumulated during an outage would pin about
**420 MB** on the source database — and this is a narrow two-table CRM with
small rows. On a wide table, or with `REPLICA IDENTITY FULL` on a table with
large columns (we use FULL deliberately, see §5), the per-change cost is
materially higher.

Two properties matter operationally:

- **Retention does not release the instant the consumer catches up.** It
  releases when the connector commits its offsets and advances `restart_lsn`.
  Between "the sink has landed everything" and "the source disk is released"
  there is a lag; monitoring the sink's progress is *not* a proxy for monitoring
  the slot.
- **No data was lost.** After resuming, the account change-event log went from
  82 to 2,482 documents — all 2,400 changes captured, zero dead-lettered. Resume
  correctness is genuinely good; it is the disk that is the hazard.

**Recommendation.** Alert on `pg_replication_slots` retained bytes as a
first-class production metric, owned by whoever owns the *source* database, with
a threshold well below its free disk. A CDC connector that is down is not a
degraded pipeline — it is a slowly filling disk on a system that belongs to
somebody else. Also decide in advance the policy for a prolonged outage:
dropping the slot releases the disk but forces a re-snapshot.

---

## 3. Source DDL drift is absorbed, and the ODS does not widen

`ALTER TABLE clients ADD COLUMN relationship_mgr TEXT` executed mid-stream,
with no coordination with the pipeline.

What happened:

1. Debezium detected the change and registered **schema version 2** for
   `ods.raw.crm.clients-value` (`[1,2]` under BACKWARD compatibility).
2. Records on the new schema flowed onto the topic and decoded normally (once
   §1 was fixed).
3. The raw-tier document did **not** gain the field: `relationship_mgr` is
   absent from the landed `AFTER` image.
4. Curation continued without interruption: 2,501 events curated, 0 skipped.

Point 3 is the useful one and it is not an accident. The sink validates every
document through the raw-tier Pydantic model with `model_validate`, which drops
fields the model does not declare. So an upstream column addition is *safe by
default* — it travels on the bus, is visible in the schema registry, and stops
at the landing contract. Adopting it becomes a deliberate act: add the field to
the model, regenerate the `.avsc`, land the change through review.

**This is the schema-drift story working end to end**: the bus governs the wire
(BACKWARD compatibility, versioned subjects), and the model governs what is
served. Neither can be widened by an upstream team acting alone.

A column *removal* is the asymmetric case and was not exercised beyond
reverting the addition. Under BACKWARD compatibility a removal is the change
that breaks consumers, and the model would keep declaring a field the source no
longer sends.

---

## 4. Reset requires more than deleting the connector

`snapshot.mode: initial` means "snapshot if there are no committed offsets" —
not "snapshot on registration". Deleting and recreating the connector produced
**no events at all**, because two pieces of state survive a connector deletion:

- the connector's committed source offsets, in the Connect offsets topic, keyed
  by connector name
- the Postgres replication slot, which remembers its LSN position

A genuine reset therefore needs: stop the connector → `DELETE
/connectors/{name}/offsets` → delete the connector → `pg_drop_replication_slot`
→ re-register. That sequence is implemented as
`python scripts/register_cdc_connector.py --reset` precisely because getting it
wrong looks exactly like "the connector is broken" (silent, no events, RUNNING
status).

Once reset properly, the re-snapshot was absorbed idempotently: `EVENT_ID` is
`<LSN>-<table>-<pk>`, the sink upserts on it, and re-reading the whole topic
with a fresh consumer group left the document count unchanged
(`test_replaying_the_change_log_is_idempotent`).

---

## 5. Smaller observations

**`REPLICA IDENTITY FULL` is required for useful deletes.** Without it a delete
event carries only the primary key, and the raw change log would record that
something was deleted without recording *what*. With FULL, the `before` image is
complete and the soft-delete curation has the state it needs for audit. The cost
is larger WAL records — which feeds directly back into §2.

**Deletes are soft, by design.** A source `DELETE` becomes `status: "CLOSED"`
with a `closeDate` in the semantic tier; the document is never removed, and the
delete event itself is retained in the raw tier. Deleting a client closes every
one of that client's accounts. Both behaviours are covered by e2e tests.

**Cross-entity ordering genuinely is not guaranteed.** Clients and accounts are
separate topics, so an account can be curated before the client it belongs to
exists. The curator handles this by embedding a placeholder client snapshot and
letting the client event's fan-out complete it later, which makes the end state
independent of arrival order
(`test_curation_converges_when_the_client_arrives_after_its_accounts`).

**A curation bug requires a consumer-group reset, not a redeploy.** When the CRM
curator was first run with a defect, it consumed the backlog, skipped every
record, and committed its offsets. Fixing the code changed nothing on its own —
the events had already been acknowledged. Recovery meant re-consuming with a
fresh consumer group. This is the everyday operational consequence of the "raw
tier is the durable copy, curation replays from the topic" design, and it argues
for keeping the 7-day retention window comfortably longer than the time it takes
to notice a curation defect.

---

## What this changes about the architecture

Nothing structural — the pattern held. Three things are worth promoting from
"detail" to "explicit operational requirement":

1. **Schema-registry and converter versions are a compatibility pair.** Treat
   them as one pinned unit, and verify with a post-change smoke test rather than
   a snapshot-only test.
2. **Replication-slot retention is a source-system SLO**, not an ingestion
   metric. It belongs on the same dashboard as the legacy database's disk.
3. **Connector reset is a four-step procedure.** Script it, because the failure
   mode of doing it partially is silence.
