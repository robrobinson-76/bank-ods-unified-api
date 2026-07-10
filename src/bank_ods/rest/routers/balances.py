from typing import Optional

from fastapi import APIRouter

import bank_ods.services.balances as svc
from bank_ods.rest.errors import check

router = APIRouter()


@router.get("/{account_id}/{currency}/projected")
async def get_projected_balance(account_id: str, currency: str, as_of_date: str):
    return check(await svc.get_projected_balance(account_id, currency, as_of_date))


@router.get("/{account_id}/{currency}")
async def get_cash_balance(account_id: str, currency: str, as_of_date: str):
    return check(await svc.get_cash_balance(account_id, currency, as_of_date))


@router.get("/{account_id}")
async def get_cash_balances(
    account_id: str, as_of_date: str, limit: int = 50, cursor: Optional[str] = None
):
    return check(await svc.get_cash_balances(account_id, as_of_date, limit, cursor))
