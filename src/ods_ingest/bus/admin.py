"""Topic and schema provisioning.

    python -m ods_ingest.bus.admin            # create topics, register schemas
    python -m ods_ingest.bus.admin --dry-run  # show what would change
    python -m ods_ingest.bus.admin --check    # exit 1 unless everything exists

Idempotent: safe to re-run. Broker auto-create is deliberately off, so every
topic this system uses — including the CDC topics Debezium writes and the DLQs
— is declared in ods_ingest/topics.py and created here.
"""
from __future__ import annotations

import argparse
import logging
import sys

from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.schema_registry import Schema

from ods_ingest import config, topics
from ods_ingest.bus.producer import schema_registry_client
from ods_ingest.schemas import load_schema_str

log = logging.getLogger("ods_ingest.admin")

# BACKWARD: a new schema can read data written with the old one, so consumers
# upgrade first and fields may only be added with defaults. See
# docs/ARCHITECTURE-ingestion.md → schema evolution governance.
COMPATIBILITY = "BACKWARD"


def desired_topics() -> dict[str, int]:
    """Topic name → partition count, including the per-feed DLQs."""
    wanted: dict[str, int] = {}
    for spec in topics.TOPICS:
        wanted[spec.name] = spec.partitions
        wanted[spec.dlq] = 1  # DLQs are low-volume; ordering across them is meaningless
    return wanted


def existing_topics(admin: AdminClient) -> set[str]:
    md = admin.list_topics(timeout=15)
    return set(md.topics.keys())


def create_topics(admin: AdminClient, missing: dict[str, int]) -> None:
    new = [
        NewTopic(
            name,
            num_partitions=parts,
            replication_factor=1,  # single-broker prototype
            config={
                "retention.ms": str(config.TOPIC_RETENTION_MS),
                "cleanup.policy": "delete",
            },
        )
        for name, parts in missing.items()
    ]
    for name, fut in admin.create_topics(new).items():
        try:
            fut.result()
            print(f"  created topic {name}")
        except Exception as exc:  # noqa: BLE001 — report and continue
            if "already exists" in str(exc):
                print(f"  topic {name} already exists")
            else:
                raise


def register_schemas() -> None:
    """Register the checked-in contracts and pin subject compatibility.

    Only for topics we author — Debezium registers its own CDC subjects, and
    writing them here would create a second source of truth.
    """
    client = schema_registry_client()
    for spec in topics.authored_topics():
        subject = f"{spec.name}-value"
        schema = Schema(load_schema_str(spec.contract), schema_type="AVRO")
        schema_id = client.register_schema(subject, schema)
        client.set_compatibility(subject_name=subject, level=COMPATIBILITY)
        print(f"  registered {subject} (id={schema_id}, compatibility={COMPATIBILITY})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show changes, apply nothing")
    parser.add_argument("--check", action="store_true",
                        help="verify provisioning; exit 1 if anything is missing")
    args = parser.parse_args(argv)

    admin = AdminClient({"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS})
    wanted = desired_topics()
    present = existing_topics(admin)
    missing = {n: p for n, p in wanted.items() if n not in present}

    print(f"broker={config.KAFKA_BOOTSTRAP_SERVERS} registry={config.SCHEMA_REGISTRY_URL}")
    print(f"topics: {len(wanted)} declared, {len(wanted) - len(missing)} present, "
          f"{len(missing)} missing")

    if args.check:
        for name in sorted(missing):
            print(f"  MISSING {name}")
        return 1 if missing else 0

    if args.dry_run:
        for name in sorted(missing):
            print(f"  would create {name} ({wanted[name]} partitions)")
        for spec in topics.authored_topics():
            print(f"  would register {spec.name}-value from {spec.contract}.avsc")
        return 0

    if missing:
        print("creating topics...")
        create_topics(admin, missing)
    print("registering schemas...")
    register_schemas()
    print("done.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=config.LOG_LEVEL)
    sys.exit(main())
