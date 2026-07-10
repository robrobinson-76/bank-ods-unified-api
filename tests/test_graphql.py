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
        "{ list_accounts(limit: 3) { count data { accountId clientName status } } }",
    )
    assert "errors" not in result
    payload = result["data"]["list_accounts"]
    assert payload["count"] > 0
    assert len(payload["data"]) <= 3


async def test_gql_list_accounts_skip(gql_client):
    """skip=1 via GraphQL should return one fewer account than skip=0 when total > 1."""
    full = await gql_query(gql_client, "{ list_accounts(limit: 50, skip: 0) { count data { accountId } } }")
    skipped = await gql_query(gql_client, "{ list_accounts(limit: 50, skip: 1) { count data { accountId } } }")
    assert "errors" not in full
    assert "errors" not in skipped
    total = full["data"]["list_accounts"]["count"]
    if total > 1:
        assert skipped["data"]["list_accounts"]["count"] == total
        assert (
            skipped["data"]["list_accounts"]["data"][0]["accountId"]
            == full["data"]["list_accounts"]["data"][1]["accountId"]
        )


async def test_gql_get_account(gql_client, first_account):
    result = await gql_query(
        gql_client,
        f'{{ get_account(accountId: "{first_account["accountId"]}") {{ accountId clientName }} }}',
    )
    assert "errors" not in result
    acct = result["data"]["get_account"]
    assert acct["accountId"] == first_account["accountId"]


# ── Transactions ───────────────────────────────────────────────────────────────

async def test_gql_get_transactions(gql_client, first_account):
    result = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{first_account["accountId"]}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 5) {{ count data {{ transactionId status }} }} }}',
    )
    assert "errors" not in result
    payload = result["data"]["get_transactions"]
    assert "count" in payload
    assert len(payload["data"]) <= 5


async def test_gql_get_transactions_skip(gql_client, first_account):
    """skip=1 offsets results; item[0] of skipped page == item[1] of full page."""
    account_id = first_account["accountId"]
    full = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 200, skip: 0) {{ count data {{ transactionId }} }} }}',
    )
    skipped = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 200, skip: 1) {{ count data {{ transactionId }} }} }}',
    )
    assert "errors" not in full
    assert "errors" not in skipped
    total = full["data"]["get_transactions"]["count"]
    if total > 1:
        assert skipped["data"]["get_transactions"]["count"] == total
        assert (
            skipped["data"]["get_transactions"]["data"][0]["transactionId"]
            == full["data"]["get_transactions"]["data"][1]["transactionId"]
        )


# ── Settlements ───────────────────────────────────────────────────────────────

async def test_gql_settlement_fails(gql_client):
    result = await gql_query(
        gql_client,
        '{ get_settlement_fails(fromDate: "2020-01-01", toDate: "2030-01-01") { count } }',
    )
    assert "errors" not in result
    assert result["data"]["get_settlement_fails"]["count"] >= 0


# ── Balances ──────────────────────────────────────────────────────────────────

async def test_gql_cash_balances(gql_client, first_balance):
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    result = await gql_query(
        gql_client,
        f'{{ get_cash_balances(accountId: "{first_balance["accountId"]}", asOfDate: "{as_of}") {{ count data {{ currency closingBalance }} }} }}',
    )
    assert "errors" not in result
    assert result["data"]["get_cash_balances"]["count"] > 0
