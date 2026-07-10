"""MCP layer tests — the missing parity leg: MCP tool == service result.

Uses fastmcp's in-process client against the real server object, so the full
tool-dispatch path (schema validation, serialization) is exercised.
"""
import pytest
from fastmcp import Client

import bank_ods.services.accounts as svc_accounts
import bank_ods.services.securities as svc_securities
import bank_ods.services.transactions as svc_transactions
from bank_ods.mcp.server import mcp

pytestmark = pytest.mark.asyncio

EXPECTED_TOOLS = {
    "get_account", "list_accounts",
    "get_security", "get_security_by_sedol", "list_securities",
    "get_transaction", "get_transactions", "get_transaction_summary",
    "get_position", "get_positions", "get_position_history",
    "get_settlement", "get_settlement_status", "get_settlements", "get_settlement_fails",
    "get_cash_balance", "get_cash_balances", "get_projected_balance",
}


def _payload(result):
    """Extract the dict payload from a fastmcp CallToolResult."""
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    import json
    return json.loads(result.content[0].text)


async def test_mcp_tool_surface():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_mcp_parity_get_account(first_account):
    account_id = first_account["accountId"]
    service = await svc_accounts.get_account(account_id)
    async with Client(mcp) as client:
        result = _payload(await client.call_tool("get_account", {"account_id": account_id}))
    assert result == service


async def test_mcp_parity_transactions_skip(first_account):
    account_id = first_account["accountId"]
    args = dict(account_id=account_id, from_date="2020-01-01", to_date="2030-01-01", limit=20, skip=1)
    service = await svc_transactions.get_transactions(**args)
    async with Client(mcp) as client:
        result = _payload(await client.call_tool("get_transactions", args))
    assert result == service


async def test_mcp_parity_list_securities():
    service = await svc_securities.list_securities(asset_class="GOVT_BOND")
    async with Client(mcp) as client:
        result = _payload(await client.call_tool("list_securities", {"asset_class": "GOVT_BOND"}))
    assert result == service
    assert result["count"] > 0


async def test_mcp_parity_get_security_by_sedol(dual_listed_security):
    # Use the secondary listing's SEDOL to prove any-element matching.
    sedol = dual_listed_security["listings"][1]["sedol"]
    service = await svc_securities.get_security_by_sedol(sedol)
    async with Client(mcp) as client:
        result = _payload(await client.call_tool("get_security_by_sedol", {"sedol": sedol}))
    assert result == service
    assert result["securityId"] == dual_listed_security["securityId"]
