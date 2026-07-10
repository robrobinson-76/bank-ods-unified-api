from typing import Optional

from bank_ods.mcp.server import mcp
import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
import bank_ods.services.positions as svc_positions
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.balances as svc_balances


# ── Accounts ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_account(account_id: str) -> dict:
    """Fetch a single account by its account ID."""
    return await svc_accounts.get_account(account_id)


@mcp.tool()
async def list_accounts(
    client_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    skip: int = 0,
) -> dict:
    """List accounts with optional filters by client_id and/or status.

    count is the total number of matching accounts; data is one page (use skip to page).
    """
    return await svc_accounts.list_accounts(client_id, status, limit, skip)


# ── Securities ────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_security(security_id: str) -> dict:
    """Fetch a single security (instrument master record) by its security ID."""
    return await svc_securities.get_security(security_id)


@mcp.tool()
async def list_securities(
    asset_class: Optional[str] = None,
    ticker: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """List securities with optional filters by asset_class (EQUITY, GOVT_BOND,
    CORP_BOND, FUND, CASH), ticker, and/or status (ACTIVE, MATURED, DELISTED).

    count is the total number of matching securities; data is one page (use skip to page).
    """
    return await svc_securities.list_securities(asset_class, ticker, status, limit, skip)


# ── Transactions ───────────────────────────────────────────────────────────────

@mcp.tool()
async def get_transaction(transaction_id: str) -> dict:
    """Fetch a single transaction by its transaction ID."""
    return await svc_transactions.get_transaction(transaction_id)


@mcp.tool()
async def get_transactions(
    account_id: str,
    from_date: str,
    to_date: str,
    status: Optional[str] = None,
    transaction_type: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> dict:
    """Query transactions for an account within an inclusive date range (YYYY-MM-DD).

    count is the total number of matching transactions; data is one page (use skip to page).
    """
    return await svc_transactions.get_transactions(
        account_id, from_date, to_date, status, transaction_type, limit, skip
    )


@mcp.tool()
async def get_transaction_summary(account_id: str, from_date: str, to_date: str) -> dict:
    """Aggregate transaction count and netAmount grouped by transactionType and status."""
    return await svc_transactions.get_transaction_summary(account_id, from_date, to_date)


# ── Positions ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_position(account_id: str, security_id: str, as_of_date: str) -> dict:
    """Fetch a single position for an account/security on a given date (YYYY-MM-DD)."""
    return await svc_positions.get_position(account_id, security_id, as_of_date)


@mcp.tool()
async def get_positions(account_id: str, as_of_date: str, skip: int = 0) -> dict:
    """Fetch all positions for an account on a given date (YYYY-MM-DD).

    count is the total number of matching positions; data is one page (use skip to page).
    """
    return await svc_positions.get_positions(account_id, as_of_date, skip)


@mcp.tool()
async def get_position_history(
    account_id: str,
    security_id: str,
    from_date: str,
    to_date: str,
    skip: int = 0,
) -> dict:
    """Return EOD position history for an account/security over an inclusive date range."""
    return await svc_positions.get_position_history(account_id, security_id, from_date, to_date, skip)


# ── Settlements ───────────────────────────────────────────────────────────────

@mcp.tool()
async def get_settlement(settlement_id: str) -> dict:
    """Fetch a settlement instruction by its settlement ID."""
    return await svc_settlements.get_settlement(settlement_id)


@mcp.tool()
async def get_settlement_status(transaction_id: str) -> dict:
    """Look up the settlement linked to a transaction ID."""
    return await svc_settlements.get_settlement_status(transaction_id)


@mcp.tool()
async def get_settlements(
    account_id: str,
    settlement_date: str,
    status: Optional[str] = None,
    skip: int = 0,
) -> dict:
    """Query settlements for an account on a settlement date (whole day, YYYY-MM-DD).

    count is the total number of matching settlements; data is one page (use skip to page).
    """
    return await svc_settlements.get_settlements(account_id, settlement_date, status, skip)


@mcp.tool()
async def get_settlement_fails(
    from_date: str,
    to_date: str,
    account_id: Optional[str] = None,
    skip: int = 0,
) -> dict:
    """Find all FAILED settlements within an inclusive date window, optionally by account.

    count is the total number of matching settlements; data is one page (use skip to page).
    """
    return await svc_settlements.get_settlement_fails(from_date, to_date, account_id, skip)


# ── Balances ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_cash_balance(account_id: str, currency: str, as_of_date: str) -> dict:
    """Fetch the cash balance for a specific account, currency, and date (YYYY-MM-DD)."""
    return await svc_balances.get_cash_balance(account_id, currency, as_of_date)


@mcp.tool()
async def get_cash_balances(account_id: str, as_of_date: str, skip: int = 0) -> dict:
    """Fetch all currency balances for an account on a given date (YYYY-MM-DD).

    count is the total number of matching balances; data is one page (use skip to page).
    """
    return await svc_balances.get_cash_balances(account_id, as_of_date, skip)


@mcp.tool()
async def get_projected_balance(account_id: str, currency: str, as_of_date: str) -> dict:
    """Return the projected balance (closing net of pending) for an account/currency/date."""
    return await svc_balances.get_projected_balance(account_id, currency, as_of_date)
