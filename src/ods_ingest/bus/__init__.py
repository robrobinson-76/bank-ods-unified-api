"""Kafka bus layer — the governance point.

Everything that touches Kafka lives here: topic provisioning, the Avro
serializing producer, the consume→write→commit loop, and the DLQ. Adapters and
curation consumers use these primitives and never construct Kafka clients
themselves, so delivery semantics are decided in one place.
"""
