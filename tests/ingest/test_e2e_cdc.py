"""End-to-end: legacy CRM database -> Debezium -> raw change log -> accounts.

Exercises the pattern's distinctive behaviours: snapshot plus streaming capture
with no change to the source application, an append-only change log in the raw
tier, client-master fan-out, soft deletes, and convergence when related events
arrive out of order.

These tests mutate the shared `accounts` collection, so each cleans up after
itself — the core suite asserts on the seeded state.
"""
from __future__ import annotations

import json
import time
import uuid

import psycopg
import pytest

from ods_ingest import config, state
from ods_ingest.bus.consumer import BatchConsumer
from ods_ingest.curation import crm_accounts
from ods_ingest.sink import mapping, writer

import scripts.crm_mutate as mutate

pytestmark = pytest.mark.ingest

CDC_SETTLE_S = 8


def _crm_reachable() -> bool:
    try:
        with psycopg.connect(config.CRM_DSN, connect_timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module", autouse=True)
def require_cdc():
    """Skip unless the CRM database and a running connector are both present."""
    if not _crm_reachable():
        pytest.skip(f"CRM postgres unavailable at {config.CRM_DSN}")

    import urllib.request
    try:
        with urllib.request.urlopen(
            f"{config.CONNECT_URL}/connectors/crm-postgres-cdc/status", timeout=5
        ) as resp:
            status = json.loads(resp.read())
    except Exception:  # noqa: BLE001
        pytest.skip("Debezium connector not registered: "
                    "run python scripts/register_cdc_connector.py")
    if status.get("connector", {}).get("state") != "RUNNING":
        pytest.skip(f"connector not RUNNING: {status.get('connector')}")


def _mutate(scenario: str, **kwargs) -> dict:
    """Run a named CRM mutation and return what it reported."""
    argv = [scenario]
    for key, value in kwargs.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert mutate.main(argv) == 0
    return json.loads(buffer.getvalue().strip().splitlines()[-1])


def _drain(sink_group: str, curation_group: str) -> crm_accounts.CurationStats:
    """Land the CDC backlog and curate it, with fresh groups each time."""
    time.sleep(CDC_SETTLE_S)  # let Debezium commit the change to the topic
    consumer = BatchConsumer(
        mapping.sink_topics(), group_id=sink_group, handler=writer.handle, stage="sink"
    )
    try:
        consumer.run_until_idle(idle_timeout=8)
    finally:
        consumer.close()
    return crm_accounts.run(once=True, idle_timeout=8, group_id=curation_group)


@pytest.fixture
def groups():
    token = uuid.uuid4().hex[:10]
    return f"test-sink-{token}", f"test-crm-{token}"


# ── snapshot + streaming capture ─────────────────────────────────────────────

def test_snapshot_landed_the_source_tables_as_change_events(db, groups):
    """Debezium's initial snapshot appears as op='r' events in the raw log.

    Nothing was installed in the legacy application to make this happen — the
    whole point of the CDC pattern.
    """
    _drain(*groups)

    clients = db["raw_crm_client_events"].count_documents({"OP": "r"})
    accounts = db["raw_crm_account_events"].count_documents({"OP": "r"})
    assert clients > 0 and accounts > 0

    event = db["raw_crm_account_events"].find_one({"OP": "r"})
    # Snapshot reads carry no prior image, and a full after image.
    assert event["BEFORE"] is None
    assert event["AFTER"]["account_nbr"] == event["PK"]
    assert event["SOURCE_TABLE"] == "accounts"
    assert event["EVENT_ID"] == f"{event['LSN']}-accounts-{event['PK']}"


def test_insert_flows_through_to_a_curated_account(db, groups):
    """A brand-new client and its accounts appear in the ODS, fully populated."""
    suffix = uuid.uuid4().hex[:6].upper()
    created = _mutate("new-client", suffix=suffix)
    try:
        _drain(*groups)

        for account_nbr in created["accounts"]:
            account = db["accounts"].find_one({"accountId": account_nbr})
            assert account is not None, f"{account_nbr} was never curated"
            assert account["status"] == "ACTIVE"
            assert account["baseCurrency"] == "GBP"
            # The embedded client master is fully resolved, not a placeholder.
            assert account["client"]["clientId"] == created["clientId"]
            assert account["client"]["clientName"].startswith("Northwind Capital")
            assert account["client"]["classification"] == "PROFESSIONAL"
            assert account["client"]["kycStatus"] == "PENDING_REVIEW"
            # Source stores a comma-joined string; curation produces a list.
            assert account["client"]["taxResidencies"] == ["GB", "US"]
    finally:
        _cleanup_client(db, created["clientId"], created["accounts"])


def test_client_update_fans_out_to_every_account(db, groups):
    """One client row changing must update every account embedding it.

    The client master is denormalized onto each account, so a single-document
    update would leave the snapshots inconsistent — an invariant
    tests/test_master_data.py enforces.
    """
    suffix = uuid.uuid4().hex[:6].upper()
    created = _mutate("new-client", suffix=suffix)
    try:
        _drain(*groups)
        assert db["accounts"].count_documents(
            {"client.clientId": created["clientId"], "client.kycStatus": "PENDING_REVIEW"}
        ) == 2

        _mutate("kyc-flip", id=created["clientId"])
        _drain(f"{groups[0]}-b", f"{groups[1]}-b")

        updated = list(db["accounts"].find({"client.clientId": created["clientId"]}))
        assert len(updated) == 2
        assert all(a["client"]["kycStatus"] == "EXPIRED" for a in updated), (
            "the client-master change did not reach every account"
        )
        # And the snapshots remain identical to one another.
        assert updated[0]["client"] == updated[1]["client"]
    finally:
        _cleanup_client(db, created["clientId"], created["accounts"])


# ── soft delete ──────────────────────────────────────────────────────────────

def test_source_delete_becomes_a_status_transition(db, groups):
    """A deleted account is CLOSED in the ODS, never removed.

    The reviewed decision: the ODS is a read-only view with no physical
    deletes; the delete event itself survives in the raw tier.
    """
    suffix = uuid.uuid4().hex[:6].upper()
    created = _mutate("new-client", suffix=suffix)
    victim = created["accounts"][0]
    try:
        _drain(*groups)
        assert db["accounts"].find_one({"accountId": victim})["status"] == "ACTIVE"

        _mutate("delete-account", id=victim)
        _drain(f"{groups[0]}-b", f"{groups[1]}-b")

        account = db["accounts"].find_one({"accountId": victim})
        assert account is not None, "the document must not be physically removed"
        assert account["status"] == "CLOSED"
        assert account["closeDate"] is not None

        # The delete event is retained in the raw change log, with the state
        # that was deleted (REPLICA IDENTITY FULL gives us the before image).
        delete_event = db["raw_crm_account_events"].find_one({"PK": victim, "OP": "d"})
        assert delete_event is not None
        assert delete_event["AFTER"] is None
        assert delete_event["BEFORE"]["account_nbr"] == victim
    finally:
        _cleanup_client(db, created["clientId"], created["accounts"])


def test_client_offboarding_closes_all_their_accounts(db, groups):
    """Deleting a client closes every account of that client."""
    suffix = uuid.uuid4().hex[:6].upper()
    created = _mutate("new-client", suffix=suffix)
    try:
        _drain(*groups)
        _mutate("offboard-client", id=created["clientId"])
        _drain(f"{groups[0]}-b", f"{groups[1]}-b")

        for account_nbr in created["accounts"]:
            account = db["accounts"].find_one({"accountId": account_nbr})
            assert account is not None
            assert account["status"] == "CLOSED", f"{account_nbr} was not closed"
            # The client snapshot is kept: it is the record of who they were.
            assert account["client"]["clientId"] == created["clientId"]
    finally:
        _cleanup_client(db, created["clientId"], created["accounts"])


# ── convergence ──────────────────────────────────────────────────────────────

def test_curation_converges_when_the_client_arrives_after_its_accounts(db, groups):
    """Cross-entity ordering is explicitly NOT guaranteed by the topic design.

    Simulated by curating the accounts topic alone first — the account lands
    with a placeholder client — then curating both. The end state must be the
    same as if they had arrived in order.
    """
    suffix = uuid.uuid4().hex[:6].upper()
    created = _mutate("new-client", suffix=suffix)
    try:
        time.sleep(CDC_SETTLE_S)
        # Land the raw tier, but curate ONLY the accounts leg first.
        consumer = BatchConsumer(
            mapping.sink_topics(), group_id=groups[0], handler=writer.handle, stage="sink"
        )
        try:
            consumer.run_until_idle(idle_timeout=8)
        finally:
            consumer.close()

        accounts_only = uuid.uuid4().hex[:8]
        stats = CurationOfAccountsOnly(f"acct-only-{accounts_only}").run()
        assert stats.curated > 0

        account = db["accounts"].find_one({"accountId": created["accounts"][0]})
        assert account is not None, "the account must be queryable immediately"

        # Now curate both legs, in the opposite order to the natural one.
        crm_accounts.run(once=True, idle_timeout=8, group_id=f"conv-{accounts_only}")

        final = db["accounts"].find_one({"accountId": created["accounts"][0]})
        assert final["client"]["clientName"].startswith("Northwind Capital"), (
            "the client fan-out did not complete the placeholder snapshot"
        )
        assert final["client"]["classification"] == "PROFESSIONAL"
    finally:
        _cleanup_client(db, created["clientId"], created["accounts"])


class CurationOfAccountsOnly:
    """Curate only the accounts topic, to force the out-of-order case."""

    def __init__(self, group_id: str):
        self.group_id = group_id

    def run(self) -> crm_accounts.CurationStats:
        stats = crm_accounts.CurationStats()
        consumer = BatchConsumer(
            ["ods.raw.crm.accounts"],
            group_id=self.group_id,
            handler=lambda records: crm_accounts.curate_batch(records, stats),
            stage="curation",
        )
        try:
            consumer.run_until_idle(idle_timeout=8)
        finally:
            consumer.close()
        return stats


# ── idempotency ──────────────────────────────────────────────────────────────

def test_replaying_the_change_log_is_idempotent(db, groups):
    """Connector restarts re-emit events; EVENT_ID makes that harmless."""
    _drain(*groups)
    before = db["raw_crm_account_events"].count_documents({})

    # A fresh group re-reads every change event from the beginning.
    consumer = BatchConsumer(
        mapping.sink_topics(), group_id=f"{groups[0]}-replay",
        handler=writer.handle, stage="sink",
    )
    try:
        consumer.run_until_idle(idle_timeout=8)
    finally:
        consumer.close()

    after = db["raw_crm_account_events"].count_documents({})
    assert after == before, "replay duplicated change events"


# ── helpers ──────────────────────────────────────────────────────────────────

def _cleanup_client(db, client_id: str, account_nbrs: list[str]) -> None:
    """Remove everything a test created, from both the ODS and the CRM.

    The core suite asserts on the seeded 20 accounts, so test artefacts cannot
    be left behind.
    """
    db["accounts"].delete_many({"accountId": {"$in": account_nbrs}})
    db["raw_crm_account_events"].delete_many({"PK": {"$in": account_nbrs}})
    db["raw_crm_client_events"].delete_many({"PK": client_id})
    try:
        with psycopg.connect(config.CRM_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE client_id = %s", (client_id,))
            cur.execute("DELETE FROM clients WHERE client_id = %s", (client_id,))
    except Exception:  # noqa: BLE001 — cleanup must not mask a test failure
        pass
