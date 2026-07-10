from contextlib import asynccontextmanager

from fastmcp import FastMCP

from bank_ods.config import LOG_LEVEL
from bank_ods.db.indexes import ensure_indexes
from bank_ods.logging_config import configure_logging


@asynccontextmanager
async def lifespan(_server):
    configure_logging(LOG_LEVEL)  # attaches the log ring the ops tools read
    await ensure_indexes()
    yield


# Operations-persona server: raw feed inspection plus operational tooling for
# engineers, support, and release-monitoring agents. Separate from the
# consumer bank-ods server on purpose — different audience, different
# security posture (internal-only), releasable on its own cadence.
mcp = FastMCP("bank-ods-ops", lifespan=lifespan)

# Raw feed inspection is this server's reason to exist, so the raw tool group
# is always registered here — EXPOSE_RAW_TIER governs whether raw entities
# appear on the CONSUMER transports (REST, GraphQL, bank-ods MCP), not on this
# internal-only ops surface. Gating is by the ops server existing at all
# (TRANSPORT_MCP_OPS_ENABLED), consistent with the operational tools below,
# which also read every registered collection regardless of tier flags.
from bank_ods.mcp.raw_tools import register_raw_tools  # noqa: E402
from bank_ods.mcp_ops import tools  # noqa: E402, F401

register_raw_tools(mcp)


if __name__ == "__main__":
    mcp.run()
