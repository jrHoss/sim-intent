"""R4b.1 durable geometry-identity ownership and integrity regression tests."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import LocalDataConfig
from app.persistence import (
    GeometryIdentityArtifactRecord,
    ModelVersion,
)
from app.runtime_mode import RuntimeMode
from app.server import create_app
from geom.identity import (
    GeometryFaceInput,
    GeometryIdentityError,
    build_geometry_identity,
)


STEP_FIXTURE = Path(__file__).parent / "fixtures" / "bracket.step"


def _project(client: TestClient, name: str = "identity") -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _upload_step(
    client: TestClient, project_id: str, *, model_id: str | None = None
) -> dict:
    route = (
        f"/api/v1/projects/{project_id}/models"
        if model_id is None
        else f"/api/v1/projects/{project_id}/models/{model_id}/versions"
    )
    response = client.post(
        route,
        files={
            "file": (
                "bracket.step",
                STEP_FIXTURE.read_bytes(),
                "application/step",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_step_upload_creates_exactly_one_bound_artifact_and_read_is_read_only(
    tmp_path,
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        uploaded = _upload_step(client, _project(client)["id"])
        version = uploaded["model_version"]
        with app.state.persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(GeometryIdentityArtifactRecord)
            ) == 1
            record = session.get(GeometryIdentityArtifactRecord, version["id"])
            assert record is not None
            assert record.model_id == uploaded["model_id"]
            assert record.source_sha256 == version["source_sha256"]
            before = bytes(record.canonical_bytes)
            digest = record.integrity_sha256

        first = client.get(
            f"/api/v1/model-versions/{version['id']}/geometry-identity"
        )
        second = client.get(
            f"/api/v1/model-versions/{version['id']}/geometry-identity"
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        body = first.json()
        assert body["model_id"] == uploaded["model_id"]
        assert body["model_version_id"] == version["id"]
        assert body["source_sha256"] == version["source_sha256"]
        assert body["artifact_sha256"] == digest == hashlib.sha256(before).hexdigest()
        assert body["faces"]
        assert {"source_ref", "stable_identity", "identity_quality", "evidence"} <= (
            body["faces"][0].keys()
        )
        with app.state.persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(GeometryIdentityArtifactRecord)
            ) == 1
            assert bytes(
                session.get(
                    GeometryIdentityArtifactRecord, version["id"]
                ).canonical_bytes
            ) == before


def test_restart_returns_byte_identical_canonical_artifact(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    first_app = create_app(
        tmp_path / "first", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(first_app) as client:
        uploaded = _upload_step(client, _project(client)["id"])
        version_id = uploaded["model_version"]["id"]
        record, before, payload = first_app.state.persistence.read_geometry_identity(
            version_id
        )
        assert hashlib.sha256(before).hexdigest() == record.integrity_sha256
        assert payload["model_binding"]["model_version_id"] == version_id

    restarted = create_app(
        tmp_path / "second", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted) as client:
        record, after, payload = restarted.state.persistence.read_geometry_identity(
            version_id
        )
        assert after == before
        assert hashlib.sha256(after).hexdigest() == record.integrity_sha256
        assert payload["model_binding"]["source_sha256"] == (
            uploaded["model_version"]["source_sha256"]
        )
        assert client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).status_code == 200


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            "UPDATE geometry_identity_artifacts "
            "SET canonical_bytes=x'7B7D' WHERE model_version_id=:id",
            "geometry_identity_integrity_failed",
        ),
        (
            "UPDATE geometry_identity_artifacts "
            "SET integrity_sha256=:bad WHERE model_version_id=:id",
            "geometry_identity_integrity_failed",
        ),
        (
            "UPDATE geometry_identity_artifacts "
            "SET source_sha256=:bad WHERE model_version_id=:id",
            "geometry_identity_binding_mismatch",
        ),
        (
            "UPDATE geometry_identity_artifacts "
            "SET artifact_version=999 WHERE model_version_id=:id",
            "geometry_identity_version_unsupported",
        ),
        (
            "DELETE FROM geometry_identity_artifacts WHERE model_version_id=:id",
            "geometry_identity_missing",
        ),
    ],
)
def test_corrupted_or_missing_artifact_fails_safely(
    tmp_path, mutation, expected_code
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        uploaded = _upload_step(client, _project(client)["id"])
        version_id = uploaded["model_version"]["id"]
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER geometry_identity_artifacts_immutable")
            )
            connection.execute(
                text(mutation),
                {"id": version_id, "bad": "b" * 64},
            )
        response = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        )
        assert response.status_code == 500
        assert response.json()["code"] == expected_code
        assert "canonical_bytes" not in response.text
        assert str(config.root) not in response.text


def test_failed_identity_construction_publishes_no_version_or_artifact(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)

    def fail_identity(**_kwargs):
        raise GeometryIdentityError(
            "geometry.test_construction_failed",
            "Geometry identity construction failed.",
        )

    monkeypatch.setattr("app.server.build_geometry_identity", fail_identity)
    with TestClient(app) as client:
        project = _project(client)
        response = client.post(
            f"/api/v1/projects/{project['id']}/models",
            files={
                "file": (
                    "bracket.step",
                    STEP_FIXTURE.read_bytes(),
                    "application/step",
                )
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "geometry.test_construction_failed"
        with app.state.persistence.sessions() as session:
            assert session.scalar(select(ModelVersion)) is None
            assert session.scalar(select(GeometryIdentityArtifactRecord)) is None
        assert list(config.blob_root.glob("sha256/*/*/*")) == []


def test_supersession_preserves_old_artifact_and_creates_independent_successor(
    tmp_path,
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        project_id = _project(client)["id"]
        first = _upload_step(client, project_id)
        first_id = first["model_version"]["id"]
        _, first_bytes, _ = app.state.persistence.read_geometry_identity(first_id)
        second = _upload_step(client, project_id, model_id=first["model_id"])
        second_id = second["model_version"]["id"]
        _, old_after, old_payload = app.state.persistence.read_geometry_identity(
            first_id
        )
        _, successor_bytes, successor_payload = (
            app.state.persistence.read_geometry_identity(second_id)
        )
        assert old_after == first_bytes
        assert first_id != second_id
        assert old_payload["model_binding"]["model_version_id"] == first_id
        assert successor_payload["model_binding"]["model_version_id"] == second_id
        assert successor_bytes != first_bytes
        with app.state.persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(GeometryIdentityArtifactRecord)
            ) == 2


def test_collision_groups_and_local_mapping_survive_persistence(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    source = b"synthetic durable STEP source"
    source_sha256 = hashlib.sha256(source).hexdigest()
    version_id = str(uuid.uuid4())
    faces = [
        GeometryFaceInput(
            source_ref=source_ref,
            surface_type="plane",
            area=10.0,
            centroid=(0.0, 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            boundary_loop_count=1,
            adjacent_refs=(other_ref,),
        )
        for source_ref, other_ref in ((7, 9), (9, 7))
    ]
    artifact = build_geometry_identity(
        model_version_id=version_id,
        source_sha256=source_sha256,
        faces=faces,
    )
    assert artifact.collision_groups
    with TestClient(app):
        persistence = app.state.persistence
        project = persistence.create_project("synthetic")
        _, version = persistence.create_model_version(
            project_id=project.id,
            source_name="synthetic.step",
            content=source,
            model_kind="step",
            version_id=version_id,
            geometry_identity_bytes=artifact.canonical_bytes(),
        )
        _, canonical, restored = persistence.read_geometry_identity(version.id)
        assert canonical == artifact.canonical_bytes()
        assert restored["collision_groups"] == artifact.to_dict()["collision_groups"]
        assert {
            face["source_ref"]: face["stable_identity"]
            for face in restored["faces"]
        } == {
            face.source_ref: face.stable_identity for face in artifact.faces
        }


def test_geometry_identity_route_is_in_public_openapi(tmp_path):
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.PRODUCTION)
    route = "/api/v1/model-versions/{version_id}/geometry-identity"
    operation = app.openapi()["paths"][route]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/GeometryIdentityArtifactResponse")
