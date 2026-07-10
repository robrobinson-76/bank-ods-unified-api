import os
from typing import Literal, cast, get_args

from bank_ods.mcp.server import mcp

Transport = Literal["stdio", "http", "sse", "streamable-http"]

# MCP_TRANSPORT=stdio  (default) — used with Claude Desktop and local dev
# MCP_TRANSPORT=sse    — used when deployed as a chatbot backend on K8s
transport = os.getenv("MCP_TRANSPORT", "stdio")
if transport not in get_args(Transport):
    raise SystemExit(f"Invalid MCP_TRANSPORT {transport!r}; expected one of {get_args(Transport)}")
mcp.run(transport=cast(Transport, transport))
