from typing import Optional

from fastapi import APIRouter

import bank_ods.services.settlements as svc
from bank_ods.rest.errors import check

router = APIRouter()


@router.get("/fails")
async def get_settlement_fails(
    from_date: str,
    to_date: str,
    account_id: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
):
    return check(await svc.get_settlement_fails(from_date, to_date, account_id, limit, cursor))


@router.get("/by-transaction/{transaction_id}")
async def get_settlement_status(transaction_id: str):
    return check(await svc.get_settlement_status(transaction_id))


@router.get("/{settlement_id}")
async def get_settlement(settlement_id: str):
    return check(await svc.get_settlement(settlement_id))


@router.get("")
async def get_settlements(
    account_id: str,
    settlement_date: str,
    status: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
):
    return check(await svc.get_settlements(account_id, settlement_date, status, limit, cursor))
