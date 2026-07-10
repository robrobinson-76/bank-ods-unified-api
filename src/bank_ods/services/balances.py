import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import day_range, serialize_doc
from bank_ods.services.pagination import DEFAULT_LIMIT, InvalidCursorError, paginate

logger = logging.getLogger("bank_ods.services")


async def get_cash_balance(account_id: str, currency: str, as_of_date: str) -> dict:
    """Fetch the cash balance for a specific account, currency, and date (YYYY-MM-DD)."""
    try:
        col = get_collection("cash_balances")
        doc = await col.find_one(
            {
                "accountId": account_id,
                "currency": currency,
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
        logger.exception("MongoDB error in get_cash_balance")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_cash_balances(
    account_id: str,
    as_of_date: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Fetch all currency balances for an account on a given date (YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("cash_balances")
        query = {"accountId": account_id, "asOfDate": day_range(as_of_date)}
        return await paginate(col, query, [("currency", 1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_cash_balances")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_projected_balance(account_id: str, currency: str, as_of_date: str) -> dict:
    """Return the projected balance (closing net of pending) for an account/currency/date."""
    try:
        col = get_collection("cash_balances")
        doc = await col.find_one(
            {
                "accountId": account_id,
                "currency": currency,
                "asOfDate": day_range(as_of_date),
            },
            {"_id": 0},
        )
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        s = serialize_doc(doc)
        return {
            "accountId": s["accountId"],
            "currency": s["currency"],
            "asOfDate": s["asOfDate"],
            "closingBalance": s.get("closingBalance"),
            "pendingCredits": s.get("pendingCredits"),
            "pendingDebits": s.get("pendingDebits"),
            "projectedBalance": s.get("projectedBalance"),
        }
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_projected_balance")
        return {"error": "Database error", "code": "MONGO_ERROR"}
