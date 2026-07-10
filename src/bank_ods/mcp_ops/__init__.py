"""Operations/debug MCP server (bank-ods-ops) — the second MCP persona.

Audience: engineers, QA, support, and release-monitoring agents. Tools cover
raw feed inspection, collection health, data freshness, raw-vs-curated
reconciliation, and in-process logs. Internal-only by intent: deploy behind
the platform boundary, never on the consumer path.
"""
from bank_ods.mcp_ops.server import mcp

__all__ = ["mcp"]
