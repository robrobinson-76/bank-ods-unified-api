"""Generic collection-level fetch helpers — the base every entity service builds on.

Entity service modules remain the public API (typed parameters, curated
filters, entity-specific docstrings); these two helpers own the shared
mechanics: the error envelope contract, document serialization, and keyset
pagination. New collections get list/get behavior by calling these with a
collection name, a Mongo query, and a sort — no per-entity boilerplate.

Envelope contract (same as every entity service):
  get_one  -> serialized document, or {"error", "code": NOT_FOUND | MONGO_ERROR}
  get_many -> {"data": [...], "page_info": {"has_more", "next_cursor"}},
              or {"error", "code": INVALID_CURSOR | MONGO_ERROR}
"""
import logging

import pymongo.errors

from bank_ods.db.client import get_collection
from bank_ods.services._common import serialize_doc
from bank_ods.services.pagination import (
    DEFAULT_LIMIT,
    InvalidCursorError,
    SortSpec,
    paginate,
)

logger = logging.getLogger("bank_ods.services")


async def get_one(collection: str, query: dict) -> dict:
    """Fetch the single document matching ``query`` from ``collection``."""
    try:
        doc = await get_collection(collection).find_one(query, {"_id": 0})
        if doc is None:
            return {"error": "Not found", "code": "NOT_FOUND"}
        return serialize_doc(doc)
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_one(%s)", collection)
        return {"error": "Database error", "code": "MONGO_ERROR"}


async def get_many(
    collection: str,
    query: dict,
    sort: SortSpec,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Keyset-paginated find over ``collection`` (see services/pagination.py)."""
    try:
        return await paginate(get_collection(collection), query, sort, limit, cursor)
    except InvalidCursorError as e:
        return {"error": str(e), "code": "INVALID_CURSOR"}
    except pymongo.errors.PyMongoError:
        logger.exception("MongoDB error in get_many(%s)", collection)
        return {"error": "Database error", "code": "MONGO_ERROR"}
