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

    This entity is LATEST-STATE, not append-only: Vendor_Ref is the security's
    stable identifier, so a redelivery replaces the document rather than adding
    one. It is also fed by two channels — the intraday REST poll and the
    start-of-day snapshot file — which makes arrival order a correctness
    concern. SRC_UPDATED_AT is the ordering field that resolves it; see
    ORDERING_FIELD on BankDocument and docs/PATTERN-snapshot-and-stream.md.
    """

    COLLECTION: ClassVar[str] = "raw_vendor_securities"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("Vendor_Ref", {"unique": True}),
        ("Cusip", {"sparse": True}),
        ("SRC_UPDATED_AT", {}),
    ]
    ID_FIELD: ClassVar[str] = "Vendor_Ref"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("Vendor_Ref", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True
    ORDERING_FIELD: ClassVar[str] = "SRC_UPDATED_AT"

    Vendor_Ref: str  # vendor's stable row reference, e.g. "VND-000117"
    # Loader-assigned, like REC_ID on the custody feed: the vendor's own
    # LAST_UPD_TS parsed into a comparable ISO 8601 instant. The raw value is
    # kept verbatim below; this is the normalised copy the ordering guard uses,
    # because LAST_UPD_TS itself arrives in several formats and cannot be
    # compared as delivered.
    #
    # Optional by necessity, twice over. A record whose timestamp is missing or
    # in no recognised format genuinely has no ordering value, and inventing one
    # would either clobber newer data or make the record permanently ignored —
    # writers treat None as "insert if absent, never overwrite". It is also what
    # keeps the field BACKWARD compatible on the wire: a required field with no
    # default is a breaking schema change, which the registry rejects outright.
    SRC_UPDATED_AT: Optional[str] = None
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
