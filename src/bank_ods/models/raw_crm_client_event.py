from __future__ import annotations

from typing import ClassVar, Optional

from .base import BankDocument, IndexSpec


class CrmClientState(BankDocument):
    """A `clients` row as it existed in the legacy CRM at one point in time.

    Column names are the source system's own (lowercase snake_case) and values
    are the source's own code lists ("professional", "approved") — nothing is
    normalized before the raw tier. Every value is carried as a string,
    including the timestamp, per the raw-tier convention.
    """

    COLLECTION: ClassVar[str] = ""  # embedded document — not a collection
    INDEXES: ClassVar[list[IndexSpec]] = []

    client_id: str
    client_name: Optional[str] = None
    lei: Optional[str] = None
    country_domicile: Optional[str] = None
    country_incorp: Optional[str] = None
    tax_residencies: Optional[str] = None  # comma-separated, never normalized by the source
    classification: Optional[str] = None
    kyc_status: Optional[str] = None
    risk_rating: Optional[str] = None
    legal_entity_type: Optional[str] = None
    parent_client_id: Optional[str] = None
    updated_at: Optional[str] = None


class RawCrmClientEvent(BankDocument):
    """Raw-tier: append-only log of CRM `clients` change events.

    One document per Debezium change event — this is a change LOG, not a
    latest-state mirror: an update writes a new document rather than replacing
    the previous one, so the full history (and every delete) survives in the
    raw tier even after the 7-day topic retention window closes.

    - OP is Debezium's operation code: "r" (snapshot read), "c" (create),
      "u" (update), "d" (delete).
    - BEFORE is null on inserts and snapshot reads; AFTER is null on deletes.
      Both images are complete because the source tables are REPLICA IDENTITY
      FULL (see infra/crm/init.sql).
    - LSN is the Postgres log sequence number — the source-order position,
      and what makes EVENT_ID deterministic under connector restarts.

    EVENT_ID is assigned by the sink: "<LSN>-clients-<PK>". Re-delivery of the
    same change (connector restart, snapshot re-run) therefore upserts the
    same document instead of duplicating it.
    """

    COLLECTION: ClassVar[str] = "raw_crm_client_events"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("EVENT_ID", {"unique": True}),
        ([("PK", 1), ("LSN", 1)], {}),
        ("OP", {}),
    ]
    ID_FIELD: ClassVar[str] = "EVENT_ID"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("EVENT_ID", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    EVENT_ID: str  # sink-assigned: "<LSN>-clients-<PK>"
    OP: str  # r | c | u | d
    PK: str  # source primary key (client_id)
    LSN: str  # Postgres log sequence number, as delivered
    TS_MS: str  # source commit time, epoch millis as delivered
    SOURCE_TABLE: str  # "clients"
    BEFORE: Optional[CrmClientState] = None
    AFTER: Optional[CrmClientState] = None
