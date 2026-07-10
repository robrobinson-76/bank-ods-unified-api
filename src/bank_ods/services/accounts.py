from bank_ods.services.generic import get_many, get_one
from bank_ods.services.pagination import DEFAULT_LIMIT


async def get_account(account_id: str) -> dict:
    """Fetch a single account by its account ID."""
    return await get_one("accounts", {"accountId": account_id})


async def list_accounts(
    client_id: str | None = None,
    status: str | None = None,
    lei: str | None = None,
    domicile: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """List accounts with optional filters by client_id, status, lei (ISO 17442),
    and/or domicile (client's ISO 3166-1 alpha-2 country of domicile).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    query: dict = {}
    if client_id:
        query["client.clientId"] = client_id
    if status:
        query["status"] = status
    if lei:
        query["client.lei"] = lei
    if domicile:
        query["client.countryOfDomicile"] = domicile
    return await get_many("accounts", query, [("accountId", 1)], limit, cursor)
