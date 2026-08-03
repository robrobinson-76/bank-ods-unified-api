"""Idempotent raw-tier writes.

Delivery is at-least-once, so correctness rests entirely on writing by natural
key: every document is upserted on its model's ID_FIELD. Replaying a batch,
re-dropping a file, or restarting a CDC connector therefore converges instead
of duplicating.

Documents are validated through the raw Pydantic model before they are written.
A record that fails validation is a contract violation — it goes to the DLQ
rather than into a collection the ODS will serve.
"""
from __future__ import annotations

import logging
from typing import Any

from pymongo import ReplaceOne, UpdateOne
from pymongo.errors import BulkWriteError

DUPLICATE_KEY = 11000

from ods_ingest import state, topics
from ods_ingest.bus.consumer import ConsumedRecord
from ods_ingest.sink.extractors import EXTRACTORS, ExtractError

log = logging.getLogger("ods_ingest.sink")


class ValidationFailed(ValueError):
    """A landed document does not satisfy its raw-tier model."""


def _table_for(topic_name: str) -> str:
    """CDC source table implied by the topic name (ods.raw.crm.accounts -> accounts)."""
    return topic_name.rsplit(".", 1)[-1]


def to_document(record: ConsumedRecord) -> dict:
    """Extract and validate one raw-tier document from a consumed message."""
    spec = topics.get(record.topic)
    extractor = EXTRACTORS[spec.extractor]
    doc = extractor(record, table=_table_for(record.topic))

    model = spec.model
    if model is None:
        raise ExtractError(f"{record.topic} has no raw model to land")

    try:
        # model_validate both checks the contract and drops anything the model
        # does not declare, so an upstream field addition cannot silently
        # widen a served collection.
        validated = model.model_validate(doc)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError and friends
        raise ValidationFailed(f"{model.__name__}: {exc}") from exc

    out = validated.model_dump()
    if not out.get(model.ID_FIELD):
        raise ValidationFailed(f"{model.__name__} has empty {model.ID_FIELD}")
    return out


def _write_op(model: Any, doc: dict) -> Any:
    """The write for one landed document — guarded when the model asks for it.

    Event-shaped entities key on the delivery position, so a plain upsert can
    never lose data and is used. Latest-state entities that declare an
    ORDERING_FIELD are written conditionally on that field increasing, so a
    stale record arriving after a newer one is a no-op rather than a silent
    overwrite. See BankDocument.ORDERING_FIELD.
    """
    key = {model.ID_FIELD: doc[model.ID_FIELD]}
    ordering_field = getattr(model, "ORDERING_FIELD", "")
    if not ordering_field:
        return ReplaceOne(key, doc, upsert=True)

    incoming = doc.get(ordering_field)
    if incoming is None:
        # No ordering value to compare: fall back to insert-if-absent so an
        # unstamped record can never overwrite a stamped one.
        return UpdateOne(key, {"$setOnInsert": doc}, upsert=True)

    # Applies when the document is absent OR strictly older. $setOnInsert would
    # not update an existing older document, so $set carries the whole doc and
    # the filter does the ordering work.
    return UpdateOne(
        {**key, "$or": [
            {ordering_field: {"$lt": incoming}},
            {ordering_field: {"$exists": False}},
        ]},
        {"$set": doc},
        upsert=True,
    )


def write_batch(records: list[ConsumedRecord]) -> int:
    """Land a batch of consumed records. Returns the number written.

    Records are grouped by topic so one bulk write serves each collection.
    Raises on the first invalid document so the consumer's per-record retry can
    isolate it — a poison record costs one record, not the batch.
    """
    by_collection: dict[str, list[Any]] = {}
    topics_seen: dict[str, str] = {}

    for record in records:
        spec = topics.get(record.topic)
        if spec.model is None:
            continue  # manifest topic: handled by the manifest writer
        doc = to_document(record)
        by_collection.setdefault(spec.collection, []).append(
            _write_op(spec.model, doc)
        )
        topics_seen[spec.collection] = record.topic

    if not by_collection:
        return 0

    db = state.get_db()
    written = 0
    for collection, operations in by_collection.items():
        try:
            result = db[collection].bulk_write(operations, ordered=False)
            # matched_count already covers the modified ones; adding both would
            # double-count every re-delivered record, which is precisely the
            # case this sink is built to expect.
            count = result.upserted_count + result.matched_count
        except BulkWriteError as exc:
            details = exc.details or {}
            errors = details.get("writeErrors", [])
            # A guarded write whose ordering predicate fails falls through to an
            # insert, which then collides on the natural key. That is the
            # intended outcome, not a failure: a newer record is already there
            # and this stale one must not be applied. Anything else is real.
            unexpected = [e for e in errors if e.get("code") != DUPLICATE_KEY]
            if unexpected:
                log.error("bulk write on %s had %d unexpected error(s); first: %s",
                          collection, len(unexpected), unexpected[0].get("errmsg"))
                raise
            count = details.get("nUpserted", 0) + details.get("nMatched", 0)
            log.debug("%s: %d stale record(s) skipped — newer already present",
                      collection, len(errors))
        written += count
        state.record_sink_progress(topics_seen[collection], written=count,
                                   collection=collection)
    return written


def write_manifests(records: list[ConsumedRecord]) -> int:
    """Batch manifests land in the ingest state ledger, not a raw collection.

    They are control data about a delivery, not feed records — nothing the ODS
    serves, everything ops tooling needs to answer "did the cycle close?".
    """
    for record in records:
        manifest = dict(record.value)
        state.record_batch(manifest["batchId"], manifest)
    return len(records)


def handle(records: list[ConsumedRecord]) -> int:
    """Sink entry point: route manifests and raw records appropriately."""
    manifests = [r for r in records if topics.get(r.topic).extractor == topics.EXTRACTOR_MANIFEST]
    data = [r for r in records if topics.get(r.topic).extractor != topics.EXTRACTOR_MANIFEST]
    written = 0
    if manifests:
        written += write_manifests(manifests)
    if data:
        written += write_batch(data)
    return written
