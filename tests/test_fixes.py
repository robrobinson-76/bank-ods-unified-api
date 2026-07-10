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


# ── Date-window regressions (bugs found in the architecture review) ──────────

@pytest.mark.asyncio
async def test_settlements_match_whole_day(db):
    """Settlements are stamped intraday (16:00 EOD); querying by calendar date
    must return them. Regression: exact-midnight equality returned 0 forever."""
    s = await db.settlements.find_one({}, {"_id": 0, "accountId": 1, "settlementDate": 1, "settlementId": 1})
    day = s["settlementDate"].strftime("%Y-%m-%d")
    result = await svc_settlements.get_settlements(s["accountId"], day, limit=200)
    assert "error" not in result
    assert result["data"]
    assert any(d["settlementId"] == s["settlementId"] for d in result["data"])


@pytest.mark.asyncio
async def test_transactions_range_includes_end_date(db):
    """from_date == to_date == a real trade day must return that day's trades.
    Regression: $lte midnight excluded all same-day (intraday-stamped) trades."""
    t = await db.transactions.find_one({}, {"_id": 0, "accountId": 1, "tradeDate": 1, "transactionId": 1})
    day = t["tradeDate"].strftime("%Y-%m-%d")
    result = await svc_transactions.get_transactions(t["accountId"], day, day, limit=200)
    assert "error" not in result
    assert result["data"]
    assert any(d["transactionId"] == t["transactionId"] for d in result["data"])


@pytest.mark.asyncio
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

@pytest.mark.asyncio
async def test_securities_parity_all_layers(rest_client, gql_client, sb_client, gr_client):
    service = await svc_securities.list_securities(asset_class="GOVT_BOND", limit=5)
    assert service["data"]

    rest = (await rest_client.get("/securities", params={"asset_class": "GOVT_BOND", "limit": 5})).json()

    q = ('{ list_securities(assetClass: "GOVT_BOND", limit: 5) '
         '{ data { securityId description couponRate maturityDate status } pageInfo { hasMore nextCursor } } }')
    ariadne = (await gql_query(gql_client, q))["data"]["list_securities"]
    sb = (await gql_query(sb_client, q))["data"]["list_securities"]
    gr = (await gql_query(gr_client, q))["data"]["list_securities"]

    assert sb == ariadne == gr
    svc_ids = [d["securityId"] for d in service["data"]]
    assert svc_ids == [d["securityId"] for d in rest["data"]]
    assert svc_ids == [d["securityId"] for d in ariadne["data"]]
    assert service["page_info"]["next_cursor"] == ariadne["pageInfo"]["nextCursor"]


@pytest.mark.asyncio
async def test_get_security_not_found_rest_404(rest_client):
    resp = await rest_client.get("/securities/SEC-DOES-NOT-EXIST")
    assert resp.status_code == 404


# ── Decimal + timestamp serialization contract ────────────────────────────────

@pytest.mark.asyncio
async def test_decimal_serialized_as_exact_string(rest_client):
    """Monetary values are Decimal128 in Mongo and exact strings on the wire."""
    r = (await rest_client.get("/securities", params={"asset_class": "GOVT_BOND", "limit": 1})).json()
    coupon = r["data"][0]["couponRate"]
    assert isinstance(coupon, str)
    from decimal import Decimal
    Decimal(coupon)  # parses exactly


@pytest.mark.asyncio
async def test_timestamps_carry_utc_offset(rest_client, first_account):
    r = (await rest_client.get(f"/accounts/{first_account['accountId']}")).json()
    assert r["openDate"].endswith("+00:00")
    assert r["createdAt"].endswith("+00:00")


# ── Readiness probes ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ready_endpoints(rest_client, gql_client, sb_client, gr_client):
    for client in (rest_client, gql_client, sb_client, gr_client):
        resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}


# ── ClientMaster + Listing model surface ──────────────────────────────────────

@pytest.mark.asyncio
async def test_account_embeds_client_master(rest_client, first_account):
    """Accounts embed the denormalized client-master snapshot with a 20-char LEI."""
    r = (await rest_client.get(f"/accounts/{first_account['accountId']}")).json()
    client = r["client"]
    assert len(client["lei"]) == 20
    assert client["classification"] in ("RETAIL", "PROFESSIONAL", "ELIGIBLE_COUNTERPARTY")
    assert isinstance(client["taxResidencies"], list) and client["taxResidencies"]


@pytest.mark.asyncio
async def test_list_accounts_lei_and_domicile_filters(rest_client, first_account):
    lei = first_account["client"]["lei"]
    r = (await rest_client.get("/accounts", params={"lei": lei})).json()
    assert len(r["data"]) >= 1
    assert all(a["client"]["lei"] == lei for a in r["data"])

    dom = first_account["client"]["countryOfDomicile"]
    r2 = (await rest_client.get("/accounts", params={"domicile": dom})).json()
    assert len(r2["data"]) >= 1
    assert all(a["client"]["countryOfDomicile"] == dom for a in r2["data"])


@pytest.mark.asyncio
async def test_security_by_sedol_all_layers(rest_client, gql_client, sb_client, gr_client, db):
    """A listing-level SEDOL resolves to its parent security identically everywhere."""
    sec = await db.securities.find_one({"listings.0": {"$exists": True}}, {"_id": 0})
    assert sec is not None
    sedol = sec["listings"][0]["sedol"]

    service = await svc_securities.get_security_by_sedol(sedol)
    rest = (await rest_client.get(f"/securities/sedol/{sedol}")).json()
    q = f'{{ get_security_by_sedol(sedol: "{sedol}") {{ securityId listings {{ sedol micCode tradedCurrency primaryListing status }} }} }}'
    ariadne = (await gql_query(gql_client, q))["data"]["get_security_by_sedol"]
    sb = (await gql_query(sb_client, q))["data"]["get_security_by_sedol"]
    gr = (await gql_query(gr_client, q))["data"]["get_security_by_sedol"]

    assert service["securityId"] == rest["securityId"] == ariadne["securityId"] == sec["securityId"]
    assert sb == ariadne == gr
    assert any(l["sedol"] == sedol for l in ariadne["listings"])


@pytest.mark.asyncio
async def test_sedols_globally_unique(db):
    """One SEDOL per market/currency line, unique across the whole master."""
    sedols = [
        l["sedol"]
        async for doc in db.securities.find({}, {"_id": 0, "listings.sedol": 1})
        for l in doc.get("listings", [])
    ]
    assert sedols, "expected seeded listings"
    assert len(sedols) == len(set(sedols))


# ── Motor client is cached per event loop ─────────────────────────────────────

def test_client_survives_multiple_event_loops():
    """One process can run several event loops (scripts, notebooks, tools).
    Regression: a single process-global Motor client was bound to the first
    loop forever, so every later loop failed with 'Event loop is closed'."""
    import asyncio
    import bank_ods.services.accounts as svc_accounts

    r1 = asyncio.run(svc_accounts.list_accounts(limit=1))
    r2 = asyncio.run(svc_accounts.list_accounts(limit=1))
    assert "error" not in r1
    assert "error" not in r2
    assert r1["data"] == r2["data"]
