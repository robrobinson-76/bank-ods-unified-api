from contextlib import asynccontextmanager

from fastmcp import FastMCP

from bank_ods.db.indexes import ensure_indexes


@asynccontextmanager
async def lifespan(_server):
    await ensure_indexes()
    yield


mcp = FastMCP("bank-ods", lifespan=lifespan)

# Import tools module so all @mcp.tool() decorators run
from bank_ods.mcp import tools  # noqa: E402, F401


if __name__ == "__main__":
    mcp.run()
