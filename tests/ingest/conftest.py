"""Fixtures for the ingestion end-to-end tests.

Everything here is marked `ingest` and auto-skips when the compose stack is not
running, so the core suite stays Mongo-only and fast:

    docker compose -f docker-compose.yml -f docker-compose.ingest.yml up -d
    python -m ods_ingest.bus.admin
    pytest -m ingest

Each test drives the real components in-process (adapter, sink, curation) with
its own consumer group and its own cycle date, so tests neither compete with a
running pipeline nor with each other.
"""
from __future__ import annotations

import socket
import uuid
from pathlib import Path
from urllib.request import urlopen

import pytest

from ods_ingest import config, state

pytestmark = pytest.mark.ingest


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:  # noqa: BLE001 — any failure means "not available"
        return False


def _kafka_reachable() -> bool:
    host, _, port = config.KAFKA_BOOTSTRAP_SERVERS.partition(":")
    return _tcp_open(host or "localhost", int(port or 9092))


def _registry_reachable() -> bool:
    base = config.SCHEMA_REGISTRY_URL.split("/apis/")[0]
    return _http_ok(f"{base}/apis/ccompat/v7/subjects")


def _mongo_reachable() -> bool:
    try:
        state.get_db().client.admin.command("ping")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session", autouse=True)
def ingest_stack():
    """Skip the whole module unless the bus, registry, and Mongo are up."""
    missing = []
    if not _kafka_reachable():
        missing.append(f"kafka ({config.KAFKA_BOOTSTRAP_SERVERS})")
    if not _registry_reachable():
        missing.append(f"schema registry ({config.SCHEMA_REGISTRY_URL})")
    if not _mongo_reachable():
        missing.append(f"mongodb ({config.MONGODB_URI})")
    if missing:
        pytest.skip(
            "ingest stack unavailable: " + ", ".join(missing) + ". Start it with: "
            "docker compose -f docker-compose.yml -f docker-compose.ingest.yml up -d "
            "&& python -m ods_ingest.bus.admin"
        )

    # Topics must exist — auto-create is off on the broker by design.
    from ods_ingest.bus.admin import desired_topics, existing_topics
    from confluent_kafka.admin import AdminClient

    admin = AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS})
    absent = set(desired_topics()) - existing_topics(admin)
    if absent:
        pytest.skip(f"topics not provisioned ({sorted(absent)[:3]}…): "
                    f"run python -m ods_ingest.bus.admin")
    yield


@pytest.fixture(scope="session")
def db():
    """Sync Mongo handle — the ingest side writes with pymongo."""
    return state.get_db()


@pytest.fixture
def unique_group() -> str:
    """A fresh consumer group per test, so each reads the topic from the start."""
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def landing_dir(tmp_path, monkeypatch) -> Path:
    """Isolated landing/archive/quarantine directories for one test."""
    landing = tmp_path / "landing"
    archive = tmp_path / "archive"
    quarantine = tmp_path / "quarantine"
    for d in (landing, archive, quarantine):
        d.mkdir(parents=True)
    monkeypatch.setattr(config, "INGEST_LANDING_DIR", str(landing))
    monkeypatch.setattr(config, "INGEST_ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(config, "INGEST_QUARANTINE_DIR", str(quarantine))
    return landing


@pytest.fixture
def archive_dir(landing_dir) -> Path:
    return Path(config.INGEST_ARCHIVE_DIR)


@pytest.fixture
def quarantine_dir(landing_dir) -> Path:
    return Path(config.INGEST_QUARANTINE_DIR)
