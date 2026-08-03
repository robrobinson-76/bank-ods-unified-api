"""Operational tools for the bank-ods-ops MCP server.

All tools are read-only and delegate to bank_ods.services.ops (collection
introspection) or the in-process log ring. Collection-name parameters only
accept collections in the entity registry.
"""
from typing import Optional

from bank_ods.logging_config import recent_logs
from bank_ods.mcp_ops.server import mcp
import bank_ods.services.ops as svc_ops


@mcp.tool()
async def ping_database() -> dict:
    """Check MongoDB reachability. Returns server version, uptime, and current
    connection count — the first thing to call when anything looks wrong."""
    return await svc_ops.ping_database()


@mcp.tool()
async def list_collections() -> dict:
    """List every registered collection with its tier (semantic/raw), document
    count, and last insert time. The starting point for a health sweep."""
    return await svc_ops.list_collections()


@mcp.tool()
async def get_collection_stats(collection: str) -> dict:
    """Document count, data/storage sizes, index inventory, and last insert
    time for one registered collection. Unknown names return UNKNOWN_COLLECTION
    with the registered list."""
    return await svc_ops.get_collection_stats(collection)


@mcp.tool()
async def query_recent(collection: str, limit: int = 10) -> dict:
    """The N most recently inserted documents of a registered collection
    (max 50), newest first. Each document carries _insertedAt derived from its
    ObjectId — use this to inspect what a loader just wrote."""
    return await svc_ops.query_recent(collection, limit)


@mcp.tool()
async def find_raw_records(collection: str, field: str, value: str, limit: int = 20) -> dict:
    """Exact-match search over one raw-tier collection by any of its feed
    fields — "show me the raw records for account X". The match is against the
    stored wire-format value (custody accounts are zero-filled 12-char, e.g.
    account 1 is POS_ACCT_NBR "000000000001"). Unknown fields return
    UNKNOWN_FIELD with the valid field list; semantic collections are rejected
    (use the domain tools on bank-ods).

    Returns the standard {data, page_info} page (max 50 rows); if
    page_info.has_more, call again with cursor set to page_info.next_cursor to
    walk every match for a busy account."""
    return await svc_ops.find_raw_records(collection, field, value, limit)


@mcp.tool()
async def reconcile_custody_feed(cycle_date: Optional[str] = None) -> dict:
    """Trace one raw custody batch cycle (CCYYMMDD; defaults to latest) into
    the curated positions collection. Answers "why didn't this record appear?"
    — each unmatched raw record is reported with a reason: UNKNOWN_ACCOUNT,
    UNKNOWN_SECURITY, or NO_CURATED_POSITION (first 20 listed)."""
    return await svc_ops.reconcile_custody_feed(cycle_date)


@mcp.tool()
async def get_recent_logs(level: str = "INFO", limit: int = 50) -> dict:
    """Newest-first log entries from THIS process at or above the given level
    (DEBUG/INFO/WARNING/ERROR). Process-local by design — cross-process search
    belongs to the platform log aggregator; this answers "what just happened
    here" during interactive debugging."""
    return {"data": recent_logs(level, limit)}


@mcp.tool()
async def run_release_checks() -> dict:
    """Composite post-release verification for a monitoring agent to poll:
    database reachability, per-collection population, raw feed freshness,
    custody-feed reconciliation, per-feed ingestion freshness, and dead-letter
    counts — rolled up to status PASS / WARN / FAIL with per-check detail. Poll
    after a deployment until PASS, and alert on FAIL or a WARN that persists
    across polls."""
    return await svc_ops.run_release_checks()


# ── Ingestion (ODS Ingest — src/ods_ingest) ──────────────────────────────────
# Read-only views over the `ingest_state` collection the ingestion component
# writes. Deliberately Mongo-only: the ODS never connects to Kafka, so live
# broker lag stays the platform's concern and these tools stay safe to expose
# wherever the ops server runs.

@mcp.tool()
async def get_ingestion_status() -> dict:
    """Per-feed landing status: which source last delivered, when, how many
    records, and whether it is CURRENT, STALE (past the cadence this feed is
    expected to meet), or NEVER_LANDED. Also returns the REST poller's
    watermark. Start here for "is feed X alive?"."""
    return await svc_ops.get_ingestion_status()


@mcp.tool()
async def get_dlq_summary() -> dict:
    """Dead-lettered record counts per feed, with the most recent error and a
    few samples. A non-zero count means records were rejected by the sink or a
    curator and are parked on the corresponding ods.dlq.* topic — the original
    bytes are preserved there for replay once the cause is fixed."""
    return await svc_ops.get_dlq_summary()


@mcp.tool()
async def get_batch_history(limit: int = 10) -> dict:
    """Recent file-batch cycles (newest first) with their control-total
    outcome: COMPLETE means the file reconciled against its own trailer and was
    produced; FAILED means it was quarantined and nothing was produced from it.
    Answers "did last night's cycle close cleanly?"."""
    return await svc_ops.get_batch_history(limit)


@mcp.tool()
async def reconcile_crm_accounts() -> dict:
    """Trace the CRM change log into curated accounts — the CDC counterpart of
    reconcile_custody_feed. Each account in the change log is checked against
    the ODS and reported as MISSING (never curated), STATUS_MISMATCH (including
    a source delete that did not become a soft delete), or STALE_CLIENT_EMBED
    (the account carries an older client snapshot than the log holds)."""
    return await svc_ops.reconcile_crm_accounts()


@mcp.tool()
async def reconcile_vendor_securities() -> dict:
    """Compare landed vendor security rows against the curated master. Rows
    matching no ODS security are reported as UNMATCHED_VENDOR_RECORD — expected
    rather than broken, since the vendor carries instruments this ODS does not,
    and curation deliberately never invents a security from a vendor feed."""
    return await svc_ops.reconcile_vendor_securities()
