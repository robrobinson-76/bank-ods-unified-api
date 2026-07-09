"""Shared fixtures for all test modules.

Requires a running MongoDB populated by `python scripts/seed_data.py`.
"""
import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from bank_ods.db.client import get_db
from bank_ods.db.indexes import ensure_indexes
from bank_ods.rest.app import app as rest_app
from bank_ods.graphql.app import app as graphql_app
from bank_ods.graphql_strawberry.app import app as strawberry_app
from bank_ods.graphql_graphene.app import app as graphene_app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db():
    await ensure_indexes()
    return get_db()


@pytest_asyncio.fixture(scope="session")
async def first_account(db):
    doc = await db.accounts.find_one({}, {"_id": 0})
    assert doc is not None, "No accounts found — run scripts/seed_data.py first"
    return doc


@pytest_asyncio.fixture(scope="session")
async def first_balance(db, first_account):
    doc = await db.cash_balances.find_one(
        {"accountId": first_account["accountId"]}, {"_id": 0}
    )
    assert doc is not None, "No balances found — run scripts/seed_data.py first"
    return doc


@pytest_asyncio.fixture(scope="session")
async def first_settled_txn(db, first_account):
    doc = await db.transactions.find_one(
        {"accountId": first_account["accountId"], "status": "SETTLED"},
        {"_id": 0},
    )
    assert doc is not None, "No settled transactions found"
    return doc


@pytest_asyncio.fixture(scope="session")
async def rest_client():
    async with AsyncClient(
        transport=ASGITransport(app=rest_app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="session")
async def gql_client():
    async with AsyncClient(
        transport=ASGITransport(app=graphql_app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="session")
async def sb_client():
    """Client for the side-by-side Strawberry GraphQL app (same contract as gql_client)."""
    async with AsyncClient(
        transport=ASGITransport(app=strawberry_app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="session")
async def gr_client():
    """Client for the side-by-side Graphene GraphQL app (same contract as gql_client)."""
    async with AsyncClient(
        transport=ASGITransport(app=graphene_app), base_url="http://test"
    ) as client:
        yield client


async def gql_query(client: AsyncClient, query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = await client.post("/graphql/", json=payload)
    resp.raise_for_status()
    return resp.json()
