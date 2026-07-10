"""JSON-structured logging for K8s log aggregation, plus an in-process ring
buffer that the operations MCP server exposes for interactive debugging."""
from __future__ import annotations

import json
import logging
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Structured fields logged via extra={"payload": {...}} are merged at
        # the top level so log aggregators can index them directly.
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            entry.update(payload)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class RingBufferHandler(logging.Handler):
    """Keeps the last N log entries in memory for the ops log tool.

    Process-local by design: each process (REST, GraphQL, either MCP server)
    sees its own log tail. Cross-process log search belongs to the platform's
    log aggregator; this buffer answers "what just happened in THIS process"
    during interactive debugging.
    """

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.buffer: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        entry: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            entry.update(payload)
        self.buffer.append(entry)


# Shared instance — attached by configure_logging, read by the ops MCP server.
log_ring = RingBufferHandler()

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def recent_logs(level: str = "INFO", limit: int = 50) -> list[dict]:
    """Newest-first slice of the in-process log ring at or above ``level``."""
    threshold = _LEVELS.get(level.upper(), 20)
    matched = [e for e in reversed(log_ring.buffer) if _LEVELS.get(e["level"], 0) >= threshold]
    return matched[: max(1, limit)]


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler, log_ring]
    root.setLevel(getattr(logging, level, logging.INFO))


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    _log = logging.getLogger("bank_ods.http")

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        self._log.info(
            "http_request",
            extra={"payload": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            }},
        )
        return response
