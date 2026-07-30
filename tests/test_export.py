"""Task 14 deterministic export adapters and server artifact gate."""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import re
import shutil
import socket
import subprocess
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app import server as server_module
from app.server import create_app
from export.abaqus_py import _preflight as abaqus_preflight
from export.abaqus_py import (
    _render_abaqus_py_with_verified_mapping,
    export_abaqus_py,
)
from export.ccx_inp import export_ccx_inp
from export.common import (
    CadModelMetadata,
    ElementFaceReference,
    ExportNotReadyError,
    InvalidRegionReferenceError,
    MeshModelMetadata,
    MissingMaterialAssignmentError,
    MissingMeshTopologyError,
    MissingRegionMappingError,
    UnsupportedEntityTypeError,
    UnsupportedLoadTypeError,
)
from geom.meshes import _scan_inp_native_ids, parse_inp
from ir.schema import (
    EngineeringConsistencyError,
    Material,
    PrescribedDisplacementBC,
    SimulationIntent,
    StressQuantity,
)
from ir.schema import Region as IrRegion
from ir.validate import validate_intent
from ir.versioning import load_simulation_intent


FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = Path(__file__).resolve().parent / "golden" / "bracket_abaqus.py"
BRACKET = FIXTURES / "bracket.step"
SOURCE_HASH = hashlib.sha256(BRACKET.read_bytes()).hexdigest()

MINIMAL_INP = """*HEADING
Task 14 deterministic tetra
*NODE
10, 0, 0, 0
20, 1, 0, 0
30, 0, 1, 0
40, 0, 0, 1
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
*NSET, NSET=FIXED_NODES
10, 20
*NSET, NSET=LOAD_NODE
30
*NSET, NSET=ALL_NODES
10, 20, 30, 40
*ELSET, ELSET=ALL_VOLUME
100
"""


#: The explicit schema-version-2 engineering configuration.  None of it is
#: defaulted by the schema, so an export fixture must state it to be eligible.
EXPLICIT_ANALYSIS_DECISIONS = {
    "dimensionality": "3d_solid",
    "solver_target": "calculix",
    "coordinate_system": "global_cartesian",
}
EXPLICIT_MESH_SETTINGS = {
    "global_element_size_mm": 1.0,
    "element_type": "tetrahedral",
    "element_order": "first_order",
    "mesher": "gmsh",
    "mesher_preset": "gmsh_tet_v1",
    "target_size_original": {"value": 1.0, "unit": "mm"},
}
EXPLICIT_SOLVER_SETTINGS = {
    "target": "calculix",
    "analysis_profile": "linear_static_v1",
    "requested_results": ["displacement", "stress", "reaction_force"],
}


def canonical_payload(*, region_status: str = "confirmed", assumption_status: str = "accepted") -> dict:
    return {
        "schema_version": 2,
        "analysis": {
            "type": "static_structural",
            "units": {"length": "mm", "force": "N", "stress": "MPa"},
            **EXPLICIT_ANALYSIS_DECISIONS,
        },
        "mesh_settings": dict(EXPLICIT_MESH_SETTINGS),
        "solver_settings": dict(EXPLICIT_SOLVER_SETTINGS),
        "materials": [
            {
                "name": "steel",
                "model": "linear_elastic_isotropic",
                "E_MPa": 210000,
                "nu": 0.3,
            }
        ],
        "regions": [
            {
                "id": "bolt_holes",
                "entity_type": "cad_face",
                "entity_ids": [11, 12],
                "selection_method": "semantic_geometry_query",
                "confidence": 0.95,
                "source_instruction": "Fix the two bolt holes.",
                "status": region_status,
            },
            {
                "id": "upper_mounting_face",
                "entity_type": "cad_face",
                "entity_ids": [4],
                "selection_method": "user_confirmed",
                "confidence": 1.0,
                "source_instruction": "Apply a total downward force of 5 kN to the upper mounting face.",
                "status": region_status,
            },
        ],
        "bcs": [
            {
                "type": "fixed_displacement",
                "region_ref": "bolt_holes",
                "components": ["x", "y", "z"],
            }
        ],
        "loads": [
            {
                "type": "resultant_surface_force",
                "region_ref": "upper_mounting_face",
                "vector": [0, -5000, 0],
            }
        ],
        "assumptions": [
            {
                "text": "The 5 kN value was interpreted as total force, not pressure or force per node.",
                "criticality": "unit_critical",
                "status": assumption_status,
            },
            {
                "text": "Downward was interpreted as the negative Y direction of the model coordinate system.",
                "criticality": "unit_critical",
                "status": assumption_status,
            },
        ],
        "validation_status": "valid",
    }


def canonical_intent(**kwargs) -> SimulationIntent:
    return load_simulation_intent(canonical_payload(**kwargs), source="test fixture")


def resolved_cad_intent(**kwargs) -> SimulationIntent:
    """A structurally resolved v3 CAD setup, still unmapped for solver export."""

    payload = canonical_payload(**kwargs)
    payload["schema_version"] = 3
    for index, item in enumerate(payload["regions"], start=1):
        tags = list(item.pop("entity_ids"))
        item["cad_face_target"] = {
            "resolution": "resolved",
            "model_version_id": "fixture-model-version",
            "artifact_sha256": "a" * 64,
            "stable_identities": [f"gfi1:{index:064x}"],
            "source_face_tags": tags,
        }
    return SimulationIntent.model_validate(payload)


#: A clearly synthetic solver-face universe for the private renderer.  It is
#: a renderer-owned solver topology contract declared by the test, never
#: derived from any CAD face tag.
SYNTHETIC_SOLVER_FACE_UNIVERSE: tuple[int, ...] = tuple(range(1, 13))


def synthetic_verified_mapping(
    intent: SimulationIntent,
) -> dict[str, tuple[int, ...]]:
    """A synthetic test-only solver mapping for the private renderer boundary.

    This stands in for the verified CAD-to-solver correspondence R6 owns.  It
    is synthetic: nothing here proves that a source CAD tag corresponds to the
    solver face ID it is paired with.
    """

    return {
        region.id: tuple(region.cad_face_target.source_face_tags)
        for region in intent.regions
        if region.entity_type == "cad_face"
        and region.cad_face_target is not None
    }


def render_mapped(
    intent: SimulationIntent,
    cad_model: CadModelMetadata,
    *,
    solver_face_universe: tuple[int, ...] = SYNTHETIC_SOLVER_FACE_UNIVERSE,
):
    return _render_abaqus_py_with_verified_mapping(
        intent,
        cad_model,
        solver_face_ids_by_region=synthetic_verified_mapping(intent),
        solver_face_universe=solver_face_universe,
    )


@pytest.fixture
def cad_model() -> CadModelMetadata:
    return CadModelMetadata(
        source_path=BRACKET,
        source_name="bracket.step",
        source_sha256=SOURCE_HASH,
        source_cad_face_tags=tuple(range(1, 13)),
    )


@pytest.fixture
def mesh_model(tmp_path) -> MeshModelMetadata:
    path = tmp_path / "native_mesh.inp"
    path.write_text(MINIMAL_INP, encoding="utf-8", newline="\n")
    inventory = parse_inp(path)
    node_ids, element_blocks = _scan_inp_native_ids(path)
    return MeshModelMetadata(
        source_path=path,
        inventory=inventory,
        node_ids=tuple(node_ids),
        element_ids=tuple(value for block in element_blocks for value in block),
    )


def mesh_intent(*, regions: list[dict], bcs: list[dict] | None = None, loads: list[dict] | None = None) -> SimulationIntent:
    """A complete native-mesh setup.

    A setup with no constraint or no load is structurally incomplete and can
    never be export-eligible, so a caller that is exercising something else gets
    a minimal real constraint and load over the fixture's native sets rather
    than an empty list.
    """

    complete_regions = list(regions)
    complete_bcs = list(bcs or [])
    complete_loads = list(loads or [])
    if not complete_bcs:
        complete_regions.append(region("_preview_support", "node_set", ["FIXED_NODES"]))
        complete_bcs.append({
            "type": "fixed_displacement", "region_ref": "_preview_support",
            "components": ["x", "y", "z"],
        })
    if not complete_loads:
        complete_regions.append(region("_preview_load", "node_set", ["LOAD_NODE"]))
        complete_loads.append({
            "type": "concentrated_force", "region_ref": "_preview_load",
            "vector": [1.0, 0.0, 0.0],
        })
    return SimulationIntent.model_validate(
        {
            "schema_version": 2,
            "analysis": {
                "type": "static_structural",
                "units": {"length": "mm", "force": "N", "stress": "MPa"},
                **EXPLICIT_ANALYSIS_DECISIONS,
            },
            "mesh_settings": dict(EXPLICIT_MESH_SETTINGS),
            "solver_settings": dict(EXPLICIT_SOLVER_SETTINGS),
            "materials": [
                {
                    "name": "steel / test",
                    "model": "linear_elastic_isotropic",
                    "E_MPa": 210000,
                    "nu": 0.3,
                }
            ],
            "regions": complete_regions,
            "bcs": complete_bcs,
            "loads": complete_loads,
            "assumptions": [
                {
                    "text": "Native mesh IDs and mm-N-MPa units were confirmed.",
                    "criticality": "unit_critical",
                    "status": "accepted",
                }
            ],
            "validation_status": "valid",
        }
    )


def region(
    region_id: str,
    entity_type: str,
    entity_ids: list[int] | list[str],
    *,
    status: str = "confirmed",
) -> dict:
    return {
        "id": region_id,
        "entity_type": entity_type,
        "entity_ids": entity_ids,
        "selection_method": "user_confirmed",
        "confidence": 1.0,
        "source_instruction": f"Use {region_id}.",
        "status": status,
    }


def test_schema_v2_numeric_cad_migrates_to_legacy_and_cannot_export(cad_model):
    intent = canonical_intent()
    assert all(region.status == "proposed" for region in intent.regions)
    assert all(
        region.cad_face_target.resolution == "legacy_local_only"
        for region in intent.regions
    )
    assert [region.cad_face_target.source_face_tags for region in intent.regions] == [
        [11, 12],
        [4],
    ]
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(intent, cad_model)
    assert caught.value.code == "export_not_ready"


def test_resolved_stable_cad_fails_closed_without_solver_mapping(cad_model):
    intent = resolved_cad_intent()
    assert validate_intent(intent).export_eligible is True
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(intent, cad_model)


def test_internal_mapped_abaqus_renderer_matches_golden(cad_model):
    result = render_mapped(resolved_cad_intent(), cad_model)
    assert result.artifact_bytes == GOLDEN.read_bytes()
    assert result.checksum_sha256 == hashlib.sha256(
        GOLDEN.read_bytes()
    ).hexdigest()


def test_internal_mapped_abaqus_renderer_syntax_and_repeatability(cad_model):
    intent = resolved_cad_intent()
    first = render_mapped(intent, cad_model)
    second = render_mapped(intent, cad_model)
    ast.parse(first.artifact_text)
    assert first.artifact_bytes == second.artifact_bytes
    assert "\r" not in first.artifact_text


def test_internal_mapped_renderer_provenance_and_load_mapping(cad_model):
    text = render_mapped(resolved_cad_intent(), cad_model).artifact_text
    assert '# source_instruction: "Fix the two bolt holes."' in text
    assert (
        "# source_cad_face_tags (provenance only, not solver IDs): [11,12]" in text
    )
    assert "# mapped_solver_face_ids (explicitly supplied): [11,12]" in text
    assert "# selection_method: semantic_geometry_query" in text
    assert "# confidence: 0.95" in text
    assert "material.Elastic(table=((210000, 0.3),))" in text
    assert "model.StaticStep(name=STEP_NAME" in text
    assert "model.DisplacementBC" in text
    assert "_traction_magnitude = 5000 / _surface_area" in text
    assert "model.SurfaceTraction" in text
    assert "model.Pressure" not in text


def test_internal_renderer_uses_mapping_not_local_cad_tags(cad_model):
    intent = resolved_cad_intent()
    mapping = synthetic_verified_mapping(intent)
    mapping["upper_mounting_face"] = (1,)
    text = _render_abaqus_py_with_verified_mapping(
        intent,
        cad_model,
        solver_face_ids_by_region=mapping,
        solver_face_universe=SYNTHETIC_SOLVER_FACE_UNIVERSE,
    ).artifact_text
    section = text.split('# Region ID: "upper_mounting_face"', 1)[1]
    assert (
        "# source_cad_face_tags (provenance only, not solver IDs): [4]" in section
    )
    assert "# mapped_solver_face_ids (explicitly supplied): [1]" in section
    assert "_solver_face_ids = (1,)" in section
    assert "original_entity_ids" not in text


def test_abaqus_load_variants_cannot_bypass_missing_stable_mapping(cad_model):
    payload = canonical_payload()
    payload["schema_version"] = 3
    for index, region_payload in enumerate(payload["regions"], start=1):
        tags = list(region_payload.pop("entity_ids"))
        region_payload["cad_face_target"] = {
            "resolution": "resolved",
            "model_version_id": "fixture-model-version",
            "artifact_sha256": "a" * 64,
            "stable_identities": [f"gfi1:{index:064x}"],
            "source_face_tags": tags,
        }
    payload["materials"][0]["density_tonne_per_mm3"] = 7.85e-9
    payload["bcs"] = [
        {
            "type": "prescribed_displacement",
            "region_ref": "bolt_holes",
            "components": {"x": 1.25, "y": -2.0, "z": -0.0},
        }
    ]
    payload["loads"] = [
        {
            "type": "surface_traction",
            "region_ref": "upper_mounting_face",
            "vector": [3, 4, 0],
        },
        {
            "type": "pressure",
            "region_ref": "upper_mounting_face",
            "magnitude": 2.0,
        },
        {"type": "gravity", "region_ref": None, "vector": [0, 0, -9810]},
    ]
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)
    mapped = synthetic_verified_mapping(SimulationIntent.model_validate(payload))
    text = _render_abaqus_py_with_verified_mapping(
        SimulationIntent.model_validate(payload),
        cad_model,
        solver_face_ids_by_region=mapped,
        solver_face_universe=SYNTHETIC_SOLVER_FACE_UNIVERSE,
    ).artifact_text
    ast.parse(text)
    assert "u1=1.25, u2=-2, u3=0" in text
    assert "magnitude=5" in text
    assert "directionVector=((0.0, 0.0, 0.0), (0.6, 0.8, 0))" in text
    assert "model.Pressure" in text
    assert "positive magnitude acts into the surface" in text
    assert "model.Gravity" in text
    assert "comp1=0, comp2=0, comp3=-9810" in text
    assert text.count("material.Density(") == 1


def test_abaqus_gravity_without_density_is_blocked_before_artifact(cad_model):
    payload = resolved_cad_intent().model_dump(mode="json")
    payload["loads"] = [
        {"type": "gravity", "region_ref": None, "vector": [0, 0, -9810]}
    ]
    candidate = SimulationIntent.model_validate(payload)
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(candidate, cad_model)
    assert "material.density_required_for_gravity" in {
        issue.code for issue in caught.value.report.issues
    }


def test_abaqus_concentrated_force_fails_instead_of_inventing_point(cad_model):
    payload = resolved_cad_intent().model_dump(mode="json")
    payload["loads"] = [
        {
            "type": "concentrated_force",
            "region_ref": "upper_mounting_face",
            "vector": [0, -100, 0],
        }
    ]
    intent = SimulationIntent.model_validate(payload)
    # The load-to-region compatibility table now rejects a concentrated force on
    # a CAD face before the adapter is reached.
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(intent, cad_model)
    assert "load.region_entity_unsupported" in {
        issue.code for issue in caught.value.report.issues
    }
    with pytest.raises(UnsupportedLoadTypeError):
        abaqus_preflight(
            intent,
            cad_model,
            solver_face_ids_by_region=synthetic_verified_mapping(intent),
            solver_face_universe=SYNTHETIC_SOLVER_FACE_UNIVERSE,
        )


@pytest.mark.parametrize(
    ("region_status", "assumption_status"),
    [("proposed", "accepted"), ("rejected", "accepted"), ("confirmed", "pending"), ("confirmed", "rejected")],
)
def test_unready_intent_cannot_generate_abaqus(
    cad_model, region_status, assumption_status
):
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(
            canonical_intent(
                region_status=region_status, assumption_status=assumption_status
            ),
            cad_model,
        )
    assert caught.value.code == "export_not_ready"


def test_stale_client_validation_status_cannot_bypass_adapter_gate(cad_model):
    payload = resolved_cad_intent(region_status="proposed").model_dump(mode="json")
    payload["validation_status"] = "valid"
    with pytest.raises(ExportNotReadyError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)


def test_abaqus_rejects_dangling_face_and_missing_material_assignment(cad_model):
    payload = resolved_cad_intent().model_dump(mode="json")
    payload["regions"][0]["cad_face_target"]["source_face_tags"] = [999]
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)

    payload = resolved_cad_intent().model_dump(mode="json")
    payload["materials"].append({**payload["materials"][0], "name": "second"})
    multi_material = SimulationIntent.model_validate(payload)
    # Validation rejects more than one material for the single solid first ...
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(multi_material, cad_model)
    assert "material.count_unsupported" in {
        issue.code for issue in caught.value.report.issues
    }
    # ... and the renderer still refuses to guess a per-region assignment.
    with pytest.raises(MissingMaterialAssignmentError):
        abaqus_preflight(
            multi_material,
            cad_model,
            solver_face_ids_by_region=synthetic_verified_mapping(
                multi_material
            ),
            solver_face_universe=SYNTHETIC_SOLVER_FACE_UNIVERSE,
        )


def test_abaqus_rejects_changed_source_file(cad_model, tmp_path):
    changed = tmp_path / "bracket.step"
    changed.write_bytes(BRACKET.read_bytes() + b"\n")
    metadata = CadModelMetadata(
        source_path=changed,
        source_name=cad_model.source_name,
        source_sha256=cad_model.source_sha256,
        source_cad_face_tags=cad_model.source_cad_face_tags,
    )
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(resolved_cad_intent(), metadata)


def test_abaqus_unsafe_names_are_sanitized_deterministically(cad_model):
    payload = resolved_cad_intent().model_dump(mode="json")
    payload["regions"][0]["id"] = "../../bolt holes"
    payload["bcs"][0]["region_ref"] = "../../bolt holes"
    intent = SimulationIntent.model_validate(payload)
    first = render_mapped(intent, cad_model)
    second = render_mapped(intent, cad_model)
    assert first.artifact_bytes == second.artifact_bytes
    assert only_in_provenance(first.artifact_text)
    assert re.search(r"SET_BOLT_HOLES_[0-9A-F]{8}", first.artifact_text)


def only_in_provenance(text: str) -> bool:
    return all(
        line.startswith("#")
        for line in text.splitlines()
        if "../../bolt holes" in line
    )

def test_ccx_fixed_bc_reuses_native_nset(mesh_model):
    intent = mesh_intent(
        regions=[region("fixed", "node_set", ["FIXED_NODES"])],
        bcs=[
            {
                "type": "fixed_displacement",
                "region_ref": "fixed",
                "components": ["x", "y", "z"],
            }
        ],
    )
    text = export_ccx_inp(intent, mesh_model).artifact_text
    assert "Reuses preserved native NSET FIXED_NODES" in text
    assert "FIXED_NODES, 1, 1, 0" in text
    assert "FIXED_NODES, 2, 2, 0" in text
    assert "FIXED_NODES, 3, 3, 0" in text


def test_ccx_prescribed_displacement_and_generated_nset_order(mesh_model):
    intent = mesh_intent(
        regions=[region("moving nodes", "node_set", [40, 10, 30])],
        bcs=[
            {
                "type": "prescribed_displacement",
                "region_ref": "moving nodes",
                "components": {"z": -0.0, "x": 0.0, "y": 1.5},
            }
        ],
    )
    text = export_ccx_inp(intent, mesh_model).artifact_text
    assert "10, 30, 40" in text
    assert ", 1, 1, 0" in text
    assert ", 2, 2, 1.5" in text
    assert ", 3, 3, 0" in text


def test_ccx_concentrated_force_single_native_node(mesh_model):
    intent = mesh_intent(
        regions=[region("load-point", "node_set", [30])],
        loads=[
            {
                "type": "concentrated_force",
                "region_ref": "load-point",
                "vector": [100, -25, 0],
            }
        ],
    )
    text = export_ccx_inp(intent, mesh_model).artifact_text
    assert "30, 1, 100" in text
    assert "30, 2, -25" in text


def test_ccx_resultant_force_split_sums_exactly_and_repeats(mesh_model):
    intent = mesh_intent(
        regions=[region("loaded", "node_set", [40, 10, 20])],
        loads=[
            {
                "type": "resultant_surface_force",
                "region_ref": "loaded",
                "vector": [100, -5000, 0],
            }
        ],
    )
    first = export_ccx_inp(intent, mesh_model)
    second = export_ccx_inp(intent, mesh_model)
    assert first.artifact_bytes == second.artifact_bytes
    component_sums = {1: Decimal(0), 2: Decimal(0), 3: Decimal(0)}
    in_cload = False
    for line in first.artifact_text.splitlines():
        if line == "*CLOAD":
            in_cload = True
            continue
        if in_cload and line.startswith("*"):
            in_cload = False
        if in_cload and line and not line.startswith("**"):
            _, dof, value = [part.strip() for part in line.split(",")]
            component_sums[int(dof)] += Decimal(value)
    assert component_sums == {1: Decimal("100"), 2: Decimal("-5000"), 3: Decimal("0")}
    assert "This is not pressure" in first.artifact_text


def test_ccx_pressure_uses_explicit_element_face_topology(mesh_model):
    group = mesh_model.inventory.facet_groups[0]
    mappings = {
        facet_id: ElementFaceReference(element_id=100, face_label="S1")
        for facet_id in group.facet_ids
    }
    mapped = MeshModelMetadata(
        source_path=mesh_model.source_path,
        inventory=mesh_model.inventory,
        node_ids=mesh_model.node_ids,
        element_ids=mesh_model.element_ids,
        element_face_by_facet=mappings,
    )
    intent = mesh_intent(
        regions=[region("pressure-face", "mesh_face", [group.id])],
        loads=[
            {
                "type": "pressure",
                "region_ref": "pressure-face",
                "magnitude": 2.0,
            }
        ],
    )
    text = export_ccx_inp(intent, mapped).artifact_text
    assert "100, P1, 2" in text
    assert "positive magnitude acts into the surface" in text


def test_ccx_pressure_without_topology_fails_before_artifact(mesh_model):
    group_id = mesh_model.inventory.facet_groups[0].id
    intent = mesh_intent(
        regions=[region("pressure-face", "mesh_face", [group_id])],
        loads=[
            {"type": "pressure", "region_ref": "pressure-face", "magnitude": 2.0}
        ],
    )
    with pytest.raises(MissingMeshTopologyError) as caught:
        export_ccx_inp(intent, mesh_model)
    assert caught.value.code == "missing_mesh_topology"


def test_ccx_gravity_uses_validated_magnitude_and_direction(mesh_model):
    intent = mesh_intent(
        regions=[region("volume", "element_set", ["ALL_VOLUME"])],
        loads=[
            {"type": "gravity", "region_ref": "volume", "vector": [0, 0, -9810]}
        ],
    )
    material = intent.materials[0].model_copy(
        update={"density_tonne_per_mm3": 7.85e-9}, deep=True
    )
    intent = intent.model_copy(update={"materials": [material]}, deep=True)
    text = export_ccx_inp(intent, mesh_model).artifact_text
    assert "ALL_VOLUME, GRAV, 9810, 0, 0, -1" in text
    assert "Reuses preserved native ELSET ALL_VOLUME" in text
    assert "*DENSITY\n7.85e-09" in text


def test_ccx_unsupported_entity_and_load_types_fail_explicitly(mesh_model):
    cad_region = region("cad", "cad_face", [1])
    cad_region["cad_face_target"] = {
        "resolution": "resolved",
        "model_version_id": "fixture-model-version",
        "artifact_sha256": "a" * 64,
        "stable_identities": ["gfi1:" + "b" * 64],
        "source_face_tags": [1],
    }
    cad_region.pop("entity_ids")
    cad = mesh_intent(regions=[cad_region])
    with pytest.raises(UnsupportedEntityTypeError):
        export_ccx_inp(cad, mesh_model)

    # A traction on a node set is not a surface target at all: the compatibility
    # table blocks it before any adapter runs.
    node_traction = mesh_intent(
        regions=[region("nodes", "node_set", [10])],
        loads=[
            {"type": "surface_traction", "region_ref": "nodes", "vector": [1, 0, 0]}
        ],
    )
    with pytest.raises(ExportNotReadyError) as caught:
        export_ccx_inp(node_traction, mesh_model)
    assert "load.region_entity_unsupported" in {
        issue.code for issue in caught.value.report.issues
    }

    # On a real surface region the adapter still refuses to approximate vector
    # traction rather than mapping it to something else.
    group_id = mesh_model.inventory.facet_groups[0].id
    traction = mesh_intent(
        regions=[region("surface", "mesh_face", [group_id])],
        loads=[
            {"type": "surface_traction", "region_ref": "surface", "vector": [1, 0, 0]}
        ],
    )
    with pytest.raises(UnsupportedLoadTypeError):
        export_ccx_inp(traction, mesh_model)


def test_ccx_dangling_native_id_cannot_generate(mesh_model):
    intent = mesh_intent(regions=[region("dangling", "node_set", [999])])
    with pytest.raises(InvalidRegionReferenceError):
        export_ccx_inp(intent, mesh_model)


def test_empty_region_cannot_generate_even_when_constructed_below_schema(cad_model):
    intent = canonical_intent()
    empty = IrRegion.model_construct(
        id="bolt_holes",
        entity_type="cad_face",
        entity_ids=[],
        selection_method="semantic_geometry_query",
        confidence=0.95,
        source_instruction="Fix the two bolt holes.",
        status="confirmed",
    )
    candidate = intent.model_copy(update={"regions": [empty, intent.regions[1]]}, deep=True)
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        export_abaqus_py(candidate, cad_model)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


def upload_bytes(app, filename: str, content: bytes) -> str:
    response = request(
        app,
        "POST",
        "/models",
        content=content,
        headers={"X-Filename": filename, "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_ready_session(app, model_id: str, payload: dict) -> None:
    draft = copy.deepcopy(payload)
    for item in draft["regions"]:
        item["status"] = "proposed"
    for item in draft["assumptions"]:
        item["status"] = "pending"
    draft["validation_status"] = "valid"  # deliberate stale client claim
    saved = request(app, "PUT", f"/session/{model_id}/intent", json=draft)
    assert saved.status_code == 200, saved.text
    for item in draft["regions"]:
        confirmed = request(
            app,
            "POST",
            f"/session/{model_id}/confirm_region",
            json={"region_id": item["id"]},
        )
        assert confirmed.status_code == 200, confirmed.text
    state = request(app, "GET", f"/session/{model_id}/intent").json()
    for assumption in state["intent"]["assumptions"]:
        accepted = request(
            app,
            "POST",
            f"/session/{model_id}/assumptions/{assumption['id']}/accept",
        )
        assert accepted.status_code == 200, accepted.text


def test_successful_endpoint_returns_safe_attachment_and_media_type(tmp_path):
    app = create_app(tmp_path / "models")
    model_id = upload_bytes(app, "endpoint.inp", MINIMAL_INP.encode())
    payload = mesh_intent(
        regions=[region("fixed", "node_set", ["FIXED_NODES"])],
        bcs=[
            {
                "type": "fixed_displacement",
                "region_ref": "fixed",
                "components": ["x", "y", "z"],
            }
        ],
    ).model_dump(mode="json")
    make_ready_session(app, model_id, payload)
    response = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "ccx_inp"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == 'attachment; filename="endpoint_ccx.inp"'
    assert response.headers["x-solver-executed"] == "false"
    assert re.fullmatch(r"[0-9a-f]{64}", response.headers["x-artifact-sha256"])
    assert "C:\\" not in response.text


def test_numeric_only_step_endpoint_cannot_confirm_or_export(tmp_path):
    app = create_app(tmp_path / "models")
    model_id = upload_bytes(app, "bracket.step", BRACKET.read_bytes())
    payload = canonical_payload()
    payload["schema_version"] = 3
    for item in payload["regions"]:
        tags = item.pop("entity_ids")
        item["cad_face_target"] = {
            "resolution": "unresolved",
            "source_face_tags": tags,
        }
        item["status"] = "proposed"
    for item in payload["assumptions"]:
        item["status"] = "pending"
    saved = request(app, "PUT", f"/session/{model_id}/intent", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["export_eligible"] is False
    for item in payload["regions"]:
        confirmed = request(
            app,
            "POST",
            f"/session/{model_id}/confirm_region",
            json={"region_id": item["id"]},
        )
        assert confirmed.status_code == 409
    response = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "abaqus_py"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["export_eligible"] is False
    assert response.json()["code"] == "export_not_ready"
    assert "*SURFACE" not in response.text
    assert "part.faces[" not in response.text


def test_blocked_endpoint_returns_409_without_artifact_and_stale_status_fails(tmp_path):
    app = create_app(tmp_path / "models")
    model_id = upload_bytes(app, "blocked.inp", MINIMAL_INP.encode())
    payload = mesh_intent(
        regions=[region("fixed", "node_set", ["FIXED_NODES"], status="proposed")],
        bcs=[
            {
                "type": "fixed_displacement",
                "region_ref": "fixed",
                "components": ["x", "y", "z"],
            }
        ],
        loads=[{
            "type": "concentrated_force",
            "region_ref": "fixed",
            "vector": [1.0, 0.0, 0.0],
        }],
    ).model_dump(mode="json")
    for assumption in payload["assumptions"]:
        assumption["status"] = "pending"
    payload["validation_status"] = "valid"
    saved = request(app, "PUT", f"/session/{model_id}/intent", json=payload)
    assert saved.status_code == 200
    response = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "ccx_inp"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "export_not_ready"
    assert response.headers["content-type"].startswith("application/json")
    assert "*STEP" not in response.text
    assert any(issue["code"] == "export.confirmation_gate_blocked" for issue in response.json()["blocking_issues"])


def test_endpoint_adapter_errors_and_task13_gate_semantics_remain(tmp_path):
    app = create_app(tmp_path / "models")
    model_id = upload_bytes(app, "adapter.inp", MINIMAL_INP.encode())
    payload = mesh_intent(regions=[region("nodes", "node_set", [10])]).model_dump(
        mode="json"
    )
    make_ready_session(app, model_id, payload)
    gate = request(app, "POST", f"/session/{model_id}/export-gate")
    assert gate.status_code == 200
    assert "No artifact was generated" in gate.json()["message"]
    incompatible = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "abaqus_py"},
    )
    assert incompatible.status_code == 422
    assert incompatible.json()["code"] == "unsupported_model_type"
    unknown = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "mystery"},
    )
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "unknown_adapter"
    assert request(
        app,
        "POST",
        f"/session/{'0' * 64}/export",
        json={"adapter": "ccx_inp"},
    ).status_code == 404


def test_endpoint_internal_failure_is_structured_and_path_safe(tmp_path, monkeypatch):
    app = create_app(tmp_path / "models")
    model_id = upload_bytes(app, "failure.inp", MINIMAL_INP.encode())
    payload = mesh_intent(regions=[region("nodes", "node_set", [10])]).model_dump(
        mode="json"
    )
    make_ready_session(app, model_id, payload)

    def fail_generation(*args, **kwargs):
        raise RuntimeError(r"C:\private\solver\secret.inp")

    monkeypatch.setattr(server_module, "export_ccx_inp", fail_generation)
    response = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "ccx_inp"},
    )
    assert response.status_code == 500
    assert response.json() == {
        "code": "artifact_generation_failed",
        "message": "Artifact generation failed unexpectedly.",
        "adapter": "ccx_inp",
    }
    assert "private" not in response.text


def test_exports_make_no_network_or_openai_call(mesh_model, monkeypatch):
    def forbidden_socket(*args, **kwargs):
        raise AssertionError("network access is forbidden during export")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    intent = mesh_intent(regions=[region("nodes", "node_set", [10])])
    result = export_ccx_inp(intent, mesh_model)
    assert result.adapter_name == "ccx_inp"


# --------------------------------------------------------------------------
# R3.1 export compatibility
# --------------------------------------------------------------------------


def blocking_codes(error: ExportNotReadyError) -> set[str]:
    return {issue.code for issue in error.report.issues if issue.blocks_export}


def test_legacy_incomplete_setup_cannot_export(cad_model):
    """A migrated schema-version-1 setup is blocked, not silently completed."""

    legacy = json.loads(
        (Path(__file__).parents[1] / "examples" / "bracket_confirmed_export_ready.json")
        .read_text(encoding="utf-8")
    )
    assert legacy["schema_version"] == 1
    intent = load_simulation_intent(legacy, source="examples")
    assert intent.mesh_settings is None and intent.solver_settings is None
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(intent, cad_model)
    assert {
        "analysis.dimensionality_missing",
        "analysis.solver_target_missing",
        "analysis.coordinate_system_missing",
        "mesh.missing",
        "solver.missing",
    } <= blocking_codes(caught.value)


def test_contradictory_quantities_cannot_export(cad_model):
    """A contradiction constructed below the schema still blocks export."""

    intent = resolved_cad_intent()
    contradictory = Material.model_construct(
        name="steel",
        model="linear_elastic_isotropic",
        E_MPa=200_000.0,
        nu=0.3,
        density_tonne_per_mm3=None,
        youngs_modulus_original=StressQuantity(value=210.0, unit="GPa"),
        density_original=None,
    )
    candidate = intent.model_copy(update={"materials": [contradictory]}, deep=True)
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(candidate, cad_model)
    assert "material.youngs_modulus_normalization_mismatch" in blocking_codes(
        caught.value
    )


def test_internal_renderer_emits_supported_nonzero_displacement(cad_model):
    intent = resolved_cad_intent()
    unsupported = PrescribedDisplacementBC.model_construct(
        type="prescribed_displacement",
        region_ref="bolt_holes",
        components={"x": 0.0, "y": 0.0, "z": 2.5},
        components_original=None,
    )
    candidate = intent.model_copy(update={"bcs": [unsupported]}, deep=True)
    result = render_mapped(candidate, cad_model)
    assert "u1=0, u2=0, u3=2.5" in result.artifact_text


def test_invalid_load_target_cannot_export(cad_model):
    payload = resolved_cad_intent().model_dump(mode="json")
    payload["loads"] = [{
        "type": "gravity",
        "region_ref": "upper_mounting_face",  # a CAD surface, never valid for gravity
        "vector": [0.0, 0.0, -9810.0],
    }]
    payload["materials"][0]["density_tonne_per_mm3"] = 7.85e-9
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)
    assert "load.region_entity_unsupported" in blocking_codes(caught.value)


def test_stale_setup_remains_blocked_from_export():
    intent = resolved_cad_intent()
    fresh = validate_intent(intent)
    assert fresh.export_eligible is True
    stale = validate_intent(intent, source_is_stale=True)
    assert stale.readiness_status == "stale_source"
    assert stale.export_eligible is False
    assert "source.stale" in {issue.code for issue in stale.issues}


def test_explicitly_complete_setup_remains_export_eligible_with_normalized_fields(
    cad_model,
):
    """Engineering-ready stable CAD still requires a future solver mapping."""

    intent = resolved_cad_intent()
    report = validate_intent(intent)
    assert report.readiness_status == "ready"
    assert report.export_eligible is True
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(intent, cad_model)


def test_exporters_receive_consistent_normalized_load_fields(cad_model):
    payload = resolved_cad_intent().model_dump(mode="json")
    payload["loads"][0] = {
        "type": "resultant_surface_force",
        "region_ref": "upper_mounting_face",
        "vector": [0.0, -5000.0, 0.0],
        "original_force": {"value": 5.0, "unit": "kN"},
        "magnitude_N": 5000.0,
        "direction": [0.0, -1.0, 0.0],
    }
    intent = SimulationIntent.model_validate(payload)
    load = intent.loads[0]
    assert load.magnitude_N == 5000.0
    assert [load.magnitude_N * component for component in load.direction] == load.vector
    text = render_mapped(intent, cad_model).artifact_text
    assert "_traction_magnitude = 5000 / _surface_area" in text
    code_lines = [
        line
        for line in text.splitlines()
        if line and not line.lstrip().startswith("#")
    ]
    assert not any("kN" in line for line in code_lines)
    assert not any("original_force" in line for line in code_lines)
    bare_payload = intent.model_dump(mode="json")
    bare_payload["loads"][0].pop("original_force", None)
    bare_payload["loads"][0].pop("magnitude_N", None)
    bare_payload["loads"][0].pop("direction", None)
    bare = SimulationIntent.model_validate(bare_payload)
    assert render_mapped(bare, cad_model).artifact_bytes == render_mapped(
        intent, cad_model
    ).artifact_bytes


def test_production_exporters_do_not_hardcode_fixture_face_ids():
    abaqus_source = (Path(__file__).parents[1] / "export" / "abaqus_py.py").read_text(
        encoding="utf-8"
    )
    ccx_source = (Path(__file__).parents[1] / "export" / "ccx_inp.py").read_text(
        encoding="utf-8"
    )
    assert "face_11" not in abaqus_source
    assert "[11, 12]" not in abaqus_source
    assert "BOLT_HOLE" not in abaqus_source
    assert "BOLT_HOLE" not in ccx_source


def test_optional_ccx_parse_run(mesh_model, tmp_path):
    executable = shutil.which("ccx")
    if executable is None:
        pytest.skip("ccx executable is not installed; optional Task 14 parse-run unavailable")
    intent = mesh_intent(
        regions=[region("all-fixed", "node_set", ["ALL_NODES"])],
        bcs=[
            {
                "type": "fixed_displacement",
                "region_ref": "all-fixed",
                "components": ["x", "y", "z"],
            }
        ],
    )
    fragment = export_ccx_inp(intent, mesh_model).artifact_text
    job = tmp_path / "task14_ccx.inp"
    job.write_text(MINIMAL_INP + fragment, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [executable, "-i", job.stem],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, (completed.stdout + completed.stderr)[-2000:]


# --------------------------------------------------------------------------
# R4B2-AUDIT-04: source CAD face tags are provenance only.  The private
# renderer is driven exclusively by an explicit solver mapping and an
# explicit solver-face universe, both supplied independently of CAD tags.
# The fixture below is deliberately disjoint -- CAD tag 40 versus solver face
# ID 1 inside universe {1, 2, 3} -- so any surviving ordinal assumption
# ("OCC tag n -> part.faces[n - 1]") fails loudly instead of coincidentally
# working.
# --------------------------------------------------------------------------

DISJOINT_SOURCE_CAD_FACE_TAGS: tuple[int, ...] = (40, 41)
DISJOINT_SOLVER_FACE_UNIVERSE: tuple[int, ...] = (1, 2, 3)


def disjoint_cad_intent() -> SimulationIntent:
    """A resolved CAD setup whose source tags are far outside solver space."""

    payload = canonical_payload()
    payload["schema_version"] = 3
    for index, item in enumerate(payload["regions"], start=1):
        item.pop("entity_ids")
        item["cad_face_target"] = {
            "resolution": "resolved",
            "model_version_id": "fixture-model-version",
            "artifact_sha256": "a" * 64,
            "stable_identities": [f"gfi1:{index:064x}"],
            "source_face_tags": [DISJOINT_SOURCE_CAD_FACE_TAGS[index - 1]],
        }
    return SimulationIntent.model_validate(payload)


def disjoint_solver_mapping() -> dict[str, tuple[int, ...]]:
    """A synthetic solver mapping that shares no value with the CAD tags."""

    return {"bolt_holes": (1,), "upper_mounting_face": (2,)}


def render_disjoint(
    *,
    solver_face_ids_by_region: dict[str, tuple[int, ...]] | None = None,
    solver_face_universe: tuple[int, ...] = DISJOINT_SOLVER_FACE_UNIVERSE,
):
    return _render_abaqus_py_with_verified_mapping(
        disjoint_cad_intent(),
        CadModelMetadata(
            source_path=BRACKET,
            source_name="bracket.step",
            source_sha256=SOURCE_HASH,
            source_cad_face_tags=DISJOINT_SOURCE_CAD_FACE_TAGS,
        ),
        solver_face_ids_by_region=(
            disjoint_solver_mapping()
            if solver_face_ids_by_region is None
            else solver_face_ids_by_region
        ),
        solver_face_universe=solver_face_universe,
    )


def test_disjoint_renderer_selects_only_explicit_mapped_solver_ids():
    """(1) Selected solver entities use only the explicitly mapped IDs."""

    text = render_disjoint().artifact_text
    ast.parse(text)
    assert "_solver_face_ids = (1,)" in text
    assert "_solver_face_ids = (2,)" in text
    # No CAD tag ever becomes a solver face ID or a solver face index.
    assert "_solver_face_ids = (40,)" not in text
    assert "_solver_face_ids = (41,)" not in text
    for line in text.splitlines():
        if line.startswith("_solver_face_ids = "):
            values = ast.literal_eval(line.split(" = ", 1)[1])
            assert set(values) <= set(DISJOINT_SOLVER_FACE_UNIVERSE)
            assert not set(values) & set(DISJOINT_SOURCE_CAD_FACE_TAGS)


def test_disjoint_renderer_keeps_cad_tags_as_provenance_only():
    """(2) and (5) CAD tags appear only as labelled provenance comments."""

    text = render_disjoint().artifact_text
    assert (
        "# source_cad_face_tags (provenance only, not solver IDs): [40]" in text
    )
    assert (
        "# source_cad_face_tags (provenance only, not solver IDs): [41]" in text
    )
    assert "# mapped_solver_face_ids (explicitly supplied): [1]" in text
    assert "# mapped_solver_face_ids (explicitly supplied): [2]" in text
    assert "original_entity_ids" not in text
    # No executable line may mention a source CAD tag value at all: the tags
    # exist in the artifact only inside their labelled provenance comment.
    tag_tokens = re.compile(
        r"\b(" + "|".join(str(tag) for tag in DISJOINT_SOURCE_CAD_FACE_TAGS) + r")\b"
    )
    for line in text.splitlines():
        if line.startswith("#") or not tag_tokens.search(line):
            continue
        raise AssertionError(f"source CAD tag leaked into solver code: {line}")
    for line in text.splitlines():
        if tag_tokens.search(line) and line.startswith("#"):
            assert "source_cad_face_tags" in line, line


def test_disjoint_renderer_topology_check_ignores_cad_tag_magnitude():
    """(3) CAD tag values never affect solver topology validation."""

    text = render_disjoint().artifact_text
    assert "# The supplied solver-face universe is 1..3;" in text
    assert "if len(part.faces) < 3:" in text
    # The obsolete behaviour would have emitted max(source CAD tags) == 41.
    assert "if len(part.faces) < 41:" not in text
    assert "if len(part.faces) < 40:" not in text

    # Widening only the CAD tags cannot widen the solver topology check.
    wider = _render_abaqus_py_with_verified_mapping(
        disjoint_cad_intent(),
        CadModelMetadata(
            source_path=BRACKET,
            source_name="bracket.step",
            source_sha256=SOURCE_HASH,
            source_cad_face_tags=(40, 41, 900),
        ),
        solver_face_ids_by_region=disjoint_solver_mapping(),
        solver_face_universe=DISJOINT_SOLVER_FACE_UNIVERSE,
    )
    assert "if len(part.faces) < 3:" in wider.artifact_text
    assert "900" not in wider.artifact_text


def test_disjoint_renderer_emits_no_ordinal_mapping_claim():
    """(4) No artifact text claims ordinal source-tag mapping."""

    text = render_disjoint().artifact_text
    for obsolete in (
        "source_step_face_order",
        "OCC tag",
        "part.faces[n - 1]",
        "part.faces[n-1]",
        "original_entity_ids",
        "confirmed source face tags",
    ):
        assert obsolete not in text, obsolete
    assert (
        "# Region mapping: explicit solver face IDs supplied by the caller "
        "below the public export gate." in text
    )
    assert (
        "# Source CAD face tags are provenance only; they are not solver "
        "face IDs and imply no mapping." in text
    )
    warnings = render_disjoint().warnings
    assert not any("OCC tag" in warning for warning in warnings)
    assert any(
        "Source CAD face tags are retained as provenance" in warning
        for warning in warnings
    )


def test_disjoint_renderer_rejects_ids_outside_the_solver_universe():
    """(6) Mapped IDs outside the explicit solver universe are rejected."""

    with pytest.raises(InvalidRegionReferenceError):
        render_disjoint(
            solver_face_ids_by_region={
                "bolt_holes": (1,),
                "upper_mounting_face": (4,),
            }
        )
    # A source CAD tag is not a member of the solver universe just because it
    # exists on the CAD side.
    with pytest.raises(InvalidRegionReferenceError):
        render_disjoint(
            solver_face_ids_by_region={
                "bolt_holes": (40,),
                "upper_mounting_face": (2,),
            }
        )
    # A non-contiguous or empty solver universe is not a solver topology.
    with pytest.raises(MissingRegionMappingError):
        render_disjoint(solver_face_universe=(1, 3))
    with pytest.raises(MissingRegionMappingError):
        render_disjoint(solver_face_universe=())


def test_disjoint_renderer_accepts_small_solver_ids_under_large_cad_tags():
    """(7) Valid solver IDs are accepted even when CAD tags are far larger."""

    result = render_disjoint()
    assert result.adapter_name == "abaqus_py"
    assert result.suggested_filename == "bracket_abaqus.py"
    assert result.artifact_size > 0
    ast.parse(result.artifact_text)


def test_disjoint_renderer_is_deterministic_across_repeated_rendering():
    """(8) Repeated rendering of the disjoint fixture is byte-identical."""

    first = render_disjoint()
    second = render_disjoint()
    assert first.artifact_bytes == second.artifact_bytes
    assert first.checksum_sha256 == second.checksum_sha256
    assert "\r" not in first.artifact_text


def test_updated_bracket_golden_is_stable_and_truthful(cad_model):
    """(9) The checked-in golden matches the renderer and claims nothing false."""

    result = render_mapped(resolved_cad_intent(), cad_model)
    golden = GOLDEN.read_bytes()
    assert result.artifact_bytes == golden
    assert result.checksum_sha256 == hashlib.sha256(golden).hexdigest()
    golden_text = golden.decode("utf-8")
    for obsolete in (
        "source_step_face_order",
        "OCC tag",
        "part.faces[n - 1]",
        "original_entity_ids",
    ):
        assert obsolete not in golden_text, obsolete
    assert "# mapped_solver_face_ids (explicitly supplied): [11,12]" in golden_text
    assert (
        "# source_cad_face_tags (provenance only, not solver IDs): [11,12]"
        in golden_text
    )


def test_public_cad_export_stays_blocked_for_the_disjoint_fixture():
    """(10) The public gate refuses regardless of any private mapping."""

    model = CadModelMetadata(
        source_path=BRACKET,
        source_name="bracket.step",
        source_sha256=SOURCE_HASH,
        source_cad_face_tags=DISJOINT_SOURCE_CAD_FACE_TAGS,
    )
    intent = disjoint_cad_intent()
    assert validate_intent(intent).export_eligible is True
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(intent, model)


def test_cad_model_metadata_exposes_no_solver_topology_contract():
    """CAD provenance metadata cannot be mistaken for solver topology."""

    fields = set(CadModelMetadata.__dataclass_fields__)
    assert "source_cad_face_tags" in fields
    assert "face_ids" not in fields
    assert "mapping_strategy" not in fields
    source = (
        Path(__file__).parents[1] / "export" / "abaqus_py.py"
    ).read_text(encoding="utf-8")
    assert "max(model.face_ids)" not in source
    assert "source_step_face_order" not in source
    assert "original_entity_ids" not in source
