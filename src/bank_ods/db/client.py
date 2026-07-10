import asyncio
import weakref

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from bank_ods.config import MONGODB_URI, MONGODB_DB, MONGO_TIMEOUT_MS

# Motor binds each client to the event loop running when it is created, so a
# single process-wide client breaks in any process that runs more than one
# loop (scripts calling asyncio.run() twice, notebooks, test harnesses).
# Cache one client per loop instead; WeakKeyDictionary lets closed loops and
# their clients be garbage-collected.
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncIOMotorClient]" = weakref.WeakKeyDictionary()


def get_client() -> AsyncIOMotorClient:
    loop = asyncio.get_event_loop()
    client = _clients.get(loop)
    if client is None:
        client = AsyncIOMotorClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
            connectTimeoutMS=MONGO_TIMEOUT_MS,
            socketTimeoutMS=MONGO_TIMEOUT_MS,
        )
        _clients[loop] = client
    return client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[MONGODB_DB]


def get_collection(name: str) -> AsyncIOMotorCollection:
    return get_db()[name]
