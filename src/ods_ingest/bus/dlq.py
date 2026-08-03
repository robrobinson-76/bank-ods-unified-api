"""Dead-letter queue.

A poisoned record must not stall a feed and must not vanish. Publishing the
ORIGINAL bytes (not a re-serialized interpretation) means a record that failed
to deserialize is still recoverable — it can be replayed once the bug or the
schema is fixed.

The DLQ topic holds the evidence; ingest_state holds a bounded counter and
sample so ops tooling can answer "is anything broken?" without consuming Kafka.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from confluent_kafka import Producer

from ods_ingest import config, state
from ods_ingest.envelope import KafkaHeaders

log = logging.getLogger("ods_ingest.dlq")

H_ERROR = "error"
H_ERROR_AT = "errorAt"
H_SOURCE_TOPIC = "sourceTopic"
H_SOURCE_PARTITION = "sourcePartition"
H_SOURCE_OFFSET = "sourceOffset"
H_STAGE = "stage"  # "sink" | "curation" | "adapter"


class DlqPublisher:
    """Raw-bytes producer for dead-lettered records.

    Deliberately not the Avro TopicProducer: the whole point is to preserve
    bytes we may have failed to decode.
    """

    def __init__(self) -> None:
        self._producer = Producer({
            "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
            "enable.idempotence": True,
            "acks": "all",
        })

    def publish(
        self,
        dlq_topic: str,
        *,
        source_topic: str,
        error: str,
        stage: str,
        raw_value: Optional[bytes] = None,
        raw_key: Optional[bytes] = None,
        partition: Optional[int] = None,
        offset: Optional[int] = None,
        sample: Optional[dict[str, Any]] = None,
    ) -> None:
        headers: KafkaHeaders = [
            (H_ERROR, error[:1000].encode()),
            (H_ERROR_AT, state.now().isoformat().encode()),
            (H_SOURCE_TOPIC, source_topic.encode()),
            (H_STAGE, stage.encode()),
        ]
        if partition is not None:
            headers.append((H_SOURCE_PARTITION, str(partition).encode()))
        if offset is not None:
            headers.append((H_SOURCE_OFFSET, str(offset).encode()))

        self._producer.produce(
            topic=dlq_topic, key=raw_key, value=raw_value, headers=headers
        )
        self._producer.poll(0)

        state.record_dlq(
            source_topic,
            error[:500],
            {
                "stage": stage,
                "partition": partition,
                "offset": offset,
                "at": state.now(),
                **({"record": sample} if sample else {}),
            },
        )
        log.warning("dead-lettered %s@%s/%s: %s", source_topic, partition, offset, error[:200])

    def flush(self, timeout: float = 30.0) -> None:
        self._producer.flush(timeout)
