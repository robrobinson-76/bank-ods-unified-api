"""GraphQL layer tests."""
import pytest
from tests.conftest import gql_query

pytestmark = pytest.mark.asyncio


# ── Ops ───────────────────────────────────────────────────────────────────────

async def test_gql_health(gql_client):
    resp = await gql_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Accounts ──────────────────────────────────────────────────────────────────

async def test_gql_list_accounts(gql_client):
    result = await gql_query(
        gql_client,
        "{ list_accounts(limit: 3) { data { accountId client { clientName } status } pageInfo { hasMore nextCursor } } }",
    )
    assert "errors" not in result
    payload = result["data"]["list_accounts"]
    assert payload["data"]
    assert len(payload["data"]) <= 3
    assert set(payload["pageInfo"]) == {"hasMore", "nextCursor"}


async def test_gql_list_accounts_cursor(gql_client):
    """Following nextCursor resumes exactly where the previous page ended."""
    full = await gql_query(gql_client, "{ list_accounts(limit: 50) { data { accountId } } }")
    page1 = await gql_query(gql_client, "{ list_accounts(limit: 1) { data { accountId } pageInfo { hasMore nextCursor } } }")
    assert "errors" not in full
    assert "errors" not in page1
    full_ids = [d["accountId"] for d in full["data"]["list_accounts"]["data"]]
    if len(full_ids) > 1:
        pi = page1["data"]["list_accounts"]["pageInfo"]
        assert pi["hasMore"] is True
        page2 = await gql_query(
            gql_client,
            f'{{ list_accounts(limit: 1, cursor: "{pi["nextCursor"]}") {{ data {{ accountId }} }} }}',
        )
        assert "errors" not in page2
        assert page2["data"]["list_accounts"]["data"][0]["accountId"] == full_ids[1]


async def test_gql_invalid_cursor_error(gql_client):
    # data is null at the root (non-null list field), so Ariadne responds 400;
    # post directly instead of gql_query, which raises on non-2xx.
    resp = await gql_client.post(
        "/graphql/",
        json={"query": '{ list_accounts(cursor: "garbage") { data { accountId } } }'},
    )
    assert resp.status_code == 400
    result = resp.json()
    assert result["data"] is None
    assert result["errors"][0]["extensions"]["code"] == "INVALID_CURSOR"


async def test_gql_get_account(gql_client, first_account):
    result = await gql_query(
        gql_client,
        f'{{ get_account(accountId: "{first_account["accountId"]}") {{ accountId client {{ clientName }} }} }}',
    )
    assert "errors" not in result
    acct = result["data"]["get_account"]
    assert acct["accountId"] == first_account["accountId"]


# ── Transactions ───────────────────────────────────────────────────────────────

async def test_gql_get_transactions(gql_client, first_account):
    result = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{first_account["accountId"]}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 5) {{ data {{ transactionId status }} pageInfo {{ hasMore }} }} }}',
    )
    assert "errors" not in result
    payload = result["data"]["get_transactions"]
    assert "pageInfo" in payload
    assert len(payload["data"]) <= 5


async def test_gql_get_transactions_cursor(gql_client, first_account):
    """Following nextCursor resumes exactly where the previous page ended."""
    account_id = first_account["accountId"]
    base = f'accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01"'
    full = await gql_query(
        gql_client,
        f'{{ get_transactions({base}, limit: 200) {{ data {{ transactionId }} }} }}',
    )
    page1 = await gql_query(
        gql_client,
        f'{{ get_transactions({base}, limit: 1) {{ data {{ transactionId }} pageInfo {{ hasMore nextCursor }} }} }}',
    )
    assert "errors" not in full
    assert "errors" not in page1
    full_ids = [d["transactionId"] for d in full["data"]["get_transactions"]["data"]]
    if len(full_ids) > 1:
        pi = page1["data"]["get_transactions"]["pageInfo"]
        assert pi["hasMore"] is True
        page2 = await gql_query(
            gql_client,
            f'{{ get_transactions({base}, limit: 1, cursor: "{pi["nextCursor"]}") {{ data {{ transactionId }} }} }}',
        )
        assert "errors" not in page2
        assert page2["data"]["get_transactions"]["data"][0]["transactionId"] == full_ids[1]


# ── Settlements ───────────────────────────────────────────────────────────────

async def test_gql_settlement_fails(gql_client):
    result = await gql_query(
        gql_client,
        '{ get_settlement_fails(fromDate: "2020-01-01", toDate: "2030-01-01") { data { settlementId } pageInfo { hasMore } } }',
    )
    assert "errors" not in result
    assert isinstance(result["data"]["get_settlement_fails"]["data"], list)


# ── Balances ──────────────────────────────────────────────────────────────────

async def test_gql_cash_balances(gql_client, first_balance):
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    result = await gql_query(
        gql_client,
        f'{{ get_cash_balances(accountId: "{first_balance["accountId"]}", asOfDate: "{as_of}") {{ data {{ currency closingBalance }} }} }}',
    )
    assert "errors" not in result
    assert result["data"]["get_cash_balances"]["data"]
