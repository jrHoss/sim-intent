"""Task 14 deterministic export adapters and server artifact gate."""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
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
from export.abaqus_py import export_abaqus_py
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
from ir.schema import SimulationIntent
from ir.schema import Region as IrRegion


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


def canonical_payload(*, region_status: str = "confirmed", assumption_status: str = "accepted") -> dict:
    return {
        "analysis": {
            "type": "static_structural",
            "units": {"length": "mm", "force": "N", "stress": "MPa"},
        },
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
    return SimulationIntent.model_validate(canonical_payload(**kwargs))


@pytest.fixture
def cad_model() -> CadModelMetadata:
    return CadModelMetadata(
        source_path=BRACKET,
        source_name="bracket.step",
        source_sha256=SOURCE_HASH,
        face_ids=tuple(range(1, 13)),
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
    return SimulationIntent.model_validate(
        {
            "analysis": {
                "type": "static_structural",
                "units": {"length": "mm", "force": "N", "stress": "MPa"},
            },
            "materials": [
                {
                    "name": "steel / test",
                    "model": "linear_elastic_isotropic",
                    "E_MPa": 210000,
                    "nu": 0.3,
                }
            ],
            "regions": regions,
            "bcs": bcs or [],
            "loads": loads or [],
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


def test_confirmed_canonical_abaqus_matches_golden(cad_model):
    result = export_abaqus_py(canonical_intent(), cad_model)
    assert result.artifact_bytes == GOLDEN.read_bytes()
    assert result.checksum_sha256 == hashlib.sha256(GOLDEN.read_bytes()).hexdigest()


def test_abaqus_script_parses_and_is_byte_identical(cad_model):
    first = export_abaqus_py(canonical_intent(), cad_model)
    second = export_abaqus_py(canonical_intent(), cad_model)
    ast.parse(first.artifact_text)
    assert first.artifact_bytes == second.artifact_bytes
    assert "\r" not in first.artifact_text


def test_abaqus_provenance_material_bc_static_step_and_resultant_mapping(cad_model):
    text = export_abaqus_py(canonical_intent(), cad_model).artifact_text
    assert '# source_instruction: "Fix the two bolt holes."' in text
    assert "# original_entity_ids: [11,12]" in text
    assert "# selection_method: semantic_geometry_query" in text
    assert "# confidence: 0.95" in text
    assert "material.Elastic(table=((210000, 0.3),))" in text
    assert "model.StaticStep(name=STEP_NAME" in text
    assert "model.DisplacementBC" in text
    assert "_traction_magnitude = 5000 / _surface_area" in text
    assert "model.SurfaceTraction" in text
    assert "model.Pressure" not in text


def test_abaqus_prescribed_traction_pressure_and_gravity_mappings(cad_model):
    payload = canonical_payload()
    payload["materials"][0]["density_tonne_per_mm3"] = 7.85e-9
    payload["bcs"] = [
        {
            "type": "prescribed_displacement",
            "region_ref": "bolt_holes",
            "components": {"x": 1.5, "z": -0.25},
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
    text = export_abaqus_py(SimulationIntent.model_validate(payload), cad_model).artifact_text
    ast.parse(text)
    assert "u1=1.5, u2=UNSET, u3=-0.25" in text
    assert "magnitude=5" in text
    assert "directionVector=((0.0, 0.0, 0.0), (0.6, 0.8, 0))" in text
    assert "model.Pressure" in text
    assert "positive magnitude acts into the surface" in text
    assert "model.Gravity" in text
    assert "comp1=0, comp2=0, comp3=-9810" in text
    assert text.count("material.Density(") == 1
    assert "material.Density(table=((7.85e-09,),))" in text


def test_abaqus_gravity_without_density_is_blocked_before_artifact(cad_model):
    payload = canonical_payload()
    payload["loads"] = [
        {"type": "gravity", "region_ref": None, "vector": [0, 0, -9810]}
    ]
    with pytest.raises(ExportNotReadyError) as caught:
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)
    assert "material.density_required_for_gravity" in {
        issue.code for issue in caught.value.report.issues
    }


def test_abaqus_concentrated_force_fails_instead_of_inventing_point(cad_model):
    payload = canonical_payload()
    payload["loads"] = [
        {
            "type": "concentrated_force",
            "region_ref": "upper_mounting_face",
            "vector": [0, -100, 0],
        }
    ]
    with pytest.raises(UnsupportedLoadTypeError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)


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
    payload = canonical_payload(region_status="proposed")
    payload["validation_status"] = "valid"
    with pytest.raises(ExportNotReadyError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)


def test_abaqus_rejects_dangling_face_and_missing_material_assignment(cad_model):
    payload = canonical_payload()
    payload["regions"][0]["entity_ids"] = [999]
    with pytest.raises(InvalidRegionReferenceError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)

    payload = canonical_payload()
    payload["materials"].append({**payload["materials"][0], "name": "second"})
    with pytest.raises(MissingMaterialAssignmentError):
        export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)


def test_abaqus_rejects_changed_source_file(cad_model, tmp_path):
    changed = tmp_path / "bracket.step"
    changed.write_bytes(BRACKET.read_bytes() + b"\n")
    metadata = CadModelMetadata(
        source_path=changed,
        source_name=cad_model.source_name,
        source_sha256=cad_model.source_sha256,
        face_ids=cad_model.face_ids,
    )
    with pytest.raises(MissingRegionMappingError):
        export_abaqus_py(canonical_intent(), metadata)


def test_abaqus_unsafe_names_are_sanitized_deterministically(cad_model):
    payload = canonical_payload()
    payload["regions"][0]["id"] = "../../bolt holes"
    payload["bcs"][0]["region_ref"] = "../../bolt holes"
    first = export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)
    second = export_abaqus_py(SimulationIntent.model_validate(payload), cad_model)
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
                "components": {"z": -2.5, "x": 1.25},
            }
        ],
    )
    text = export_ccx_inp(intent, mesh_model).artifact_text
    assert "10, 30, 40" in text
    assert ", 1, 1, 1.25" in text
    assert ", 3, 3, -2.5" in text


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
    cad = mesh_intent(regions=[region("cad", "cad_face", [1])])
    with pytest.raises(UnsupportedEntityTypeError):
        export_ccx_inp(cad, mesh_model)

    traction = mesh_intent(
        regions=[region("nodes", "node_set", [10])],
        loads=[
            {"type": "surface_traction", "region_ref": "nodes", "vector": [1, 0, 0]}
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
    with pytest.raises(ExportNotReadyError):
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


def test_ready_step_endpoint_returns_golden_abaqus_artifact(tmp_path):
    app = create_app(tmp_path / "models")
    model_id = upload_bytes(app, "bracket.step", BRACKET.read_bytes())
    make_ready_session(app, model_id, canonical_payload())
    response = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "abaqus_py"},
    )
    assert response.status_code == 200, response.text
    assert response.content == GOLDEN.read_bytes()
    assert response.headers["content-type"].startswith("text/x-python")
    assert response.headers["content-disposition"] == 'attachment; filename="bracket_abaqus.py"'


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


def test_exports_make_no_network_or_openai_call(cad_model, monkeypatch):
    def forbidden_socket(*args, **kwargs):
        raise AssertionError("network access is forbidden during export")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    result = export_abaqus_py(canonical_intent(), cad_model)
    assert result.adapter_name == "abaqus_py"


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
