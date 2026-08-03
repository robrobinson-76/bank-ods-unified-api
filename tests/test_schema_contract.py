"""Wire contract governance — the .avsc files must agree with the raw models.

Two contract surfaces exist: the raw-tier Pydantic models (what lands in Mongo
and is served by the ODS) and the .avsc files (what travels on the bus). This
module is what stops them drifting, the same way test_protection.py guards the
GraphQL SDL snapshot.

Core suite: no Kafka, no registry, no Mongo — pure file/model comparison.
"""
import json

import fastavro
import pytest

from ods_ingest import topics
from ods_ingest.schemas import derive_avro_schema, dumps, load_schema, schema_path

MODEL_BACKED = [t for t in topics.authored_topics() if t.model is not None]
ALL_AUTHORED = topics.authored_topics()


@pytest.mark.parametrize("spec", ALL_AUTHORED, ids=lambda s: s.schema_name)
def test_schema_file_exists_and_parses(spec):
    """Every authored contract is present and is valid Avro."""
    assert schema_path(spec.schema_name).exists(), (
        f"Missing contract {spec.schema_name}.avsc for topic {spec.name}"
    )
    fastavro.parse_schema(load_schema(spec.contract))


@pytest.mark.parametrize("spec", MODEL_BACKED, ids=lambda s: s.schema_name)
def test_schema_matches_model(spec):
    """The checked-in contract must match the schema its raw model implies.

    A diff here means the wire contract and the landing contract disagree —
    usually because a raw model gained or lost a field. If the model change is
    intentional, regenerate and commit the diff alongside it:

        python scripts/regen_avro_schemas.py
    """
    on_disk = schema_path(spec.schema_name).read_text(encoding="utf-8")
    derived = dumps(derive_avro_schema(spec.model))
    assert on_disk.replace("\r\n", "\n") == derived, (
        f"{spec.schema_name}.avsc is out of date with {spec.model.__name__}; "
        f"run: python scripts/regen_avro_schemas.py"
    )


@pytest.mark.parametrize("spec", MODEL_BACKED, ids=lambda s: s.schema_name)
def test_id_field_present_in_schema(spec):
    """The natural key must travel on the wire — the sink upserts on it.

    Without it, at-least-once delivery could not be made idempotent.
    """
    schema = load_schema(spec.contract)
    names = {f["name"] for f in schema["fields"]}
    assert spec.model.ID_FIELD in names
    # And it must be non-nullable: a null key would break the upsert.
    id_field = next(f for f in schema["fields"] if f["name"] == spec.model.ID_FIELD)
    assert id_field["type"] == "string", (
        f"{spec.model.ID_FIELD} must be a required string on {spec.schema_name}"
    )


@pytest.mark.parametrize("spec", MODEL_BACKED, ids=lambda s: s.schema_name)
def test_round_trip_through_avro(spec):
    """A model instance survives encode→decode with every field intact.

    Guards the mapping itself, not just the shape: a wrong Avro type would
    serialize but come back changed.
    """
    schema = fastavro.parse_schema(load_schema(spec.contract))
    sample = {
        name: ("X" if field.is_required() else None)
        for name, field in spec.model.model_fields.items()
    }
    import io

    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, schema, sample)
    buf.seek(0)
    assert fastavro.schemaless_reader(buf, schema) == sample


def test_key_fields_are_real_payload_fields():
    """Kafka message keys must name a field the payload actually carries."""
    for spec in topics.TOPICS:
        if not spec.key_field or not spec.schema_name:
            continue
        names = {f["name"] for f in load_schema(spec.contract)["fields"]}
        assert spec.key_field in names, (
            f"{spec.name} keys on {spec.key_field}, absent from {spec.schema_name}"
        )


def test_cdc_topics_have_no_authored_schema():
    """Debezium authors and registers its own subjects — we must not shadow them.

    Checking in a contract for a CDC topic would create two sources of truth
    for one subject, which is exactly the drift this module exists to prevent.
    """
    for spec in topics.TOPICS:
        if spec.extractor == topics.EXTRACTOR_DEBEZIUM:
            assert spec.schema_name is None
            assert not schema_path(spec.name.split(".")[-1]).exists()


def test_every_raw_model_is_reachable_from_a_topic():
    """A raw-tier entity with no feed is a landing contract nothing can fill.

    Catches the half-finished state where a model was registered but its topic
    was never declared.
    """
    from bank_ods.models.registry import ENTITIES_RAW

    fed = {t.model for t in topics.TOPICS if t.model is not None}
    for model in ENTITIES_RAW:
        assert model in fed, (
            f"{model.__name__} is registered but no topic feeds it — "
            f"add a TopicSpec in ods_ingest/topics.py"
        )


def test_manifest_schema_is_hand_authored():
    """The batch manifest is control data with no raw model behind it."""
    spec = topics.get("ods.raw.custody.batches")
    assert spec.model is None
    schema = load_schema(spec.contract)
    names = {f["name"] for f in schema["fields"]}
    # The completeness contract consumers rely on.
    assert {"batchId", "cycleDate", "recordCount", "status"} <= names
    assert json.loads(json.dumps(schema))  # plain JSON, no surprises
