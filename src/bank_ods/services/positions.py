import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import date_window, day_range, serialize_doc
from bank_ods.services.pagination import DEFAULT_LIMIT, InvalidCursorError, paginate

logger = logging.getLogger("bank_ods.services")


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


async def get_positions(
    account_id: str,
    as_of_date: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Fetch all positions for an account on a given date (YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("positions")
        query = {"accountId": account_id, "asOfDate": day_range(as_of_date)}
        return await paginate(col, query, [("securityId", 1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
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
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Return EOD position history for an account/security over an inclusive date range.

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("positions")
        query = {
            "accountId": account_id,
            "securityId": security_id,
            "asOfDate": date_window(from_date, to_date),
        }
        return await paginate(col, query, [("asOfDate", 1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_position_history")
        return {"error": "Database error", "code": "MONGO_ERROR"}
