"""Avro wire contracts for the raw topics.

Two contract surfaces exist and must not drift:

  * the raw-tier Pydantic models — what LANDS in Mongo and is served by the ODS
  * the .avsc files here          — what TRAVELS on the bus

``derive_avro_schema()`` produces the Avro schema a raw model implies. The
.avsc files in this directory are the checked-in, code-reviewed contract, and
``tests/test_schema_contract.py`` asserts the two agree — the same governance
pattern as the GraphQL SDL snapshot. Regeneration command is in that test.

CDC subjects are exempt: Debezium authors and registers those schemas itself.
"""
from __future__ import annotations

import json
import types
import typing
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from bank_ods.models.base import BankDocument

SCHEMA_DIR = Path(__file__).parent
NAMESPACE = "ods.raw"

# Python annotation -> Avro primitive type
_PRIMITIVES: dict[Any, str] = {
    str: "string",
    int: "long",
    float: "double",
    bool: "boolean",
}


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    """Return (is_optional, inner_type) for Optional[T] / T | None."""
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(args) != len(non_none) and len(non_none) == 1:
            return True, non_none[0]
    return False, annotation


def _avro_type(annotation: Any, seen: set[str]) -> Any:
    """Map a Python annotation to an Avro type declaration."""
    if annotation in _PRIMITIVES:
        return _PRIMITIVES[annotation]

    origin = get_origin(annotation)
    if origin in (list, typing.List):
        (item,) = get_args(annotation)
        return {"type": "array", "items": _avro_type(item, seen)}

    if isinstance(annotation, type) and issubclass(annotation, BankDocument):
        return _record_schema(annotation, seen)

    raise TypeError(f"No Avro mapping for annotation {annotation!r}")


def _record_schema(model: type[BankDocument], seen: set[str]) -> Any:
    """Build the Avro record for a model, or a bare name reference if already defined.

    Avro requires a named type be defined once and referenced by name after
    that; emitting the full record twice is a schema error.
    """
    if model.__name__ in seen:
        return f"{NAMESPACE}.{model.__name__}"
    seen.add(model.__name__)

    record: dict[str, Any] = {
        "type": "record",
        "name": model.__name__,
        "namespace": NAMESPACE,
    }
    if model.ID_FIELD:
        record["doc"] = f"Raw-tier feed record. Natural key: {model.ID_FIELD}."

    fields = []
    for name, field in model.model_fields.items():
        optional, inner = _is_optional(field.annotation)
        avro = _avro_type(inner, seen)
        if optional:
            # Union order matters: "null" first makes null the natural default.
            fields.append({"name": name, "type": ["null", avro], "default": None})
        else:
            fields.append({"name": name, "type": avro})
    record["fields"] = fields
    return record


def derive_avro_schema(model: type[BankDocument]) -> dict:
    """The Avro schema implied by a raw-tier Pydantic model."""
    schema = _record_schema(model, set())
    assert isinstance(schema, dict)  # top level is always a full record
    return schema


def dumps(schema: dict) -> str:
    """Canonical on-disk form: 2-space indent, trailing newline, LF endings."""
    return json.dumps(schema, indent=2) + "\n"


def schema_path(name: str) -> Path:
    return SCHEMA_DIR / f"{name}.avsc"


def load_schema(name: str) -> dict:
    """Read a checked-in .avsc contract by bare name (no extension)."""
    with open(schema_path(name), encoding="utf-8") as f:
        return json.load(f)


def load_schema_str(name: str) -> str:
    """Read a contract as a string, for registering with the schema registry."""
    return json.dumps(load_schema(name))
