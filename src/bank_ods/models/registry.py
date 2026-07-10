"""Entity registry — the single list every layer derives its surface from.

Entities are partitioned into two tiers:

  semantic — curated, normalized models (camelCase fields, typed values).
  raw      — as-received feed records (source field names and wire-format
             values preserved; see the raw_* model docstrings).

``active_entities()`` applies the EXPOSE_*_TIER config flags and is what the
CONSUMER surfaces iterate — SDL generation, the REST raw routers, and the raw
GraphQL resolvers — so a tier flagged off never appears on a consumer transport.
Two things deliberately do NOT gate on the flags: ``ensure_indexes()`` (indexes
track the physical collections, which the loader always writes) and the
internal-only ops server (``bank-ods-ops``), whose whole purpose is raw feed
inspection — both iterate the full ``ENTITIES`` list. ``ENTITIES`` is the flat
list of everything known, independent of flags.
"""
import re

from bank_ods import config

from .account import Account
from .base import BankDocument
from .cash_balance import CashBalance
from .position import Position
from .raw_custody_position import RawCustodyPosition
from .raw_vendor_security import RawVendorSecurity
from .security import Security
from .settlement import Settlement
from .transaction import Transaction

ENTITIES_SEMANTIC: list[type[BankDocument]] = [
    Account,
    Security,
    Transaction,
    Position,
    Settlement,
    CashBalance,
]

ENTITIES_RAW: list[type[BankDocument]] = [
    RawCustodyPosition,
    RawVendorSecurity,
]

# Every entity known to the system, regardless of exposure flags.
ENTITIES: list[type[BankDocument]] = ENTITIES_SEMANTIC + ENTITIES_RAW


def active_entities() -> list[type[BankDocument]]:
    """Entities exposed by this deployment, per the tier flags."""
    result: list[type[BankDocument]] = []
    if config.EXPOSE_SEMANTIC_TIER:
        result.extend(ENTITIES_SEMANTIC)
    if config.EXPOSE_RAW_TIER:
        result.extend(ENTITIES_RAW)
    return result


# ── Derived operation names ───────────────────────────────────────────────────
# One derivation shared by every layer (SDL, resolvers, MCP tools, REST paths,
# parity tests) so the same entity carries the same operation names everywhere.

def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def get_field_name(model: type[BankDocument]) -> str:
    """Get-by-id operation name, e.g. RawCustodyPosition -> get_raw_custody_position."""
    return f"get_{_snake(model.__name__)}"


def list_field_name(model: type[BankDocument]) -> str:
    """List operation name, e.g. RawCustodyPosition -> list_raw_custody_positions."""
    return f"list_{model.COLLECTION}"
