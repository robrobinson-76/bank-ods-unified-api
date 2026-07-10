from bank_ods.services._common import date_window, day_range
from bank_ods.services.generic import get_many, get_one
from bank_ods.services.pagination import DEFAULT_LIMIT


async def get_settlement(settlement_id: str) -> dict:
    """Fetch a settlement instruction by its settlement ID."""
    return await get_one("settlements", {"settlementId": settlement_id})


async def get_settlement_status(transaction_id: str) -> dict:
    """Look up the settlement linked to a transaction ID."""
    return await get_one("settlements", {"transactionId": transaction_id})


async def get_settlements(
    account_id: str,
    settlement_date: str,
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Query settlements for an account on a settlement date (whole calendar day, YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        query: dict = {
            "accountId": account_id,
            "settlementDate": day_range(settlement_date),
        }
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    if status:
        query["status"] = status
    return await get_many("settlements", query, [("settlementId", 1)], limit, cursor)


async def get_settlement_fails(
    from_date: str,
    to_date: str,
    account_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Find all FAILED settlements within an inclusive date window, optionally by account.

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        query: dict = {
            "status": "FAILED",
            "settlementDate": date_window(from_date, to_date),
        }
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    if account_id:
        query["accountId"] = account_id
    return await get_many("settlements", query, [("settlementDate", -1)], limit, cursor)
