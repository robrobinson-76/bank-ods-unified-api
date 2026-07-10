"""Strawberry GraphQL app — side-by-side evaluation twin of bank_ods.graphql.

Run: uvicorn bank_ods.graphql_strawberry:app --port 8002
Endpoint: POST http://localhost:8002/graphql/

auto_camel_case=False keeps the snake_case query names (get_account, ...) of
the existing contract.
"""
from contextlib import asynccontextmanager

import strawberry
from fastapi import FastAPI, HTTPException
from strawberry.fastapi import GraphQLRouter
from strawberry.schema.config import StrawberryConfig

from bank_ods.config import LOG_LEVEL
from bank_ods.db.client import get_db
from bank_ods.db.indexes import ensure_indexes
from bank_ods.graphql_strawberry.resolvers import Query
from bank_ods.logging_config import RequestLoggingMiddleware, configure_logging

schema = strawberry.Schema(
    query=Query,
    config=StrawberryConfig(auto_camel_case=False),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(LOG_LEVEL)
    await ensure_indexes()
    yield


def create_app() -> FastAPI:
    fast = FastAPI(title="Bank ODS GraphQL API (Strawberry)", version="0.2.0", lifespan=lifespan)
    fast.add_middleware(RequestLoggingMiddleware)

    graphql_router = GraphQLRouter(schema, path="/")
    fast.include_router(graphql_router, prefix="/graphql")

    @fast.get("/health", tags=["ops"])
    async def health():
        return {"status": "ok"}

    @fast.get("/ready", tags=["ops"])
    async def ready():
        try:
            await get_db().command("ping")
            return {"status": "ready"}
        except Exception:
            raise HTTPException(status_code=503, detail="MongoDB unreachable")

    @fast.get("/")
    async def root():
        return {"message": "GraphQL (Strawberry) available at /graphql"}

    return fast


app = create_app()
