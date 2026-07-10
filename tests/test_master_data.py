"""Reference-data integrity tests for the seeded master data.

Validates that seeded identifiers are format-valid against their standards
(SEDOL check digits, ISO 17442 LEI check pairs) and that the denormalized
structures obey the invariants the model layer cannot enforce:

- SEDOL uniqueness must hold globally. The multikey unique index on
  listings.sedol only enforces uniqueness across documents, not within one
  document's listings array, so it is asserted here.
- Every account of a client must embed an identical client-master snapshot.
"""
import re

import pytest

pytestmark = pytest.mark.asyncio

SEDOL_RE = re.compile(r"^[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}\d$")
LEI_RE = re.compile(r"^[0-9A-Z]{18}\d{2}$")
_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9)


def sedol_check_digit(body: str) -> int:
    return (10 - sum(w * int(c, 36) for w, c in zip(_SEDOL_WEIGHTS, body)) % 10) % 10


def lei_is_valid(lei: str) -> bool:
    # ISO 7064 MOD 97-10: the full 20-char code maps to digits and mod-97s to 1.
    return int("".join(str(int(ch, 36)) for ch in lei)) % 97 == 1


async def _all_listings(db) -> list[dict]:
    cursor = db.securities.aggregate([
        {"$unwind": "$listings"},
        {"$replaceRoot": {"newRoot": "$listings"}},
    ])
    return await cursor.to_list(length=None)


async def test_sedol_format_and_check_digit(db):
    listings = await _all_listings(db)
    assert listings, "No listings seeded"
    for lst in listings:
        sedol = lst["sedol"]
        assert SEDOL_RE.match(sedol), f"Malformed SEDOL: {sedol}"
        assert int(sedol[6]) == sedol_check_digit(sedol[:6]), f"Bad check digit: {sedol}"


async def test_sedols_globally_unique(db):
    listings = await _all_listings(db)
    sedols = [lst["sedol"] for lst in listings]
    assert len(sedols) == len(set(sedols)), "Duplicate SEDOLs across listings"


async def test_listing_reference_fields(db):
    listings = await _all_listings(db)
    for lst in listings:
        assert re.match(r"^[A-Z0-9]{4}$", lst["micCode"]), f"Bad MIC: {lst['micCode']}"
        assert re.match(r"^[A-Z0-9]{4}$", lst["operatingMic"])
        assert re.match(r"^[A-Z]{2}$", lst["countryOfListing"])
        assert re.match(r"^[A-Z]{3}$", lst["tradedCurrency"])
        assert lst["settlementLocation"], "Listing missing settlement location"


async def test_each_security_with_listings_has_one_primary(db):
    cursor = db.securities.find({"listings.0": {"$exists": True}}, {"_id": 0})
    async for sec in cursor:
        primaries = [lst for lst in sec["listings"] if lst["primaryListing"]]
        assert len(primaries) == 1, f"{sec['securityId']} has {len(primaries)} primary listings"


async def test_dual_listed_securities_exist(db):
    dual = await db.securities.count_documents({"listings.1": {"$exists": True}})
    assert dual >= 3, "Expected at least 3 multi-listed securities in seed data"

    # At least one security must carry two listings on the same venue in
    # different currencies (the post-2008 per-currency SEDOL allocation case).
    per_currency_case = False
    cursor = db.securities.find({"listings.1": {"$exists": True}}, {"_id": 0})
    async for sec in cursor:
        seen: dict[str, set[str]] = {}
        for lst in sec["listings"]:
            seen.setdefault(lst["micCode"], set()).add(lst["tradedCurrency"])
        if any(len(currencies) > 1 for currencies in seen.values()):
            per_currency_case = True
            break
    assert per_currency_case, "No same-MIC multi-currency listing pair seeded"


async def test_bonds_have_no_listings(db):
    cursor = db.securities.find(
        {"assetClass": {"$in": ["GOVT_BOND", "CORP_BOND"]}}, {"_id": 0}
    )
    async for bond in cursor:
        assert bond["listings"] == [], f"{bond['securityId']} should have no listings"


async def test_lei_format_and_mod97_check(db):
    cursor = db.accounts.find({}, {"_id": 0})
    leis = set()
    async for acct in cursor:
        lei = acct["client"]["lei"]
        assert LEI_RE.match(lei), f"Malformed LEI: {lei}"
        assert lei_is_valid(lei), f"LEI fails ISO 7064 MOD 97-10 check: {lei}"
        leis.add(lei)
    assert leis, "No accounts seeded"


async def test_client_master_consistent_per_client(db):
    by_client: dict[str, list[dict]] = {}
    cursor = db.accounts.find({}, {"_id": 0})
    async for acct in cursor:
        by_client.setdefault(acct["client"]["clientId"], []).append(acct["client"])
    assert by_client, "No accounts seeded"
    for client_id, snapshots in by_client.items():
        first = snapshots[0]
        for snap in snapshots[1:]:
            assert snap == first, f"Inconsistent client master for {client_id}"


async def test_client_master_country_codes(db):
    cursor = db.accounts.find({}, {"_id": 0})
    async for acct in cursor:
        client = acct["client"]
        assert re.match(r"^[A-Z]{2}$", client["countryOfDomicile"])
        assert re.match(r"^[A-Z]{2}$", client["countryOfIncorporation"])
        assert client["taxResidencies"], "taxResidencies must not be empty"
        for jurisdiction in client["taxResidencies"]:
            assert re.match(r"^[A-Z]{2}$", jurisdiction)
        assert client["countryOfDomicile"] in client["taxResidencies"]


async def test_parent_client_links_resolve(db):
    client_ids = set()
    parents = set()
    cursor = db.accounts.find({}, {"_id": 0})
    async for acct in cursor:
        client_ids.add(acct["client"]["clientId"])
        if acct["client"]["parentClientId"]:
            parents.add(acct["client"]["parentClientId"])
    assert parents, "Expected at least one parentClientId link in seed data"
    assert parents <= client_ids, f"Dangling parentClientId(s): {parents - client_ids}"
