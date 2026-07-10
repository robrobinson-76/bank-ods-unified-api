from typing import Any, Callable

from ariadne import QueryType
from graphql import GraphQLError

from bank_ods import config
from bank_ods.models.base import BankDocument
from bank_ods.models.registry import ENTITIES_RAW, get_field_name, list_field_name
import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.positions as svc_positions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances
import bank_ods.services.raw as svc_raw

query = QueryType()


def _bind(name: str, resolver: Callable[..., Any]) -> None:
    query.field(name)(resolver)


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


# Resolvers must be bound only for fields the SDL actually emits: Ariadne
# rejects a resolver for a field not in the schema. Both tiers' SDL fields are
# gated (sdl.py), so both tiers' resolver registration is gated the same way —
# otherwise a tier-off deployment crashes at make_executable_schema().


# ── Semantic tier ─────────────────────────────────────────────────────────────

async def resolve_get_account(_obj, _info, accountId: str):
    return await svc_accounts.get_account(accountId)


async def resolve_list_accounts(_obj, _info, clientId=None, status=None, lei=None, domicile=None, limit=50, cursor=None):
    return _page(await svc_accounts.list_accounts(clientId, status, lei, domicile, limit, cursor))


async def resolve_get_security(_obj, _info, securityId: str):
    return await svc_securities.get_security(securityId)


async def resolve_get_security_by_sedol(_obj, _info, sedol: str):
    return await svc_securities.get_security_by_sedol(sedol)


async def resolve_list_securities(_obj, _info, assetClass=None, ticker=None, status=None, sedol=None, limit=50, cursor=None):
    return _page(await svc_securities.list_securities(assetClass, ticker, status, sedol, limit, cursor))


async def resolve_get_transaction(_obj, _info, transactionId: str):
    return await svc_transactions.get_transaction(transactionId)


async def resolve_get_transactions(_obj, _info, accountId, fromDate, toDate, status=None, transactionType=None, limit=50, cursor=None):
    return _page(await svc_transactions.get_transactions(accountId, fromDate, toDate, status, transactionType, limit, cursor))


async def resolve_get_transaction_summary(_obj, _info, accountId, fromDate, toDate):
    return _page(await svc_transactions.get_transaction_summary(accountId, fromDate, toDate))


async def resolve_get_position(_obj, _info, accountId, securityId, asOfDate):
    return await svc_positions.get_position(accountId, securityId, asOfDate)


async def resolve_get_positions(_obj, _info, accountId, asOfDate, limit=50, cursor=None):
    return _page(await svc_positions.get_positions(accountId, asOfDate, limit, cursor))


async def resolve_get_position_history(_obj, _info, accountId, securityId, fromDate, toDate, limit=50, cursor=None):
    return _page(await svc_positions.get_position_history(accountId, securityId, fromDate, toDate, limit, cursor))


async def resolve_get_settlement(_obj, _info, settlementId):
    return await svc_settlements.get_settlement(settlementId)


async def resolve_get_settlement_status(_obj, _info, transactionId):
    return await svc_settlements.get_settlement_status(transactionId)


async def resolve_get_settlements(_obj, _info, accountId, settlementDate, status=None, limit=50, cursor=None):
    return _page(await svc_settlements.get_settlements(accountId, settlementDate, status, limit, cursor))


async def resolve_get_settlement_fails(_obj, _info, fromDate, toDate, accountId=None, limit=50, cursor=None):
    return _page(await svc_settlements.get_settlement_fails(fromDate, toDate, accountId, limit, cursor))


async def resolve_get_cash_balance(_obj, _info, accountId, currency, asOfDate):
    return await svc_balances.get_cash_balance(accountId, currency, asOfDate)


async def resolve_get_cash_balances(_obj, _info, accountId, asOfDate, limit=50, cursor=None):
    return _page(await svc_balances.get_cash_balances(accountId, asOfDate, limit, cursor))


async def resolve_get_projected_balance(_obj, _info, accountId, currency, asOfDate):
    return await svc_balances.get_projected_balance(accountId, currency, asOfDate)


_SEMANTIC_RESOLVERS: dict[str, Callable[..., Any]] = {
    "get_account": resolve_get_account,
    "list_accounts": resolve_list_accounts,
    "get_security": resolve_get_security,
    "get_security_by_sedol": resolve_get_security_by_sedol,
    "list_securities": resolve_list_securities,
    "get_transaction": resolve_get_transaction,
    "get_transactions": resolve_get_transactions,
    "get_transaction_summary": resolve_get_transaction_summary,
    "get_position": resolve_get_position,
    "get_positions": resolve_get_positions,
    "get_position_history": resolve_get_position_history,
    "get_settlement": resolve_get_settlement,
    "get_settlement_status": resolve_get_settlement_status,
    "get_settlements": resolve_get_settlements,
    "get_settlement_fails": resolve_get_settlement_fails,
    "get_cash_balance": resolve_get_cash_balance,
    "get_cash_balances": resolve_get_cash_balances,
    "get_projected_balance": resolve_get_projected_balance,
}


# ── Raw tier (generated from the registry) ────────────────────────────────────

def _register_raw_resolvers(model: type[BankDocument]) -> None:
    async def resolve_get_raw(_obj, _info, **kwargs):
        return await svc_raw.get_raw_record(model, kwargs[model.ID_FIELD])

    async def resolve_list_raw(_obj, _info, limit=50, cursor=None):
        return _page(await svc_raw.list_raw_records(model, limit, cursor))

    _bind(get_field_name(model), resolve_get_raw)
    _bind(list_field_name(model), resolve_list_raw)


if config.EXPOSE_SEMANTIC_TIER:
    for _name, _resolver in _SEMANTIC_RESOLVERS.items():
        _bind(_name, _resolver)

if config.EXPOSE_RAW_TIER:
    for _model in ENTITIES_RAW:
        _register_raw_resolvers(_model)
