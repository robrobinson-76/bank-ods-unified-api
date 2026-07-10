from bank_ods.db.client import get_db
from bank_ods.models.registry import ENTITIES

_indexes_created = False


async def ensure_indexes() -> None:
    """Create indexes for every registered collection.

    Indexes track the physical collections that exist, not what a deployment
    exposes: the loader writes every collection regardless of tier flags, and
    the ops server queries raw collections even when the raw tier is withheld
    from consumer transports. Gating index creation on exposure would leave
    written-and-queried collections unindexed (and drop the raw tier's unique
    keys), so this iterates the full registry, not active_entities().
    """
    global _indexes_created
    if _indexes_created:
        return
    db = get_db()
    for entity in ENTITIES:
        col = db[entity.COLLECTION]
        for keys, options in entity.INDEXES:
            await col.create_index(keys, **options)
    _indexes_created = True
