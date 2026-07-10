import os
from typing import Literal, cast, get_args

from bank_ods import config
from bank_ods.mcp_ops.server import mcp

if not config.TRANSPORT_MCP_OPS_ENABLED:
    raise SystemExit("Ops MCP transport is disabled (TRANSPORT_MCP_OPS_ENABLED=false)")

Transport = Literal["stdio", "http", "sse", "streamable-http"]

# MCP_TRANSPORT=stdio  (default) — local engineer/IDE use
# MCP_TRANSPORT=sse    — internal ops tooling endpoint (never on the consumer path)
transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport not in get_args(Transport):
    raise SystemExit(f"Invalid MCP_TRANSPORT {transport!r}; expected one of {get_args(Transport)}")
mcp.run(transport=cast(Transport, transport))
