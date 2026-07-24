"""Typed, frozen Task 15 evaluation-case contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from eval.versioning import EVALUATION_CASE_MIGRATIONS
from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, get_inventory
from ir.schema import EntityType, StrictModel
from ir.schema_version import (
    EVALUATION_CASE_SCHEMA_VERSION,
    SCHEMA_VERSION_FIELD,
)
from ir.versioning import PayloadStructureError, decode_json_object


FixtureName = Literal["bracket.step", "plate_hole.step"]
ConditionType = Literal[
    "fixed_displacement",
    "prescribed_displacement",
    "resultant_surface_force",
    "surface_traction",
    "pressure",
    "gravity",
    "concentrated_force",
]


class ClickInput(StrictModel):
    intent_index: int = Field(ge=0)
    entity_ids: list[int] = Field(min_length=1)


class ClarificationAction(StrictModel):
    intent_index: int = Field(ge=0)
    entity_ids: list[int] = Field(min_length=1)


class ExpectedCondition(StrictModel):
    intent_index: int = Field(ge=0)
    entity_ids: list[int]
    region_entity_type: EntityType | None
    condition_type: ConditionType
    components: list[Literal["x", "y", "z"]] | dict[Literal["x", "y", "z"], float] | None = None
    vector: list[float] | None = Field(default=None, min_length=3, max_length=3)
    magnitude: float | None = None
    internal_unit: Literal["mm", "N", "MPa", "mm/s^2", "none"]
    expected_ir_subset: dict[str, Any]

    @model_validator(mode="after")
    def _shape_matches_condition(self) -> "ExpectedCondition":
        if self.condition_type in {"fixed_displacement", "prescribed_displacement"}:
            if self.components is None or self.vector is not None or self.magnitude is not None:
                raise ValueError("displacement expectations require only components")
        elif self.condition_type == "pressure":
            if self.magnitude is None or self.vector is not None or self.components is not None:
                raise ValueError("pressure expectations require only magnitude")
        else:
            if self.vector is None or self.components is not None or self.magnitude is not None:
                raise ValueError("vector-load expectations require only vector")
        if self.condition_type == "gravity":
            if self.region_entity_type is not None or self.entity_ids:
                raise ValueError("whole-model gravity must not claim a selected region")
        elif self.region_entity_type is None or not self.entity_ids:
            raise ValueError("non-gravity conditions require a non-empty typed region")
        return self


class EvaluationCase(StrictModel):
    # Task 19: the evaluation-case *record* version.  The IR fragments this
    # record carries (``expected_ir_subset``, ``expected_structured_ir_subset``)
    # are deliberately partial and are never versioned or migrated as a
    # SimulationIntent.
    schema_version: int = Field(default=EVALUATION_CASE_SCHEMA_VERSION, ge=1)
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    model_fixture: FixtureName
    instruction: str = Field(min_length=1)
    click_evidence: list[ClickInput] = Field(default_factory=list)
    clarification_response: ClarificationAction | None = None
    expected_conditions: list[ExpectedCondition] = Field(min_length=1)
    clarification_required: bool
    expected_structured_ir_subset: dict[str, Any]
    artifact_export_eligible: bool
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent_interactions(self) -> "EvaluationCase":
        indexes = [item.intent_index for item in self.expected_conditions]
        if indexes != list(range(len(indexes))):
            raise ValueError("expected condition intent indexes must be contiguous and ordered")
        click_indexes = [item.intent_index for item in self.click_evidence]
        if len(click_indexes) != len(set(click_indexes)):
            raise ValueError("click evidence intent indexes must be unique")
        if self.clarification_required != (self.clarification_response is not None):
            raise ValueError("clarification-required cases need exactly one predefined action")
        if self.clarification_response is not None and self.clarification_response.intent_index not in indexes:
            raise ValueError("clarification action references an unknown intent")
        return self


def canonical_case_bytes(case: EvaluationCase) -> bytes:
    """Historical canonical bytes, excluding the record version declaration.

    Task 19 decision D-3: the frozen 15-case manifest hash
    ``47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`` is
    immutable baseline evidence recorded by Tasks 16 and 18.  Stamping a record
    version must not silently rebaseline it, so the version declaration is
    excluded here and covered by :func:`versioned_manifest_hash` instead.
    """

    payload = case.model_dump(mode="json")
    payload.pop(SCHEMA_VERSION_FIELD, None)
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def versioned_canonical_case_bytes(case: EvaluationCase) -> bytes:
    """Canonical bytes including the record version declaration."""

    return (
        json.dumps(case.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def load_evaluation_case(
    raw: str | bytes | dict[str, Any], *, source: str | None = None
) -> EvaluationCase:
    """Authoritative external ingestion path for one evaluation case."""

    family = EVALUATION_CASE_MIGRATIONS.family
    payload = decode_json_object(raw, family=family, source=source)
    migrated = EVALUATION_CASE_MIGRATIONS.migrate(payload, source=source)
    try:
        case = EvaluationCase.model_validate(migrated, strict=True)
    except Exception as exc:
        raise PayloadStructureError(
            "Payload does not satisfy the EvaluationCase contract.",
            family=family,
            source=source,
        ) from exc
    return case


def load_cases(
    case_dir: str | Path,
    *,
    fixture_dir: str | Path = Path("tests") / "fixtures",
) -> list[EvaluationCase]:
    """Load and fully validate exactly 15 cases in stable filename order."""

    case_path = Path(case_dir)
    paths = sorted(case_path.glob("*.json"), key=lambda path: path.name)
    if len(paths) != 15:
        raise ValueError(f"expected exactly 15 evaluation cases, found {len(paths)}")
    cases = [
        load_evaluation_case(
            path.read_text(encoding="utf-8"), source=f"eval/cases/{path.name}"
        )
        for path in paths
    ]
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("evaluation case IDs must be unique")

    inventories: dict[str, FaceInventory] = {}
    fixture_root = Path(fixture_dir)
    for case in cases:
        fixture = fixture_root / case.model_fixture
        if not fixture.is_file():
            raise ValueError(f"unknown or missing model fixture: {case.model_fixture}")
        inventory = inventories.get(case.model_fixture)
        if inventory is None:
            inventory, _ = get_inventory(fixture)
            inventories[case.model_fixture] = inventory
            # Force the established Task 3 path during case validation as well.
            analyze_cylinders(fixture)
        known_ids = {face.tag for face in inventory.faces}
        supplied_ids = [
            entity_id
            for condition in case.expected_conditions
            for entity_id in condition.entity_ids
        ]
        supplied_ids.extend(
            entity_id for click in case.click_evidence for entity_id in click.entity_ids
        )
        if case.clarification_response is not None:
            supplied_ids.extend(case.clarification_response.entity_ids)
        missing = sorted(set(supplied_ids) - known_ids)
        if missing:
            raise ValueError(f"case {case.case_id} references nonexistent entity IDs: {missing}")
    return cases


def manifest_hash(cases: list[EvaluationCase]) -> str:
    """Frozen corpus manifest hash; version declarations are excluded (D-3)."""

    digest = hashlib.sha256()
    for case in cases:
        digest.update(case.case_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(canonical_case_bytes(case))
    return digest.hexdigest()


def versioned_manifest_hash(cases: list[EvaluationCase]) -> str:
    """Version-aware corpus hash, computed alongside the frozen hash (D-3)."""

    digest = hashlib.sha256()
    for case in cases:
        digest.update(case.case_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(versioned_canonical_case_bytes(case))
    return digest.hexdigest()
