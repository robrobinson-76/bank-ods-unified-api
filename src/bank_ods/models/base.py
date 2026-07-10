from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar

from bson import ObjectId
from bson.decimal128 import Decimal128
from pydantic import BaseModel, ConfigDict, field_validator


# IndexSpec: (keys, options) where keys is a field name string or list of (field, direction) tuples
IndexSpec = tuple[str | list[tuple[str, int]], dict[str, Any]]


class BankDocument(BaseModel):
    """Base for all six bank ODS entity models.

    Field names use camelCase to match MongoDB documents directly.
    COLLECTION and INDEXES must be set on each subclass.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        # Allow arbitrary types (ObjectId, datetime) in from_mongo before coercion
        arbitrary_types_allowed=True,
    )

    COLLECTION: ClassVar[str]
    INDEXES: ClassVar[list[IndexSpec]]

    @field_validator("*", mode="before")
    @classmethod
    def _decimal128_to_decimal(cls, v: Any) -> Any:
        """Accept raw BSON Decimal128 values when validating Mongo documents."""
        if isinstance(v, Decimal128):
            return v.to_decimal()
        return v

    @classmethod
    def from_mongo(cls, doc: dict) -> "BankDocument":
        """Construct a model instance from a raw MongoDB document."""
        clean = {k: v for k, v in doc.items() if k != "_id"}
        return cls.model_validate(clean)

    def to_response(self) -> dict:
        """Return a JSON-safe dict with dates as ISO strings and ObjectIds as strings."""
        return _serialize(self.model_dump())


def _serialize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, (Decimal, Decimal128)):
        return str(obj)
    return obj


def serialize_doc(doc: dict) -> dict:
    """Convert a raw MongoDB document dict to a JSON-safe dict."""
    return _serialize({k: v for k, v in doc.items() if k != "_id"})
