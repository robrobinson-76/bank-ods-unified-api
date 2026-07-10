from contextlib import asynccontextmanager

from fastmcp import FastMCP

from bank_ods.db.indexes import ensure_indexes


@asynccontextmanager
async def lifespan(_server):
    await ensure_indexes()
    yield


# Consumer-persona server: clean semantic domain tools for AI agents and
# downstream teams. Raw feed inspection and operational tooling live on the
# separate bank-ods-ops server (bank_ods/mcp_ops) with its own security
# posture and release cadence.
mcp = FastMCP("bank-ods", lifespan=lifespan)

from bank_ods import config  # noqa: E402

if config.EXPOSE_SEMANTIC_TIER:
    from bank_ods.mcp import tools  # noqa: E402, F401


if __name__ == "__main__":
    mcp.run()
