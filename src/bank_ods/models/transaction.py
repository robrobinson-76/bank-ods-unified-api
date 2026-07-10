from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, Optional

from .base import BankDocument, IndexSpec


class Transaction(BankDocument):
    COLLECTION: ClassVar[str] = "transactions"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("transactionId", {"unique": True}),
        ([("accountId", 1), ("tradeDate", -1), ("_id", 1)], {}),
        ("status", {}),
        ("settlementDate", {}),
        ("securityId", {}),
    ]

    transactionId: str
    transactionType: Literal["BUY", "SELL", "DEPOSIT", "WITHDRAWAL", "TRANSFER_IN", "TRANSFER_OUT", "DIVIDEND", "FX"]
    tradeDate: datetime
    settlementDate: datetime
    accountId: str
    securityId: Optional[str] = None
    quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    currency: str
    grossAmount: Decimal
    fees: Decimal
    netAmount: Decimal
    fxRate: Decimal
    counterpartyId: str
    status: Literal["PENDING", "MATCHED", "SETTLED", "FAILED", "CANCELLED"]
    settlementRef: Optional[str] = None
    sourceSystem: str
    internalRef: str
    createdAt: datetime
    updatedAt: datetime
