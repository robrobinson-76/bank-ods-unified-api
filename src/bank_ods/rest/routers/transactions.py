from typing import Optional

from fastapi import APIRouter

import bank_ods.services.transactions as svc
from bank_ods.rest.errors import check

router = APIRouter()


@router.get("/summary")
async def get_transaction_summary(account_id: str, from_date: str, to_date: str):
    return check(await svc.get_transaction_summary(account_id, from_date, to_date))


@router.get("/{transaction_id}")
async def get_transaction(transaction_id: str):
    return check(await svc.get_transaction(transaction_id))


@router.get("")
async def get_transactions(
    account_id: str,
    from_date: str,
    to_date: str,
    status: Optional[str] = None,
    transaction_type: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
):
    return check(
        await svc.get_transactions(account_id, from_date, to_date, status, transaction_type, limit, cursor)
    )
