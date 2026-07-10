from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, Optional

from pydantic import Field

from .base import BankDocument, IndexSpec


class Listing(BankDocument):
    """One market-level listing of a security.

    SEDOL is allocated per market of official listing (OPOL) and, since 2008,
    per traded currency on the same venue — so a dual-listed or multi-currency
    security carries one Listing (and one SEDOL) per market/currency line.
    """

    COLLECTION: ClassVar[str] = ""  # embedded document — not a collection
    INDEXES: ClassVar[list[IndexSpec]] = []

    sedol: str  # 7-char LSEG SEDOL (6 alphanumeric, no vowels + check digit)
    micCode: str  # ISO 10383 market segment MIC, e.g. XNGS
    operatingMic: str  # ISO 10383 operating MIC, e.g. XNAS
    exchangeName: Optional[str] = None
    tradedCurrency: str  # ISO 4217
    countryOfListing: str  # ISO 3166-1 alpha-2
    settlementLocation: str  # settlement CSD BIC, e.g. DTCYUS33
    localCode: Optional[str] = None  # exchange-local ticker
    primaryListing: bool
    status: Literal["ACTIVE", "SUSPENDED", "DELISTED"]


class Security(BankDocument):
    COLLECTION: ClassVar[str] = "securities"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("securityId", {"unique": True}),
        ("isin", {"unique": True, "sparse": True}),
        ("ticker", {}),
        ("assetClass", {}),
        # Multikey unique index: uniqueness is enforced across documents only,
        # not within one document's listings array — seed data must guarantee
        # global uniqueness itself. Partial filter excludes securities with no
        # listings (e.g. bonds).
        (
            "listings.sedol",
            {
                "unique": True,
                "partialFilterExpression": {"listings.sedol": {"$exists": True}},
            },
        ),
    ]
    ID_FIELD: ClassVar[str] = "securityId"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("securityId", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    securityId: str
    isin: Optional[str] = None
    cusip: Optional[str] = None
    ticker: Optional[str] = None
    figi: Optional[str] = None  # OpenFIGI share-class FIGI (1:1 with ISIN)
    description: str
    assetClass: Literal["EQUITY", "GOVT_BOND", "CORP_BOND", "FUND", "CASH"]
    subType: str
    currency: str
    exchange: Optional[str] = None  # primary-listing exchange name convenience
    issuer: str
    country: str
    maturityDate: Optional[datetime] = None
    couponRate: Optional[Decimal] = None
    status: Literal["ACTIVE", "MATURED", "DELISTED"]
    listings: list[Listing] = Field(default_factory=list)
    createdAt: datetime
    updatedAt: datetime
