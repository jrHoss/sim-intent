"""Focused R2.2 source supersession and durable-capacity evidence."""

from concurrent.futures import ThreadPoolExecutor
import uuid

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event as sa_event, text
from sqlalchemy.exc import IntegrityError

from app.config import LocalDataConfig
from app.blob_store import BlobStore
from app.migrations import alembic_config
from app.persistence import create_sqlite_engine
from app.persistence import Model, ModelVersion, SimulationSetup
from app.runtime_mode import RuntimeMode
from app.server import create_app
from tests.test_project_persistence import create_project, minimal_inp, request, upload
from tests.test_session import intent_payload


def replacement(app, project_id, model_id, content):
    return request(
        app, "POST",
        f"/api/v1/projects/{project_id}/models/{model_id}/versions",
        files={"file": ("replacement.inp", content, "application/octet-stream")},
    )


def test_supersession_stales_setup_preserves_history_and_allows_fresh_setup(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        first = upload(app, project["id"], minimal_inp())
        first_id = first["model_version"]["id"]
        assert first["model_version"]["is_current"] is True
        created = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={"model_id": first["model_id"], "model_version_id": first_id,
                  "request_id": "old", "intent": intent_payload()},
        ).json()
        setup_id = created["setup"]["id"]
        confirmed = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json={"expected_revision": 1, "request_id": "confirmed"},
        )
        assert confirmed.status_code == 201

        second = replacement(
            app, project["id"], first["model_id"], minimal_inp(scale=2)
        )
        assert second.status_code == 201
        second_id = second.json()["model_version"]["id"]
        versions = request(
            app, "GET", f"/api/v1/models/{first['model_id']}/versions"
        ).json()
        assert versions[0]["is_superseded"] is True
        assert versions[0]["superseded_by_version_id"] == second_id
        assert versions[1]["is_current"] is True
        old_create = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={"model_id": first["model_id"], "model_version_id": first_id,
                  "request_id": "late-old", "intent": intent_payload()},
        )
        assert old_create.status_code == 409
        assert old_create.json()["code"] == "setup_source_superseded"

        historical = request(app, "GET", f"/api/v1/setups/{setup_id}")
        assert historical.status_code == 200
        assert historical.json()["setup"]["is_stale"] is True
        assert historical.json()["setup"]["stale_reason"] == "source_replaced"
        assert historical.json()["current"]["export_eligible"] is False
        assert request(
            app, "GET", f"/api/v1/setups/{setup_id}/revisions/2"
        ).json()["intent"]["regions"][0]["status"] == "confirmed"
        blocked = request(
            app, "POST", f"/api/v1/setups/{setup_id}/regions/loaded_region/confirm",
            json={"expected_revision": 2, "request_id": "blocked"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "setup_source_superseded"

        fresh = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={"model_id": first["model_id"], "model_version_id": second_id,
                  "request_id": "fresh", "intent": intent_payload()},
        )
        assert fresh.status_code == 201
        statuses = [r["status"] for r in fresh.json()["current"]["intent"]["regions"]]
        assert statuses == ["proposed", "proposed"]

    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as reopened:
        body = request(reopened.app, "GET", f"/api/v1/setups/{setup_id}").json()
        assert body["setup"]["is_stale"] is True
        assert body["setup"]["model_version_is_current"] is False


def test_interleaved_replacement_cannot_leave_old_setup_active(tmp_path):
    import threading

    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        uploaded = upload(app, project["id"], minimal_inp())
        reached_flush = threading.Event()
        release_flush = threading.Event()

        def pause_setup_flush(session, _context, _instances):
            if any(isinstance(record, SimulationSetup) for record in session.new):
                reached_flush.set()
                assert release_flush.wait(5)

        session_class = app.state.persistence.sessions.class_
        sa_event.listen(session_class, "before_flush", pause_setup_flush)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                setup_future = pool.submit(
                    request, app, "POST",
                    f"/api/v1/projects/{project['id']}/setups",
                    json={"model_id": uploaded["model_id"],
                          "model_version_id": uploaded["model_version"]["id"],
                          "request_id": "interleaved", "intent": intent_payload()},
                )
                assert reached_flush.wait(5)
                replacement_future = pool.submit(
                    replacement, app, project["id"], uploaded["model_id"],
                    minimal_inp(2),
                )
                assert not replacement_future.done()
                release_flush.set()
                setup_response = setup_future.result(timeout=10)
                replacement_response = replacement_future.result(timeout=10)
        finally:
            release_flush.set()
            sa_event.remove(session_class, "before_flush", pause_setup_flush)

        assert setup_response.status_code == replacement_response.status_code == 201
        setup_id = setup_response.json()["setup"]["id"]
        projection = request(app, "GET", f"/api/v1/setups/{setup_id}").json()
        assert projection["setup"]["is_stale"] is True
        assert projection["setup"]["model_version_is_current"] is False


def test_sql_rejects_active_old_setup_stale_revival_and_invalid_pointers(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        first = upload(app, project["id"], minimal_inp())
        setup = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={"model_id": first["model_id"],
                  "model_version_id": first["model_version"]["id"],
                  "request_id": "setup", "intent": intent_payload()},
        ).json()["setup"]
        second = replacement(
            app, project["id"], first["model_id"], minimal_inp(2)
        ).json()["model_version"]
        engine = app.state.persistence.engine

        with pytest.raises(IntegrityError, match="setup source is superseded"):
            with engine.begin() as connection:
                connection.execute(text("""
                  INSERT INTO simulation_setups
                  (id,project_id,model_id,model_version_id,current_revision,
                   create_request_id,create_request_sha256,created_at,updated_at,
                   is_stale,stale_reason,stale_at)
                  SELECT :id,project_id,model_id,model_version_id,NULL,
                    'sql-old','a',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0,NULL,NULL
                  FROM simulation_setups WHERE id=:source
                """), {"id": str(uuid.uuid4()), "source": setup["id"]})

        for statement in (
            "UPDATE simulation_setups SET is_stale=0,stale_reason=NULL,stale_at=NULL WHERE id=:id",
            "UPDATE simulation_setups SET stale_reason=NULL WHERE id=:id",
            "UPDATE simulation_setups SET stale_at=NULL WHERE id=:id",
        ):
            with pytest.raises(IntegrityError, match="invalid setup staleness"):
                with engine.begin() as connection:
                    connection.execute(text(statement), {"id": setup["id"]})

        with pytest.raises(IntegrityError, match="invalid current model version"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE models SET current_version_id=NULL WHERE id=:id"
                ), {"id": first["model_id"]})
        with pytest.raises(IntegrityError, match="invalid current model version"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE models SET current_version_id=:old WHERE id=:id"
                ), {"old": first["model_version"]["id"], "id": first["model_id"]})
        foreign = upload(app, project["id"], minimal_inp(9))
        with pytest.raises(IntegrityError, match="invalid current model version"):
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE models SET current_version_id=:foreign WHERE id=:id"
                ), {"foreign": foreign["model_version"]["id"],
                    "id": first["model_id"]})
        with pytest.raises(IntegrityError, match="cannot delete current model version"):
            with engine.begin() as connection:
                connection.execute(text(
                    "DELETE FROM model_versions WHERE id=:id"
                ), {"id": second["id"]})
        with pytest.raises(IntegrityError, match="invalid current model version"):
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO models (id,project_id,created_at,current_version_id) "
                    "VALUES (:id,:project,CURRENT_TIMESTAMP,:foreign)"
                ), {"id": str(uuid.uuid4()), "project": project["id"],
                    "foreign": second["id"]})

        versions = request(
            app, "GET", f"/api/v1/models/{first['model_id']}/versions"
        ).json()
        assert [item["is_current"] for item in versions] == [False, True]


def test_all_currentness_projections_follow_pointer_under_inconsistent_flags(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        uploaded = upload(app, project["id"], minimal_inp())
        active = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={"model_id": uploaded["model_id"],
                  "model_version_id": uploaded["model_version"]["id"],
                  "request_id": "active", "intent": intent_payload()},
        ).json()
        other_version_id = str(uuid.uuid4())
        stale_setup_id = str(uuid.uuid4())
        stale_revision_id = str(uuid.uuid4())
        digest = "f" * 64
        with app.state.persistence.engine.begin() as connection:
            # Inserts do not weaken or disable any trigger. This deliberately
            # models legacy/inconsistent flags while retaining one pointer.
            connection.execute(text("""
              INSERT INTO model_versions
              (id,model_id,version,source_sha256,source_name,size_bytes,
               media_type,model_kind,blob_key,created_at,is_superseded)
              VALUES (:id,:model,2,:digest,'other.inp',1,
                'application/octet-stream','inp',:blob,CURRENT_TIMESTAMP,0)
            """), {"id": other_version_id, "model": uploaded["model_id"],
                   "digest": digest, "blob": f"sha256/ff/ff/{digest}"})
            connection.execute(text("""
              INSERT INTO simulation_setups
              (id,project_id,model_id,model_version_id,current_revision,
               create_request_id,create_request_sha256,created_at,updated_at,
               is_stale,stale_reason,stale_at)
              VALUES (:id,:project,:model,:version,NULL,'stale-copy','copy',
                CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,'source_replaced',
                CURRENT_TIMESTAMP)
            """), {"id": stale_setup_id, "project": project["id"],
                   "model": uploaded["model_id"], "version": other_version_id})
            connection.execute(text("""
              INSERT INTO setup_revisions
              (id,setup_id,revision,parent_revision_id,schema_version,
               intent_json,intent_sha256,mutation_type,request_id,
               mutation_sha256,created_at)
              SELECT :revision,:setup,1,NULL,schema_version,intent_json,
                intent_sha256,'create','stale-copy-revision',mutation_sha256,
                CURRENT_TIMESTAMP
              FROM setup_revisions WHERE setup_id=:source AND revision=1
            """), {"revision": stale_revision_id, "setup": stale_setup_id,
                   "source": active["setup"]["id"]})
            connection.execute(text(
                "UPDATE simulation_setups SET current_revision=1 WHERE id=:id"
            ), {"id": stale_setup_id})

        versions = request(
            app, "GET", f"/api/v1/models/{uploaded['model_id']}/versions"
        ).json()
        assert [item["is_superseded"] for item in versions] == [False, False]
        assert [item["is_current"] for item in versions] == [True, False]
        other = request(
            app, "GET", f"/api/v1/model-versions/{other_version_id}"
        ).json()
        assert other["is_current"] is False
        projection = request(
            app, "GET", f"/api/v1/setups/{stale_setup_id}"
        ).json()
        assert projection["setup"]["model_version_is_current"] is False
        assert projection["setup"]["is_stale"] is True
        assert projection["current"]["export_eligible"] is False


def test_pre_replacement_intent_and_decision_replays_remain_idempotent(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        uploaded = upload(app, project["id"], minimal_inp())
        intent = intent_payload()
        intent["assumptions"] = [{
            "text": "Input force unit interpreted as N",
            "criticality": "unit_critical",
            "status": "pending",
        }]
        created = request(
            app, "POST", f"/api/v1/projects/{project['id']}/setups",
            json={"model_id": uploaded["model_id"],
                  "model_version_id": uploaded["model_version"]["id"],
                  "request_id": "create", "intent": intent},
        ).json()
        setup_id = created["setup"]["id"]
        mutation_body = {
            "expected_revision": 1, "request_id": "intent-replay",
            "intent": intent,
        }
        intent_revision = request(
            app, "POST", f"/api/v1/setups/{setup_id}/revisions",
            json=mutation_body,
        )
        assert intent_revision.status_code == 201
        decision_body = {"expected_revision": 2, "request_id": "decision-replay"}
        decision = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json=decision_body,
        )
        assert decision.status_code == 201
        assumption_id = created["current"]["intent"]["assumptions"][0]["id"]
        assumption_body = {
            "expected_revision": 3, "request_id": "assumption-replay"
        }
        assumption = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/assumptions/{assumption_id}/accept",
            json=assumption_body,
        )
        assert assumption.status_code == 201
        replacement(app, project["id"], uploaded["model_id"], minimal_inp(2))
        intent_replay = request(
            app, "POST", f"/api/v1/setups/{setup_id}/revisions",
            json=mutation_body,
        )
        decision_replay = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/regions/fixed_region/confirm",
            json=decision_body,
        )
        assumption_replay = request(
            app, "POST",
            f"/api/v1/setups/{setup_id}/assumptions/{assumption_id}/accept",
            json=assumption_body,
        )
        assert (
            intent_replay.status_code
            == decision_replay.status_code
            == assumption_replay.status_code
            == 201
        )
        assert intent_replay.json()["id"] == intent_revision.json()["id"]
        assert decision_replay.json()["id"] == decision.json()["id"]
        assert assumption_replay.json()["id"] == assumption.json()["id"]
        mismatch = request(
            app, "POST", f"/api/v1/setups/{setup_id}/revisions",
            json={**mutation_body, "expected_revision": 2},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "setup_request_id_conflict"


def test_storage_cap_dedup_failure_atomicity_and_concurrency(tmp_path):
    one, two = minimal_inp(1), minimal_inp(2)
    cap = len(one) + len(two) - 1
    config = LocalDataConfig(
        tmp_path / "data", max_source_storage_bytes=cap
    )
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        app = client.app
        project = create_project(app)
        first = upload(app, project["id"], one)
        duplicate = upload(app, project["id"], one)
        assert duplicate["model_version"]["source_sha256"] == first["model_version"]["source_sha256"]
        failed = request(
            app, "POST", f"/api/v1/projects/{project['id']}/models",
            files={"file": ("two.inp", two, "application/octet-stream")},
        )
        assert failed.status_code == 507
        assert failed.json()["code"] == "source_storage_limit_exceeded"
        with app.state.persistence.sessions() as session:
            assert session.query(Model).count() == 2
            assert session.query(ModelVersion).count() == 2

    payloads = (minimal_inp(3), minimal_inp(4))
    concurrent = LocalDataConfig(
        tmp_path / "concurrent-data",
        max_source_storage_bytes=max(map(len, payloads)),
    )
    with TestClient(create_app(tmp_path / "other", mode=RuntimeMode.TEST, data_config=concurrent)) as client:
        app = client.app
        project2 = create_project(app, "concurrent")
        results = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for response in pool.map(
                lambda body: request(
                    app, "POST", f"/api/v1/projects/{project2['id']}/models",
                    files={"file": ("part.inp", body, "application/octet-stream")},
                ),
                payloads,
            ):
                results.append(response.status_code)
        assert results.count(201) == 1
        assert results.count(507) == 1
        assert app.state.persistence.blobs.source_bytes() <= concurrent.max_source_storage_bytes


def test_storage_accounting_ignores_malformed_and_symlink_entries(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    malformed_digest = "a" * 64
    malformed = store.path_for_key(store.key(malformed_digest))
    malformed.parent.mkdir(parents=True)
    malformed.write_bytes(b"not the named digest")
    unrelated = store.root / "database.sqlite3"
    unrelated.write_bytes(b"x" * 100)
    target = store.root / "target"
    target.write_bytes(b"x" * 100)
    link = store.root / "sha256" / "ff"
    try:
        link.symlink_to(target)
    except OSError:
        pass  # Windows developer accounts may not have symlink privilege.
    assert store.source_bytes() == 0


def test_orphan_blob_is_reclaimed_before_next_quota_check(tmp_path):
    first, second = minimal_inp(7), minimal_inp(8)
    config = LocalDataConfig(
        tmp_path / "data", max_source_storage_bytes=len(second)
    )
    with TestClient(create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)) as client:
        persistence = client.app.state.persistence
        with pytest.raises(LookupError):
            persistence.create_model_version(
                project_id=str(uuid.uuid4()), source_name="orphan.inp",
                content=first, model_kind="inp",
            )
        assert persistence.blobs.source_bytes() == len(first)
        project = persistence.create_project("valid")
        _, version = persistence.create_model_version(
            project_id=project.id, source_name="valid.inp",
            content=second, model_kind="inp",
        )
        assert version.source_sha256 == persistence.blobs.digest(second)
        assert persistence.blobs.source_bytes() == len(second)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "1.5", "false"])
def test_storage_limit_configuration_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("SIM_INTENT_MAX_SOURCE_STORAGE_BYTES", value)
    with pytest.raises(ValueError, match="positive integer"):
        LocalDataConfig.from_env()


def test_populated_0002_upgrade_deterministic_backfill_downgrade_reupgrade(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    config.root.mkdir(parents=True)
    alembic = alembic_config(config.database_url)
    command.upgrade(alembic, "0002_setup_revisions")
    engine = create_sqlite_engine(config.database_url)
    (
        project_id, other_project_id, model_id, other_model_id,
        older_id, middle_id, newer_id, other_version_id,
        old_setup_id, current_setup_id, old_revision_1, old_revision_2,
        current_revision_id,
    ) = (str(uuid.uuid4()) for _ in range(13))
    with engine.begin() as connection:
        for value, name in ((project_id, "p"), (other_project_id, "other")):
            connection.execute(text(
                "INSERT INTO projects VALUES (:p, :name, CURRENT_TIMESTAMP)"
            ), {"p": value, "name": name})
        for value, owner in (
            (model_id, project_id), (other_model_id, other_project_id)
        ):
            connection.execute(text(
                "INSERT INTO models VALUES (:m, :p, CURRENT_TIMESTAMP)"
            ), {"m": value, "p": owner})
        for version, version_id, owner in (
            (1, older_id, model_id), (2, middle_id, model_id),
            (3, newer_id, model_id), (1, other_version_id, other_model_id),
        ):
            digest = str(version) * 64
            connection.execute(text(
                "INSERT INTO model_versions VALUES "
                "(:id,:m,:v,:d,'part.inp',1,'application/octet-stream','inp',:k,CURRENT_TIMESTAMP)"
            ), {"id": version_id, "m": owner, "v": version, "d": digest,
                "k": f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"})
        for setup_id, version_id, request_id in (
            (old_setup_id, older_id, "old"), (current_setup_id, newer_id, "current")
        ):
            connection.execute(text("""
              INSERT INTO simulation_setups
              VALUES (:id,:p,:m,:v,NULL,:request,'digest',
                CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """), {"id": setup_id, "p": project_id, "m": model_id,
                   "v": version_id, "request": request_id})
        payload = (
            '{"regions":[{"status":"confirmed"}],'
            '"assumptions":[{"status":"accepted"}]}'
        )
        for revision_id, setup_id, revision, parent, request_id in (
            (old_revision_1, old_setup_id, 1, None, "old-r1"),
            (old_revision_2, old_setup_id, 2, old_revision_1, "old-r2"),
            (current_revision_id, current_setup_id, 1, None, "current-r1"),
        ):
            connection.execute(text("""
              INSERT INTO setup_revisions
              VALUES (:id,:setup,:revision,:parent,1,:payload,'intent',
                'test',:request,'mutation',CURRENT_TIMESTAMP)
            """), {"id": revision_id, "setup": setup_id, "revision": revision,
                   "parent": parent, "payload": payload, "request": request_id})
        connection.execute(text(
            "UPDATE simulation_setups SET current_revision=2 WHERE id=:id"
        ), {"id": old_setup_id})
        connection.execute(text(
            "UPDATE simulation_setups SET current_revision=1 WHERE id=:id"
        ), {"id": current_setup_id})
    command.upgrade(alembic, "head")
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT current_version_id FROM models WHERE id=:m"
        ), {"m": model_id}) == newer_id
        rows = connection.execute(text("""
          SELECT id,is_superseded,superseded_at,superseded_by_version_id
          FROM model_versions WHERE model_id=:m ORDER BY version
        """), {"m": model_id}).all()
        assert [(row.id, row.is_superseded) for row in rows] == [
            (older_id, 1), (middle_id, 1), (newer_id, 0)
        ]
        assert all(row.superseded_at is not None for row in rows[:2])
        assert all(row.superseded_by_version_id == newer_id for row in rows[:2])
        assert connection.scalar(text(
            "SELECT is_stale FROM simulation_setups WHERE id=:id"
        ), {"id": old_setup_id}) == 1
        assert connection.scalar(text(
            "SELECT is_stale FROM simulation_setups WHERE id=:id"
        ), {"id": current_setup_id}) == 0
        assert connection.scalar(text(
            "SELECT count(*) FROM setup_revisions WHERE setup_id=:id"
        ), {"id": old_setup_id}) == 2
        assert "confirmed" in connection.scalar(text(
            "SELECT intent_json FROM setup_revisions WHERE id=:id"
        ), {"id": old_revision_2})
    command.downgrade(alembic, "0002_setup_revisions")
    command.upgrade(alembic, "head")
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT current_version_id FROM models WHERE id=:m"
        ), {"m": model_id}) == newer_id
    with pytest.raises(IntegrityError, match="invalid setup staleness"):
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE simulation_setups SET is_stale=0,stale_reason=NULL,"
                "stale_at=NULL WHERE id=:id"
            ), {"id": old_setup_id})
    engine.dispose()
