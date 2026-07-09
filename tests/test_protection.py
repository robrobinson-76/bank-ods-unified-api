"""GraphQL query-protection and schema-governance tests.

Covers the design gaps identified in the library review: unbounded query
depth, alias amplification of root fields, introspection exposure, and
accidental schema drift.
"""
import pytest
from graphql import build_schema, parse, validate

from bank_ods.graphql.protection import depth_limit_rule, root_fields_limit_rule
from bank_ods.graphql.sdl import generate_sdl
from tests.conftest import gql_query

_TEST_SDL = """
type Child { name: String leaf: String child: Child }
type Query { root: Child }
"""


# ── Depth limit (unit — rule behavior under a controlled recursive schema) ────

def test_depth_rule_rejects_deep_query():
    schema = build_schema(_TEST_SDL)
    deep = "{ root { child { child { child { name } } } } }"  # depth 5
    errors = validate(schema, parse(deep), rules=[depth_limit_rule(3)])
    assert errors and "exceeds maximum allowed depth 3" in errors[0].message


def test_depth_rule_allows_shallow_query():
    schema = build_schema(_TEST_SDL)
    shallow = "{ root { child { name } } }"  # depth 3
    assert validate(schema, parse(shallow), rules=[depth_limit_rule(3)]) == []


def test_depth_rule_follows_fragments():
    schema = build_schema(_TEST_SDL)
    q = """
    { root { ...deep } }
    fragment deep on Child { child { child { child { name } } } }
    """
    errors = validate(schema, parse(q), rules=[depth_limit_rule(3)])
    assert errors and "exceeds maximum allowed depth" in errors[0].message


# ── Root-field / alias amplification limit ────────────────────────────────────

def test_root_fields_rule_rejects_alias_amplification():
    schema = build_schema(_TEST_SDL)
    q = "{ " + " ".join(f"a{i}: root {{ name }}" for i in range(11)) + " }"
    errors = validate(schema, parse(q), rules=[root_fields_limit_rule(10)])
    assert errors and "11 root fields" in errors[0].message


def test_root_fields_rule_allows_normal_query():
    schema = build_schema(_TEST_SDL)
    q = "{ a: root { name } b: root { name } }"
    assert validate(schema, parse(q), rules=[root_fields_limit_rule(10)]) == []


# ── End-to-end against the live Ariadne app (default env: depth 10, roots 10) ─

@pytest.mark.asyncio
async def test_app_rejects_alias_amplification(gql_client, first_account):
    account_id = first_account["accountId"]
    q = "{ " + " ".join(
        f'a{i}: get_account(accountId: "{account_id}") {{ accountId }}' for i in range(11)
    ) + " }"
    resp = await gql_client.post("/graphql/", json={"query": q})
    body = resp.json()
    assert body.get("errors"), "expected validation error for 11 aliased root fields"
    assert "root fields" in body["errors"][0]["message"]
    assert body.get("data") is None  # rejected before any resolver/DB work


@pytest.mark.asyncio
async def test_app_allows_normal_queries(gql_client, first_account):
    q = f'{{ get_account(accountId: "{first_account["accountId"]}") {{ accountId }} }}'
    result = await gql_query(gql_client, q)
    assert "errors" not in result


@pytest.mark.asyncio
async def test_app_introspection_enabled_by_default(gql_client):
    result = await gql_query(gql_client, "{ __schema { queryType { name } } }")
    assert "errors" not in result
    assert result["data"]["__schema"]["queryType"]["name"] == "Query"


# ── Schema snapshot — contract governance ─────────────────────────────────────

def test_schema_matches_snapshot():
    """The generated SDL must match the checked-in snapshot. A diff here means
    the GraphQL contract changed (usually via a model edit); if intentional,
    regenerate with:

        python -c "from bank_ods.graphql.sdl import generate_sdl; \
open('tests/schema.snapshot.graphql','w',newline='\\n').write(generate_sdl())"

    and include the snapshot diff in the same PR so reviewers see the
    contract change explicitly."""
    with open("tests/schema.snapshot.graphql", newline="") as f:
        snapshot = f.read()
    assert generate_sdl().replace("\r\n", "\n") == snapshot.replace("\r\n", "\n")
