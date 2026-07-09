"""Graphene Query root — same 15 query fields as the Ariadne layer.

auto_camelcase=False (set in app.py) keeps snake_case query names and the
camelCase argument names of the existing contract.

PydanticObjectType rejects the service layer's plain dicts (its is_type_of
check requires model instances), so every resolver re-validates the dict into
the Pydantic model — the same per-request conversion tax as the Strawberry
layer. The service error envelope translates to None for single items and an
exception (GraphQL errors array) for lists.
"""
import graphene

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

from bank_ods.graphql_graphene.types import (
    AccountType, AccountList,
    TransactionType, TransactionList, TransactionSummaryItem, TransactionSummaryList,
    PositionType, PositionList,
    SettlementType, SettlementList,
    CashBalanceType, CashBalanceList,
    ProjectedBalance,
)


def _item(result: dict, model_cls):
    if "error" in result:
        return None
    return model_cls.model_validate(result)


def _entity_list(result: dict, model_cls, list_cls):
    if "error" in result:
        raise RuntimeError(result["error"])
    return list_cls(count=result["count"], data=[model_cls.model_validate(d) for d in result["data"]])


class Query(graphene.ObjectType):

    # ── Accounts ──────────────────────────────────────────────────────────

    get_account = graphene.Field(AccountType, accountId=graphene.String(required=True))
    list_accounts = graphene.Field(
        AccountList, required=True,
        clientId=graphene.String(), status=graphene.String(),
        limit=graphene.Int(), skip=graphene.Int(),
    )

    async def resolve_get_account(self, _info, accountId):
        return _item(await svc_accounts.get_account(accountId), AccountModel)

    async def resolve_list_accounts(self, _info, clientId=None, status=None, limit=20, skip=0):
        return _entity_list(await svc_accounts.list_accounts(clientId, status, limit, skip),
                            AccountModel, AccountList)

    # ── Transactions ──────────────────────────────────────────────────────

    get_transaction = graphene.Field(TransactionType, transactionId=graphene.String(required=True))
    get_transactions = graphene.Field(
        TransactionList, required=True,
        accountId=graphene.String(required=True),
        fromDate=graphene.String(required=True), toDate=graphene.String(required=True),
        status=graphene.String(), transactionType=graphene.String(),
        limit=graphene.Int(), skip=graphene.Int(),
    )
    get_transaction_summary = graphene.Field(
        TransactionSummaryList, required=True,
        accountId=graphene.String(required=True),
        fromDate=graphene.String(required=True), toDate=graphene.String(required=True),
    )

    async def resolve_get_transaction(self, _info, transactionId):
        return _item(await svc_transactions.get_transaction(transactionId), TransactionModel)

    async def resolve_get_transactions(self, _info, accountId, fromDate, toDate,
                                       status=None, transactionType=None, limit=50, skip=0):
        result = await svc_transactions.get_transactions(accountId, fromDate, toDate, status, transactionType, limit, skip)
        return _entity_list(result, TransactionModel, TransactionList)

    async def resolve_get_transaction_summary(self, _info, accountId, fromDate, toDate):
        result = await svc_transactions.get_transaction_summary(accountId, fromDate, toDate)
        if "error" in result:
            raise RuntimeError(result["error"])
        return TransactionSummaryList(
            count=result["count"],
            data=[TransactionSummaryItem(**item) for item in result["data"]],
        )

    # ── Positions ─────────────────────────────────────────────────────────

    get_position = graphene.Field(
        PositionType,
        accountId=graphene.String(required=True), securityId=graphene.String(required=True),
        asOfDate=graphene.String(required=True),
    )
    get_positions = graphene.Field(
        PositionList, required=True,
        accountId=graphene.String(required=True), asOfDate=graphene.String(required=True),
        skip=graphene.Int(),
    )
    get_position_history = graphene.Field(
        PositionList, required=True,
        accountId=graphene.String(required=True), securityId=graphene.String(required=True),
        fromDate=graphene.String(required=True), toDate=graphene.String(required=True),
        skip=graphene.Int(),
    )

    async def resolve_get_position(self, _info, accountId, securityId, asOfDate):
        return _item(await svc_positions.get_position(accountId, securityId, asOfDate), PositionModel)

    async def resolve_get_positions(self, _info, accountId, asOfDate, skip=0):
        return _entity_list(await svc_positions.get_positions(accountId, asOfDate, skip),
                            PositionModel, PositionList)

    async def resolve_get_position_history(self, _info, accountId, securityId, fromDate, toDate, skip=0):
        result = await svc_positions.get_position_history(accountId, securityId, fromDate, toDate, skip)
        return _entity_list(result, PositionModel, PositionList)

    # ── Settlements ───────────────────────────────────────────────────────

    get_settlement = graphene.Field(SettlementType, settlementId=graphene.String(required=True))
    get_settlement_status = graphene.Field(SettlementType, transactionId=graphene.String(required=True))
    get_settlements = graphene.Field(
        SettlementList, required=True,
        accountId=graphene.String(required=True), settlementDate=graphene.String(required=True),
        status=graphene.String(), skip=graphene.Int(),
    )
    get_settlement_fails = graphene.Field(
        SettlementList, required=True,
        fromDate=graphene.String(required=True), toDate=graphene.String(required=True),
        accountId=graphene.String(), skip=graphene.Int(),
    )

    async def resolve_get_settlement(self, _info, settlementId):
        return _item(await svc_settlements.get_settlement(settlementId), SettlementModel)

    async def resolve_get_settlement_status(self, _info, transactionId):
        return _item(await svc_settlements.get_settlement_status(transactionId), SettlementModel)

    async def resolve_get_settlements(self, _info, accountId, settlementDate, status=None, skip=0):
        result = await svc_settlements.get_settlements(accountId, settlementDate, status, skip)
        return _entity_list(result, SettlementModel, SettlementList)

    async def resolve_get_settlement_fails(self, _info, fromDate, toDate, accountId=None, skip=0):
        result = await svc_settlements.get_settlement_fails(fromDate, toDate, accountId, skip)
        return _entity_list(result, SettlementModel, SettlementList)

    # ── Balances ──────────────────────────────────────────────────────────

    get_cash_balance = graphene.Field(
        CashBalanceType,
        accountId=graphene.String(required=True), currency=graphene.String(required=True),
        asOfDate=graphene.String(required=True),
    )
    get_cash_balances = graphene.Field(
        CashBalanceList, required=True,
        accountId=graphene.String(required=True), asOfDate=graphene.String(required=True),
        skip=graphene.Int(),
    )
    get_projected_balance = graphene.Field(
        ProjectedBalance,
        accountId=graphene.String(required=True), currency=graphene.String(required=True),
        asOfDate=graphene.String(required=True),
    )

    async def resolve_get_cash_balance(self, _info, accountId, currency, asOfDate):
        return _item(await svc_balances.get_cash_balance(accountId, currency, asOfDate), CashBalanceModel)

    async def resolve_get_cash_balances(self, _info, accountId, asOfDate, skip=0):
        return _entity_list(await svc_balances.get_cash_balances(accountId, asOfDate, skip),
                            CashBalanceModel, CashBalanceList)

    async def resolve_get_projected_balance(self, _info, accountId, currency, asOfDate):
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
