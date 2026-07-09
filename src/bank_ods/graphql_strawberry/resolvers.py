"""Strawberry Query root — same 15 query fields as the Ariadne layer.

Field names stay snake_case (get_account, list_accounts, ...) to match the
existing GraphQL contract; this requires StrawberryConfig(auto_camel_case=False)
in app.py.

Unlike Ariadne, Strawberry cannot resolve plain dicts against typed fields, so
every resolver re-validates the service-layer dict into the Pydantic model and
converts it to the Strawberry type via from_pydantic(). The service error
envelope ({"error", "code"}) also cannot pass through a typed resolver: single-
item queries translate it to None, list queries raise (surfaced in the GraphQL
errors array).
"""
from typing import Optional

import strawberry

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.positions as svc_positions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances

from bank_ods.models.account import Account as AccountModel
from bank_ods.models.transaction import Transaction as TransactionModel
from bank_ods.models.position import Position as PositionModel
from bank_ods.models.settlement import Settlement as SettlementModel
from bank_ods.models.cash_balance import CashBalance as CashBalanceModel

from bank_ods.graphql_strawberry.types import (
    AccountType, AccountList,
    TransactionType, TransactionList, TransactionSummaryItem, TransactionSummaryList,
    PositionType, PositionList,
    SettlementType, SettlementList,
    CashBalanceType, CashBalanceList,
    ProjectedBalance,
)


def _item(result: dict, model_cls, type_cls):
    """Service dict → Pydantic model → Strawberry type; error envelope → None."""
    if "error" in result:
        return None
    return type_cls.from_pydantic(model_cls.model_validate(result))


def _entity_list(result: dict, model_cls, type_cls, list_cls):
    """Service list envelope → Strawberry list wrapper; error envelope → raise."""
    if "error" in result:
        raise RuntimeError(result["error"])
    return list_cls(
        count=result["count"],
        data=[type_cls.from_pydantic(model_cls.model_validate(d)) for d in result["data"]],
    )


@strawberry.type
class Query:

    # ── Accounts ──────────────────────────────────────────────────────────

    @strawberry.field
    async def get_account(self, accountId: str) -> Optional[AccountType]:
        return _item(await svc_accounts.get_account(accountId), AccountModel, AccountType)

    @strawberry.field
    async def list_accounts(
        self, clientId: Optional[str] = None, status: Optional[str] = None,
        limit: Optional[int] = 20, skip: Optional[int] = 0,
    ) -> AccountList:
        result = await svc_accounts.list_accounts(clientId, status, limit if limit is not None else 20, skip or 0)
        return _entity_list(result, AccountModel, AccountType, AccountList)

    # ── Transactions ──────────────────────────────────────────────────────

    @strawberry.field
    async def get_transaction(self, transactionId: str) -> Optional[TransactionType]:
        return _item(await svc_transactions.get_transaction(transactionId), TransactionModel, TransactionType)

    @strawberry.field
    async def get_transactions(
        self, accountId: str, fromDate: str, toDate: str,
        status: Optional[str] = None, transactionType: Optional[str] = None,
        limit: Optional[int] = 50, skip: Optional[int] = 0,
    ) -> TransactionList:
        result = await svc_transactions.get_transactions(
            accountId, fromDate, toDate, status, transactionType,
            limit if limit is not None else 50, skip or 0,
        )
        return _entity_list(result, TransactionModel, TransactionType, TransactionList)

    @strawberry.field
    async def get_transaction_summary(self, accountId: str, fromDate: str, toDate: str) -> TransactionSummaryList:
        result = await svc_transactions.get_transaction_summary(accountId, fromDate, toDate)
        if "error" in result:
            raise RuntimeError(result["error"])
        return TransactionSummaryList(
            count=result["count"],
            data=[TransactionSummaryItem(**item) for item in result["data"]],
        )

    # ── Positions ─────────────────────────────────────────────────────────

    @strawberry.field
    async def get_position(self, accountId: str, securityId: str, asOfDate: str) -> Optional[PositionType]:
        return _item(await svc_positions.get_position(accountId, securityId, asOfDate), PositionModel, PositionType)

    @strawberry.field
    async def get_positions(self, accountId: str, asOfDate: str, skip: Optional[int] = 0) -> PositionList:
        result = await svc_positions.get_positions(accountId, asOfDate, skip or 0)
        return _entity_list(result, PositionModel, PositionType, PositionList)

    @strawberry.field
    async def get_position_history(
        self, accountId: str, securityId: str, fromDate: str, toDate: str, skip: Optional[int] = 0,
    ) -> PositionList:
        result = await svc_positions.get_position_history(accountId, securityId, fromDate, toDate, skip or 0)
        return _entity_list(result, PositionModel, PositionType, PositionList)

    # ── Settlements ───────────────────────────────────────────────────────

    @strawberry.field
    async def get_settlement(self, settlementId: str) -> Optional[SettlementType]:
        return _item(await svc_settlements.get_settlement(settlementId), SettlementModel, SettlementType)

    @strawberry.field
    async def get_settlement_status(self, transactionId: str) -> Optional[SettlementType]:
        return _item(await svc_settlements.get_settlement_status(transactionId), SettlementModel, SettlementType)

    @strawberry.field
    async def get_settlements(
        self, accountId: str, settlementDate: str, status: Optional[str] = None, skip: Optional[int] = 0,
    ) -> SettlementList:
        result = await svc_settlements.get_settlements(accountId, settlementDate, status, skip or 0)
        return _entity_list(result, SettlementModel, SettlementType, SettlementList)

    @strawberry.field
    async def get_settlement_fails(
        self, fromDate: str, toDate: str, accountId: Optional[str] = None, skip: Optional[int] = 0,
    ) -> SettlementList:
        result = await svc_settlements.get_settlement_fails(fromDate, toDate, accountId, skip or 0)
        return _entity_list(result, SettlementModel, SettlementType, SettlementList)

    # ── Balances ──────────────────────────────────────────────────────────

    @strawberry.field
    async def get_cash_balance(self, accountId: str, currency: str, asOfDate: str) -> Optional[CashBalanceType]:
        return _item(await svc_balances.get_cash_balance(accountId, currency, asOfDate), CashBalanceModel, CashBalanceType)

    @strawberry.field
    async def get_cash_balances(self, accountId: str, asOfDate: str, skip: Optional[int] = 0) -> CashBalanceList:
        result = await svc_balances.get_cash_balances(accountId, asOfDate, skip or 0)
        return _entity_list(result, CashBalanceModel, CashBalanceType, CashBalanceList)

    @strawberry.field
    async def get_projected_balance(self, accountId: str, currency: str, asOfDate: str) -> Optional[ProjectedBalance]:
        result = await svc_balances.get_projected_balance(accountId, currency, asOfDate)
        if "error" in result:
            return None
        return ProjectedBalance(
            accountId=result["accountId"],
            currency=result["currency"],
            asOfDate=result["asOfDate"],
            closingBalance=result.get("closingBalance"),
            pendingCredits=result.get("pendingCredits"),
            pendingDebits=result.get("pendingDebits"),
            projectedBalance=result.get("projectedBalance"),
        )
