import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import serialize_doc
from bank_ods.services.pagination import DEFAULT_LIMIT, InvalidCursorError, paginate

logger = logging.getLogger("bank_ods.services")


async def get_security(security_id: str) -> dict:
    """Fetch a single security by its security ID."""
    try:
        col = get_collection("securities")
        doc = await col.find_one({"securityId": security_id}, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_security")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_security_by_sedol(sedol: str) -> dict:
    """Fetch the security carrying a given market-level SEDOL in its listings."""
    try:
        col = get_collection("securities")
        doc = await col.find_one({"listings.sedol": sedol}, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_security_by_sedol")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def list_securities(
    asset_class: str | None = None,
    ticker: str | None = None,
    status: str | None = None,
    sedol: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """List securities with optional filters by asset_class, ticker, status,
    and/or sedol (matches any listing's SEDOL).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("securities")
        query: dict = {}
        if asset_class:
            query["assetClass"] = asset_class
        if ticker:
            query["ticker"] = ticker
        if status:
            query["status"] = status
        if sedol:
            query["listings.sedol"] = sedol
        return await paginate(col, query, [("securityId", 1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in list_securities")
        return {"error": "Database error", "code": "MONGO_ERROR"}
