"""Turning a Kafka message into a raw-tier document.

Two shapes reach the sink:

  canonical — the payload IS the raw record; the adapter already produced it in
              the model's shape, so there is nothing to do.
  debezium  — the payload is a change envelope (op/before/after/source). The
              record we land is the CHANGE, not the row: an append-only event
              log keyed by a deterministic EVENT_ID so connector restarts and
              snapshot re-runs upsert rather than duplicate.

Unwrapping is the only transformation the sink is permitted to do. Anything
that interprets a value belongs in curation.
"""
from __future__ import annotations

from typing import Any, Optional

from ods_ingest.bus.consumer import ConsumedRecord


class ExtractError(ValueError):
    """The message does not have the shape this extractor requires."""


def canonical(record: ConsumedRecord, *, table: str = "") -> dict:
    return dict(record.value)


def _state_of(image: Optional[dict[str, Any]]) -> Optional[dict]:
    """Normalize a Debezium row image to plain strings.

    Raw-tier convention is wire-format values verbatim as strings; Debezium
    delivers typed values (ints, timestamps), so they are stringified here
    without interpretation. None stays None — the absence of a `before` image
    on an insert is meaningful.
    """
    if image is None:
        return None
    return {k: (None if v is None else str(v)) for k, v in image.items()}


def debezium(record: ConsumedRecord, *, table: str) -> dict:
    """Unwrap a Debezium change event into a raw change-event document."""
    payload = record.value
    if not isinstance(payload, dict) or "op" not in payload:
        raise ExtractError(f"not a Debezium envelope: keys={sorted(payload)[:8]}")

    op = payload["op"]
    before = _state_of(payload.get("before"))
    after = _state_of(payload.get("after"))
    source = payload.get("source") or {}

    # A delete carries only a `before` image; everything else carries `after`.
    image = after if after is not None else before
    if image is None:
        raise ExtractError("change event has neither a before nor an after image")

    pk_field = PK_FIELDS[table]
    pk = image.get(pk_field)
    if pk is None:
        raise ExtractError(f"change event has no {pk_field} to key on")

    # LSN orders changes within the source and makes EVENT_ID stable across
    # connector restarts. Snapshot reads have no LSN, so fall back to the
    # snapshot marker plus the primary key, which is equally deterministic.
    lsn = source.get("lsn")
    lsn_str = str(lsn) if lsn is not None else f"snapshot-{source.get('ts_ms', 0)}"

    return {
        "EVENT_ID": f"{lsn_str}-{table}-{pk}",
        "OP": str(op),
        "PK": str(pk),
        "LSN": lsn_str,
        "TS_MS": str(payload.get("ts_ms") or source.get("ts_ms") or 0),
        "SOURCE_TABLE": table,
        "BEFORE": before,
        "AFTER": after,
    }


# Primary key column per CDC source table.
PK_FIELDS = {
    "clients": "client_id",
    "accounts": "account_nbr",
}

EXTRACTORS = {
    "canonical": canonical,
    "debezium": debezium,
}
