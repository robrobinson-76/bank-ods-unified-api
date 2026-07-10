"""Graphene evaluation harness — asserts the side-by-side Graphene GraphQL app
(bank_ods.graphql_graphene) honors the same contract as the Ariadne app.

Mirrors tests/test_strawberry_parity.py; reuses its introspection helpers so
all three GraphQL layers are held to the identical schema contract.
"""
import pytest
from tests.conftest import gql_query
from tests.test_strawberry_parity import _CONTRACT_TYPES, _INTROSPECT, _contract_map

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances

pytestmark = pytest.mark.asyncio


# ── Ops ───────────────────────────────────────────────────────────────────────

async def test_gr_health(gr_client):
    resp = await gr_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Parity vs service, REST, and Ariadne ─────────────────────────────────────

async def test_gr_parity_get_account(rest_client, gql_client, gr_client, first_account):
    account_id = first_account["accountId"]
    q = (
        f'{{ get_account(accountId: "{account_id}") '
        f'{{ accountId accountName accountType baseCurrency status openDate closeDate custodianBranch createdAt updatedAt '
        f'client {{ clientId clientName lei countryOfDomicile countryOfIncorporation taxResidencies '
        f'classification kycStatus riskRating legalEntityType parentClientId }} }} }}'
    )

    service = await svc_accounts.get_account(account_id)
    rest = (await rest_client.get(f"/accounts/{account_id}")).json()
    ariadne = (await gql_query(gql_client, q))["data"]["get_account"]
    gr = (await gql_query(gr_client, q))["data"]["get_account"]

    assert gr == ariadne
    assert gr["client"] == service["client"] == rest["client"]
    for key in ("accountId", "status", "openDate", "createdAt"):
        assert service[key] == rest[key] == ariadne[key] == gr[key]


async def test_gr_parity_list_accounts_cursor(gql_client, gr_client):
    q1 = "{ list_accounts(limit: 2) { data { accountId } pageInfo { hasMore nextCursor } } }"
    ariadne1 = (await gql_query(gql_client, q1))["data"]["list_accounts"]
    gr1 = (await gql_query(gr_client, q1))["data"]["list_accounts"]
    assert gr1 == ariadne1

    cursor = ariadne1["pageInfo"]["nextCursor"]
    if cursor is None:
        pytest.skip("Need more than 2 accounts")
    q2 = f'{{ list_accounts(limit: 2, cursor: "{cursor}") {{ data {{ accountId }} pageInfo {{ hasMore nextCursor }} }} }}'
    ariadne2 = (await gql_query(gql_client, q2))["data"]["list_accounts"]
    gr2 = (await gql_query(gr_client, q2))["data"]["list_accounts"]
    assert gr2 == ariadne2
    assert gr2["data"] != gr1["data"]


async def test_gr_parity_transactions_cursor(gql_client, gr_client, first_account):
    account_id = first_account["accountId"]
    base = f'accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01"'
    q1 = f'{{ get_transactions({base}, limit: 1) {{ data {{ transactionId }} pageInfo {{ hasMore nextCursor }} }} }}'
    ariadne1 = (await gql_query(gql_client, q1))["data"]["get_transactions"]
    gr1 = (await gql_query(gr_client, q1))["data"]["get_transactions"]
    assert gr1 == ariadne1

    cursor = ariadne1["pageInfo"]["nextCursor"]
    if cursor is None:
        pytest.skip("Need more than 1 transaction")
    q2 = (
        f'{{ get_transactions({base}, limit: 20, cursor: "{cursor}") '
        f'{{ data {{ transactionId transactionType tradeDate netAmount status }} pageInfo {{ hasMore nextCursor }} }} }}'
    )
    ariadne2 = (await gql_query(gql_client, q2))["data"]["get_transactions"]
    gr2 = (await gql_query(gr_client, q2))["data"]["get_transactions"]
    assert gr2 == ariadne2


async def test_gr_parity_transaction_summary(gql_client, gr_client, first_account):
    account_id = first_account["accountId"]
    q = (
        f'{{ get_transaction_summary(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01") '
        f'{{ data {{ transactionType status count totalNetAmount }} }} }}'
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_transaction_summary"]
    gr = (await gql_query(gr_client, q))["data"]["get_transaction_summary"]
    assert gr == ariadne


async def test_gr_parity_settlement_nested_history(gql_client, gr_client, first_settled_txn):
    txn_id = first_settled_txn["transactionId"]
    q = (
        f'{{ get_settlement_status(transactionId: "{txn_id}") '
        f'{{ settlementId transactionId deliveryType status statusHistory {{ status timestamp }} settlementDate createdAt }} }}'
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_settlement_status"]
    gr = (await gql_query(gr_client, q))["data"]["get_settlement_status"]
    assert gr == ariadne


async def test_gr_parity_security_nested_listings(rest_client, gql_client, gr_client, dual_listed_security):
    """Security carries the nested listings list — market-level SEDOL records."""
    security_id = dual_listed_security["securityId"]
    q = (
        f'{{ get_security(securityId: "{security_id}") '
        f'{{ securityId isin figi assetClass status '
        f'listings {{ sedol micCode operatingMic exchangeName tradedCurrency '
        f'countryOfListing settlementLocation localCode primaryListing status }} }} }}'
    )
    service = await svc_securities.get_security(security_id)
    rest = (await rest_client.get(f"/securities/{security_id}")).json()
    ariadne = (await gql_query(gql_client, q))["data"]["get_security"]
    gr = (await gql_query(gr_client, q))["data"]["get_security"]

    assert gr == ariadne
    assert gr["listings"] == service["listings"] == rest["listings"]
    assert len(gr["listings"]) >= 2


async def test_gr_parity_get_security_by_sedol(gql_client, gr_client, dual_listed_security):
    sedol = dual_listed_security["listings"][1]["sedol"]
    q = (
        f'{{ get_security_by_sedol(sedol: "{sedol}") '
        f'{{ securityId listings {{ sedol tradedCurrency primaryListing }} }} }}'
    )
    service = await svc_securities.get_security_by_sedol(sedol)
    ariadne = (await gql_query(gql_client, q))["data"]["get_security_by_sedol"]
    gr = (await gql_query(gr_client, q))["data"]["get_security_by_sedol"]

    assert gr == ariadne
    assert gr["securityId"] == service["securityId"] == dual_listed_security["securityId"]


async def test_gr_parity_settlement_fails_page(gql_client, gr_client):
    q = '{ get_settlement_fails(fromDate: "2020-01-01", toDate: "2030-01-01") { data { settlementId } pageInfo { hasMore nextCursor } } }'
    service = await svc_settlements.get_settlement_fails("2020-01-01", "2030-01-01")
    ariadne = (await gql_query(gql_client, q))["data"]["get_settlement_fails"]
    gr = (await gql_query(gr_client, q))["data"]["get_settlement_fails"]
    assert gr == ariadne
    svc_ids = [d["settlementId"] for d in service["data"]]
    assert svc_ids == [d["settlementId"] for d in gr["data"]]
    assert service["page_info"]["next_cursor"] == gr["pageInfo"]["nextCursor"]


async def test_gr_parity_cash_balance(gql_client, gr_client, first_balance):
    account_id = first_balance["accountId"]
    currency = first_balance["currency"]
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    q = (
        f'{{ get_cash_balance(accountId: "{account_id}", currency: "{currency}", asOfDate: "{as_of}") '
        f'{{ balanceId accountId currency asOfDate openingBalance credits debits closingBalance pendingCredits pendingDebits projectedBalance snapshotType }} }}'
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_cash_balance"]
    gr = (await gql_query(gr_client, q))["data"]["get_cash_balance"]
    assert gr == ariadne


async def test_gr_parity_projected_balance(gql_client, gr_client, first_balance):
    account_id = first_balance["accountId"]
    currency = first_balance["currency"]
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    q = (
        f'{{ get_projected_balance(accountId: "{account_id}", currency: "{currency}", asOfDate: "{as_of}") '
        f'{{ accountId currency asOfDate closingBalance pendingCredits pendingDebits projectedBalance }} }}'
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_projected_balance"]
    gr = (await gql_query(gr_client, q))["data"]["get_projected_balance"]
    assert gr == ariadne


async def test_gr_parity_positions(gql_client, gr_client, db):
    pos = await db.positions.find_one({}, {"_id": 0})
    assert pos is not None
    as_of = pos["asOfDate"].strftime("%Y-%m-%d")
    q = (
        f'{{ get_positions(accountId: "{pos["accountId"]}", asOfDate: "{as_of}") '
        f'{{ data {{ positionId securityId quantity marketValue unrealizedPnL positionType snapshotType asOfDate }} pageInfo {{ hasMore nextCursor }} }} }}'
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_positions"]
    gr = (await gql_query(gr_client, q))["data"]["get_positions"]
    assert gr == ariadne
    assert gr["data"]


# ── Schema contract comparison ────────────────────────────────────────────────

async def test_gr_schema_contract_identical(gql_client, gr_client):
    """Every contract type, field, field type, and query argument must match
    between the Ariadne schema and the Graphene schema."""
    ariadne = _contract_map(await gql_query(gql_client, _INTROSPECT))
    gr = _contract_map(await gql_query(gr_client, _INTROSPECT))

    assert set(ariadne.keys()) == set(gr.keys()) == set(_CONTRACT_TYPES)
    for name in _CONTRACT_TYPES:
        assert ariadne[name] == gr[name], f"schema mismatch on type {name}"


# ── Documented behavioral difference ─────────────────────────────────────────

async def test_gr_not_found_shape(gql_client, gr_client):
    """Like Strawberry, Graphene's typed resolvers return a clean null for
    not-found (no errors entry), whereas Ariadne leaks a non-null violation."""
    q = '{ get_account(accountId: "ACC-DOES-NOT-EXIST") { accountId accountName } }'

    ariadne = await gql_query(gql_client, q)
    gr = await gql_query(gr_client, q)

    assert ariadne["data"]["get_account"] is None
    assert gr["data"]["get_account"] is None
    assert ariadne.get("errors"), "Ariadne emits a non-null violation error"
    assert not gr.get("errors"), "Graphene returns clean null without errors"
