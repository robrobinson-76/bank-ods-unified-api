from __future__ import annotations

from typing import ClassVar, Optional

from .base import BankDocument, IndexSpec


class RawVendorSecurity(BankDocument):
    """Raw-tier: bespoke third-party instrument reference feed, loaded as received.

    One document per row of the vendor's delivery file. Column names are the
    vendor's own (mixed casing preserved), and values are kept exactly as
    delivered — no normalization happens before the raw tier:

    - Identifiers are inconsistently filled: Cusip may have lost a leading
      zero in a spreadsheet round-trip; ISIN_CODE may be "N/A"; sedol may be
      "#N/A" from a lookup failure.
    - ASSET_CLS mixes generations of the vendor's code list ("EQ", "Equity",
      "COM", "1").
    - Numbers are string-encoded (CPN_RATE "05.250" vs "5.25"; "0" on
      equities instead of blank).
    - Dates mix formats and sentinels: CCYYMMDD, MM/DD/YYYY, "99991231"
      (perpetual), "00000000".
    - Flags mix Y/N/U and blank; country and currency codes drift between
      ISO and long-form ("US", "USA", "usd", "GBp").

    Vendor_Ref is the vendor's own stable row reference and the only value
    the feed guarantees unique.
    """

    COLLECTION: ClassVar[str] = "raw_vendor_securities"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("Vendor_Ref", {"unique": True}),
        ("Cusip", {"sparse": True}),
    ]
    ID_FIELD: ClassVar[str] = "Vendor_Ref"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("Vendor_Ref", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    Vendor_Ref: str  # vendor's stable row reference, e.g. "VND-000117"
    Cusip: Optional[str] = None
    ISIN_CODE: Optional[str] = None
    sedol: Optional[str] = None
    TICKER: Optional[str] = None
    SecurityDesc: str
    Issuer_Name: Optional[str] = None
    ASSET_CLS: str
    CPN_RATE: Optional[str] = None
    MATURITY_DT: Optional[str] = None
    CCY: Optional[str] = None
    CNTRY_DOM: Optional[str] = None
    CALLABLE_FLG: Optional[str] = None
    ISSUE_STATUS: Optional[str] = None
    EXCH_CD: Optional[str] = None
    LAST_UPD_TS: Optional[str] = None  # vendor timestamp, format varies by delivery
