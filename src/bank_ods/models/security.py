from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, Optional

from .base import BankDocument, IndexSpec


class Security(BankDocument):
    COLLECTION: ClassVar[str] = "securities"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("securityId", {"unique": True}),
        ("isin", {"unique": True, "sparse": True}),
        ("ticker", {}),
        ("assetClass", {}),
    ]

    securityId: str
    isin: Optional[str] = None
    cusip: Optional[str] = None
    ticker: Optional[str] = None
    description: str
    assetClass: Literal["EQUITY", "GOVT_BOND", "CORP_BOND", "FUND", "CASH"]
    subType: str
    currency: str
    exchange: Optional[str] = None
    issuer: str
    country: str
    maturityDate: Optional[datetime] = None
    couponRate: Optional[Decimal] = None
    status: Literal["ACTIVE", "MATURED", "DELISTED"]
    createdAt: datetime
    updatedAt: datetime
