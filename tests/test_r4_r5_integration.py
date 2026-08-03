"""Focused integration evidence for the completed R4 and R5.2 histories."""

from __future__ import annotations

import json

from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.blob_store import (
    BlobCoordinationPathError,
    BlobCoordinationTimeoutError,
    BlobIntegrityError,
)
from app.config import LocalDataConfig
from app.migrations import alembic_config
from app.persistence import create_sqlite_engine
from app.runtime_mode import RuntimeMode
from app.server import create_app
from tests.test_engineering_setup import inp_payload
from tests.test_project_persistence import minimal_inp

R4_HEAD = "0005_stable_cad_region_references"
R5_HEAD = "0005_mesh_domain_persistence"
INTEGRATION_HEAD = "0006_merge_r4_r5_heads"


def _versions(engine) -> set[str]:
    with engine.connect() as connection:
        return set(connection.scalars(text("SELECT version_num FROM alembic_version")))


def test_merge_revision_upgrades_from_each_predecessor(tmp_path):
    for predecessor in (R4_HEAD, R5_HEAD):
        database = tmp_path / f"{predecessor}.sqlite3"
        config = alembic_config(f"sqlite:///{database}")
        command.upgrade(config, predecessor)
        engine = create_sqlite_engine(f"sqlite:///{database}")
        assert _versions(engine) == {predecessor}
        engine.dispose()

        command.upgrade(config, "head")
        engine = create_sqlite_engine(f"sqlite:///{database}")
        assert _versions(engine) == {INTEGRATION_HEAD}
        with engine.connect() as connection:
            tables = set(
                connection.scalars(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            )
            triggers = set(
                connection.scalars(
                    text("SELECT name FROM sqlite_master WHERE type='trigger'")
                )
            )
        assert "mesh_revisions" in tables
        assert {
            "mesh_revisions_immutable",
            "mesh_revisions_ownership_currentness",
            "mesh_revisions_exact_lineage",
            "setup_revisions_immutable",
        }.issubset(triggers)
        engine.dispose()


def test_merge_revision_upgrades_when_both_predecessors_are_present(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'both.sqlite3'}"
    config = alembic_config(database_url)
    command.upgrade(config, R4_HEAD)
    command.upgrade(config, R5_HEAD)
    engine = create_sqlite_engine(database_url)
    assert _versions(engine) == {R4_HEAD, R5_HEAD}
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_sqlite_engine(database_url)
    assert _versions(engine) == {INTEGRATION_HEAD}
    engine.dispose()
    assert ScriptDirectory.from_config(config).get_heads() == [INTEGRATION_HEAD]


def _project_and_inp(client: TestClient) -> tuple[str, dict]:
    project = client.post("/api/v1/projects", json={"name": "cas"})
    assert project.status_code == 201
    project_id = project.json()["id"]
    upload = client.post(
        f"/api/v1/projects/{project_id}/models",
        files={"file": ("part.inp", minimal_inp(), "application/octet-stream")},
    )
    assert upload.status_code == 201, upload.text
    return project_id, upload.json()


def test_setup_write_coordination_failures_are_sanitized_and_atomic(
    tmp_path, monkeypatch
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project_id, uploaded = _project_and_inp(client)
        version_id = uploaded["model_version"]["id"]
        with app.state.persistence.engine.connect() as connection:
            before = (
                connection.scalar(text("SELECT count(*) FROM simulation_setups")),
                connection.scalar(text("SELECT count(*) FROM setup_revisions")),
            )

        intent = inp_payload()
        for region in intent["regions"]:
            region["status"] = "proposed"

        sensitive_path = str(tmp_path.resolve() / "private" / "cas.lock")
        for error, code, retryable in (
            (
                BlobCoordinationTimeoutError(
                    f"timeout at {sensitive_path}"
                ),
                "storage_coordination_unavailable",
                True,
            ),
            (
                BlobCoordinationPathError(f"unsafe {sensitive_path}"),
                "storage_coordination_failed",
                False,
            ),
        ):
            def fail(error=error):
                raise error

            monkeypatch.setattr(
                app.state.persistence.blobs.coordination_lock,
                "acquire",
                fail,
            )
            response = client.post(
                f"/api/v1/projects/{project_id}/setups",
                json={
                    "model_id": uploaded["model_id"],
                    "model_version_id": version_id,
                    "request_id": code,
                    "intent": intent,
                },
            )
            assert response.status_code == 500, response.text
            assert response.headers["content-type"].startswith(
                "application/problem+json"
            )
            problem = response.json()
            assert problem["code"] == code
            assert problem["retryable"] is retryable
            assert sensitive_path not in json.dumps(problem)
            with app.state.persistence.engine.connect() as connection:
                after = (
                    connection.scalar(text("SELECT count(*) FROM simulation_setups")),
                    connection.scalar(text("SELECT count(*) FROM setup_revisions")),
                )
            assert after == before


def test_upload_blob_integrity_failure_is_sanitized_and_atomic(
    tmp_path, monkeypatch
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post("/api/v1/projects", json={"name": "integrity"})
        project_id = project.json()["id"]

        def fail(*_args, **_kwargs):
            raise BlobIntegrityError(f"corrupt {tmp_path.resolve() / 'private' / 'source.bin'}")

        monkeypatch.setattr(
            app.state.persistence.blobs, "publish_file_with_limit", fail
        )
        response = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={
                "file": (
                    "part.inp",
                    minimal_inp(),
                    "application/octet-stream",
                )
            },
        )
        assert response.status_code == 500
        problem = response.json()
        assert problem["code"] == "source_blob_integrity_failed"
        assert problem["retryable"] is False
        assert str(tmp_path.resolve()) not in json.dumps(problem)
        with app.state.persistence.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM models")) == 0
            assert connection.scalar(text("SELECT count(*) FROM model_versions")) == 0
