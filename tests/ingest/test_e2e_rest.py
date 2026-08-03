"""End-to-end: legacy SaaS REST API -> watermark poller -> raw tier -> securities.

The pattern's distinctive problem is that nothing pushes: the adapter has to
decide what is new, remember where it got to, survive rate limits, and be
restartable without losing or duplicating records.

The stub SaaS runs in-process via ASGI, so these tests need no extra service —
only Kafka, the registry, and Mongo.
"""
from __future__ import annotations

import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn

from ods_ingest import config, state
from ods_ingest.adapters.rest_poll import poller
from ods_ingest.bus.consumer import BatchConsumer
from ods_ingest.curation import vendor_securities
from ods_ingest.sink import mapping, writer
from ods_ingest.stub_saas.app import app as saas_app

pytestmark = pytest.mark.ingest


@pytest.fixture(scope="module")
def saas_server():
    """The stub SaaS on a real socket, for the lifetime of this module.

    A real HTTP server rather than an ASGI transport: the poller uses a sync
    httpx client (ASGITransport is async-only), and going over the loopback
    exercises the status codes, headers, and connection handling the adapter
    actually has to cope with.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(saas_app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base_url}/health", timeout=1.0)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        pytest.skip("stub SaaS did not start")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def saas(saas_server, monkeypatch):
    """Pristine dataset and no fault injection at the start of each test."""
    monkeypatch.delenv("SAAS_429_EVERY", raising=False)
    monkeypatch.delenv("SAAS_500_RATE", raising=False)
    with httpx.Client(base_url=saas_server) as client:
        client.post("/_admin/reset")
    return saas_server


@pytest.fixture(autouse=True)
def fresh_watermark():
    """Each test starts with no position, so its poll is self-contained."""
    state.reset_watermark(poller.SOURCE)
    yield
    state.reset_watermark(poller.SOURCE)


def _run_sink(group: str) -> None:
    consumer = BatchConsumer(
        mapping.sink_topics(), group_id=group, handler=writer.handle, stage="sink"
    )
    try:
        consumer.run_until_idle(idle_timeout=8)
    finally:
        consumer.close()


def _touch(base_url, vendor_ref: str, **extra) -> str:
    with httpx.Client(base_url=base_url) as client:
        response = client.post("/_admin/touch", json={"vendor_ref": vendor_ref, **extra})
        response.raise_for_status()
        return response.json()["updated_at"]


# ── incremental capture ──────────────────────────────────────────────────────

def test_first_poll_reads_everything_and_sets_a_watermark(db, saas):
    """With no stored position the adapter backfills the whole source."""
    result = poller.poll_once(saas)

    assert result.records > 0
    assert result.pages > 1, "the dataset should span more than one page"
    assert result.watermark is not None
    assert state.get_watermark(poller.SOURCE) == result.watermark

    _run_sink(f"test-{uuid.uuid4().hex[:8]}")
    assert db["raw_vendor_securities"].count_documents({}) >= result.records


def test_second_poll_returns_only_the_overlap_window(db, saas):
    """A steady-state poll must not re-read the world.

    It is not zero records: the adapter deliberately re-requests a small
    overlap before the watermark, because a source can commit a record with a
    timestamp behind one already returned.
    """
    first = poller.poll_once(saas)
    second = poller.poll_once(saas)

    assert second.records < first.records
    # Nothing changed, so the position must not move.
    assert second.watermark == first.watermark


def test_a_changed_record_is_picked_up_by_the_next_poll(db, saas):
    poller.poll_once(saas)
    _touch(saas, "VND-000007", issue_status="DELISTED")

    result = poller.poll_once(saas)
    assert result.records >= 1

    _run_sink(f"test-{uuid.uuid4().hex[:8]}")
    landed = db["raw_vendor_securities"].find_one({"Vendor_Ref": "VND-000007"})
    assert landed is not None
    assert landed["ISSUE_STATUS"] == "DELISTED"


def test_the_watermark_column_never_reaches_the_raw_tier(db, saas):
    """`updated_at` is the vendor's delivery metadata, not part of the record.

    The raw model does not declare it, so letting it through would either be
    dropped silently at validation or widen the served collection.
    """
    poller.poll_once(saas)
    _run_sink(f"test-{uuid.uuid4().hex[:8]}")

    doc = db["raw_vendor_securities"].find_one({"Vendor_Ref": "VND-000007"})
    assert doc is not None
    assert "updated_at" not in doc


# ── resilience ───────────────────────────────────────────────────────────────

def test_rate_limits_and_server_errors_are_retried(db, saas, monkeypatch):
    """A 429 partway through a page walk must slow the poll, not break it."""
    # Small pages so the walk spans enough requests for the injected 429 to
    # land mid-walk rather than after the poll has already finished.
    monkeypatch.setattr(config, "SAAS_PAGE_SIZE", 10)
    monkeypatch.setenv("SAAS_429_EVERY", "3")

    result = poller.poll_once(saas)

    assert result.retries > 0, "the fault injection did not fire"
    assert result.pages > 1
    assert result.records > 0, "the poll should still complete after backing off"
    # And the position still advances: a retried poll is a successful poll.
    assert state.get_watermark(poller.SOURCE) == result.watermark


def test_a_failed_poll_leaves_the_watermark_untouched(db, saas, monkeypatch):
    """If a page cannot be fetched, the position must not advance.

    Advancing past records that were never produced would lose them silently —
    the worst possible failure for an ingestion adapter.
    """
    poller.poll_once(saas)
    committed = state.get_watermark(poller.SOURCE)
    _touch(saas, "VND-000009")

    # Every request fails: the poll cannot complete.
    monkeypatch.setenv("SAAS_500_RATE", "1.0")
    with pytest.raises(Exception):
        poller.poll_once(saas)

    assert state.get_watermark(poller.SOURCE) == committed


def test_full_resync_rereads_the_entire_source(db, saas):
    """Backfill is just forgetting the position — no separate code path."""
    first = poller.poll_once(saas)
    steady = poller.poll_once(saas)
    assert steady.records < first.records

    resync = poller.poll_once(saas, full_resync=True)
    assert resync.records == first.records


# ── curation ─────────────────────────────────────────────────────────────────

def test_vendor_rows_enrich_matching_securities_only(db, saas):
    """Vendor rows enrich known instruments; unmatched rows are reported.

    The vendor is not authoritative for the existence of a security, so a row
    matching nothing must not create one.
    """
    before = db["securities"].count_documents({})
    poller.poll_once(saas)
    _run_sink(f"test-{uuid.uuid4().hex[:8]}")

    stats = vendor_securities.run(
        once=True, idle_timeout=8, group_id=f"test-cur-{uuid.uuid4().hex[:8]}"
    )
    assert stats.curated > 0
    # The vendor-only instruments in the dataset have nowhere to land.
    assert stats.reasons.get("UNMATCHED_VENDOR_RECORD", 0) > 0
    assert db["securities"].count_documents({}) == before, (
        "curation invented securities from a vendor feed"
    )


def test_vendor_status_maps_across_code_generations(db, saas):
    """'A', 'ACT', and 'Active' all mean ACTIVE; unknown codes change nothing."""
    assert vendor_securities.map_status("A") == "ACTIVE"
    assert vendor_securities.map_status("ACT") == "ACTIVE"
    assert vendor_securities.map_status("Active") == "ACTIVE"
    assert vendor_securities.map_status("MAT'D") == "MATURED"
    assert vendor_securities.map_status("DELISTED") == "DELISTED"
    # Unrecognised or absent: leave the curated value alone rather than guess.
    assert vendor_securities.map_status("???") is None
    assert vendor_securities.map_status(None) is None


def test_vendor_null_sentinels_are_treated_as_absent():
    """The feed spells 'nothing' several ways; all mean the same."""
    for sentinel in ("", "  ", "N/A", "#N/A", "NULL", "-"):
        assert vendor_securities.clean(sentinel) is None
    assert vendor_securities.clean(" 037833100 ") == "037833100"
