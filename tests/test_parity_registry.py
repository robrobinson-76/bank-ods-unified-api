"""Registry-driven parity baseline — REST == GraphQL == service for every
exposed entity that supports it, derived from the entity registry.

test_parity.py keeps the entity-specific cases (filter arguments, composite
get keys, special queries); this module parametrizes over the registry so a
new entity picks up baseline cross-transport parity coverage just by being
registered — no new test file required.

Derivations (shared with SDL generation, REST mounting, and MCP tool naming):
  REST list       GET /<COLLECTION>
  REST get        GET /<COLLECTION>/<id>
  GraphQL list    list_field_name(model)(limit, cursor)
  GraphQL get     get_field_name(model)(<ID_FIELD>: ...)
  service         generic.get_many / generic.get_one
"""
import pytest

from bank_ods.models.registry import (
    active_entities,
    get_field_name,
    list_field_name,
)
from bank_ods.services import generic
from tests.conftest import gql_query

pytestmark = pytest.mark.asyncio

LISTABLE = [m for m in active_entities() if m.UNFILTERED_LIST]
GETTABLE = [m for m in LISTABLE if m.ID_FIELD]


@pytest.mark.parametrize("model", LISTABLE, ids=lambda m: m.COLLECTION)
async def test_baseline_list_parity(model, rest_client, gql_client):
    """First page, cursor token, and cursor-follow agree across all three layers."""
    id_field = model.ID_FIELD
    gql_field = list_field_name(model)

    service = await generic.get_many(model.COLLECTION, {}, model.DEFAULT_SORT, limit=2)
    rest = (await rest_client.get(f"/{model.COLLECTION}", params={"limit": 2})).json()
    gql = (await gql_query(
        gql_client,
        f"{{ {gql_field}(limit: 2) {{ data {{ {id_field} }} "
        f"pageInfo {{ hasMore nextCursor }} }} }}",
    ))["data"][gql_field]

    svc_ids = [d[id_field] for d in service["data"]]
    assert svc_ids == [d[id_field] for d in rest["data"]]
    assert svc_ids == [d[id_field] for d in gql["data"]]
    assert (
        service["page_info"]["has_more"]
        == rest["page_info"]["has_more"]
        == gql["pageInfo"]["hasMore"]
    )
    # The opaque cursor string itself must be identical — one shared implementation.
    assert (
        service["page_info"]["next_cursor"]
        == rest["page_info"]["next_cursor"]
        == gql["pageInfo"]["nextCursor"]
    )

    if not service["page_info"]["has_more"]:
        return
    cursor = service["page_info"]["next_cursor"]
    service2 = await generic.get_many(
        model.COLLECTION, {}, model.DEFAULT_SORT, limit=2, cursor=cursor
    )
    rest2 = (
        await rest_client.get(f"/{model.COLLECTION}", params={"limit": 2, "cursor": cursor})
    ).json()
    gql2 = (await gql_query(
        gql_client,
        f'{{ {gql_field}(limit: 2, cursor: "{cursor}") {{ data {{ {id_field} }} }} }}',
    ))["data"][gql_field]

    svc2_ids = [d[id_field] for d in service2["data"]]
    assert svc2_ids == [d[id_field] for d in rest2["data"]]
    assert svc2_ids == [d[id_field] for d in gql2["data"]]
    assert svc2_ids != svc_ids


@pytest.mark.parametrize("model", GETTABLE, ids=lambda m: m.COLLECTION)
async def test_baseline_get_parity(model, db, rest_client, gql_client):
    """Get-by-natural-key returns the same record through every layer."""
    doc = await db[model.COLLECTION].find_one({}, sort=model.DEFAULT_SORT)
    assert doc is not None, f"No {model.COLLECTION} found — run scripts/seed_data.py first"
    record_id = doc[model.ID_FIELD]

    service = await generic.get_one(model.COLLECTION, {model.ID_FIELD: record_id})
    rest = (await rest_client.get(f"/{model.COLLECTION}/{record_id}")).json()

    gql_field = get_field_name(model)
    gql = (await gql_query(
        gql_client,
        f'{{ {gql_field}({model.ID_FIELD}: "{record_id}") {{ {model.ID_FIELD} }} }}',
    ))["data"][gql_field]

    # Service and REST return the full serialized document — must be identical.
    assert service == rest
    assert service[model.ID_FIELD] == gql[model.ID_FIELD] == record_id


@pytest.mark.parametrize("model", GETTABLE, ids=lambda m: m.COLLECTION)
async def test_baseline_get_not_found(model, rest_client, gql_client):
    """Unknown natural keys map to NOT_FOUND / 404 / GraphQL error everywhere."""
    missing = "NO-SUCH-RECORD-000000"

    service = await generic.get_one(model.COLLECTION, {model.ID_FIELD: missing})
    assert service == {"error": "Not found", "code": "NOT_FOUND"}

    rest_resp = await rest_client.get(f"/{model.COLLECTION}/{missing}")
    assert rest_resp.status_code == 404

    gql_field = get_field_name(model)
    result = await gql_query(
        gql_client,
        f'{{ {gql_field}({model.ID_FIELD}: "{missing}") {{ {model.ID_FIELD} }} }}',
    )
    assert result["data"][gql_field] is None
