"""The consume→write→commit loop every consumer reuses.

Delivery semantics live here, once:

  * offsets are committed only AFTER the handler's write returns, so a crash
    replays the batch rather than losing it — at-least-once.
  * a record that cannot be deserialized or handled goes to the DLQ and the
    batch continues; one poison record must not stall a feed.

Handlers therefore only have to be idempotent, which every downstream write is
(upsert on a natural key).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from confluent_kafka import Consumer, KafkaError, KafkaException, Message
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from ods_ingest import config, topics
from ods_ingest.bus.dlq import DlqPublisher
from ods_ingest.bus.producer import schema_registry_client
from ods_ingest.envelope import decode_headers

log = logging.getLogger("ods_ingest.consumer")


@dataclass
class ConsumedRecord:
    topic: str
    partition: int
    offset: int
    key: Optional[str]
    value: dict
    headers: dict[str, str]


# A handler receives one batch and returns how many documents it wrote.
Handler = Callable[[list[ConsumedRecord]], int]


class BatchConsumer:
    def __init__(
        self,
        topic_names: Iterable[str],
        *,
        group_id: str,
        handler: Handler,
        stage: str = "sink",
        from_beginning: bool = True,
    ):
        self.topic_names = list(topic_names)
        self.group_id = group_id
        self.handler = handler
        self.stage = stage

        # One deserializer for every topic: with schema_str=None the writer's
        # schema is fetched from the registry by the id in the message, so
        # Debezium-authored records decode the same way ours do.
        self._deserializer = AvroDeserializer(schema_registry_client(), schema_str=None)
        self._consumer = Consumer({
            "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "auto.offset.reset": "earliest" if from_beginning else "latest",
            # We commit explicitly after the write — auto-commit would risk
            # acknowledging records that were never persisted.
            "enable.auto.commit": False,
            "max.poll.interval.ms": 600000,
        })
        self._consumer.subscribe(self.topic_names)
        self._dlq = DlqPublisher()
        self.records_handled = 0
        self.records_dead_lettered = 0
        # perf_counter reading of when the last batch finished being written.
        # run_until_idle() necessarily waits out its idle timeout before
        # returning, so wall-clock around the call overstates the work by that
        # timeout; this is the honest end-of-work marker for measurement.
        self.last_batch_at: Optional[float] = None

    # ── internals ────────────────────────────────────────────────────────────

    def _dlq_topic_for(self, topic: str) -> str:
        spec = topics.BY_NAME.get(topic)
        return spec.dlq if spec else f"ods.dlq.{topic}"

    def _decode(self, msg: Message) -> Optional[ConsumedRecord]:
        """Deserialize one message; dead-letter and return None on failure."""
        # The client types these as optional, but a message that reached here
        # has passed the error check and always carries them.
        topic = msg.topic() or ""
        partition = msg.partition()
        offset = msg.offset()
        try:
            value = self._deserializer(
                msg.value(), SerializationContext(topic, MessageField.VALUE)
            )
            if value is None:
                # A tombstone (null value) on a topic we land as documents has
                # no meaning here — deletes arrive as change events instead.
                return None
            key = msg.key()
            return ConsumedRecord(
                topic=topic,
                partition=partition if partition is not None else -1,
                offset=offset if offset is not None else -1,
                key=key.decode(errors="replace") if isinstance(key, bytes) else key,
                value=value,
                headers=decode_headers(msg.headers()),
            )
        except Exception as exc:  # noqa: BLE001 — any decode failure is a DLQ case
            raw_key = msg.key()
            self._dlq.publish(
                self._dlq_topic_for(topic),
                source_topic=topic,
                error=f"deserialize failed: {exc}",
                stage=self.stage,
                raw_value=msg.value(),
                raw_key=raw_key if isinstance(raw_key, bytes) else None,
                partition=partition,
                offset=offset,
            )
            self.records_dead_lettered += 1
            return None

    def _handle_batch(self, batch: list[ConsumedRecord]) -> None:
        """Run the handler, then commit. A batch failure is retried per record."""
        if not batch:
            return
        try:
            self.handler(batch)
        except Exception as exc:  # noqa: BLE001
            # Isolate the poison record: re-run one at a time so a single bad
            # record costs one record, not the whole batch.
            log.warning("batch handler failed (%s); retrying records individually", exc)
            for record in batch:
                try:
                    self.handler([record])
                except Exception as single_exc:  # noqa: BLE001
                    self._dlq.publish(
                        self._dlq_topic_for(record.topic),
                        source_topic=record.topic,
                        error=f"handler failed: {single_exc}",
                        stage=self.stage,
                        partition=record.partition,
                        offset=record.offset,
                        sample=record.value,
                    )
                    self.records_dead_lettered += 1
        self.records_handled += len(batch)
        self._dlq.flush(10.0)
        # Commit last: everything above has been persisted by the handler.
        self._consumer.commit(asynchronous=False)
        self.last_batch_at = time.monotonic()

    def _poll_batch(self, max_records: int, timeout: float) -> tuple[list[ConsumedRecord], bool]:
        """Collect up to max_records. Returns (batch, saw_any_message)."""
        msgs = self._consumer.consume(num_messages=max_records, timeout=timeout)
        batch: list[ConsumedRecord] = []
        for msg in msgs:
            err = msg.error()
            if err is not None:
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(err)
            decoded = self._decode(msg)
            if decoded is not None:
                batch.append(decoded)
        return batch, bool(msgs)

    # ── public API ───────────────────────────────────────────────────────────

    def run_until_idle(self, idle_timeout: Optional[float] = None,
                       max_batches: Optional[int] = None) -> int:
        """Drain the current backlog, then return.

        "Idle" means no messages arrived for idle_timeout seconds. Used by the
        one-shot runs and by the e2e tests, which need a deterministic finish
        rather than a daemon.
        """
        idle = idle_timeout if idle_timeout is not None else config.CONSUMER_IDLE_TIMEOUT_S
        deadline = time.monotonic() + idle
        batches = 0
        while time.monotonic() < deadline:
            batch, saw_messages = self._poll_batch(
                config.CONSUMER_BATCH_SIZE, config.CONSUMER_POLL_TIMEOUT_S
            )
            if batch:
                self._handle_batch(batch)
            if saw_messages:
                deadline = time.monotonic() + idle  # reset: still flowing
                batches += 1
                if max_batches is not None and batches >= max_batches:
                    break
        return self.records_handled

    def run_forever(self) -> None:
        log.info("consuming %s as %s", self.topic_names, self.group_id)
        try:
            while True:
                batch, _ = self._poll_batch(
                    config.CONSUMER_BATCH_SIZE, config.CONSUMER_POLL_TIMEOUT_S
                )
                if batch:
                    self._handle_batch(batch)
        except KeyboardInterrupt:
            log.info("stopping %s", self.group_id)
        finally:
            self.close()

    def close(self) -> None:
        """Release this consumer's Kafka resources.

        Deliberately does NOT close the shared Mongo client: a process runs
        several consumers in sequence (and tests run many), so the client's
        lifetime belongs to the process entry point, not to one consumer.
        """
        self._dlq.flush(10.0)
        self._consumer.close()

    def __enter__(self) -> "BatchConsumer":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
