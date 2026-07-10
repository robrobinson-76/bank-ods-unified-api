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
    assert "page_info" in data
    assert isinstance(data["data"], list)
    assert len(data["data"]) <= 5


async def test_rest_list_accounts_cursor(rest_client):
    """Following next_cursor resumes exactly where the previous page ended."""
    full_resp = await rest_client.get("/accounts", params={"limit": 50})
    page1_resp = await rest_client.get("/accounts", params={"limit": 1})
    assert full_resp.status_code == 200
    assert page1_resp.status_code == 200
    full = full_resp.json()
    page1 = page1_resp.json()
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        page2_resp = await rest_client.get(
            "/accounts", params={"limit": 1, "cursor": page1["page_info"]["next_cursor"]}
        )
        assert page2_resp.status_code == 200
        assert page2_resp.json()["data"][0]["accountId"] == full["data"][1]["accountId"]


async def test_rest_invalid_cursor_400(rest_client):
    resp = await rest_client.get("/accounts", params={"cursor": "garbage"})
    assert resp.status_code == 400


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
    assert "page_info" in data
    assert len(data["data"]) <= 5


async def test_rest_get_transactions_cursor(rest_client, first_account):
    """Following next_cursor resumes exactly where the previous page ended."""
    base = dict(
        account_id=first_account["accountId"],
        from_date="2020-01-01",
        to_date="2030-01-01",
    )
    full = (await rest_client.get("/transactions", params={**base, "limit": 200})).json()
    page1 = (await rest_client.get("/transactions", params={**base, "limit": 1})).json()
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        page2 = (await rest_client.get(
            "/transactions",
            params={**base, "limit": 1, "cursor": page1["page_info"]["next_cursor"]},
        )).json()
        assert page2["data"][0]["transactionId"] == full["data"][1]["transactionId"]


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
    body = resp.json()
    assert isinstance(body["data"], list)
    assert "page_info" not in body  # summary is not paginated


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
    assert "page_info" in resp.json()


# ── Balances ──────────────────────────────────────────────────────────────────

async def test_rest_cash_balances(rest_client, first_balance):
    resp = await rest_client.get(
        f"/balances/{first_balance['accountId']}",
        params={"as_of_date": first_balance["asOfDate"].strftime("%Y-%m-%d")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "page_info" in data


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
