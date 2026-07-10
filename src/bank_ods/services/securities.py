import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import clamp_limit, clamp_skip, serialize_doc

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
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List securities with optional filters by asset_class, ticker, status,
    and/or sedol (matches any listing's SEDOL).

    count is the TOTAL number of matching documents; data is the requested page.
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
        total = await col.count_documents(query)
        n = clamp_limit(limit)
        cursor = col.find(query, {"_id": 0}).sort("securityId", 1).skip(clamp_skip(skip)).limit(n)
        docs = await cursor.to_list(length=n)
        return {"count": total, "data": [serialize_doc(d) for d in docs]}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in list_securities")
        return {"error": "Database error", "code": "MONGO_ERROR"}
