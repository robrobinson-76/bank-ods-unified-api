"""JSON-structured logging for K8s log aggregation."""
from __future__ import annotations

import json
import logging
import time

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


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
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
