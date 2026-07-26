"""Focused R1.2 durable setup revision evidence."""

from concurrent.futures import ThreadPoolExecutor
import threading

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import LocalDataConfig
from app.migrations import alembic_config
from app.persistence import SetupRevision, SimulationSetup
from app.runtime_mode import RuntimeMode
from app.server import create_app
from tests.test_project_persistence import create_project, minimal_inp, request, upload
from tests.test_session import intent_payload


def create_setup(app, *, project=None, uploaded=None, intent=None, request_id="create-1"):
    project = project or create_project(app)
    uploaded = uploaded or upload(app, project["id"], minimal_inp())
    response = request(
        app, "POST", f"/api/v1/projects/{project['id']}/setups",
        json={
            "model_id": uploaded["model_id"],
            "model_version_id": uploaded["model_version"]["id"],
            "request_id": request_id,
            "intent": intent or intent_payload(),
        },
    )
    assert response.status_code == 201, response.text
    return project, uploaded, response.json()


def create_three_revision_setup(app, **kwargs):
    project, uploaded, created = create_setup(app, **kwargs)
    setup_id = created["setup"]["id"]
    for revision, region_id in enumerate(("fixed_region", "loaded_region"), start=1):
        response = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/{region_id}/confirm",
            json={"expected_revision": revision, "request_id": f"three-{region_id}"},
        )
        assert response.status_code == 201, response.text
    return project, uploaded, created


def test_decisions_revisions_idempotency_conflict_and_restart(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        _, _, created = create_setup(app)
        setup_id = created["setup"]["id"]
        first = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm-fixed"},
        )
        assert first.status_code == 201
        replay = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "confirm-fixed"},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == first.json()["id"]
        stale = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/loaded_region/confirm",
            json={"expected_revision": 1, "request_id": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "setup_revision_conflict"
        final = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/loaded_region/confirm",
            json={"expected_revision": 2, "request_id": "confirm-loaded"},
        )
        assert final.status_code == 201
        assert final.json()["revision"] == 3
        assert final.json()["export_eligible"] is True
        with app.state.persistence.engine.begin() as connection:
            try:
                connection.execute(text(
                    "UPDATE setup_revisions SET mutation_type='changed' WHERE id=:id"
                ), {"id": final.json()["id"]})
                raise AssertionError("revision update unexpectedly succeeded")
            except IntegrityError as exc:
                assert "immutable" in str(exc)

    with TestClient(create_app(tmp_path / "legacy-2", mode=RuntimeMode.TEST, data_config=config)) as client:
        reopened = request(client.app, "GET", f"/api/v1/setups/{setup_id}")
        assert reopened.status_code == 200
        assert reopened.json()["current"]["revision"] == 3
        assert reopened.json()["current"]["export_eligible"] is True


def test_concurrent_api_mutations_have_one_success_and_one_controlled_conflict(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        _, _, created = create_setup(app)
        setup_id = created["setup"]["id"]
        barrier = threading.Barrier(3)

        def confirm(region_id: str):
            barrier.wait()
            return request(
                app, "POST",
                f"/api/v1/setups/{setup_id}/regions/{region_id}/confirm",
                json={"expected_revision": 1, "request_id": f"confirm-{region_id}"},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(confirm, "fixed_region"),
                executor.submit(confirm, "loaded_region"),
            ]
            barrier.wait()
            responses = [future.result(timeout=10) for future in futures]

        assert sorted(response.status_code for response in responses) == [201, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.headers["content-type"].startswith("application/problem+json")
        assert conflict.json()["code"] == "setup_revision_conflict"
        history = request(app, "GET", f"/api/v1/setups/{setup_id}/revisions")
        assert history.status_code == 200
        revisions = history.json()
        assert [item["revision"] for item in revisions] == [1, 2]
        assert revisions[1]["parent_revision_id"] == revisions[0]["id"]


def test_assumption_decision_and_derived_state_survive_restart(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    payload = intent_payload()
    payload["assumptions"] = [{
        "text": "Input force unit interpreted as N",
        "criticality": "unit_critical",
        "status": "pending",
    }]
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        _, _, created = create_setup(app, intent=payload)
        setup_id = created["setup"]["id"]
        assumption_id = created["current"]["intent"]["assumptions"][0]["id"]
        revision = 1
        for region_id in ("fixed_region", "loaded_region"):
            response = request(
                app, "POST", f"/api/v1/setups/{setup_id}/regions/{region_id}/confirm",
                json={"expected_revision": revision, "request_id": f"confirm-{region_id}"},
            )
            assert response.status_code == 201
            revision += 1
        accepted = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/assumptions/{assumption_id}/accept",
            json={"expected_revision": revision, "request_id": "accept-assumption"},
        )
        assert accepted.status_code == 201
        expected_validation = accepted.json()["validation"]
        assert accepted.json()["intent"]["assumptions"][0]["status"] == "accepted"
        assert accepted.json()["export_eligible"] is True

    with TestClient(create_app(tmp_path / "legacy-reopen", mode=RuntimeMode.TEST, data_config=config)) as client:
        reopened = request(client.app, "GET", f"/api/v1/setups/{setup_id}")
        current = reopened.json()["current"]
        assert current["intent"]["assumptions"][0]["status"] == "accepted"
        assert current["validation"] == expected_validation
        assert current["intent"]["validation_status"] == expected_validation["validation_status"]
        assert current["export_eligible"] is True


def test_cross_lineage_setup_creation_is_rejected_without_records(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        first_project = create_project(app, "first")
        second_project = create_project(app, "second")
        uploaded = upload(app, first_project["id"], minimal_inp())
        response = request(
            app, "POST", f"/api/v1/projects/{second_project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": "invalid-lineage",
                "intent": intent_payload(),
            },
        )
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "setup_lineage_conflict"
        with app.state.persistence.sessions() as session:
            assert session.scalar(select(func.count()).select_from(SimulationSetup)) == 0
            assert session.scalar(select(func.count()).select_from(SetupRevision)) == 0


def test_model_version_setup_decisions_remain_isolated_after_restart(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        first_upload = upload(app, project["id"], minimal_inp())
        second_response = request(
            app, "POST",
            f"/api/v1/projects/{project['id']}/models/{first_upload['model_id']}/versions",
            files={"file": ("part-v2.inp", minimal_inp(2), "application/octet-stream")},
        )
        assert second_response.status_code == 201
        second_upload = second_response.json()
        _, _, first_setup = create_setup(
            app, project=project, uploaded=first_upload, request_id="create-v1"
        )
        _, _, second_setup = create_setup(
            app, project=project, uploaded=second_upload, request_id="create-v2"
        )
        first_id = first_setup["setup"]["id"]
        second_id = second_setup["setup"]["id"]
        decided = request(
            app, "POST", f"/api/v1/setups/{first_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "v1-confirm"},
        )
        assert decided.status_code == 201
        untouched = request(app, "GET", f"/api/v1/setups/{second_id}")
        assert untouched.json()["current"]["revision"] == 1
        assert untouched.json()["current"]["intent"]["regions"][0]["status"] == "proposed"

    with TestClient(create_app(tmp_path / "legacy-reopen", mode=RuntimeMode.TEST, data_config=config)) as client:
        first = request(client.app, "GET", f"/api/v1/setups/{first_id}").json()
        second = request(client.app, "GET", f"/api/v1/setups/{second_id}").json()
        assert first["setup"]["model_version_id"] == first_upload["model_version"]["id"]
        assert second["setup"]["model_version_id"] == second_upload["model_version"]["id"]
        assert first["current"]["revision"] == 2
        assert first["current"]["intent"]["regions"][0]["status"] == "confirmed"
        assert second["current"]["revision"] == 1
        assert second["current"]["intent"]["regions"][0]["status"] == "proposed"
        assert len(request(client.app, "GET", f"/api/v1/setups/{first_id}/revisions").json()) == 2
        assert len(request(client.app, "GET", f"/api/v1/setups/{second_id}/revisions").json()) == 1


def test_project_and_direct_setup_deletion_cascade_three_revisions(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project, _, created = create_three_revision_setup(app)
        setup_id = created["setup"]["id"]
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM simulation_setups WHERE id=:id"), {"id": setup_id}
            )
            assert connection.scalar(text(
                "SELECT count(*) FROM setup_revisions WHERE setup_id=:id"
            ), {"id": setup_id}) == 0

        project2, _, created2 = create_three_revision_setup(
            app, request_id="second-create"
        )
        setup2 = created2["setup"]["id"]
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM projects WHERE id=:id"), {"id": project2["id"]}
            )
            assert connection.scalar(text(
                "SELECT count(*) FROM simulation_setups WHERE id=:id"
            ), {"id": setup2}) == 0
            assert connection.scalar(text(
                "SELECT count(*) FROM setup_revisions WHERE setup_id=:id"
            ), {"id": setup2}) == 0


def test_create_rejects_client_managed_statuses_and_cannot_start_ready(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        uploaded = upload(app, project["id"], minimal_inp())
        confirmed = intent_payload()
        confirmed["regions"][0]["status"] = "confirmed"
        response = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": "terminal-region",
                "intent": confirmed,
            },
        )
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "setup_transition_invalid"

        accepted = intent_payload()
        accepted["assumptions"] = [{
            "text": "Accepted by client",
            "criticality": "unit_critical",
            "status": "accepted",
        }]
        response = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": "terminal-assumption",
                "intent": accepted,
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "setup_transition_invalid"

        _, _, created = create_setup(
            app, project=project, uploaded=uploaded, request_id="valid-create"
        )
        assert created["current"]["export_eligible"] is False
        assert created["current"]["revision"] == 1


def test_setup_creation_idempotency_sequential_concurrent_and_project_scoped(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app, "idempotent")
        uploaded = upload(app, project["id"], minimal_inp())
        body = {
            "model_id": uploaded["model_id"],
            "model_version_id": uploaded["model_version"]["id"],
            "request_id": "same-create",
            "intent": intent_payload(),
        }
        first = request(app, "POST", f"/api/v1/projects/{project['id']}/setups", json=body)
        replay = request(app, "POST", f"/api/v1/projects/{project['id']}/setups", json=body)
        assert replay.status_code == 201
        assert replay.json() == first.json()

        different = dict(body)
        changed_intent = intent_payload(first_ids=[99])
        different["intent"] = changed_intent
        conflict = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups", json=different
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "setup_request_id_conflict"

        concurrent_body = {**body, "request_id": "concurrent-create"}
        barrier = threading.Barrier(3)

        def retry_create():
            barrier.wait()
            return request(
                app, "POST", f"/api/v1/projects/{project['id']}/setups",
                json=concurrent_body,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(retry_create) for _ in range(2)]
            barrier.wait()
            responses = [future.result(timeout=10) for future in futures]
        assert [response.status_code for response in responses] == [201, 201]
        assert responses[0].json()["setup"]["id"] == responses[1].json()["setup"]["id"]

        other_project = create_project(app, "unrelated")
        other_upload = upload(app, other_project["id"], minimal_inp(3))
        scoped = create_setup(
            app, project=other_project, uploaded=other_upload,
            request_id="same-create",
        )[2]
        assert scoped["setup"]["id"] != first.json()["setup"]["id"]
        with app.state.persistence.sessions() as session:
            assert session.scalar(select(func.count()).select_from(SimulationSetup)) == 3


def test_decision_replay_fingerprints_exact_region_and_assumption_operation(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    payload = intent_payload()
    payload["assumptions"] = [
        {"text": "First assumption", "status": "pending"},
        {"text": "Second assumption", "status": "pending"},
    ]
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        _, _, created = create_setup(app, intent=payload)
        setup_id = created["setup"]["id"]
        assumptions = created["current"]["intent"]["assumptions"]

        first = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "exact-region"},
        )
        assert first.status_code == 201
        exact = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "exact-region"},
        )
        assert exact.json()["id"] == first.json()["id"]
        request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/loaded_region/confirm",
            json={"expected_revision": 2, "request_id": "other-region"},
        )
        wrong_subject = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/loaded_region/confirm",
            json={"expected_revision": 1, "request_id": "exact-region"},
        )
        assert wrong_subject.status_code == 409
        assert wrong_subject.json()["code"] == "setup_request_id_conflict"
        wrong_action = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/reject",
            json={"expected_revision": 1, "request_id": "exact-region"},
        )
        assert wrong_action.status_code == 409
        assert wrong_action.json()["code"] == "setup_request_id_conflict"

        accepted = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/assumptions/{assumptions[0]['id']}/accept",
            json={"expected_revision": 3, "request_id": "exact-assumption"},
        )
        assert accepted.status_code == 201
        request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/assumptions/{assumptions[1]['id']}/accept",
            json={"expected_revision": 4, "request_id": "other-assumption"},
        )
        wrong_assumption = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/assumptions/{assumptions[1]['id']}/accept",
            json={"expected_revision": 3, "request_id": "exact-assumption"},
        )
        assert wrong_assumption.status_code == 409
        assert wrong_assumption.json()["code"] == "setup_request_id_conflict"


def test_nonexistent_current_pointer_rejected_and_stale_status_precedes_transition(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project, uploaded, created = create_setup(app)
        with pytest.raises(IntegrityError, match="invalid current setup revision"):
            with app.state.persistence.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO simulation_setups "
                    "(id, project_id, model_id, model_version_id, current_revision, "
                    "create_request_id, create_request_sha256, created_at, updated_at) "
                    "VALUES (:id, :project, :model, :version, 99, 'bad-pointer', "
                    ":digest, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ), {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "project": project["id"],
                    "model": uploaded["model_id"],
                    "version": uploaded["model_version"]["id"],
                    "digest": "a" * 64,
                })
        setup_id = created["setup"]["id"]
        request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "advance"},
        )
        stale = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/reject",
            json={"expected_revision": 1, "request_id": "stale-status"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "setup_revision_conflict"


def test_populated_downgrade_reupgrade_and_triggers_with_three_revisions(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        create_three_revision_setup(client.app)

    migration = alembic_config(config.database_url)
    command.downgrade(migration, "0001_projects_models")
    engine = None
    try:
        from app.persistence import create_sqlite_engine

        engine = create_sqlite_engine(config.database_url)
        assert "simulation_setups" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_projects_models"
        engine.dispose()
        engine = None

        command.upgrade(migration, "head")
        engine = create_sqlite_engine(config.database_url)
        trigger_names = set(engine.connect().scalars(text(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )))
        assert {
            "simulation_setups_lineage_insert",
            "simulation_setups_lineage_update",
            "simulation_setups_current_revision_insert",
            "simulation_setups_current_revision",
            "setup_revisions_immutable",
            "setup_revisions_sequential",
        }.issubset(trigger_names)
        with engine.begin() as connection:
            project_id, model_id, version_id, setup_id = (
                f"22222222-2222-2222-2222-22222222222{index}" for index in range(4)
            )
            connection.execute(text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES (:id, 'reupgrade', CURRENT_TIMESTAMP)"
            ), {"id": project_id})
            connection.execute(text(
                "INSERT INTO models (id, project_id, created_at) "
                "VALUES (:id, :project, CURRENT_TIMESTAMP)"
            ), {"id": model_id, "project": project_id})
            connection.execute(text(
                "INSERT INTO model_versions "
                "(id, model_id, version, source_sha256, source_name, size_bytes, "
                "media_type, model_kind, blob_key, created_at) VALUES "
                "(:id, :model, 1, :digest, 'part.inp', 1, "
                "'application/octet-stream', 'inp', :blob, CURRENT_TIMESTAMP)"
            ), {
                "id": version_id, "model": model_id, "digest": "b" * 64,
                "blob": f"sha256/bb/bb/{'b' * 64}",
            })
            connection.execute(text(
                "INSERT INTO simulation_setups "
                "(id, project_id, model_id, model_version_id, current_revision, "
                "create_request_id, create_request_sha256, created_at, updated_at) "
                "VALUES (:id, :project, :model, :version, NULL, 'reupgrade', "
                ":digest, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {
                "id": setup_id, "project": project_id, "model": model_id,
                "version": version_id, "digest": "c" * 64,
            })
        with pytest.raises(IntegrityError, match="invalid setup revision parent"):
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO setup_revisions "
                    "(id, setup_id, revision, parent_revision_id, schema_version, "
                    "intent_json, intent_sha256, mutation_type, request_id, "
                    "mutation_sha256, created_at) VALUES "
                    "(:id, :setup, 2, NULL, 1, '{}', :digest, 'bad', 'bad', "
                    ":digest, CURRENT_TIMESTAMP)"
                ), {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "setup": setup_id, "digest": "d" * 64,
                })
        revision_id = "44444444-4444-4444-4444-444444444444"
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO setup_revisions "
                "(id, setup_id, revision, parent_revision_id, schema_version, "
                "intent_json, intent_sha256, mutation_type, request_id, "
                "mutation_sha256, created_at) VALUES "
                "(:id, :setup, 1, NULL, 1, '{}', :digest, 'create', 'valid', "
                ":digest, CURRENT_TIMESTAMP)"
            ), {"id": revision_id, "setup": setup_id, "digest": "e" * 64})
            connection.execute(text(
                "UPDATE simulation_setups SET current_revision=1 WHERE id=:id"
            ), {"id": setup_id})
        with pytest.raises(IntegrityError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE setup_revisions SET mutation_type='changed' WHERE id=:id"
                ), {"id": revision_id})
    finally:
        if engine is not None:
            engine.dispose()
