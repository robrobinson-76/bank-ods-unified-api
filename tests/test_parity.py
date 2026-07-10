"""Parity harness — asserts REST == GraphQL == service for each operation.

Each test case calls the service directly, then compares each transport's result.
"""
import pytest
from tests.conftest import gql_query

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_meta(d: dict) -> dict:
    """Remove fields that legitimately differ across transports (none currently)."""
    return d


# ── Account parity ────────────────────────────────────────────────────────────

async def test_parity_get_account(rest_client, gql_client, first_account):
    account_id = first_account["accountId"]

    service = await svc_accounts.get_account(account_id)

    rest_resp = await rest_client.get(f"/accounts/{account_id}")
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_account(accountId: "{account_id}") '
        f'{{ accountId accountName accountType client {{ clientId clientName lei }} baseCurrency status custodianBranch }} }}',
    )
    gql = gql_resp["data"]["get_account"]

    assert service["accountId"] == rest["accountId"] == gql["accountId"]
    assert service["client"]["clientName"] == rest["client"]["clientName"] == gql["client"]["clientName"]
    assert service["client"]["lei"] == rest["client"]["lei"] == gql["client"]["lei"]
    assert service["status"] == rest["status"] == gql["status"]


async def test_parity_list_accounts_page(rest_client, gql_client):
    """First page and its cursor are byte-identical across all three layers."""
    service = await svc_accounts.list_accounts(limit=2)

    rest_resp = await rest_client.get("/accounts", params={"limit": 2})
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        "{ list_accounts(limit: 2) { data { accountId } pageInfo { hasMore nextCursor } } }",
    )
    gql = gql_resp["data"]["list_accounts"]

    svc_ids = [d["accountId"] for d in service["data"]]
    assert svc_ids == [d["accountId"] for d in rest["data"]]
    assert svc_ids == [d["accountId"] for d in gql["data"]]
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


async def test_parity_cursor_follow_list_accounts(rest_client, gql_client):
    """Following the same cursor yields the same second page in all three layers."""
    page1 = await svc_accounts.list_accounts(limit=1)
    if not page1["page_info"]["has_more"]:
        pytest.skip("Need more than 1 account")
    cursor = page1["page_info"]["next_cursor"]

    service = await svc_accounts.list_accounts(limit=1, cursor=cursor)
    rest = (await rest_client.get("/accounts", params={"limit": 1, "cursor": cursor})).json()
    gql = (await gql_query(
        gql_client,
        f'{{ list_accounts(limit: 1, cursor: "{cursor}") {{ data {{ accountId }} }} }}',
    ))["data"]["list_accounts"]

    svc_ids = [d["accountId"] for d in service["data"]]
    assert svc_ids == [d["accountId"] for d in rest["data"]]
    assert svc_ids == [d["accountId"] for d in gql["data"]]


# ── Security parity ───────────────────────────────────────────────────────────

async def test_parity_get_security(rest_client, gql_client, first_security):
    security_id = first_security["securityId"]

    service = await svc_securities.get_security(security_id)

    rest_resp = await rest_client.get(f"/securities/{security_id}")
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_security(securityId: "{security_id}") '
        f'{{ securityId isin figi assetClass status listings {{ sedol micCode tradedCurrency }} }} }}',
    )
    gql = gql_resp["data"]["get_security"]

    assert service["securityId"] == rest["securityId"] == gql["securityId"]
    assert service["isin"] == rest["isin"] == gql["isin"]
    svc_sedols = [lst["sedol"] for lst in service["listings"]]
    rest_sedols = [lst["sedol"] for lst in rest["listings"]]
    gql_sedols = [lst["sedol"] for lst in gql["listings"]]
    assert svc_sedols == rest_sedols == gql_sedols


async def test_parity_get_security_by_sedol(rest_client, gql_client, dual_listed_security):
    # Secondary listing's SEDOL must resolve to the same parent everywhere.
    sedol = dual_listed_security["listings"][1]["sedol"]

    service = await svc_securities.get_security_by_sedol(sedol)

    rest_resp = await rest_client.get(f"/securities/sedol/{sedol}")
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_security_by_sedol(sedol: "{sedol}") '
        f'{{ securityId listings {{ sedol primaryListing settlementLocation }} }} }}',
    )
    gql = gql_resp["data"]["get_security_by_sedol"]

    assert service["securityId"] == rest["securityId"] == gql["securityId"]
    assert service["securityId"] == dual_listed_security["securityId"]


async def test_parity_list_securities_sedol_filter(rest_client, gql_client, dual_listed_security):
    sedol = dual_listed_security["listings"][0]["sedol"]

    service = await svc_securities.list_securities(sedol=sedol)

    rest_resp = await rest_client.get("/securities", params={"sedol": sedol})
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ list_securities(sedol: "{sedol}") {{ data {{ securityId }} }} }}',
    )
    gql = gql_resp["data"]["list_securities"]

    assert len(service["data"]) == len(rest["data"]) == len(gql["data"]) == 1
    assert service["data"][0]["securityId"] == dual_listed_security["securityId"]


# ── Transaction parity ────────────────────────────────────────────────────────

async def test_parity_get_transactions_page(rest_client, gql_client, first_account):
    """First page and its cursor are byte-identical across all three layers."""
    account_id = first_account["accountId"]

    service = await svc_transactions.get_transactions(
        account_id=account_id, from_date="2020-01-01", to_date="2030-01-01", limit=20
    )
    rest_resp = await rest_client.get("/transactions", params={
        "account_id": account_id, "from_date": "2020-01-01", "to_date": "2030-01-01", "limit": 20
    })
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 20) {{ data {{ transactionId }} pageInfo {{ hasMore nextCursor }} }} }}',
    )
    gql = gql_resp["data"]["get_transactions"]

    svc_ids = [d["transactionId"] for d in service["data"]]
    assert svc_ids == [d["transactionId"] for d in rest["data"]]
    assert svc_ids == [d["transactionId"] for d in gql["data"]]
    assert (
        service["page_info"]["next_cursor"]
        == rest["page_info"]["next_cursor"]
        == gql["pageInfo"]["nextCursor"]
    )


async def test_parity_cursor_follow_transactions(rest_client, gql_client, first_account):
    """Following the same cursor yields the same second page in all three layers."""
    account_id = first_account["accountId"]
    page1 = await svc_transactions.get_transactions(
        account_id=account_id, from_date="2020-01-01", to_date="2030-01-01", limit=1
    )
    if not page1["page_info"]["has_more"]:
        pytest.skip("Need more than 1 transaction")
    cursor = page1["page_info"]["next_cursor"]

    service = await svc_transactions.get_transactions(
        account_id=account_id, from_date="2020-01-01", to_date="2030-01-01",
        limit=1, cursor=cursor,
    )
    rest = (await rest_client.get("/transactions", params={
        "account_id": account_id, "from_date": "2020-01-01", "to_date": "2030-01-01",
        "limit": 1, "cursor": cursor,
    })).json()
    gql = (await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 1, cursor: "{cursor}") {{ data {{ transactionId }} }} }}',
    ))["data"]["get_transactions"]

    svc_ids = [d["transactionId"] for d in service["data"]]
    assert svc_ids == [d["transactionId"] for d in rest["data"]]
    assert svc_ids == [d["transactionId"] for d in gql["data"]]
    assert svc_ids != [d["transactionId"] for d in page1["data"]]


# ── Settlement parity ─────────────────────────────────────────────────────────

async def test_parity_settlement_fails_page(rest_client, gql_client):
    service = await svc_settlements.get_settlement_fails("2020-01-01", "2030-01-01")

    rest_resp = await rest_client.get(
        "/settlements/fails", params={"from_date": "2020-01-01", "to_date": "2030-01-01"}
    )
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        '{ get_settlement_fails(fromDate: "2020-01-01", toDate: "2030-01-01") { data { settlementId } pageInfo { hasMore nextCursor } } }',
    )
    gql = gql_resp["data"]["get_settlement_fails"]

    svc_ids = [d["settlementId"] for d in service["data"]]
    assert svc_ids == [d["settlementId"] for d in rest["data"]]
    assert svc_ids == [d["settlementId"] for d in gql["data"]]
    assert (
        service["page_info"]["next_cursor"]
        == rest["page_info"]["next_cursor"]
        == gql["pageInfo"]["nextCursor"]
    )


# ── Balance parity ────────────────────────────────────────────────────────────

async def test_parity_cash_balance(rest_client, gql_client, first_balance):
    account_id = first_balance["accountId"]
    currency = first_balance["currency"]
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")

    service = await svc_balances.get_cash_balance(account_id, currency, as_of)

    rest_resp = await rest_client.get(
        f"/balances/{account_id}/{currency}", params={"as_of_date": as_of}
    )
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_cash_balance(accountId: "{account_id}", currency: "{currency}", asOfDate: "{as_of}") '
        f'{{ accountId currency closingBalance projectedBalance }} }}',
    )
    gql = gql_resp["data"]["get_cash_balance"]

    assert service["closingBalance"] == rest["closingBalance"] == gql["closingBalance"]
    assert service["accountId"] == rest["accountId"] == gql["accountId"]


async def test_parity_projected_balance(rest_client, gql_client, first_balance):
    account_id = first_balance["accountId"]
    currency = first_balance["currency"]
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")

    service = await svc_balances.get_projected_balance(account_id, currency, as_of)

    rest_resp = await rest_client.get(
        f"/balances/{account_id}/{currency}/projected", params={"as_of_date": as_of}
    )
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_projected_balance(accountId: "{account_id}", currency: "{currency}", asOfDate: "{as_of}") '
        f'{{ accountId currency projectedBalance closingBalance }} }}',
    )
    gql = gql_resp["data"]["get_projected_balance"]

    assert service["projectedBalance"] == rest["projectedBalance"] == gql["projectedBalance"]
