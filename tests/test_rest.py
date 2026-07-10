"""REST layer tests — assert each endpoint returns same shape as service layer."""
import pytest

pytestmark = pytest.mark.asyncio


# ── Ops ───────────────────────────────────────────────────────────────────────

async def test_rest_health(rest_client):
    resp = await rest_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Accounts ──────────────────────────────────────────────────────────────────

async def test_rest_get_account(rest_client, first_account):
    resp = await rest_client.get(f"/accounts/{first_account['accountId']}")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" not in data
    assert data["accountId"] == first_account["accountId"]


async def test_rest_get_account_not_found(rest_client):
    resp = await rest_client.get("/accounts/ACC-DOES-NOT-EXIST")
    assert resp.status_code == 404


async def test_rest_list_accounts(rest_client):
    resp = await rest_client.get("/accounts", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert isinstance(data["data"], list)
    assert len(data["data"]) <= 5


async def test_rest_list_accounts_skip(rest_client):
    """count is the total (unchanged by skip); the data page is offset by 1."""
    all_resp = await rest_client.get("/accounts", params={"limit": 50, "skip": 0})
    skip_resp = await rest_client.get("/accounts", params={"limit": 50, "skip": 1})
    assert all_resp.status_code == 200
    assert skip_resp.status_code == 200
    total = all_resp.json()["count"]
    if total > 1:
        assert skip_resp.json()["count"] == total
        # First item of skipped page should match second item of full page
        assert skip_resp.json()["data"][0]["accountId"] == all_resp.json()["data"][1]["accountId"]


# ── Transactions ───────────────────────────────────────────────────────────────

async def test_rest_get_transaction_not_found(rest_client):
    resp = await rest_client.get("/transactions/TXN-DOES-NOT-EXIST")
    assert resp.status_code == 404


async def test_rest_get_transactions(rest_client, first_account):
    resp = await rest_client.get(
        "/transactions",
        params={
            "account_id": first_account["accountId"],
            "from_date": "2020-01-01",
            "to_date": "2030-01-01",
            "limit": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert len(data["data"]) <= 5


async def test_rest_get_transactions_skip(rest_client, first_account):
    """skip=1 offsets results; item[0] of skipped page == item[1] of full page."""
    base = dict(
        account_id=first_account["accountId"],
        from_date="2020-01-01",
        to_date="2030-01-01",
        limit=200,  # max — ensures skip difference is visible
    )
    all_resp = await rest_client.get("/transactions", params={**base, "skip": 0})
    skip_resp = await rest_client.get("/transactions", params={**base, "skip": 1})
    assert all_resp.status_code == 200
    assert skip_resp.status_code == 200
    total = all_resp.json()["count"]
    if total > 1:
        assert skip_resp.json()["count"] == total
        assert (
            skip_resp.json()["data"][0]["transactionId"]
            == all_resp.json()["data"][1]["transactionId"]
        )


async def test_rest_transaction_summary(rest_client, first_account):
    resp = await rest_client.get(
        "/transactions/summary",
        params={
            "account_id": first_account["accountId"],
            "from_date": "2020-01-01",
            "to_date": "2030-01-01",
        },
    )
    assert resp.status_code == 200
    assert "count" in resp.json()


# ── Settlements ───────────────────────────────────────────────────────────────

async def test_rest_get_settlement_not_found(rest_client):
    resp = await rest_client.get("/settlements/STL-DOES-NOT-EXIST")
    assert resp.status_code == 404


async def test_rest_settlement_fails(rest_client):
    resp = await rest_client.get(
        "/settlements/fails",
        params={"from_date": "2020-01-01", "to_date": "2030-01-01"},
    )
    assert resp.status_code == 200
    assert "count" in resp.json()


# ── Balances ──────────────────────────────────────────────────────────────────

async def test_rest_cash_balances(rest_client, first_balance):
    resp = await rest_client.get(
        f"/balances/{first_balance['accountId']}",
        params={"as_of_date": first_balance["asOfDate"].strftime("%Y-%m-%d")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data


async def test_rest_projected_balance(rest_client, first_balance):
    resp = await rest_client.get(
        f"/balances/{first_balance['accountId']}/{first_balance['currency']}/projected",
        params={"as_of_date": first_balance["asOfDate"].strftime("%Y-%m-%d")},
    )
    assert resp.status_code == 200
    assert "projectedBalance" in resp.json()


async def test_rest_cash_balance_not_found(rest_client):
    resp = await rest_client.get(
        "/balances/ACC-NOPE/USD",
        params={"as_of_date": "2020-01-01"},
    )
    assert resp.status_code == 404
