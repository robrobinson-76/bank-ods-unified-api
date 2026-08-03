"""Regenerate the checked-in .avsc wire contracts from the raw-tier models.

The .avsc files are the code-reviewed contract; this script is how an
intentional model change is propagated to them. tests/test_schema_contract.py
fails when the two drift, so the regenerated diff must land in the same commit
as the model change — the same discipline as the GraphQL SDL snapshot.

    python scripts/regen_avro_schemas.py

The batch-manifest schema has no model behind it (it is pure control data) and
is hand-authored; this script leaves it alone.
"""
import sys

from ods_ingest import topics
from ods_ingest.schemas import derive_avro_schema, dumps, schema_path


def main() -> int:
    written = []
    for spec in topics.authored_topics():
        if spec.model is None:
            continue  # hand-authored control schema (manifest)
        path = schema_path(spec.contract)
        text = dumps(derive_avro_schema(spec.model))
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing != text:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            written.append(f"updated {path.name}")
        else:
            written.append(f"unchanged {path.name}")
    print("\n".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
