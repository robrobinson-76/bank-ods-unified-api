"""Canonical ingestion envelope.

Transport metadata travels in Kafka *headers* so the Avro payload stays a pure
source record. Every adapter stamps the same header set; consumers read it
without knowing which adapter produced the record.

CDC is the documented exception: Debezium's own change-event envelope is the
payload (op/before/after/source are data, not transport metadata), so CDC
records carry only whatever headers Debezium sets. Consumers must therefore
treat every header as optional.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional, Union

# Header names — the wire contract for transport metadata.
H_SOURCE_SYSTEM = "sourceSystem"
H_ADAPTER_ID = "adapterId"
H_ADAPTER_VERSION = "adapterVersion"
H_BATCH_ID = "batchId"
H_RECORD_SEQ = "recordSeq"
H_EXTRACTED_AT = "extractedAt"

# Matches what the Kafka client accepts and delivers. Values are bytes on the
# way out; inbound records — including ones we did not produce — may carry
# str or None, so both are tolerated on the way in.
HeaderValue = Union[str, bytes, None]
KafkaHeaders = list[tuple[str, HeaderValue]]


def utc_now_iso() -> str:
    """Current time as an ISO 8601 string with explicit UTC offset."""
    return datetime.now(timezone.utc).isoformat()


def build_headers(
    *,
    source_system: str,
    adapter_id: str,
    adapter_version: str,
    batch_id: Optional[str] = None,
    record_seq: Optional[int] = None,
    extracted_at: Optional[str] = None,
) -> KafkaHeaders:
    """Encode the canonical envelope as Kafka headers.

    batch_id/record_seq are omitted for non-batch (streaming) sources rather
    than sent empty, so their absence is meaningful.
    """
    headers: KafkaHeaders = [
        (H_SOURCE_SYSTEM, source_system.encode()),
        (H_ADAPTER_ID, adapter_id.encode()),
        (H_ADAPTER_VERSION, adapter_version.encode()),
        (H_EXTRACTED_AT, (extracted_at or utc_now_iso()).encode()),
    ]
    if batch_id is not None:
        headers.append((H_BATCH_ID, batch_id.encode()))
    if record_seq is not None:
        headers.append((H_RECORD_SEQ, str(record_seq).encode()))
    return headers


def decode_headers(
    headers: Union[dict[str, HeaderValue], Iterable[tuple[str, HeaderValue]], None],
) -> dict[str, str]:
    """Decode Kafka headers to a plain str->str dict.

    Tolerates None (no headers at all), None values, str values, and the dict
    form the client may hand back — all of which occur on records this codebase
    did not produce, notably CDC events.
    """
    if not headers:
        return {}
    pairs = headers.items() if isinstance(headers, dict) else headers
    out: dict[str, str] = {}
    for key, value in pairs:
        if value is None:
            continue
        if isinstance(value, str):
            out[key] = value
            continue
        try:
            out[key] = value.decode()
        except UnicodeDecodeError:
            # Not our envelope — keep the record processable rather than fail.
            continue
    return out


def record_seq_of(decoded: dict[str, str]) -> Optional[int]:
    """Read recordSeq from decoded headers, or None when absent/malformed."""
    raw = decoded.get(H_RECORD_SEQ)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
