"""Task 1 DoD tests for the IR schema."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ir.schema import (
    ExportBlockedError,
    Region,
    SimulationIntent,
    export_json_schema,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.json"))


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_examples_exist():
    assert len(EXAMPLE_FILES) == 3


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.stem)
def test_round_trip(path):
    intent = SimulationIntent.model_validate_json(path.read_text(encoding="utf-8"))
    dumped = intent.model_dump_json()
    reloaded = SimulationIntent.model_validate_json(dumped)
    assert reloaded == intent
    assert reloaded.model_dump() == intent.model_dump()


def test_sprint_goal_example_validates():
    intent = SimulationIntent.model_validate(
        load_example("bracket_sprint_goal.json")
    )
    assert intent.analysis.type == "static_structural"
    assert intent.analysis.units.length == "mm"
    assert intent.analysis.units.force == "N"
    assert intent.materials[0].E_MPa == 210000
    assert intent.materials[0].nu == 0.3

    by_id = {r.id: r for r in intent.regions}
    assert by_id["bolt_holes"].entity_ids == [18, 19, 24, 25]
    assert by_id["bolt_holes"].selection_method == "semantic_geometry_query"
    assert by_id["bolt_holes"].confidence == 0.94
    assert by_id["upper_mounting_face"].entity_ids == [7]
    assert by_id["upper_mounting_face"].confidence == 0.88

    assert intent.bcs[0].type == "fixed_displacement"
    assert intent.bcs[0].components == ["x", "y", "z"]
    assert intent.loads[0].type == "resultant_surface_force"
    assert intent.loads[0].vector == [0, -5000, 0]
    assert len(intent.assumptions) == 2

    # Rule 2: full provenance present on every region.
    for region in intent.regions:
        assert region.source_instruction
        assert region.status in ("proposed", "confirmed", "rejected")


def region_dict(**overrides) -> dict:
    base = {
        "id": "r1",
        "entity_type": "cad_face",
        "entity_ids": [1, 2],
        "selection_method": "semantic_geometry_query",
        "confidence": 0.9,
        "source_instruction": "fix the holes",
        "status": "proposed",
    }
    base.update(overrides)
    return base


def test_region_without_confidence_rejected():
    data = region_dict()
    del data["confidence"]
    with pytest.raises(ValidationError):
        Region.model_validate(data)


@pytest.mark.parametrize("field", ["source_instruction", "status", "selection_method"])
def test_region_missing_provenance_rejected(field):
    data = region_dict()
    del data[field]
    with pytest.raises(ValidationError):
        Region.model_validate(data)


def test_region_empty_entity_ids_rejected():
    with pytest.raises(ValidationError):
        Region.model_validate(region_dict(entity_ids=[]))


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Region.model_validate(region_dict(confidence=1.5))


def test_unknown_region_ref_rejected():
    data = load_example("bracket_sprint_goal.json")
    data["loads"][0]["region_ref"] = "does_not_exist"
    with pytest.raises(ValidationError):
        SimulationIntent.model_validate(data)


def test_export_blocked_for_unconfirmed_regions():
    intent = SimulationIntent.model_validate(
        load_example("bracket_sprint_goal.json")
    )
    assert any(r.status != "confirmed" for r in intent.regions)
    with pytest.raises(ExportBlockedError):
        intent.export_payload()


def test_export_allowed_when_all_regions_confirmed():
    intent = SimulationIntent.model_validate(
        load_example("bracket_confirmed_export_ready.json")
    )
    payload = intent.export_payload()
    assert payload["regions"][0]["status"] == "confirmed"
    assert payload["analysis"]["units"] == {
        "length": "mm",
        "force": "N",
        "stress": "MPa",
    }


def test_json_schema_export():
    schema = export_json_schema()
    assert schema["title"] == "SimulationIntent"
    for prop in (
        "analysis",
        "materials",
        "regions",
        "bcs",
        "loads",
        "assumptions",
        "validation_status",
    ):
        assert prop in schema["properties"]
    # Region provenance fields are required in the published schema too.
    region_schema = schema["$defs"]["Region"]
    for field in (
        "confidence",
        "source_instruction",
        "status",
        "selection_method",
        "entity_ids",
    ):
        assert field in region_schema["required"]
