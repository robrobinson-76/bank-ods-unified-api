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


async def test_parity_list_accounts_count(rest_client, gql_client):
    service = await svc_accounts.list_accounts(limit=10)

    rest_resp = await rest_client.get("/accounts", params={"limit": 10})
    rest = rest_resp.json()

    gql_resp = await gql_query(gql_client, "{ list_accounts(limit: 10) { count } }")
    gql_count = gql_resp["data"]["list_accounts"]["count"]

    assert service["count"] == rest["count"] == gql_count


async def test_parity_skip_list_accounts(rest_client, gql_client):
    """All three layers return the same records when skip=1 is applied."""
    service = await svc_accounts.list_accounts(limit=20, skip=1)

    rest_resp = await rest_client.get("/accounts", params={"limit": 20, "skip": 1})
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        "{ list_accounts(limit: 20, skip: 1) { count data { accountId } } }",
    )
    gql = gql_resp["data"]["list_accounts"]

    assert service["count"] == rest["count"] == gql["count"]
    svc_ids = [d["accountId"] for d in service["data"]]
    rest_ids = [d["accountId"] for d in rest["data"]]
    gql_ids = [d["accountId"] for d in gql["data"]]
    assert svc_ids == rest_ids == gql_ids


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
        f'{{ list_securities(sedol: "{sedol}") {{ count data {{ securityId }} }} }}',
    )
    gql = gql_resp["data"]["list_securities"]

    assert service["count"] == rest["count"] == gql["count"] == 1
    assert service["data"][0]["securityId"] == dual_listed_security["securityId"]


# ── Transaction parity ────────────────────────────────────────────────────────

async def test_parity_get_transactions_count(rest_client, gql_client, first_account):
    account_id = first_account["accountId"]
    params = dict(account_id=account_id, from_date="2020-01-01", to_date="2030-01-01", limit=20)

    service = await svc_transactions.get_transactions(**params)

    rest_resp = await rest_client.get("/transactions", params={
        "account_id": account_id, "from_date": "2020-01-01", "to_date": "2030-01-01", "limit": 20
    })
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 20) {{ count }} }}',
    )
    gql_count = gql_resp["data"]["get_transactions"]["count"]

    assert service["count"] == rest["count"] == gql_count


async def test_parity_skip_transactions(rest_client, gql_client, first_account):
    """skip=1 returns identical counts and first-item IDs across all three layers."""
    account_id = first_account["accountId"]

    service = await svc_transactions.get_transactions(
        account_id=account_id, from_date="2020-01-01", to_date="2030-01-01", limit=20, skip=1
    )
    rest_resp = await rest_client.get("/transactions", params={
        "account_id": account_id, "from_date": "2020-01-01", "to_date": "2030-01-01",
        "limit": 20, "skip": 1,
    })
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 20, skip: 1) {{ count data {{ transactionId }} }} }}',
    )
    gql = gql_resp["data"]["get_transactions"]

    assert service["count"] == rest["count"] == gql["count"]
    if service["count"] > 0:
        assert service["data"][0]["transactionId"] == rest["data"][0]["transactionId"]
        assert rest["data"][0]["transactionId"] == gql["data"][0]["transactionId"]


# ── Settlement parity ─────────────────────────────────────────────────────────

async def test_parity_settlement_fails_count(rest_client, gql_client):
    service = await svc_settlements.get_settlement_fails("2020-01-01", "2030-01-01")

    rest_resp = await rest_client.get(
        "/settlements/fails", params={"from_date": "2020-01-01", "to_date": "2030-01-01"}
    )
    rest = rest_resp.json()

    gql_resp = await gql_query(
        gql_client,
        '{ get_settlement_fails(fromDate: "2020-01-01", toDate: "2030-01-01") { count } }',
    )
    gql_count = gql_resp["data"]["get_settlement_fails"]["count"]

    assert service["count"] == rest["count"] == gql_count


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
