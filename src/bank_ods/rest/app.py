from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from bank_ods.config import LOG_LEVEL
from bank_ods.db.indexes import ensure_indexes
from bank_ods.logging_config import RequestLoggingMiddleware, configure_logging
from bank_ods.db.client import get_db
from bank_ods.rest.routers import accounts, balances, positions, securities, settlements, transactions


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(LOG_LEVEL)
    await ensure_indexes()
    yield


app = FastAPI(title="Bank ODS REST API", version="0.2.0", lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)

app.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
app.include_router(securities.router, prefix="/securities", tags=["securities"])
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(positions.router, prefix="/positions", tags=["positions"])
app.include_router(settlements.router, prefix="/settlements", tags=["settlements"])
app.include_router(balances.router, prefix="/balances", tags=["balances"])


@app.get("/health", tags=["ops"])
async def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready():
    """Readiness: MongoDB is reachable. K8s readiness probes point here."""
    try:
        await get_db().command("ping")
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unreachable")
