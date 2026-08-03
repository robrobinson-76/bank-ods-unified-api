"""Copybook layout and parser for the nightly custody position extract.

The layout table below IS the contract with the mainframe — the generator
(scripts/generate_custody_file.py) writes from it and the adapter reads from
it, so a field can never be written at one offset and read at another.

Parsing is deliberately mechanical: every value comes back as the verbatim
string the file carried (trailing space fill removed, nothing else). Zoned
decimals stay zoned, julian dates stay julian. Interpretation happens in
curation/decode.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

RECORD_LENGTH = 200

REC_TYPE_HEADER = "01"
REC_TYPE_DETAIL = "03"
REC_TYPE_TRAILER = "99"


@dataclass(frozen=True)
class Field:
    name: str
    length: int


def _offsets(fields: list[Field]) -> list[tuple[Field, int, int]]:
    out, pos = [], 0
    for f in fields:
        out.append((f, pos, pos + f.length))
        pos += f.length
    return out


# ── Record type 01: batch header ─────────────────────────────────────────────
HEADER_FIELDS = [
    Field("HDR_REC_TYPE", 2),
    Field("HDR_BUS_DATE", 8),        # CCYYMMDD cycle this file represents
    Field("HDR_SRC_SYS_ID", 8),
    Field("HDR_CREATED_TS", 14),     # CCYYMMDDHHMMSS
    Field("HDR_FILLER", 168),
]

# ── Record type 03: position detail ──────────────────────────────────────────
# Field order and widths mirror the copybook; names match RawCustodyPosition.
DETAIL_FIELDS = [
    Field("POS_REC_TYPE", 2),
    Field("POS_BUS_DATE", 8),
    Field("POS_BANK_NBR", 3),
    Field("POS_BRANCH_CD", 4),
    Field("POS_ACCT_NBR", 12),
    Field("POS_ACCT_TYPE_CD", 2),
    Field("POS_CUSIP_NBR", 9),
    Field("POS_ISIN_NBR", 12),
    Field("POS_SEC_DESC", 40),
    Field("POS_ASSET_CLS_CD", 3),
    Field("POS_REG_TYPE_CD", 1),
    Field("POS_LOC_CD", 4),
    Field("POS_SHR_QTY", 16),        # PIC 9(12)V9(4)
    Field("POS_SHR_QTY_PEND", 16),   # PIC 9(12)V9(4)
    Field("POS_MKT_PRICE", 15),      # PIC 9(3)V9(12)
    Field("POS_MKT_VALUE", 15),      # PIC 9(13)V99
    Field("POS_ACCR_INT", 11),       # PIC S9(9)V99, sign overpunch
    Field("POS_PRICE_DT", 7),        # julian CCYYDDD
    Field("POS_LAST_ACTVY_DT", 8),   # CCYYMMDD
    Field("POS_PLEDGE_IND", 1),
    Field("POS_CCY_CD", 3),
    Field("POS_SRC_SYS_ID", 8),
]

# ── Record type 99: batch trailer (control totals) ───────────────────────────
TRAILER_FIELDS = [
    Field("TRL_REC_TYPE", 2),
    Field("TRL_BUS_DATE", 8),
    Field("TRL_REC_COUNT", 9),        # detail records the mainframe wrote
    Field("TRL_TOT_SHR_QTY", 18),     # sum of POS_SHR_QTY, scale 4
    Field("TRL_TOT_MKT_VALUE", 18),   # sum of POS_MKT_VALUE, scale 2
    Field("TRL_FILLER", 145),
]

# Scales for the trailer's control totals (implied decimal, as on detail records).
TRAILER_QTY_SCALE = 4
TRAILER_VALUE_SCALE = 2

HEADER_LAYOUT = _offsets(HEADER_FIELDS)
DETAIL_LAYOUT = _offsets(DETAIL_FIELDS)
TRAILER_LAYOUT = _offsets(TRAILER_FIELDS)

for _layout, _name in (
    (HEADER_FIELDS, "header"), (DETAIL_FIELDS, "detail"), (TRAILER_FIELDS, "trailer")
):
    _total = sum(f.length for f in _layout)
    assert _total == RECORD_LENGTH, f"{_name} layout is {_total} bytes, expected {RECORD_LENGTH}"


class ParseError(ValueError):
    """A line could not be parsed as the record type it claims to be."""


def _unpack(line: str, layout: list[tuple[Field, int, int]]) -> dict[str, str]:
    if len(line) != RECORD_LENGTH:
        raise ParseError(f"expected {RECORD_LENGTH}-char record, got {len(line)}")
    # Trailing space fill is padding, not data — the model documents its removal.
    return {f.name: line[start:end].rstrip() for f, start, end in layout}


def parse_header(line: str) -> dict[str, str]:
    rec = _unpack(line, HEADER_LAYOUT)
    if rec["HDR_REC_TYPE"] != REC_TYPE_HEADER:
        raise ParseError(f"not a header record: type {rec['HDR_REC_TYPE']!r}")
    return rec


def parse_detail(line: str) -> dict[str, str]:
    rec = _unpack(line, DETAIL_LAYOUT)
    if rec["POS_REC_TYPE"] != REC_TYPE_DETAIL:
        raise ParseError(f"not a detail record: type {rec['POS_REC_TYPE']!r}")
    return rec


def parse_trailer(line: str) -> dict[str, str]:
    rec = _unpack(line, TRAILER_LAYOUT)
    if rec["TRL_REC_TYPE"] != REC_TYPE_TRAILER:
        raise ParseError(f"not a trailer record: type {rec['TRL_REC_TYPE']!r}")
    return rec


def pack(fields: list[Field], values: dict[str, str]) -> str:
    """Render one record from field values — the generator's writer side.

    Alpha fields are left-justified and space-filled; a value longer than its
    field is a programming error, not something to silently truncate.
    """
    out = []
    for f in fields:
        v = values.get(f.name, "")
        if len(v) > f.length:
            raise ParseError(f"{f.name} value {v!r} exceeds {f.length} chars")
        out.append(v.ljust(f.length))
    return "".join(out)


@dataclass
class DetailLine:
    """One detail record and where it came from, for error reporting."""
    line_no: int
    seq: int
    record: Optional[dict[str, str]]
    error: Optional[str] = None


def iter_records(lines: Iterator[str]) -> Iterator[tuple[str, int, str]]:
    """Yield (record_type, line_no, line) for non-empty lines."""
    for line_no, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        yield line[:2], line_no, line
