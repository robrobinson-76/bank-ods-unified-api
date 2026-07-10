from bank_ods.services.generic import get_many, get_one
from bank_ods.services.pagination import DEFAULT_LIMIT


async def get_security(security_id: str) -> dict:
    """Fetch a single security by its security ID."""
    return await get_one("securities", {"securityId": security_id})


async def get_security_by_sedol(sedol: str) -> dict:
    """Fetch the security carrying a given market-level SEDOL in its listings."""
    return await get_one("securities", {"listings.sedol": sedol})


async def list_securities(
    asset_class: str | None = None,
    ticker: str | None = None,
    status: str | None = None,
    sedol: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """List securities with optional filters by asset_class, ticker, status,
    and/or sedol (matches any listing's SEDOL).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    query: dict = {}
    if asset_class:
        query["assetClass"] = asset_class
    if ticker:
        query["ticker"] = ticker
    if status:
        query["status"] = status
    if sedol:
        query["listings.sedol"] = sedol
    return await get_many("securities", query, [("securityId", 1)], limit, cursor)
