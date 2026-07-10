from typing import Optional

from fastapi import APIRouter

import bank_ods.services.securities as svc
from bank_ods.rest.errors import check

router = APIRouter()


@router.get("/{security_id}")
async def get_security(security_id: str):
    return check(await svc.get_security(security_id))


@router.get("")
async def list_securities(
    asset_class: Optional[str] = None,
    ticker: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
):
    return check(await svc.list_securities(asset_class, ticker, status, limit, skip))
