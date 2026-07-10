from typing import Optional

from fastapi import APIRouter

import bank_ods.services.securities as svc
from bank_ods.rest.errors import check

router = APIRouter()


# Registered before /{security_id} so "sedol" is never captured as an ID.
@router.get("/sedol/{sedol}")
async def get_security_by_sedol(sedol: str):
    return check(await svc.get_security_by_sedol(sedol))


@router.get("/{security_id}")
async def get_security(security_id: str):
    return check(await svc.get_security(security_id))


@router.get("")
async def list_securities(
    asset_class: Optional[str] = None,
    ticker: Optional[str] = None,
    status: Optional[str] = None,
    sedol: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
):
    return check(await svc.list_securities(
        asset_class=asset_class, ticker=ticker, status=status, sedol=sedol,
        limit=limit, skip=skip,
    ))
