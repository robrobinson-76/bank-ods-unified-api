"""REST routers for raw-tier collections, generated from the registry.

One router per raw entity, mounted at /<collection>. The routes mirror the
generated GraphQL fields and MCP tools: unfiltered keyset-paginated list plus
get by the entity's natural key. Adding a raw entity to the registry is all
it takes to expose it here.
"""
from typing import Optional

from fastapi import APIRouter

import bank_ods.services.raw as svc_raw
from bank_ods.models.base import BankDocument
from bank_ods.models.registry import ENTITIES_RAW
from bank_ods.rest.errors import check


def _build_router(model: type[BankDocument]) -> APIRouter:
    router = APIRouter()

    @router.get("")
    async def list_records(limit: int = 50, cursor: Optional[str] = None) -> dict:
        return check(await svc_raw.list_raw_records(model, limit, cursor))

    @router.get("/{record_id}")
    async def get_record(record_id: str) -> dict:
        return check(await svc_raw.get_raw_record(model, record_id))

    return router


def build_raw_routers() -> list[tuple[APIRouter, str]]:
    """(router, mount prefix) for every raw entity in the registry."""
    return [(_build_router(model), f"/{model.COLLECTION}") for model in ENTITIES_RAW]
