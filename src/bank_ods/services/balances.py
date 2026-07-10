from bank_ods.services._common import day_range
from bank_ods.services.generic import get_many, get_one
from bank_ods.services.pagination import DEFAULT_LIMIT


async def get_cash_balance(account_id: str, currency: str, as_of_date: str) -> dict:
    """Fetch the cash balance for a specific account, currency, and date (YYYY-MM-DD)."""
    try:
        query = {
            "accountId": account_id,
            "currency": currency,
            "asOfDate": day_range(as_of_date),
        }
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    return await get_one("cash_balances", query)


async def get_cash_balances(
    account_id: str,
    as_of_date: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Fetch all currency balances for an account on a given date (YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        query = {"accountId": account_id, "asOfDate": day_range(as_of_date)}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    return await get_many("cash_balances", query, [("currency", 1)], limit, cursor)


async def get_projected_balance(account_id: str, currency: str, as_of_date: str) -> dict:
    """Return the projected balance (closing net of pending) for an account/currency/date."""
    result = await get_cash_balance(account_id, currency, as_of_date)
    if "error" in result:
        return result
    return {
        "accountId": result["accountId"],
        "currency": result["currency"],
        "asOfDate": result["asOfDate"],
        "closingBalance": result.get("closingBalance"),
        "pendingCredits": result.get("pendingCredits"),
        "pendingDebits": result.get("pendingDebits"),
        "projectedBalance": result.get("projectedBalance"),
    }
