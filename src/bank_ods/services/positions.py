from bank_ods.services._common import date_window, day_range
from bank_ods.services.generic import get_many, get_one
from bank_ods.services.pagination import DEFAULT_LIMIT


async def get_position(account_id: str, security_id: str, as_of_date: str) -> dict:
    """Fetch a single position for an account/security on a given date (YYYY-MM-DD)."""
    try:
        query = {
            "accountId": account_id,
            "securityId": security_id,
            "asOfDate": day_range(as_of_date),
        }
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    return await get_one("positions", query)


async def get_positions(
    account_id: str,
    as_of_date: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Fetch all positions for an account on a given date (YYYY-MM-DD).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        query = {"accountId": account_id, "asOfDate": day_range(as_of_date)}
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    return await get_many("positions", query, [("securityId", 1)], limit, cursor)


async def get_position_history(
    account_id: str,
    security_id: str,
    from_date: str,
    to_date: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """Return EOD position history for an account/security over an inclusive date range.

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    try:
        query = {
            "accountId": account_id,
            "securityId": security_id,
            "asOfDate": date_window(from_date, to_date),
        }
    except ValueError as e:
        return {"error": f"Invalid date: {e}", "code": "INVALID_DATE"}
    return await get_many("positions", query, [("asOfDate", 1)], limit, cursor)
