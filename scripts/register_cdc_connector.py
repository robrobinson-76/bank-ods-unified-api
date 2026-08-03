"""Register (or remove) the Debezium Postgres connector for the legacy CRM.

    python scripts/register_cdc_connector.py            # create / update
    python scripts/register_cdc_connector.py --status
    python scripts/register_cdc_connector.py --delete
    python scripts/register_cdc_connector.py --restart  # after a task failure

Idempotent: the config is PUT, so re-running updates in place rather than
failing on a duplicate.

Two details carry the design decisions:

  * NO unwrap SMT. The full Debezium envelope (op/before/after/source) is the
    payload, because the raw tier lands an append-only CHANGE LOG rather than a
    latest-state mirror — the op code and the before image are data.
  * A RegexRouter renames crm.public.<table> to ods.raw.crm.<table>, so CDC
    topics follow the same ods.raw.<source>.<entity> convention as every other
    feed and the generic sink needs no special case.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from ods_ingest import config

CONNECTOR_NAME = "crm-postgres-cdc"

# The Debezium image ships Apicurio's Avro converter (ENABLE_APICURIO_CONVERTERS).
# as-confluent=true makes it emit the standard Confluent wire format — magic
# byte + 4-byte schema id — which python's confluent_kafka decodes natively.
# Both sides therefore share one wire format and one registry.
CONVERTER = "io.apicurio.registry.utils.converter.AvroConverter"
REGISTRY_INTERNAL_URL = "http://schema-registry:8080/apis/registry/v2"


def build_config(registry_url: str) -> dict:
    return {
        "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
        "tasks.max": "1",
        "database.hostname": "postgres-crm",
        "database.port": "5432",
        "database.user": "crm",
        "database.password": "crm",
        "database.dbname": "crm",
        "topic.prefix": "crm",
        "plugin.name": "pgoutput",
        "publication.name": "dbz_publication",
        "slot.name": "dbz_crm_slot",
        "table.include.list": "public.clients,public.accounts",
        "snapshot.mode": "initial",
        # We land deletes as change events; a null tombstone carries no state
        # and the sink would have nothing to write.
        "tombstones.on.delete": "false",
        # Decimals as strings keeps the raw tier's "values as delivered" rule.
        "decimal.handling.mode": "string",
        "time.precision.mode": "connect",

        "key.converter": CONVERTER,
        "key.converter.apicurio.registry.url": registry_url,
        "key.converter.apicurio.registry.auto-register": "true",
        "key.converter.apicurio.registry.find-latest": "true",
        "key.converter.apicurio.registry.as-confluent": "true",
        # The id written into the Confluent-format wire header must be the one
        # the ccompat API resolves, which is Apicurio's contentId. With globalId
        # the two spaces coincide only by luck for the first schema version and
        # diverge the moment a second version is registered — every record on
        # the new schema then dead-letters with "No content with id …".
        "key.converter.apicurio.registry.use-id": "contentId",
        "key.converter.apicurio.registry.headers.enabled": "false",
        "value.converter": CONVERTER,
        "value.converter.apicurio.registry.url": registry_url,
        "value.converter.apicurio.registry.auto-register": "true",
        "value.converter.apicurio.registry.find-latest": "true",
        "value.converter.apicurio.registry.as-confluent": "true",
        "value.converter.apicurio.registry.use-id": "contentId",
        "value.converter.apicurio.registry.headers.enabled": "false",

        "transforms": "route",
        "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter",
        "transforms.route.regex": r"crm\.public\.(.*)",
        "transforms.route.replacement": r"ods.raw.crm.$1",
    }


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    url = f"{config.CONNECT_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def reset_connector() -> None:
    """Clear everything that makes the connector resume instead of re-snapshot.

    Deleting a connector does NOT delete its committed source offsets — they
    live in the Connect offsets topic keyed by connector name — nor the
    Postgres replication slot. Both must go for `snapshot.mode: initial` to
    mean "snapshot again".
    """
    import time

    # Stopping first is required before the offsets endpoint will accept a reset.
    _request("PUT", f"/connectors/{CONNECTOR_NAME}/stop")
    time.sleep(2)
    status, body = _request("DELETE", f"/connectors/{CONNECTOR_NAME}/offsets")
    print(f"  offsets reset -> {status} {body[:120]}")
    _request("DELETE", f"/connectors/{CONNECTOR_NAME}")
    time.sleep(2)

    # Drop the slot so the new snapshot starts from a clean logical position.
    try:
        import psycopg

        with psycopg.connect(config.CRM_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
                "WHERE slot_name = %s",
                ("dbz_crm_slot",),
            )
            print(f"  replication slot dropped: {cur.rowcount > 0}")
    except Exception as exc:  # noqa: BLE001 — a missing slot is not an error here
        print(f"  replication slot not dropped ({exc})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--reset", action="store_true",
                        help="delete the connector, its stored offsets, and the replication "
                             "slot, then re-register — forces a fresh snapshot")
    parser.add_argument("--registry-url", default=REGISTRY_INTERNAL_URL,
                        help="registry URL as seen from inside the Connect container")
    args = parser.parse_args(argv)

    if args.delete:
        status, body = _request("DELETE", f"/connectors/{CONNECTOR_NAME}")
        print(f"delete -> {status} {body[:200]}")
        return 0 if status in (204, 404) else 1

    if args.reset:
        reset_connector()
        status, body = _request("PUT", f"/connectors/{CONNECTOR_NAME}/config",
                                build_config(args.registry_url))
        if status not in (200, 201):
            print(f"re-registration failed: {status}\n{body[:2000]}", file=sys.stderr)
            return 1
        print(f"reset and re-registered {CONNECTOR_NAME} — a fresh snapshot will run")
        return 0

    if args.status:
        status, body = _request("GET", f"/connectors/{CONNECTOR_NAME}/status")
        if status == 404:
            print("connector not registered")
            return 1
        print(json.dumps(json.loads(body), indent=2))
        return 0

    if args.restart:
        status, body = _request("POST", f"/connectors/{CONNECTOR_NAME}/restart?includeTasks=true")
        print(f"restart -> {status} {body[:200]}")
        return 0

    status, body = _request("PUT", f"/connectors/{CONNECTOR_NAME}/config",
                            build_config(args.registry_url))
    if status not in (200, 201):
        print(f"registration failed: {status}\n{body[:2000]}", file=sys.stderr)
        return 1
    print(f"registered {CONNECTOR_NAME} -> {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
