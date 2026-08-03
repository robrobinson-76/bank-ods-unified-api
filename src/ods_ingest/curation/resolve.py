"""Resolving source identifiers to ODS identifiers.

The mainframe keys on its own account numbers and on CUSIP/ISIN; the ODS keys
on accountId and securityId. This module owns that translation and caches the
lookup tables for the life of a curation run.

The per-record resolution rules are deliberately the same ones
services/ops.py:reconcile_custody_feed applies, so "why didn't this record
appear?" and "why wasn't this record curated?" always give the same answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bank_ods.services._common import account_id_from_custody, cusip_from_isin

from ods_ingest import state


@dataclass
class ReferenceIndex:
    """Cached account and security lookups for one curation run."""

    account_ids: set[str] = field(default_factory=set)
    sec_by_cusip: dict[str, str] = field(default_factory=dict)
    sec_by_isin: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "ReferenceIndex":
        db = state.get_db()
        index = cls()
        index.account_ids = {
            d["accountId"] for d in db["accounts"].find({}, {"accountId": 1})
        }
        for doc in db["securities"].find({}, {"securityId": 1, "cusip": 1, "isin": 1}):
            if doc.get("cusip"):
                index.sec_by_cusip[doc["cusip"]] = doc["securityId"]
            if doc.get("isin"):
                index.sec_by_isin[doc["isin"]] = doc["securityId"]
                # Feeds often carry the CUSIP embedded in a US/CA ISIN even when
                # the curated master stores only the ISIN.
                embedded = cusip_from_isin(doc["isin"])
                if embedded:
                    index.sec_by_cusip.setdefault(embedded, doc["securityId"])
        return index

    def account(self, custody_nbr: str) -> Optional[str]:
        """Custody account number to accountId, or None when unknown/malformed."""
        account_id = account_id_from_custody(custody_nbr or "")
        if account_id is None or account_id not in self.account_ids:
            return None
        return account_id

    def security(self, cusip: Optional[str], isin: Optional[str]) -> Optional[str]:
        """CUSIP first, then ISIN — the order the mainframe populates them."""
        if cusip:
            found = self.sec_by_cusip.get(cusip)
            if found:
                return found
        if isin:
            return self.sec_by_isin.get(isin)
        return None
