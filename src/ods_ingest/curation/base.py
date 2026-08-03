"""Shared curation machinery.

Every curator is a consumer group over one or more raw topics that upserts into
a semantic collection by that collection's natural key. Two rules hold for all
of them:

  * idempotent — replaying a topic converges rather than duplicating, because
    the write key is the domain key, not the delivery.
  * convergent — topics are per-(source, entity) with NO cross-entity ordering
    guarantee, so a curator must reach the same end state regardless of the
    order two related records arrive in.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from bson.decimal128 import Decimal128
from pymongo import UpdateOne

from ods_ingest import state

log = logging.getLogger("ods_ingest.curation")


def to_decimal128(value: Decimal) -> Decimal128:
    """Money and quantities are stored as Decimal128, never IEEE-754."""
    return Decimal128(value)


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class CurationStats:
    """What a curation run did — the numbers the e2e tests assert on.

    `seen` counts every record the curator was handed, so
    ``curated + skipped == seen`` is an invariant: a record can never be
    silently neither curated nor accounted for.
    """

    seen: int = 0
    curated: int = 0
    skipped_unknown_account: int = 0
    skipped_unknown_security: int = 0
    skipped_malformed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        if reason == "UNKNOWN_ACCOUNT":
            self.skipped_unknown_account += 1
        elif reason == "UNKNOWN_SECURITY":
            self.skipped_unknown_security += 1
        else:
            self.skipped_malformed += 1

    @property
    def skipped(self) -> int:
        return sum(self.reasons.values())

    def as_dict(self) -> dict[str, Any]:
        return {"seen": self.seen, "curated": self.curated, "skipped": self.skipped,
                "reasons": dict(self.reasons)}


def bulk_upsert(collection: str, operations: list[UpdateOne]) -> int:
    """Apply upserts to a semantic collection; returns documents affected."""
    if not operations:
        return 0
    result = state.get_db()[collection].bulk_write(operations, ordered=False)
    return result.upserted_count + result.matched_count


def set_on_insert_timestamps(doc: dict, now: Optional[datetime] = None) -> tuple[dict, dict]:
    """Split a document into ($set, $setOnInsert) parts.

    createdAt must survive re-curation of the same record; updatedAt must not.
    """
    stamp = now or utc_now()
    return {**doc, "updatedAt": stamp}, {"createdAt": stamp}
