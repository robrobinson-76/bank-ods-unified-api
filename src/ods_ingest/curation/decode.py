"""Wire-format decoding for the mainframe custody feed.

The adapter captures these values verbatim; this module is where they become
typed. Keeping the two apart is what makes a mapping bug fixable by replaying
the raw tier instead of re-requesting the file from the source.

Conventions implemented here are the ones documented on the RawCustodyPosition
model: zoned (display) decimal with an implied decimal point, sign carried as
an overpunch in the final character, CCYYMMDD and julian CCYYDDD dates.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

# Overpunch tables: the final character encodes the last digit AND the sign.
OVERPUNCH_POS = "{ABCDEFGHI"  # digits 0-9, positive
OVERPUNCH_NEG = "}JKLMNOPQR"  # digits 0-9, negative


class DecodeError(ValueError):
    """A field could not be decoded — the record is malformed, not merely odd."""


def zoned_to_decimal(raw: str, scale: int, *, signed: bool = False) -> Decimal:
    """Display-decimal string to an exact Decimal.

        zoned_to_decimal("0000000008505000", 4)             -> Decimal("850.5")
        zoned_to_decimal("0000012345}", 2, signed=True)     -> Decimal("-1234.50")

    Exact by construction: the digits are shifted, never divided through a
    float, so no monetary value ever passes through IEEE-754.
    """
    if raw is None:
        raise DecodeError("missing zoned value")
    s = raw.strip()
    if not s:
        raise DecodeError("empty zoned value")

    negative = False
    if signed:
        last = s[-1]
        if last in OVERPUNCH_POS:
            s = s[:-1] + str(OVERPUNCH_POS.index(last))
        elif last in OVERPUNCH_NEG:
            s = s[:-1] + str(OVERPUNCH_NEG.index(last))
            negative = True
        elif not last.isdigit():
            raise DecodeError(f"bad sign overpunch {last!r} in {raw!r}")

    if not s.isdigit():
        raise DecodeError(f"non-numeric zoned value {raw!r}")

    value = Decimal(s).scaleb(-scale)
    return -value if negative else value


def decimal_to_zoned(value: Decimal | float | int, digits: int, scale: int,
                     *, signed: bool = False) -> str:
    """Inverse of zoned_to_decimal — used by the file generator.

    Kept beside the decoder so the two cannot drift; a round-trip test asserts
    they agree.
    """
    dec = Decimal(str(value))
    raw = int(abs(dec).scaleb(scale).to_integral_value())
    s = f"{raw:0{digits + scale}d}"
    if not signed:
        return s
    table = OVERPUNCH_NEG if dec < 0 else OVERPUNCH_POS
    return s[:-1] + table[int(s[-1])]


def ccyymmdd_to_datetime(raw: str) -> datetime:
    """CCYYMMDD to a UTC midnight datetime, matching the ODS date convention."""
    s = (raw or "").strip()
    if len(s) != 8 or not s.isdigit():
        raise DecodeError(f"bad CCYYMMDD date {raw!r}")
    try:
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), tzinfo=timezone.utc)
    except ValueError as exc:
        raise DecodeError(f"bad CCYYMMDD date {raw!r}: {exc}") from exc


def julian_to_datetime(raw: str) -> datetime:
    """Julian CCYYDDD (day-of-year) to a UTC midnight datetime."""
    s = (raw or "").strip()
    if len(s) != 7 or not s.isdigit():
        raise DecodeError(f"bad julian date {raw!r}")
    year, day = int(s[:4]), int(s[4:])
    if not 1 <= day <= 366:
        raise DecodeError(f"day-of-year out of range in {raw!r}")
    result = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)
    if result.year != year:
        raise DecodeError(f"day-of-year {day} does not exist in {year}")
    return result


def datetime_to_julian(dt: datetime) -> str:
    return f"{dt.year:04d}{dt.timetuple().tm_yday:03d}"


def optional_date(raw: Optional[str]) -> Optional[datetime]:
    """CCYYMMDD that may legitimately be absent or a zero sentinel."""
    s = (raw or "").strip()
    if not s or s in ("00000000", "99999999"):
        return None
    try:
        return ccyymmdd_to_datetime(s)
    except DecodeError:
        return None


# The vendor stamps LAST_UPD_TS in whichever format the delivering system used.
# All of these appear in real deliveries.
_VENDOR_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",   # 2026-01-30 04:12:44
    "%Y-%m-%dT%H:%M:%S",   # 2026-01-30T04:12:44
    "%Y-%m-%d",            # 2026-01-30
    "%d-%b-%y",            # 14-FEB-25
    "%d-%b-%Y",            # 14-FEB-2025
    "%m/%d/%Y",            # 02/14/2025
    "%Y%m%d",              # 20260130
)


def source_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Parse a vendor update timestamp into a comparable instant.

    Ordering a latest-state feed requires comparable timestamps, and this feed's
    own field is not comparable as delivered — "14-FEB-25" and
    "2026-01-30 04:12:44" do not sort against each other as strings. The adapter
    normalises once, at capture, and stores the result alongside the verbatim
    original.

    Returns None when the value is absent or in no recognised format; callers
    must decide what an unorderable record means rather than guessing a value,
    because inventing one (now(), epoch) would either clobber newer data or be
    permanently ignored.
    """
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in _VENDOR_TS_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def source_timestamp_iso(raw: Optional[str], *, default: Optional[str] = None) -> Optional[str]:
    """source_timestamp() rendered as a sortable ISO 8601 string, or None.

    ISO 8601 with a fixed UTC offset sorts identically as a string and as an
    instant, which lets the ordering guard be a plain `$lt` on a string field —
    no BSON date conversion needed in the raw tier, whose convention is that
    every value is a string.

    None (not "") when there is nothing to parse: an empty string would compare
    as older than every real timestamp, quietly turning "unknown" into
    "ancient". Writers must see the absence and fall back to insert-if-absent.
    """
    parsed = source_timestamp(raw)
    if parsed is None:
        return default
    return parsed.astimezone(timezone.utc).isoformat()


def plain_decimal(raw: Optional[str]) -> Optional[Decimal]:
    """A delimited feed's plain signed decimal ("-1234.56"); None when absent."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception as exc:  # noqa: BLE001 — InvalidOperation and friends
        raise DecodeError(f"non-numeric decimal {raw!r}") from exc
