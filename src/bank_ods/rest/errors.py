from typing import Any

from fastapi import HTTPException


_STATUS_BY_CODE = {
    "NOT_FOUND": 404,
    "INVALID_DATE": 400,
    "INVALID_CURSOR": 400,
}


def check(result: dict[str, Any]) -> dict[str, Any]:
    """Raise HTTPException for service-layer error envelopes."""
    if "error" not in result:
        return result
    status = _STATUS_BY_CODE.get(result.get("code", ""), 500)
    raise HTTPException(status_code=status, detail=result["error"])
