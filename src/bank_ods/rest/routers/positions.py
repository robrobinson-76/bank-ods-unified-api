from typing import Optional

from fastapi import APIRouter

import bank_ods.services.positions as svc
from bank_ods.rest.errors import check

router = APIRouter()


@router.get("/{account_id}/{security_id}/history")
async def get_position_history(
    account_id: str,
    security_id: str,
    from_date: str,
    to_date: str,
    limit: int = 50,
    cursor: Optional[str] = None,
):
    return check(
        await svc.get_position_history(account_id, security_id, from_date, to_date, limit, cursor)
    )


@router.get("/{account_id}/{security_id}")
async def get_position(account_id: str, security_id: str, as_of_date: str):
    return check(await svc.get_position(account_id, security_id, as_of_date))


@router.get("/{account_id}")
async def get_positions(
    account_id: str, as_of_date: str, limit: int = 50, cursor: Optional[str] = None
):
    return check(await svc.get_positions(account_id, as_of_date, limit, cursor))
