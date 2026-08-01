"""Focused R4b.2 durable stable CAD-region regressions."""

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import LocalDataConfig
from app.orchestration import _condition_signature
from app.migrations import alembic_config
from app.persistence import (
    CadRegionReferenceError,
    Persistence,
    SetupRevisionConflictError,
    canonical_intent,
    create_sqlite_engine,
)
from app.runtime_mode import RuntimeMode
from app.server import create_app
from app.session import InvalidRegionTransitionError, SelectionSessionStore
from export.abaqus_py import export_abaqus_py
from export.common import (
    CadModelMetadata,
    ExportNotReadyError,
    MissingRegionMappingError,
)
from ir.schema import (
    CadFaceTarget,
    EngineeringConsistencyError,
    ExportBlockedError,
    Region,
    SimulationIntent,
    enforce_cad_region_entity_ids_invariant,
    validate_cad_numeric_evidence,
)
from ir.validate import validate_intent
from ir.versioning import load_simulation_intent
from geom.identity import GeometryFaceInput, build_geometry_identity
from tests.test_session import intent_payload
from tests.test_project_persistence import minimal_inp


STEP = Path(__file__).parent / "fixtures" / "bracket.step"


def _project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "r4b2"})
    assert response.status_code == 201
    return response.json()["id"]


def _upload(
    client: TestClient, project_id: str, model_id: str | None = None
) -> dict:
    path = (
        f"/api/v1/projects/{project_id}/models"
        if model_id is None
        else f"/api/v1/projects/{project_id}/models/{model_id}/versions"
    )
    response = client.post(
        path,
        files={"file": ("bracket.step", STEP.read_bytes(), "application/step")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _target(version_id: str, artifact: dict, face: dict) -> dict:
    return {
        "model_version_id": version_id,
        "artifact_sha256": artifact["artifact_sha256"],
        "resolution": "resolved",
        "stable_identities": [face["stable_identity"]],
        "source_face_tags": [face["source_ref"]],
    }


def _cad_intent(version_id: str, artifact: dict) -> dict:
    body = intent_payload()
    unique = [face for face in artifact["faces"] if not face["ambiguous"]]
    assert len(unique) >= 2
    for region, face in zip(body["regions"], unique, strict=False):
        region["entity_type"] = "cad_face"
        region.pop("entity_ids", None)
        region["cad_face_target"] = _target(version_id, artifact, face)
    return body


def test_exact_version_binding_confirmation_restart_and_no_successor_rebinding(
    tmp_path,
):
    config = LocalDataConfig(tmp_path / "data")
    first_app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(first_app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        body = _cad_intent(version_id, artifact)
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "create",
                "intent": body,
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        confirmed = client.post(
            f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        )
        assert confirmed.status_code == 201, confirmed.text
        expected_target = confirmed.json()["intent"]["regions"][0]["cad_face_target"]

        successor = _upload(client, project_id, uploaded["model_id"])
        successor_id = successor["model_version"]["id"]
        forged = deepcopy(body)
        for region in forged["regions"]:
            region["cad_face_target"]["model_version_id"] = version_id
        mismatch = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": successor_id,
                "request_id": "copied-reference",
                "intent": forged,
            },
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["code"] == "cad_region_model_version_mismatch"

    restarted = create_app(
        tmp_path / "restart", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted) as client:
        reopened = client.get(f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200
        region = reopened.json()["current"]["intent"]["regions"][0]
        assert region["status"] == "confirmed"
        assert region["cad_face_target"] == expected_target
        assert reopened.json()["setup"]["model_version_id"] == version_id
        assert reopened.json()["setup"]["is_stale"] is True


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda body: body["regions"][0]["cad_face_target"].update(
                stable_identities=["gfi1:" + "f" * 64]
            ),
            "cad_region_identity_unknown",
        ),
        (
            lambda body: body["regions"][0].update(
                entity_ids=body["regions"][1]["cad_face_target"][
                    "source_face_tags"
                ]
            ),
            "cad_region_entity_ids_forbidden",
        ),
    ],
)
def test_forged_or_contradictory_targets_publish_no_setup(
    tmp_path, mutation, code
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        body = _cad_intent(version_id, artifact)
        mutation(body)
        response = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "forged",
                "intent": body,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == code
        assert app.state.persistence.list_setups(project_id) == []


def test_contradictory_http_request_is_sanitized_with_exceptions_suppressed(
    tmp_path,
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        body = _cad_intent(version_id, artifact)
        body["regions"][0]["entity_ids"] = body["regions"][1][
            "cad_face_target"
        ]["source_face_tags"]
        response = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "contradictory-suppressed",
                "intent": body,
            },
        )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        problem = response.json()
        assert problem["code"] == "cad_region_entity_ids_forbidden"
        assert problem["detail"] == (
            "The CAD region contains forbidden or contradictory numeric evidence."
        )
        assert app.state.persistence.list_setups(project_id) == []


def test_invalid_v3_numeric_evidence_is_rejected_not_migrated(tmp_path):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    invalid_values = (
        [0],
        [-1],
        [1, 1],
        ["1"],
        [1.0],
        [True],
        [1, -1],
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        for index, values in enumerate(invalid_values):
            body = _cad_intent(version_id, artifact)
            body["regions"][0]["cad_face_target"]["source_face_tags"] = values
            response = client.post(
                f"/api/v1/projects/{project_id}/setups",
                json={
                    "model_id": uploaded["model_id"],
                    "model_version_id": version_id,
                    "request_id": f"invalid-v3-{index}",
                    "intent": body,
                },
            )
            assert response.status_code == 422
            assert response.json()["code"] == "request_validation_failed"
            assert "invalid_legacy_evidence" not in response.text
        assert app.state.persistence.list_setups(project_id) == []


def test_ambiguous_target_is_persistable_but_not_confirmable(tmp_path):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        ambiguous = next(
            (face for face in artifact["faces"] if face["ambiguous"]), None
        )
        if ambiguous is None:
            synthetic = build_geometry_identity(
                model_version_id=version_id,
                source_sha256=uploaded["model_version"]["source_sha256"],
                faces=[
                    GeometryFaceInput(
                        source_ref=1,
                        surface_type="plane",
                        area=1.0,
                        centroid=(0.0, 0.0, 0.0),
                        normal=(0.0, 0.0, 1.0),
                        boundary_loop_count=1,
                        adjacent_refs=(2,),
                    ),
                    GeometryFaceInput(
                        source_ref=2,
                        surface_type="plane",
                        area=1.0,
                        centroid=(0.0, 0.0, 0.0),
                        normal=(0.0, 0.0, 1.0),
                        boundary_loop_count=1,
                        adjacent_refs=(1,),
                    ),
                ],
            )
            raw = synthetic.canonical_bytes()
            with app.state.persistence.engine.begin() as connection:
                connection.execute(
                    text("DROP TRIGGER geometry_identity_artifacts_immutable")
                )
                connection.execute(
                    text(
                        "UPDATE geometry_identity_artifacts "
                        "SET canonical_bytes=:raw, integrity_sha256=:digest "
                        "WHERE model_version_id=:version_id"
                    ),
                    {
                        "raw": raw,
                        "digest": hashlib.sha256(raw).hexdigest(),
                        "version_id": version_id,
                    },
                )
            artifact = client.get(
                f"/api/v1/model-versions/{version_id}/geometry-identity"
            ).json()
            ambiguous = next(
                face for face in artifact["faces"] if face["ambiguous"]
            )
            body = intent_payload()
            for region in body["regions"]:
                region["entity_type"] = "cad_face"
                region.pop("entity_ids", None)
                region["cad_face_target"] = {
                "model_version_id": version_id,
                "artifact_sha256": artifact["artifact_sha256"],
                "resolution": "ambiguous",
                "collision_group_ids": [ambiguous["collision_group_id"]],
                "source_face_tags": [ambiguous["source_ref"]],
            }
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "ambiguous",
                "intent": body,
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        assert created.json()["current"]["export_eligible"] is False
        confirm = client.post(
            f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        )
        assert confirm.status_code == 409
        assert confirm.json()["code"] == "cad_region_unresolved"


def test_corrupt_historical_artifact_fails_closed_without_read_time_repair(
    tmp_path,
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "corruption",
                "intent": _cad_intent(version_id, artifact),
            },
        )
        assert created.status_code == 201
        setup_id = created.json()["setup"]["id"]
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER geometry_identity_artifacts_immutable")
            )
            connection.execute(
                text(
                    "UPDATE geometry_identity_artifacts "
                    "SET integrity_sha256=:bad WHERE model_version_id=:version_id"
                ),
                {"bad": "0" * 64, "version_id": version_id},
            )
        reopened = client.get(f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200
        current = reopened.json()["current"]
        assert current["export_eligible"] is False
        assert "cad_region_artifact_integrity_failed" in {
            issue["code"] for issue in current["validation"]["issues"]
        }
        with app.state.persistence.engine.begin() as connection:
            digest = connection.execute(
                text(
                    "SELECT integrity_sha256 FROM geometry_identity_artifacts "
                    "WHERE model_version_id=:version_id"
                ),
                {"version_id": version_id},
            ).scalar_one()
        assert digest == "0" * 64


def test_v2_confirmed_numeric_region_migrates_to_blocked_legacy_evidence():
    body = intent_payload()
    body["schema_version"] = 2
    region = body["regions"][0]
    region["entity_type"] = "cad_face"
    region["entity_ids"] = [7]
    region["status"] = "confirmed"
    intent = load_simulation_intent(body, source="legacy setup revision")
    migrated = intent.regions[0]
    assert migrated.status == "proposed"
    assert migrated.cad_face_target is not None
    assert migrated.cad_face_target.resolution == "legacy_local_only"
    assert migrated.cad_face_target.source_face_tags == [7]
    assert migrated.cad_face_target.legacy_status == "confirmed"
    with pytest.raises(ExportBlockedError):
        intent.export_payload()


@pytest.mark.parametrize(
    ("entity_ids", "source_face_tags", "valid"),
    [
        ([1, 2], [1, 2], True),
        ([1, 2], [2, 1], True),
        ([1, 2], [1], False),
        ([1], [1, 2], False),
        ([1, 1], [1, 1], False),
        ([1], None, True),
        (None, [1], True),
    ],
)
def test_shared_numeric_evidence_rule_compares_unordered_valid_membership(
    entity_ids, source_face_tags, valid
):
    if valid:
        validate_cad_numeric_evidence(entity_ids, source_face_tags)
    else:
        with pytest.raises(EngineeringConsistencyError):
            validate_cad_numeric_evidence(entity_ids, source_face_tags)
    assert entity_ids in (None, [1], [1, 1], [1, 2])
    assert source_face_tags in (None, [1], [1, 1], [1, 2], [2, 1])


def _synthetic_resolved_cad_intent(*, source_face_tags: list[int]) -> dict:
    body = intent_payload()
    region = body["regions"][0]
    region.update(
        entity_type="cad_face",
        cad_face_target={
            "resolution": "resolved",
            "model_version_id": "historical-version",
            "artifact_sha256": "a" * 64,
            "stable_identities": ["gfi1:" + "b" * 64],
            "source_face_tags": source_face_tags,
        },
    )
    region.pop("entity_ids", None)
    return body


def test_single_cad_evidence_field_reorders_canonically_and_sessions_agree():
    ordered = _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    reordered = _synthetic_resolved_cad_intent(source_face_tags=[2, 1])
    first = SimulationIntent.model_validate(ordered)
    second = SimulationIntent.model_validate(reordered)
    assert first.regions[0] == second.regions[0]
    assert second.regions[0].cad_face_target.source_face_tags == [1, 2]
    assert load_simulation_intent(
        reordered, source="reordered v3"
    ).regions[0].cad_face_target.source_face_tags == [1, 2]

    independent = deepcopy(ordered)
    independent["regions"][0]["entity_ids"] = [1, 2]
    with pytest.raises(
        ValidationError, match="cad_region_entity_ids_forbidden"
    ):
        SimulationIntent.model_validate(independent)
    with pytest.raises(Exception) as deserialization:
        load_simulation_intent(independent, source="independent v3")
    assert deserialization.value.__class__.__name__ == "PayloadStructureError"

    store = SelectionSessionStore()
    store.save_intent("model", first)
    store.save_intent("model", second)
    first_regions = {region.id: region for region in first.regions}
    second_regions = {region.id: region for region in second.regions}
    assert _condition_signature(
        first.bcs[0], first_regions
    ) == _condition_signature(second.bcs[0], second_regions)


def test_model_copy_cad_entity_ids_is_rejected_by_validation_session_and_export(
    tmp_path,
):
    valid = SimulationIntent.model_validate(
        _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    )
    hostile_region = valid.regions[0].model_copy(
        update={"entity_ids": [999], "status": "confirmed"}
    )
    hostile = valid.model_copy(
        update={
            "regions": [
                hostile_region,
                *[
                    region.model_copy(update={"status": "confirmed"})
                    for region in valid.regions[1:]
                ],
            ]
        }
    )
    report = validate_intent(hostile)
    assert "cad_region_entity_ids_forbidden" in {
        issue.code for issue in report.issues
    }
    assert report.export_eligible is False
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        hostile.export_payload()
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        SelectionSessionStore().save_intent("hostile", hostile)
    with pytest.raises(EngineeringConsistencyError):
        export_abaqus_py(
            hostile,
            CadModelMetadata(
                source_path=tmp_path / "never-created.step",
                source_name="never-created.step",
                source_sha256="0" * 64,
                source_cad_face_tags=(1, 2),
            ),
        )
    assert not list(tmp_path.iterdir())


def test_hostile_assignment_and_non_cad_membership_follow_shared_invariant():
    intent = SimulationIntent.model_validate(
        _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    )
    intent.regions[0].entity_ids = [999]
    report = validate_intent(intent)
    assert "cad_region_entity_ids_forbidden" in {
        issue.code for issue in report.issues
    }
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        intent.export_payload()

    non_cad = SimulationIntent.model_validate(intent_payload())
    non_cad.regions[0].entity_ids = [999]
    assert non_cad.regions[0].entity_ids == [999]
    assert "cad_region_entity_ids_forbidden" not in {
        issue.code for issue in validate_intent(non_cad).issues
    }


def test_persistence_create_and_update_reject_hostile_cad_entity_ids(
    tmp_path,
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        clean = SimulationIntent.model_validate(
            _cad_intent(version_id, artifact)
        )
        hostile = clean.model_copy(deep=True)
        hostile.regions[0].entity_ids = [999]
        with pytest.raises(
            CadRegionReferenceError,
            match="cad_region_entity_ids_forbidden",
        ):
            app.state.persistence.create_setup(
                project_id=project_id,
                model_id=uploaded["model_id"],
                model_version_id=version_id,
                intent=hostile,
                request_id="hostile-create",
            )

        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "clean-create",
                "intent": clean.model_dump(mode="json"),
            },
        )
        assert created.status_code == 201, created.text
        with pytest.raises(
            CadRegionReferenceError,
            match="cad_region_entity_ids_forbidden",
        ):
            app.state.persistence.mutate_setup(
                setup_id=created.json()["setup"]["id"],
                expected_revision=1,
                request_id="hostile-update",
                mutation_type="intent_updated",
                intent=hostile,
            )


def test_confirmation_and_audit_reject_hostile_internal_cad_state(
    tmp_path, monkeypatch
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        clean = SimulationIntent.model_validate(
            _cad_intent(version_id, artifact)
        )
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "confirm-clean",
                "intent": clean.model_dump(mode="json"),
            },
        )
        hostile = clean.model_copy(deep=True)
        hostile.regions[0].entity_ids = [999]
        monkeypatch.setattr(
            app.state.persistence,
            "revision_intent",
            lambda _revision: hostile,
        )
        confirm = client.post(
            f"/api/v1/setups/{created.json()['setup']['id']}/regions/"
            f"{clean.regions[0].id}/confirm",
            json={"expected_revision": 1, "request_id": "hostile-confirm"},
        )
        assert confirm.status_code == 422
        assert confirm.json()["code"] == "cad_region_entity_ids_forbidden"

    audit_app = create_app(tmp_path / "audit")
    record = audit_app.state.model_store.add("part.step", STEP.read_bytes())
    clean_audit = SimulationIntent.model_validate(
        _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    )
    audit_app.state.session_store.save_intent(record.model_id, clean_audit)
    with TestClient(audit_app) as client:
        clean_response = client.get(f"/session/{record.model_id}/audit")
        assert clean_response.status_code == 200
        audit_region = clean_response.json()["regions"][0]
        assert "entity_ids" not in audit_region
        assert audit_region["cad_face_target"]["source_face_tags"] == [1, 2]
    stored = audit_app.state.session_store._sessions[record.model_id].intent
    assert stored is not None
    stored.regions[0].entity_ids = [999]
    with TestClient(audit_app, raise_server_exceptions=False) as client:
        audit = client.get(f"/session/{record.model_id}/audit")
        assert audit.status_code == 422
        assert audit.json()["code"] == "cad_region_entity_ids_forbidden"


@pytest.mark.parametrize(
    "tags",
    [[0], [-1], [-1, -2], [1, 1], [2, -1, 3]],
    ids=["zero", "negative", "multiple-negative", "duplicates", "mixed"],
)
def test_invalid_v2_numeric_evidence_migrates_without_repair(tags):
    body = intent_payload()
    body["schema_version"] = 2
    region = body["regions"][0]
    region.update(entity_type="cad_face", entity_ids=tags, status="confirmed")
    region.pop("cad_face_target", None)
    original = deepcopy(body)

    migrated = load_simulation_intent(body, source="historical setup revision")
    target = migrated.regions[0].cad_face_target
    assert target.resolution == "invalid_legacy_evidence"
    assert target.legacy_reason == "invalid_numeric_tags"
    assert target.source_face_tags == tags
    assert migrated.regions[0].entity_ids is None
    assert "entity_ids" not in migrated.model_dump(mode="json")["regions"][0]
    assert migrated.regions[0].status == "proposed"
    assert body == original
    with pytest.raises(ExportBlockedError):
        migrated.export_payload()


def test_invalid_v2_setup_reopens_without_read_time_repair_and_stays_bound(
    tmp_path,
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    historical_tags = {
        "zero": [0],
        "negative": [-1],
        "multiple-negative": [-1, -2],
        "duplicates": [1, 1],
        "mixed": [2, -1, 3],
        "valid": [2, 1],
    }
    stored: dict[str, tuple[str, str, str]] = {}
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        for name, tags in historical_tags.items():
            created = client.post(
                f"/api/v1/projects/{project_id}/setups",
                json={
                    "model_id": uploaded["model_id"],
                    "model_version_id": version_id,
                    "request_id": f"base-{name}",
                    "intent": intent_payload(),
                },
            )
            assert created.status_code == 201, created.text
            setup_id = created.json()["setup"]["id"]
            body = intent_payload()
            body["schema_version"] = 2
            region = body["regions"][0]
            region.update(
                entity_type="cad_face",
                entity_ids=tags,
                status="confirmed",
            )
            region.pop("cad_face_target", None)
            raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            stored[name] = (setup_id, raw, digest)
        with app.state.persistence.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER setup_revisions_immutable"))
            for setup_id, raw, digest in stored.values():
                connection.execute(
                    text(
                        "UPDATE setup_revisions SET schema_version=2, "
                        "intent_json=:raw, intent_sha256=:digest "
                        "WHERE setup_id=:setup_id"
                    ),
                    {
                        "setup_id": setup_id,
                        "raw": raw,
                        "digest": digest,
                    },
                )

    restarted = create_app(
        tmp_path / "restart", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted) as client:
        for name, tags in historical_tags.items():
            setup_id, raw, digest = stored[name]
            response = client.get(f"/api/v1/setups/{setup_id}")
            assert response.status_code == 200, response.text
            setup = response.json()
            assert setup["setup"]["model_version_id"] == version_id
            current = setup["current"]
            target = current["intent"]["regions"][0]["cad_face_target"]
            expected = (
                "legacy_local_only"
                if name == "valid"
                else "invalid_legacy_evidence"
            )
            assert target["resolution"] == expected
            assert target["source_face_tags"] == (
                sorted(tags) if name == "valid" else tags
            )
            assert "entity_ids" not in current["intent"]["regions"][0]
            assert current["intent"]["regions"][0]["status"] == "proposed"
            assert current["stored_simulation_intent_schema_version"] == 2
            assert current["stored_intent_sha256"] == digest
            assert current["export_eligible"] is False
            with pytest.raises(ExportBlockedError):
                load_simulation_intent(
                    raw, source="stored historical setup"
                ).export_payload()
            confirmation = client.post(
                f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
                json={
                    "expected_revision": 1,
                    "request_id": f"confirm-{name}",
                },
            )
            assert confirmation.status_code == 409
            assert confirmation.json()["code"] == "cad_region_unresolved"
        with restarted.state.persistence.engine.connect() as connection:
            for setup_id, raw, digest in stored.values():
                row = connection.execute(
                    text(
                        "SELECT schema_version, intent_json, intent_sha256 "
                        "FROM setup_revisions WHERE setup_id=:setup_id"
                    ),
                    {"setup_id": setup_id},
                ).one()
                assert tuple(row) == (2, raw, digest)

        with restarted.state.persistence.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM geometry_identity_artifacts "
                    "WHERE model_version_id=:version_id"
                ),
                {"version_id": version_id},
            )
        missing_artifact = client.get(
            f"/api/v1/setups/{stored['zero'][0]}"
        )
        assert missing_artifact.status_code == 200
        current = missing_artifact.json()["current"]
        assert current["intent"]["regions"][0]["cad_face_target"][
            "source_face_tags"
        ] == [0]
        assert "cad_region_artifact_missing" in {
            issue["code"] for issue in current["validation"]["issues"]
        }


def test_v2_valid_empty_and_inp_migration_policies_are_truthful():
    valid = intent_payload()
    valid["schema_version"] = 2
    valid["regions"][0].update(
        entity_type="cad_face", entity_ids=[2, 1], status="confirmed"
    )
    valid["regions"][0].pop("cad_face_target", None)
    migrated = load_simulation_intent(valid, source="valid historical v2")
    assert migrated.regions[0].cad_face_target.resolution == "legacy_local_only"
    assert migrated.regions[0].cad_face_target.source_face_tags == [1, 2]

    empty = deepcopy(valid)
    empty["regions"][0]["entity_ids"] = []
    migrated_empty = load_simulation_intent(empty, source="invalid empty v2")
    assert (
        migrated_empty.regions[0].cad_face_target.resolution
        == "invalid_legacy_evidence"
    )
    assert migrated_empty.regions[0].cad_face_target.source_face_tags == []

    inp = intent_payload()
    inp["schema_version"] = 2
    migrated_inp = load_simulation_intent(inp, source="historical INP setup")
    assert all(
        region.entity_type != "cad_face"
        and region.cad_face_target is None
        for region in migrated_inp.regions
    )


def test_solver_adapter_never_treats_v3_local_evidence_as_authoritative(
    tmp_path,
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        body = _cad_intent(version_id, artifact)
        for region in body["regions"]:
            region["status"] = "confirmed"
        intent = load_simulation_intent(body, source="resolved v3")
        assert intent.export_payload()["regions"][0]["cad_face_target"][
            "stable_identities"
        ]
        with pytest.raises(MissingRegionMappingError):
            export_abaqus_py(
                intent,
                CadModelMetadata(
                    source_path=STEP,
                    source_name=STEP.name,
                    source_sha256=hashlib.sha256(STEP.read_bytes()).hexdigest(),
                    source_cad_face_tags=tuple(
                        sorted(face["source_ref"] for face in artifact["faces"])
                    ),
                ),
            )


def test_cad_target_discriminated_contract_acceptance_matrix():
    adapter = TypeAdapter(CadFaceTarget)
    valid = [
        {
            "resolution": "resolved",
            "model_version_id": "version",
            "artifact_sha256": "a" * 64,
            "stable_identities": ["gfi1:" + "b" * 64],
            "source_face_tags": [1],
        },
        {
            "resolution": "ambiguous",
            "model_version_id": "version",
            "artifact_sha256": "a" * 64,
            "collision_group_ids": ["c" * 64],
            "source_face_tags": [1],
        },
        {
            "resolution": "unresolved",
            "model_version_id": "version",
            "source_face_tags": [1],
        },
        {
            "resolution": "legacy_local_only",
            "legacy_status": "confirmed",
            "source_face_tags": [1],
        },
    ]
    for payload in valid:
        assert adapter.validate_python(payload, strict=True).resolution == (
            payload["resolution"]
        )
    invalid = [
        {**valid[0], "stable_identities": []},
        {**valid[1], "collision_group_ids": []},
        {**valid[2], "artifact_sha256": "a" * 64},
        {**valid[3], "model_version_id": "version"},
        {**valid[0], "stable_identities": ["not-a-stable-identity"]},
        {**valid[1], "collision_group_ids": ["not-a-collision-group"]},
        {**valid[0], "source_face_tags": [0]},
        {**valid[0], "source_face_tags": [-1]},
        {
            **valid[0],
            "stable_identities": [
                "gfi1:" + "b" * 64,
                "gfi1:" + "b" * 64,
            ],
        },
        {
            **valid[1],
            "collision_group_ids": ["c" * 64, "c" * 64],
        },
        {**valid[2], "source_face_tags": [1, 1]},
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            adapter.validate_python(payload, strict=True)
    schema = adapter.json_schema()
    assert schema["discriminator"]["propertyName"] == "resolution"
    assert len(schema["oneOf"]) == 5


def test_generated_cad_target_array_constraints_match_runtime():
    root = Path(__file__).parents[1]
    openapi = json.loads((root / "schema" / "openapi.json").read_text())
    standalone = json.loads(
        (root / "schema" / "simulation-intent.schema.json").read_text()
    )

    for definitions in (
        openapi["components"]["schemas"],
        standalone["$defs"],
    ):
        target = definitions["Region"]["properties"]["cad_face_target"]
        assert target["anyOf"][1] == {"type": "null"}
        union = target["anyOf"][0]
        assert union["discriminator"]["propertyName"] == "resolution"
        assert set(union["discriminator"]["mapping"]) == {
            "resolved",
            "ambiguous",
            "unresolved",
            "legacy_local_only",
            "invalid_legacy_evidence",
        }
        resolved = definitions["ResolvedCadFaceTarget"]["properties"]
        ambiguous = definitions["AmbiguousCadFaceTarget"]["properties"]
        unresolved = definitions["UnresolvedCadFaceTarget"]["properties"]

        stable = resolved["stable_identities"]
        assert stable["minItems"] == 1
        assert stable["uniqueItems"] is True
        assert stable["items"] == {
            "maxLength": 69,
            "minLength": 69,
            "pattern": "^gfi1:[0-9a-f]{64}$",
            "type": "string",
        }

        collisions = ambiguous["collision_group_ids"]
        assert collisions["minItems"] == 1
        assert collisions["uniqueItems"] is True
        assert collisions["items"] == {
            "maxLength": 64,
            "minLength": 64,
            "pattern": "^[0-9a-f]{64}$",
            "type": "string",
        }

        for properties in (resolved, ambiguous, unresolved):
            tags = properties["source_face_tags"]
            assert tags["minItems"] == 1
            assert tags["uniqueItems"] is True
            assert tags["items"]["minimum"] == 1
            assert tags["items"]["type"] == "integer"
        invalid_legacy = definitions["InvalidLegacyCadFaceTarget"][
            "properties"
        ]["source_face_tags"]
        assert "uniqueItems" not in invalid_legacy
        assert "minimum" not in invalid_legacy["items"]
        assert invalid_legacy["items"]["type"] == "integer"
        entity_ids = definitions["Region"]["properties"]["entity_ids"]
        array_branches = [
            branch for branch in entity_ids["anyOf"]
            if branch.get("type") == "array"
        ]
        assert len(array_branches) == 2
        assert all(branch["minItems"] == 1 for branch in array_branches)
        assert all("minLength" not in branch for branch in array_branches)
        assert definitions["Region"]["allOf"][0]["then"] == {
            "not": {"required": ["entity_ids"]}
        }


def test_generated_contracts_publish_cad_authority_semantics():
    root = Path(__file__).parents[1]
    openapi = json.loads((root / "schema" / "openapi.json").read_text())
    standalone = json.loads(
        (root / "schema" / "simulation-intent.schema.json").read_text()
    )
    required_markers = (
        "authoritative",
        "exact bound ModelVersion",
        "persisted geometry-identity artifact",
        "non-authoritative",
        "rebind",
        "unordered membership",
        "sole public numeric",
        "cannot be confirmed or exported",
        "CAD-to-mesh mapping",
        "no cross-version identity transfer",
    )
    for definitions in (
        openapi["components"]["schemas"],
        standalone["$defs"],
    ):
        descriptions = " ".join(
            [
                definitions["Region"]["properties"]["cad_face_target"][
                    "description"
                ],
                definitions["Region"]["properties"]["entity_ids"][
                    "description"
                ],
                definitions["ResolvedCadFaceTarget"]["properties"][
                    "stable_identities"
                ]["description"],
                definitions["ResolvedCadFaceTarget"]["properties"][
                    "source_face_tags"
                ]["description"],
            ]
        )
        for marker in required_markers:
            assert marker in descriptions
    generated_typescript = (
        root / "schema" / "generated" / "typescript" / "api-types.ts"
    ).read_text()
    for marker in required_markers:
        assert marker in generated_typescript


def test_current_v3_forged_legacy_and_contradictory_unresolved_are_rejected(
    tmp_path,
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        base = _cad_intent(version_id, artifact)
        forged = deepcopy(base)
        original_tags = forged["regions"][0]["cad_face_target"][
            "source_face_tags"
        ]
        forged["regions"][0]["cad_face_target"] = {
            "resolution": "legacy_local_only",
            "legacy_status": "confirmed",
            "source_face_tags": original_tags,
        }
        response = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "forged-legacy",
                "intent": forged,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "cad_region_legacy_client_forbidden"

        contradictory = deepcopy(base)
        contradictory["regions"][0]["entity_ids"] = contradictory["regions"][1][
            "cad_face_target"
        ]["source_face_tags"]
        response = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "contradictory-unresolved",
                "intent": contradictory,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "cad_region_entity_ids_forbidden"


def test_raw_v2_cad_cannot_confirm_or_export_through_production_paths():
    body = intent_payload()
    body["schema_version"] = 2
    body["regions"][0]["entity_type"] = "cad_face"
    body["regions"][0]["entity_ids"] = [1]
    body["regions"][0].pop("cad_face_target", None)
    migrated = load_simulation_intent(body, source="v2")
    assert migrated.regions[0].cad_face_target.resolution == "legacy_local_only"
    with pytest.raises(ExportBlockedError):
        migrated.export_payload()

    raw_region = Region.model_construct(
        id="raw",
        entity_type="cad_face",
        entity_ids=[1],
        selection_method="user_confirmed",
        confidence=1.0,
        source_instruction="raw",
        status="confirmed",
        cad_face_target=None,
    )
    hostile = SimulationIntent.model_construct(
        **{
            **SimulationIntent.model_validate(intent_payload()).model_dump(
                mode="python"
            ),
            "schema_version": 2,
            "regions": [raw_region],
            "bcs": [],
            "loads": [],
        }
    )
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        hostile.export_payload()


@pytest.mark.parametrize("model_kind", ["step", "inp"])
@pytest.mark.parametrize(
    "target",
    [
        None,
        {
            "resolution": "unresolved",
            "model_version_id": "forged-version",
            "source_face_tags": [1],
        },
        {
            "resolution": "ambiguous",
            "model_version_id": "forged-version",
            "artifact_sha256": "a" * 64,
            "collision_group_ids": ["b" * 64],
            "source_face_tags": [1],
        },
        {
            "resolution": "resolved",
            "model_version_id": "forged-version",
            "artifact_sha256": "a" * 64,
            "stable_identities": ["gfi1:" + "b" * 64],
            "source_face_tags": [1],
        },
    ],
    ids=["missing", "unresolved", "ambiguous", "forged-resolved"],
)
def test_volatile_session_cad_confirmation_never_mutates(
    tmp_path, model_kind, target
):
    app = create_app(
        tmp_path / f"legacy-{model_kind}",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / f"data-{model_kind}"),
    )
    content = STEP.read_bytes() if model_kind == "step" else minimal_inp()
    suffix = "step" if model_kind == "step" else "inp"
    record = app.state.model_store.add(f"part.{suffix}", content)
    with TestClient(app) as client:
        session_id = record.model_id
        body = intent_payload()
        body["regions"][0]["entity_type"] = "cad_face"
        body["regions"][0].pop("entity_ids", None)
        if target is None:
            body["regions"][0]["cad_face_target"] = {
                "resolution": "unresolved",
                "source_face_tags": [1],
            }
        else:
            body["regions"][0]["cad_face_target"] = deepcopy(target)
        saved = client.put(f"/session/{session_id}/intent", json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["export_eligible"] is False
        confirm = client.post(
            f"/session/{session_id}/confirm_region",
            json={"region_id": body["regions"][0]["id"]},
        )
        assert confirm.status_code == 409
        reopened = client.get(f"/session/{session_id}/intent")
        assert reopened.json()["intent"]["regions"][0]["status"] == "proposed"


@pytest.mark.parametrize("boundary", ["insert", "flush", "commit"])
def test_setup_database_failures_are_sanitized_and_atomic(
    tmp_path, monkeypatch, boundary
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        upload = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={
                "file": (
                    "part.inp",
                    minimal_inp(),
                    "application/octet-stream",
                )
            },
        ).json()
        persistence = app.state.persistence
        if boundary == "insert":
            original_add = Session.add

            def fail_add(session, instance, *args, **kwargs):
                if instance.__class__.__name__ == "SimulationSetup":
                    raise SQLAlchemyError("sensitive insertion details")
                return original_add(session, instance, *args, **kwargs)

            monkeypatch.setattr(Session, "add", fail_add)
        elif boundary == "flush":
            monkeypatch.setattr(
                Session,
                "flush",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    SQLAlchemyError("sensitive flush details")
                ),
            )
        else:
            original_transaction = persistence.transaction

            @contextmanager
            def fail_commit():
                with original_transaction() as session:
                    yield session
                    raise SQLAlchemyError("sensitive commit details")

            monkeypatch.setattr(persistence, "transaction", fail_commit)
        response = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": upload["model_id"],
                "model_version_id": upload["model_version"]["id"],
                "request_id": boundary,
                "intent": intent_payload(),
            },
        )
        assert response.status_code == 500
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert response.json()["code"] == "setup_database_write_failed"
        assert "sensitive" not in response.text
        with persistence.engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM simulation_setups "
                    "WHERE project_id=:project_id"
                ),
                {"project_id": project_id},
            ) == 0


def _browser_unresolved_cad_intent(version_id: str, region_id: str) -> dict:
    """The exact browser payload a STEP viewer click submits.

    No stable identity, no artifact digest and no ``entity_ids`` — only an
    unresolved claim, which is what forces the route-layer resolution reads.
    """

    body = intent_payload()
    body["regions"] = [
        {
            "id": region_id,
            "entity_type": "cad_face",
            "cad_face_target": {
                "resolution": "unresolved",
                "model_version_id": version_id,
                "source_face_tags": [1],
            },
            "selection_method": "user_click",
            "confidence": 1.0,
            "source_instruction": "Use selected viewer face_1.",
            "status": "proposed",
        }
    ]
    body["bcs"] = []
    body["loads"] = []
    return body


def _durable_counts(persistence, project_id: str) -> tuple[int, int]:
    with persistence.engine.connect() as connection:
        setups = connection.scalar(
            text(
                "SELECT count(*) FROM simulation_setups "
                "WHERE project_id=:project_id"
            ),
            {"project_id": project_id},
        )
        revisions = connection.scalar(
            text(
                "SELECT count(*) FROM setup_revisions WHERE setup_id IN "
                "(SELECT id FROM simulation_setups WHERE project_id=:pid)"
            ),
            {"pid": project_id},
        )
    return setups, revisions


@pytest.mark.parametrize("route", ["create", "mutate"])
def test_cad_resolution_database_failures_are_sanitized_and_atomic(
    tmp_path, monkeypatch, route
):
    """A database outage on the route-layer CAD resolution reads stays RFC 9457.

    Resolving an unresolved ``cad_face_target`` reads the ModelVersion, the
    setup and the persisted geometry-identity artifact *before* anything is
    written. The setup routes recognize ``PersistenceDatabaseError`` alone, so
    an unwrapped ``SQLAlchemyError`` here answered an unsanitized
    ``text/plain`` 500 — the one path R4b.3 exists to enable, and the one the
    INP-shaped parametrisation above can never reach.
    """

    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        assert uploaded["model_version"]["model_kind"] == "step"
        persistence = app.state.persistence

        if route == "mutate":
            base = intent_payload()
            base["regions"] = []
            base["bcs"] = []
            base["loads"] = []
            created = client.post(
                f"/api/v1/projects/{project_id}/setups",
                json={
                    "model_id": uploaded["model_id"],
                    "model_version_id": version_id,
                    "request_id": "base",
                    "intent": base,
                },
            )
            assert created.status_code == 201, created.text
            setup_id = created.json()["setup"]["id"]

        before = _durable_counts(persistence, project_id)

        # Exactly the read the resolution path performs on this route.
        failing = "ModelVersion" if route == "create" else "SimulationSetup"
        original_get = Session.get

        def fail_get(session, entity, *args, **kwargs):
            if getattr(entity, "__name__", "") == failing:
                raise SQLAlchemyError(
                    "sensitive resolution read details: "
                    "SELECT * FROM model_versions WHERE id='...'"
                )
            return original_get(session, entity, *args, **kwargs)

        monkeypatch.setattr(Session, "get", fail_get)

        intent = _browser_unresolved_cad_intent(version_id, "clicked_region")
        if route == "create":
            response = client.post(
                f"/api/v1/projects/{project_id}/setups",
                json={
                    "model_id": uploaded["model_id"],
                    "model_version_id": version_id,
                    "request_id": "resolution-failure",
                    "intent": intent,
                },
            )
        else:
            response = client.post(
                f"/api/v1/setups/{setup_id}/revisions",
                json={
                    "expected_revision": 1,
                    "request_id": "resolution-failure",
                    "intent": intent,
                },
            )

        assert response.status_code == 500, response.text
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        problem = response.json()
        assert problem["code"] == "setup_database_write_failed"
        assert problem["status"] == 500
        assert problem["retryable"] is True
        assert isinstance(problem["trace_id"], str) and problem["trace_id"]
        body = response.text
        for leaked in (
            "sensitive",
            "SELECT",
            "Traceback",
            "sqlalchemy",
            "cad_face_target",
            str(tmp_path),
            tmp_path.as_posix(),
        ):
            assert leaked not in body, leaked

        monkeypatch.undo()
        assert _durable_counts(persistence, project_id) == before
        if route == "mutate":
            reopened = client.get(f"/api/v1/setups/{setup_id}")
            assert reopened.status_code == 200
            assert reopened.json()["current"]["revision"] == 1
            assert reopened.json()["current"]["intent"]["regions"] == []


def test_independent_connection_create_and_mutation_races_are_classified(
    tmp_path,
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project_id = _project(client)
        upload = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={
                "file": (
                    "part.inp",
                    minimal_inp(),
                    "application/octet-stream",
                )
            },
        ).json()
        first = app.state.persistence
        database_path = config.database_path.as_posix()
        second_engine = create_sqlite_engine(
            f"sqlite:///{database_path}?timeout=10"
        )
        second = Persistence(second_engine, first.blobs)
        intent = SimulationIntent.model_validate(intent_payload())

        def create(persistence):
            return persistence.create_setup(
                project_id=project_id,
                model_id=upload["model_id"],
                model_version_id=upload["model_version"]["id"],
                intent=intent,
                request_id="cross-worker-create",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            created = list(executor.map(create, (first, second)))
        assert created[0][0].id == created[1][0].id

        setup, _ = first.create_setup(
            project_id=project_id,
            model_id=upload["model_id"],
            model_version_id=upload["model_version"]["id"],
            intent=intent,
            request_id="mutation-identical-base",
        )

        def mutate_same(persistence):
            return persistence.mutate_setup(
                setup_id=setup.id,
                expected_revision=1,
                request_id="same-mutation",
                mutation_type="intent_updated",
                intent=intent,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            revisions = list(executor.map(mutate_same, (first, second)))
        assert revisions[0].id == revisions[1].id

        conflict_setup, _ = first.create_setup(
            project_id=project_id,
            model_id=upload["model_id"],
            model_version_id=upload["model_version"]["id"],
            intent=intent,
            request_id="mutation-conflict-base",
        )

        def mutate_different(item):
            persistence, request_id = item
            try:
                return persistence.mutate_setup(
                    setup_id=conflict_setup.id,
                    expected_revision=1,
                    request_id=request_id,
                    mutation_type="intent_updated",
                    intent=intent,
                )
            except SetupRevisionConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    mutate_different,
                    ((first, "worker-a"), (second, "worker-b")),
                )
            )
        assert sum(value == "conflict" for value in outcomes) == 1
        second_engine.dispose()


def test_historical_v2_response_names_stored_and_materialized_hashes(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    first_app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(first_app) as client:
        project_id = _project(client)
        upload = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={
                "file": (
                    "part.inp",
                    minimal_inp(),
                    "application/octet-stream",
                )
            },
        ).json()
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": upload["model_id"],
                "model_version_id": upload["model_version"]["id"],
                "request_id": "historical",
                "intent": intent_payload(),
            },
        ).json()
        setup_id = created["setup"]["id"]
        v2 = intent_payload()
        v2["schema_version"] = 2
        raw = json.dumps(v2, sort_keys=True, separators=(",", ":"))
        stored_digest = hashlib.sha256(raw.encode()).hexdigest()
        with first_app.state.persistence.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER setup_revisions_immutable"))
            connection.execute(
                text(
                    "UPDATE setup_revisions SET schema_version=2, "
                    "intent_json=:raw, intent_sha256=:digest "
                    "WHERE setup_id=:setup_id"
                ),
                {
                    "raw": raw,
                    "digest": stored_digest,
                    "setup_id": setup_id,
                },
            )

    restarted = create_app(
        tmp_path / "restart", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted) as client:
        current = client.get(f"/api/v1/setups/{setup_id}").json()["current"]
        assert current["stored_simulation_intent_schema_version"] == 2
        assert current["stored_intent_sha256"] == stored_digest
        assert current["simulation_intent_schema_version"] == 3
        returned = SimulationIntent.model_validate(current["intent"])
        assert current["intent_sha256"] == canonical_intent(returned)[1]


def _normalized_sql(value: str) -> str:
    """Ignore only whitespace, casing and statement-terminator formatting."""

    return "".join(value.lower().replace(";", "").split())


#: The exact trigger bodies migration 0002 installs.  Comparing normalized
#: ``sqlite_master.sql`` against these literals means a re-upgrade that
#: recreates a trigger with a *weakened* condition (a dropped ``NOT EXISTS``
#: clause, a narrowed ``UPDATE OF`` column list, a missing ``RAISE(ABORT)``)
#: fails here instead of passing a name-only check.
EXPECTED_SETUP_TRIGGER_SQL: dict[str, str] = {
    "simulation_setups_lineage_insert": """
    CREATE TRIGGER simulation_setups_lineage_insert
    BEFORE INSERT ON simulation_setups
    WHEN NOT EXISTS (
      SELECT 1 FROM models m JOIN model_versions v ON v.model_id=m.id
      WHERE m.id=NEW.model_id AND m.project_id=NEW.project_id
        AND v.id=NEW.model_version_id
    )
    BEGIN SELECT RAISE(ABORT, 'invalid setup lineage'); END
    """,
    "simulation_setups_lineage_update": """
    CREATE TRIGGER simulation_setups_lineage_update
    BEFORE UPDATE OF project_id, model_id, model_version_id ON simulation_setups
    BEGIN SELECT RAISE(ABORT, 'setup lineage is immutable'); END
    """,
    "simulation_setups_current_revision_insert": """
    CREATE TRIGGER simulation_setups_current_revision_insert
    BEFORE INSERT ON simulation_setups
    WHEN NEW.current_revision IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM setup_revisions r
      WHERE r.setup_id=NEW.id AND r.revision=NEW.current_revision
    )
    BEGIN SELECT RAISE(ABORT, 'invalid current setup revision'); END
    """,
    "simulation_setups_current_revision": """
    CREATE TRIGGER simulation_setups_current_revision
    BEFORE UPDATE OF current_revision ON simulation_setups
    WHEN NEW.current_revision IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM setup_revisions r
      WHERE r.setup_id=NEW.id AND r.revision=NEW.current_revision
    )
    BEGIN SELECT RAISE(ABORT, 'invalid current setup revision'); END
    """,
    "setup_revisions_immutable": """
    CREATE TRIGGER setup_revisions_immutable
    BEFORE UPDATE ON setup_revisions
    BEGIN SELECT RAISE(ABORT, 'setup_revisions are immutable'); END
    """,
    "setup_revisions_sequential": """
    CREATE TRIGGER setup_revisions_sequential
    BEFORE INSERT ON setup_revisions
    WHEN (NEW.revision=1 AND NEW.parent_revision_id IS NOT NULL)
      OR (NEW.revision>1 AND NOT EXISTS (
        SELECT 1 FROM setup_revisions p
        WHERE p.id=NEW.parent_revision_id AND p.setup_id=NEW.setup_id
          AND p.revision=NEW.revision-1
      ))
    BEGIN SELECT RAISE(ABORT, 'invalid setup revision parent'); END
    """,
}

_MIGRATION_TABLES = (
    "projects",
    "models",
    "model_versions",
    "simulation_setups",
    "setup_revisions",
)

_INSERT_REVISION = (
    "INSERT INTO setup_revisions (id, setup_id, revision, parent_revision_id, "
    "schema_version, intent_json, intent_sha256, mutation_type, request_id, "
    "mutation_sha256, created_at) VALUES (:id, :setup, :revision, :parent, 2, "
    "'{}', :digest, 'intent_updated', :request, :mutation, CURRENT_TIMESTAMP)"
)

_INSERT_SETUP = (
    "INSERT INTO simulation_setups (id, project_id, model_id, "
    "model_version_id, current_revision, create_request_id, "
    "create_request_sha256, created_at, updated_at) "
    "VALUES (:id, :project, :model, :version, :current, :request, :digest, "
    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)


def _schema_snapshot(engine) -> dict:
    inspector = inspect(engine)
    return {
        table: {
            "columns": tuple(
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns(table)
            ),
            "indexes": tuple(
                sorted(
                    (index["name"], tuple(index["column_names"]))
                    for index in inspector.get_indexes(table)
                )
            ),
            "foreign_keys": tuple(
                sorted(
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                    for fk in inspector.get_foreign_keys(table)
                )
            ),
        }
        for table in _MIGRATION_TABLES
    }


def _trigger_snapshot(connection) -> dict[str, str]:
    return {
        name: _normalized_sql(sql)
        for name, sql in connection.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='trigger' ORDER BY name"
            )
        )
    }


def _data_snapshot(connection) -> dict:
    return {
        "setups": connection.execute(
            text(
                "SELECT id, project_id, model_id, model_version_id, "
                "current_revision, create_request_id, create_request_sha256, "
                "is_stale, stale_reason FROM simulation_setups ORDER BY id"
            )
        ).all(),
        "revisions": connection.execute(
            text(
                "SELECT id, setup_id, revision, parent_revision_id, "
                "schema_version, intent_json, intent_sha256, mutation_type, "
                "request_id, mutation_sha256 FROM setup_revisions "
                "ORDER BY setup_id, revision"
            )
        ).all(),
        "models": connection.execute(
            text(
                "SELECT id, project_id, current_version_id FROM models "
                "ORDER BY id"
            )
        ).all(),
        "model_versions": connection.execute(
            text(
                "SELECT id, model_id, version, source_sha256, source_name, "
                "model_kind, is_superseded FROM model_versions ORDER BY id"
            )
        ).all(),
    }


def _blocked(engine, sql: str, params: dict | None = None) -> str:
    """Assert the *database* refuses ``sql`` and return the abort message."""

    with pytest.raises(IntegrityError) as caught:
        with engine.begin() as connection:
            connection.execute(text(sql), params or {})
    return str(caught.value)


def test_populated_v2_downgrade_and_reupgrade_are_safe(tmp_path):
    """Populated 0005 downgrade/re-upgrade preserves schema, data and teeth.

    The database is populated throughout: two projects, two models, two exact
    ModelVersions, a two-revision setup lineage with a current pointer, plus a
    second setup used for ownership probes.  After returning to head every
    structural definition, every historical byte, and every enforcement
    behaviour is re-verified against the pre-downgrade state.
    """

    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project_id = _project(client)
        upload = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={
                "file": ("part.inp", minimal_inp(), "application/octet-stream")
            },
        ).json()
        model_id = upload["model_id"]
        version_id = upload["model_version"]["id"]
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": model_id,
                "model_version_id": version_id,
                "request_id": "v2-downgrade",
                "intent": intent_payload(),
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]

        revised = intent_payload()
        revised["regions"][0]["source_instruction"] = "Revised instruction."
        second = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": 1,
                "request_id": "v2-downgrade-r2",
                "intent": revised,
            },
        )
        assert second.status_code == 201, second.text
        assert second.json()["revision"] == 2

        # A second, independent lineage for the ownership probes.
        other_project_id = client.post(
            "/api/v1/projects", json={"name": "r4b2-other"}
        ).json()["id"]
        other_upload = client.post(
            f"/api/v1/projects/{other_project_id}/models",
            files={
                "file": ("part.inp", minimal_inp(), "application/octet-stream")
            },
        ).json()
        other_model_id = other_upload["model_id"]
        other_version_id = other_upload["model_version"]["id"]

        with app.state.persistence.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER setup_revisions_immutable"))
            connection.execute(
                text("UPDATE setup_revisions SET schema_version=2")
            )
            connection.execute(
                text(
                    "CREATE TRIGGER setup_revisions_immutable "
                    "BEFORE UPDATE ON setup_revisions "
                    "BEGIN SELECT RAISE(ABORT, "
                    "'setup_revisions are immutable'); END"
                )
            )

    migration = alembic_config(config.database_url)
    engine = create_sqlite_engine(config.database_url)
    before_schema = _schema_snapshot(engine)
    with engine.connect() as connection:
        before_triggers = _trigger_snapshot(connection)
        before_data = _data_snapshot(connection)
    engine.dispose()

    assert len(before_data["revisions"]) == 2
    assert before_data["setups"][0][4] == 2  # current_revision advanced
    revision_ids = {row[2]: row[0] for row in before_data["revisions"]}
    assert before_data["revisions"][1][3] == revision_ids[1]  # parent link
    historical_intents = {row[2]: row[5] for row in before_data["revisions"]}
    historical_hashes = {row[2]: row[6] for row in before_data["revisions"]}
    historical_audit = {
        row[2]: (row[7], row[8], row[9]) for row in before_data["revisions"]
    }
    assert historical_intents[1] != historical_intents[2]

    # ---------------- populated downgrade ----------------
    command.downgrade(migration, "0004_geometry_identity_artifacts")
    engine = create_sqlite_engine(config.database_url)
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0004_geometry_identity_artifacts"
        assert connection.scalar(
            text("SELECT count(*) FROM setup_revisions")
        ) == 2
        assert connection.scalar(
            text("SELECT count(*) FROM simulation_setups")
        ) == 1
    engine.dispose()

    # ---------------- populated re-upgrade ----------------
    command.upgrade(migration, "head")
    engine = create_sqlite_engine(config.database_url)

    # --- schema and trigger definitions ---
    assert _schema_snapshot(engine) == before_schema
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == "0005_stable_cad_region_references"
        after_triggers = _trigger_snapshot(connection)
        after_data = _data_snapshot(connection)
    assert after_triggers == before_triggers
    assert set(EXPECTED_SETUP_TRIGGER_SQL).issubset(after_triggers)
    for name, expected in EXPECTED_SETUP_TRIGGER_SQL.items():
        assert after_triggers[name] == _normalized_sql(expected), name
    # Guard the guard: a body that lost its condition must not compare equal.
    weakened = EXPECTED_SETUP_TRIGGER_SQL["setup_revisions_sequential"].replace(
        "AND p.revision=NEW.revision-1", ""
    )
    assert after_triggers["setup_revisions_sequential"] != _normalized_sql(
        weakened
    )

    # --- historical data preservation ---
    assert after_data == before_data
    setup_row = after_data["setups"][0]
    assert setup_row[0] == setup_id
    assert setup_row[1] == project_id            # project ownership
    assert setup_row[2] == model_id              # model ownership
    assert setup_row[3] == version_id            # exact ModelVersion binding
    assert setup_row[4] == 2                     # current revision pointer
    for revision_number, row in zip((1, 2), after_data["revisions"]):
        assert row[2] == revision_number
        assert row[1] == setup_id
        assert row[5] == historical_intents[revision_number]   # intent JSON
        assert row[6] == historical_hashes[revision_number]    # intent hash
        assert (row[7], row[8], row[9]) == historical_audit[revision_number]
        # The stored column was forced to 2 to permit the downgrade; the
        # historical document itself is preserved byte-for-byte, including
        # the schema version recorded inside it.
        assert row[4] == 2
        assert json.loads(row[5]) == json.loads(
            historical_intents[revision_number]
        )
    assert after_data["revisions"][0][3] is None
    assert after_data["revisions"][1][3] == revision_ids[1]
    assert (
        hashlib.sha256(
            after_data["revisions"][1][5].encode("utf-8")
        ).hexdigest()
        == historical_hashes[2]
    )

    # --- enforcement behaviour after re-upgrade ---
    # Sequential-parent enforcement.
    assert "invalid setup revision parent" in _blocked(
        engine,
        _INSERT_REVISION,
        {
            "id": "probe-skip",
            "setup": setup_id,
            "revision": 3,
            "parent": revision_ids[1],
            "digest": "e" * 64,
            "request": "probe-skip",
            "mutation": "f" * 64,
        },
    )
    assert "invalid setup revision parent" in _blocked(
        engine,
        _INSERT_REVISION,
        {
            "id": "probe-root",
            "setup": setup_id,
            "revision": 1,
            "parent": revision_ids[2],
            "digest": "e" * 64,
            "request": "probe-root",
            "mutation": "f" * 64,
        },
    )

    # Lineage immutability.
    for column, value in (
        ("project_id", other_project_id),
        ("model_id", other_model_id),
        ("model_version_id", other_version_id),
    ):
        assert "setup lineage is immutable" in _blocked(
            engine,
            f"UPDATE simulation_setups SET {column}=:value WHERE id=:id",
            {"value": value, "id": setup_id},
        )

    # Setup ownership mismatch: a model owned by another project.
    assert "invalid setup lineage" in _blocked(
        engine,
        _INSERT_SETUP,
        {
            "id": "probe-cross-owner",
            "project": project_id,
            "model": other_model_id,
            "version": other_version_id,
            "current": None,
            "request": "probe-cross-owner",
            "digest": "a" * 64,
        },
    )
    # Exact ModelVersion ownership mismatch: a version owned by another model.
    # Two independent triggers stand in the way (lineage ownership from 0002
    # and current-source integrity from 0003); SQLite may fire either first,
    # so the assertion is that the database refuses with one of them.
    cross_version = _blocked(
        engine,
        _INSERT_SETUP,
        {
            "id": "probe-cross-version",
            "project": other_project_id,
            "model": other_model_id,
            "version": version_id,
            "current": None,
            "request": "probe-cross-version",
            "digest": "a" * 64,
        },
    )
    assert (
        "invalid setup lineage" in cross_version
        or "setup source is superseded" in cross_version
    ), cross_version

    # Current-pointer enforcement, on update and on insert.
    assert "invalid current setup revision" in _blocked(
        engine,
        "UPDATE simulation_setups SET current_revision=99 WHERE id=:id",
        {"id": setup_id},
    )
    assert "invalid current setup revision" in _blocked(
        engine,
        _INSERT_SETUP,
        {
            "id": "probe-pointer",
            "project": other_project_id,
            "model": other_model_id,
            "version": other_version_id,
            "current": 7,
            "request": "probe-pointer",
            "digest": "a" * 64,
        },
    )

    # Revision immutability: every stored column, not just one.
    for column, value in (
        ("intent_json", "{}"),
        ("intent_sha256", "0" * 64),
        ("mutation_type", "changed"),
        ("schema_version", "3"),
    ):
        assert "setup_revisions are immutable" in _blocked(
            engine,
            f"UPDATE setup_revisions SET {column}=:value WHERE id=:id",
            {"value": value, "id": revision_ids[2]},
        )

    # Valid next revision creation and a valid current-pointer advance.
    with engine.begin() as connection:
        connection.execute(
            text(_INSERT_REVISION),
            {
                "id": "probe-valid-3",
                "setup": setup_id,
                "revision": 3,
                "parent": revision_ids[2],
                "digest": "e" * 64,
                "request": "probe-valid-3",
                "mutation": "f" * 64,
            },
        )
        connection.execute(
            text(
                "UPDATE simulation_setups SET current_revision=3 WHERE id=:id"
            ),
            {"id": setup_id},
        )
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT current_revision FROM simulation_setups WHERE id=:id"),
            {"id": setup_id},
        ) == 3
        # The historical revisions are untouched by the valid advance.
        assert connection.execute(
            text(
                "SELECT intent_json, intent_sha256 FROM setup_revisions "
                "WHERE id=:id"
            ),
            {"id": revision_ids[1]},
        ).one() == (historical_intents[1], historical_hashes[1])

    # Revision removal is not free-form: the parent foreign key cascades, so a
    # deleted ancestor takes its descendants with it rather than leaving an
    # orphaned lineage behind.  This runs last because it is destructive.
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM setup_revisions WHERE id=:id"),
            {"id": revision_ids[1]},
        )
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM setup_revisions WHERE setup_id=:id"),
            {"id": setup_id},
        ) == 0
    engine.dispose()


# --------------------------------------------------------------------------
# R4B2-AUDIT-01: explicit CAD ``entity_ids: null`` must be rejected, never
# accepted-and-silently-stripped.  The invariant is that a CAD region never
# carries the key at all, so mapping inputs are judged on key presence.
# --------------------------------------------------------------------------


def _cad_region_mapping_with_explicit_null() -> dict:
    """One CAD region mapping that explicitly carries ``entity_ids: null``."""

    region = _synthetic_resolved_cad_intent(source_face_tags=[1, 2])["regions"][0]
    assert "entity_ids" not in region
    region["entity_ids"] = None
    return region


def _intent_mapping_with_explicit_null_cad_entity_ids() -> dict:
    body = _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    body["regions"][0]["entity_ids"] = None
    return body


def _hostile_null_intent() -> SimulationIntent:
    """A SimulationIntent whose region survives as a raw hostile mapping.

    ``model_copy(update=...)`` performs no validation or coercion, so the
    region stays a mapping that still carries the forbidden key.  This is the
    only way the hostile state can reach an internal service boundary,
    because every deserializing boundary rejects the mapping outright.
    """

    valid = SimulationIntent.model_validate(
        _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    )
    return valid.model_copy(
        update={"regions": [_cad_region_mapping_with_explicit_null()]}
    )


def test_explicit_cad_entity_ids_null_rejected_by_direct_region_validation():
    """Boundary 1: direct Region validation on the hostile mapping."""

    hostile = _cad_region_mapping_with_explicit_null()
    with pytest.raises(
        ValidationError, match="cad_region_entity_ids_forbidden"
    ):
        Region.model_validate(hostile)
    with pytest.raises(
        ValidationError, match="cad_region_entity_ids_forbidden"
    ):
        Region(**hostile)
    with pytest.raises(
        ValidationError, match="cad_region_entity_ids_forbidden"
    ):
        TypeAdapter(list[Region]).validate_python([hostile])

    # The same mapping without the key remains valid, and validation never
    # resurrects the forbidden key on the way out.
    clean = dict(hostile)
    clean.pop("entity_ids")
    region = Region.model_validate(clean)
    assert region.entity_ids is None
    assert "entity_ids" not in region.model_dump(mode="json")


def test_explicit_cad_entity_ids_null_does_not_ban_non_cad_membership():
    """The key-presence rule must stay scoped to CAD-face regions."""

    non_cad = intent_payload()["regions"][0]
    assert non_cad["entity_type"] != "cad_face"
    region = Region.model_validate(non_cad)
    assert region.entity_ids == non_cad["entity_ids"]
    assert region.model_dump(mode="json")["entity_ids"] == non_cad["entity_ids"]

    # A non-CAD region still *requires* membership; an explicit null there is
    # rejected by the non-CAD contract, not by the CAD invariant.
    null_non_cad = dict(non_cad)
    null_non_cad["entity_ids"] = None
    with pytest.raises(ValidationError) as excinfo:
        Region.model_validate(null_non_cad)
    assert "cad_region_entity_ids_forbidden" not in str(excinfo.value)

    intent = SimulationIntent.model_validate(intent_payload())
    assert "cad_region_entity_ids_forbidden" not in {
        issue.code for issue in validate_intent(intent).issues
    }


def test_explicit_cad_entity_ids_null_rejected_by_intent_deserialization():
    """Boundary 2: complete intent deserialization."""

    body = _intent_mapping_with_explicit_null_cad_entity_ids()
    with pytest.raises(
        ValidationError, match="cad_region_entity_ids_forbidden"
    ):
        SimulationIntent.model_validate(body)
    with pytest.raises(Exception) as deserialization:
        load_simulation_intent(body, source="explicit null v3")
    assert deserialization.value.__class__.__name__ == "PayloadStructureError"

    # A legacy payload carrying the same explicit null is not silently
    # migrated into a seemingly clean CAD region either.
    legacy = deepcopy(body)
    legacy["schema_version"] = 2
    legacy["regions"][0].pop("cad_face_target", None)
    with pytest.raises(Exception) as legacy_deserialization:
        load_simulation_intent(legacy, source="explicit null v2")
    assert (
        legacy_deserialization.value.__class__.__name__
        == "PayloadStructureError"
    )


def test_explicit_cad_entity_ids_null_rejected_by_session_service():
    """Boundary 3: session service creation refuses the hostile state."""

    store = SelectionSessionStore()
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        store.save_intent("hostile-null", _hostile_null_intent())
    assert store._sessions.get("hostile-null") is None or (
        store._sessions["hostile-null"].intent is None
    )

    # The shared guard rejects the raw mapping wherever a service is handed
    # one directly, and rejects the nested intent shape as well.
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        enforce_cad_region_entity_ids_invariant(
            _cad_region_mapping_with_explicit_null()
        )
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        enforce_cad_region_entity_ids_invariant(
            _intent_mapping_with_explicit_null_cad_entity_ids()
        )


def test_explicit_cad_entity_ids_null_rejected_by_persistence(tmp_path):
    """Boundary 4: persistence create, mutate, canonicalization and read."""

    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        clean = SimulationIntent.model_validate(_cad_intent(version_id, artifact))
        hostile = clean.model_copy(
            update={
                "regions": [
                    {
                        **clean.regions[0].model_dump(mode="python"),
                        "entity_ids": None,
                    },
                    *clean.regions[1:],
                ]
            }
        )
        with pytest.raises(
            EngineeringConsistencyError,
            match="cad_region_entity_ids_forbidden",
        ):
            canonical_intent(hostile)
        with pytest.raises(
            CadRegionReferenceError,
            match="cad_region_entity_ids_forbidden",
        ):
            app.state.persistence.create_setup(
                project_id=project_id,
                model_id=uploaded["model_id"],
                model_version_id=version_id,
                intent=hostile,
                request_id="hostile-null-create",
            )

        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "clean-null-create",
                "intent": clean.model_dump(mode="json"),
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        with pytest.raises(
            CadRegionReferenceError,
            match="cad_region_entity_ids_forbidden",
        ):
            app.state.persistence.mutate_setup(
                setup_id=setup_id,
                expected_revision=1,
                request_id="hostile-null-update",
                mutation_type="intent_updated",
                intent=hostile,
            )

        # A tampered stored row is a mapping boundary: reading it back must
        # fail loudly rather than silently drop the forbidden key.
        with app.state.persistence.engine.begin() as connection:
            stored = json.loads(
                connection.scalar(
                    text(
                        "SELECT intent_json FROM setup_revisions "
                        "WHERE setup_id=:setup_id AND revision=1"
                    ),
                    {"setup_id": setup_id},
                )
            )
            stored["regions"][0]["entity_ids"] = None
            connection.execute(text("DROP TRIGGER setup_revisions_immutable"))
            connection.execute(
                text(
                    "UPDATE setup_revisions SET intent_json=:payload "
                    "WHERE setup_id=:setup_id AND revision=1"
                ),
                {"payload": json.dumps(stored), "setup_id": setup_id},
            )
            connection.execute(
                text(
                    "CREATE TRIGGER setup_revisions_immutable "
                    "BEFORE UPDATE ON setup_revisions "
                    "BEGIN SELECT RAISE(ABORT, "
                    "'setup_revisions are immutable'); END"
                )
            )
        revision = app.state.persistence.get_revision(setup_id, 1)
        with pytest.raises(Exception) as read_back:
            app.state.persistence.revision_intent(revision)
        assert read_back.value.__class__.__name__ == "PayloadStructureError"


def test_explicit_cad_entity_ids_null_rejected_by_confirmation_and_audit(
    tmp_path, monkeypatch
):
    """Boundaries 5 and 6: confirmation and audit serialization."""

    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        clean = SimulationIntent.model_validate(_cad_intent(version_id, artifact))
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "confirm-null-clean",
                "intent": clean.model_dump(mode="json"),
            },
        )
        assert created.status_code == 201, created.text
        hostile = clean.model_copy(
            update={
                "regions": [
                    {
                        **clean.regions[0].model_dump(mode="python"),
                        "entity_ids": None,
                    },
                    *clean.regions[1:],
                ]
            }
        )
        monkeypatch.setattr(
            app.state.persistence, "revision_intent", lambda _revision: hostile
        )
        confirm = client.post(
            f"/api/v1/setups/{created.json()['setup']['id']}/regions/"
            f"{clean.regions[0].id}/confirm",
            json={"expected_revision": 1, "request_id": "hostile-null-confirm"},
        )
        assert confirm.status_code == 422
        assert confirm.json()["code"] == "cad_region_entity_ids_forbidden"

    audit_app = create_app(tmp_path / "audit")
    record = audit_app.state.model_store.add("part.step", STEP.read_bytes())
    clean_audit = SimulationIntent.model_validate(
        _synthetic_resolved_cad_intent(source_face_tags=[1, 2])
    )
    audit_app.state.session_store.save_intent(record.model_id, clean_audit)
    audit_app.state.session_store._sessions[record.model_id].intent = (
        _hostile_null_intent()
    )
    with TestClient(audit_app, raise_server_exceptions=False) as client:
        audit = client.get(f"/session/{record.model_id}/audit")
        assert audit.status_code == 422
        assert audit.json()["code"] == "cad_region_entity_ids_forbidden"


def test_explicit_cad_entity_ids_null_rejected_by_export_path(tmp_path):
    """Boundary 7: the export path refuses and writes nothing."""

    hostile = _hostile_null_intent()
    with pytest.raises(
        EngineeringConsistencyError,
        match="cad_region_entity_ids_forbidden",
    ):
        hostile.export_payload()
    with pytest.raises(EngineeringConsistencyError):
        export_abaqus_py(
            hostile,
            CadModelMetadata(
                source_path=tmp_path / "never-created.step",
                source_name="never-created.step",
                source_sha256="0" * 64,
                source_cad_face_tags=(1, 2),
            ),
        )
    assert not list(tmp_path.iterdir())


def test_explicit_cad_entity_ids_null_rejected_by_http_setup_creation(tmp_path):
    """Boundary 8: HTTP setup creation persists nothing at all."""

    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        hostile = _cad_intent(version_id, artifact)
        hostile["regions"][0]["entity_ids"] = None

        response = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "hostile-null-http",
                "intent": hostile,
            },
        )
        assert response.status_code == 422, response.text
        problem = response.json()
        assert problem["code"] == "cad_region_entity_ids_forbidden"
        assert problem["title"] == "Invalid CAD region reference"
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )

        assert client.get(f"/api/v1/projects/{project_id}/setups").json() == []
        with app.state.persistence.engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM simulation_setups")
                )
                == 0
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM setup_revisions"))
                == 0
            )
