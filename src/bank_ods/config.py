import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB: str = os.getenv("MONGODB_DB", "bank_ods")

# Connection / query timeout in milliseconds (default: 10s for K8s pod restarts)
MONGO_TIMEOUT_MS: int = int(os.getenv("MONGO_TIMEOUT_MS", "10000"))

DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# GraphQL query protection (see graphql/protection.py)
GRAPHQL_MAX_DEPTH: int = int(os.getenv("GRAPHQL_MAX_DEPTH", "10"))
GRAPHQL_MAX_ROOT_FIELDS: int = int(os.getenv("GRAPHQL_MAX_ROOT_FIELDS", "10"))
GRAPHQL_INTROSPECTION: bool = os.getenv("GRAPHQL_INTROSPECTION", "true").lower() == "true"
