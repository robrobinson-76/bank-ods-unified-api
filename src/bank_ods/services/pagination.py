"""Keyset (seek) cursor pagination over Motor collections.

Every list service declares its sort as an ordered ``SortSpec``; ``paginate()``
appends an ``("_id", 1)`` tie-breaker so the total order is always unique, then
fetches ``limit + 1`` documents to detect whether more pages exist. The cursor
returned to clients is an opaque base64url token encoding the last row's sort
values; the next page resumes with a range query ($gt/$lt) instead of skip.

Constraint: every sort field must be present and non-null in every document of
the collection (all current sort fields are required model fields).
"""
import base64
import hashlib
import json
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.decimal128 import Decimal128
from motor.motor_asyncio import AsyncIOMotorCollection

from bank_ods.services._common import serialize_doc

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_CURSOR_VERSION = 1

SortSpec = list[tuple[str, int]]  # ordered (field, 1 | -1)


class InvalidCursorError(Exception):
    """Cursor is malformed, tampered with, or from a different query shape.

    Deliberately not a ValueError: services catch ValueError as INVALID_DATE.
    """


def clamp_limit(limit: int, maximum: int = MAX_LIMIT) -> int:
    return min(max(1, limit), maximum)


# ── Type-tagged value codec (BSON ↔ JSON round-trip) ──────────────────────────

def _tag(value: Any) -> list:
    if isinstance(value, ObjectId):
        return ["oid", str(value)]
    if isinstance(value, datetime):
        # Mongo returns naive UTC datetimes; naive isoformat round-trips exactly
        return ["dt", value.isoformat()]
    if isinstance(value, Decimal128):
        return ["dec", str(value)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return ["raw", value]
    raise TypeError(f"Unsupported sort-key type: {type(value).__name__}")


def _untag(pair: Any) -> Any:
    try:
        tag, value = pair
        if tag == "oid":
            return ObjectId(value)
        if tag == "dt":
            return datetime.fromisoformat(value)
        if tag == "dec":
            return Decimal128(value)
        if tag == "raw":
            return value
    except Exception as e:
        raise InvalidCursorError("Malformed cursor") from e
    raise InvalidCursorError("Malformed cursor")


# ── Opaque cursor encode/decode ───────────────────────────────────────────────

def _fingerprint(collection: str, sort: SortSpec) -> str:
    canon = json.dumps([collection, sort], separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:8]


def encode_cursor(collection: str, sort: SortSpec, values: list[Any]) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "f": _fingerprint(collection, sort),
        "k": [_tag(v) for v in values],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, collection: str, sort: SortSpec) -> list[Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        version, fp, keys = payload["v"], payload["f"], payload["k"]
    except InvalidCursorError:
        raise
    except Exception as e:
        raise InvalidCursorError("Malformed cursor") from e
    if version != _CURSOR_VERSION:
        raise InvalidCursorError("Unsupported cursor version")
    if fp != _fingerprint(collection, sort):
        raise InvalidCursorError("Cursor does not belong to this query")
    if not isinstance(keys, list) or len(keys) != len(sort):
        raise InvalidCursorError("Malformed cursor")
    return [_untag(p) for p in keys]


# ── Seek predicate ────────────────────────────────────────────────────────────

def seek_predicate(sort: SortSpec, values: list[Any]) -> dict:
    """Range query resuming strictly after the cursor position.

    {$or: [{k1 >v1}, {k1: v1, k2 >v2}, ...]} where > is $gt for ascending
    keys and $lt for descending keys.
    """
    branches = []
    for i, (field, direction) in enumerate(sort):
        clause: dict = {sort[j][0]: values[j] for j in range(i)}
        clause[field] = {("$gt" if direction == 1 else "$lt"): values[i]}
        branches.append(clause)
    return {"$or": branches}


# ── Single entry point ────────────────────────────────────────────────────────

def _get_path(doc: dict, dotted: str) -> Any:
    current: Any = doc
    for part in dotted.split("."):
        current = current[part]
    return current


async def paginate(
    col: AsyncIOMotorCollection,
    query: dict,
    sort: SortSpec,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Keyset-paginated find returning the full list envelope.

    Returns {"data": [...], "page_info": {"has_more": bool, "next_cursor": str|None}}.
    Raises InvalidCursorError (callers map it to code INVALID_CURSOR).
    """
    full_sort: SortSpec = list(sort) + [("_id", 1)]
    n = clamp_limit(limit)
    if cursor is not None:
        values = decode_cursor(cursor, col.name, full_sort)
        query = {"$and": [query, seek_predicate(full_sort, values)]}
    # No {"_id": 0} projection — the tie-breaker needs _id; serialize_doc
    # strips it from the output.
    docs = await col.find(query).sort(full_sort).limit(n + 1).to_list(length=n + 1)
    has_more = len(docs) > n
    docs = docs[:n]
    next_cursor = None
    if has_more:
        last = docs[-1]
        next_cursor = encode_cursor(
            col.name, full_sort, [_get_path(last, f) for f, _ in full_sort]
        )
    return {
        "data": [serialize_doc(d) for d in docs],
        "page_info": {"has_more": has_more, "next_cursor": next_cursor},
    }
