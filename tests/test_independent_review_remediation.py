from __future__ import annotations

import copy
import itertools
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import LocalDataConfig
from app.persistence import canonical_intent
from app.runtime_mode import RuntimeMode
from app.server import create_app
from export.ccx_inp import export_ccx_inp
from export.common import (
    ElementFaceReference,
    InvalidRegionReferenceError,
    MeshModelMetadata,
    assess_artifact_capability,
)
from geom.meshes import _scan_inp_native_ids, load_mesh
from ir.schema import Assumption, SimulationIntent
from ir.validate import summarize_loads, validate_intent
from llm.interpreter import (
    FaceInventorySummary,
    Interpreter,
    UnsupportedCapabilityError,
    UnsupportedMaterialInputError,
)
from tests.test_engineering_setup import payload, region
from tests.test_project_persistence import create_project, minimal_inp, request, upload
from tests.test_r3_2a_engineering_rules import (
    client_proposal,
    confirm_all_regions,
    proposed_material_payload,
    setup_body,
)


NATIVE_INP = b"""*HEADING
native capability fixture
*NODE
10, 0, 0, 0
20, 1, 0, 0
30, 0, 1, 0
40, 0, 0, 1
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
*NSET, NSET=FIXED
10
*NSET, NSET=LOAD_NODE
20
*NSET, NSET=SURFACE_NODES
10, 20, 30
"""


def native_metadata(path: Path, *, with_topology: bool = False) -> MeshModelMetadata:
    inventory = load_mesh(path)
    node_ids, blocks = _scan_inp_native_ids(path)
    topology = {}
    if with_topology:
        labels = ("S1", "S2", "S3", "S4")
        topology = {
            facet.id: ElementFaceReference(
                element_id=100, face_label=labels[index]
            )
            for index, facet in enumerate(inventory.facets)
        }
    return MeshModelMetadata(
        source_path=path,
        inventory=inventory,
        node_ids=tuple(node_ids),
        element_ids=tuple(item for block in blocks for item in block),
        element_face_by_facet=topology,
    )


def native_intent() -> dict:
    candidate = payload()
    candidate["regions"] = [
        region("fixed", "node_set", ["FIXED"]),
        region("loaded", "node_set", ["LOAD_NODE"]),
    ]
    candidate["loads"] = [
        {
            "type": "concentrated_force",
            "region_ref": "loaded",
            "vector": [0.0, -5000.0, 0.0],
        }
    ]
    return candidate


def test_capability_uses_exporter_native_resolution_and_blocks_direct_bypass(tmp_path):
    source = tmp_path / "native.inp"
    source.write_bytes(NATIVE_INP)
    metadata = native_metadata(source)

    valid = SimulationIntent.model_validate(native_intent())
    valid_capability = assess_artifact_capability(
        valid, model_kind="inp", model=metadata
    )
    assert valid_capability.supported is True
    assert valid_capability.blocking_issue_codes == []
    assert export_ccx_inp(valid, metadata).adapter_name == "ccx_inp"

    for entity_type, missing_name, load in (
        (
            "node_set",
            "UNKNOWN_NSET",
            {
                "type": "concentrated_force",
                "region_ref": "loaded",
                "vector": [1.0, 0.0, 0.0],
            },
        ),
        (
            "element_set",
            "UNKNOWN_ELSET",
            {
                "type": "gravity",
                "region_ref": "loaded",
                "vector": [0.0, 0.0, -9810.0],
            },
        ),
    ):
        body = native_intent()
        body["regions"][1] = region("loaded", entity_type, [missing_name])
        body["loads"] = [load]
        missing = SimulationIntent.model_validate(body)
        report = validate_intent(missing)
        capability = assess_artifact_capability(
            missing, model_kind="inp", model=metadata
        )
        assert report.engineering_ready is True
        assert capability.supported is False
        assert "artifact.native_region_missing" in capability.blocking_issue_codes
        with pytest.raises(InvalidRegionReferenceError) as caught:
            export_ccx_inp(missing, metadata)
        assert caught.value.code == "artifact.native_region_missing"


def test_step_and_unverified_inp_mapping_have_stable_capability_blockers():
    intent = SimulationIntent.model_validate(native_intent())
    step = assess_artifact_capability(intent, model_kind="step")
    assert step.supported is False
    assert step.blocking_issue_codes == [
        "artifact.mapping_not_verified",
        "artifact.step_meshing_required",
    ]
    unverified = assess_artifact_capability(intent, model_kind="inp")
    assert unverified.supported is False
    assert unverified.blocking_issue_codes == ["artifact.mapping_not_verified"]


@pytest.mark.parametrize(
    ("bc", "load", "loaded_region"),
    [
        (
            {
                "type": "fixed_displacement",
                "region_ref": "fixed",
                "components": ["z", "x", "y"],
            },
            {
                "type": "resultant_surface_force",
                "region_ref": "loaded",
                "vector": [1.0, 2.0, 3.0],
            },
            region("loaded", "node_set", ["SURFACE_NODES"]),
        ),
        (
            {
                "type": "prescribed_displacement",
                "region_ref": "fixed",
                "components": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
            {
                "type": "concentrated_force",
                "region_ref": "loaded",
                "vector": [1.0, 0.0, 0.0],
            },
            region("loaded", "node_set", ["LOAD_NODE"]),
        ),
        (
            {
                "type": "fixed_displacement",
                "region_ref": "fixed",
                "components": ["x", "y", "z"],
            },
            {
                "type": "gravity",
                "region_ref": "loaded",
                "vector": [0.0, 0.0, -9810.0],
            },
            region("loaded", "element_set", ["SOLID"]),
        ),
    ],
)
def test_every_native_bc_and_load_adapter_path_is_preflighted(
    tmp_path, bc, load, loaded_region
):
    source = tmp_path / "native.inp"
    source.write_bytes(NATIVE_INP)
    body = native_intent()
    body["bcs"] = [bc]
    body["regions"][1] = loaded_region
    body["loads"] = [load]
    capability = assess_artifact_capability(
        SimulationIntent.model_validate(body),
        model_kind="inp",
        model=native_metadata(source),
    )
    assert capability.supported is True, capability.blocking_issue_codes


def test_pressure_mapping_and_surface_traction_are_distinguished(tmp_path):
    source = tmp_path / "native.inp"
    source.write_bytes(NATIVE_INP)
    inventory = load_mesh(source)
    surface = region("loaded", "mesh_face", [inventory.facet_groups[0].id])

    pressure_body = native_intent()
    pressure_body["regions"][1] = surface
    pressure_body["loads"] = [
        {"type": "pressure", "region_ref": "loaded", "magnitude": 2.0}
    ]
    pressure = SimulationIntent.model_validate(pressure_body)
    missing_mapping = assess_artifact_capability(
        pressure, model_kind="inp", model=native_metadata(source)
    )
    assert missing_mapping.supported is False
    assert "artifact.mapping_not_verified" in missing_mapping.blocking_issue_codes
    mapped = assess_artifact_capability(
        pressure,
        model_kind="inp",
        model=native_metadata(source, with_topology=True),
    )
    assert mapped.supported is True

    traction_body = copy.deepcopy(pressure_body)
    traction_body["loads"] = [
        {
            "type": "surface_traction",
            "region_ref": "loaded",
            "vector": [1.0, 0.0, 0.0],
        }
    ]
    traction = assess_artifact_capability(
        SimulationIntent.model_validate(traction_body),
        model_kind="inp",
        model=native_metadata(source, with_topology=True),
    )
    assert traction.supported is False
    assert "artifact.adapter_condition_unsupported" in traction.blocking_issue_codes


@pytest.fixture(scope="module")
def durable(tmp_path_factory):
    root = tmp_path_factory.mktemp("independent-review-durable")
    config = LocalDataConfig(root / "data")
    app = create_app(root / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app):
        yield app


def accepted_material_setup(app) -> tuple[str, dict, str]:
    body = proposed_material_payload(status="pending")
    body["regions"] = [
        region("fixed", "mesh_face", [1]),
        region("loaded", "mesh_face", [2]),
    ]
    path, request_body = setup_body(app, client_proposal(body))
    created = request(app, "POST", path, json=request_body)
    assert created.status_code == 201, created.text
    current = confirm_all_regions(app, created.json()["current"], "fp-confirm")
    assumption_id = current["intent"]["assumptions"][0]["id"]
    accepted = request(
        app,
        "POST",
        f"/api/v1/setups/{current['setup_id']}/assumptions/{assumption_id}/accept",
        json={"expected_revision": current["revision"], "request_id": "fp-accept"},
    )
    assert accepted.status_code == 201, accepted.text
    return current["setup_id"], accepted.json(), assumption_id


@pytest.mark.parametrize(
    "mutate_material",
    [
        lambda item: item.update({"E_MPa": 200_000.0}),
        lambda item: item.update({"nu": 0.29}),
        lambda item: item.update({"density_tonne_per_mm3": 7.7e-9}),
        lambda item: item.update(
            {"youngs_modulus_original": {"value": 210_000.0, "unit": "MPa"}}
        ),
        lambda item: item.update(
            {"density_original": {"value": 7.85e-9, "unit": "t/mm^3"}}
        ),
        lambda item: item.update({"name": "renamed"}),
        lambda item: item.update({"authority": "engineer_entered"}),
        lambda item: item.update({"proposal_assumption_ref": "unknown"}),
    ],
)
def test_accepted_material_snapshot_cannot_be_changed_in_place(
    durable, mutate_material
):
    setup_id, accepted, _ = accepted_material_setup(durable)
    candidate = copy.deepcopy(accepted["intent"])
    mutate_material(candidate["materials"][0])
    response = request(
        durable,
        "POST",
        f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": accepted["revision"],
            "request_id": "stale-material-edit",
            "intent": candidate,
        },
    )
    assert response.status_code in {409, 422}
    history = request(
        durable, "GET", f"/api/v1/setups/{setup_id}/revisions"
    ).json()
    assert history[-1]["id"] == accepted["id"]


def test_material_change_requires_new_pending_decision_or_engineer_authority(durable):
    setup_id, accepted, old_decision_id = accepted_material_setup(durable)
    old_fingerprint = accepted["intent"]["assumptions"][0][
        "material_proposal_fingerprint_sha256"
    ]
    assert len(old_fingerprint) == 64
    stale = copy.deepcopy(accepted["intent"])
    stale["materials"][0]["E_MPa"] = 205_000.0
    stale["materials"][0]["youngs_modulus_original"] = {
        "value": 205.0,
        "unit": "GPa",
    }
    stale_report = validate_intent(SimulationIntent.model_validate(stale))
    assert stale_report.engineering_ready is False
    assert "material.proposal_decision_stale" in {
        issue.code for issue in stale_report.issues
    }

    new_decision = Assumption(
        text="Revised proposal E=205 GPa, nu=0.3.",
        criticality="unit_critical",
        status="pending",
    )
    proposed = copy.deepcopy(accepted["intent"])
    proposed["materials"][0]["E_MPa"] = 205_000.0
    proposed["materials"][0]["youngs_modulus_original"] = {
        "value": 205.0,
        "unit": "GPa",
    }
    proposed["materials"][0]["proposal_assumption_ref"] = new_decision.id
    proposed["assumptions"].append(new_decision.model_dump(mode="json"))
    revised = request(
        durable,
        "POST",
        f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": accepted["revision"],
            "request_id": "new-material-proposal",
            "intent": proposed,
        },
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["validation"]["readiness_status"] == (
        "awaiting_assumption_acceptance"
    )
    assert revised.json()["intent"]["assumptions"][-1][
        "material_proposal_fingerprint_sha256"
    ] is None
    replay = request(
        durable,
        "POST",
        f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": accepted["revision"],
            "request_id": "new-material-proposal",
            "intent": proposed,
        },
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == revised.json()["id"]
    changed_reuse = copy.deepcopy(proposed)
    changed_reuse["materials"][0]["E_MPa"] = 204_000.0
    changed_reuse["materials"][0]["youngs_modulus_original"] = {
        "value": 204.0,
        "unit": "GPa",
    }
    conflict = request(
        durable,
        "POST",
        f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": accepted["revision"],
            "request_id": "new-material-proposal",
            "intent": changed_reuse,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "setup_request_id_conflict"

    accepted_new = request(
        durable,
        "POST",
        f"/api/v1/setups/{setup_id}/assumptions/{new_decision.id}/accept",
        json={
            "expected_revision": revised.json()["revision"],
            "request_id": "accept-new-material",
        },
    )
    assert accepted_new.status_code == 201, accepted_new.text
    new_fingerprint = next(
        item["material_proposal_fingerprint_sha256"]
        for item in accepted_new.json()["intent"]["assumptions"]
        if item["id"] == new_decision.id
    )
    assert new_fingerprint != old_fingerprint

    engineer = copy.deepcopy(accepted_new.json()["intent"])
    engineer["materials"][0]["authority"] = "engineer_entered"
    engineer["materials"][0]["proposal_assumption_ref"] = None
    converted = request(
        durable,
        "POST",
        f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": accepted_new.json()["revision"],
            "request_id": "engineer-material",
            "intent": engineer,
        },
    )
    assert converted.status_code == 201, converted.text
    assert converted.json()["intent"]["materials"][0]["authority"] == (
        "engineer_entered"
    )
    assert converted.json()["intent"]["materials"][0][
        "proposal_assumption_ref"
    ] is None
    assert any(
        item["id"] == old_decision_id and item["status"] == "accepted"
        for item in converted.json()["intent"]["assumptions"]
    )
    historical = request(
        durable,
        "GET",
        f"/api/v1/setups/{setup_id}/revisions/{accepted['revision']}",
    ).json()
    assert historical["intent"]["materials"][0]["E_MPa"] == 210_000.0
    assert historical["intent"]["assumptions"][0][
        "material_proposal_fingerprint_sha256"
    ] == old_fingerprint


def test_material_fingerprint_survives_restart(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(
        create_app(
            tmp_path / "legacy",
            mode=RuntimeMode.TEST,
            data_config=config,
        )
    ) as client:
        setup_id, accepted, _ = accepted_material_setup(client.app)
        fingerprint = accepted["intent"]["assumptions"][0][
            "material_proposal_fingerprint_sha256"
        ]
    with TestClient(
        create_app(
            tmp_path / "legacy-reopen",
            mode=RuntimeMode.TEST,
            data_config=config,
        )
    ) as client:
        reopened = request(client.app, "GET", f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200
        current = reopened.json()["current"]
        assert current["intent"]["assumptions"][0][
            "material_proposal_fingerprint_sha256"
        ] == fingerprint
        assert "material.proposal_decision_stale" not in {
            issue["code"] for issue in current["validation"]["issues"]
        }


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("Assign material alloy with E = 210 GPa, ν = 0.3", ("210 GPa", 0.3, None)),
        ("Assign material alloy with E = 210000 MPa, nu = 0.3", ("210000 MPa", 0.3, None)),
        ("Assign material alloy with E = 210000000000 Pa, nu = 0.3", ("210000000000 Pa", 0.3, None)),
        ("Assign material alloy with E=210 GPa, nu=0.3, density=7850 kg/m³", ("210 GPa", 0.3, "7850 kg/m³")),
        ("Assign material alloy with E=210 GPa, nu=0.3, density=7850 kg/m3", ("210 GPa", 0.3, "7850 kg/m3")),
    ],
)
def test_material_parser_preserves_every_supported_explicit_property(
    instruction, expected
):
    result = Interpreter().interpret(instruction, {})
    proposal = result.material_proposal
    assert proposal is not None
    assert (
        proposal.youngs_modulus,
        proposal.poisson_ratio,
        proposal.density,
    ) == expected


@pytest.mark.parametrize(
    ("instruction", "code"),
    [
        ("Assign material x with E=30 ksi, nu=0.3", "quantity.unsupported_unit"),
        ("Assign material x with E=210 GPa, nu=0.3, density=1 lb/in3", "quantity.unsupported_unit"),
        ("Assign material x with E=210 GPa, nu=0.8", "material.poissons_ratio_invalid"),
        ("Assign material x with E=210 GPa, nu=banana", "material.property_parse_failed"),
        ("Assign material x with E=210 GPa, nu=0.3, density=heavy", "material.property_parse_failed"),
        ("Use Inconel 718 material", "material.properties_incomplete"),
    ],
)
def test_material_parser_fails_closed_with_stable_codes(instruction, code):
    with pytest.raises(UnsupportedMaterialInputError) as caught:
        Interpreter().interpret(instruction, {})
    assert caught.value.code == code


def mixed_load_intent(order) -> SimulationIntent:
    body = payload()
    body["regions"].append(region("node", "node_set", [10]))
    loads = [
        {"type": "concentrated_force", "region_ref": "node", "vector": [1e16, -4.0, 0.0]},
        {"type": "concentrated_force", "region_ref": "node", "vector": [1.0, 5.0, 0.0]},
        {"type": "concentrated_force", "region_ref": "node", "vector": [-1e16, -1.0, 0.0]},
        {"type": "resultant_surface_force", "region_ref": "loaded", "vector": [2.0, -3.0, 4.0]},
        {"type": "pressure", "region_ref": "loaded", "magnitude": 2.0},
        {"type": "surface_traction", "region_ref": "loaded", "vector": [-1.0, 0.0, 0.0]},
        {"type": "gravity", "region_ref": None, "vector": [0.0, 0.0, -9810.0]},
    ]
    body["loads"] = [loads[index] for index in order]
    return SimulationIntent.model_validate(body)


def test_load_summary_and_durable_hash_are_permutation_invariant():
    baseline_summary = None
    baseline_hash = None
    for order in itertools.permutations(range(7)):
        intent = mixed_load_intent(order)
        summary = summarize_loads(intent).model_dump(mode="json")
        _canonical, digest = canonical_intent(intent)
        if baseline_summary is None:
            baseline_summary, baseline_hash = summary, digest
        assert summary == baseline_summary
        assert digest == baseline_hash
    assert baseline_summary["concentrated_force_total_N"][0] == 1.0
    assert baseline_summary["explicit_force_vector_sum_N"] == [3.0, -3.0, 4.0]
    assert baseline_summary["concentrated_force_count"] == 3
    assert baseline_summary["resultant_surface_force_count"] == 1
    assert baseline_summary["pressure_load_count"] == 1
    assert baseline_summary["traction_load_count"] == 1
    assert baseline_summary["gravity_load_count"] == 1


def test_semantic_duplicates_ignore_order_ids_and_original_provenance():
    body = payload()
    body["bcs"] = [
        {
            "type": "fixed_displacement",
            "region_ref": "fixed",
            "components": ["x", "y", "z"],
        },
        {
            "type": "fixed_displacement",
            "region_ref": "fixed",
            "components": ["z", "x", "y"],
        },
    ]
    assert "bc.duplicate" in {
        issue.code for issue in validate_intent(
            SimulationIntent.model_validate(body)
        ).issues
    }

    body["bcs"] = [
        {
            "type": "prescribed_displacement",
            "region_ref": "fixed",
            "components": {"x": 1.0},
            "components_original": {"x": {"value": 1.0, "unit": "mm"}},
        },
        {
            "type": "prescribed_displacement",
            "region_ref": "fixed",
            "components": {"x": 1.0},
            "components_original": {"x": {"value": 0.001, "unit": "m"}},
        },
    ]
    assert "bc.duplicate" in {
        issue.code for issue in validate_intent(
            SimulationIntent.model_validate(body)
        ).issues
    }

    load = copy.deepcopy(payload()["loads"][0])
    equivalent = copy.deepcopy(load)
    equivalent["original_force"] = {"value": 5000.0, "unit": "N"}
    body = payload()
    body["loads"] = [load, equivalent]
    assert "load.duplicate" in {
        issue.code for issue in validate_intent(
            SimulationIntent.model_validate(body)
        ).issues
    }
    body["regions"].append(region("other", "cad_face", [3]))
    equivalent["region_ref"] = "other"
    body["loads"] = [load, equivalent]
    assert "load.duplicate" not in {
        issue.code for issue in validate_intent(
            SimulationIntent.model_validate(body)
        ).issues
    }


SUMMARY = FaceInventorySummary.model_validate(
    {
        "source_name": "steel-shell-bracket.step",
        "face_count": 1,
        "surface_type_counts": {"Plane": 1},
        "available_labels": ["bottom_face"],
        "hole_groups": [],
        "face_areas": {
            "minimum_mm2": 1.0,
            "maximum_mm2": 1.0,
            "largest_first_mm2": [1.0],
        },
    },
    strict=True,
)


class RecordingTransport:
    def __init__(self):
        self.calls = 0

    def complete(self, _request):
        self.calls += 1
        return {
            "intents": [
                {
                    "op_list": [{"op": "labeled", "name": "bottom_face"}],
                    "bc": {
                        "type": "fixed_displacement",
                        "components": ["x", "y", "z"],
                    },
                    "load": None,
                    "target_description": "bottom face",
                }
            ]
        }


@pytest.mark.parametrize(
    "instruction",
    [
        "Apply dynamic pressure to the bottom face",
        "Fix the bottom face of the steel bracket",
        "Fix the shell housing bottom face",
        "Fix the aluminium beam support bottom face",
    ],
)
def test_contextual_unsupported_detection_allows_supported_requests(instruction):
    transport = RecordingTransport()
    Interpreter(transport=transport).interpret(instruction, SUMMARY)
    assert transport.calls == 1


@pytest.mark.parametrize(
    ("instruction", "code"),
    [
        ("Run a dynamic analysis", "analysis.dynamics_unsupported"),
        ("Run a modal analysis", "analysis.dynamics_unsupported"),
        ("Perform a transient simulation", "analysis.dynamics_unsupported"),
        ("Define contact between these parts", "interaction.contact_unsupported"),
        ("Use a plastic material model", "material.plastic_unsupported"),
        ("Analyze this as a shell", "analysis.shell_unsupported"),
        ("Model this as a beam", "analysis.beam_unsupported"),
        ("Use a local coordinate system", "coordinate_system.local_unsupported"),
    ],
)
def test_clear_unsupported_requests_stop_before_provider(instruction, code):
    transport = RecordingTransport()
    with pytest.raises(UnsupportedCapabilityError) as caught:
        Interpreter(transport=transport).interpret(instruction, SUMMARY)
    assert caught.value.code == code
    assert transport.calls == 0


MALFORMED_COLLECTION_VALUES = [
    None,
    "not-a-list",
    7,
    {"unexpected": "object"},
    [None, "scalar", 7, {"malformed": True}],
]


@pytest.mark.parametrize(
    ("collection", "bad_value"),
    list(itertools.product(
        ("materials", "regions", "bcs", "loads", "assumptions"),
        MALFORMED_COLLECTION_VALUES,
    )),
)
def test_malformed_durable_collections_never_500_or_create_records(
    durable, collection, bad_value
):
    candidate = payload()
    candidate[collection] = bad_value
    path, body = setup_body(
        durable, candidate, f"malformed-{collection}-{type(bad_value).__name__}"
    )
    response = request(durable, "POST", path, json=body)
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    )
    assert request(durable, "GET", path).json() == []


def test_malformed_revision_collections_never_500_or_extend_history(durable):
    path, body = setup_body(
        durable, client_proposal(payload()), "malformed-revision-create"
    )
    created = request(durable, "POST", path, json=body)
    assert created.status_code == 201, created.text
    setup_id = created.json()["setup"]["id"]
    original = created.json()["current"]["intent"]
    for collection, bad_value in itertools.product(
        ("materials", "regions", "bcs", "loads", "assumptions"),
        MALFORMED_COLLECTION_VALUES,
    ):
        candidate = copy.deepcopy(original)
        candidate[collection] = bad_value
        response = request(
            durable,
            "POST",
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": 1,
                "request_id": (
                    f"bad-revision-{collection}-{type(bad_value).__name__}-"
                    f"{repr(bad_value)[:20]}"
                ),
                "intent": candidate,
            },
        )
        assert response.status_code == 422, response.text
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
    history = request(
        durable, "GET", f"/api/v1/setups/{setup_id}/revisions"
    ).json()
    assert len(history) == 1


def test_truthful_export_projection_on_durable_native_and_step(durable, tmp_path):
    project = create_project(durable, "truthful")
    native_upload = upload(
        durable, project["id"], NATIVE_INP, filename="native.inp"
    )
    native_body = native_intent()
    native_response = request(
        durable,
        "POST",
        f"/api/v1/projects/{project['id']}/setups",
        json={
            "model_id": native_upload["model_id"],
            "model_version_id": native_upload["model_version"]["id"],
            "request_id": "native",
            "intent": client_proposal(native_body),
        },
    )
    assert native_response.status_code == 201, native_response.text
    current = confirm_all_regions(
        durable, native_response.json()["current"], "truthful-native"
    )
    assert current["engineering_ready"] is True
    assert current["artifact_capability"]["supported"] is True
    assert current["validation"]["export_eligible"] is True
    assert current["export_eligible"] is True

    missing = copy.deepcopy(native_body)
    missing["regions"][0]["entity_ids"] = ["MISSING"]
    missing_response = request(
        durable,
        "POST",
        f"/api/v1/projects/{project['id']}/setups",
        json={
            "model_id": native_upload["model_id"],
            "model_version_id": native_upload["model_version"]["id"],
            "request_id": "missing-native",
            "intent": client_proposal(missing),
        },
    )
    assert missing_response.status_code == 201, missing_response.text
    current = confirm_all_regions(
        durable, missing_response.json()["current"], "truthful-missing"
    )
    assert current["engineering_ready"] is True
    assert current["artifact_capability"]["supported"] is False
    assert current["validation"]["export_eligible"] is False
    assert current["export_eligible"] is False
    assert "artifact.native_region_missing" in current[
        "artifact_capability"
    ]["blocking_issue_codes"]
