"""Task 13 deterministic validation, audit, and readiness-gate tests."""

from __future__ import annotations

import asyncio
import copy
import math

import httpx
import pytest

from app.server import create_app
from ir.schema import (
    Analysis,
    Assumption,
    GravityLoad,
    Material,
    PrescribedDisplacementBC,
    PressureLoad,
    Region,
    ResultantSurfaceForceLoad,
    SimulationIntent,
    Units,
)
from ir.validate import validate_intent


def valid_payload(*, region_status: str = "confirmed", assumption_status: str = "accepted") -> dict:
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
            }
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
                "region_ref": "bolt_holes",
                "vector": [0, -5000, 0],
            }
        ],
        "assumptions": [
            {
                "text": "The 5 kN value was converted to a total force in N.",
                "criticality": "unit_critical",
                "status": assumption_status,
            }
        ],
        "validation_status": "unvalidated",
    }


def intent(**kwargs) -> SimulationIntent:
    return SimulationIntent.model_validate(valid_payload(**kwargs))


def codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_valid_confirmed_intent_passes_and_is_export_eligible():
    report = validate_intent(intent())
    assert report.validation_status == "valid"
    assert report.export_eligible is True
    assert report.issues == []


def test_non_gravity_intent_does_not_require_density():
    candidate = intent()
    assert candidate.materials[0].density_tonne_per_mm3 is None
    assert "material.density_required_for_gravity" not in codes(
        validate_intent(candidate)
    )


def test_gravity_without_density_is_blocked():
    candidate = intent().model_copy(
        update={
            "loads": [
                GravityLoad(
                    type="gravity", region_ref=None, vector=[0.0, 0.0, -9810.0]
                )
            ]
        },
        deep=True,
    )
    report = validate_intent(candidate)
    assert "material.density_required_for_gravity" in codes(report)
    assert report.export_eligible is False


def test_gravity_with_positive_density_is_valid():
    candidate = intent()
    material = candidate.materials[0].model_copy(
        update={"density_tonne_per_mm3": 7.85e-9}, deep=True
    )
    candidate = candidate.model_copy(
        update={
            "materials": [material],
            "loads": [
                GravityLoad(
                    type="gravity", region_ref=None, vector=[0.0, 0.0, -9810.0]
                )
            ],
        },
        deep=True,
    )
    report = validate_intent(candidate)
    assert "material.density_required_for_gravity" not in codes(report)
    assert report.export_eligible is True


def test_proposed_region_is_valid_draft_but_blocks_export():
    report = validate_intent(intent(region_status="proposed"))
    assert report.validation_status == "valid"
    assert report.export_eligible is False
    assert "region.proposed" in codes(report)
    assert "export.confirmation_gate_blocked" in codes(report)


def test_rejected_referenced_region_is_invalid_and_blocks_export():
    report = validate_intent(intent(region_status="rejected"))
    assert report.validation_status == "invalid"
    assert report.export_eligible is False
    assert {"region.rejected", "bc.region_rejected", "load.region_rejected"} <= set(
        codes(report)
    )


def test_missing_region_reference_is_deterministic_and_blocks_export():
    candidate = intent()
    dangling = ResultantSurfaceForceLoad.model_construct(
        type="resultant_surface_force", region_ref="missing", vector=[0, -1, 0]
    )
    candidate = candidate.model_copy(update={"loads": [dangling]}, deep=True)
    report = validate_intent(candidate)
    assert "load.region_unresolved" in codes(report)
    assert report.export_eligible is False


def test_empty_region_entity_list_is_reported_and_blocks_export():
    candidate = intent()
    empty = Region.model_construct(
        id="bolt_holes",
        entity_type="cad_face",
        entity_ids=[],
        selection_method="semantic_geometry_query",
        confidence=0.95,
        source_instruction="Fix the two bolt holes.",
        status="confirmed",
    )
    candidate = candidate.model_copy(update={"regions": [empty]}, deep=True)
    report = validate_intent(candidate)
    assert {"region.entity_ids_empty", "bc.region_empty", "load.region_empty"} <= set(
        codes(report)
    )
    assert report.export_eligible is False


def test_nonfinite_region_confidence_is_reported():
    candidate = intent()
    region_data = candidate.regions[0].model_dump(mode="python")
    region_data["confidence"] = math.nan
    region = Region.model_construct(**region_data)
    report = validate_intent(
        candidate.model_copy(update={"regions": [region]}, deep=True)
    )
    assert "region.confidence_invalid" in codes(report)
    assert report.export_eligible is False


@pytest.mark.parametrize("vector", [[0, 0, 0], [0, math.inf, 0], [0, math.nan, 0]])
def test_zero_or_nonfinite_load_vectors_are_blocking(vector):
    payload = valid_payload()
    payload["loads"][0]["vector"] = vector
    candidate = SimulationIntent.model_construct(**payload)
    candidate.loads = [
        ResultantSurfaceForceLoad.model_construct(
            type="resultant_surface_force",
            region_ref="bolt_holes",
            vector=vector,
        )
    ]
    candidate.analysis = intent().analysis
    candidate.materials = intent().materials
    candidate.regions = intent().regions
    candidate.bcs = intent().bcs
    candidate.assumptions = intent().assumptions
    report = validate_intent(candidate)
    expected = "load.vector_zero" if vector == [0, 0, 0] else "load.vector_nonfinite"
    assert expected in codes(report)
    assert report.export_eligible is False


def test_zero_prescribed_displacement_pressure_and_gravity_are_blocking():
    base = intent()
    displacement = base.model_copy(
        update={
            "bcs": [
                PrescribedDisplacementBC.model_construct(
                    type="prescribed_displacement",
                    region_ref="bolt_holes",
                    components={"z": 0.0},
                )
            ]
        },
        deep=True,
    )
    pressure = base.model_copy(
        update={
            "loads": [
                PressureLoad.model_construct(
                    type="pressure", region_ref="bolt_holes", magnitude=0.0
                )
            ]
        },
        deep=True,
    )
    gravity = base.model_copy(
        update={
            "loads": [
                GravityLoad.model_construct(
                    type="gravity", region_ref=None, vector=[0.0, 0.0, 0.0]
                )
            ]
        },
        deep=True,
    )
    assert "bc.vector_zero" in codes(validate_intent(displacement))
    assert "load.magnitude_zero" in codes(validate_intent(pressure))
    assert "load.vector_zero" in codes(validate_intent(gravity))


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("E_MPa", 0.0, "material.youngs_modulus_invalid"),
        ("E_MPa", math.inf, "material.youngs_modulus_invalid"),
        ("nu", 0.5, "material.poisson_ratio_invalid"),
        ("nu", -1.0, "material.poisson_ratio_invalid"),
        ("nu", math.nan, "material.poisson_ratio_invalid"),
    ],
)
def test_invalid_material_fields_are_reported(field, value, expected):
    candidate = intent()
    material_data = candidate.materials[0].model_dump(mode="python")
    material_data[field] = value
    material = Material.model_construct(**material_data)
    candidate = candidate.model_copy(update={"materials": [material]}, deep=True)
    report = validate_intent(candidate)
    assert expected in codes(report)
    assert report.export_eligible is False


@pytest.mark.parametrize(
    ("units", "expected"),
    [
        (Units.model_construct(length="mm", force="N", stress=None), "units.incomplete"),
        (Units.model_construct(length="m", force="N", stress="MPa"), "units.unsupported"),
        (None, "units.missing"),
    ],
)
def test_missing_incomplete_or_unsupported_units_block_export(units, expected):
    candidate = intent()
    analysis = Analysis.model_construct(type="static_structural", units=units)
    candidate = candidate.model_copy(update={"analysis": analysis}, deep=True)
    report = validate_intent(candidate)
    assert expected in codes(report)
    assert report.export_eligible is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pending", "assumption.unit_critical_pending"),
        ("rejected", "assumption.unit_critical_rejected"),
    ],
)
def test_unaccepted_unit_critical_assumptions_block_export(status, expected):
    report = validate_intent(intent(assumption_status=status))
    assert expected in codes(report)
    assert report.export_eligible is False


def test_accepted_unit_critical_assumption_no_longer_blocks():
    assert validate_intent(intent()).export_eligible is True


def test_assumption_ids_are_content_derived_and_stable():
    first = Assumption(
        text="The force was converted to N.",
        criticality="unit_critical",
        status="pending",
    )
    second = Assumption.model_validate(first.model_dump(mode="python"))
    different = Assumption(
        text="The pressure was converted to MPa.",
        criticality="unit_critical",
        status="pending",
    )
    assert first.id == second.id
    assert first.id != different.id
    assert first.id.startswith("assumption_")


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_noncritical_assumption_is_reported_but_does_not_block(status):
    payload = valid_payload()
    payload["assumptions"][0]["criticality"] = "noncritical"
    payload["assumptions"][0]["status"] = status
    report = validate_intent(SimulationIntent.model_validate(payload))
    assert f"assumption.noncritical_{status}" in codes(report)
    assert report.validation_status == "valid"
    assert report.export_eligible is True


def test_client_validation_status_is_ignored_and_report_is_stable():
    stale = valid_payload(region_status="proposed", assumption_status="pending")
    stale["validation_status"] = "valid"
    candidate = SimulationIntent.model_validate(stale)
    first = validate_intent(candidate).model_dump_json()
    candidate.validation_status = "invalid"
    second = validate_intent(candidate).model_dump_json()
    assert first == second
    issue_tuples = [
        (issue.severity, issue.code, issue.object_id or "")
        for issue in validate_intent(candidate).issues
    ]
    assert issue_tuples == sorted(
        issue_tuples,
        key=lambda item: ({"error": 0, "warning": 1}[item[0]], item[1], item[2]),
    )


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


def minimal_inp(label: str) -> bytes:
    return f"""*HEADING
{label}
*NODE
10, 0, 0, 0
20, 1, 0, 0
30, 0, 1, 0
40, 0, 0, 1
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
""".encode()


def upload(app, name: str) -> str:
    response = request(
        app,
        "POST",
        "/models",
        content=minimal_inp(name),
        headers={"X-Filename": name, "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def api(tmp_path):
    return create_app(tmp_path / "models")


def save(api, model_id: str, payload: dict) -> httpx.Response:
    return request(api, "PUT", f"/session/{model_id}/intent", json=payload)


def transition_region(api, model_id: str, action: str) -> httpx.Response:
    return request(
        api,
        "POST",
        f"/session/{model_id}/{action}_region",
        json={"region_id": "bolt_holes"},
    )


def assumption_id(state: dict) -> str:
    return state["intent"]["assumptions"][0]["id"]


def test_audit_and_direct_export_gate_block_proposed_region(api):
    model_id = upload(api, "proposed.inp")
    payload = valid_payload(region_status="proposed", assumption_status="pending")
    payload["validation_status"] = "valid"
    saved = save(api, model_id, payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["intent"]["validation_status"] == "invalid"

    audit = request(api, "GET", f"/session/{model_id}/audit")
    assert audit.status_code == 200
    body = audit.json()
    assert body["regions"][0]["entity_ids"] == [11, 12]
    assert body["regions"][0]["source_instruction"] == "Fix the two bolt holes."
    assert body["regions"][0]["selection_method"] == "semantic_geometry_query"
    assert body["regions"][0]["status"] == "proposed"
    assert body["regions"][0]["boundary_conditions"][0]["type"] == "fixed_displacement"
    assert body["regions"][0]["loads"][0]["type"] == "resultant_surface_force"
    assert body["assumptions"][0]["criticality"] == "unit_critical"
    assert body["export_eligible"] is False

    gate = request(api, "POST", f"/session/{model_id}/export-gate")
    assert gate.status_code == 409
    assert gate.json()["status"] == "blocked"
    assert gate.json()["export_eligible"] is False
    assert any(
        issue["code"] == "export.confirmation_gate_blocked"
        for issue in gate.json()["blocking_issues"]
    )


def test_region_and_assumption_transitions_recompute_readiness(api):
    model_id = upload(api, "transitions.inp")
    saved = save(
        api,
        model_id,
        valid_payload(region_status="proposed", assumption_status="pending"),
    ).json()
    aid = assumption_id(saved)

    confirmed = transition_region(api, model_id, "confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["export_eligible"] is False
    accepted = request(
        api, "POST", f"/session/{model_id}/assumptions/{aid}/accept"
    )
    assert accepted.status_code == 200
    assert accepted.json()["intent"]["assumptions"][0]["status"] == "accepted"
    assert accepted.json()["intent"]["validation_status"] == "valid"
    assert accepted.json()["export_eligible"] is True

    audit = request(api, "GET", f"/session/{model_id}/audit").json()
    assert audit["export_eligible"] is True
    assert audit["blocking_reasons"] == []
    gate = request(api, "POST", f"/session/{model_id}/export-gate")
    assert gate.status_code == 200
    assert gate.json()["status"] == "ready"
    assert "No artifact was generated" in gate.json()["message"]


def test_rejected_region_and_rejected_critical_assumption_stay_blocked(api):
    model_id = upload(api, "rejected.inp")
    saved = save(
        api,
        model_id,
        valid_payload(region_status="proposed", assumption_status="pending"),
    ).json()
    aid = assumption_id(saved)
    assert transition_region(api, model_id, "reject").status_code == 200
    assert request(
        api, "POST", f"/session/{model_id}/assumptions/{aid}/reject"
    ).status_code == 200
    gate = request(api, "POST", f"/session/{model_id}/export-gate")
    assert gate.status_code == 409
    gate_codes = {issue["code"] for issue in gate.json()["blocking_issues"]}
    assert "region.rejected" in gate_codes
    assert "assumption.unit_critical_rejected" in gate_codes


def test_assumption_errors_and_client_bypass_attempts_are_clean(api):
    model_id = upload(api, "assumption-errors.inp")
    payload = valid_payload(region_status="proposed", assumption_status="pending")
    saved = save(api, model_id, payload).json()
    aid = assumption_id(saved)

    missing = request(
        api, "POST", f"/session/{model_id}/assumptions/assumption_missing/accept"
    )
    assert missing.status_code == 404

    bypass = copy.deepcopy(saved["intent"])
    bypass["assumptions"][0]["status"] = "accepted"
    bypass["validation_status"] = "valid"
    response = save(api, model_id, bypass)
    assert response.status_code == 409
    current = request(api, "GET", f"/session/{model_id}/intent").json()
    assert current["intent"]["assumptions"][0]["status"] == "pending"

    accepted = request(api, "POST", f"/session/{model_id}/assumptions/{aid}/accept")
    assert accepted.status_code == 200
    repeated = request(api, "POST", f"/session/{model_id}/assumptions/{aid}/reject")
    assert repeated.status_code == 409


def test_validation_and_assumption_changes_are_isolated_between_models(api):
    first = upload(api, "first-validation.inp")
    second = upload(api, "second-validation.inp")
    first_state = save(
        api, first, valid_payload(region_status="proposed", assumption_status="pending")
    ).json()
    second_state = save(
        api, second, valid_payload(region_status="proposed", assumption_status="pending")
    ).json()
    assert assumption_id(first_state) == assumption_id(second_state)

    transition_region(api, first, "confirm")
    request(
        api,
        "POST",
        f"/session/{first}/assumptions/{assumption_id(first_state)}/accept",
    )
    first_audit = request(api, "GET", f"/session/{first}/audit").json()
    second_audit = request(api, "GET", f"/session/{second}/audit").json()
    assert first_audit["export_eligible"] is True
    assert second_audit["export_eligible"] is False
    assert second_audit["regions"][0]["status"] == "proposed"
    assert second_audit["assumptions"][0]["status"] == "pending"


def test_unknown_model_and_missing_draft_are_clean(api):
    unknown = "0" * 64
    assert request(api, "GET", f"/session/{unknown}/audit").status_code == 404
    model_id = upload(api, "empty-session.inp")
    assert request(api, "GET", f"/session/{model_id}/audit").status_code == 409
    assert request(api, "POST", f"/session/{model_id}/export-gate").status_code == 409


def test_audit_frontend_is_served_and_uses_backend_source_of_truth(api):
    index = request(api, "GET", "/")
    javascript = request(api, "GET", "/static/audit.js")
    assert index.status_code == javascript.status_code == 200
    assert 'id="audit-export-button"' in index.text
    assert 'id="audit-region-list"' in index.text
    assert "/session/${modelId}/audit" in javascript.text
    assert "/session/${modelId}/export-gate" in javascript.text
    assert "exportButton.disabled = !audit.export_eligible" in javascript.text
    assert "criticality" in javascript.text
