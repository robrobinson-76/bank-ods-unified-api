"""Graphene types derived from the Pydantic entity models via graphene-pydantic.

Unlike Strawberry's integration, PydanticObjectType maps every field
automatically — including Literal[...] (→ String!) — so there are no per-field
declarations here. The trade-offs live elsewhere:

- PydanticObjectType installs an is_type_of check that rejects plain dicts, so
  resolvers must return Pydantic model instances (re-validation per request,
  same tax as Strawberry).
- graphene-pydantic's last release was 2024-02 and graphene's 2024-12; both
  are effectively dormant. See docs/REVIEW-strawberry-graphql.md.
"""
import graphene
from graphene_pydantic import PydanticObjectType

from bank_ods.models.account import Account as AccountModel
from bank_ods.models.security import Security as SecurityModel
from bank_ods.models.transaction import Transaction as TransactionModel
from bank_ods.models.position import Position as PositionModel
from bank_ods.models.settlement import Settlement as SettlementModel, StatusHistoryEntry as StatusHistoryEntryModel
from bank_ods.models.cash_balance import CashBalance as CashBalanceModel


# ── Entity types ──────────────────────────────────────────────────────────────

class AccountType(PydanticObjectType):
    class Meta:
        model = AccountModel
        name = "Account"


class SecurityType(PydanticObjectType):
    class Meta:
        model = SecurityModel
        name = "Security"


class TransactionType(PydanticObjectType):
    class Meta:
        model = TransactionModel
        name = "Transaction"


class PositionType(PydanticObjectType):
    class Meta:
        model = PositionModel
        name = "Position"


class StatusHistoryEntryType(PydanticObjectType):
    class Meta:
        model = StatusHistoryEntryModel
        name = "StatusHistoryEntry"


class SettlementType(PydanticObjectType):
    class Meta:
        model = SettlementModel
        name = "Settlement"

    # graphene-pydantic maps list[Model] as [StatusHistoryEntry]!; the contract
    # requires inner non-null [StatusHistoryEntry!]! — override explicitly.
    statusHistory = graphene.List(graphene.NonNull(StatusHistoryEntryType), required=True)


class CashBalanceType(PydanticObjectType):
    class Meta:
        model = CashBalanceModel
        name = "CashBalance"


# ── List wrappers ─────────────────────────────────────────────────────────────

def _list_wrapper(name: str, item_type) -> type:
    return type(name, (graphene.ObjectType,), {
        "count": graphene.Int(required=True),
        "data": graphene.List(graphene.NonNull(item_type), required=True),
    })


AccountList = _list_wrapper("AccountList", AccountType)
SecurityList = _list_wrapper("SecurityList", SecurityType)
TransactionList = _list_wrapper("TransactionList", TransactionType)
PositionList = _list_wrapper("PositionList", PositionType)
SettlementList = _list_wrapper("SettlementList", SettlementType)
CashBalanceList = _list_wrapper("CashBalanceList", CashBalanceType)


# ── Ad-hoc response types (no backing Pydantic model) ─────────────────────────

class TransactionSummaryItem(graphene.ObjectType):
    transactionType = graphene.String(required=True)
    status = graphene.String(required=True)
    count = graphene.Int(required=True)
    totalNetAmount = graphene.Decimal(required=True)


TransactionSummaryList = _list_wrapper("TransactionSummaryList", TransactionSummaryItem)


class ProjectedBalance(graphene.ObjectType):
    accountId = graphene.String(required=True)
    currency = graphene.String(required=True)
    asOfDate = graphene.String(required=True)
    closingBalance = graphene.Decimal()
    pendingCredits = graphene.Decimal()
    pendingDebits = graphene.Decimal()
    projectedBalance = graphene.Decimal()
