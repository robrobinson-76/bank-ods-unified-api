"""Operational introspection queries — the service layer behind the ops MCP
server (bank-ods-ops).

Everything here is read-only and registry-scoped: tools that accept a
collection name only accept collections registered in the entity registry.
Consumer-facing transports never call this module; it exists for engineers,
support, and release-monitoring agents working on the operational state of
the store itself (counts, freshness, index health, raw-vs-curated drift).
"""
import logging
from datetime import datetime, timezone
from typing import Any

import pymongo.errors

from bank_ods.db.client import get_collection, get_db
from bank_ods.models.registry import ENTITIES, ENTITIES_RAW
from bank_ods.services import generic
from bank_ods.services._common import (
    account_id_from_custody,
    cusip_from_isin,
    serialize_doc,
)
from bank_ods.services.pagination import clamp_limit

logger = logging.getLogger("bank_ods.services.ops")

_OPS_MAX_LIMIT = 50

_KNOWN_COLLECTIONS = {e.COLLECTION: e for e in ENTITIES}


def serialize_value(value: Any) -> Any:
    """JSON-safe single value — datetimes to ISO 8601 with an explicit offset.

    The ODS serialization rule applied to the scalar fields of operational
    state documents, which are not entity documents and so do not pass through
    serialize_doc().
    """
    if isinstance(value, datetime):
        stamped = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamped.isoformat()
    return value


def _tier(collection: str) -> str:
    model = _KNOWN_COLLECTIONS[collection]
    return "raw" if model in ENTITIES_RAW else "semantic"


def _unknown(collection: str) -> dict:
    return {
        "error": f"Unknown collection {collection!r}; registered: "
                 f"{sorted(_KNOWN_COLLECTIONS)}",
        "code": "UNKNOWN_COLLECTION",
    }


async def _last_insert_at(collection: str) -> str | None:
    """Insertion time of the newest document, derived from its ObjectId."""
    docs = await get_collection(collection).find({}, {"_id": 1}).sort("_id", -1).limit(1).to_list(1)
    if not docs:
        return None
    return docs[0]["_id"].generation_time.isoformat()


async def ping_database() -> dict:
    """Server reachability, version, and uptime."""
    try:
        db = get_db()
        await db.command("ping")
        build = await db.command("buildInfo")
        status = await db.command("serverStatus")
        return {
            "ok": True,
            "version": build.get("version"),
            "uptimeSeconds": int(status.get("uptime", 0)),
            "connections": status.get("connections", {}).get("current"),
        }
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in ping_database")
        return {"ok": False, "error": "Database unreachable", "code": "MONGO_ERROR"}


async def list_collections() -> dict:
    """Every registered collection with tier, document count, and last insert time."""
    try:
        rows = []
        for collection in _KNOWN_COLLECTIONS:
            count = await get_collection(collection).count_documents({})
            rows.append({
                "collection": collection,
                "tier": _tier(collection),
                "count": count,
                "lastInsertAt": await _last_insert_at(collection),
            })
        return {"data": rows}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in list_collections")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_collection_stats(collection: str) -> dict:
    """Count, sizes, and index inventory for one registered collection."""
    if collection not in _KNOWN_COLLECTIONS:
        return _unknown(collection)
    try:
        db = get_db()
        stats = await db.command("collStats", collection)
        indexes = await get_collection(collection).index_information()
        return {
            "collection": collection,
            "tier": _tier(collection),
            "count": stats.get("count", 0),
            "sizeBytes": stats.get("size", 0),
            "storageSizeBytes": stats.get("storageSize", 0),
            "indexes": [
                {"name": name, "keys": [list(k) for k in info.get("key", [])]}
                for name, info in sorted(indexes.items())
            ],
            "lastInsertAt": await _last_insert_at(collection),
        }
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_collection_stats(%s)", collection)
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def query_recent(collection: str, limit: int = 10) -> dict:
    """The N most recently inserted documents of a registered collection.

    Each document gains an ``_insertedAt`` field derived from its ObjectId so
    an investigator can see when the loader wrote it.
    """
    if collection not in _KNOWN_COLLECTIONS:
        return _unknown(collection)
    try:
        n = clamp_limit(limit, _OPS_MAX_LIMIT)
        docs = await get_collection(collection).find({}).sort("_id", -1).limit(n).to_list(n)
        out = []
        for doc in docs:
            inserted = doc["_id"].generation_time.isoformat()
            out.append({"_insertedAt": inserted, **serialize_doc(doc)})
        return {"data": out}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in query_recent(%s)", collection)
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def find_raw_records(
    collection: str, field: str, value: str, limit: int = 20
) -> dict:
    """Exact-match search over one raw collection by any of its model fields.

    The investigation tool for "show me the raw records for account X": scoped
    to raw-tier collections (semantic entities have curated domain queries),
    and the field name is validated against the model so only real feed fields
    are queryable. The match is exact against the stored wire-format value —
    e.g. account 1 in the custody feed is POS_ACCT_NBR "000000000001".

    Returns the standard keyset-paginated envelope ({data, page_info}); page
    through page_info.next_cursor to walk every match for a busy account.
    """
    model = _KNOWN_COLLECTIONS.get(collection)
    if model is None:
        return _unknown(collection)
    if model not in ENTITIES_RAW:
        return {
            "error": f"{collection!r} is semantic-tier; use the domain query tools",
            "code": "NOT_RAW_COLLECTION",
        }
    if field not in model.model_fields:
        return {
            "error": f"Unknown field {field!r} on {collection}; fields: "
                     f"{sorted(model.model_fields)}",
            "code": "UNKNOWN_FIELD",
        }
    return await generic.get_many(
        collection, {field: value}, model.DEFAULT_SORT, clamp_limit(limit, _OPS_MAX_LIMIT)
    )


async def reconcile_custody_feed(cycle_date: str | None = None) -> dict:
    """Trace one raw custody batch cycle into the curated positions collection.

    Answers "why didn't this record appear?": for each raw detail record in
    the cycle, resolve the account (POS_ACCT_NBR -> accountId) and the
    security (POS_CUSIP_NBR / POS_ISIN_NBR -> securityId), then check a
    curated position exists for that pair. Unresolvable or positionless
    records are reported with their REC_ID (first 20).

    cycle_date is CCYYMMDD; defaults to the latest cycle present in the feed.
    """
    try:
        raw_col = get_collection("raw_custody_positions")
        if cycle_date is None:
            newest = await raw_col.find({}, {"POS_BUS_DATE": 1}).sort("POS_BUS_DATE", -1).limit(1).to_list(1)
            if not newest:
                return {"error": "raw_custody_positions is empty", "code": "NOT_FOUND"}
            cycle_date = newest[0]["POS_BUS_DATE"]

        raw_docs = await raw_col.find({"POS_BUS_DATE": cycle_date}).to_list(length=None)
        if not raw_docs:
            return {"error": f"No raw records for cycle {cycle_date}", "code": "NOT_FOUND"}

        account_ids = {
            doc["accountId"]
            async for doc in get_collection("accounts").find({}, {"accountId": 1})
        }
        sec_by_cusip: dict[str, str] = {}
        sec_by_isin: dict[str, str] = {}
        async for doc in get_collection("securities").find(
            {}, {"securityId": 1, "cusip": 1, "isin": 1}
        ):
            if doc.get("cusip"):
                sec_by_cusip[doc["cusip"]] = doc["securityId"]
            if doc.get("isin"):
                sec_by_isin[doc["isin"]] = doc["securityId"]
                # Raw feeds often carry the CUSIP embedded in a US/CA ISIN even
                # when the curated master stores only the ISIN.
                embedded = cusip_from_isin(doc["isin"])
                if embedded:
                    sec_by_cusip.setdefault(embedded, doc["securityId"])

        position_pairs = {
            (d["_id"]["a"], d["_id"]["s"])
            for d in await get_collection("positions").aggregate([
                {"$group": {"_id": {"a": "$accountId", "s": "$securityId"}}}
            ]).to_list(length=None)
        }

        matched = 0
        issues: list[dict] = []
        for rec in raw_docs:
            # Raw docs are stored as received and not validated on read, so a
            # malformed record may have a non-numeric or missing key. Resolve
            # defensively — an unresolvable account is an UNKNOWN_ACCOUNT issue
            # to report, never an exception that aborts the whole reconcile.
            cusip = rec.get("POS_CUSIP_NBR")
            account_id = account_id_from_custody(rec.get("POS_ACCT_NBR", ""))
            security_id = (sec_by_cusip.get(cusip) if cusip else None) or (
                sec_by_isin.get(rec["POS_ISIN_NBR"]) if rec.get("POS_ISIN_NBR") else None
            )
            if account_id is None or account_id not in account_ids:
                reason = "UNKNOWN_ACCOUNT"
            elif security_id is None:
                reason = "UNKNOWN_SECURITY"
            elif (account_id, security_id) not in position_pairs:
                reason = "NO_CURATED_POSITION"
            else:
                matched += 1
                continue
            issues.append({
                "recId": rec.get("REC_ID"),
                "reason": reason,
                "accountId": account_id,
                "securityId": security_id,
                "cusip": cusip,
            })

        return {
            "cycleDate": cycle_date,
            "records": len(raw_docs),
            "matched": matched,
            "unmatched": len(issues),
            "issues": issues[:20],
        }
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in reconcile_custody_feed")
        return {"error": "Database error", "code": "MONGO_ERROR"}


# ── Ingestion observability ──────────────────────────────────────────────────
#
# ODS Ingest (src/ods_ingest) writes its operational state — watermarks, the
# batch ledger, sink heartbeats, DLQ counters — to the `ingest_state`
# collection. These functions read it so the ops MCP server can answer "is this
# feed alive and current?" without the ODS ever talking to Kafka: the read side
# stays a Mongo-only consumer, and live broker metrics remain the platform's job.

INGEST_STATE_COLLECTION = "ingest_state"

# How stale a feed may go before it is worth reporting, per its cadence.
_FEED_FRESHNESS_HOURS = {
    "ods.raw.custody.positions": 30,      # nightly cycle, plus slack
    "ods.raw.custody.batches": 30,
    "ods.raw.cash.movements": 8,          # several intraday drops
    "ods.raw.vendorsec.securities": 26,   # daily vendor delivery
    "ods.raw.crm.clients": 26,            # streaming, but quiet sources are normal
    "ods.raw.crm.accounts": 26,
}


def _hours_since(value: Any) -> float | None:
    if not isinstance(value, datetime):
        return None
    stamped = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - stamped).total_seconds() / 3600.0


async def get_ingestion_status() -> dict:
    """Per-feed landing status: what arrived, when, and how stale it is.

    Reports every declared feed, including ones that have never landed —
    a feed that was never wired up is exactly what this tool should surface.
    """
    try:
        state_col = get_collection(INGEST_STATE_COLLECTION)
        sinks = {d["_id"]: d async for d in state_col.find({"kind": "sink"})}
        watermarks = {d["_id"]: d async for d in state_col.find({"kind": "watermark"})}

        feeds = []
        for topic, max_age in _FEED_FRESHNESS_HOURS.items():
            sink = sinks.get(f"sink:{topic}")
            age = _hours_since(sink.get("lastLandedAt")) if sink else None
            feeds.append({
                "topic": topic,
                "collection": (sink or {}).get("collection"),
                "recordsLanded": (sink or {}).get("recordsLanded", 0),
                "lastLandedAt": serialize_value((sink or {}).get("lastLandedAt")),
                "ageHours": round(age, 2) if age is not None else None,
                "maxAgeHours": max_age,
                "status": ("NEVER_LANDED" if age is None
                           else ("STALE" if age > max_age else "CURRENT")),
            })

        return {
            "data": feeds,
            "watermarks": [
                {
                    "source": d.get("source"),
                    "value": d.get("value"),
                    "recordsTotal": d.get("recordsTotal", 0),
                    "polls": d.get("polls", 0),
                    "updatedAt": serialize_value(d.get("updatedAt")),
                }
                for d in watermarks.values()
            ],
        }
    except pymongo.errors.PyMongoError:
        logger.exception("get_ingestion_status failed")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_dlq_summary() -> dict:
    """Dead-lettered record counts and recent failure samples, per feed.

    Counts come from the ingest state written alongside each DLQ publish; the
    DLQ topics themselves hold the original bytes for replay.
    """
    try:
        state_col = get_collection(INGEST_STATE_COLLECTION)
        entries = []
        total = 0
        async for doc in state_col.find({"kind": "dlq"}).sort("count", pymongo.DESCENDING):
            count = doc.get("count", 0)
            total += count
            entries.append({
                "topic": doc.get("topic"),
                "count": count,
                "lastError": doc.get("lastError"),
                "lastErrorAt": serialize_value(doc.get("lastErrorAt")),
                "samples": [
                    {k: serialize_value(v) for k, v in sample.items() if k != "record"}
                    for sample in (doc.get("samples") or [])[-3:]
                ],
            })
        return {"data": entries, "totalDeadLettered": total}
    except pymongo.errors.PyMongoError:
        logger.exception("get_dlq_summary failed")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_batch_history(limit: int = 10) -> dict:
    """Recent file-batch cycles and whether each closed cleanly.

    A batch is the file adapter's unit of delivery; its manifest records the
    control totals and whether they reconciled.
    """
    limit = clamp_limit(limit, _OPS_MAX_LIMIT)
    try:
        state_col = get_collection(INGEST_STATE_COLLECTION)
        batches = []
        cursor = state_col.find({"kind": "batch"}).sort("updatedAt", pymongo.DESCENDING).limit(limit)
        async for doc in cursor:
            manifest = doc.get("manifest") or {}
            batches.append({
                "batchId": doc.get("batchId"),
                "fileName": manifest.get("fileName"),
                "cycleDate": manifest.get("cycleDate"),
                "recordCount": manifest.get("recordCount"),
                "status": manifest.get("status"),
                "failReason": manifest.get("failReason"),
                "updatedAt": serialize_value(doc.get("updatedAt")),
            })
        return {"data": batches}
    except pymongo.errors.PyMongoError:
        logger.exception("get_batch_history failed")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def reconcile_crm_accounts() -> dict:
    """Compare the CRM change log against curated accounts.

    Answers "did every captured account change reach the ODS?" — the CDC
    equivalent of reconcile_custody_feed. Classifies each latest-state account
    as MISSING (never curated), STATUS_MISMATCH, or STALE_CLIENT_EMBED (the
    account carries an older client snapshot than the change log holds).
    """
    try:
        events_col = get_collection("raw_crm_account_events")
        if await events_col.count_documents({}, limit=1) == 0:
            return {"error": "raw_crm_account_events is empty", "code": "NOT_FOUND"}

        # Latest event per account, by source order (LSN).
        latest: dict[str, dict] = {}
        async for event in events_col.find({}).sort("LSN", pymongo.ASCENDING):
            latest[event["PK"]] = event

        client_names: dict[str, str] = {}
        async for event in get_collection("raw_crm_client_events").find({}).sort(
            "LSN", pymongo.ASCENDING
        ):
            after = event.get("AFTER") or {}
            if after.get("client_name"):
                client_names[event["PK"]] = after["client_name"]

        accounts = {
            d["accountId"]: d
            async for d in get_collection("accounts").find(
                {}, {"accountId": 1, "status": 1, "client": 1}
            )
        }

        matched = 0
        issues: list[dict] = []
        for account_nbr, event in latest.items():
            after = event.get("AFTER")
            curated = accounts.get(account_nbr)

            if curated is None:
                issues.append({"accountNbr": account_nbr, "reason": "MISSING",
                               "lastOp": event.get("OP")})
                continue

            # A deleted source row must appear as a soft delete, not vanish.
            expected_status = "CLOSED" if (event.get("OP") == "d" or after is None) else {
                "active": "ACTIVE", "suspended": "SUSPENDED", "closed": "CLOSED",
            }.get((after or {}).get("status", "").lower(), "ACTIVE")

            if curated.get("status") != expected_status:
                issues.append({
                    "accountNbr": account_nbr, "reason": "STATUS_MISMATCH",
                    "curated": curated.get("status"), "expected": expected_status,
                })
                continue

            client_id = (curated.get("client") or {}).get("clientId")
            expected_name = client_names.get(client_id) if client_id else None
            if expected_name and (curated.get("client") or {}).get("clientName") != expected_name:
                issues.append({
                    "accountNbr": account_nbr, "reason": "STALE_CLIENT_EMBED",
                    "clientId": client_id,
                    "curated": (curated.get("client") or {}).get("clientName"),
                    "expected": expected_name,
                })
                continue

            matched += 1

        return {
            "accountsInChangeLog": len(latest),
            "matched": matched,
            "unmatched": len(issues),
            "issues": issues[:20],
        }
    except pymongo.errors.PyMongoError:
        logger.exception("reconcile_crm_accounts failed")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def reconcile_vendor_securities() -> dict:
    """Compare landed vendor rows against the curated security master.

    Vendor rows that match nothing are expected — the vendor carries
    instruments the ODS does not — so they are reported as UNMATCHED rather
    than treated as failures.
    """
    try:
        raw_col = get_collection("raw_vendor_securities")
        if await raw_col.count_documents({}, limit=1) == 0:
            return {"error": "raw_vendor_securities is empty", "code": "NOT_FOUND"}

        by_cusip: dict[str, str] = {}
        by_isin: dict[str, str] = {}
        by_sedol: dict[str, str] = {}
        async for doc in get_collection("securities").find(
            {}, {"securityId": 1, "cusip": 1, "isin": 1, "listings.sedol": 1}
        ):
            sid = doc["securityId"]
            if doc.get("cusip"):
                by_cusip[doc["cusip"]] = sid
            if doc.get("isin"):
                by_isin[doc["isin"]] = sid
                embedded = cusip_from_isin(doc["isin"])
                if embedded:
                    by_cusip.setdefault(embedded, sid)
            for listing in doc.get("listings") or []:
                if listing.get("sedol"):
                    by_sedol[listing["sedol"]] = sid

        def _clean(value: Any) -> str | None:
            text = (str(value).strip() if value is not None else "")
            return None if text.upper() in {"", "N/A", "#N/A", "NA", "NULL", "NONE", "-"} else text

        matched = 0
        unmatched: list[dict] = []
        async for row in raw_col.find({}):
            isin = _clean(row.get("ISIN_CODE"))
            cusip = _clean(row.get("Cusip"))
            sedol = _clean(row.get("sedol"))
            security_id = (
                (by_isin.get(isin) if isin else None)
                or (by_cusip.get(cusip) if cusip else None)
                or (by_cusip.get(cusip.zfill(9)) if cusip else None)
                or (by_sedol.get(sedol) if sedol else None)
            )
            if security_id:
                matched += 1
            else:
                unmatched.append({
                    "vendorRef": row.get("Vendor_Ref"),
                    "reason": "UNMATCHED_VENDOR_RECORD",
                    "description": row.get("SecurityDesc"),
                    "cusip": cusip, "isin": isin, "sedol": sedol,
                })

        return {
            "vendorRows": matched + len(unmatched),
            "matched": matched,
            "unmatched": len(unmatched),
            "issues": unmatched[:20],
        }
    except pymongo.errors.PyMongoError:
        logger.exception("reconcile_vendor_securities failed")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def run_release_checks() -> dict:
    """Composite post-release verification, designed for a monitoring agent to
    poll: database reachability, per-collection population, raw feed
    freshness, and custody-feed reconciliation, rolled up to PASS/WARN/FAIL.
    """
    checks: list[dict[str, Any]] = []

    ping = await ping_database()
    checks.append({
        "name": "database_reachable",
        "status": "PASS" if ping.get("ok") else "FAIL",
        "detail": ping,
    })

    collections = await list_collections()
    if "error" in collections:
        checks.append({"name": "collections_populated", "status": "FAIL", "detail": collections})
    else:
        empty = [r["collection"] for r in collections["data"] if r["count"] == 0]
        checks.append({
            "name": "collections_populated",
            "status": "PASS" if not empty else "FAIL",
            "detail": {"empty": empty} if empty else {"collections": len(collections["data"])},
        })

    # Reconcile once; both freshness and reconciliation report on the cycle it
    # resolved, so the two checks can never disagree about which cycle is latest.
    recon = await reconcile_custody_feed()
    if "error" in recon:
        checks.append({"name": "custody_feed_freshness", "status": "FAIL", "detail": recon})
        checks.append({"name": "custody_reconciliation", "status": "FAIL", "detail": recon})
    else:
        cycle = recon["cycleDate"]
        try:
            age_days = (
                datetime.now(tz=timezone.utc)
                - datetime.strptime(cycle, "%Y%m%d").replace(tzinfo=timezone.utc)
            ).days
            fresh_status = "PASS" if age_days <= 3 else "WARN"
            fresh_detail: dict[str, Any] = {"latestCycle": cycle, "ageDays": age_days}
        except ValueError:
            fresh_status = "FAIL"
            fresh_detail = {"error": f"Unparseable cycle date {cycle!r}"}
        checks.append({
            "name": "custody_feed_freshness",
            "status": fresh_status,
            "detail": fresh_detail,
        })
        checks.append({
            "name": "custody_reconciliation",
            "status": "PASS" if recon["unmatched"] == 0 else "WARN",
            "detail": {k: recon[k] for k in ("cycleDate", "records", "matched", "unmatched")},
        })

    # ── ingestion checks ──
    # A feed that stopped arriving and a DLQ that is filling are the two
    # ingestion failures a release-monitoring agent must not miss.
    ingestion = await get_ingestion_status()
    if "error" in ingestion:
        checks.append({"name": "feed_freshness", "status": "FAIL", "detail": ingestion})
    else:
        stale = [f["topic"] for f in ingestion["data"] if f["status"] == "STALE"]
        never = [f["topic"] for f in ingestion["data"] if f["status"] == "NEVER_LANDED"]
        if stale:
            status = "WARN"
        elif never and len(never) == len(ingestion["data"]):
            # Nothing has ever landed: ingestion is not running at all.
            status = "FAIL"
        else:
            status = "PASS"
        checks.append({
            "name": "feed_freshness",
            "status": status,
            "detail": {"stale": stale, "neverLanded": never,
                       "feeds": len(ingestion["data"])},
        })

    dlq = await get_dlq_summary()
    if "error" in dlq:
        checks.append({"name": "dlq_empty", "status": "FAIL", "detail": dlq})
    else:
        checks.append({
            "name": "dlq_empty",
            "status": "PASS" if dlq["totalDeadLettered"] == 0 else "WARN",
            "detail": {"totalDeadLettered": dlq["totalDeadLettered"],
                       "topics": [e["topic"] for e in dlq["data"]]},
        })

    statuses = {c["status"] for c in checks}
    overall = "FAIL" if "FAIL" in statuses else ("WARN" if "WARN" in statuses else "PASS")
    return {"status": overall, "checks": checks}
