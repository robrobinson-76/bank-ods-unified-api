"""Strawberry evaluation harness — asserts the side-by-side Strawberry GraphQL
app (bank_ods.graphql_strawberry) honors the same contract as the Ariadne app.

Four-way parity: service == REST == Ariadne GraphQL == Strawberry GraphQL,
plus a full schema-contract comparison via introspection and a test that
documents the one intentional behavioral difference (not-found error shape).
"""
import pytest
from tests.conftest import gql_query

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances

pytestmark = pytest.mark.asyncio


# ── Ops ───────────────────────────────────────────────────────────────────────

async def test_sb_health(sb_client):
    resp = await sb_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Four-way parity ───────────────────────────────────────────────────────────

async def test_parity4_get_account(rest_client, gql_client, sb_client, first_account):
    account_id = first_account["accountId"]
    q = (
        f'{{ get_account(accountId: "{account_id}") '
        f'{{ accountId accountName accountType clientId clientName baseCurrency status openDate closeDate custodianBranch createdAt updatedAt }} }}'
    )

    service = await svc_accounts.get_account(account_id)
    rest = (await rest_client.get(f"/accounts/{account_id}")).json()
    ariadne = (await gql_query(gql_client, q))["data"]["get_account"]
    sb = (await gql_query(sb_client, q))["data"]["get_account"]

    # Strawberry must return the identical full record the Ariadne layer returns
    assert sb == ariadne
    for key in ("accountId", "clientName", "status", "openDate", "createdAt"):
        assert service[key] == rest[key] == ariadne[key] == sb[key]


async def test_parity4_list_accounts_skip(rest_client, gql_client, sb_client):
    q = "{ list_accounts(limit: 20, skip: 1) { count data { accountId } } }"

    service = await svc_accounts.list_accounts(limit=20, skip=1)
    rest = (await rest_client.get("/accounts", params={"limit": 20, "skip": 1})).json()
    ariadne = (await gql_query(gql_client, q))["data"]["list_accounts"]
    sb = (await gql_query(sb_client, q))["data"]["list_accounts"]

    assert service["count"] == rest["count"] == ariadne["count"] == sb["count"]
    svc_ids = [d["accountId"] for d in service["data"]]
    assert svc_ids == [d["accountId"] for d in rest["data"]]
    assert svc_ids == [d["accountId"] for d in ariadne["data"]]
    assert svc_ids == [d["accountId"] for d in sb["data"]]


async def test_parity4_transactions_skip(rest_client, gql_client, sb_client, first_account):
    account_id = first_account["accountId"]
    q = (
        f'{{ get_transactions(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01", limit: 20, skip: 1) '
        f'{{ count data {{ transactionId transactionType tradeDate netAmount status }} }} }}'
    )

    service = await svc_transactions.get_transactions(
        account_id=account_id, from_date="2020-01-01", to_date="2030-01-01", limit=20, skip=1
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_transactions"]
    sb = (await gql_query(sb_client, q))["data"]["get_transactions"]

    assert service["count"] == ariadne["count"] == sb["count"]
    assert sb["data"] == ariadne["data"]
    if service["count"] > 0:
        assert service["data"][0]["transactionId"] == sb["data"][0]["transactionId"]


async def test_parity4_transaction_summary(gql_client, sb_client, first_account):
    account_id = first_account["accountId"]
    q = (
        f'{{ get_transaction_summary(accountId: "{account_id}", fromDate: "2020-01-01", toDate: "2030-01-01") '
        f'{{ count data {{ transactionType status count totalNetAmount }} }} }}'
    )
    service = await svc_transactions.get_transaction_summary(account_id, "2020-01-01", "2030-01-01")
    ariadne = (await gql_query(gql_client, q))["data"]["get_transaction_summary"]
    sb = (await gql_query(sb_client, q))["data"]["get_transaction_summary"]

    assert sb == ariadne
    assert sb["count"] == service["count"]


async def test_parity4_settlement_nested_history(gql_client, sb_client, first_settled_txn):
    """Settlement carries the nested statusHistory list — the hardest shape."""
    txn_id = first_settled_txn["transactionId"]
    q = (
        f'{{ get_settlement_status(transactionId: "{txn_id}") '
        f'{{ settlementId transactionId deliveryType status statusHistory {{ status timestamp }} settlementDate createdAt }} }}'
    )
    service = await svc_settlements.get_settlement_status(txn_id)
    ariadne = (await gql_query(gql_client, q))["data"]["get_settlement_status"]
    sb = (await gql_query(sb_client, q))["data"]["get_settlement_status"]

    assert sb == ariadne
    if "error" not in service:
        assert sb["settlementId"] == service["settlementId"]
        assert [h["status"] for h in sb["statusHistory"]] == [h["status"] for h in service["statusHistory"]]


async def test_parity4_settlement_fails_count(rest_client, gql_client, sb_client):
    q = '{ get_settlement_fails(fromDate: "2020-01-01", toDate: "2030-01-01") { count } }'

    service = await svc_settlements.get_settlement_fails("2020-01-01", "2030-01-01")
    rest = (await rest_client.get(
        "/settlements/fails", params={"from_date": "2020-01-01", "to_date": "2030-01-01"}
    )).json()
    ariadne = (await gql_query(gql_client, q))["data"]["get_settlement_fails"]["count"]
    sb = (await gql_query(sb_client, q))["data"]["get_settlement_fails"]["count"]

    assert service["count"] == rest["count"] == ariadne == sb


async def test_parity4_cash_balance(rest_client, gql_client, sb_client, first_balance):
    account_id = first_balance["accountId"]
    currency = first_balance["currency"]
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    q = (
        f'{{ get_cash_balance(accountId: "{account_id}", currency: "{currency}", asOfDate: "{as_of}") '
        f'{{ balanceId accountId currency asOfDate openingBalance credits debits closingBalance pendingCredits pendingDebits projectedBalance snapshotType }} }}'
    )

    service = await svc_balances.get_cash_balance(account_id, currency, as_of)
    rest = (await rest_client.get(f"/balances/{account_id}/{currency}", params={"as_of_date": as_of})).json()
    ariadne = (await gql_query(gql_client, q))["data"]["get_cash_balance"]
    sb = (await gql_query(sb_client, q))["data"]["get_cash_balance"]

    assert sb == ariadne
    assert service["closingBalance"] == rest["closingBalance"] == sb["closingBalance"]


async def test_parity4_projected_balance(gql_client, sb_client, first_balance):
    account_id = first_balance["accountId"]
    currency = first_balance["currency"]
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    q = (
        f'{{ get_projected_balance(accountId: "{account_id}", currency: "{currency}", asOfDate: "{as_of}") '
        f'{{ accountId currency asOfDate closingBalance pendingCredits pendingDebits projectedBalance }} }}'
    )
    service = await svc_balances.get_projected_balance(account_id, currency, as_of)
    ariadne = (await gql_query(gql_client, q))["data"]["get_projected_balance"]
    sb = (await gql_query(sb_client, q))["data"]["get_projected_balance"]

    assert sb == ariadne
    assert sb["projectedBalance"] == service["projectedBalance"]


async def test_parity4_positions(gql_client, sb_client, db, first_account):
    pos = await db.positions.find_one({}, {"_id": 0})
    assert pos is not None
    as_of = pos["asOfDate"].strftime("%Y-%m-%d")
    q = (
        f'{{ get_positions(accountId: "{pos["accountId"]}", asOfDate: "{as_of}") '
        f'{{ count data {{ positionId securityId quantity marketValue unrealizedPnL positionType snapshotType asOfDate }} }} }}'
    )
    ariadne = (await gql_query(gql_client, q))["data"]["get_positions"]
    sb = (await gql_query(sb_client, q))["data"]["get_positions"]
    assert sb == ariadne
    assert sb["count"] > 0


# ── Schema contract comparison ────────────────────────────────────────────────

_INTROSPECT = """
query {
  __schema {
    types {
      name kind
      fields(includeDeprecated: true) {
        name
        type { ...T }
        args { name type { ...T } }
      }
    }
  }
}
fragment T on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name } } }
}
"""

_CONTRACT_TYPES = [
    "Query", "DateTime",
    "Account", "AccountList", "Security", "SecurityList",
    "Transaction", "TransactionList", "Position", "PositionList",
    "Settlement", "SettlementList", "StatusHistoryEntry",
    "CashBalance", "CashBalanceList",
    "TransactionSummaryItem", "TransactionSummaryList", "ProjectedBalance",
]


def _type_str(t):
    if t is None:
        return ""
    if t["kind"] == "NON_NULL":
        return _type_str(t.get("ofType")) + "!"
    if t["kind"] == "LIST":
        return "[" + _type_str(t.get("ofType")) + "]"
    return t["name"] or ""


def _contract_map(introspection: dict) -> dict:
    out = {}
    for t in introspection["data"]["__schema"]["types"]:
        if t["name"] not in _CONTRACT_TYPES:
            continue
        fields = {}
        for f in t.get("fields") or []:
            fields[f["name"]] = {
                "type": _type_str(f["type"]),
                "args": {a["name"]: _type_str(a["type"]) for a in f.get("args") or []},
            }
        out[t["name"]] = {"kind": t["kind"], "fields": fields}
    return out


async def test_schema_contract_identical(gql_client, sb_client):
    """Every contract type, field, field type, and query argument must match
    between the Ariadne schema and the Strawberry schema."""
    ariadne = _contract_map(await gql_query(gql_client, _INTROSPECT))
    sb = _contract_map(await gql_query(sb_client, _INTROSPECT))

    assert set(ariadne.keys()) == set(sb.keys()) == set(_CONTRACT_TYPES)
    for name in _CONTRACT_TYPES:
        assert ariadne[name] == sb[name], f"schema mismatch on type {name}"


# ── Documented behavioral difference ─────────────────────────────────────────

async def test_not_found_shape_differs(gql_client, sb_client):
    """Both layers return data.get_account == null for an unknown ID, but
    Ariadne surfaces a non-null-violation entry in `errors` (the raw error
    envelope leaks into field resolution) while Strawberry returns a clean
    null. Clients that inspect `errors` to detect not-found would need to
    change with a Strawberry migration."""
    q = '{ get_account(accountId: "ACC-DOES-NOT-EXIST") { accountId clientName } }'

    ariadne = await gql_query(gql_client, q)
    sb = await gql_query(sb_client, q)

    assert ariadne["data"]["get_account"] is None
    assert sb["data"]["get_account"] is None
    assert ariadne.get("errors"), "Ariadne emits a non-null violation error"
    assert not sb.get("errors"), "Strawberry returns clean null without errors"
