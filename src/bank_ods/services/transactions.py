import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import clamp_limit, clamp_skip, date_window, serialize_doc

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
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """Query transactions for an account within an inclusive date range (YYYY-MM-DD).

    count is the TOTAL number of matching documents; data is the requested page.
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
        total = await col.count_documents(query)
        n = clamp_limit(limit)
        cursor = col.find(query, {"_id": 0}).sort("tradeDate", -1).skip(clamp_skip(skip)).limit(n)
        docs = await cursor.to_list(length=n)
        return {"count": total, "data": [serialize_doc(d) for d in docs]}
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
        pipeline = [
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
        return {"count": len(data), "data": data}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_transaction_summary")
        return {"error": "Database error", "code": "MONGO_ERROR"}
