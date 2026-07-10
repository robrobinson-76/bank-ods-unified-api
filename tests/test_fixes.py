"""Regression tests for the MVP review fixes.

Covers: whole-day date matching, inclusive date ranges, INVALID_DATE handling,
securities surface parity, Decimal string serialization, explicit UTC offsets,
and the /ready probes.
"""
import pytest
from tests.conftest import gql_query

import bank_ods.services.securities as svc_securities
import bank_ods.services.settlements as svc_settlements
import bank_ods.services.transactions as svc_transactions

pytestmark = pytest.mark.asyncio


# ── Date-window regressions (bugs found in the architecture review) ──────────

async def test_settlements_match_whole_day(db):
    """Settlements are stamped intraday (16:00 EOD); querying by calendar date
    must return them. Regression: exact-midnight equality returned 0 forever."""
    s = await db.settlements.find_one({}, {"_id": 0, "accountId": 1, "settlementDate": 1, "settlementId": 1})
    day = s["settlementDate"].strftime("%Y-%m-%d")
    result = await svc_settlements.get_settlements(s["accountId"], day)
    assert "error" not in result
    assert result["count"] > 0
    assert any(d["settlementId"] == s["settlementId"] for d in result["data"])


async def test_transactions_range_includes_end_date(db):
    """from_date == to_date == a real trade day must return that day's trades.
    Regression: $lte midnight excluded all same-day (intraday-stamped) trades."""
    t = await db.transactions.find_one({}, {"_id": 0, "accountId": 1, "tradeDate": 1, "transactionId": 1})
    day = t["tradeDate"].strftime("%Y-%m-%d")
    result = await svc_transactions.get_transactions(t["accountId"], day, day, limit=200)
    assert "error" not in result
    assert result["count"] > 0
    assert any(d["transactionId"] == t["transactionId"] for d in result["data"])


async def test_invalid_date_envelope_and_rest_400(rest_client, first_account):
    """Bad date input returns the INVALID_DATE envelope (never raises) and
    maps to HTTP 400 at the REST boundary — not a 500."""
    result = await svc_transactions.get_transactions(first_account["accountId"], "not-a-date", "2030-01-01")
    assert result.get("code") == "INVALID_DATE"

    resp = await rest_client.get("/transactions", params={
        "account_id": first_account["accountId"], "from_date": "not-a-date", "to_date": "2030-01-01",
    })
    assert resp.status_code == 400


# ── Securities surface (previously had no query surface at all) ──────────────

async def test_securities_parity_all_layers(rest_client, gql_client, sb_client, gr_client):
    service = await svc_securities.list_securities(asset_class="GOVT_BOND", limit=5)
    assert service["count"] > 0

    rest = (await rest_client.get("/securities", params={"asset_class": "GOVT_BOND", "limit": 5})).json()

    q = ('{ list_securities(assetClass: "GOVT_BOND", limit: 5) '
         '{ count data { securityId description couponRate maturityDate status } } }')
    ariadne = (await gql_query(gql_client, q))["data"]["list_securities"]
    sb = (await gql_query(sb_client, q))["data"]["list_securities"]
    gr = (await gql_query(gr_client, q))["data"]["list_securities"]

    assert service["count"] == rest["count"] == ariadne["count"] == sb["count"] == gr["count"]
    assert sb == ariadne == gr
    svc_ids = [d["securityId"] for d in service["data"]]
    assert svc_ids == [d["securityId"] for d in ariadne["data"]]


async def test_get_security_not_found_rest_404(rest_client):
    resp = await rest_client.get("/securities/SEC-DOES-NOT-EXIST")
    assert resp.status_code == 404


# ── Decimal + timestamp serialization contract ────────────────────────────────

async def test_decimal_serialized_as_exact_string(rest_client):
    """Monetary values are Decimal128 in Mongo and exact strings on the wire."""
    r = (await rest_client.get("/securities", params={"asset_class": "GOVT_BOND", "limit": 1})).json()
    coupon = r["data"][0]["couponRate"]
    assert isinstance(coupon, str)
    from decimal import Decimal
    Decimal(coupon)  # parses exactly


async def test_timestamps_carry_utc_offset(rest_client, first_account):
    r = (await rest_client.get(f"/accounts/{first_account['accountId']}")).json()
    assert r["openDate"].endswith("+00:00")
    assert r["createdAt"].endswith("+00:00")


# ── Readiness probes ──────────────────────────────────────────────────────────

async def test_ready_endpoints(rest_client, gql_client, sb_client, gr_client):
    for client in (rest_client, gql_client, sb_client, gr_client):
        resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}
