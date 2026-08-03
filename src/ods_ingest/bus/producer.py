"""Avro serializing producer.

One wrapper so every adapter produces identically: the checked-in .avsc as the
subject's schema, the canonical envelope in headers, the declared key field as
the message key, and a hard flush before the caller believes anything was sent.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext, StringSerializer

from ods_ingest import config, topics
from ods_ingest.envelope import KafkaHeaders
from ods_ingest.schemas import load_schema_str

log = logging.getLogger("ods_ingest.producer")


def schema_registry_client() -> SchemaRegistryClient:
    return SchemaRegistryClient({"url": config.SCHEMA_REGISTRY_URL})


class TopicProducer:
    """Produces Avro records to one topic.

    Delivery is at-least-once by design: we flush and surface failures, but do
    not attempt exactly-once. Duplicates are resolved downstream by the sink's
    idempotent upsert on the record's natural key.
    """

    def __init__(self, topic: str, *, adapter_id: str, adapter_version: str = "1.0.0",
                 overrides: Optional[dict[str, Any]] = None):
        self.spec = topics.get(topic)
        self.adapter_id = adapter_id
        self.adapter_version = adapter_version

        self._serializer = AvroSerializer(
            schema_registry_client(),
            load_schema_str(self.spec.contract),
            # Records are plain dicts all the way through — no intermediate
            # object layer between the parser and the wire.
            lambda obj, ctx: obj,
        )
        self._key_serializer = StringSerializer("utf_8")
        settings: dict[str, Any] = {
            "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
            # No duplicates from broker retries. With acks=all this is the
            # combination that makes a redelivery the sink's problem to absorb
            # rather than the bus's to create.
            "enable.idempotence": config.PRODUCER_IDEMPOTENCE,
            "acks": config.PRODUCER_ACKS,
            "compression.type": config.PRODUCER_COMPRESSION,
            "linger.ms": config.PRODUCER_LINGER_MS,   # batch a little; EOD files are bursty
            "batch.size": config.PRODUCER_BATCH_SIZE,
        }
        # Benchmark/tuning hook. Production paths leave this alone and take the
        # configured defaults.
        settings.update(overrides or {})
        self._producer = Producer(settings)
        self._failures: list[str] = []
        self._delivered = 0

    # ── delivery accounting ──────────────────────────────────────────────────

    def _on_delivery(self, err: Any, msg: Any) -> None:
        if err is not None:
            self._failures.append(str(err))
            log.error("delivery failed on %s: %s", self.spec.name, err)
        else:
            self._delivered += 1

    @property
    def delivered(self) -> int:
        return self._delivered

    # ── producing ────────────────────────────────────────────────────────────

    def produce(self, record: dict, *, headers: Optional[KafkaHeaders] = None,
                key: Optional[str] = None) -> None:
        """Queue one record. Nothing is guaranteed sent until flush() returns."""
        if key is None and self.spec.key_field:
            key = record.get(self.spec.key_field)

        payload = self._serializer(
            record, SerializationContext(self.spec.name, MessageField.VALUE)
        )
        # Retry once on a full local queue rather than dropping the record.
        try:
            self._producer.produce(
                topic=self.spec.name,
                key=self._key_serializer(key) if key is not None else None,
                value=payload,
                headers=headers,
                on_delivery=self._on_delivery,
            )
        except BufferError:
            self._producer.poll(1.0)
            self._producer.produce(
                topic=self.spec.name,
                key=self._key_serializer(key) if key is not None else None,
                value=payload,
                headers=headers,
                on_delivery=self._on_delivery,
            )
        self._producer.poll(0)

    def flush(self, timeout: float = 60.0) -> int:
        """Block until the queue drains; raise if anything failed to deliver.

        Callers rely on this: the file adapter only emits a batch manifest, and
        the poller only advances its watermark, after a clean flush.
        """
        remaining = self._producer.flush(timeout)
        if remaining:
            raise RuntimeError(
                f"{remaining} message(s) still queued for {self.spec.name} after {timeout}s"
            )
        if self._failures:
            first = self._failures[0]
            count = len(self._failures)
            self._failures.clear()
            raise RuntimeError(f"{count} delivery failure(s) on {self.spec.name}; first: {first}")
        return self._delivered

    def __enter__(self) -> "TopicProducer":
        return self

    def __exit__(self, *exc: Any) -> None:
        # On the error path a flush failure must not mask the original error.
        if exc[0] is None:
            self.flush()
        else:
            self._producer.flush(10.0)
