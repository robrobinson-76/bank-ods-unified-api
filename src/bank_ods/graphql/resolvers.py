from ariadne import QueryType
from graphql import GraphQLError

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.positions as svc_positions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances

query = QueryType()


def _page(result: dict) -> dict:
    """Adapt a service list envelope for GraphQL.

    Error envelopes become GraphQLErrors (with extensions.code); the snake_case
    page_info is remapped to the camelCase pageInfo field of the SDL.
    """
    if "error" in result:
        raise GraphQLError(result["error"], extensions={"code": result.get("code")})
    out = {"data": result["data"]}
    if "page_info" in result:
        pi = result["page_info"]
        out["pageInfo"] = {"hasMore": pi["has_more"], "nextCursor": pi["next_cursor"]}
    return out


# ── Accounts ──────────────────────────────────────────────────────────────────

@query.field("get_account")
async def resolve_get_account(_obj, _info, accountId: str):
    return await svc_accounts.get_account(accountId)


@query.field("list_accounts")
async def resolve_list_accounts(_obj, _info, clientId=None, status=None, lei=None, domicile=None, limit=50, cursor=None):
    return _page(await svc_accounts.list_accounts(clientId, status, lei, domicile, limit, cursor))


# ── Securities ────────────────────────────────────────────────────────────────

@query.field("get_security")
async def resolve_get_security(_obj, _info, securityId: str):
    return await svc_securities.get_security(securityId)


@query.field("get_security_by_sedol")
async def resolve_get_security_by_sedol(_obj, _info, sedol: str):
    return await svc_securities.get_security_by_sedol(sedol)


@query.field("list_securities")
async def resolve_list_securities(_obj, _info, assetClass=None, ticker=None, status=None, sedol=None, limit=50, cursor=None):
    return _page(await svc_securities.list_securities(assetClass, ticker, status, sedol, limit, cursor))


# ── Transactions ───────────────────────────────────────────────────────────────

@query.field("get_transaction")
async def resolve_get_transaction(_obj, _info, transactionId: str):
    return await svc_transactions.get_transaction(transactionId)


@query.field("get_transactions")
async def resolve_get_transactions(_obj, _info, accountId, fromDate, toDate, status=None, transactionType=None, limit=50, cursor=None):
    return _page(await svc_transactions.get_transactions(accountId, fromDate, toDate, status, transactionType, limit, cursor))


@query.field("get_transaction_summary")
async def resolve_get_transaction_summary(_obj, _info, accountId, fromDate, toDate):
    return _page(await svc_transactions.get_transaction_summary(accountId, fromDate, toDate))


# ── Positions ─────────────────────────────────────────────────────────────────

@query.field("get_position")
async def resolve_get_position(_obj, _info, accountId, securityId, asOfDate):
    return await svc_positions.get_position(accountId, securityId, asOfDate)


@query.field("get_positions")
async def resolve_get_positions(_obj, _info, accountId, asOfDate, limit=50, cursor=None):
    return _page(await svc_positions.get_positions(accountId, asOfDate, limit, cursor))


@query.field("get_position_history")
async def resolve_get_position_history(_obj, _info, accountId, securityId, fromDate, toDate, limit=50, cursor=None):
    return _page(await svc_positions.get_position_history(accountId, securityId, fromDate, toDate, limit, cursor))


# ── Settlements ───────────────────────────────────────────────────────────────

@query.field("get_settlement")
async def resolve_get_settlement(_obj, _info, settlementId):
    return await svc_settlements.get_settlement(settlementId)


@query.field("get_settlement_status")
async def resolve_get_settlement_status(_obj, _info, transactionId):
    return await svc_settlements.get_settlement_status(transactionId)


@query.field("get_settlements")
async def resolve_get_settlements(_obj, _info, accountId, settlementDate, status=None, limit=50, cursor=None):
    return _page(await svc_settlements.get_settlements(accountId, settlementDate, status, limit, cursor))


@query.field("get_settlement_fails")
async def resolve_get_settlement_fails(_obj, _info, fromDate, toDate, accountId=None, limit=50, cursor=None):
    return _page(await svc_settlements.get_settlement_fails(fromDate, toDate, accountId, limit, cursor))


# ── Balances ──────────────────────────────────────────────────────────────────

@query.field("get_cash_balance")
async def resolve_get_cash_balance(_obj, _info, accountId, currency, asOfDate):
    return await svc_balances.get_cash_balance(accountId, currency, asOfDate)


@query.field("get_cash_balances")
async def resolve_get_cash_balances(_obj, _info, accountId, asOfDate, limit=50, cursor=None):
    return _page(await svc_balances.get_cash_balances(accountId, asOfDate, limit, cursor))


@query.field("get_projected_balance")
async def resolve_get_projected_balance(_obj, _info, accountId, currency, asOfDate):
    return await svc_balances.get_projected_balance(accountId, currency, asOfDate)
