"""Operational state — watermarks, batch ledger, heartbeats, DLQ counters.

Lives in the `ingest_state` collection, deliberately OUTSIDE the entity
registry: it is ODS Ingest's own bookkeeping, never feed data, and no ODS
transport serves it. Ops tooling reads it (see services/ops.py) to answer
"is this feed alive and current?".

Document shape — one document per tracked thing, discriminated by `kind`:

    {_id: "watermark:vendorsec",  kind: "watermark", ...}
    {_id: "batch:<batchId>",      kind: "batch",     ...}
    {_id: "sink:<topic>",         kind: "sink",      ...}
    {_id: "dlq:<topic>",          kind: "dlq",       ...}
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import pymongo
from pymongo.collection import Collection
from pymongo.database import Database

from ods_ingest import config

KIND_WATERMARK = "watermark"
KIND_BATCH = "batch"
KIND_SINK = "sink"
KIND_DLQ = "dlq"

_client: Optional[pymongo.MongoClient] = None


def get_db() -> Database:
    """Process-wide Mongo handle for the ingest writer.

    ods_ingest writes with sync pymongo (like scripts/seed_data.py); the async
    motor client in bank_ods.db is the read path and stays untouched.
    """
    global _client
    if _client is None:
        _client = pymongo.MongoClient(config.MONGODB_URI)
    return _client[config.MONGODB_DB]


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def state_collection() -> Collection:
    return get_db()[config.INGEST_STATE_COLLECTION]


def now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── Watermarks (REST poller) ─────────────────────────────────────────────────

def get_watermark(source: str) -> Optional[str]:
    """Last successfully-consumed source timestamp, or None on first run."""
    doc = state_collection().find_one({"_id": f"watermark:{source}"})
    return doc.get("value") if doc else None


def set_watermark(source: str, value: str, *, records: int = 0) -> None:
    """Advance the watermark. Only called after deliveries are confirmed —
    advancing early would silently skip records on the next poll."""
    state_collection().update_one(
        {"_id": f"watermark:{source}"},
        {
            "$set": {"kind": KIND_WATERMARK, "source": source, "value": value,
                     "updatedAt": now()},
            "$inc": {"recordsTotal": records, "polls": 1},
        },
        upsert=True,
    )


def reset_watermark(source: str) -> None:
    """Full-resync: forget the position so the next poll re-reads everything."""
    state_collection().delete_one({"_id": f"watermark:{source}"})


# ── Batch ledger (file adapter) ──────────────────────────────────────────────

def batch_seen(batch_id: str) -> bool:
    """Has this exact file (name + content hash) already been processed?

    The idempotency guard for re-delivered files: the same bytes arriving twice
    must not double-produce.
    """
    return state_collection().count_documents({"_id": f"batch:{batch_id}"}, limit=1) > 0


def record_batch(batch_id: str, manifest: dict[str, Any]) -> None:
    state_collection().update_one(
        {"_id": f"batch:{batch_id}"},
        {"$set": {"kind": KIND_BATCH, "batchId": batch_id, "manifest": manifest,
                  "updatedAt": now()}},
        upsert=True,
    )


def get_batch(batch_id: str) -> Optional[dict]:
    return state_collection().find_one({"_id": f"batch:{batch_id}"})


def recent_batches(limit: int = 20) -> list[dict]:
    return list(
        state_collection()
        .find({"kind": KIND_BATCH})
        .sort("updatedAt", pymongo.DESCENDING)
        .limit(limit)
    )


# ── Sink heartbeats ──────────────────────────────────────────────────────────

def record_sink_progress(topic: str, *, written: int, collection: str) -> None:
    """Per-batch heartbeat — what ops tooling reads for feed freshness."""
    state_collection().update_one(
        {"_id": f"sink:{topic}"},
        {
            "$set": {"kind": KIND_SINK, "topic": topic, "collection": collection,
                     "lastLandedAt": now()},
            "$inc": {"recordsLanded": written},
        },
        upsert=True,
    )


def sink_status(topic: str) -> Optional[dict]:
    return state_collection().find_one({"_id": f"sink:{topic}"})


# ── DLQ counters ─────────────────────────────────────────────────────────────

MAX_DLQ_SAMPLES = 10


def record_dlq(topic: str, error: str, sample: dict[str, Any]) -> None:
    """Count a poisoned record and keep a bounded sample of recent failures.

    Bounded on purpose: this is a triage aid, not a second copy of the DLQ
    topic (which holds the original bytes).
    """
    state_collection().update_one(
        {"_id": f"dlq:{topic}"},
        {
            "$set": {"kind": KIND_DLQ, "topic": topic, "lastError": error,
                     "lastErrorAt": now()},
            "$inc": {"count": 1},
            "$push": {"samples": {"$each": [sample], "$slice": -MAX_DLQ_SAMPLES}},
        },
        upsert=True,
    )


def dlq_status(topic: str) -> Optional[dict]:
    return state_collection().find_one({"_id": f"dlq:{topic}"})


def clear(prefix: str = "") -> int:
    """Drop state documents; used by tests to start from a known position.

    The prefix is matched literally — batch ids contain dots, which would
    otherwise be regex wildcards.
    """
    query = {"_id": {"$regex": f"^{re.escape(prefix)}"}} if prefix else {}
    return state_collection().delete_many(query).deleted_count
