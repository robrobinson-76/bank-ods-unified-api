"""Graphene GraphQL app — side-by-side evaluation twin of bank_ods.graphql.

Run: uvicorn bank_ods.graphql_graphene:app --port 8003
Endpoint: POST http://localhost:8003/graphql/

Unlike Ariadne (ariadne.asgi.GraphQL) and Strawberry (GraphQLRouter), graphene
ships no ASGI integration or GraphiQL — the POST endpoint below is hand-rolled
around schema.execute_async(). Third-party bridges exist (starlette-graphene3)
but are as dormant as graphene-pydantic, so this twin avoids adding another
stale dependency.
"""
from contextlib import asynccontextmanager

import graphene
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bank_ods.config import LOG_LEVEL
from bank_ods.db.indexes import ensure_indexes
from bank_ods.graphql_graphene.resolvers import Query
from bank_ods.graphql_graphene.types import SecurityType, SecurityList
from bank_ods.logging_config import RequestLoggingMiddleware, configure_logging

# Security/SecurityList are unreferenced by Query fields; force-include them to
# match the Ariadne schema, which emits every ENTITIES model.
schema = graphene.Schema(query=Query, auto_camelcase=False, types=[SecurityType, SecurityList])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(LOG_LEVEL)
    await ensure_indexes()
    yield


def create_app() -> FastAPI:
    fast = FastAPI(title="Bank ODS GraphQL API (Graphene)", version="0.2.0", lifespan=lifespan)
    fast.add_middleware(RequestLoggingMiddleware)

    async def graphql_endpoint(request: Request) -> JSONResponse:
        payload = await request.json()
        result = await schema.execute_async(
            payload.get("query"),
            variable_values=payload.get("variables"),
            operation_name=payload.get("operationName"),
        )
        body: dict = {"data": result.data}
        if result.errors:
            body["errors"] = [e.formatted for e in result.errors]
        return JSONResponse(body)

    fast.add_api_route("/graphql", graphql_endpoint, methods=["POST"], include_in_schema=False)
    fast.add_api_route("/graphql/", graphql_endpoint, methods=["POST"], include_in_schema=False)

    @fast.get("/health", tags=["ops"])
    async def health():
        return {"status": "ok"}

    @fast.get("/")
    async def root():
        return {"message": "GraphQL (Graphene) available at /graphql"}

    return fast


app = create_app()
