from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, Optional

from .base import BankDocument, IndexSpec


class StatusHistoryEntry(BankDocument):
    COLLECTION: ClassVar[str] = ""
    INDEXES: ClassVar[list[IndexSpec]] = []

    status: str
    timestamp: datetime


class Settlement(BankDocument):
    COLLECTION: ClassVar[str] = "settlements"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("settlementId", {"unique": True}),
        ("transactionId", {}),
        ([("accountId", 1), ("settlementDate", -1)], {}),
        ("status", {}),
    ]

    settlementId: str
    transactionId: str
    accountId: str
    securityId: Optional[str] = None
    settlementDate: datetime
    deliveryType: Literal["DVP", "FOP", "RVP", "RFP"]
    quantity: Optional[Decimal] = None
    currency: str
    settlementAmount: Decimal
    counterpartyId: str
    counterpartyAccount: str
    custodianAccount: str
    status: Literal["PENDING", "INSTRUCTED", "MATCHED", "SETTLED", "FAILED", "CANCELLED", "RECYCLED"]
    statusHistory: list[StatusHistoryEntry]
    failReason: Optional[str] = None
    csdRef: Optional[str] = None
    swiftRef: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime
