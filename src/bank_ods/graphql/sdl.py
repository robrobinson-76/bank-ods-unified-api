"""Generate GraphQL SDL from the Pydantic entity models.

Type blocks and list wrappers are generated for every active entity (see
models/registry.py). Query root fields come from two places:

  semantic tier — curated field definitions below (_SEMANTIC_QUERY_FIELDS):
      these carry per-entity filter arguments and composite get keys that
      model metadata doesn't capture.
  raw tier      — generated from each model's access metadata (ID_FIELD,
      UNFILTERED_LIST): a new raw entity added to the registry appears in
      the SDL with get/list fields automatically.
"""
from __future__ import annotations

import typing
from decimal import Decimal
from typing import Any, get_args, get_origin

from bank_ods import config
from bank_ods.models.base import BankDocument
from bank_ods.models.registry import (
    ENTITIES_RAW,
    active_entities,
    get_field_name,
    list_field_name,
)


# ── Python type → GraphQL scalar ─────────────────────────────────────────────

_SCALAR_MAP: dict[Any, str] = {
    str: "String",
    int: "Int",
    float: "Float",
    bool: "Boolean",
    Decimal: "Decimal",
}


def _py_to_gql(annotation: Any, nullable: bool = False) -> str:
    """Convert a Python type annotation to a GraphQL type string."""
    origin = get_origin(annotation)

    # Optional[T] → nullable T
    if origin is typing.Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        inner = _py_to_gql(args[0], nullable=True)
        return inner  # already nullable (no !)

    # list[T] or List[T]
    if origin is list:
        inner = _py_to_gql(get_args(annotation)[0])
        t = f"[{inner}]"
        return t if nullable else f"{t}!"

    # Literal[...] → String (all our literals are string enums)
    if origin is typing.Literal:
        t = "String"
        return t if nullable else f"{t}!"

    # Nested BankDocument subclass
    if isinstance(annotation, type) and issubclass(annotation, BankDocument):
        t = annotation.__name__
        return t if nullable else f"{t}!"

    # datetime → custom DateTime scalar
    from datetime import datetime
    if annotation is datetime:
        t = "DateTime"
        return t if nullable else f"{t}!"

    scalar = _SCALAR_MAP.get(annotation, "String")
    return scalar if nullable else f"{scalar}!"


# ── Type block generation ─────────────────────────────────────────────────────

def _collect_nested_types(model: type[BankDocument], seen: set) -> list[type[BankDocument]]:
    """DFS collect all nested BankDocument types referenced by a model's fields."""
    result = []
    for field_info in model.model_fields.values():
        nested = _find_nested(field_info.annotation)
        for cls in nested:
            if cls not in seen:
                seen.add(cls)
                result.extend(_collect_nested_types(cls, seen))
                result.append(cls)
    return result


def _find_nested(annotation: Any) -> list[type[BankDocument]]:
    origin = get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _find_nested(args[0]) if args else []
    if origin is list:
        return _find_nested(get_args(annotation)[0])
    if isinstance(annotation, type) and issubclass(annotation, BankDocument):
        return [annotation]
    return []


def _type_block(model: type[BankDocument]) -> str:
    lines = [f"type {model.__name__} {{"]
    for field_name, field_info in model.model_fields.items():
        gql_type = _py_to_gql(field_info.annotation)
        lines.append(f"  {field_name}: {gql_type}")
    lines.append("}")
    return "\n".join(lines)


def _list_type_block(model: type[BankDocument]) -> str:
    name = model.__name__
    return f"type {name}List {{\n  data: [{name}!]!\n  pageInfo: PageInfo!\n}}"


# ── Query root ────────────────────────────────────────────────────────────────

_SEMANTIC_QUERY_FIELDS = """
  # Accounts
  get_account(accountId: String!): Account
  list_accounts(clientId: String, status: String, lei: String, domicile: String, limit: Int, cursor: String): AccountList!

  # Securities
  get_security(securityId: String!): Security
  get_security_by_sedol(sedol: String!): Security
  list_securities(assetClass: String, ticker: String, status: String, sedol: String, limit: Int, cursor: String): SecurityList!

  # Transactions
  get_transaction(transactionId: String!): Transaction
  get_transactions(accountId: String!, fromDate: String!, toDate: String!, status: String, transactionType: String, limit: Int, cursor: String): TransactionList!
  get_transaction_summary(accountId: String!, fromDate: String!, toDate: String!): TransactionSummaryList!

  # Positions
  get_position(accountId: String!, securityId: String!, asOfDate: String!): Position
  get_positions(accountId: String!, asOfDate: String!, limit: Int, cursor: String): PositionList!
  get_position_history(accountId: String!, securityId: String!, fromDate: String!, toDate: String!, limit: Int, cursor: String): PositionList!

  # Settlements
  get_settlement(settlementId: String!): Settlement
  get_settlement_status(transactionId: String!): Settlement
  get_settlements(accountId: String!, settlementDate: String!, status: String, limit: Int, cursor: String): SettlementList!
  get_settlement_fails(fromDate: String!, toDate: String!, accountId: String, limit: Int, cursor: String): SettlementList!

  # Balances
  get_cash_balance(accountId: String!, currency: String!, asOfDate: String!): CashBalance
  get_cash_balances(accountId: String!, asOfDate: String!, limit: Int, cursor: String): CashBalanceList!
  get_projected_balance(accountId: String!, currency: String!, asOfDate: String!): ProjectedBalance
"""

_EXTRA_TYPES = """
type TransactionSummaryItem {
  transactionType: String!
  status: String!
  count: Int!
  totalNetAmount: Decimal!
}

type TransactionSummaryList {
  data: [TransactionSummaryItem!]!
}

type ProjectedBalance {
  accountId: String!
  currency: String!
  asOfDate: String!
  closingBalance: Decimal
  pendingCredits: Decimal
  pendingDebits: Decimal
  projectedBalance: Decimal
}
"""


def _generated_query_fields(model: type[BankDocument]) -> str:
    """Query fields derived from a model's access metadata."""
    lines = [f"  # {model.__name__} (generated from registry metadata)"]
    if model.ID_FIELD:
        lines.append(
            f"  {get_field_name(model)}({model.ID_FIELD}: String!): {model.__name__}"
        )
    if model.UNFILTERED_LIST:
        lines.append(
            f"  {list_field_name(model)}(limit: Int, cursor: String): {model.__name__}List!"
        )
    return "\n".join(lines)


def generate_sdl() -> str:
    """Build the full GraphQL SDL string from the entity models."""
    entities = active_entities()

    blocks: list[str] = [
        "scalar DateTime",
        "scalar Decimal",
        "",
        "type PageInfo {\n  hasMore: Boolean!\n  nextCursor: String\n}",
        "",
    ]

    # Collect and emit nested types (e.g. StatusHistoryEntry) first
    seen_nested: set = set()
    nested_types: list[type[BankDocument]] = []
    for model in entities:
        for cls in _collect_nested_types(model, seen_nested):
            nested_types.append(cls)
    for cls in nested_types:
        blocks.append(_type_block(cls))
        blocks.append("")

    # Entity types + list wrappers
    for model in entities:
        if not model.COLLECTION:
            continue
        blocks.append(_type_block(model))
        blocks.append("")
        blocks.append(_list_type_block(model))
        blocks.append("")

    query_fields = ""
    if config.EXPOSE_SEMANTIC_TIER:
        blocks.append(_EXTRA_TYPES)
        blocks.append("")
        query_fields += _SEMANTIC_QUERY_FIELDS
    if config.EXPOSE_RAW_TIER:
        for model in ENTITIES_RAW:
            query_fields += "\n" + _generated_query_fields(model) + "\n"
    if not query_fields:
        # An empty `type Query {}` is invalid SDL and would surface as an
        # opaque parser crash; refuse with an actionable message instead.
        raise RuntimeError(
            "GraphQL schema has no query fields: enable EXPOSE_SEMANTIC_TIER "
            "and/or EXPOSE_RAW_TIER, or disable the GraphQL transport."
        )
    blocks.append(f"type Query {{{query_fields}}}")

    return "\n".join(blocks)
