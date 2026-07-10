"""Unit and DB-backed tests for the keyset pagination helper."""
from datetime import datetime

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128

from bank_ods.services.pagination import (
    InvalidCursorError,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    paginate,
    seek_predicate,
)

_SORT = [("tradeDate", -1), ("_id", 1)]


# ── Cursor round-trip ─────────────────────────────────────────────────────────

def test_cursor_roundtrip_all_types():
    values = [
        datetime(2026, 3, 14, 16, 0, 0, 123456),  # naive UTC w/ microseconds
        ObjectId(),
        Decimal128("1234.5600"),
        "TXN-0001",
        42,
    ]
    sort = [("a", -1), ("b", 1), ("c", 1), ("d", 1), ("_id", 1)]
    cursor = encode_cursor("transactions", sort, values)
    assert isinstance(cursor, str) and "=" not in cursor
    decoded = decode_cursor(cursor, "transactions", sort)
    assert decoded == values
    assert isinstance(decoded[0], datetime)
    assert isinstance(decoded[1], ObjectId)
    assert isinstance(decoded[2], Decimal128)


def test_cursor_deterministic():
    values = [datetime(2026, 1, 2), ObjectId("665f00000000000000000001")]
    a = encode_cursor("transactions", _SORT, values)
    b = encode_cursor("transactions", _SORT, values)
    assert a == b


def test_cursor_rejects_other_collection():
    cursor = encode_cursor("transactions", _SORT, [datetime(2026, 1, 2), ObjectId()])
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, "settlements", _SORT)


def test_cursor_rejects_other_sort():
    cursor = encode_cursor("transactions", _SORT, [datetime(2026, 1, 2), ObjectId()])
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, "transactions", [("tradeDate", 1), ("_id", 1)])


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "not-base64!!!",
        "aGVsbG8",  # base64 of "hello" — not JSON
        "e30",  # base64 of "{}" — missing keys
        "W10",  # base64 of "[]" — wrong type
    ],
)
def test_cursor_rejects_garbage(garbage):
    with pytest.raises(InvalidCursorError):
        decode_cursor(garbage, "transactions", _SORT)


def test_cursor_rejects_tampered():
    cursor = encode_cursor("transactions", _SORT, [datetime(2026, 1, 2), ObjectId()])
    flipped = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    with pytest.raises(InvalidCursorError):
        decode_cursor(flipped, "transactions", _SORT)


def test_cursor_rejects_wrong_arity():
    cursor = encode_cursor("transactions", _SORT, [datetime(2026, 1, 2), ObjectId()])
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, "transactions", _SORT + [("x", 1)])


# ── Seek predicate ────────────────────────────────────────────────────────────

def test_seek_predicate_single_asc():
    assert seek_predicate([("accountId", 1)], ["ACC-1"]) == {
        "$or": [{"accountId": {"$gt": "ACC-1"}}]
    }


def test_seek_predicate_desc_with_tiebreaker():
    oid = ObjectId()
    dt = datetime(2026, 3, 14)
    assert seek_predicate(_SORT, [dt, oid]) == {
        "$or": [
            {"tradeDate": {"$lt": dt}},
            {"tradeDate": dt, "_id": {"$gt": oid}},
        ]
    }


def test_seek_predicate_three_keys_mixed():
    assert seek_predicate([("a", 1), ("b", -1), ("c", 1)], [1, 2, 3]) == {
        "$or": [
            {"a": {"$gt": 1}},
            {"a": 1, "b": {"$lt": 2}},
            {"a": 1, "b": 2, "c": {"$gt": 3}},
        ]
    }


# ── clamp_limit ───────────────────────────────────────────────────────────────

def test_clamp_limit():
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(50) == 50
    assert clamp_limit(9999) == 200


# ── DB-backed (seeded Mongo) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_walk_no_dups_no_gaps(db):
    """Walking every page at limit=3 yields the exact full set, in order."""
    col = db.accounts
    expected = [
        d["accountId"]
        async for d in col.find({}).sort([("accountId", 1), ("_id", 1)])
    ]
    seen: list[str] = []
    cursor = None
    for _ in range(1000):  # hard stop against infinite loops
        page = await paginate(col, {}, [("accountId", 1)], limit=3, cursor=cursor)
        seen.extend(a["accountId"] for a in page["data"])
        if not page["page_info"]["has_more"]:
            assert page["page_info"]["next_cursor"] is None
            break
        assert page["page_info"]["next_cursor"]
        cursor = page["page_info"]["next_cursor"]
    assert seen == expected


@pytest.mark.asyncio
async def test_exact_multiple_of_limit(db):
    """When the result count is an exact multiple of limit, the last full page
    reports has_more and the following page is empty-adjacent (probe works)."""
    col = db.accounts
    total = await col.count_documents({})
    if total < 2:
        pytest.skip("Need at least 2 accounts")
    # Pick a limit that divides the total so the boundary is exact.
    limit = next((n for n in range(2, min(total, 200) + 1) if total % n == 0), total)
    pages = 0
    cursor = None
    while True:
        page = await paginate(col, {}, [("accountId", 1)], limit=limit, cursor=cursor)
        pages += 1
        if not page["page_info"]["has_more"]:
            break
        cursor = page["page_info"]["next_cursor"]
    assert pages == total // limit
    assert len(page["data"]) == limit  # final page is full, not empty


@pytest.mark.asyncio
async def test_empty_result(db):
    page = await paginate(db.accounts, {"accountId": "NO-SUCH"}, [("accountId", 1)])
    assert page["data"] == []
    assert page["page_info"] == {"has_more": False, "next_cursor": None}


@pytest.mark.asyncio
async def test_insert_mid_iteration_stability(db):
    """A doc inserted before the current position never causes re-reads."""
    col = db.accounts
    page1 = await paginate(col, {}, [("accountId", 1)], limit=2)
    if not page1["page_info"]["has_more"]:
        pytest.skip("Need more than 2 accounts")
    page1_ids = {a["accountId"] for a in page1["data"]}
    inserted = await col.insert_one({"accountId": "AAA-0000-STABILITY"})
    try:
        page2 = await paginate(
            col, {}, [("accountId", 1)], limit=2,
            cursor=page1["page_info"]["next_cursor"],
        )
        page2_ids = {a["accountId"] for a in page2["data"]}
        assert not page1_ids & page2_ids
        assert "AAA-0000-STABILITY" not in page2_ids  # sorts before the boundary
    finally:
        await col.delete_one({"_id": inserted.inserted_id})


@pytest.mark.asyncio
async def test_paginate_data_excludes_id(db):
    page = await paginate(db.accounts, {}, [("accountId", 1)], limit=1)
    assert page["data"] and "_id" not in page["data"][0]
