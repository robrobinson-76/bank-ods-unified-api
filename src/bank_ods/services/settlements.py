import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import date_window, day_range, serialize_doc
from bank_ods.services.pagination import DEFAULT_LIMIT, InvalidCursorError, paginate

logger = logging.getLogger("bank_ods.services")


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
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Query settlements for an account on a settlement date (whole calendar day, YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("settlements")
        query: dict = {
            "accountId": account_id,
            "settlementDate": day_range(settlement_date),
        }
        if status:
            query["status"] = status
        return await paginate(col, query, [("settlementId", 1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_settlements")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_settlement_fails(
    from_date: str,
    to_date: str,
    account_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Find all FAILED settlements within an inclusive date window, optionally by account.

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("settlements")
        query: dict = {
            "status": "FAILED",
            "settlementDate": date_window(from_date, to_date),
        }
        if account_id:
            query["accountId"] = account_id
        return await paginate(col, query, [("settlementDate", -1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_settlement_fails")
        return {"error": "Database error", "code": "MONGO_ERROR"}
