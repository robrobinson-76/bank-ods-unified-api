"""ODS Ingest — legacy source adapters feeding the bank ODS via Kafka/Avro.

Not part of the ODS proper: this component adapts external sources *into* the
ODS. It is the sanctioned writer of the raw tier and the curated collections;
the bank_ods transports remain read-only.

Import direction is one-way — ods_ingest may import bank_ods (models, registry,
logging), never the reverse. See docs/ARCHITECTURE-ingestion.md.
"""

__version__ = "0.1.0"
