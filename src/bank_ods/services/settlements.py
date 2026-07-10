import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import clamp_skip, date_window, day_range, serialize_doc

logger = logging.getLogger("bank_ods.services")

_PAGE_SIZE = 200


async def get_settlement(settlement_id: str) -> dict:
    """Fetch a settlement instruction by its settlement ID."""
    try:
        col = get_collection("settlements")
        doc = await col.find_one({"settlementId": settlement_id}, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_settlement")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_settlement_status(transaction_id: str) -> dict:
    """Look up the settlement linked to a transaction ID."""
    try:
        col = get_collection("settlements")
        doc = await col.find_one({"transactionId": transaction_id}, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_settlement_status")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_settlements(
    account_id: str,
    settlement_date: str,
    status: str | None = None,
    skip: int = 0,
) -> dict:
    """Query settlements for an account on a settlement date (whole calendar day, YYYY-MM-DD).

    count is the TOTAL number of matching documents; data is the requested page.
    """
    try:
        col = get_collection("settlements")
        query: dict = {
            "accountId": account_id,
            "settlementDate": day_range(settlement_date),
        }
        if status:
            query["status"] = status
        total = await col.count_documents(query)
        cursor = col.find(query, {"_id": 0}).skip(clamp_skip(skip)).limit(_PAGE_SIZE)
        docs = await cursor.to_list(length=_PAGE_SIZE)
        return {"count": total, "data": [serialize_doc(d) for d in docs]}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_settlements")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_settlement_fails(
    from_date: str,
    to_date: str,
    account_id: str | None = None,
    skip: int = 0,
) -> dict:
    """Find all FAILED settlements within an inclusive date window, optionally by account.

    count is the TOTAL number of matching documents; data is the requested page.
    """
    try:
        col = get_collection("settlements")
        query: dict = {
            "status": "FAILED",
            "settlementDate": date_window(from_date, to_date),
        }
        if account_id:
            query["accountId"] = account_id
        total = await col.count_documents(query)
        cursor = (
            col.find(query, {"_id": 0})
            .sort("settlementDate", -1)
            .skip(clamp_skip(skip))
            .limit(_PAGE_SIZE)
        )
        docs = await cursor.to_list(length=_PAGE_SIZE)
        return {"count": total, "data": [serialize_doc(d) for d in docs]}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_settlement_fails")
        return {"error": "Database error", "code": "MONGO_ERROR"}
