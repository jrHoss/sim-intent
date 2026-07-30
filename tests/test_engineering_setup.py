"""R3.1 engineering-setup schema evidence.

Covers the five things the independent review asked for in one place:

1. original-versus-normalized consistency for every engineering quantity that
   stores both forms, through the single ``ground.semantics`` unit path;
2. the controlled schema-version-1 -> 2 migration, which must leave a legacy
   payload structurally incomplete rather than granting it new approvals;
3. the finite translational prescribed-displacement envelope;
4. the authoritative load/constraint-to-region compatibility table and the
   deterministic readiness precedence;
5. that natural-language interpretation approves no engineering configuration:
   confirming regions and accepting assumptions never synthesizes the missing
   analysis, meshing or solver decisions, and only a deliberate durable
   engineering-setup revision can make a setup ready.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import LocalDataConfig
from app.orchestration import propose_from_interpretation
from app.runtime_mode import RuntimeMode
from app.server import create_app
from geom.cylinders import analyze_cylinders
from geom.inventory import get_inventory
from ground import semantics
from llm.interpreter import Interpretation
from ground.semantics import (
    NORMALIZATION_RELATIVE_TOLERANCE,
    SUPPORTED_UNITS_BY_KIND,
    normalize_density,
    normalize_quantity,
    normalize_youngs_modulus,
    normalized_matches,
    supported_units,
)
from ir.schema import (
    BC_REGION_COMPATIBILITY,
    LOAD_REGION_COMPATIBILITY,
    UNSUPPORTED_UNIT_CODE,
    AccelerationQuantity,
    AccelerationUnit,
    DensityQuantity,
    DensityUnit,
    EntityType,
    ForceQuantity,
    ForceUnit,
    LengthQuantity,
    LengthUnit,
    PressureLoad,
    PrescribedDisplacementBC,
    SimulationIntent,
    StressQuantity,
    StressUnit,
)
from ir.schema_version import (
    SCHEMA_VERSION_FIELD,
    SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION,
    SIMULATION_INTENT_SCHEMA_VERSION,
)
from ir.validate import validate_intent
from ir.versioning import (
    SIMULATION_INTENT_MIGRATIONS,
    UnsupportedFutureVersionError,
    dump_simulation_intent,
    load_simulation_intent,
)
from tests.test_project_persistence import (
    create_project,
    minimal_inp,
    request,
    upload,
)

ROOT = Path(__file__).resolve().parents[1]
MINIMAL_INP = minimal_inp()

EXPLICIT_ANALYSIS_DECISIONS = {
    "dimensionality": "3d_solid",
    "solver_target": "calculix",
    "coordinate_system": "global_cartesian",
}
EXPLICIT_MESH_SETTINGS = {
    "global_element_size_mm": 2.5,
    "element_type": "tetrahedral",
    "element_order": "first_order",
    "mesher": "gmsh",
    "mesher_preset": "gmsh_tet_v1",
    "target_size_original": {"value": 2.5, "unit": "mm"},
}
EXPLICIT_SOLVER_SETTINGS = {
    "target": "calculix",
    "analysis_profile": "linear_static_v1",
    "requested_results": ["displacement", "stress", "reaction_force"],
}


def region(region_id: str, entity_type: str, entity_ids: list | None = None) -> dict:
    """Build one region of *entity_type*.

    A ``cad_face`` region gets a synthetic ``resolved`` target whose
    ``model_version_id``/``artifact_sha256`` are placeholders.  That is valid
    only for in-memory schema checks: R4b.2 binds a CAD region to the exact
    uploaded ModelVersion and its persisted geometry-identity artifact, so a
    fixture that submits a CAD region through the durable API must overwrite
    ``cad_face_target`` with the real uploaded identity (see
    ``test_confirming_and_accepting_everything_never_supplies_configuration``).
    A fixture that uploads :func:`minimal_inp` has mesh entities rather than
    CAD faces and must use a mesh region kind -- see :func:`inp_payload`.
    """

    ids = entity_ids or [1]
    value = {
        "id": region_id,
        "entity_type": entity_type,
        "selection_method": "user_confirmed",
        "confidence": 1.0,
        "source_instruction": f"Use {region_id}.",
        "status": "confirmed",
    }
    if entity_type == "cad_face":
        value["cad_face_target"] = {
            "model_version_id": "unit-test-version",
            "artifact_sha256": "a" * 64,
            "resolution": "resolved",
            "stable_identities": [
                f"gfi1:{entity_id:064x}" for entity_id in ids
            ],
            "source_face_tags": ids,
        }
    else:
        value["entity_ids"] = ids
    return value


def payload() -> dict:
    """An explicitly complete, contradiction-free current-version setup."""

    return {
        SCHEMA_VERSION_FIELD: SIMULATION_INTENT_SCHEMA_VERSION,
        "analysis": {
            "type": "static_structural",
            "units": {"length": "mm", "force": "N", "stress": "MPa"},
            **EXPLICIT_ANALYSIS_DECISIONS,
        },
        "materials": [{
            "name": "Steel",
            "model": "linear_elastic_isotropic",
            "E_MPa": 210_000.0,
            "nu": 0.3,
            "density_tonne_per_mm3": 7.85e-9,
            "youngs_modulus_original": {"value": 210.0, "unit": "GPa"},
            "density_original": {"value": 7850.0, "unit": "kg/m^3"},
        }],
        "regions": [region("fixed", "cad_face", [1]), region("loaded", "cad_face", [2])],
        "bcs": [{
            "type": "fixed_displacement",
            "region_ref": "fixed",
            "components": ["x", "y", "z"],
        }],
        "loads": [{
            "type": "resultant_surface_force",
            "region_ref": "loaded",
            "vector": [0.0, -5000.0, 0.0],
            "original_force": {"value": 5.0, "unit": "kN"},
            "magnitude_N": 5000.0,
            "direction": [0.0, -1.0, 0.0],
            "distribution": "uniform",
        }],
        "mesh_settings": dict(EXPLICIT_MESH_SETTINGS),
        "solver_settings": dict(EXPLICIT_SOLVER_SETTINGS),
        "assumptions": [],
        "validation_status": "unvalidated",
    }


def inp_payload() -> dict:
    """:func:`payload` retargeted at the mesh regions of an uploaded INP model.

    R4b.2 scopes ``cad_face`` regions to STEP/CAD ModelVersions: an INP
    ModelVersion carries mesh entities, not CAD faces, so the durable API
    rejects a CAD region against one with ``cad_region_not_applicable``.  This
    keeps the region ids, constraint target and load target of :func:`payload`
    unchanged -- ``mesh_face`` is in both ``SURFACE_REGION_ENTITY_TYPES`` and
    ``CONSTRAINT_REGION_ENTITY_TYPES`` -- so only the entity vocabulary moves.
    """

    body = payload()
    body["regions"] = [
        region("fixed", "mesh_face", [1]),
        region("loaded", "mesh_face", [2]),
    ]
    return body


def codes(intent: SimulationIntent) -> set[str]:
    return {issue.code for issue in validate_intent(intent).issues}


def rejection_code(body: dict) -> str:
    """Validate *body* and return the single stable code it was rejected with."""

    with pytest.raises(ValidationError) as caught:
        SimulationIntent.model_validate(body)
    rendered = str(caught.value)
    tokens = [
        line.split("Value error, ", 1)[1].split(":", 1)[0].strip()
        for line in rendered.splitlines()
        if "Value error, " in line
    ]
    assert tokens, f"no stable engineering code in:\n{rendered}"
    return tokens[0]


# --------------------------------------------------------------------------
# 0. One central supported-unit and normalization path
# --------------------------------------------------------------------------


def test_schema_unit_vocabularies_mirror_the_central_unit_table():
    """A unit can never be added to only one of the two places."""

    from typing import get_args

    assert get_args(ForceUnit) == SUPPORTED_UNITS_BY_KIND["force"]
    assert get_args(StressUnit) == SUPPORTED_UNITS_BY_KIND["stress"]
    assert get_args(LengthUnit) == SUPPORTED_UNITS_BY_KIND["length"]
    assert get_args(DensityUnit) == SUPPORTED_UNITS_BY_KIND["density"]
    assert get_args(AccelerationUnit) == SUPPORTED_UNITS_BY_KIND["acceleration"]


@pytest.mark.parametrize(
    ("kind", "unit", "value", "expected"),
    [
        ("force", "N", 1.0, 1.0),
        ("force", "kN", 1.0, 1_000.0),
        ("force", "MN", 1.0, 1_000_000.0),
        ("stress", "Pa", 1.0, 1e-6),
        ("stress", "kPa", 1.0, 1e-3),
        ("stress", "MPa", 1.0, 1.0),
        ("stress", "GPa", 1.0, 1_000.0),
        ("length", "mm", 1.0, 1.0),
        ("length", "m", 1.0, 1_000.0),
        ("density", "kg/m^3", 7850.0, 7.85e-9),
        ("density", "kg/m3", 7850.0, 7.85e-9),
        ("density", "t/mm^3", 7.85e-9, 7.85e-9),
        ("density", "tonne/mm^3", 7.85e-9, 7.85e-9),
        ("acceleration", "mm/s^2", 9810.0, 9810.0),
        ("acceleration", "m/s^2", 9.81, 9810.0),
    ],
)
def test_every_supported_unit_conversion_boundary(kind, unit, value, expected):
    converted = normalize_quantity(value, unit, kind=kind)
    assert converted.kind == kind
    assert converted.value == pytest.approx(expected, rel=1e-12)
    assert unit in supported_units(kind)


def test_supported_unit_vocabulary_is_closed():
    every = set(supported_units())
    assert every == {
        unit for group in SUPPORTED_UNITS_BY_KIND.values() for unit in group
    }
    for unsupported in ("", "kg", "psi", "lbf", "inch", "N/mm", "furlong"):
        with pytest.raises(ValueError):
            normalize_quantity(1.0, unsupported)


def test_normalization_tolerance_is_relative_and_documented():
    assert NORMALIZATION_RELATIVE_TOLERANCE == 1e-9
    assert normalized_matches(1000.0, 1000.0 * (1 + 5e-10))
    assert not normalized_matches(1000.0, 1000.0 * (1 + 5e-8))
    # Without a scale an exact zero must stay an exact zero.
    assert normalized_matches(0.0, -0.0)
    assert not normalized_matches(0.0, 1e-30)
    # A vector component is compared against the vector's own magnitude.
    assert normalized_matches(0.0, 1e-30, scale=1000.0)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_nonfinite_quantities_are_rejected_before_canonical_serialization(value):
    with pytest.raises(ValueError):
        normalize_quantity(value, "kN")
    body = payload()
    body["loads"][0]["vector"] = [0.0, value, 0.0]
    with pytest.raises(ValidationError):
        SimulationIntent.model_validate(body)


def test_material_quantities_normalize_to_mm_n_mpa_units():
    modulus, modulus_original = normalize_youngs_modulus(210, "GPa")
    density, density_original = normalize_density(7850, "kg/m^3")
    assert modulus == 210_000
    assert modulus_original.unit == "GPa"
    assert density == pytest.approx(7.85e-9)
    assert density_original.value == 7850


# --------------------------------------------------------------------------
# 1. Original-versus-normalized consistency
# --------------------------------------------------------------------------


def test_complete_engineering_setup_is_ready_and_canonical_dump_is_explicit():
    intent = SimulationIntent.model_validate(payload())
    report = validate_intent(intent)
    assert report.readiness_status == "ready"
    assert report.export_eligible
    dumped = intent.model_dump(mode="json")
    assert dumped["materials"][0]["E_MPa"] == 210_000.0
    assert dumped["materials"][0]["youngs_modulus_original"] == {
        "value": 210.0, "unit": "GPa"
    }
    assert dumped["mesh_settings"]["global_element_size_mm"] == 2.5
    assert dumped["solver_settings"]["target"] == "calculix"


def test_contradictory_youngs_modulus_is_rejected():
    body = payload()
    body["materials"][0]["E_MPa"] = 200_000.0
    assert rejection_code(body) == "material.youngs_modulus_normalization_mismatch"


def test_contradictory_density_is_rejected():
    body = payload()
    body["materials"][0]["density_tonne_per_mm3"] = 2.7e-9
    assert rejection_code(body) == "material.density_normalization_mismatch"


def test_density_provenance_without_a_normalized_density_is_rejected():
    body = payload()
    body["materials"][0]["density_tonne_per_mm3"] = None
    assert rejection_code(body) == "material.density_normalization_missing"


@pytest.mark.parametrize(
    ("path", "unit"),
    [
        (("materials", 0, "youngs_modulus_original"), "psi"),
        (("materials", 0, "density_original"), "lb/in^3"),
        (("loads", 0, "original_force"), "lbf"),
        (("mesh_settings", "target_size_original"), "inch"),
    ],
)
def test_unsupported_original_units_report_one_stable_code(path, unit):
    """An unsupported unit is a stable engineering rejection, not a Literal
    implementation detail."""

    body = payload()
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = {**target[path[-1]], "unit": unit}
    with pytest.raises(ValidationError) as caught:
        SimulationIntent.model_validate(body)
    errors = caught.value.errors()
    unit_errors = [item for item in errors if item["loc"][-1] == "unit"]
    assert unit_errors, errors
    for item in unit_errors:
        assert item["type"] == "value_error"
        assert item["ctx"]["error"].code == UNSUPPORTED_UNIT_CODE
        # The rejected value is never echoed back into the message.
        assert unit not in str(item["ctx"]["error"])


def test_unsupported_unit_code_is_a_fixed_server_owned_constant():
    assert UNSUPPORTED_UNIT_CODE == "quantity.unsupported_unit"


@pytest.mark.parametrize(
    ("quantity_type", "kind"),
    [
        (ForceQuantity, "force"),
        (StressQuantity, "stress"),
        (LengthQuantity, "length"),
        (DensityQuantity, "density"),
        (AccelerationQuantity, "acceleration"),
    ],
)
def test_every_quantity_type_publishes_the_same_code_and_keeps_its_enum(
    quantity_type, kind
):
    """One code for every kind; the wire vocabulary is unchanged."""

    for unit in supported_units(kind):
        assert quantity_type(value=1.0, unit=unit).unit == unit
    for unsupported in ("", "psi", "lbf", "inch", "furlong", "MPa " , 7, None):
        with pytest.raises(ValidationError) as caught:
            quantity_type(value=1.0, unit=unsupported)
        codes_found = {
            item["ctx"]["error"].code
            for item in caught.value.errors()
            if item["loc"] == ("unit",) and "ctx" in item
        }
        assert codes_found == {UNSUPPORTED_UNIT_CODE}, unsupported
    # The declared Literal still reaches the generated contracts.
    schema = quantity_type.model_json_schema()
    assert schema["properties"]["unit"]["enum"] == list(supported_units(kind))


def test_resultant_force_magnitude_mismatch_is_rejected():
    body = payload()
    body["loads"][0]["magnitude_N"] = 4000.0
    body["loads"][0]["vector"] = [0.0, -4000.0, 0.0]
    assert rejection_code(body) == "load.force.magnitude_normalization_mismatch"


def test_resultant_force_vector_magnitude_mismatch_is_rejected():
    body = payload()
    body["loads"][0]["vector"] = [0.0, -4000.0, 0.0]
    assert rejection_code(body) == "load.force.vector_magnitude_mismatch"


def test_resultant_force_vector_direction_mismatch_is_rejected():
    body = payload()
    body["loads"][0]["vector"] = [5000.0, 0.0, 0.0]
    assert rejection_code(body) == "load.force.vector_direction_mismatch"


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ([0.0, -2.0, 0.0], "load.force.direction_not_normalized"),
        ([0.0, 0.0, 0.0], "load.force.direction_zero"),
    ],
)
def test_resultant_force_direction_must_be_normalized_and_nonzero(direction, expected):
    body = payload()
    body["loads"][0]["direction"] = direction
    assert rejection_code(body) == expected


def test_partial_force_provenance_is_rejected():
    body = payload()
    del body["loads"][0]["direction"]
    assert rejection_code(body) == "load.force.provenance_incomplete"


def test_resultant_force_rejects_irrelevant_pressure_or_traction_metadata():
    for stray in ("magnitude", "original_pressure", "original_traction", "magnitude_MPa"):
        body = payload()
        body["loads"][0][stray] = 1.0
        with pytest.raises(ValidationError) as caught:
            SimulationIntent.model_validate(body)
        assert "extra_forbidden" in str(caught.value)


def test_pressure_has_no_client_controlled_direction():
    body = payload()
    body["loads"][0] = {
        "type": "pressure",
        "region_ref": "loaded",
        "magnitude": 2.0,
        "original_pressure": {"value": 2.0, "unit": "MPa"},
        "direction": [0.0, -1.0, 0.0],
    }
    with pytest.raises(ValidationError) as caught:
        SimulationIntent.model_validate(body)
    assert "extra_forbidden" in str(caught.value)
    assert "direction" not in PressureLoad.model_fields


def test_contradictory_pressure_normalization_is_rejected():
    body = payload()
    body["loads"][0] = {
        "type": "pressure",
        "region_ref": "loaded",
        "magnitude": 3.0,
        "original_pressure": {"value": 2.0, "unit": "MPa"},
    }
    assert rejection_code(body) == "load.pressure_normalization_mismatch"


def test_pressure_is_a_nonnegative_scalar():
    body = payload()
    body["loads"][0] = {
        "type": "pressure", "region_ref": "loaded", "magnitude": -1.0,
    }
    with pytest.raises(ValidationError):
        SimulationIntent.model_validate(body)


def test_contradictory_traction_normalization_is_rejected():
    body = payload()
    body["loads"][0] = {
        "type": "surface_traction",
        "region_ref": "loaded",
        "vector": [0.0, -2.0, 0.0],
        "original_traction": {"value": 3.0, "unit": "MPa"},
        "magnitude_MPa": 3.0,
        "direction": [0.0, -1.0, 0.0],
    }
    assert rejection_code(body) == "load.traction.vector_magnitude_mismatch"


def test_traction_original_quantity_must_agree_with_its_magnitude():
    body = payload()
    body["loads"][0] = {
        "type": "surface_traction",
        "region_ref": "loaded",
        "vector": [0.0, -2.0, 0.0],
        "original_traction": {"value": 3.0, "unit": "MPa"},
        "magnitude_MPa": 2.0,
        "direction": [0.0, -1.0, 0.0],
    }
    assert rejection_code(body) == "load.traction.magnitude_normalization_mismatch"


def gravity_body(**overrides) -> dict:
    body = payload()
    body["materials"][0]["density_tonne_per_mm3"] = 7.85e-9
    load = {
        "type": "gravity",
        "region_ref": None,
        "vector": [0.0, 0.0, -9810.0],
        "original_acceleration": {"value": 9.81, "unit": "m/s^2"},
        "magnitude_mm_per_s2": 9810.0,
        "direction": [0.0, 0.0, -1.0],
    }
    load.update(overrides)
    body["loads"][0] = load
    return body


def test_contradictory_gravity_normalization_is_rejected():
    assert rejection_code(
        gravity_body(magnitude_mm_per_s2=9.81)
    ) == "load.gravity.magnitude_normalization_mismatch"
    assert rejection_code(
        gravity_body(vector=[0.0, 0.0, -1000.0])
    ) == "load.gravity.vector_magnitude_mismatch"
    assert rejection_code(
        gravity_body(direction=[0.0, 0.0, 0.0])
    ) == "load.gravity.direction_zero"


def test_gravity_requires_usable_density_and_prohibits_a_surface_target():
    body = gravity_body()
    body["materials"][0].pop("density_original")
    body["materials"][0]["density_tonne_per_mm3"] = None
    assert "material.density_required_for_gravity" in codes(
        SimulationIntent.model_validate(body)
    )

    body = gravity_body(region_ref="loaded")  # a cad_face surface region
    assert "load.region_entity_unsupported" in codes(
        SimulationIntent.model_validate(body)
    )


def test_contradictory_mesh_size_normalization_is_rejected():
    body = payload()
    body["mesh_settings"]["target_size_original"] = {"value": 1.0, "unit": "m"}
    assert rejection_code(body) == "mesh.target_size_normalization_mismatch"


@pytest.mark.parametrize("size", [0.0, -1.0, math.inf, math.nan])
def test_invalid_mesh_size_is_rejected(size):
    body = payload()
    body["mesh_settings"]["global_element_size_mm"] = size
    body["mesh_settings"].pop("target_size_original")
    with pytest.raises(ValidationError):
        SimulationIntent.model_validate(body)


@pytest.mark.parametrize("field,value", [
    ("E_MPa", 0.0), ("E_MPa", math.inf), ("nu", -1.0), ("nu", 0.5),
])
def test_invalid_material_ranges_are_rejected(field, value):
    body = payload()
    body["materials"][0].pop("youngs_modulus_original")
    body["materials"][0][field] = value
    with pytest.raises(ValidationError):
        SimulationIntent.model_validate(body)


# --------------------------------------------------------------------------
# 2. Controlled schema-version migration
# --------------------------------------------------------------------------


LEGACY_EXAMPLES = [
    "examples/bracket_confirmed_export_ready.json",
    "examples/bracket_sprint_goal.json",
    "examples/plate_hole_pressure.json",
    "docs/task13-bracket-demo.json",
]


def test_the_current_schema_version_is_three_with_registered_migrations():
    assert SIMULATION_INTENT_SCHEMA_VERSION == 3
    assert SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION == 1
    assert SIMULATION_INTENT_MIGRATIONS.registered_edges == (1, 2)


@pytest.mark.parametrize("relative", LEGACY_EXAMPLES)
def test_legacy_v1_payload_loads_but_stays_incomplete(relative):
    raw = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    assert raw[SCHEMA_VERSION_FIELD] == 1

    intent = load_simulation_intent(raw, source=relative)
    assert intent.schema_version == SIMULATION_INTENT_SCHEMA_VERSION

    # None of the version-2 decisions was granted.
    assert intent.analysis.dimensionality is None
    assert intent.analysis.solver_target is None
    assert intent.analysis.coordinate_system is None
    assert intent.mesh_settings is None
    assert intent.solver_settings is None

    report = validate_intent(intent)
    assert report.readiness_status == "structurally_incomplete"
    assert report.export_eligible is False
    assert {
        "analysis.dimensionality_missing",
        "analysis.solver_target_missing",
        "analysis.coordinate_system_missing",
        "mesh.missing",
        "solver.missing",
    } <= {issue.code for issue in report.issues}


@pytest.mark.parametrize("relative", LEGACY_EXAMPLES)
def test_migrated_legacy_dump_reload_is_deterministic(relative):
    raw = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    once = load_simulation_intent(raw, source=relative)
    first = dump_simulation_intent(once)
    twice = load_simulation_intent(first, source=relative)
    second = dump_simulation_intent(twice)
    assert first == second
    assert once == twice
    assert first[SCHEMA_VERSION_FIELD] == SIMULATION_INTENT_SCHEMA_VERSION


def test_migration_reinterprets_no_unit_or_load_semantics():
    raw = json.loads(
        (ROOT / "examples/bracket_sprint_goal.json").read_text(encoding="utf-8")
    )
    before = copy.deepcopy(raw)
    migrated = SIMULATION_INTENT_MIGRATIONS.migrate(raw)
    assert migrated[SCHEMA_VERSION_FIELD] == SIMULATION_INTENT_SCHEMA_VERSION
    for key in ("materials", "bcs", "loads", "assumptions",
                "validation_status"):
        assert migrated[key] == before[key], key
    for current, old in zip(migrated["regions"], before["regions"], strict=True):
        assert {
            key: value
            for key, value in current.items()
            if key != "cad_face_target"
        } == {
            key: value for key, value in old.items()
            if key != "entity_ids"
        }
        assert current["cad_face_target"]["resolution"] == "legacy_local_only"
    assert migrated["analysis"]["units"] == before["analysis"]["units"]
    assert migrated["analysis"]["type"] == before["analysis"]["type"]
    # The migration adds five missing decisions and explicit legacy CAD state.
    added = {
        key for key in migrated if key not in before
    } | {
        f"analysis.{key}"
        for key in migrated["analysis"]
        if key not in before["analysis"]
    }
    assert added == {
        "mesh_settings",
        "solver_settings",
        "analysis.dimensionality",
        "analysis.solver_target",
        "analysis.coordinate_system",
    }
    assert migrated["mesh_settings"] is None
    assert migrated["solver_settings"] is None


def test_explicit_current_version_payload_becomes_ready():
    intent = load_simulation_intent(payload(), source="client")
    report = validate_intent(intent)
    assert report.readiness_status == "ready"
    assert report.export_eligible is True


def test_unsupported_future_schema_version_is_rejected():
    body = payload()
    body[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION + 1
    with pytest.raises(UnsupportedFutureVersionError):
        load_simulation_intent(body, source="client")


def test_canonical_hash_is_deterministic_across_reload():
    intent = load_simulation_intent(payload(), source="client")
    reloaded = load_simulation_intent(dump_simulation_intent(intent), source="client")
    assert json.dumps(
        dump_simulation_intent(intent), sort_keys=True
    ) == json.dumps(dump_simulation_intent(reloaded), sort_keys=True)


# --------------------------------------------------------------------------
# 3. Finite translational prescribed displacement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "components",
    [
        {"x": 1.0}, {"x": -1.0},
        {"y": 1.0}, {"y": -1.0},
        {"z": 1.0}, {"z": -1.0},
        {"x": 0.0, "y": 2.5}, {"x": -0.0, "z": -2.5},
        {"x": 1e-12}, {"z": -1e-12},
    ],
)
def test_finite_nonzero_prescribed_displacement_is_accepted(components):
    body = payload()
    body["bcs"] = [{
        "type": "prescribed_displacement",
        "region_ref": "fixed",
        "components": components,
    }]
    intent = SimulationIntent.model_validate(body)
    assert intent.bcs[0].components == components


@pytest.mark.parametrize(
    "components",
    [
        {"x": 0.0}, {"x": -0.0}, {"y": 0.0}, {"y": -0.0}, {"z": 0.0}, {"z": -0.0},
        {"x": 0.0, "y": -0.0, "z": 0.0},
    ],
)
def test_zero_prescribed_displacement_is_accepted_component_wise(components):
    body = payload()
    body["bcs"] = [{
        "type": "prescribed_displacement",
        "region_ref": "fixed",
        "components": components,
    }]
    intent = SimulationIntent.model_validate(body)
    assert intent.bcs[0].components == components
    report = validate_intent(intent)
    missing = {"x", "y", "z"} - set(components)
    if missing:
        assert report.readiness_status == "semantically_invalid"
        assert {
            f"constraint.rigid_body_translation_{axis}" for axis in missing
        } <= {issue.code for issue in report.issues}
    else:
        assert report.readiness_status == "ready"
        assert report.export_eligible is True


@pytest.mark.parametrize("value", [math.inf, math.nan])
def test_nonfinite_prescribed_displacement_is_rejected(value):
    body = payload()
    body["bcs"] = [{
        "type": "prescribed_displacement",
        "region_ref": "fixed",
        "components": {"z": value},
    }]
    assert rejection_code(body) == "bc.prescribed_displacement_nonfinite"


def test_finite_nonzero_prescribed_displacement_is_not_a_validation_issue():

    intent = SimulationIntent.model_validate(payload())
    unsupported = PrescribedDisplacementBC.model_construct(
        type="prescribed_displacement",
        region_ref="fixed",
        components={"z": 2.5},
        components_original=None,
    )
    candidate = intent.model_copy(update={"bcs": [unsupported]}, deep=True)
    report = validate_intent(candidate)
    assert "bc.prescribed_displacement_nonzero" not in {
        issue.code for issue in report.issues
    }
    assert "constraint.rigid_body_translation_x" in {
        issue.code for issue in report.issues
    }
    assert report.export_eligible is False


def test_prescribed_displacement_provenance_must_agree():
    body = payload()
    body["bcs"] = [{
        "type": "prescribed_displacement",
        "region_ref": "fixed",
        "components": {"z": 0.0},
        "components_original": {"z": {"value": 1.0, "unit": "mm"}},
    }]
    assert rejection_code(body) == "bc.displacement_normalization_mismatch"

    body["bcs"][0]["components_original"] = {"x": {"value": 0.0, "unit": "mm"}}
    assert rejection_code(body) == "bc.displacement_provenance_mismatch"


# --------------------------------------------------------------------------
# 4. Load / constraint to region compatibility
# --------------------------------------------------------------------------


ENTITY_TYPES = ("cad_face", "cad_edge", "mesh_face", "node_set", "element_set")


def test_entity_type_matrix_uses_the_existing_region_vocabulary():
    from typing import get_args

    assert set(ENTITY_TYPES) == set(get_args(EntityType))
    for rule in (*LOAD_REGION_COMPATIBILITY.values(), *BC_REGION_COMPATIBILITY.values()):
        assert rule.entity_types <= set(ENTITY_TYPES)
        # Meshing does not exist yet, so no mesh-domain type is invented.
        assert rule.target in {"required", "optional", "prohibited"}


def load_for(load_type: str, region_ref: str | None) -> dict:
    bodies = {
        "resultant_surface_force": {
            "type": "resultant_surface_force", "vector": [0.0, -5000.0, 0.0],
        },
        "surface_traction": {
            "type": "surface_traction", "vector": [0.0, -2.0, 0.0],
        },
        "pressure": {"type": "pressure", "magnitude": 2.0},
        "gravity": {"type": "gravity", "vector": [0.0, 0.0, -9810.0]},
        "concentrated_force": {
            "type": "concentrated_force", "vector": [0.0, -100.0, 0.0],
        },
    }
    return {**bodies[load_type], "region_ref": region_ref}


@pytest.mark.parametrize("load_type", sorted(LOAD_REGION_COMPATIBILITY))
@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
def test_load_to_region_compatibility_matrix(load_type, entity_type):
    rule = LOAD_REGION_COMPATIBILITY[load_type]
    body = payload()
    body["materials"][0]["density_tonne_per_mm3"] = 7.85e-9
    body["regions"] = [region("fixed", "cad_face", [1]), region("target", entity_type)]
    body["loads"] = [load_for(load_type, "target")]
    found = codes(SimulationIntent.model_validate(body))

    if rule.target == "prohibited":
        assert "load.region_target_prohibited" in found
    elif entity_type in rule.entity_types:
        assert "load.region_entity_unsupported" not in found
    else:
        assert "load.region_entity_unsupported" in found


@pytest.mark.parametrize("load_type", sorted(LOAD_REGION_COMPATIBILITY))
def test_load_target_requirement_and_prohibition(load_type):
    rule = LOAD_REGION_COMPATIBILITY[load_type]
    body = payload()
    body["materials"][0]["density_tonne_per_mm3"] = 7.85e-9
    body["regions"] = [region("fixed", "cad_face", [1])]
    body["loads"] = [load_for(load_type, None)]
    if rule.target == "required":
        # A required target is not even representable as null for these loads.
        with pytest.raises(ValidationError):
            SimulationIntent.model_validate(body)
        return
    found = codes(SimulationIntent.model_validate(body))
    assert "load.region_missing" not in found
    assert "load.region_target_prohibited" not in found


@pytest.mark.parametrize("bc_type", sorted(BC_REGION_COMPATIBILITY))
@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
def test_constraint_to_region_compatibility_matrix(bc_type, entity_type):
    rule = BC_REGION_COMPATIBILITY[bc_type]
    body = payload()
    body["regions"] = [region("target", entity_type), region("loaded", "cad_face", [2])]
    if bc_type == "fixed_displacement":
        body["bcs"] = [{
            "type": bc_type, "region_ref": "target", "components": ["x", "y", "z"],
        }]
    else:
        body["bcs"] = [{
            "type": bc_type, "region_ref": "target", "components": {"z": 0.0},
        }]
    found = codes(SimulationIntent.model_validate(body))
    if entity_type in rule.entity_types:
        assert "bc.region_entity_unsupported" not in found
    else:
        assert "bc.region_entity_unsupported" in found


def test_region_existence_and_terminal_status_are_validated():
    body = payload()
    body["regions"][1]["status"] = "rejected"
    found = codes(SimulationIntent.model_validate(body))
    assert {"region.rejected", "load.region_rejected"} <= found

    body = payload()
    body["regions"][1]["status"] = "proposed"
    found = codes(SimulationIntent.model_validate(body))
    assert {"region.proposed", "load.region_unconfirmed"} <= found

    body = payload()
    body["loads"][0]["region_ref"] = "loaded"
    body["regions"] = [region("fixed", "cad_face", [1])]
    with pytest.raises(ValidationError):
        SimulationIntent.model_validate(body)  # unknown region reference


# --------------------------------------------------------------------------
# 5. Readiness precedence
# --------------------------------------------------------------------------


def incomplete_payload() -> dict:
    body = payload()
    body["mesh_settings"] = None
    return body


def invalid_payload() -> dict:
    body = payload()
    # A supported load on an unsupported region type: complete, but wrong.
    body["regions"][1]["entity_type"] = "element_set"
    body["regions"][1]["entity_ids"] = ["VOLUME"]
    body["regions"][1]["cad_face_target"] = None
    return body


@pytest.mark.parametrize(
    ("build", "stale", "expected"),
    [
        (payload, False, "ready"),
        (payload, True, "stale_source"),
        (incomplete_payload, False, "structurally_incomplete"),
        (incomplete_payload, True, "structurally_incomplete"),
        (invalid_payload, False, "semantically_invalid"),
        (invalid_payload, True, "semantically_invalid"),
    ],
)
def test_readiness_precedence_matrix(build, stale, expected):
    intent = SimulationIntent.model_validate(build())
    report = validate_intent(intent, source_is_stale=stale)
    assert report.readiness_status == expected
    if stale:
        # Every finding is retained even though one status is selected.
        assert "source.stale" in {issue.code for issue in report.issues}
    assert report.export_eligible is False or expected == "ready"


@pytest.mark.parametrize(
    ("drop", "code"),
    [
        ("materials", "material.missing"),
        ("bcs", "bc.missing"),
        ("loads", "load.missing"),
        ("mesh_settings", "mesh.missing"),
        ("solver_settings", "solver.missing"),
    ],
)
@pytest.mark.parametrize("stale", [False, True])
def test_every_missing_part_reports_structural_incompleteness(drop, code, stale):
    body = payload()
    body[drop] = None if drop in {"mesh_settings", "solver_settings"} else []
    intent = SimulationIntent.model_validate(body)
    report = validate_intent(intent, source_is_stale=stale)
    assert code in {issue.code for issue in report.issues}
    assert report.readiness_status == "structurally_incomplete"
    assert report.export_eligible is False


@pytest.mark.parametrize(
    ("region_status", "assumption_status", "expected"),
    [
        ("confirmed", "accepted", "ready"),
        ("proposed", "accepted", "awaiting_region_confirmation"),
        ("rejected", "accepted", "semantically_invalid"),
        ("confirmed", "pending", "awaiting_assumption_acceptance"),
        ("confirmed", "rejected", "semantically_invalid"),
    ],
)
def test_region_and_assumption_readiness_precedence(
    region_status, assumption_status, expected
):
    body = payload()
    for item in body["regions"]:
        item["status"] = region_status
    body["assumptions"] = [{
        "text": "Downward was interpreted as negative Y.",
        "criticality": "noncritical" if assumption_status != "rejected" else "unit_critical",
        "status": assumption_status,
    }]
    report = validate_intent(SimulationIntent.model_validate(body))
    assert report.readiness_status == expected


def test_issue_ordering_is_deterministic():
    intent = SimulationIntent.model_validate(incomplete_payload())
    first = validate_intent(intent, source_is_stale=True)
    second = validate_intent(intent, source_is_stale=True)
    assert first.model_dump_json() == second.model_dump_json()
    ordering = [
        ({"error": 0, "warning": 1}[issue.severity], issue.code,
         issue.object_type or "", issue.object_id or "", issue.field or "",
         issue.message)
        for issue in first.issues
    ]
    assert ordering == sorted(ordering)


# --------------------------------------------------------------------------
# 6. Interpretation approves no engineering configuration
# --------------------------------------------------------------------------


#: An underspecified request: it states a constraint and a load, and says
#: nothing about dimensionality, coordinate system, solver target, meshing or
#: requested results.
UNDERSPECIFIED_INSTRUCTION = (
    "Fix both bolt holes and pull the top flange down with 5 kN."
)

#: The typed operations a successful interpretation of that request yields.
#: Interpretation itself is exercised elsewhere; what matters here is that the
#: production bridge turns them into regions, a constraint, a load and
#: assumptions -- and into nothing else.
UNDERSPECIFIED_INTERPRETATION = {
    "intents": [
        {
            "op_list": [{"op": "hole_groups", "min_size": 2, "max_size": 2}],
            "bc": {"type": "fixed_displacement", "components": ["x", "y", "z"]},
            "load": None,
            "target_description": "both bolt holes",
        },
        {
            "op_list": [{"op": "labeled", "name": "top_face"}],
            "bc": None,
            "load": {
                "type": "resultant_surface_force",
                "magnitude": "5 kN",
                "direction": "downward",
            },
            "target_description": "the top flange",
        },
    ]
}

MISSING_CONFIGURATION_CODES = {
    "analysis.dimensionality_missing",
    "analysis.solver_target_missing",
    "analysis.coordinate_system_missing",
    "mesh.missing",
    "solver.missing",
}

BRACKET_STEP = ROOT / "tests" / "fixtures" / "bracket.step"


def underspecified_proposal() -> SimulationIntent:
    """Run the production interpretation bridge over a real fixture."""

    inventory, _ = get_inventory(BRACKET_STEP)
    proposal = propose_from_interpretation(
        instruction=UNDERSPECIFIED_INSTRUCTION,
        interpretation=Interpretation.model_validate(UNDERSPECIFIED_INTERPRETATION),
        inventory=inventory,
        cylinders=analyze_cylinders(BRACKET_STEP),
    )
    assert proposal.clarifications == []
    assert proposal.intent is not None
    return proposal.intent


def assert_configuration_is_absent(intent: dict) -> None:
    assert intent["analysis"]["dimensionality"] is None
    assert intent["analysis"]["solver_target"] is None
    assert intent["analysis"]["coordinate_system"] is None
    assert intent["mesh_settings"] is None
    assert intent["solver_settings"] is None


def test_interpretation_proposes_conditions_but_no_engineering_configuration():
    """The bridge states what the instruction supports, and nothing else."""

    intent = underspecified_proposal()
    dumped = intent.model_dump(mode="json")

    # It proposes exactly what the request contained ...
    assert [region["status"] for region in dumped["regions"]] == ["proposed"] * 2
    assert [bc["type"] for bc in dumped["bcs"]] == ["fixed_displacement"]
    assert [load["type"] for load in dumped["loads"]] == ["resultant_surface_force"]
    assert dumped["assumptions"], "unit and material assumptions must be proposed"
    # ... and no engineering configuration at all.
    assert_configuration_is_absent(dumped)

    report = validate_intent(intent)
    assert report.readiness_status == "structurally_incomplete"
    assert report.export_eligible is False
    assert MISSING_CONFIGURATION_CODES <= {issue.code for issue in report.issues}


def test_proposal_records_no_mesh_provenance_that_could_read_as_acceptance():
    """``target_size_original`` is provenance, never an engineering decision."""

    dumped = underspecified_proposal().model_dump(mode="json")
    assert dumped["mesh_settings"] is None
    assert "target_size_original" not in json.dumps(dumped)

    source = (ROOT / "app" / "orchestration.py").read_text(encoding="utf-8")
    for forbidden in (
        "MeshSettings(",
        "SolverSettings(",
        "gmsh_tet_v1",
        "linear_static_v1",
        "3d_solid",
        "global_cartesian",
        "target_size_original",
        "requested_results",
    ):
        assert forbidden not in source, forbidden


def engineering_revision(intent: dict) -> dict:
    """Return *intent* plus explicit configuration and engineer material.

    Every server-managed region and assumption status is round-tripped exactly
    as the server reported it, so this is a configuration revision rather than
    an approval.
    """

    revised = {
        **copy.deepcopy(intent),
        "analysis": {
            **copy.deepcopy(intent["analysis"]),
            **EXPLICIT_ANALYSIS_DECISIONS,
        },
        "mesh_settings": dict(EXPLICIT_MESH_SETTINGS),
        "solver_settings": dict(EXPLICIT_SOLVER_SETTINGS),
    }
    if not revised["materials"]:
        revised["materials"] = [{
            "name": "engineer_steel",
            "model": "linear_elastic_isotropic",
            "authority": "engineer_entered",
            "E_MPa": 210_000.0,
            "nu": 0.3,
        }]
    return revised


def test_confirming_and_accepting_everything_never_supplies_configuration(tmp_path):
    """The full durable review loop, across a restart, then one real revision."""

    config = LocalDataConfig(tmp_path / "data")
    proposal = underspecified_proposal().model_dump(mode="json")

    with TestClient(
        create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    ) as client:
        app = client.app
        project = create_project(app, "underspecified")
        uploaded = upload(
            app, project["id"], BRACKET_STEP.read_bytes(), filename="bracket.step"
        )
        version_id = uploaded["model_version"]["id"]
        record, _raw, artifact = app.state.persistence.read_geometry_identity(
            version_id
        )
        faces = {face["source_ref"]: face for face in artifact["faces"]}
        for region_body in proposal["regions"]:
            tags = region_body["cad_face_target"]["source_face_tags"]
            selected = [faces[tag] for tag in tags]
            region_body["cad_face_target"] = {
                "model_version_id": version_id,
                "artifact_sha256": record.integrity_sha256,
                "resolution": "resolved",
                "stable_identities": sorted(
                    face["stable_identity"] for face in selected
                ),
                "source_face_tags": list(tags),
            }
        created = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "underspecified-create",
                "intent": proposal,
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        current = created.json()["current"]
        assert current["validation"]["readiness_status"] == "structurally_incomplete"
        assert current["export_eligible"] is False
        assert_configuration_is_absent(current["intent"])

        # 2. confirm every proposed region.
        revision = current["revision"]
        for region in current["intent"]["regions"]:
            assert region["status"] == "proposed"
            response = request(
                app, "POST",
                f"/api/v1/setups/{setup_id}/regions/{region['id']}/confirm",
                json={
                    "expected_revision": revision,
                    "request_id": f"confirm-{region['id']}",
                },
            )
            assert response.status_code == 201, response.text
            current, revision = response.json(), response.json()["revision"]

        # 3. accept every existing assumption, critical and noncritical alike.
        for assumption in current["intent"]["assumptions"]:
            assert assumption["status"] == "pending"
            response = request(
                app, "POST",
                f"/api/v1/setups/{setup_id}/assumptions/{assumption['id']}/accept",
                json={
                    "expected_revision": revision,
                    "request_id": f"accept-{assumption['id']}",
                },
            )
            assert response.status_code == 201, response.text
            current, revision = response.json(), response.json()["revision"]

        # 4. and 5. the setup is still incomplete and still has no configuration.
        assert all(
            region["status"] == "confirmed" for region in current["intent"]["regions"]
        )
        assert all(
            item["status"] == "accepted" for item in current["intent"]["assumptions"]
        )
        assert current["validation"]["readiness_status"] == "structurally_incomplete"
        assert current["export_eligible"] is False
        assert_configuration_is_absent(current["intent"])
        assert MISSING_CONFIGURATION_CODES <= {
            issue["code"] for issue in current["validation"]["issues"]
        }

    # Restart: reopening the durable store preserves the incompleteness.
    with TestClient(
        create_app(tmp_path / "legacy-2", mode=RuntimeMode.TEST, data_config=config)
    ) as client:
        app = client.app
        reopened = request(app, "GET", f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200
        current = reopened.json()["current"]
        assert current["validation"]["readiness_status"] == "structurally_incomplete"
        assert current["export_eligible"] is False
        assert_configuration_is_absent(current["intent"])

        # 6. one explicit durable engineering-setup revision supplies the
        #    version-2 configuration and nothing else.
        response = request(
            app, "POST", f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": current["revision"],
                "request_id": "engineering-configuration",
                "intent": engineering_revision(current["intent"]),
            },
        )
        assert response.status_code == 201, response.text
        configured = response.json()

        # 7. only now is the engineering setup ready. The selected CalculiX
        # fragment adapter still requires a mesh for this STEP source.
        assert configured["validation"]["readiness_status"] == "ready"
        assert configured["engineering_ready"] is True
        assert configured["export_eligible"] is False
        assert configured["artifact_capability"]["blocking_issue_codes"] == [
            "artifact.mapping_not_verified",
            "artifact.step_meshing_required",
        ]
        assert configured["intent"]["mesh_settings"] == EXPLICIT_MESH_SETTINGS
        assert configured["intent"]["solver_settings"] == EXPLICIT_SOLVER_SETTINGS
        assert configured["intent"]["materials"][0]["authority"] == "engineer_entered"
        for key in ("regions", "bcs", "loads", "assumptions"):
            assert configured["intent"][key] == current["intent"][key], key


# --------------------------------------------------------------------------
# 7. One stable unsupported-unit code through the durable API
# --------------------------------------------------------------------------


def durable_region(region_id: str, entity_type: str, entity_ids: list) -> dict:
    return {**region(region_id, entity_type, entity_ids), "status": "proposed"}


def durable_payload() -> dict:
    """A valid current-version setup carrying every original-quantity slot."""

    return {
        SCHEMA_VERSION_FIELD: SIMULATION_INTENT_SCHEMA_VERSION,
        "analysis": {
            "type": "static_structural",
            "units": {"length": "mm", "force": "N", "stress": "MPa"},
            **EXPLICIT_ANALYSIS_DECISIONS,
        },
        "materials": [{
            "name": "Steel",
            "model": "linear_elastic_isotropic",
            "E_MPa": 210_000.0,
            "nu": 0.3,
            "density_tonne_per_mm3": 7.85e-9,
            "youngs_modulus_original": {"value": 210.0, "unit": "GPa"},
            "density_original": {"value": 7850.0, "unit": "kg/m^3"},
        }],
        "regions": [
            durable_region("fixed_face", "mesh_face", [1]),
            durable_region("loaded_face", "mesh_face", [2]),
            durable_region("load_nodes", "node_set", ["LOAD_NODES"]),
            durable_region("solid_cells", "element_set", ["SOLID"]),
        ],
        "bcs": [{
            "type": "fixed_displacement",
            "region_ref": "fixed_face",
            "components": ["x", "y", "z"],
        }],
        "loads": [{
            "type": "resultant_surface_force",
            "region_ref": "loaded_face",
            "vector": [0.0, -5000.0, 0.0],
            "original_force": {"value": 5.0, "unit": "kN"},
            "magnitude_N": 5000.0,
            "direction": [0.0, -1.0, 0.0],
        }],
        "assumptions": [],
        "mesh_settings": dict(EXPLICIT_MESH_SETTINGS),
        "solver_settings": dict(EXPLICIT_SOLVER_SETTINGS),
        "validation_status": "unvalidated",
    }


def _set_prescribed_displacement(body: dict, unit: str) -> list:
    body["bcs"] = [{
        "type": "prescribed_displacement",
        "region_ref": "fixed_face",
        "components": {"z": 0.0},
        "components_original": {"z": {"value": 0.0, "unit": unit}},
    }]
    return ["bcs", 0, "prescribed_displacement", "components_original", "z", "unit"]


def _set_material_quantity(field: str, unit: str, value: float):
    def apply(body: dict) -> list:
        body["materials"][0][field] = {"value": value, "unit": unit}
        return ["materials", 0, field, "unit"]

    return apply


def _set_mesh_target_size(body: dict, unit: str) -> list:
    body["mesh_settings"]["target_size_original"] = {"value": 1.0, "unit": unit}
    return ["mesh_settings", "target_size_original", "unit"]


def _set_load(load: dict, provenance_field: str):
    def apply(body: dict) -> list:
        body["loads"] = [copy.deepcopy(load)]
        return ["loads", 0, load["type"], provenance_field, "unit"]

    return apply


#: Every original quantity the R3.1 envelope retains, with an unsupported unit
#: drawn from a real engineering vocabulary this build does not support.
UNSUPPORTED_UNIT_CASES = {
    "youngs_modulus": lambda body: _set_material_quantity(
        "youngs_modulus_original", "psi", 210.0
    )(body),
    "density": lambda body: _set_material_quantity(
        "density_original", "lb/in^3", 0.284
    )(body),
    "prescribed_displacement": lambda body: _set_prescribed_displacement(body, "inch"),
    "mesh_target_size": lambda body: _set_mesh_target_size(body, "inch"),
    "resultant_force": _set_load(
        {
            "type": "resultant_surface_force",
            "region_ref": "loaded_face",
            "vector": [0.0, -5000.0, 0.0],
            "original_force": {"value": 5.0, "unit": "lbf"},
            "magnitude_N": 5000.0,
            "direction": [0.0, -1.0, 0.0],
        },
        "original_force",
    ),
    "concentrated_force": _set_load(
        {
            "type": "concentrated_force",
            "region_ref": "load_nodes",
            "vector": [0.0, -100.0, 0.0],
            "original_force": {"value": 100.0, "unit": "lbf"},
            "magnitude_N": 100.0,
            "direction": [0.0, -1.0, 0.0],
        },
        "original_force",
    ),
    "pressure": _set_load(
        {
            "type": "pressure",
            "region_ref": "loaded_face",
            "magnitude": 2.0,
            "original_pressure": {"value": 2.0, "unit": "psi"},
        },
        "original_pressure",
    ),
    "traction": _set_load(
        {
            "type": "surface_traction",
            "region_ref": "loaded_face",
            "vector": [0.0, -2.0, 0.0],
            "original_traction": {"value": 2.0, "unit": "psi"},
            "magnitude_MPa": 2.0,
            "direction": [0.0, -1.0, 0.0],
        },
        "original_traction",
    ),
    "gravity": _set_load(
        {
            "type": "gravity",
            "region_ref": "solid_cells",
            "vector": [0.0, 0.0, -9810.0],
            "original_acceleration": {"value": 32.2, "unit": "ft/s^2"},
            "magnitude_mm_per_s2": 9810.0,
            "direction": [0.0, 0.0, -1.0],
        },
        "original_acceleration",
    ),
}

#: The unsupported spellings above, so a response can be checked for reflection.
SUBMITTED_UNSUPPORTED_UNITS = ("psi", "lb/in^3", "inch", "lbf", "ft/s^2")


@pytest.fixture
def durable_setup(tmp_path):
    """A created durable setup plus a caller for its revision endpoint."""

    config = LocalDataConfig(tmp_path / "data")
    with TestClient(
        create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    ) as client:
        app = client.app
        project = create_project(app, "unsupported-units")
        uploaded = upload(app, project["id"], MINIMAL_INP)
        created = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": "units-create",
                "intent": durable_payload(),
            },
        )
        assert created.status_code == 201, created.text
        yield app, created.json()["setup"]["id"]


def revisions_of(app, setup_id: str) -> list:
    response = request(app, "GET", f"/api/v1/setups/{setup_id}/revisions")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("case", sorted(UNSUPPORTED_UNIT_CASES))
def test_durable_revision_rejects_every_unsupported_unit_with_one_code(
    durable_setup, case
):
    app, setup_id = durable_setup
    before = revisions_of(app, setup_id)

    body = durable_payload()
    expected_location = UNSUPPORTED_UNIT_CASES[case](body)

    response = request(
        app, "POST", f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": 1,
            "request_id": f"unsupported-{case}",
            "intent": body,
        },
    )

    # RFC 9457 status and structure.
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["type"] == "about:blank"
    assert problem["status"] == 422
    assert problem["code"] == "request_validation_failed"
    assert problem["retryable"] is False
    assert problem["title"] and problem["detail"] and problem["trace_id"]

    # Exactly the fixed server-owned engineering code, at the right field.
    unit_errors = [
        item for item in problem["errors"] if item.get("code") is not None
    ]
    assert unit_errors, problem["errors"]
    assert {item["code"] for item in unit_errors} == {"quantity.unsupported_unit"}
    assert [item["location"] for item in unit_errors] == [
        ["body", "intent", *expected_location]
    ]

    # No raw exception, internal object repr, or reflected client input.
    text = response.text
    for leaked in (
        "Traceback", "ValidationError", "pydantic", "literal_error",
        "object at 0x", "EngineeringConsistencyError", "Input should be",
    ):
        assert leaked not in text, leaked
    for unit in SUBMITTED_UNSUPPORTED_UNITS:
        assert unit not in text, unit

    # No revision was created.
    assert revisions_of(app, setup_id) == before
    assert request(app, "GET", f"/api/v1/setups/{setup_id}").json()[
        "setup"
    ]["current_revision"] == 1


def test_supported_units_continue_through_normalization_and_persistence(tmp_path):
    """The boundary rejects only unsupported units; the rest still normalizes."""

    config = LocalDataConfig(tmp_path / "data")
    body = durable_payload()
    # Deliberately non-canonical but supported spellings on every quantity.
    body["materials"][0]["youngs_modulus_original"] = {"value": 210.0, "unit": "GPa"}
    body["materials"][0]["density_original"] = {"value": 7850.0, "unit": "kg/m3"}
    body["mesh_settings"]["target_size_original"] = {"value": 0.0025, "unit": "m"}
    body["loads"][0]["original_force"] = {"value": 0.005, "unit": "MN"}

    with TestClient(
        create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    ) as client:
        app = client.app
        project = create_project(app, "supported-units")
        uploaded = upload(app, project["id"], MINIMAL_INP)
        created = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": "supported-units-create",
                "intent": body,
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        stored = created.json()["current"]["intent"]
        # The originals are retained verbatim ...
        assert stored["materials"][0]["density_original"] == {
            "value": 7850.0, "unit": "kg/m3"
        }
        assert stored["mesh_settings"]["target_size_original"] == {
            "value": 0.0025, "unit": "m"
        }
        # ... and agree with the normalized mm-N-MPa values ground.semantics
        # derives from them.
        assert stored["materials"][0]["E_MPa"] == 210_000.0
        assert stored["materials"][0]["density_tonne_per_mm3"] == pytest.approx(7.85e-9)
        assert stored["mesh_settings"]["global_element_size_mm"] == 2.5
        assert stored["loads"][0]["magnitude_N"] == 5000.0

    with TestClient(
        create_app(tmp_path / "legacy-2", mode=RuntimeMode.TEST, data_config=config)
    ) as client:
        reopened = request(client.app, "GET", f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200
        assert reopened.json()["current"]["intent"] == stored


def test_semantics_module_remains_the_sole_conversion_owner():
    """``ir.schema`` must reach conversion through ``ground.semantics``."""

    source = (ROOT / "ir" / "schema.py").read_text(encoding="utf-8")
    assert "from ground.semantics import" in source
    assert hasattr(semantics, "normalize_quantity")
    assert hasattr(semantics, "normalized_matches")
