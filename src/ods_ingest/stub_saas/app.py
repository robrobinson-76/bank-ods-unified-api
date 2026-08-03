"""The stub vendor SaaS API.

Endpoints:

    GET  /securities?updated_since=<iso>&page=<n>&page_size=<n>
    GET  /health
    POST /_admin/touch   {"vendor_ref": "VND-000007"}   bump a record's updated_at
    POST /_admin/reset                                   restore the pristine dataset

The _admin endpoints exist only so tests can create a change the adapter should
notice; a real vendor would have no such thing.

Fault injection (env): SAAS_429_EVERY=n returns 429 on every nth request,
SAAS_500_RATE=0.02 fails ~2% of requests. Both default to off so the stub is
quiet unless a test asks for trouble.
"""
from __future__ import annotations

import copy
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

DATA_FILE = Path(__file__).parent / "vendor_securities.json"

app = FastAPI(title="Vendor Security Master (stub)", version="1.0.0")


def _load() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


_PRISTINE: list[dict] = _load()
_ROWS: list[dict] = copy.deepcopy(_PRISTINE)
_request_count = 0


def _fault_check() -> None:
    """Behave like a real API under load: rate limits and transient failures."""
    global _request_count
    _request_count += 1

    every = int(os.getenv("SAAS_429_EVERY", "0"))
    if every and _request_count % every == 0:
        raise HTTPException(status_code=429, detail="rate limited",
                            headers={"Retry-After": "1"})

    rate = float(os.getenv("SAAS_500_RATE", "0"))
    if rate and random.random() < rate:
        raise HTTPException(status_code=500, detail="internal error")


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad updated_since: {value!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "records": len(_ROWS)}


@app.get("/securities")
def list_securities(
    updated_since: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict:
    """Records changed at or after `updated_since`, oldest change first.

    Ordering by updated_at is what makes watermarking possible at all; a vendor
    that returned changes unordered would force a full sweep every time.
    """
    _fault_check()
    since = _parse_since(updated_since)

    rows = sorted(_ROWS, key=lambda r: r["updated_at"])
    if since is not None:
        rows = [r for r in rows if datetime.fromisoformat(r["updated_at"]) >= since]

    start = (page - 1) * page_size
    window = rows[start:start + page_size]
    return {
        "data": window,
        "page": page,
        "page_size": page_size,
        "has_more": start + page_size < len(rows),
        # Deliberately no total count and no way to see deletions — see the
        # findings on what polling cannot tell you.
    }


class TouchRequest(BaseModel):
    vendor_ref: str
    issue_status: Optional[str] = None


@app.post("/_admin/touch")
def touch(request: TouchRequest) -> dict:
    """Mark a record as changed now — how a test creates an incremental update.

    Advances BOTH timestamps, because a genuine source-side update does:

      updated_at    the API's change marker, which the poller's watermark walks
      LAST_UPD_TS   the record's own last-modified time, which is what
                    downstream ordering compares (see PATTERN-snapshot-and-stream)

    Moving only the first would model a vendor that changes content without
    advancing the record's timestamp — in which case the ordering guard
    correctly refuses the change, and the update is silently lost. That is a
    real hazard worth knowing about, but it is not what this endpoint is for.
    """
    now = datetime.now(tz=timezone.utc)
    for row in _ROWS:
        if row["Vendor_Ref"] == request.vendor_ref:
            row["updated_at"] = now.isoformat()
            row["LAST_UPD_TS"] = now.strftime("%Y-%m-%d %H:%M:%S")
            if request.issue_status is not None:
                row["ISSUE_STATUS"] = request.issue_status
            return {"touched": request.vendor_ref, "updated_at": row["updated_at"],
                    "LAST_UPD_TS": row["LAST_UPD_TS"]}
    raise HTTPException(status_code=404, detail=f"unknown Vendor_Ref {request.vendor_ref}")


@app.post("/_admin/reset")
def reset() -> dict:
    global _ROWS, _request_count
    _ROWS = copy.deepcopy(_PRISTINE)
    _request_count = 0
    return {"reset": True, "records": len(_ROWS)}


@app.get("/_admin/stats")
def stats() -> dict:
    return {"records": len(_ROWS), "requests": _request_count}
