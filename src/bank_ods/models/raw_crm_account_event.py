from __future__ import annotations

from typing import ClassVar, Optional

from .base import BankDocument, IndexSpec


class CrmAccountState(BankDocument):
    """An `accounts` row as it existed in the legacy CRM at one point in time.

    Source column names and code values preserved verbatim; all values carried
    as strings per the raw-tier convention (dates arrive as the source's own
    "CCYY-MM-DD", not as typed dates).
    """

    COLLECTION: ClassVar[str] = ""  # embedded document — not a collection
    INDEXES: ClassVar[list[IndexSpec]] = []

    account_nbr: str
    client_id: Optional[str] = None
    account_name: Optional[str] = None
    account_type: Optional[str] = None  # custody / proprietary / omnibus
    base_ccy: Optional[str] = None
    status: Optional[str] = None  # active / suspended / closed
    open_date: Optional[str] = None
    close_date: Optional[str] = None
    branch: Optional[str] = None
    updated_at: Optional[str] = None


class RawCrmAccountEvent(BankDocument):
    """Raw-tier: append-only log of CRM `accounts` change events.

    Same shape and rules as RawCrmClientEvent — see that model's docstring for
    the OP codes, the before/after image semantics, and why EVENT_ID makes
    re-delivery idempotent. EVENT_ID here is "<LSN>-accounts-<PK>".
    """

    COLLECTION: ClassVar[str] = "raw_crm_account_events"
    INDEXES: ClassVar[list[IndexSpec]] = [
        ("EVENT_ID", {"unique": True}),
        ([("PK", 1), ("LSN", 1)], {}),
        ("OP", {}),
    ]
    ID_FIELD: ClassVar[str] = "EVENT_ID"
    DEFAULT_SORT: ClassVar[list[tuple[str, int]]] = [("EVENT_ID", 1)]
    UNFILTERED_LIST: ClassVar[bool] = True

    EVENT_ID: str  # sink-assigned: "<LSN>-accounts-<PK>"
    OP: str  # r | c | u | d
    PK: str  # source primary key (account_nbr)
    LSN: str  # Postgres log sequence number, as delivered
    TS_MS: str  # source commit time, epoch millis as delivered
    SOURCE_TABLE: str  # "accounts"
    BEFORE: Optional[CrmAccountState] = None
    AFTER: Optional[CrmAccountState] = None
