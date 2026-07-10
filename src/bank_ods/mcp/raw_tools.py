"""Raw-tier MCP tool group, generated from the registry.

Registered on the operations server (bank-ods-ops) — raw feed inspection is
an engineering/support activity, so it lives with the ops persona rather
than the consumer surface. Registration is a function of the target server
so the group stays reusable and independently gateable.

Tool names match the GraphQL fields and REST routes for the same entities
(get_raw_custody_position / list_raw_custody_positions, ...).
"""
from typing import Optional

from fastmcp import FastMCP

from bank_ods.models.base import BankDocument
from bank_ods.models.registry import ENTITIES_RAW, get_field_name, list_field_name
import bank_ods.services.raw as svc_raw


def _summary(model: type[BankDocument]) -> str:
    """First line of the model docstring — what this feed is."""
    return (model.__doc__ or model.__name__).strip().splitlines()[0]


def _register(server: FastMCP, model: type[BankDocument]) -> None:
    async def get_record(record_id: str) -> dict:
        return await svc_raw.get_raw_record(model, record_id)

    get_record.__name__ = get_field_name(model)
    get_record.__doc__ = (
        f"Fetch one document from {model.COLLECTION} by its {model.ID_FIELD}. "
        f"{_summary(model)} Values keep their source wire format — see the "
        f"collection's field conventions before interpreting numerics or dates."
    )
    server.tool()(get_record)

    async def list_records(limit: int = 50, cursor: Optional[str] = None) -> dict:
        return await svc_raw.list_raw_records(model, limit, cursor)

    list_records.__name__ = list_field_name(model)
    list_records.__doc__ = (
        f"List documents from {model.COLLECTION} in {model.ID_FIELD} order. "
        f"{_summary(model)}\n\n"
        "data is one page. If page_info.has_more is true, call again with "
        "cursor set to page_info.next_cursor EXACTLY as returned (opaque "
        "token — never construct or modify it). There is no total count; "
        "page until has_more is false."
    )
    server.tool()(list_records)


def register_raw_tools(server: FastMCP) -> None:
    """Register get/list tools for every raw entity in the registry."""
    for model in ENTITIES_RAW:
        _register(server, model)
