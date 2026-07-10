import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import clamp_skip, date_window, day_range, serialize_doc

logger = logging.getLogger("bank_ods.services")

_PAGE_SIZE = 200


async def get_position(account_id: str, security_id: str, as_of_date: str) -> dict:
    """Fetch a single position for an account/security on a given date (YYYY-MM-DD)."""
    try:
        col = get_collection("positions")
        doc = await col.find_one(
            {
                "accountId": account_id,
                "securityId": security_id,
                "asOfDate": day_range(as_of_date),
            },
            {"_id": 0},
        )
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_position")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_positions(account_id: str, as_of_date: str, skip: int = 0) -> dict:
    """Fetch all positions for an account on a given date (YYYY-MM-DD).

    count is the TOTAL number of matching documents; data is the requested page.
    """
    try:
        col = get_collection("positions")
        query = {"accountId": account_id, "asOfDate": day_range(as_of_date)}
        total = await col.count_documents(query)
        cursor = col.find(query, {"_id": 0}).skip(clamp_skip(skip)).limit(_PAGE_SIZE)
        docs = await cursor.to_list(length=_PAGE_SIZE)
        return {"count": total, "data": [serialize_doc(d) for d in docs]}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_positions")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_position_history(
    account_id: str,
    security_id: str,
    from_date: str,
    to_date: str,
    skip: int = 0,
) -> dict:
    """Return EOD position history for an account/security over an inclusive date range.

    count is the TOTAL number of matching documents; data is the requested page.
    """
    try:
        col = get_collection("positions")
        query = {
            "accountId": account_id,
            "securityId": security_id,
            "asOfDate": date_window(from_date, to_date),
        }
        total = await col.count_documents(query)
        cursor = (
            col.find(query, {"_id": 0})
            .sort("asOfDate", 1)
            .skip(clamp_skip(skip))
            .limit(_PAGE_SIZE)
        )
        docs = await cursor.to_list(length=_PAGE_SIZE)
        return {"count": total, "data": [serialize_doc(d) for d in docs]}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_position_history")
        return {"error": "Database error", "code": "MONGO_ERROR"}
