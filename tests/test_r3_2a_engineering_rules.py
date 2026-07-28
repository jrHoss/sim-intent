from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import LocalDataConfig
from app.runtime_mode import RuntimeMode
from app.server import create_app
from app.orchestration import propose_from_interpretation
from export.common import assess_artifact_capability
from geom.inventory import FaceInventory
from ir.schema import Assumption, SimulationIntent
from ir.validate import validate_intent
from llm.interpreter import (
    Interpreter,
    UnsupportedCapabilityError,
    UnsupportedMaterialInputError,
)
from tests.test_engineering_setup import payload, region
from tests.test_project_persistence import create_project, minimal_inp, request, upload


class NoCallTransport:
    def __init__(self) -> None:
        self.called = False

    def complete(self, request):
        self.called = True
        raise AssertionError("unsupported requests and material proposals are deterministic")


@pytest.fixture
def durable(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app):
        yield app


def setup_body(app, intent: dict, request_id: str = "create") -> tuple[str, dict]:
    project = create_project(app)
    uploaded = upload(app, project["id"], minimal_inp())
    body = {
        "model_id": uploaded["model_id"],
        "model_version_id": uploaded["model_version"]["id"],
        "request_id": request_id,
        "intent": intent,
    }
    return f"/api/v1/projects/{project['id']}/setups", body


def client_proposal(intent: dict) -> dict:
    candidate = copy.deepcopy(intent)
    for item in candidate["regions"]:
        item["status"] = "proposed"
    for item in candidate["assumptions"]:
        item["status"] = "pending"
    return candidate


def confirm_all_regions(app, current: dict, prefix: str) -> dict:
    for item in current["intent"]["regions"]:
        response = request(
            app,
            "POST",
            f"/api/v1/setups/{current['setup_id']}/regions/{item['id']}/confirm",
            json={
                "expected_revision": current["revision"],
                "request_id": f"{prefix}-{item['id']}",
            },
        )
        assert response.status_code == 201, response.text
        current = response.json()
    return current


@pytest.mark.parametrize(
    ("declared", "code"),
    [
        (None, "simulation_intent.schema_version_required"),
        ("2", "simulation_intent.schema_version_invalid"),
        (True, "simulation_intent.schema_version_invalid"),
        (0, "simulation_intent.schema_version_invalid"),
        (1, "simulation_intent.schema_version_unsupported_legacy"),
        (3, "simulation_intent.schema_version_unsupported_future"),
    ],
)
def test_durable_create_requires_the_current_explicit_version(durable, declared, code):
    candidate = payload()
    if declared is None:
        candidate.pop("schema_version")
    else:
        candidate["schema_version"] = declared
    path, body = setup_body(durable, candidate, f"version-{declared!r}")
    response = request(durable, "POST", path, json=body)
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == code
    assert request(durable, "GET", path).json() == []


def test_durable_revision_rejects_invalid_versions_without_extending_history(durable):
    path, body = setup_body(durable, client_proposal(payload()))
    created = request(durable, "POST", path, json=body)
    assert created.status_code == 201, created.text
    setup_id = created.json()["setup"]["id"]
    for declared, code in (
        (None, "simulation_intent.schema_version_required"),
        ("2", "simulation_intent.schema_version_invalid"),
        (1, "simulation_intent.schema_version_unsupported_legacy"),
        (3, "simulation_intent.schema_version_unsupported_future"),
    ):
        candidate = copy.deepcopy(created.json()["current"]["intent"])
        if declared is None:
            candidate.pop("schema_version")
        else:
            candidate["schema_version"] = declared
        rejected = request(
            durable,
            "POST",
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": 1,
                "request_id": f"invalid-{declared!r}",
                "intent": candidate,
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["errors"][0]["code"] == code
    assert len(request(durable, "GET", f"/api/v1/setups/{setup_id}/revisions").json()) == 1


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("value", [2.5, -2.5, 0.0, -0.0])
def test_prescribed_translations_preserve_sign_and_original_units(axis, value):
    candidate = payload()
    candidate["bcs"] = [{
        "type": "prescribed_displacement",
        "region_ref": "fixed",
        "components": {axis: value},
        "components_original": {axis: {"value": value / 1000.0, "unit": "m"}},
    }]
    intent = SimulationIntent.model_validate(candidate)
    assert intent.bcs[0].components[axis] == value
    assert intent.bcs[0].components_original[axis].unit == "m"


def test_rotational_prescribed_constraint_is_typed_unsupported_without_model_call():
    transport = NoCallTransport()
    with pytest.raises(UnsupportedCapabilityError) as caught:
        Interpreter(transport=transport).interpret(
            "Prescribe a rotational displacement RZ=0.1 on the top face", {}
        )
    assert caught.value.code == "constraint.rotation_unsupported"
    assert transport.called is False


def proposed_material_payload(*, status: str) -> dict:
    candidate = payload()
    decision = Assumption(
        text="Proposed isotropic material: E=210 GPa, nu=0.3.",
        criticality="unit_critical",
        status=status,
    )
    candidate["materials"] = [{
        "name": "numeric_proposal",
        "model": "linear_elastic_isotropic",
        "authority": "system_proposed",
        "proposal_assumption_ref": decision.id,
        "E_MPa": 210_000.0,
        "nu": 0.3,
        "youngs_modulus_original": {"value": 210.0, "unit": "GPa"},
    }]
    candidate["assumptions"] = [decision.model_dump(mode="json")]
    return candidate


def test_material_proposal_acceptance_and_rejection_create_successor_revisions(durable):
    path, body = setup_body(
        durable, client_proposal(proposed_material_payload(status="pending"))
    )
    created = request(durable, "POST", path, json=body)
    assert created.status_code == 201, created.text
    current = confirm_all_regions(durable, created.json()["current"], "confirm-accept")
    assert current["validation"]["readiness_status"] == "awaiting_assumption_acceptance", current["validation"]["issues"]
    assert current["intent"]["materials"][0]["authority"] == "system_proposed"
    assumption_id = current["intent"]["assumptions"][0]["id"]

    accepted = request(
        durable,
        "POST",
        f"/api/v1/setups/{current['setup_id']}/assumptions/{assumption_id}/accept",
        json={"expected_revision": current["revision"], "request_id": "accept-material"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["revision"] == current["revision"] + 1
    assert accepted.json()["validation"]["readiness_status"] == "ready"
    assert current["intent"]["assumptions"][0]["status"] == "pending"

    path, body = setup_body(
        durable,
        client_proposal(proposed_material_payload(status="pending")),
        "create-reject",
    )
    rejected_setup = confirm_all_regions(
        durable,
        request(durable, "POST", path, json=body).json()["current"],
        "confirm-reject",
    )
    assumption_id = rejected_setup["intent"]["assumptions"][0]["id"]
    rejected = request(
        durable,
        "POST",
        f"/api/v1/setups/{rejected_setup['setup_id']}/assumptions/{assumption_id}/reject",
        json={
            "expected_revision": rejected_setup["revision"],
            "request_id": "reject-material",
        },
    )
    assert rejected.status_code == 201
    assert rejected.json()["validation"]["readiness_status"] == "semantically_invalid"


def test_numeric_material_proposal_and_incomplete_named_material_are_safe():
    transport = NoCallTransport()
    interpreted = Interpreter(transport=transport).interpret(
        "Assign material alloy42 with Young's modulus E=70 gpa, "
        "Poisson's ratio nu=0.33, density=2700 kg/m3",
        {},
    )
    assert interpreted.material_proposal.youngs_modulus == "70 GPa"
    assert interpreted.material_proposal.poisson_ratio == 0.33
    assert transport.called is False
    proposal = propose_from_interpretation(
        instruction="Assign numeric material",
        interpretation=interpreted,
        inventory=FaceInventory(source_name="empty.step", file_sha256="0" * 64, faces=[]),
        cylinders={},
    ).intent
    assert proposal is not None
    assert proposal.materials[0].authority == "system_proposed"
    assert proposal.materials[0].E_MPa == 70_000.0
    assert proposal.materials[0].density_tonne_per_mm3 == pytest.approx(2.7e-9)
    assert proposal.assumptions[0].status == "pending"

    with pytest.raises(UnsupportedMaterialInputError) as caught:
        Interpreter(transport=transport).interpret("Use Inconel 718 material", {})
    assert caught.value.code == "material.properties_incomplete"


def test_duplicate_conflict_and_restraint_codes_are_deterministic():
    duplicate = payload()
    duplicate["bcs"].append(copy.deepcopy(duplicate["bcs"][0]))
    duplicate["loads"].append(copy.deepcopy(duplicate["loads"][0]))
    codes = [issue.code for issue in validate_intent(
        SimulationIntent.model_validate(duplicate)
    ).issues]
    assert "bc.duplicate" in codes
    assert "load.duplicate" in codes

    conflict = payload()
    conflict["bcs"] = [
        {"type": "prescribed_displacement", "region_ref": "fixed", "components": {"x": 1.0}},
        {"type": "prescribed_displacement", "region_ref": "fixed", "components": {"x": 2.0}},
        {"type": "fixed_displacement", "region_ref": "fixed", "components": ["x"]},
    ]
    report = validate_intent(SimulationIntent.model_validate(conflict))
    found = {issue.code for issue in report.issues}
    assert "bc.prescribed_displacement_conflict" in found
    assert "bc.fixed_prescribed_conflict" in found
    assert "constraint.rigid_body_translation_y" in found
    assert "constraint.rigid_body_translation_z" in found

    componentwise = payload()
    componentwise["bcs"] = [
        {"type": "fixed_displacement", "region_ref": "fixed", "components": [axis]}
        for axis in ("x", "y", "z")
    ]
    report = validate_intent(SimulationIntent.model_validate(componentwise))
    assert report.engineering_ready is True
    assert "constraint.rotational_restraint_unverified" in {
        issue.code for issue in report.issues
    }


def test_load_summary_never_invents_pressure_or_traction_resultants():
    candidate = payload()
    candidate["regions"].append(region("node", "node_set", [10]))
    candidate["loads"] = [
        {"type": "resultant_surface_force", "region_ref": "loaded", "vector": [10, -5, 2]},
        {"type": "concentrated_force", "region_ref": "node", "vector": [-3, 7, 1]},
        {"type": "pressure", "region_ref": "loaded", "magnitude": 4.0},
        {"type": "surface_traction", "region_ref": "loaded", "vector": [1, 0, 0]},
    ]
    summary = validate_intent(SimulationIntent.model_validate(candidate)).load_summary
    assert summary.resultant_surface_force_total_N == [10.0, -5.0, 2.0]
    assert summary.concentrated_force_total_N == [-3.0, 7.0, 1.0]
    assert summary.explicit_force_vector_sum_N == [7.0, 2.0, 3.0]
    assert [item.load_type for item in summary.unresolved_resultants] == [
        "pressure", "surface_traction"
    ]


@pytest.mark.parametrize(
    ("instruction", "code"),
    [
        ("Run a nonlinear analysis", "analysis.nonlinear_unsupported"),
        ("Add contact between the parts", "interaction.contact_unsupported"),
        ("Use an orthotropic material", "material.orthotropic_unsupported"),
        ("Analyze this as shells", "analysis.shell_unsupported"),
        ("Use a local coordinate system", "coordinate_system.local_unsupported"),
    ],
)
def test_explicit_unsupported_modes_have_stable_codes(instruction, code):
    transport = NoCallTransport()
    with pytest.raises(UnsupportedCapabilityError) as caught:
        Interpreter(transport=transport).interpret(instruction, {})
    assert caught.value.code == code
    assert transport.called is False


def test_engineering_readiness_is_separate_from_calculix_capability():
    candidate = payload()
    candidate["loads"] = [{
        "type": "surface_traction",
        "region_ref": "loaded",
        "vector": [1.0, 0.0, 0.0],
    }]
    intent = SimulationIntent.model_validate(candidate)
    report = validate_intent(intent)
    capability = assess_artifact_capability(intent, model_kind="inp")
    assert report.engineering_ready is True
    assert capability.capable is False
    assert capability.blocking_issue_codes == [
        "artifact.calculix.surface_traction_unsupported",
        "artifact.mapping_not_verified",
    ]
