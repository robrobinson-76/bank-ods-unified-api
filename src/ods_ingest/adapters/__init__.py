"""Source adapters — the only components that know a source is legacy.

Each adapter turns one source's protocol and wire format into canonical Avro
records on a Kafka topic, and does nothing else: no domain mapping, no Mongo,
no knowledge of other sources or of curation. Downstream of the bus, an
adapted legacy feed is indistinguishable from a native Kafka producer, which
is the entire point of the pattern.
"""
