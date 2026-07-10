from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from bson import ObjectId
from bson.decimal128 import Decimal128


def parse_date(date_str: str) -> datetime:
    return datetime.fromisoformat(date_str)


def day_start(date_str: str) -> datetime:
    """Midnight at the start of the given calendar day."""
    return parse_date(date_str).replace(hour=0, minute=0, second=0, microsecond=0)


def day_after(date_str: str) -> datetime:
    """Midnight at the start of the following calendar day."""
    return day_start(date_str) + timedelta(days=1)


def day_range(date_str: str) -> dict:
    """Mongo filter matching any timestamp on the given calendar day (UTC).

    Documents carry intraday times (e.g. EOD snapshots at 16:00), so a date
    parameter must never be compared with equality — it means the whole day.
    """
    return {"$gte": day_start(date_str), "$lt": day_after(date_str)}


def date_window(from_date: str, to_date: str) -> dict:
    """Inclusive [from_date .. to_date] calendar-day window filter."""
    return {"$gte": day_start(from_date), "$lt": day_after(to_date)}


def clamp_limit(limit: int, maximum: int = 200) -> int:
    return min(max(1, limit), maximum)


def clamp_skip(skip: int) -> int:
    return max(0, skip)


def serialize_doc(doc: dict) -> dict:
    return _serialize({k: v for k, v in doc.items() if k != "_id"})


def _serialize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        # MongoDB stores UTC and returns naive datetimes; make UTC explicit
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, Decimal128):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    return obj
