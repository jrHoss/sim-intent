"""R3.2b durable browser workspace and engineering-editor evidence."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.config import LocalDataConfig
from app.persistence import Model, ModelVersion, Project, SetupRevision, SimulationSetup
from app.runtime_mode import RuntimeMode
from app.server import create_app
from tests.test_engineering_setup import (
    EXPLICIT_MESH_SETTINGS,
    EXPLICIT_SOLVER_SETTINGS,
    inp_payload as engineering_inp_payload,
)
from tests.test_project_persistence import create_project, minimal_inp, request, upload


ROOT = Path(__file__).resolve().parents[1]
BRACKET_STEP = (ROOT / "tests" / "fixtures" / "bracket.step").read_bytes()


def material_instruction() -> str:
    return (
        "Assign material alloy42 with Young's modulus E=70 GPa, "
        "Poisson's ratio nu=0.33, density=2700 kg/m3"
    )


def revision(app, setup_id: str, current: dict, request_id: str, intent: dict):
    response = request(
        app,
        "POST",
        f"/api/v1/setups/{setup_id}/revisions",
        json={
            "expected_revision": current["revision"],
            "request_id": request_id,
            "intent": intent,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def decide(app, setup_id: str, current: dict, path: str, request_id: str):
    response = request(
        app,
        "POST",
        f"/api/v1/setups/{setup_id}/{path}",
        json={
            "expected_revision": current["revision"],
            "request_id": request_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_durable_interpretation_is_read_only_until_setup_creation(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app):
        project = create_project(app, "Durable material proposal")
        uploaded = upload(
            app, project["id"], BRACKET_STEP, filename="bracket.step"
        )
        version_id = uploaded["model_version"]["id"]
        subscriber: asyncio.Queue[str] = asyncio.Queue()
        app.state.viewer_events._subscribers.add(subscriber)

        def durable_counts():
            with app.state.persistence.sessions() as session:
                return tuple(
                    session.scalar(select(func.count()).select_from(model))
                    for model in (
                        Project,
                        Model,
                        ModelVersion,
                        SimulationSetup,
                        SetupRevision,
                    )
                )

        before_counts = durable_counts()
        before_legacy_files = sorted(
            path.relative_to(app.state.model_store.root)
            for path in app.state.model_store.root.rglob("*")
        ) if app.state.model_store.root.exists() else []

        responses = [
            request(
                app,
                "POST",
                f"/api/v1/model-versions/{version_id}/interpret",
                json={
                    "instruction": material_instruction(),
                    "clicked_entity_ids": [],
                },
            )
            for _ in range(3)
        ]
        assert all(item.status_code == 200 for item in responses)
        assert responses[0].json() == responses[1].json() == responses[2].json()
        proposal = responses[0].json()
        assert proposal["state"] == "proposed"
        assert proposal["intent"]["materials"][0]["authority"] == "system_proposed"
        assert proposal["intent"]["assumptions"][0]["status"] == "pending"
        assert app.state.session_store._sessions == {}
        assert app.state.pending_interpretations == {}
        assert subscriber.empty()
        assert durable_counts() == before_counts
        after_legacy_files = sorted(
            path.relative_to(app.state.model_store.root)
            for path in app.state.model_store.root.rglob("*")
        ) if app.state.model_store.root.exists() else []
        assert after_legacy_files == before_legacy_files
        assert request(
            app, "GET", f"/api/v1/projects/{project['id']}/setups"
        ).json() == []

        created = request(
            app,
            "POST",
            f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version_id,
                "request_id": "browser-create-from-proposal",
                "intent": proposal["intent"],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["current"]["revision"] == 1
        assert app.state.session_store._sessions == {}


def test_signed_zero_survives_api_storage_reopen_and_unrelated_revision(tmp_path):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app):
        project = create_project(app, "Signed zero")
        uploaded = upload(
            app,
            project["id"],
            minimal_inp(),
            filename="signed-zero.inp",
        )
        # An INP ModelVersion carries mesh entities, not CAD faces.
        candidate = engineering_inp_payload()
        assert [item["entity_type"] for item in candidate["regions"]] == [
            "mesh_face",
            "mesh_face",
        ]
        for region_item in candidate["regions"]:
            region_item["status"] = "proposed"
        for assumption in candidate["assumptions"]:
            assumption["status"] = "pending"
        candidate["bcs"] = [{
            "type": "prescribed_displacement",
            "region_ref": "fixed",
            "components": {"x": -0.0, "y": 0.0},
            "components_original": {
                "x": {"value": -0.0, "unit": "mm"},
                "y": {"value": 0.0, "unit": "mm"},
            },
        }]
        created = request(
            app,
            "POST",
            f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": "signed-zero-create",
                "intent": candidate,
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]

        def assert_signs(current):
            bc = current["intent"]["bcs"][0]
            assert str(json.dumps(bc["components"]["x"])) == "-0.0"
            assert str(json.dumps(bc["components_original"]["x"]["value"])) == "-0.0"
            assert str(json.dumps(bc["components"]["y"])) == "0.0"
            assert str(json.dumps(bc["components_original"]["y"]["value"])) == "0.0"

        assert_signs(created.json()["current"])
        reopened = request(app, "GET", f"/api/v1/setups/{setup_id}").json()
        assert_signs(reopened["current"])
        successor_intent = copy.deepcopy(reopened["current"]["intent"])
        successor_intent["mesh_settings"]["global_element_size_mm"] = 3.0
        successor_intent["mesh_settings"]["target_size_original"] = {
            "value": 3.0,
            "unit": "mm",
        }
        successor = revision(
            app,
            setup_id,
            reopened["current"],
            "signed-zero-unrelated",
            successor_intent,
        )
        assert_signs(successor)
        assert_signs(
            request(app, "GET", f"/api/v1/setups/{setup_id}").json()["current"]
        )


def test_durable_interpretation_unsupported_source_is_stable_and_side_effect_free(
    tmp_path,
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app):
        project = create_project(app, "Unsupported durable interpretation")
        uploaded = upload(
            app,
            project["id"],
            minimal_inp(),
            filename="unsupported.inp",
        )
        subscriber: asyncio.Queue[str] = asyncio.Queue()
        app.state.viewer_events._subscribers.add(subscriber)
        responses = [
            request(
                app,
                "POST",
                f"/api/v1/model-versions/{uploaded['model_version']['id']}/interpret",
                json={"instruction": "Fix the left side", "clicked_entity_ids": []},
            )
            for _ in range(2)
        ]
        assert [item.status_code for item in responses] == [422, 422]
        assert [item.json()["code"] for item in responses] == [
            "interpretation.step_required",
            "interpretation.step_required",
        ]
        assert subscriber.empty()
        assert app.state.session_store._sessions == {}
        assert app.state.pending_interpretations == {}
        assert request(
            app, "GET", f"/api/v1/projects/{project['id']}/setups"
        ).json() == []


@pytest.mark.skipif(
    shutil.which("node") is None and shutil.which("node.exe") is None,
    reason="Node.js is unavailable in this Python-only test environment",
)
def test_executable_javascript_dom_state_harness():
    env = os.environ.copy()
    env["SIM_INTENT_ROOT"] = str(ROOT)
    completed = subprocess.run(
        [
            shutil.which("node") or shutil.which("node.exe") or "node",
            "--no-warnings",
            "--experimental-loader",
            "./tests/js/r3_2b_loader.mjs",
            "./tests/js/r3_2b_harness.mjs",
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
    assert result["finalSetup"] == "setup-C"
    assert result["viewerVersion"] == "version-C"


def test_browser_authored_setup_restarts_reopens_and_extends_history(tmp_path):
    """Exercise the same durable operations exposed by the DOM editor.

    The browser never submits a raw JSON textarea: each successor below
    corresponds to one deterministic form mutation (configuration, BC, load)
    or one durable decision control.
    """

    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy-a", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app):
        project = create_project(app, "Restartable browser study")
        uploaded = upload(
            app, project["id"], BRACKET_STEP, filename="bracket.step"
        )
        version = uploaded["model_version"]
        # What a current browser client reads back before authoring a CAD
        # region: the persisted geometry-identity artifact of the exact
        # uploaded ModelVersion.  A viewer click yields a stable identity, not
        # a solver id, and the ambiguous faces are not confirmable.
        identity = request(
            app,
            "GET",
            f"/api/v1/model-versions/{version['id']}/geometry-identity",
        ).json()
        clicked = next(
            face for face in identity["faces"] if not face["ambiguous"]
        )
        interpreted = request(
            app,
            "POST",
            f"/api/v1/model-versions/{version['id']}/interpret",
            json={
                "instruction": material_instruction(),
                "clicked_entity_ids": [],
            },
        ).json()
        created = request(
            app,
            "POST",
            f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": version["id"],
                "request_id": "browser-create",
                "intent": interpreted["intent"],
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        current = created.json()["current"]

        configured = copy.deepcopy(current["intent"])
        configured["analysis"].update(
            {
                "dimensionality": "3d_solid",
                "solver_target": "calculix",
                "coordinate_system": "global_cartesian",
            }
        )
        configured["mesh_settings"] = dict(EXPLICIT_MESH_SETTINGS)
        configured["solver_settings"] = dict(EXPLICIT_SOLVER_SETTINGS)
        current = revision(
            app, setup_id, current, "browser-configuration", configured
        )

        with_bc = copy.deepcopy(current["intent"])
        with_bc["regions"].append(
            {
                "id": "browser_support",
                "entity_type": "cad_face",
                "cad_face_target": {
                    "resolution": "resolved",
                    "model_version_id": version["id"],
                    "artifact_sha256": identity["artifact_sha256"],
                    "stable_identities": [clicked["stable_identity"]],
                    "source_face_tags": [clicked["source_ref"]],
                },
                "selection_method": "user_click",
                "confidence": 1.0,
                "source_instruction": (
                    f"Use selected viewer face_{clicked['source_ref']}."
                ),
                "status": "proposed",
            }
        )
        with_bc["bcs"].append(
            {
                "type": "fixed_displacement",
                "region_ref": "browser_support",
                "components": ["x", "y", "z"],
            }
        )
        current = revision(app, setup_id, current, "browser-add-bc", with_bc)
        stored_support = next(
            item
            for item in current["intent"]["regions"]
            if item["id"] == "browser_support"
        )
        # The persisted CAD region is bound to the exact uploaded ModelVersion
        # and its artifact, and carries no removed numeric solver evidence.
        assert "entity_ids" not in stored_support
        assert stored_support["cad_face_target"]["model_version_id"] == version["id"]
        assert (
            stored_support["cad_face_target"]["artifact_sha256"]
            == identity["artifact_sha256"]
        )
        assert stored_support["cad_face_target"]["stable_identities"] == [
            clicked["stable_identity"]
        ]

        with_load = copy.deepcopy(current["intent"])
        with_load["loads"].append(
            {
                "type": "gravity",
                "region_ref": None,
                "vector": [0.0, 0.0, -9810.0],
                "original_acceleration": {
                    "value": 9.81,
                    "unit": "m/s^2",
                },
                "magnitude_mm_per_s2": 9810.0,
                "direction": [0.0, 0.0, -1.0],
                "distribution": "uniform",
            }
        )
        current = revision(app, setup_id, current, "browser-add-load", with_load)
        current = decide(
            app,
            setup_id,
            current,
            "regions/browser_support/confirm",
            "browser-confirm-region",
        )
        assumption_id = current["intent"]["assumptions"][0]["id"]
        current = decide(
            app,
            setup_id,
            current,
            f"assumptions/{assumption_id}/accept",
            "browser-accept-material",
        )
        assert current["validation"]["readiness_status"] == "ready"
        assert current["engineering_ready"] is True
        assert current["artifact_capability"]["supported"] is False
        assert set(current["artifact_capability"]["blocking_issue_codes"]) == {
            "artifact.mapping_not_verified",
            "artifact.step_meshing_required",
        }
        revision_before_restart = current["revision"]
        history_before_restart = request(
            app, "GET", f"/api/v1/setups/{setup_id}/revisions"
        ).json()
        assert len(history_before_restart) == revision_before_restart
        assert app.state.session_store._sessions == {}

    restarted = create_app(
        tmp_path / "legacy-b", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted):
        reopened = request(
            restarted, "GET", f"/api/v1/setups/{setup_id}"
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["current"] == current
        reopened_history = request(
            restarted, "GET", f"/api/v1/setups/{setup_id}/revisions"
        ).json()
        assert reopened_history == history_before_restart

        edited = copy.deepcopy(reopened.json()["current"]["intent"])
        edited["mesh_settings"]["global_element_size_mm"] = 3.0
        edited["mesh_settings"]["target_size_original"] = {
            "value": 3.0,
            "unit": "mm",
        }
        successor = revision(
            restarted,
            setup_id,
            reopened.json()["current"],
            "browser-after-restart",
            edited,
        )
        assert successor["revision"] == revision_before_restart + 1
        assert len(
            request(
                restarted, "GET", f"/api/v1/setups/{setup_id}/revisions"
            ).json()
        ) == revision_before_restart + 1
        assert restarted.state.session_store._sessions == {}


def test_browser_assets_expose_bounded_durable_editor_without_raw_json():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    api_js = (ROOT / "app" / "static" / "durable-api.js").read_text(
        encoding="utf-8"
    )
    editor_js = (ROOT / "app" / "static" / "engineering.js").read_text(
        encoding="utf-8"
    )

    for marker in (
        "project-create-form",
        "project-select",
        "setup-select",
        "revision-history-list",
        "configuration-form",
        "material-form",
        "bc-form",
        "load-form",
        "durable-region-list",
        "durable-assumption-list",
        "revision-conflict",
        "reload-current-revision",
        "retry-mutation",
    ):
        assert f'id="{marker}"' in html
    assert 'textarea id="raw' not in html.lower()
    assert 'fileInput.addEventListener("change", () => uploadDurableModel' in app_js
    assert 'viewer.addEventListener("drop", (event) => uploadDurableModel' in app_js
    assert "createEngineeringWorkspace" in app_js
    assert "/api/v1/projects" in api_js
    assert "/api/v1/model-versions/${versionId}/interpret" in api_js
    assert "/api/v1/setups/${setupId}/revisions" in api_js
    assert "/session/" not in api_js
    assert "expected_revision: state.current.revision" in editor_js
    assert "makeRequestId(prefix)" in editor_js
    assert "setup_revision_conflict" in editor_js
    assert "Your form input has been preserved" in api_js
    assert "Object.is(value, -0)" in api_js
    assert '"-0.0"' in api_js
    assert "artifact_capability.supported" in editor_js
    assert "engineering_ready" in editor_js
    assert "Selection is not confirmation." in html
    assert "meshing not yet executed" in html
    for code in (
        "analysis.nonlinear_unsupported",
        "analysis.thermal_unsupported",
        "analysis.dynamics_unsupported",
        "analysis.shell_unsupported",
        "analysis.beam_unsupported",
        "coordinate_system.local_unsupported",
        "constraint.rotation_unsupported",
        "interaction.contact_unsupported",
        "material.orthotropic_unsupported",
    ):
        assert code in html
