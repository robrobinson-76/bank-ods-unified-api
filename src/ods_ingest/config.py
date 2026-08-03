"""Environment configuration for ODS Ingest.

Mirrors the style of bank_ods.config: plain module-level values read once from
the environment (.env honoured via python-dotenv).
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── Bus ───────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
# Apicurio's Confluent-compatible API. Both the python clients and the Debezium
# converters use this URL, so one wire format is in play everywhere.
SCHEMA_REGISTRY_URL: str = os.getenv(
    "SCHEMA_REGISTRY_URL", "http://localhost:8081/apis/ccompat/v7"
)

# 7 days — the agreed replay window (docs/ARCHITECTURE-ingestion.md).
TOPIC_RETENTION_MS: int = int(os.getenv("TOPIC_RETENTION_MS", str(7 * 24 * 60 * 60 * 1000)))

# ── Producer tuning ───────────────────────────────────────────────────────────
# Defaults favour safety over raw throughput: idempotence plus acks=all means a
# broker retry can never duplicate or silently drop a record. The knobs are
# exposed because they are the first thing to reach for on a large file feed —
# see docs/PATTERN-snapshot-and-stream.md for what each is worth, measured.
PRODUCER_COMPRESSION: str = os.getenv("PRODUCER_COMPRESSION", "lz4")
PRODUCER_LINGER_MS: int = int(os.getenv("PRODUCER_LINGER_MS", "20"))
PRODUCER_BATCH_SIZE: int = int(os.getenv("PRODUCER_BATCH_SIZE", str(512 * 1024)))
PRODUCER_ACKS: str = os.getenv("PRODUCER_ACKS", "all")
PRODUCER_IDEMPOTENCE: bool = os.getenv("PRODUCER_IDEMPOTENCE", "true").lower() == "true"

# ── Mongo (ingest is a writer; bank_ods.config owns the read side) ────────────
MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB: str = os.getenv("MONGODB_DB", "bank_ods")

# Operational state (watermarks, processed-file ledger, heartbeats, DLQ counts).
# Deliberately outside the entity registry: it is ingest's own state, never
# served by any ODS transport.
INGEST_STATE_COLLECTION: str = "ingest_state"

# ── File adapter ──────────────────────────────────────────────────────────────
INGEST_DATA_ROOT: str = os.getenv("INGEST_DATA_ROOT", "./data/ingest")
_DATA_ROOT = INGEST_DATA_ROOT
INGEST_LANDING_DIR: str = os.getenv("INGEST_LANDING_DIR", f"{_DATA_ROOT}/landing")
INGEST_ARCHIVE_DIR: str = os.getenv("INGEST_ARCHIVE_DIR", f"{_DATA_ROOT}/archive")
INGEST_QUARANTINE_DIR: str = os.getenv("INGEST_QUARANTINE_DIR", f"{_DATA_ROOT}/quarantine")
# Durable adapter-owned state that is not a landing directory — currently the
# snapshot adapter's retained key index. Losing it is recoverable but expensive,
# so it lives outside the landing/archive churn.
INGEST_STATE_DIR: str = os.getenv("INGEST_STATE_DIR", f"{_DATA_ROOT}/state")
FILE_POLL_INTERVAL_S: float = float(os.getenv("FILE_POLL_INTERVAL_S", "5"))

# ── REST poll adapter ─────────────────────────────────────────────────────────
SAAS_BASE_URL: str = os.getenv("SAAS_BASE_URL", "http://localhost:8010")
POLL_INTERVAL_S: float = float(os.getenv("POLL_INTERVAL_S", "30"))
# Re-request a little before the watermark: guards against records committed
# out of timestamp order during a page walk. Duplicates are absorbed by the
# sink's idempotent upsert.
POLL_OVERLAP_S: int = int(os.getenv("POLL_OVERLAP_S", "60"))
SAAS_PAGE_SIZE: int = int(os.getenv("SAAS_PAGE_SIZE", "50"))

# ── Postgres CRM (CDC source) ─────────────────────────────────────────────────
CRM_DSN: str = os.getenv("CRM_DSN", "postgresql://crm:crm@localhost:5434/crm")
CONNECT_URL: str = os.getenv("CONNECT_URL", "http://localhost:8083")

# ── Consumer tuning ───────────────────────────────────────────────────────────
CONSUMER_BATCH_SIZE: int = int(os.getenv("CONSUMER_BATCH_SIZE", "500"))
CONSUMER_POLL_TIMEOUT_S: float = float(os.getenv("CONSUMER_POLL_TIMEOUT_S", "1.0"))
# How long a run_until_idle() loop waits with no records before declaring the
# backlog drained (used by tests and one-shot runs).
CONSUMER_IDLE_TIMEOUT_S: float = float(os.getenv("CONSUMER_IDLE_TIMEOUT_S", "5.0"))

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
