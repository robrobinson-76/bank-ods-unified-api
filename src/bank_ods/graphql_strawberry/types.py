"""Strawberry types derived from the Pydantic entity models.

Uses strawberry's official (experimental) pydantic integration:
`@strawberry.experimental.pydantic.type(model=...)`.

`all_fields=True` cannot be used: every `Literal[...]` field fails at
schema-build time with "Unexpected type 'typing.Literal[...]'", so each
Literal field is manually overridden as `str` and every other field must
then be listed explicitly as `strawberry.auto`. A field added to a Pydantic
model will NOT appear in this schema until it is also added here — unlike
the Ariadne SDL generator, which derives the schema from the ENTITIES
registry automatically.
"""
import typing

import strawberry
from strawberry import auto

from bank_ods.models.account import Account as AccountModel
from bank_ods.models.security import Security as SecurityModel
from bank_ods.models.transaction import Transaction as TransactionModel
from bank_ods.models.position import Position as PositionModel
from bank_ods.models.settlement import Settlement as SettlementModel, StatusHistoryEntry as StatusHistoryEntryModel
from bank_ods.models.cash_balance import CashBalance as CashBalanceModel


# ── Entity types ──────────────────────────────────────────────────────────────

@strawberry.experimental.pydantic.type(model=AccountModel, name="Account")
class AccountType:
    accountId: auto
    accountName: auto
    accountType: str  # Literal — unsupported by strawberry, overridden
    clientId: auto
    clientName: auto
    baseCurrency: auto
    status: str  # Literal
    openDate: auto
    closeDate: auto
    custodianBranch: auto
    createdAt: auto
    updatedAt: auto


@strawberry.experimental.pydantic.type(model=SecurityModel, name="Security")
class SecurityType:
    securityId: auto
    isin: auto
    cusip: auto
    ticker: auto
    description: auto
    assetClass: str  # Literal
    subType: auto
    currency: auto
    exchange: auto
    issuer: auto
    country: auto
    maturityDate: auto
    couponRate: auto
    status: str  # Literal
    createdAt: auto
    updatedAt: auto


@strawberry.experimental.pydantic.type(model=TransactionModel, name="Transaction")
class TransactionType:
    transactionId: auto
    transactionType: str  # Literal
    tradeDate: auto
    settlementDate: auto
    accountId: auto
    securityId: auto
    quantity: auto
    price: auto
    currency: auto
    grossAmount: auto
    fees: auto
    netAmount: auto
    fxRate: auto
    counterpartyId: auto
    status: str  # Literal
    settlementRef: auto
    sourceSystem: auto
    internalRef: auto
    createdAt: auto
    updatedAt: auto


@strawberry.experimental.pydantic.type(model=PositionModel, name="Position")
class PositionType:
    positionId: auto
    accountId: auto
    securityId: auto
    asOfDate: auto
    quantity: auto
    currency: auto
    costBasis: auto
    marketPrice: auto
    marketValue: auto
    unrealizedPnL: auto
    positionType: str  # Literal
    snapshotType: str  # Literal
    createdAt: auto
    updatedAt: auto


@strawberry.experimental.pydantic.type(model=StatusHistoryEntryModel, name="StatusHistoryEntry", all_fields=True)
class StatusHistoryEntryType:
    pass


@strawberry.experimental.pydantic.type(model=SettlementModel, name="Settlement")
class SettlementType:
    settlementId: auto
    transactionId: auto
    accountId: auto
    securityId: auto
    settlementDate: auto
    deliveryType: str  # Literal
    quantity: auto
    currency: auto
    settlementAmount: auto
    counterpartyId: auto
    counterpartyAccount: auto
    custodianAccount: auto
    status: str  # Literal
    statusHistory: auto
    failReason: auto
    csdRef: auto
    swiftRef: auto
    createdAt: auto
    updatedAt: auto


@strawberry.experimental.pydantic.type(model=CashBalanceModel, name="CashBalance")
class CashBalanceType:
    balanceId: auto
    accountId: auto
    currency: auto
    asOfDate: auto
    openingBalance: auto
    credits: auto
    debits: auto
    closingBalance: auto
    pendingCredits: auto
    pendingDebits: auto
    projectedBalance: auto
    snapshotType: str  # Literal
    createdAt: auto
    updatedAt: auto


# ── List wrappers ─────────────────────────────────────────────────────────────

@strawberry.type(name="AccountList")
class AccountList:
    count: int
    data: typing.List[AccountType]


@strawberry.type(name="SecurityList")
class SecurityList:
    count: int
    data: typing.List[SecurityType]


@strawberry.type(name="TransactionList")
class TransactionList:
    count: int
    data: typing.List[TransactionType]


@strawberry.type(name="PositionList")
class PositionList:
    count: int
    data: typing.List[PositionType]


@strawberry.type(name="SettlementList")
class SettlementList:
    count: int
    data: typing.List[SettlementType]


@strawberry.type(name="CashBalanceList")
class CashBalanceList:
    count: int
    data: typing.List[CashBalanceType]


# ── Ad-hoc response types (no backing Pydantic model) ─────────────────────────

@strawberry.type(name="TransactionSummaryItem")
class TransactionSummaryItem:
    transactionType: str
    status: str
    count: int
    totalNetAmount: float


@strawberry.type(name="TransactionSummaryList")
class TransactionSummaryList:
    count: int
    data: typing.List[TransactionSummaryItem]


@strawberry.type(name="ProjectedBalance")
class ProjectedBalance:
    accountId: str
    currency: str
    asOfDate: str
    closingBalance: typing.Optional[float]
    pendingCredits: typing.Optional[float]
    pendingDebits: typing.Optional[float]
    projectedBalance: typing.Optional[float]
