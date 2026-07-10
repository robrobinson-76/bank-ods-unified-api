"""Raw-tier record access — registry-driven, built on the generic helpers.

Raw entities have no curated filters; their whole access contract is the
model's metadata (ID_FIELD, DEFAULT_SORT), so two functions cover every raw
collection. Adding a raw entity to the registry is all it takes to expose it.
"""
from bank_ods.models.base import BankDocument
from bank_ods.services.generic import get_many, get_one
from bank_ods.services.pagination import DEFAULT_LIMIT


async def get_raw_record(model: type[BankDocument], record_id: str) -> dict:
    """Fetch a single raw record by the entity's natural key (model.ID_FIELD)."""
    return await get_one(model.COLLECTION, {model.ID_FIELD: record_id})


async def list_raw_records(
    model: type[BankDocument],
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """List a raw collection in its stable default order (model.DEFAULT_SORT).

    data is one page; while page_info.has_more, pass page_info.next_cursor
    back as cursor to fetch the next page.
    """
    return await get_many(model.COLLECTION, {}, model.DEFAULT_SORT, limit, cursor)
