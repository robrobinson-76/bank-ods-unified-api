"""Operations MCP server (bank-ods-ops) tests — the second MCP persona.

Covers the tool surface, raw tool parity with the raw service, and the
operational tools (health, stats, recent docs, reconciliation, logs, release
checks) against the seeded database.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastmcp import Client

import bank_ods.services.raw as svc_raw
from bank_ods.mcp_ops.server import mcp as ops_mcp
from bank_ods.models.registry import ENTITIES, ENTITIES_RAW, get_field_name, list_field_name
from tests.conftest import mcp_payload as _payload

pytestmark = pytest.mark.asyncio

EXPECTED_OPS_OPERATIONAL_TOOLS = {
    # operational group (mcp_ops/tools.py) — hand-written, no registry derivation
    "ping_database", "list_collections", "get_collection_stats", "query_recent",
    "find_raw_records", "reconcile_custody_feed", "get_recent_logs", "run_release_checks",
    # ingestion observability (reads ingest_state; never touches Kafka)
    "get_ingestion_status", "get_dlq_summary", "get_batch_history",
    "reconcile_crm_accounts", "reconcile_vendor_securities",
}

# The raw group is registry-generated: every ENTITIES_RAW model contributes a
# get + list tool. Deriving the expectation the same way the server does keeps
# this assertion about the *mechanism* — a new raw entity should not need this
# test edited, while a broken generator still fails loudly.
EXPECTED_OPS_TOOLS = EXPECTED_OPS_OPERATIONAL_TOOLS | {
    name
    for model in ENTITIES_RAW
    for name in (get_field_name(model), list_field_name(model))
}


async def test_ops_tool_surface():
    async with Client(ops_mcp) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == EXPECTED_OPS_TOOLS


# ── Raw tool group parity (moved here with the ops persona) ──────────────────

@pytest.mark.parametrize("model", ENTITIES_RAW, ids=lambda m: m.COLLECTION)
async def test_ops_raw_list_and_get_parity(model):
    """Generated raw tools return the identical envelopes as the raw service."""
    service = await svc_raw.list_raw_records(model, limit=2)
    async with Client(ops_mcp) as client:
        listed = _payload(await client.call_tool(list_field_name(model), {"limit": 2}))
        assert listed == service
        assert listed["data"], f"No {model.COLLECTION} seeded — run scripts/seed_data.py"

        record_id = listed["data"][0][model.ID_FIELD]
        got = _payload(
            await client.call_tool(get_field_name(model), {"record_id": record_id})
        )
    assert got == await svc_raw.get_raw_record(model, record_id)
    assert got[model.ID_FIELD] == record_id


# ── Operational tools ─────────────────────────────────────────────────────────

async def test_ops_ping_database():
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("ping_database", {}))
    assert result["ok"] is True
    assert result["version"]


async def test_ops_list_collections_covers_registry():
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("list_collections", {}))
    rows = {r["collection"]: r for r in result["data"]}
    assert set(rows) == {e.COLLECTION for e in ENTITIES}
    for row in rows.values():
        assert row["count"] > 0, f"{row['collection']} is empty — run scripts/seed_data.py"
        assert row["tier"] in ("semantic", "raw")
        assert row["lastInsertAt"]


async def test_ops_collection_stats_and_unknown():
    async with Client(ops_mcp) as client:
        stats = _payload(await client.call_tool(
            "get_collection_stats", {"collection": "raw_custody_positions"}
        ))
        unknown = _payload(await client.call_tool(
            "get_collection_stats", {"collection": "no_such_thing"}
        ))
    assert stats["count"] > 0
    assert stats["tier"] == "raw"
    index_names = {i["name"] for i in stats["indexes"]}
    assert any("REC_ID" in n for n in index_names)
    assert unknown["code"] == "UNKNOWN_COLLECTION"


async def test_ops_query_recent():
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool(
            "query_recent", {"collection": "raw_vendor_securities", "limit": 3}
        ))
    assert len(result["data"]) == 3
    for doc in result["data"]:
        assert doc["_insertedAt"]
        assert doc["Vendor_Ref"]


async def test_ops_find_raw_records():
    """Exact-match field search: all hits carry the searched value."""
    async with Client(ops_mcp) as client:
        first = _payload(await client.call_tool(
            "query_recent", {"collection": "raw_custody_positions", "limit": 1}
        ))["data"][0]
        acct = first["POS_ACCT_NBR"]
        result = _payload(await client.call_tool("find_raw_records", {
            "collection": "raw_custody_positions",
            "field": "POS_ACCT_NBR",
            "value": acct,
        }))
    assert result["data"], "expected at least the record we searched from"
    assert all(d["POS_ACCT_NBR"] == acct for d in result["data"])
    assert "next_cursor" in result["page_info"]


async def test_ops_find_raw_records_guards():
    """Semantic collections, unknown fields, and unknown collections are rejected."""
    async with Client(ops_mcp) as client:
        semantic = _payload(await client.call_tool("find_raw_records", {
            "collection": "accounts", "field": "accountId", "value": "ACC-000001",
        }))
        bad_field = _payload(await client.call_tool("find_raw_records", {
            "collection": "raw_vendor_securities", "field": "no_such_field", "value": "x",
        }))
        bad_coll = _payload(await client.call_tool("find_raw_records", {
            "collection": "no_such_thing", "field": "x", "value": "x",
        }))
    assert semantic["code"] == "NOT_RAW_COLLECTION"
    assert bad_field["code"] == "UNKNOWN_FIELD"
    assert "Vendor_Ref" in bad_field["error"]  # error lists the valid fields
    assert bad_coll["code"] == "UNKNOWN_COLLECTION"


async def test_ops_reconcile_custody_feed():
    """Reconciliation traces the latest cycle and classifies every record."""
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("reconcile_custody_feed", {}))
    assert result["records"] > 0
    assert result["matched"] + result["unmatched"] == result["records"]
    for issue in result["issues"]:
        assert issue["reason"] in ("UNKNOWN_ACCOUNT", "UNKNOWN_SECURITY", "NO_CURATED_POSITION")
        assert issue["recId"].startswith(result["cycleDate"])


async def test_ops_recent_logs():
    logging.getLogger("bank_ods.services").warning("ops-log-probe")
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool(
            "get_recent_logs", {"level": "WARNING", "limit": 10}
        ))
    assert any(e["msg"] == "ops-log-probe" for e in result["data"])
    assert all(e["level"] in ("WARNING", "ERROR", "CRITICAL") for e in result["data"])


async def test_ops_release_checks():
    """On a seeded database the composite check never FAILs; WARN is allowed
    (seeded raw and curated data are independent samples, so reconciliation
    may legitimately report drift)."""
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("run_release_checks", {}))
    assert result["status"] in ("PASS", "WARN")
    names = {c["name"] for c in result["checks"]}
    assert names == {
        "database_reachable", "collections_populated",
        "custody_feed_freshness", "custody_reconciliation",
        "feed_freshness", "dlq_empty",
    }
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["database_reachable"]["status"] == "PASS"
    assert by_name["collections_populated"]["status"] == "PASS"


# ── Ingestion observability ──────────────────────────────────────────────────
#
# These read the `ingest_state` collection that ODS Ingest writes. The fixture
# below seeds synthetic state so the tests stay in the core (Mongo-only) suite:
# the tools are being tested, not the pipeline that fills them.

INGEST_STATE = "ingest_state"


@pytest_asyncio.fixture
async def ingest_state(db):
    """Synthetic ingest state: one current feed, one stale, one DLQ, one batch.

    Saves and restores whatever was already there: on a machine that has run
    the pipeline, `ingest_state` holds real heartbeats under exactly these
    keys, and a test must not destroy them.
    """
    now = datetime.now(tz=timezone.utc)
    docs = [
        {"_id": "sink:ods.raw.custody.positions", "kind": "sink",
         "topic": "ods.raw.custody.positions", "collection": "raw_custody_positions",
         "recordsLanded": 500, "lastLandedAt": now - timedelta(hours=1)},
        {"_id": "sink:ods.raw.cash.movements", "kind": "sink",
         "topic": "ods.raw.cash.movements", "collection": "raw_cash_movements",
         "recordsLanded": 40, "lastLandedAt": now - timedelta(hours=48)},  # past its cadence
        {"_id": "watermark:vendorsec", "kind": "watermark", "source": "vendorsec",
         "value": "2026-07-30T12:00:00+00:00", "recordsTotal": 52, "polls": 3,
         "updatedAt": now},
        {"_id": "dlq:ods.raw.crm.clients", "kind": "dlq", "topic": "ods.raw.crm.clients",
         "count": 3, "lastError": "deserialize failed: schema id not found",
         "lastErrorAt": now, "samples": [{"stage": "sink", "partition": 0, "offset": 19}]},
        {"_id": "batch:CUSTPOS_20260730.dat:abc123", "kind": "batch",
         "batchId": "CUSTPOS_20260730.dat:abc123", "updatedAt": now,
         "manifest": {"batchId": "CUSTPOS_20260730.dat:abc123",
                      "fileName": "CUSTPOS_20260730.dat", "cycleDate": "20260730",
                      "recordCount": 500, "status": "COMPLETE", "failReason": None}},
    ]
    # Also taken over: a feed that must appear as NEVER_LANDED. On a machine
    # that has run the pipeline it has a real heartbeat, so the test controls
    # its absence rather than assuming it.
    absent_ids = ["sink:ods.raw.vendorsec.securities"]
    managed_ids = [d["_id"] for d in docs] + absent_ids
    saved = await db[INGEST_STATE].find({"_id": {"$in": managed_ids}}).to_list(length=None)

    await db[INGEST_STATE].delete_many({"_id": {"$in": managed_ids}})
    await db[INGEST_STATE].insert_many(docs)
    try:
        yield
    finally:
        await db[INGEST_STATE].delete_many({"_id": {"$in": managed_ids}})
        if saved:
            await db[INGEST_STATE].insert_many(saved)


async def test_ops_ingestion_status_classifies_feed_freshness(ingest_state):
    """Every declared feed is reported, including ones that never landed."""
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("get_ingestion_status", {}))

    by_topic = {f["topic"]: f for f in result["data"]}
    assert by_topic["ods.raw.custody.positions"]["status"] == "CURRENT"
    assert by_topic["ods.raw.custody.positions"]["recordsLanded"] == 500
    # 48h old against an 8h intraday cadence.
    assert by_topic["ods.raw.cash.movements"]["status"] == "STALE"
    # A feed with no sink heartbeat at all must be visible, not omitted.
    assert by_topic["ods.raw.vendorsec.securities"]["status"] == "NEVER_LANDED"
    assert by_topic["ods.raw.vendorsec.securities"]["lastLandedAt"] is None

    watermarks = {w["source"]: w for w in result["watermarks"]}
    assert watermarks["vendorsec"]["value"] == "2026-07-30T12:00:00+00:00"
    assert watermarks["vendorsec"]["polls"] == 3


async def test_ops_dlq_summary(ingest_state):
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("get_dlq_summary", {}))
    assert result["totalDeadLettered"] >= 3
    entry = next(e for e in result["data"] if e["topic"] == "ods.raw.crm.clients")
    assert entry["count"] == 3
    assert "schema id not found" in entry["lastError"]
    assert entry["samples"]


async def test_ops_batch_history(ingest_state):
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("get_batch_history", {"limit": 5}))
    batch = next(b for b in result["data"] if b["cycleDate"] == "20260730")
    assert batch["status"] == "COMPLETE"
    assert batch["recordCount"] == 500
    assert batch["failReason"] is None


async def test_ops_release_checks_flag_stale_feeds_and_dlq(ingest_state):
    """The two ingestion failures a monitoring agent must not miss."""
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("run_release_checks", {}))
    by_name = {c["name"]: c for c in result["checks"]}
    # A feed past its cadence warns rather than fails — it may simply be quiet.
    assert by_name["feed_freshness"]["status"] == "WARN"
    assert "ods.raw.cash.movements" in by_name["feed_freshness"]["detail"]["stale"]
    assert by_name["dlq_empty"]["status"] == "WARN"
    assert by_name["dlq_empty"]["detail"]["totalDeadLettered"] >= 3
    assert result["status"] in ("WARN", "FAIL")


async def test_ops_reconcile_crm_accounts():
    """Seeded CRM change events trace cleanly into the seeded accounts."""
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("reconcile_crm_accounts", {}))
    assert result["accountsInChangeLog"] > 0
    assert result["matched"] + result["unmatched"] == result["accountsInChangeLog"]
    for issue in result["issues"]:
        assert issue["reason"] in ("MISSING", "STATUS_MISMATCH", "STALE_CLIENT_EMBED")


async def test_ops_reconcile_vendor_securities():
    """Vendor rows resolve to securities; unmatched rows are reported, not failed."""
    async with Client(ops_mcp) as client:
        result = _payload(await client.call_tool("reconcile_vendor_securities", {}))
    assert result["vendorRows"] > 0
    assert result["matched"] + result["unmatched"] == result["vendorRows"]
    for issue in result["issues"]:
        assert issue["reason"] == "UNMATCHED_VENDOR_RECORD"