import logging
from typing import Any

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import date_window, serialize_doc
from bank_ods.services.pagination import DEFAULT_LIMIT, InvalidCursorError, paginate

logger = logging.getLogger("bank_ods.services")


async def get_transaction(transaction_id: str) -> dict:
    """Fetch a single transaction by its transaction ID."""
    try:
        col = get_collection("transactions")
        doc = await col.find_one({"transactionId": transaction_id}, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_transaction")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_transactions(
    account_id: str,
    from_date: str,
    to_date: str,
    status: str | None = None,
    transaction_type: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Query transactions for an account within an inclusive date range (YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("transactions")
        query: dict = {
            "accountId": account_id,
            "tradeDate": date_window(from_date, to_date),
        }
        if status:
            query["status"] = status
        if transaction_type:
            query["transactionType"] = transaction_type
        return await paginate(col, query, [("tradeDate", -1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_transactions")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_transaction_summary(
    account_id: str,
    from_date: str,
    to_date: str,
) -> dict:
    """Aggregate transaction count and netAmount grouped by transactionType and status."""
    try:
        col = get_collection("transactions")
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "accountId": account_id,
                    "tradeDate": date_window(from_date, to_date),
                }
            },
            {
                "$group": {
                    "_id": {"transactionType": "$transactionType", "status": "$status"},
                    "count": {"$sum": 1},
                    "totalNetAmount": {"$sum": "$netAmount"},
                }
            },
            {"$sort": {"_id.transactionType": 1, "_id.status": 1}},
        ]
        rows = await col.aggregate(pipeline).to_list(length=None)
        data = [
            serialize_doc({
                "transactionType": r["_id"]["transactionType"],
                "status": r["_id"]["status"],
                "count": r["count"],
                "totalNetAmount": r["totalNetAmount"],
            })
            for r in rows
        ]
        return {"data": data}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_transaction_summary")
        return {"error": "Database error", "code": "MONGO_ERROR"}
