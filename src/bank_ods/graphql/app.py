from contextlib import asynccontextmanager

from ariadne import ScalarType, make_executable_schema
from ariadne.asgi import GraphQL
from fastapi import FastAPI, HTTPException
from graphql.validation import NoSchemaIntrospectionCustomRule

from bank_ods.db.client import get_db

from bank_ods.config import (
    DEBUG,
    GRAPHQL_INTROSPECTION,
    GRAPHQL_MAX_DEPTH,
    GRAPHQL_MAX_ROOT_FIELDS,
    LOG_LEVEL,
    TRANSPORT_GRAPHQL_ENABLED,
)
from bank_ods.db.indexes import ensure_indexes
from bank_ods.graphql.protection import depth_limit_rule, root_fields_limit_rule
from bank_ods.graphql.sdl import generate_sdl
from bank_ods.graphql.resolvers import query
from bank_ods.logging_config import RequestLoggingMiddleware, configure_logging

datetime_scalar = ScalarType("DateTime")
decimal_scalar = ScalarType("Decimal")


@datetime_scalar.serializer
def serialize_datetime(value):
    return value if isinstance(value, str) else str(value)


@decimal_scalar.serializer
def serialize_decimal(value):
    # Service layer already serializes Decimal128/Decimal to exact strings
    return value if isinstance(value, str) else str(value)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not TRANSPORT_GRAPHQL_ENABLED:
        raise RuntimeError(
            "GraphQL transport is disabled (TRANSPORT_GRAPHQL_ENABLED=false)"
        )
    configure_logging(LOG_LEVEL)
    await ensure_indexes()
    yield


def _build_graphql_app() -> GraphQL:
    type_defs = generate_sdl()
    schema = make_executable_schema(type_defs, query, datetime_scalar, decimal_scalar)
    validation_rules = [
        depth_limit_rule(GRAPHQL_MAX_DEPTH),
        root_fields_limit_rule(GRAPHQL_MAX_ROOT_FIELDS),
    ]
    if not GRAPHQL_INTROSPECTION:
        validation_rules.append(NoSchemaIntrospectionCustomRule)
    return GraphQL(schema, debug=DEBUG, validation_rules=validation_rules)


def create_app() -> FastAPI:
    fast = FastAPI(title="Bank ODS GraphQL API", version="0.2.0", lifespan=lifespan)
    fast.add_middleware(RequestLoggingMiddleware)

    graphql_app = _build_graphql_app()
    fast.mount("/graphql", graphql_app)

    @fast.get("/health", tags=["ops"])
    async def health():
        """Liveness: the process is up."""
        return {"status": "ok"}

    @fast.get("/ready", tags=["ops"])
    async def ready():
        """Readiness: MongoDB is reachable. K8s readiness probes point here."""
        try:
            await get_db().command("ping")
            return {"status": "ready"}
        except Exception:
            raise HTTPException(status_code=503, detail="MongoDB unreachable")

    @fast.get("/")
    async def root():
        return {"message": "GraphQL available at /graphql"}

    return fast


app = create_app()
