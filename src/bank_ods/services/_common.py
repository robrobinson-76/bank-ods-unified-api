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


# ── Custody-feed identifier conversions ───────────────────────────────────────
# The mainframe custody feed keys accounts by a zero-filled 12-char number and
# carries CUSIPs embedded in US/CA ISINs. The seed loader needs the forward
# mapping and the reconciliation tool the reverse; keeping both here makes the
# feed's conventions a single source of truth (change once, not per-caller).

def custody_acct_nbr(account_id: str) -> str:
    """Semantic accountId (ACC-000007) -> zero-filled 12-char custody key."""
    return f"{int(account_id.removeprefix('ACC-')):012d}"


def account_id_from_custody(acct_nbr: str) -> str | None:
    """Custody key (000000000007) -> accountId (ACC-000007), or None when the
    raw value isn't the expected numeric form — i.e. a malformed feed record,
    which the reconciler classifies rather than crashing on."""
    try:
        return f"ACC-{int(acct_nbr):06d}"
    except (ValueError, TypeError):
        return None


def cusip_from_isin(isin: str | None) -> str | None:
    """The 9-char CUSIP embedded in a US/CA ISIN (positions 3-11), else None."""
    if isin and isin[:2] in ("US", "CA"):
        return isin[2:11]
    return None


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
