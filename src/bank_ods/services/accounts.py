import logging

import pymongo.errors
from bank_ods.db.client import get_collection
from bank_ods.services._common import serialize_doc
from bank_ods.services.pagination import DEFAULT_LIMIT, InvalidCursorError, paginate

logger = logging.getLogger("bank_ods.services")


async def get_account(account_id: str) -> dict:
    """Fetch a single account by its account ID."""
    try:
        col = get_collection("accounts")
        doc = await col.find_one({"accountId": account_id}, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_account")
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def list_accounts(
    client_id: str | None = None,
    status: str | None = None,
    lei: str | None = None,
    domicile: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """List accounts with optional filters by client_id, status, lei (ISO 17442),
    and/or domicile (client's ISO 3166-1 alpha-2 country of domicile).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        col = get_collection("accounts")
        query: dict = {}
        if client_id:
            query["client.clientId"] = client_id
        if status:
            query["status"] = status
        if lei:
            query["client.lei"] = lei
        if domicile:
            query["client.countryOfDomicile"] = domicile
        return await paginate(col, query, [("accountId", 1)], limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in list_accounts")
        return {"error": "Database error", "code": "MONGO_ERROR"}
