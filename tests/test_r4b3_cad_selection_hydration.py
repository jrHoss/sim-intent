"""Focused R4b.3 durable CAD-selection hydration and evidence regressions.

These tests reproduce the four measured breakages that made a durable CAD
selection unreachable through the product — a stale browser schema version, a
forbidden ``entity_ids`` projection on viewer-created CAD regions, viewer
clicks that no durable write path ever resolved, and a selection projection
that published source-local face tags as if they were authoritative membership.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import LocalDataConfig
from app.runtime_mode import RuntimeMode
from app.server import create_app
from geom.identity import GeometryFaceInput, build_geometry_identity
from ir.schema_version import SIMULATION_INTENT_SCHEMA_VERSION
from tests.test_project_persistence import minimal_inp


ROOT = Path(__file__).resolve().parents[1]
STEP = Path(__file__).parent / "fixtures" / "bracket.step"
STATIC = ROOT / "app" / "static"


def _app(tmp_path, **kwargs):
    return create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
        **kwargs,
    )


def _project(client: TestClient) -> str:
    response = client.post("/api/v1/projects", json={"name": "r4b3"})
    assert response.status_code == 201
    return response.json()["id"]


def _upload(
    client: TestClient,
    project_id: str,
    model_id: str | None = None,
    *,
    inp: bool = False,
) -> dict:
    path = (
        f"/api/v1/projects/{project_id}/models"
        if model_id is None
        else f"/api/v1/projects/{project_id}/models/{model_id}/versions"
    )
    native = minimal_inp()
    payload = (
        (
            "model.inp",
            native if isinstance(native, bytes) else native.encode(),
            "text/plain",
        )
        if inp
        else ("bracket.step", STEP.read_bytes(), "application/step")
    )
    response = client.post(path, files={"file": payload})
    assert response.status_code == 201, response.text
    return response.json()


def browser_blank_intent() -> dict:
    """The exact shape ``baseIntent()`` in engineering.js submits."""

    return {
        "schema_version": SIMULATION_INTENT_SCHEMA_VERSION,
        "analysis": {
            "type": "static_structural",
            "units": {"length": "mm", "force": "N", "stress": "MPa"},
            "dimensionality": None,
            "solver_target": None,
            "coordinate_system": None,
        },
        "materials": [],
        "regions": [],
        "bcs": [],
        "loads": [],
        "assumptions": [],
        "mesh_settings": None,
        "solver_settings": None,
        "validation_status": "unvalidated",
    }


def viewer_click_region(version_id: str, tag: int, region_id: str) -> dict:
    """The exact shape ``viewerCandidate()`` submits for a STEP click.

    No stable identity, no collision group, no artifact digest, and no
    ``entity_ids`` key at all — the browser is not an identity authority.
    """

    return {
        "id": region_id,
        "entity_type": "cad_face",
        "cad_face_target": {
            "resolution": "unresolved",
            "model_version_id": version_id,
            "source_face_tags": [tag],
        },
        "selection_method": "user_click",
        "confidence": 1,
        "source_instruction": f"Use selected viewer face_{tag}.",
        "status": "proposed",
    }


def browser_setup_with_click(version_id: str, tag: int, region_id: str) -> dict:
    intent = browser_blank_intent()
    intent["regions"] = [viewer_click_region(version_id, tag, region_id)]
    return intent


def _create(client, project_id, uploaded, intent, request_id):
    return client.post(
        f"/api/v1/projects/{project_id}/setups",
        json={
            "model_id": uploaded["model_id"],
            "model_version_id": uploaded["model_version"]["id"],
            "request_id": request_id,
            "intent": intent,
        },
    )


def _unique_tags(artifact: dict, count: int) -> list[int]:
    tags = [
        face["source_ref"] for face in artifact["faces"] if not face["ambiguous"]
    ]
    assert len(tags) >= count, "fixture does not expose enough unique faces"
    return tags[:count]


# ---------------------------------------------------------------------------
# F1 — the browser's own blank-setup payload must be writable at all.
# ---------------------------------------------------------------------------


def test_browser_shaped_blank_setup_is_accepted_at_schema_version_3(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        created = _create(
            client, project_id, uploaded, browser_blank_intent(), "blank"
        )
        assert created.status_code == 201, created.text
        current = created.json()["current"]
        assert current["simulation_intent_schema_version"] == 3
        assert current["cad_selection_evidence"] == {}


def test_browser_schema_version_constant_matches_the_server(tmp_path):
    source = (STATIC / "engineering.js").read_text(encoding="utf-8")
    match = re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", source)
    assert match is not None, "engineering.js no longer declares SCHEMA_VERSION"
    assert int(match.group(1)) == SIMULATION_INTENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# F2/F4 — an unresolved viewer click must be resolved by the backend and
# become confirmable, with no CAD entity_ids on any path.
# ---------------------------------------------------------------------------


def test_unresolved_viewer_click_is_resolved_and_becomes_confirmable(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        created = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(version_id, tag, "clicked_region"),
            "viewer-click",
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        current = created.json()["current"]

        region = current["intent"]["regions"][0]
        assert "entity_ids" not in region
        target = region["cad_face_target"]
        assert target["resolution"] == "resolved"
        assert target["model_version_id"] == version_id
        assert target["artifact_sha256"] == artifact["artifact_sha256"]
        assert target["stable_identities"]
        assert target["source_face_tags"] == [tag]

        evidence = current["cad_selection_evidence"]["clicked_region"]
        assert evidence["resolution"] == "resolved"
        assert evidence["stable_identity_authoritative"] is True
        assert evidence["viewer_binding_valid"] is True
        assert evidence["confirmable"] is True
        assert evidence["blocking_code"] is None
        assert evidence["artifact_sha256"] == artifact["artifact_sha256"]
        assert evidence["source_face_tags"] == [tag]
        assert evidence["viewer_node_names"] == [f"face_{tag}"]

        confirmed = client.post(
            f"/api/v1/setups/{setup_id}/regions/clicked_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        )
        assert confirmed.status_code == 201, confirmed.text
        after = confirmed.json()
        assert after["intent"]["regions"][0]["status"] == "confirmed"
        assert after["cad_selection_evidence"]["clicked_region"][
            "stable_identity_authoritative"
        ] is True
        # Already confirmed: nothing blocks it, and it is not re-confirmable.
        assert after["cad_selection_evidence"]["clicked_region"]["confirmable"] is False
        assert after["cad_selection_evidence"]["clicked_region"]["blocking_code"] is None


def test_distinct_clicked_faces_create_distinct_resolved_regions(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        first, second = _unique_tags(artifact, 2)
        intent = browser_blank_intent()
        intent["regions"] = [
            viewer_click_region(version_id, first, "region_first"),
            viewer_click_region(version_id, second, "region_second"),
        ]
        created = _create(client, project_id, uploaded, intent, "two-clicks")
        assert created.status_code == 201, created.text
        regions = created.json()["current"]["intent"]["regions"]
        assert [region["id"] for region in regions] == [
            "region_first",
            "region_second",
        ]
        identities = [
            tuple(region["cad_face_target"]["stable_identities"])
            for region in regions
        ]
        assert identities[0] != identities[1]
        assert regions[0]["cad_face_target"]["source_face_tags"] == [first]
        assert regions[1]["cad_face_target"]["source_face_tags"] == [second]


def test_no_cad_creation_path_emits_entity_ids(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        created = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(version_id, tag, "clicked_region"),
            "no-entity-ids",
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        for payload in (
            created.json()["current"],
            client.get(f"/api/v1/setups/{setup_id}").json()["current"],
            client.get(f"/api/v1/setups/{setup_id}/revisions").json()[0],
        ):
            for region in payload["intent"]["regions"]:
                if region["entity_type"] == "cad_face":
                    assert "entity_ids" not in region

        # A hostile client that re-adds the key is refused outright.
        forged = deepcopy(created.json()["current"]["intent"])
        forged["regions"][0]["entity_ids"] = [tag]
        rejected = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": 1,
                "request_id": "forged-entity-ids",
                "intent": forged,
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["code"] in {
            "cad_region_entity_ids_forbidden",
            "request_validation_failed",
        }
        assert "cad_region_entity_ids_forbidden" in rejected.text
        assert (
            client.get(f"/api/v1/setups/{setup_id}").json()["setup"][
                "current_revision"
            ]
            == 1
        )


# ---------------------------------------------------------------------------
# Exact binding, round trips, replay, and restart.
# ---------------------------------------------------------------------------


def test_confirmed_target_round_trips_unchanged_through_an_ordinary_edit(
    tmp_path,
):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        created = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(version_id, tag, "clicked_region"),
            "round-trip",
        )
        setup_id = created.json()["setup"]["id"]
        confirmed = client.post(
            f"/api/v1/setups/{setup_id}/regions/clicked_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        ).json()
        exact_target = deepcopy(confirmed["intent"]["regions"][0]["cad_face_target"])

        edited = deepcopy(confirmed["intent"])
        edited["materials"] = [
            {
                "name": "steel",
                "model": "linear_elastic_isotropic",
                "E_MPa": 210000,
                "nu": 0.3,
            }
        ]
        updated = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": confirmed["revision"],
                "request_id": "material-edit",
                "intent": edited,
            },
        )
        assert updated.status_code == 201, updated.text
        after = updated.json()["intent"]["regions"][0]
        assert after["cad_face_target"] == exact_target
        assert after["status"] == "confirmed"


def test_replay_of_the_same_request_id_is_deterministic_after_resolution(
    tmp_path,
):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        intent = browser_setup_with_click(version_id, tag, "clicked_region")
        first = _create(client, project_id, uploaded, intent, "replay-create")
        second = _create(client, project_id, uploaded, intent, "replay-create")
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["setup"]["id"] == second.json()["setup"]["id"]
        assert (
            first.json()["current"]["intent_sha256"]
            == second.json()["current"]["intent_sha256"]
        )
        assert len(client.get(
            f"/api/v1/projects/{project_id}/setups"
        ).json()) == 1

        setup_id = first.json()["setup"]["id"]
        edited = deepcopy(first.json()["current"]["intent"])
        edited["regions"].append(
            viewer_click_region(version_id, _unique_tags(artifact, 2)[1], "second")
        )
        body = {
            "expected_revision": 1,
            "request_id": "replay-mutate",
            "intent": edited,
        }
        one = client.post(f"/api/v1/setups/{setup_id}/revisions", json=body)
        two = client.post(f"/api/v1/setups/{setup_id}/revisions", json=body)
        assert one.status_code == 201, one.text
        assert two.status_code == 201, two.text
        assert one.json()["id"] == two.json()["id"]
        assert one.json()["intent_sha256"] == two.json()["intent_sha256"]
        assert len(
            client.get(f"/api/v1/setups/{setup_id}/revisions").json()
        ) == 2


def test_restart_and_reopen_preserve_identity_digest_binding_and_confirmation(
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
        tag = _unique_tags(artifact, 1)[0]
        created = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(version_id, tag, "clicked_region"),
            "restart",
        )
        setup_id = created.json()["setup"]["id"]
        confirmed = client.post(
            f"/api/v1/setups/{setup_id}/regions/clicked_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        ).json()
        before_target = deepcopy(confirmed["intent"]["regions"][0]["cad_face_target"])
        before_evidence = deepcopy(
            confirmed["cad_selection_evidence"]["clicked_region"]
        )
        before_digest = confirmed["stored_intent_sha256"]

    restarted = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted) as client:
        reopened = client.get(f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200, reopened.text
        current = reopened.json()["current"]
        assert current["intent"]["regions"][0]["cad_face_target"] == before_target
        assert current["intent"]["regions"][0]["status"] == "confirmed"
        assert current["stored_intent_sha256"] == before_digest
        assert (
            current["cad_selection_evidence"]["clicked_region"] == before_evidence
        )
        assert reopened.json()["setup"]["is_stale"] is False
        assert client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()["artifact_sha256"] == before_target["artifact_sha256"]


def test_stale_source_replacement_invalidates_binding_without_rebinding(
    tmp_path,
):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        created = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(version_id, tag, "clicked_region"),
            "stale",
        )
        setup_id = created.json()["setup"]["id"]
        confirmed = client.post(
            f"/api/v1/setups/{setup_id}/regions/clicked_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        ).json()
        bound_target = deepcopy(confirmed["intent"]["regions"][0]["cad_face_target"])

        successor = _upload(client, project_id, uploaded["model_id"])
        successor_id = successor["model_version"]["id"]
        assert successor_id != version_id

        view = client.get(f"/api/v1/setups/{setup_id}").json()
        assert view["setup"]["is_stale"] is True
        assert view["setup"]["stale_reason"] == "source_replaced"
        assert view["setup"]["model_version_id"] == version_id
        region = view["current"]["intent"]["regions"][0]
        # The historical record is preserved exactly; it is never rebound.
        assert region["cad_face_target"] == bound_target
        assert region["cad_face_target"]["model_version_id"] == version_id

        evidence = view["current"]["cad_selection_evidence"]["clicked_region"]
        assert evidence["stable_identity_authoritative"] is True
        assert evidence["viewer_binding_valid"] is False
        assert evidence["confirmable"] is False
        assert evidence["blocking_code"] == "setup_source_superseded"
        assert view["current"]["export_eligible"] is False

        blocked = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": view["current"]["revision"],
                "request_id": "after-stale",
                "intent": view["current"]["intent"],
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "setup_source_superseded"


# ---------------------------------------------------------------------------
# INP behaviour is untouched by geometry-identity resolution.
# ---------------------------------------------------------------------------


def test_inp_setups_never_reach_geometry_identity_resolution(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id, inp=True)
        created = _create(
            client, project_id, uploaded, browser_blank_intent(), "inp-blank"
        )
        assert created.status_code == 201, created.text
        assert "geometry_identity_not_applicable" not in created.text
        setup_id = created.json()["setup"]["id"]

        native = deepcopy(created.json()["current"]["intent"])
        native["regions"] = [
            {
                "id": "native_region",
                "entity_type": "mesh_face",
                "entity_ids": [1],
                "selection_method": "user_click",
                "confidence": 1,
                "source_instruction": "Use selected viewer face_1.",
                "status": "proposed",
            }
        ]
        updated = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json={
                "expected_revision": 1,
                "request_id": "inp-region",
                "intent": native,
            },
        )
        assert updated.status_code == 201, updated.text
        payload = updated.json()
        assert payload["intent"]["regions"][0]["entity_ids"] == [1]
        assert payload["selected_entities"]["native_region"] == [1]
        assert payload["cad_selection_evidence"] == {}
        assert "geometry_identity" not in payload["validation"]["issues"].__str__()


# ---------------------------------------------------------------------------
# Fail-closed evidence handling.
# ---------------------------------------------------------------------------


def test_wrong_version_and_wrong_artifact_targets_fail_closed(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        face = next(
            item for item in artifact["faces"] if item["source_ref"] == tag
        )

        other_project = _project(client)
        other = _upload(client, other_project)
        other_version_id = other["model_version"]["id"]

        wrong_version = browser_setup_with_click(
            other_version_id, tag, "clicked_region"
        )
        response = _create(
            client, project_id, uploaded, wrong_version, "wrong-version"
        )
        assert response.status_code == 422
        assert response.json()["code"] == "cad_region_model_version_mismatch"

        wrong_artifact = browser_blank_intent()
        wrong_artifact["regions"] = [
            {
                "id": "clicked_region",
                "entity_type": "cad_face",
                "cad_face_target": {
                    "resolution": "resolved",
                    "model_version_id": version_id,
                    "artifact_sha256": "0" * 64,
                    "stable_identities": [face["stable_identity"]],
                    "source_face_tags": [tag],
                },
                "selection_method": "user_click",
                "confidence": 1,
                "source_instruction": "forged digest",
                "status": "proposed",
            }
        ]
        response = _create(
            client, project_id, uploaded, wrong_artifact, "wrong-artifact"
        )
        assert response.status_code == 422
        assert response.json()["code"] == "cad_region_artifact_mismatch"

        forged_identity = deepcopy(wrong_artifact)
        forged_identity["regions"][0]["cad_face_target"]["artifact_sha256"] = (
            artifact["artifact_sha256"]
        )
        forged_identity["regions"][0]["cad_face_target"]["stable_identities"] = [
            "gfi1:" + "0" * 64
        ]
        response = _create(
            client, project_id, uploaded, forged_identity, "forged-identity"
        )
        assert response.status_code == 422
        assert response.json()["code"] == "cad_region_identity_unknown"

        inconsistent = deepcopy(wrong_artifact)
        inconsistent["regions"][0]["cad_face_target"]["artifact_sha256"] = (
            artifact["artifact_sha256"]
        )
        other_face = next(
            item
            for item in artifact["faces"]
            if item["source_ref"] != tag
            and not item["ambiguous"]
            and item["stable_identity"] is not None
        )
        inconsistent["regions"][0]["cad_face_target"]["stable_identities"] = [
            other_face["stable_identity"]
        ]
        response = _create(
            client, project_id, uploaded, inconsistent, "inconsistent"
        )
        assert response.status_code == 422
        assert (
            response.json()["code"]
            == "cad_region_identity_evidence_inconsistent"
        )

        # Nothing partial was written by any refusal above.
        assert client.get(
            f"/api/v1/projects/{project_id}/setups"
        ).json() == []


def test_problem_details_are_sanitized_and_specific(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]
        other = _upload(client, _project(client))
        wrong_version = browser_setup_with_click(
            other["model_version"]["id"], tag, "clicked_region"
        )
        problem = _create(
            client, project_id, uploaded, wrong_version, "sanitized"
        ).json()
        assert problem["code"] == "cad_region_model_version_mismatch"
        assert "ModelVersion" in problem["detail"]
        assert "trace_id" in problem
        body = json.dumps(problem)
        assert str(tmp_path) not in body
        assert "Traceback" not in body
        assert "sqlite" not in body.lower()


def test_missing_and_corrupt_artifacts_fail_closed(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        tag = _unique_tags(artifact, 1)[0]

        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER geometry_identity_artifacts_immutable")
            )
            connection.execute(
                text(
                    "UPDATE geometry_identity_artifacts "
                    "SET canonical_bytes=:raw "
                    "WHERE model_version_id=:version_id"
                ),
                {"raw": b"corrupted", "version_id": version_id},
            )
        response = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(version_id, tag, "clicked_region"),
            "corrupt",
        )
        assert response.status_code in {422, 500}
        assert response.json()["code"] in {
            "cad_region_artifact_integrity_failed",
            "geometry_identity_integrity_failed",
        }
        assert client.get(
            f"/api/v1/projects/{project_id}/setups"
        ).json() == []


def test_ambiguous_evidence_stays_proposed_and_unconfirmable(tmp_path):
    """Synthetic ambiguity: no frozen fixture yields a collision group.

    ``bracket.step`` resolves every face uniquely, so this replaces the stored
    artifact with a two-identical-face artifact rather than adding a fixture to
    the frozen evaluation corpus.
    """

    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
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
        ambiguous = next(face for face in artifact["faces"] if face["ambiguous"])

        created = _create(
            client,
            project_id,
            uploaded,
            browser_setup_with_click(
                version_id, ambiguous["source_ref"], "clicked_region"
            ),
            "ambiguous-click",
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        current = created.json()["current"]
        region = current["intent"]["regions"][0]
        assert region["status"] == "proposed"
        assert region["cad_face_target"]["resolution"] == "ambiguous"
        assert region["cad_face_target"]["collision_group_ids"] == [
            ambiguous["collision_group_id"]
        ]

        evidence = current["cad_selection_evidence"]["clicked_region"]
        assert evidence["resolution"] == "ambiguous"
        assert evidence["stable_identity_authoritative"] is False
        assert evidence["confirmable"] is False
        assert evidence["blocking_code"] == "cad_region_unresolved"
        assert evidence["collision_group_ids"] == [
            ambiguous["collision_group_id"]
        ]
        assert evidence["stable_identities"] == []

        confirm = client.post(
            f"/api/v1/setups/{setup_id}/regions/clicked_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm"},
        )
        assert confirm.status_code == 409
        assert confirm.json()["code"] == "cad_region_unresolved"


# ---------------------------------------------------------------------------
# Projected authority never outlives the evidence that authorizes it.
# ---------------------------------------------------------------------------


def _two_region_setup(client, project_id, uploaded, request_id) -> tuple[str, dict]:
    """Create a STEP setup with two independently resolved clicked faces."""

    version_id = uploaded["model_version"]["id"]
    artifact = client.get(
        f"/api/v1/model-versions/{version_id}/geometry-identity"
    ).json()
    first, second = _unique_tags(artifact, 2)
    intent = browser_blank_intent()
    intent["regions"] = [
        viewer_click_region(version_id, first, "region_healthy"),
        viewer_click_region(version_id, second, "region_offender"),
    ]
    created = _create(client, project_id, uploaded, intent, request_id)
    assert created.status_code == 201, created.text
    for evidence in created.json()["current"]["cad_selection_evidence"].values():
        assert evidence["stable_identity_authoritative"] is True
        assert evidence["viewer_binding_valid"] is True
        assert evidence["blocking_code"] is None
    return created.json()["setup"]["id"], artifact


def test_corrupt_artifact_withdraws_authority_from_every_bound_region(tmp_path):
    """Integrity failure is artifact-scoped: no region bound to it stays true.

    The projection previously derived authority from ``resolution`` alone, so
    a corrupt artifact published ``stable_identity_authoritative: true`` and
    ``viewer_binding_valid: true`` next to its own integrity-failure blocking
    code.
    """

    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        setup_id, _artifact = _two_region_setup(
            client, project_id, uploaded, "corrupt-projection"
        )

        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER geometry_identity_artifacts_immutable")
            )
            connection.execute(
                text(
                    "UPDATE geometry_identity_artifacts "
                    "SET canonical_bytes=:raw "
                    "WHERE model_version_id=:version_id"
                ),
                {"raw": b"corrupted", "version_id": version_id},
            )

        view = client.get(f"/api/v1/setups/{setup_id}").json()
        # The source itself was never replaced: a false binding here can only
        # come from the failed artifact verification.
        assert view["setup"]["is_stale"] is False
        evidence = view["current"]["cad_selection_evidence"]
        assert set(evidence) == {"region_healthy", "region_offender"}
        for region_id, item in evidence.items():
            assert item["resolution"] == "resolved", region_id
            assert item["stable_identity_authoritative"] is False, region_id
            assert item["viewer_binding_valid"] is False, region_id
            assert item["confirmable"] is False, region_id
            assert item["blocking_code"] == (
                "cad_region_artifact_integrity_failed"
            ), region_id
        assert view["current"]["validation"]["validation_status"] == "invalid"
        assert view["current"]["export_eligible"] is False

        blocked = client.post(
            f"/api/v1/setups/{setup_id}/regions/region_healthy/confirm",
            json={"expected_revision": 1, "request_id": "confirm-corrupt"},
        )
        assert blocked.status_code == 422
        assert blocked.json()["code"] == "cad_region_artifact_integrity_failed"


def test_missing_artifact_withdraws_authority_from_every_bound_region(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        setup_id, _artifact = _two_region_setup(
            client, project_id, uploaded, "missing-projection"
        )

        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM geometry_identity_artifacts "
                    "WHERE model_version_id=:version_id"
                ),
                {"version_id": version_id},
            )

        view = client.get(f"/api/v1/setups/{setup_id}").json()
        assert view["setup"]["is_stale"] is False
        for region_id, item in view["current"][
            "cad_selection_evidence"
        ].items():
            assert item["stable_identity_authoritative"] is False, region_id
            assert item["viewer_binding_valid"] is False, region_id
            assert item["confirmable"] is False, region_id
            assert item["blocking_code"] == "cad_region_artifact_missing"
        assert view["current"]["export_eligible"] is False


def test_region_specific_failure_is_not_charged_to_a_healthy_region(tmp_path):
    """One forged region must not brand its healthy neighbour.

    The projection previously assigned the setup-wide reference error to every
    CAD region, so a single forged identity published
    ``cad_region_identity_unknown`` on a region whose evidence verified.
    """

    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        setup_id, _artifact = _two_region_setup(
            client, project_id, uploaded, "per-region"
        )

        # Database-level tampering is the only way to persist forged evidence:
        # every write path already refuses it. This is what the read-time
        # revalidation exists to catch.
        with app.state.persistence.engine.begin() as connection:
            stored = connection.execute(
                text(
                    "SELECT intent_json FROM setup_revisions "
                    "WHERE setup_id=:setup_id AND revision=1"
                ),
                {"setup_id": setup_id},
            ).scalar_one()
            document = json.loads(stored)
            offender = next(
                region
                for region in document["regions"]
                if region["id"] == "region_offender"
            )
            offender["cad_face_target"]["stable_identities"] = [
                "gfi1:" + "0" * 64
            ]
            connection.execute(
                text("DROP TRIGGER setup_revisions_immutable")
            )
            connection.execute(
                text(
                    "UPDATE setup_revisions SET intent_json=:document "
                    "WHERE setup_id=:setup_id AND revision=1"
                ),
                {
                    "document": json.dumps(document),
                    "setup_id": setup_id,
                },
            )

        view = client.get(f"/api/v1/setups/{setup_id}").json()
        evidence = view["current"]["cad_selection_evidence"]

        offender_evidence = evidence["region_offender"]
        assert offender_evidence["blocking_code"] == "cad_region_identity_unknown"
        assert offender_evidence["stable_identity_authoritative"] is False
        assert offender_evidence["viewer_binding_valid"] is False
        assert offender_evidence["confirmable"] is False

        healthy = evidence["region_healthy"]
        assert healthy["blocking_code"] is None
        assert healthy["stable_identity_authoritative"] is True
        assert healthy["viewer_binding_valid"] is True
        # Still fail-closed: nothing in this setup may be confirmed while any
        # durable CAD reference fails to revalidate.
        assert healthy["confirmable"] is False

        assert view["current"]["validation"]["validation_status"] == "invalid"
        assert view["current"]["export_eligible"] is False
        blocked = client.post(
            f"/api/v1/setups/{setup_id}/regions/region_healthy/confirm",
            json={"expected_revision": 1, "request_id": "confirm-healthy"},
        )
        assert blocked.status_code == 422
        assert blocked.json()["code"] == "cad_region_identity_unknown"


def test_shared_artifact_corruption_stays_artifact_scoped_for_both_regions(
    tmp_path,
):
    """The artifact is shared, so its failure is charged to both regions.

    This is the counterpart to the per-region attribution above: a truthful
    projection must still block every region bound to a single defective
    artifact, rather than picking one of them.
    """

    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        version_id = uploaded["model_version"]["id"]
        setup_id, artifact = _two_region_setup(
            client, project_id, uploaded, "artifact-scope"
        )

        # A binding mismatch is artifact-scoped and names no single region.
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER geometry_identity_artifacts_immutable")
            )
            connection.execute(
                text(
                    "UPDATE geometry_identity_artifacts "
                    "SET source_sha256=:digest "
                    "WHERE model_version_id=:version_id"
                ),
                {"digest": "0" * 64, "version_id": version_id},
            )

        view = client.get(f"/api/v1/setups/{setup_id}").json()
        codes = {
            region_id: item["blocking_code"]
            for region_id, item in view["current"][
                "cad_selection_evidence"
            ].items()
        }
        assert codes == {
            "region_healthy": "cad_region_artifact_binding_mismatch",
            "region_offender": "cad_region_artifact_binding_mismatch",
        }
        assert artifact["artifact_sha256"]


def test_a_cad_region_without_a_stable_target_can_never_persist(tmp_path):
    """``target_missing`` is projected defensively and cannot be reached.

    ``viewer_binding_valid`` is explicitly false for a missing target, and no
    write path can store one: this asserts the invariant that keeps the state
    unreachable rather than merely improbable.
    """

    app = _app(tmp_path)
    with TestClient(app) as client:
        project_id = _project(client)
        uploaded = _upload(client, project_id)
        intent = browser_blank_intent()
        intent["regions"] = [
            {
                "id": "no_target_region",
                "entity_type": "cad_face",
                "selection_method": "user_click",
                "confidence": 1,
                "source_instruction": "no durable target at all",
                "status": "proposed",
            }
        ]
        response = _create(client, project_id, uploaded, intent, "no-target")
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "cad_region_stable_target_required"
        assert client.get(f"/api/v1/projects/{project_id}/setups").json() == []


# ---------------------------------------------------------------------------
# Generated contract and browser-authority scans.
# ---------------------------------------------------------------------------


def _cad_branch_forbids_entity_ids(schema: dict) -> bool:
    for clause in schema.get("allOf", []):
        condition = clause.get("if", {}).get("properties", {})
        entity_type = condition.get("entity_type", {})
        if entity_type.get("const") != "cad_face":
            continue
        if clause.get("then", {}).get("not", {}).get("required") == ["entity_ids"]:
            return True
    return False


def test_generated_contract_forbids_cad_entity_ids_but_keeps_non_cad_ones():
    openapi = json.loads(
        (ROOT / "schema" / "openapi.json").read_text(encoding="utf-8")
    )
    schemas = openapi["components"]["schemas"]

    assert _cad_branch_forbids_entity_ids(schemas["Region"])
    assert "entity_ids" not in schemas["AuditCadRegion"]["properties"]
    for name in (
        "ResolvedCadFaceTarget",
        "AmbiguousCadFaceTarget",
        "UnresolvedCadFaceTarget",
        "LegacyCadFaceTarget",
        "InvalidLegacyCadFaceTarget",
    ):
        assert "entity_ids" not in schemas[name]["properties"], name

    # Legitimate non-CAD membership is untouched.
    assert "entity_ids" in schemas["AuditNonCadRegion"]["properties"]
    assert "entity_ids" in schemas["Region"]["properties"]
    assert "entity_ids" in schemas["SessionHighlight"]["properties"]

    evidence = schemas["CadSelectionEvidence"]["properties"]
    assert "entity_ids" not in evidence
    assert set(evidence) >= {
        "resolution",
        "stable_identity_authoritative",
        "viewer_binding_valid",
        "confirmable",
        "blocking_code",
        "model_version_id",
        "artifact_sha256",
        "stable_identities",
        "collision_group_ids",
        "source_face_tags",
        "viewer_node_names",
    }
    revision = schemas["SetupRevisionResponse"]["properties"]
    assert "cad_selection_evidence" in revision
    for field in ("selected_entities", "highlight_state"):
        assert "viewer" in revision[field]["description"].lower()
        assert "non-authoritative" in revision[field]["description"].lower()


def test_generated_typescript_publishes_the_evidence_contract():
    types = (
        ROOT / "schema" / "generated" / "typescript" / "api-types.ts"
    ).read_text(encoding="utf-8")
    assert "CadSelectionEvidence" in types
    assert "cad_selection_evidence" in types
    assert "stable_identity_authoritative" in types
    assert "viewer_binding_valid" in types


@pytest.mark.skipif(
    shutil.which("node") is None and shutil.which("node.exe") is None,
    reason="Node.js is unavailable in this Python-only test environment",
)
def test_executable_r4b3_browser_dom_harness():
    """Run the R4b.3 DOM harness.

    The frontend CI job pins Node and runs this harness directly, so the skip
    below only ever applies to a Python-only developer environment — it can no
    longer hide a browser regression from review.
    """

    env = os.environ.copy()
    env["SIM_INTENT_ROOT"] = str(ROOT)
    completed = subprocess.run(
        [
            shutil.which("node") or shutil.which("node.exe") or "node",
            "--no-warnings",
            "--experimental-loader",
            "./tests/js/r3_2b_loader.mjs",
            "./tests/js/r4b3_harness.mjs",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["schemaVersion"] == SIMULATION_INTENT_SCHEMA_VERSION
    assert result["cadRegions"] == 2


def test_browser_never_mints_stable_identities_or_resolves_them():
    for name in (
        "engineering.js",
        "durable-api.js",
        "app.js",
        "audit.js",
        "grounding-highlights.js",
    ):
        source = (STATIC / name).read_text(encoding="utf-8")
        # A minted identity would have to appear as a string literal; the
        # scan deliberately ignores prose mentioning the prefix.
        assert not re.search(r"""["'`]gfi1:""", source), name
        assert 'resolution: "resolved"' not in source, name
        assert 'resolution: "ambiguous"' not in source, name
        assert "collision_group_ids:" not in source, name

    editor = (STATIC / "engineering.js").read_text(encoding="utf-8")
    # A CAD click submits an unresolved claim only.
    assert 'resolution: "unresolved"' in editor
    assert "source_face_tags" in editor
    assert "cad_face_target" in editor

    # Structural, not a prose scan: ``regionEvidence`` is the single place a
    # submitted region's membership evidence is built. Converting CAD source
    # tags into ``entity_ids`` — or into a native set name — has to edit its
    # CAD branch, and this assertion fails when it does.
    projection = re.search(
        r"function regionEvidence\(candidate\)\s*\{(?P<body>.*?)\n  \}",
        editor,
        re.S,
    )
    assert projection is not None, (
        "regionEvidence is no longer the single region-evidence projection"
    )
    cad_branch = re.search(
        r"\?(?P<branch>.*?)\n\s*:", projection.group("body"), re.S
    )
    assert cad_branch is not None, "regionEvidence no longer branches on CAD"
    branch = cad_branch.group("branch")
    assert "cad_face_target" in branch
    for forbidden in ("entity_ids", "node_set", "element_set", "nset", "elset"):
        assert forbidden not in branch, forbidden
