from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal, Optional

import pymongo

from .base import BankDocument, IndexSpec


class ClientMaster(BankDocument):
    """Denormalized client-master snapshot embedded on each account.

    The standard external linkage key is the LEI (ISO 17442); clientId is the
    internal key. All accounts of a client carry an identical snapshot.
    """

    COLLECTION: ClassVar[str] = ""  # embedded document — not a collection
    INDEXES: ClassVar[list[IndexSpec]] = []

    clientId: str
    clientName: str
    lei: str  # 20-char ISO 17442 Legal Entity Identifier
    countryOfDomicile: str  # ISO 3166-1 alpha-2
    countryOfIncorporation: str  # ISO 3166-1 alpha-2
    taxResidencies: list[str]  # FATCA/CRS jurisdictions, ISO 3166-1 alpha-2
    classification: Literal["RETAIL", "PROFESSIONAL", "ELIGIBLE_COUNTERPARTY"]
    kycStatus: Literal["APPROVED", "PENDING_REVIEW", "EXPIRED"]
    riskRating: Literal["LOW", "MEDIUM", "HIGH"]
    legalEntityType: Literal[
        "CORPORATION", "PARTNERSHIP", "FUND", "TRUST", "GOVERNMENT", "INDIVIDUAL"
    ]
    parentClientId: Optional[str] = None


class Account(BankDocument):
    COLLECTION: ClassVar[str] = "accounts"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("accountId", {"unique": True}),
        ("client.clientId", {}),
        ("client.lei", {}),
        ("status", {}),
    ]
    ID_FIELD: ClassVar[str] = "accountId"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("accountId", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    accountId: str
    accountName: str
    accountType: Literal["CUSTODY", "PROPRIETARY", "OMNIBUS"]
    client: ClientMaster
    baseCurrency: str
    status: Literal["ACTIVE", "SUSPENDED", "CLOSED"]
    openDate: datetime
    closeDate: Optional[datetime] = None
    custodianBranch: str
    createdAt: datetime
    updatedAt: datetime
