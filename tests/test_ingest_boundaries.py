"""Architecture boundaries between the ODS and its ingestion component.

Two import rules carry real design weight, and neither is enforced by anything
except this module.

1. **bank_ods must never import ods_ingest.** The ODS read side stays
   independent of how data arrives — it must be buildable, testable, and
   deployable with no knowledge of Kafka.

2. **ods_ingest.adapters must never import bank_ods.** Adapters are the one
   piece that can be handed to a source system's own team (see
   docs/ARCHITECTURE-adapter-scale.md). An adapter that reaches into ODS domain
   models has quietly stopped being extractable — and, worse, has started making
   domain judgements that belong in curation.

Curation is deliberately exempt from rule 2: it writes the semantic tier, so
depending on ODS models is its whole job.

Core suite: pure static analysis, no infrastructure.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
BANK_ODS = SRC / "bank_ods"
ADAPTERS = SRC / "ods_ingest" / "adapters"


def imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by one file, from static analysis."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports have no module of interest here.
            if node.level == 0 and node.module:
                found.add(node.module)
    return found


def python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC)).replace("\\", "/")


@pytest.mark.parametrize("path", python_files(BANK_ODS), ids=_rel)
def test_ods_never_imports_the_ingestion_component(path):
    """Rule 1 — the read side knows nothing about how data arrives."""
    offenders = {m for m in imported_modules(path) if m.split(".")[0] == "ods_ingest"}
    assert not offenders, (
        f"{_rel(path)} imports {sorted(offenders)}. The ODS read side must not "
        f"depend on the ingestion component — the dependency runs one way only."
    )


@pytest.mark.parametrize("path", python_files(ADAPTERS), ids=_rel)
def test_adapters_never_import_ods_domain_models(path):
    """Rule 2 — adapters stay extractable, and stay mechanical.

    If an adapter needs an ODS model to do its job, the work it is doing is
    curation and belongs in ods_ingest/curation/ instead.
    """
    offenders = {m for m in imported_modules(path) if m.split(".")[0] == "bank_ods"}
    assert not offenders, (
        f"{_rel(path)} imports {sorted(offenders)}. Adapters must depend only on "
        f"the bus and their own parser, so an adapter can be handed to the source "
        f"system's team. Domain mapping belongs in ods_ingest/curation/."
    )


def test_the_boundary_check_is_actually_looking_at_something():
    """Guard against the rules passing because the globs matched nothing."""
    assert len(python_files(BANK_ODS)) > 20
    assert len(python_files(ADAPTERS)) > 4


def test_curation_is_expected_to_depend_on_ods_models():
    """The exemption is deliberate, and asserted so it stays visible.

    Curation writes the semantic tier; if it ever stopped importing ODS models
    that would mean the mapping had leaked somewhere it does not belong.
    """
    curation = SRC / "ods_ingest" / "curation"
    importers = [
        p for p in python_files(curation)
        if any(m.split(".")[0] == "bank_ods" for m in imported_modules(p))
    ]
    assert importers, (
        "No curation module imports bank_ods — has the semantic-tier mapping "
        "moved somewhere it should not be?"
    )
