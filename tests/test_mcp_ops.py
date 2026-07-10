"""Operations MCP server (bank-ods-ops) tests — the second MCP persona.

Covers the tool surface, raw tool parity with the raw service, and the
operational tools (health, stats, recent docs, reconciliation, logs, release
checks) against the seeded database.
"""
import logging

import pytest
from fastmcp import Client

import bank_ods.services.raw as svc_raw
from bank_ods.mcp_ops.server import mcp as ops_mcp
from bank_ods.models.registry import ENTITIES, ENTITIES_RAW, get_field_name, list_field_name
from tests.conftest import mcp_payload as _payload

pytestmark = pytest.mark.asyncio

EXPECTED_OPS_TOOLS = {
    # operational group (mcp_ops/tools.py)
    "ping_database", "list_collections", "get_collection_stats", "query_recent",
    "find_raw_records", "reconcile_custody_feed", "get_recent_logs", "run_release_checks",
    # raw tool group (mcp/raw_tools.py, registry-generated)
    "get_raw_custody_position", "list_raw_custody_positions",
    "get_raw_vendor_security", "list_raw_vendor_securities",
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
    }
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["database_reachable"]["status"] == "PASS"
    assert by_name["collections_populated"]["status"] == "PASS"