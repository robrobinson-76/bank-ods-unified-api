"""Topic map — the single place a feed is declared.

Adding a feed means adding one TopicSpec row (plus its raw model in the
bank_ods registry and, for non-CDC feeds, its .avsc contract). Topic creation,
schema registration, the sink's collection routing, and the DLQ names all
derive from this table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bank_ods.models.base import BankDocument
from bank_ods.models.raw_cash_movement import RawCashMovement
from bank_ods.models.raw_crm_account_event import RawCrmAccountEvent
from bank_ods.models.raw_crm_client_event import RawCrmClientEvent
from bank_ods.models.raw_custody_position import RawCustodyPosition
from bank_ods.models.raw_vendor_security import RawVendorSecurity

# How the sink turns a Kafka message into a raw-tier document.
#   canonical — the payload IS the raw record (adapters we wrote)
#   debezium  — the payload is a Debezium change envelope to unwrap
#   manifest  — batch control record; lands in ingest_state, not a raw collection
EXTRACTOR_CANONICAL = "canonical"
EXTRACTOR_DEBEZIUM = "debezium"
EXTRACTOR_MANIFEST = "manifest"

DLQ_PREFIX = "ods.dlq."


@dataclass(frozen=True)
class TopicSpec:
    name: str
    partitions: int
    extractor: str
    # Raw-tier model this topic lands as; None for the manifest topic.
    model: Optional[type[BankDocument]] = None
    # Checked-in .avsc contract name; None when Debezium authors the schema.
    schema_name: Optional[str] = None
    # Payload field used as the Kafka message key (canonical topics only).
    key_field: Optional[str] = None
    source_system: str = ""

    @property
    def collection(self) -> str:
        return self.model.COLLECTION if self.model else ""

    @property
    def contract(self) -> str:
        """The checked-in .avsc contract name.

        Only meaningful for topics we author: asking a Debezium-managed topic
        for a contract we own is a programming error, not a missing file.
        """
        if self.schema_name is None:
            raise ValueError(f"{self.name} has a Debezium-managed schema, not an authored one")
        return self.schema_name

    @property
    def dlq(self) -> str:
        """DLQ topic for this feed — the topic name minus the ods.raw. prefix."""
        return DLQ_PREFIX + self.name.removeprefix("ods.raw.")


TOPICS: list[TopicSpec] = [
    TopicSpec(
        name="ods.raw.custody.positions",
        partitions=6,
        extractor=EXTRACTOR_CANONICAL,
        model=RawCustodyPosition,
        schema_name="raw_custody_position",
        key_field="POS_ACCT_NBR",
        source_system="MAINFRAME_CUSTODY",
    ),
    TopicSpec(
        name="ods.raw.custody.batches",
        partitions=1,
        extractor=EXTRACTOR_MANIFEST,
        schema_name="custody_batch_manifest",
        key_field="batchId",
        source_system="MAINFRAME_CUSTODY",
    ),
    TopicSpec(
        name="ods.raw.cash.movements",
        partitions=3,
        extractor=EXTRACTOR_CANONICAL,
        model=RawCashMovement,
        schema_name="raw_cash_movement",
        key_field="MOV_ACCT_NBR",
        source_system="MAINFRAME_CASH",
    ),
    TopicSpec(
        name="ods.raw.vendorsec.securities",
        partitions=3,
        extractor=EXTRACTOR_CANONICAL,
        model=RawVendorSecurity,
        schema_name="raw_vendor_security",
        key_field="Vendor_Ref",
        source_system="VENDORSEC_SAAS",
    ),
    # CDC topics: created by us (auto-create is off on the broker) but written
    # by Debezium, which registers its own schemas.
    TopicSpec(
        name="ods.raw.crm.clients",
        partitions=3,
        extractor=EXTRACTOR_DEBEZIUM,
        model=RawCrmClientEvent,
        source_system="CRM_PG",
    ),
    TopicSpec(
        name="ods.raw.crm.accounts",
        partitions=3,
        extractor=EXTRACTOR_DEBEZIUM,
        model=RawCrmAccountEvent,
        source_system="CRM_PG",
    ),
]

BY_NAME: dict[str, TopicSpec] = {t.name: t for t in TOPICS}


def authored_topics() -> list[TopicSpec]:
    """Topics whose Avro contract we author and check in (i.e. not Debezium's)."""
    return [t for t in TOPICS if t.schema_name]


def get(name: str) -> TopicSpec:
    return BY_NAME[name]
