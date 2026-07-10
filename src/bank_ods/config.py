import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB: str = os.getenv("MONGODB_DB", "bank_ods")

# Connection / query timeout in milliseconds (default: 10s for K8s pod restarts)
MONGO_TIMEOUT_MS: int = int(os.getenv("MONGO_TIMEOUT_MS", "10000"))

DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() == "true"


# Transport enablement — everything runs in dev by default; a deployment turns
# individual transports off until each is cleared for exposure. Each transport
# checks its own flag at startup and refuses to serve when disabled.
#
# The two MCP servers are separate transports on purpose (dual persona):
# TRANSPORT_MCP_ENABLED gates the consumer server (bank-ods, semantic tools,
# productionized with REST/GraphQL); TRANSPORT_MCP_OPS_ENABLED gates the
# operations server (bank-ods-ops, raw + operational tools, internal-only,
# releasable on its own cadence).
TRANSPORT_REST_ENABLED: bool = _flag("TRANSPORT_REST_ENABLED")
TRANSPORT_GRAPHQL_ENABLED: bool = _flag("TRANSPORT_GRAPHQL_ENABLED")
TRANSPORT_MCP_ENABLED: bool = _flag("TRANSPORT_MCP_ENABLED")
TRANSPORT_MCP_OPS_ENABLED: bool = _flag("TRANSPORT_MCP_OPS_ENABLED")

# Data-tier exposure — the registry (models/registry.py) partitions entities
# into a semantic tier (curated models) and a raw tier (as-received feed
# records). These flags decide which tiers the SDL, REST routers, MCP tool
# groups, and index bootstrap pick up. Flag-gating is the interim control; a
# per-caller permission model can replace it without changing the grouping.
EXPOSE_SEMANTIC_TIER: bool = _flag("EXPOSE_SEMANTIC_TIER")
EXPOSE_RAW_TIER: bool = _flag("EXPOSE_RAW_TIER")

# GraphQL query protection (see graphql/protection.py)
GRAPHQL_MAX_DEPTH: int = int(os.getenv("GRAPHQL_MAX_DEPTH", "10"))
GRAPHQL_MAX_ROOT_FIELDS: int = int(os.getenv("GRAPHQL_MAX_ROOT_FIELDS", "10"))
GRAPHQL_INTROSPECTION: bool = os.getenv("GRAPHQL_INTROSPECTION", "true").lower() == "true"
