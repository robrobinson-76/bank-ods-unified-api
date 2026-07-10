"""Direct service layer tests — the canonical correctness reference."""
import pytest
import pytest_asyncio

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.positions as svc_positions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances


pytestmark = pytest.mark.asyncio


# ── Accounts ──────────────────────────────────────────────────────────────────

async def test_get_account_found(first_account):
    result = await svc_accounts.get_account(first_account["accountId"])
    assert "error" not in result
    assert result["accountId"] == first_account["accountId"]
    assert result["client"]["clientName"]
    assert len(result["client"]["lei"]) == 20


async def test_get_account_not_found():
    result = await svc_accounts.get_account("ACC-DOES-NOT-EXIST")
    assert result.get("code") == "NOT_FOUND"


async def test_list_accounts(first_account):
    result = await svc_accounts.list_accounts(limit=5)
    assert "error" not in result
    assert isinstance(result["data"], list)
    assert result["data"]
    assert set(result["page_info"]) == {"has_more", "next_cursor"}


async def test_list_accounts_by_status():
    result = await svc_accounts.list_accounts(status="ACTIVE")
    assert "error" not in result
    for acct in result["data"]:
        assert acct["status"] == "ACTIVE"


async def test_list_accounts_cursor():
    """Following next_cursor resumes exactly where the previous page ended."""
    full = await svc_accounts.list_accounts(limit=50)
    page1 = await svc_accounts.list_accounts(limit=1)
    assert "error" not in full
    assert "error" not in page1
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        cursor = page1["page_info"]["next_cursor"]
        page2 = await svc_accounts.list_accounts(limit=1, cursor=cursor)
        assert page2["data"][0]["accountId"] == full["data"][1]["accountId"]


async def test_list_accounts_invalid_cursor():
    result = await svc_accounts.list_accounts(cursor="not-a-cursor")
    assert result.get("code") == "INVALID_CURSOR"


async def test_list_accounts_by_lei(first_account):
    lei = first_account["client"]["lei"]
    result = await svc_accounts.list_accounts(lei=lei)
    assert "error" not in result
    assert result["data"]
    for acct in result["data"]:
        assert acct["client"]["lei"] == lei


async def test_list_accounts_by_domicile(first_account):
    domicile = first_account["client"]["countryOfDomicile"]
    result = await svc_accounts.list_accounts(domicile=domicile)
    assert "error" not in result
    assert result["data"]
    for acct in result["data"]:
        assert acct["client"]["countryOfDomicile"] == domicile


# ── Securities ────────────────────────────────────────────────────────────────

async def test_get_security_found(first_security):
    result = await svc_securities.get_security(first_security["securityId"])
    assert "error" not in result
    assert result["securityId"] == first_security["securityId"]


async def test_get_security_not_found():
    result = await svc_securities.get_security("SEC-DOES-NOT-EXIST")
    assert result.get("code") == "NOT_FOUND"


async def test_get_security_by_sedol_found(dual_listed_security):
    # Match on the SECONDARY listing's SEDOL to prove any-element matching.
    sedol = dual_listed_security["listings"][1]["sedol"]
    result = await svc_securities.get_security_by_sedol(sedol)
    assert "error" not in result
    assert result["securityId"] == dual_listed_security["securityId"]


async def test_get_security_by_sedol_not_found():
    result = await svc_securities.get_security_by_sedol("0000000")
    assert result.get("code") == "NOT_FOUND"


async def test_list_securities_sedol_filter(dual_listed_security):
    sedol = dual_listed_security["listings"][0]["sedol"]
    result = await svc_securities.list_securities(sedol=sedol)
    assert "error" not in result
    assert len(result["data"]) == 1
    assert result["page_info"]["has_more"] is False
    assert result["data"][0]["securityId"] == dual_listed_security["securityId"]


# ── Transactions ───────────────────────────────────────────────────────────────

async def test_get_transaction_found(first_settled_txn):
    result = await svc_transactions.get_transaction(first_settled_txn["transactionId"])
    assert "error" not in result
    assert result["transactionId"] == first_settled_txn["transactionId"]


async def test_get_transaction_not_found():
    result = await svc_transactions.get_transaction("TXN-NOPE")
    assert result.get("code") == "NOT_FOUND"


async def test_get_transactions(first_account):
    result = await svc_transactions.get_transactions(
        account_id=first_account["accountId"],
        from_date="2020-01-01",
        to_date="2030-01-01",
        limit=10,
    )
    assert "error" not in result
    assert isinstance(result["data"], list)
    assert "page_info" in result


async def test_get_transactions_cursor(first_account):
    """Following next_cursor resumes exactly where the previous page ended."""
    base = dict(
        account_id=first_account["accountId"],
        from_date="2020-01-01",
        to_date="2030-01-01",
    )
    full = await svc_transactions.get_transactions(**base, limit=200)
    page1 = await svc_transactions.get_transactions(**base, limit=1)
    assert "error" not in full
    assert "error" not in page1
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        page2 = await svc_transactions.get_transactions(
            **base, limit=1, cursor=page1["page_info"]["next_cursor"]
        )
        assert page2["data"][0]["transactionId"] == full["data"][1]["transactionId"]


async def test_get_transaction_summary(first_account):
    result = await svc_transactions.get_transaction_summary(
        account_id=first_account["accountId"],
        from_date="2020-01-01",
        to_date="2030-01-01",
    )
    assert "error" not in result
    assert "page_info" not in result  # not paginated — data holds every group
    assert isinstance(result["data"], list)


# ── Positions ─────────────────────────────────────────────────────────────────

async def test_get_positions(db, first_account):
    pos_doc = await db.positions.find_one({"accountId": first_account["accountId"]}, {"_id": 0})
    if pos_doc is None:
        pytest.skip("No positions for this account")
    as_of = pos_doc["asOfDate"].strftime("%Y-%m-%d")
    result = await svc_positions.get_positions(first_account["accountId"], as_of)
    assert "error" not in result
    assert result["data"]


async def test_get_positions_cursor(db, first_account):
    """Following next_cursor resumes exactly where the previous page ended."""
    pos_doc = await db.positions.find_one({"accountId": first_account["accountId"]}, {"_id": 0})
    if pos_doc is None:
        pytest.skip("No positions for this account")
    as_of = pos_doc["asOfDate"].strftime("%Y-%m-%d")
    full = await svc_positions.get_positions(first_account["accountId"], as_of, limit=200)
    page1 = await svc_positions.get_positions(first_account["accountId"], as_of, limit=1)
    assert "error" not in full
    assert "error" not in page1
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        page2 = await svc_positions.get_positions(
            first_account["accountId"], as_of, limit=1,
            cursor=page1["page_info"]["next_cursor"],
        )
        assert page2["data"][0]["securityId"] == full["data"][1]["securityId"]


# ── Settlements ───────────────────────────────────────────────────────────────

async def test_get_settlement_fails():
    result = await svc_settlements.get_settlement_fails(
        from_date="2020-01-01", to_date="2030-01-01"
    )
    assert "error" not in result
    assert isinstance(result["data"], list)
    assert "page_info" in result


async def test_get_settlement_fails_cursor():
    """Following next_cursor resumes exactly where the previous page ended."""
    full = await svc_settlements.get_settlement_fails("2020-01-01", "2030-01-01", limit=200)
    page1 = await svc_settlements.get_settlement_fails("2020-01-01", "2030-01-01", limit=1)
    assert "error" not in full
    assert "error" not in page1
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        page2 = await svc_settlements.get_settlement_fails(
            "2020-01-01", "2030-01-01", limit=1,
            cursor=page1["page_info"]["next_cursor"],
        )
        assert page2["data"][0]["settlementId"] == full["data"][1]["settlementId"]


async def test_get_settlement_status(db, first_settled_txn):
    stl_ref = first_settled_txn.get("settlementRef")
    if not stl_ref:
        pytest.skip("Transaction has no settlementRef")
    result = await svc_settlements.get_settlement_status(first_settled_txn["transactionId"])
    assert "error" not in result
    assert result["transactionId"] == first_settled_txn["transactionId"]


# ── Balances ──────────────────────────────────────────────────────────────────

async def test_get_cash_balance(first_balance):
    result = await svc_balances.get_cash_balance(
        account_id=first_balance["accountId"],
        currency=first_balance["currency"],
        as_of_date=first_balance["asOfDate"].strftime("%Y-%m-%d"),
    )
    assert "error" not in result
    assert "closingBalance" in result


async def test_get_cash_balances(first_balance):
    result = await svc_balances.get_cash_balances(
        account_id=first_balance["accountId"],
        as_of_date=first_balance["asOfDate"].strftime("%Y-%m-%d"),
    )
    assert "error" not in result
    assert result["data"]


async def test_get_cash_balances_cursor(first_balance):
    """Following next_cursor resumes exactly where the previous page ended."""
    as_of = first_balance["asOfDate"].strftime("%Y-%m-%d")
    full = await svc_balances.get_cash_balances(first_balance["accountId"], as_of, limit=200)
    page1 = await svc_balances.get_cash_balances(first_balance["accountId"], as_of, limit=1)
    assert "error" not in full
    assert "error" not in page1
    if len(full["data"]) > 1:
        assert page1["page_info"]["has_more"] is True
        page2 = await svc_balances.get_cash_balances(
            first_balance["accountId"], as_of, limit=1,
            cursor=page1["page_info"]["next_cursor"],
        )
        assert page2["data"][0]["currency"] == full["data"][1]["currency"]


async def test_get_projected_balance(first_balance):
    result = await svc_balances.get_projected_balance(
        account_id=first_balance["accountId"],
        currency=first_balance["currency"],
        as_of_date=first_balance["asOfDate"].strftime("%Y-%m-%d"),
    )
    assert "error" not in result
    assert "projectedBalance" in result
