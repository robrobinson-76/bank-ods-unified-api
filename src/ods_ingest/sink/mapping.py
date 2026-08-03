"""Which topics the sink consumes.

Derived from the topic map, which is itself anchored to the bank_ods registry:
every topic that carries a raw model, plus the manifest topic. No per-source
knowledge lives here — that is the point of a generic sink.
"""
from __future__ import annotations

from ods_ingest import topics

SINK_GROUP_ID = "ods-ingest-sink"


def sink_topics() -> list[str]:
    """Every topic the sink lands, including the batch manifest topic."""
    return [t.name for t in topics.TOPICS]


def describe() -> list[tuple[str, str, str]]:
    """(topic, collection, extractor) — what `--list` prints, and what tests assert."""
    return [
        (t.name, t.collection or "(ingest_state)", t.extractor)
        for t in topics.TOPICS
    ]
