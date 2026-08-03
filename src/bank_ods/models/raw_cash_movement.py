from __future__ import annotations

from typing import ClassVar

from .base import BankDocument, IndexSpec


class RawCashMovement(BankDocument):
    """Raw-tier: intraday cash movement drop, loaded as received.

    One document per row of the intraday cash CSV. Unlike the custody extract
    this feed is delimited rather than fixed-width, but the raw-tier rules are
    identical — column names and wire-format string values are preserved
    verbatim, and every interpretation happens in curation:

    - MOV_AMT is a signed plain-decimal string ("-1234.56"); the sign is a
      leading minus, not a zoned overpunch.
    - MOV_FILE_DATE is CCYYMMDD and MOV_FILE_TIME is HHMM, taken from the
      drop's file name — several drops land per business day.
    - MOV_VALUE_TS is the source's own timestamp format
      ("CCYYMMDD HH:MM:SS"), not ISO 8601.
    - MOV_TYPE_CD is the legacy movement code list (DEP/WDL/FEE/INT/DIV/FX).
    - Alpha fields are uppercase; absent values arrive as "".

    MOVEMENT_ID is assigned by the feed loader: "<batchId>-<sequence>", the
    row's position within its drop.
    """

    COLLECTION: ClassVar[str] = "raw_cash_movements"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("MOVEMENT_ID", {"unique": True}),
        ([("MOV_FILE_DATE", 1), ("MOV_ACCT_NBR", 1)], {}),
        ("MOV_ACCT_NBR", {}),
    ]
    ID_FIELD: ClassVar[str] = "MOVEMENT_ID"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("MOVEMENT_ID", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    MOVEMENT_ID: str  # loader-assigned: "<batchId>-<seq>"
    MOV_FILE_DATE: str  # CCYYMMDD, from the drop file name
    MOV_FILE_TIME: str  # HHMM, from the drop file name
    MOV_ACCT_NBR: str  # 12-char, right-justified zero-filled
    MOV_CCY_CD: str  # ISO 4217
    MOV_AMT: str  # signed plain decimal, e.g. "-1234.56"
    MOV_TYPE_CD: str  # DEP / WDL / FEE / INT / DIV / FX
    MOV_VALUE_TS: str  # "CCYYMMDD HH:MM:SS"
    MOV_NARRATIVE: str
    MOV_SRC_SYS_ID: str  # originating application
