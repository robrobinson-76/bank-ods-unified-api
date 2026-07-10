from __future__ import annotations

from typing import ClassVar

from .base import BankDocument, IndexSpec


class RawCustodyPosition(BankDocument):
    """Raw-tier: nightly custody position extract, loaded as received.

    One document per fixed-width detail record (record type 03) from the
    mainframe custody system's batch feed. Copybook field names are preserved
    verbatim (POS-SHR-QTY → POS_SHR_QTY); values keep their wire conventions:

    - Numerics are display (zoned) decimal: right-justified, zero-filled,
      implied decimal point (PIC 9(12)V9(4) → "0000000008505000" = 850.5).
    - POS_ACCR_INT is signed zoned decimal: the last character carries the
      sign as an overpunch ({, A–I positive; }, J–R negative), so
      "0000012345}" = -1234.50.
    - POS_BUS_DATE / POS_LAST_ACTVY_DT are CCYYMMDD strings; POS_PRICE_DT is
      julian CCYYDDD.
    - Alpha fields are uppercase, left-justified (trailing space fill removed
      at load); absent identifiers load as "".

    REC_ID is assigned by the feed loader: "<POS_BUS_DATE>-<sequence>", the
    record's position within its batch cycle.
    """

    COLLECTION: ClassVar[str] = "raw_custody_positions"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("REC_ID", {"unique": True}),
        ([("POS_BUS_DATE", 1), ("POS_ACCT_NBR", 1)], {}),
        ("POS_CUSIP_NBR", {}),
    ]
    ID_FIELD: ClassVar[str] = "REC_ID"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("REC_ID", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    REC_ID: str  # loader-assigned: "<POS_BUS_DATE>-<seq>"
    POS_REC_TYPE: str  # "03" = position detail
    POS_BUS_DATE: str  # CCYYMMDD batch cycle date
    POS_BANK_NBR: str  # processing bank/entity, zero-filled
    POS_BRANCH_CD: str
    POS_ACCT_NBR: str  # 12-char, right-justified zero-filled
    POS_ACCT_TYPE_CD: str  # CU=custody, PR=proprietary, OM=omnibus
    POS_CUSIP_NBR: str  # 9-char, all-or-nothing fill
    POS_ISIN_NBR: str  # 12-char or "" when not supplied
    POS_SEC_DESC: str  # uppercase, space-padding stripped
    POS_ASSET_CLS_CD: str  # EQ / FI / FND
    POS_REG_TYPE_CD: str  # registration type
    POS_LOC_CD: str  # safekeeping location: DTC / CDS / PHYS / SEG
    POS_SHR_QTY: str  # PIC 9(12)V9(4), implied decimal
    POS_SHR_QTY_PEND: str  # PIC 9(12)V9(4), implied decimal
    POS_MKT_PRICE: str  # PIC 9(3)V9(12), implied decimal
    POS_MKT_VALUE: str  # PIC 9(13)V99, implied decimal
    POS_ACCR_INT: str  # PIC S9(9)V99, sign overpunch in last char
    POS_PRICE_DT: str  # julian CCYYDDD
    POS_LAST_ACTVY_DT: str  # CCYYMMDD
    POS_PLEDGE_IND: str  # Y / N / ""
    POS_CCY_CD: str  # ISO 4217
    POS_SRC_SYS_ID: str  # originating mainframe application
