"""Focused acceptance regressions for confirmed R4b.1 findings."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path

import gmsh
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import LocalDataConfig
from app.persistence import (
    GeometryIdentityArtifactError,
    GeometryIdentityArtifactRecord,
    ModelVersion,
)
from app.runtime_mode import RuntimeMode
from app.server import create_app
from geom.analytic import analyze_identity_surfaces
from geom.identity import (
    GeometryFaceInput,
    GeometryIdentityError,
    build_geometry_identity,
    canonical_json_bytes,
    deserialize_geometry_identity,
    faces_from_inventory,
)
from geom.inventory import FaceInventory, file_sha256
from geom.parser import parse_step


BRACKET_STEP = Path(__file__).parent / "fixtures" / "bracket.step"


def _project(client: TestClient, name: str = "r4b1") -> str:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _upload(
    client: TestClient,
    project_id: str,
    source: Path,
    *,
    model_id: str | None = None,
):
    route = (
        f"/api/v1/projects/{project_id}/models"
        if model_id is None
        else f"/api/v1/projects/{project_id}/models/{model_id}/versions"
    )
    return client.post(
        route,
        files={"file": (source.name, source.read_bytes(), "application/step")},
    )


def _write_analytic_step(path: Path, surface_type: str) -> None:
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(surface_type)
        if surface_type == "cone":
            gmsh.model.occ.addCone(1, 2, 3, 0, 0, 10, 5, 2)
        elif surface_type == "sphere":
            gmsh.model.occ.addSphere(1, 2, 3, 5)
        elif surface_type == "torus":
            gmsh.model.occ.addTorus(1, 2, 3, 5, 2)
        else:  # pragma: no cover - guarded by the parametrization
            raise AssertionError(surface_type)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


@pytest.mark.parametrize("surface_type", ["cone", "sphere", "torus"])
def test_isolated_ingestion_matches_direct_r4a_analytic_artifact(
    tmp_path, surface_type
):
    source = tmp_path / f"{surface_type}.step"
    _write_analytic_step(source, surface_type)
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        response = _upload(client, _project(client), source)
        assert response.status_code == 201, response.text
        version = response.json()["model_version"]
        _record, stored, stored_payload = (
            app.state.persistence.read_geometry_identity(version["id"])
        )
        inventory = FaceInventory(
            source.name, file_sha256(source), parse_step(source)
        )
        evidence = analyze_identity_surfaces(source)
        direct = build_geometry_identity(
            model_version_id=version["id"],
            source_sha256=version["source_sha256"],
            faces=faces_from_inventory(
                inventory, analytic_surfaces=evidence
            ),
        )
        assert stored == direct.canonical_bytes()
        assert stored_payload == direct.to_dict()
        assert {
            face["surface_type"] for face in stored_payload["faces"]
        } >= {surface_type}


def _rewrite_artifact(app, version_id: str, mutator) -> None:
    with app.state.persistence.engine.begin() as connection:
        connection.execute(
            text("DROP TRIGGER geometry_identity_artifacts_immutable")
        )
        raw = connection.scalar(
            text(
                "SELECT canonical_bytes FROM geometry_identity_artifacts "
                "WHERE model_version_id=:id"
            ),
            {"id": version_id},
        )
        payload = json.loads(bytes(raw).decode("ascii"))
        mutator(payload)
        canonical = canonical_json_bytes(payload)
        connection.execute(
            text(
                "UPDATE geometry_identity_artifacts "
                "SET canonical_bytes=:canonical, integrity_sha256=:digest "
                "WHERE model_version_id=:id"
            ),
            {
                "id": version_id,
                "canonical": canonical,
                "digest": hashlib.sha256(canonical).hexdigest(),
            },
        )


def _only_version_and_binding(payload):
    binding = payload["model_binding"]
    payload.clear()
    payload.update(
        {
            "artifact_type": "geometry_identity",
            "schema_version": 1,
            "hash_domain": "sim-intent.geometry-identity/v1",
            "model_binding": binding,
        }
    )


def _missing_faces(payload):
    payload.pop("faces")


def _missing_mapping(payload):
    payload["faces"][0]["topology"].pop("adjacent_source_refs")


def _malformed_collision(payload):
    payload["collision_groups"] = [
        {
            "collision_group_id": "a" * 64,
            "identity_candidates": [],
            "member_source_refs": [payload["faces"][0]["source_ref"]],
            "reason": "within_declared_ambiguity_tolerance",
        }
    ]


def _invalid_quality(payload):
    payload["faces"][0]["identity_quality"] = "certain"


def _invalid_identity_state(payload):
    payload["faces"][0]["ambiguous"] = True


def _wrong_nested_type(payload):
    payload["faces"][0]["topology"]["adjacent_source_refs"] = {}


def _duplicate_local_reference(payload):
    payload["faces"][1]["source_ref"] = payload["faces"][0]["source_ref"]


def _unknown_variant(payload):
    payload["faces"][0]["surface_type"] = "revolution"


def _forged_repeated_feature_signature(payload):
    face = next(
        item
        for item in payload["faces"]
        if item["evidence"]["repeated_feature_signature"] is not None
    )
    original = face["evidence"]["repeated_feature_signature"]
    face["evidence"]["repeated_feature_signature"] = (
        "f" * 64 if original != "f" * 64 else "e" * 64
    )


def _forged_repeated_feature_group_size(payload):
    repeated = [
        item
        for item in payload["faces"]
        if item["evidence"]["repeated_feature_signature"] is not None
    ]
    assert repeated
    forged_size = repeated[0]["evidence"]["repeated_feature_group_size"] + 1
    for face in repeated:
        face["evidence"]["repeated_feature_group_size"] = forged_size
        face["topology"]["repeated_feature_count"] = forged_size


@pytest.mark.parametrize(
    "mutator",
    [
        _forged_repeated_feature_signature,
        _forged_repeated_feature_group_size,
    ],
    ids=["signature", "group_size"],
)
def test_repeated_feature_partition_is_recomputed_at_every_read_boundary(
    tmp_path, mutator
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        uploaded = _upload(client, _project(client), BRACKET_STEP)
        assert uploaded.status_code == 201, uploaded.text
        version_id = uploaded.json()["model_version"]["id"]
        _record, valid_bytes, _payload = (
            app.state.persistence.read_geometry_identity(version_id)
        )
        assert (
            deserialize_geometry_identity(valid_bytes).canonical_bytes()
            == valid_bytes
        )

        forged_payload = json.loads(valid_bytes.decode("ascii"))
        mutator(forged_payload)
        forged_bytes = canonical_json_bytes(forged_payload)
        with pytest.raises(GeometryIdentityError) as direct:
            deserialize_geometry_identity(forged_bytes)
        assert direct.value.code == "geometry.artifact_schema_invalid"

        _rewrite_artifact(app, version_id, mutator)
        with pytest.raises(GeometryIdentityArtifactError) as persisted:
            app.state.persistence.read_geometry_identity(version_id)
        assert persisted.value.code == "geometry_identity_schema_invalid"

        response = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        )
        assert response.status_code == 500
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert response.json()["code"] == "geometry_identity_schema_invalid"


@pytest.mark.parametrize(
    "mutator",
    [
        _only_version_and_binding,
        _missing_faces,
        _missing_mapping,
        _malformed_collision,
        _invalid_quality,
        _invalid_identity_state,
        _wrong_nested_type,
        _duplicate_local_reference,
        _unknown_variant,
    ],
    ids=lambda item: item.__name__.removeprefix("_"),
)
def test_correctly_hashed_semantically_invalid_artifact_is_safely_rejected(
    tmp_path, mutator
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        uploaded = _upload(client, _project(client), BRACKET_STEP)
        assert uploaded.status_code == 201
        version_id = uploaded.json()["model_version"]["id"]
        _rewrite_artifact(app, version_id, mutator)
        with pytest.raises(GeometryIdentityArtifactError) as caught:
            app.state.persistence.read_geometry_identity(version_id)
        assert caught.value.code == "geometry_identity_schema_invalid"
        response = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        )
        assert response.status_code == 500
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert response.json()["code"] == "geometry_identity_schema_invalid"
        assert "KeyError" not in response.text
        assert str(config.root) not in response.text


def test_semantically_invalid_artifact_is_rejected_before_insertion(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    source = b"bounded synthetic STEP source"
    version_id = str(uuid.uuid4())
    valid = build_geometry_identity(
        model_version_id=version_id,
        source_sha256=hashlib.sha256(source).hexdigest(),
        faces=[
            GeometryFaceInput(
                source_ref=1,
                surface_type="plane",
                area=1.0,
                centroid=(0.0, 0.0, 0.0),
                normal=(0.0, 0.0, 1.0),
                boundary_loop_count=1,
            )
        ],
    ).to_dict()
    valid.pop("faces")
    malformed = canonical_json_bytes(valid)
    with TestClient(app):
        persistence = app.state.persistence
        project = persistence.create_project("strict insertion")
        with pytest.raises(GeometryIdentityArtifactError) as caught:
            persistence.create_model_version(
                project_id=project.id,
                source_name="synthetic.step",
                content=source,
                model_kind="step",
                version_id=version_id,
                geometry_identity_bytes=malformed,
            )
        assert caught.value.code == "geometry_identity_schema_invalid"
        with persistence.sessions() as session:
            assert session.scalar(select(ModelVersion)) is None
            assert session.scalar(
                select(GeometryIdentityArtifactRecord)
            ) is None
        assert list(config.blob_root.glob("sha256/*/*/*")) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 999),
        ("hash_domain", "sim-intent.geometry-identity/v999"),
    ],
)
def test_correctly_hashed_unsupported_artifact_versions_are_rejected(
    tmp_path, field, value
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        uploaded = _upload(client, _project(client), BRACKET_STEP)
        assert uploaded.status_code == 201
        version_id = uploaded.json()["model_version"]["id"]
        _rewrite_artifact(
            app,
            version_id,
            lambda payload: payload.__setitem__(field, value),
        )
        with pytest.raises(GeometryIdentityArtifactError) as caught:
            app.state.persistence.read_geometry_identity(version_id)
        assert caught.value.code == "geometry_identity_version_unsupported"
        response = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        )
        assert response.status_code == 500
        assert response.json()["code"] == "geometry_identity_version_unsupported"


def _minimal_inp() -> bytes:
    return (
        b"*HEADING\nR4b.1\n*NODE\n1,0,0,0\n2,1,0,0\n3,0,1,0\n4,0,0,1\n"
        b"*ELEMENT,TYPE=C3D4\n1,1,2,3,4\n"
    )


def test_inp_identity_is_typed_not_applicable_in_persistence_and_http(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project_id = _project(client)
        response = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={"file": ("mesh.inp", _minimal_inp(), "text/plain")},
        )
        assert response.status_code == 201, response.text
        version_id = response.json()["model_version"]["id"]
        with pytest.raises(GeometryIdentityArtifactError) as caught:
            app.state.persistence.read_geometry_identity(version_id)
        assert caught.value.code == "geometry_identity_not_applicable"
        problem = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        )
        assert problem.status_code == 422
        assert problem.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert problem.json()["code"] == "geometry_identity_not_applicable"
        assert problem.json()["retryable"] is False


def test_copied_valid_artifact_between_projects_is_rejected_by_binding(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        first = _upload(client, _project(client, "first"), BRACKET_STEP)
        second = _upload(client, _project(client, "second"), BRACKET_STEP)
        assert first.status_code == second.status_code == 201
        first_id = first.json()["model_version"]["id"]
        second_id = second.json()["model_version"]["id"]
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER geometry_identity_artifacts_immutable")
            )
            copied = connection.execute(
                text(
                    "SELECT canonical_bytes, integrity_sha256 "
                    "FROM geometry_identity_artifacts "
                    "WHERE model_version_id=:id"
                ),
                {"id": first_id},
            ).one()
            connection.execute(
                text(
                    "UPDATE geometry_identity_artifacts "
                    "SET canonical_bytes=:canonical, integrity_sha256=:digest "
                    "WHERE model_version_id=:id"
                ),
                {
                    "id": second_id,
                    "canonical": copied.canonical_bytes,
                    "digest": copied.integrity_sha256,
                },
            )
        with pytest.raises(GeometryIdentityArtifactError) as caught:
            app.state.persistence.read_geometry_identity(second_id)
        assert caught.value.code == "geometry_identity_binding_mismatch"
        response = client.get(
            f"/api/v1/model-versions/{second_id}/geometry-identity"
        )
        assert response.status_code == 500
        assert response.json()["code"] == "geometry_identity_binding_mismatch"


def test_unknown_version_and_missing_step_artifact_remain_distinct(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        unknown = client.get(
            f"/api/v1/model-versions/{uuid.uuid4()}/geometry-identity"
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "model_version_not_found"
        uploaded = _upload(client, _project(client), BRACKET_STEP)
        assert uploaded.status_code == 201
        version_id = uploaded.json()["model_version"]["id"]
        with app.state.persistence.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM geometry_identity_artifacts "
                    "WHERE model_version_id=:id"
                ),
                {"id": version_id},
            )
        missing = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        )
        assert missing.status_code == 500
        assert missing.json()["code"] == "geometry_identity_missing"


@pytest.mark.parametrize(
    "failure_point",
    [
        "artifact_insert",
        "commit",
        "after_model_version_insert",
        "after_artifact_insert",
    ],
)
def test_database_write_failures_rollback_and_clean_retry(
    tmp_path, monkeypatch, failure_point
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        persistence = app.state.persistence
        project_id = _project(client)
        removers = []

        def database_failure(*_args, **_kwargs):
            raise OperationalError(
                "private SQL", {}, RuntimeError("private database detail")
            )

        if failure_point == "artifact_insert":
            def fail_artifact_insert(
                _connection, _cursor, statement, _parameters, _context, _many
            ):
                if statement.lstrip().upper().startswith(
                    "INSERT INTO GEOMETRY_IDENTITY_ARTIFACTS"
                ):
                    database_failure()

            event.listen(
                persistence.engine,
                "before_cursor_execute",
                fail_artifact_insert,
            )
            removers.append(
                lambda: event.remove(
                    persistence.engine,
                    "before_cursor_execute",
                    fail_artifact_insert,
                )
            )
        elif failure_point == "commit":
            event.listen(persistence.engine, "commit", database_failure)
            removers.append(
                lambda: event.remove(
                    persistence.engine, "commit", database_failure
                )
            )
        elif failure_point == "after_model_version_insert":
            original = persistence._insert_model_version

            def fail_after_model_version(session, version):
                original(session, version)
                database_failure()

            monkeypatch.setattr(
                persistence, "_insert_model_version", fail_after_model_version
            )
        else:
            def fail_after_artifact_flush(session, _context):
                if any(
                    isinstance(item, GeometryIdentityArtifactRecord)
                    for item in session.new
                ):
                    database_failure()

            event.listen(
                persistence.sessions.class_, "after_flush", fail_after_artifact_flush
            )
            removers.append(
                lambda: event.remove(
                    persistence.sessions.class_,
                    "after_flush",
                    fail_after_artifact_flush,
                )
            )

        failed = _upload(client, project_id, BRACKET_STEP)
        assert failed.status_code == 500
        assert failed.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert failed.json()["code"] == "database_write_failed"
        assert failed.json()["retryable"] is True
        assert "private" not in failed.text
        with persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(ModelVersion)
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(
                    GeometryIdentityArtifactRecord
                )
            ) == 0
        assert list(config.blob_root.glob("sha256/*/*/*")) == []

        for remove in removers:
            remove()
        if failure_point == "after_model_version_insert":
            monkeypatch.setattr(
                persistence, "_insert_model_version", original
            )
        retried = _upload(client, project_id, BRACKET_STEP)
        assert retried.status_code == 201, retried.text
        with persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(ModelVersion)
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(
                    GeometryIdentityArtifactRecord
                )
            ) == 1


def test_failure_immediately_after_new_blob_publication_is_cleaned_and_retries(
    tmp_path
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        persistence = app.state.persistence
        project_id = _project(client)

        def fail_after_publication():
            raise RuntimeError("injected post-publication failure")

        persistence._after_blob_publish = fail_after_publication
        failed = _upload(client, project_id, BRACKET_STEP)
        assert failed.status_code == 500
        with persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(ModelVersion)
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(
                    GeometryIdentityArtifactRecord
                )
            ) == 0
        assert list(config.blob_root.glob("sha256/*/*/*")) == []

        persistence._after_blob_publish = None
        retried = _upload(client, project_id, BRACKET_STEP)
        assert retried.status_code == 201, retried.text


def test_failed_deduplicated_publication_preserves_shared_blob(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        persistence = app.state.persistence
        project_id = _project(client)
        first = _upload(client, project_id, BRACKET_STEP)
        assert first.status_code == 201, first.text
        blob_paths = list(config.blob_root.glob("sha256/*/*/*"))
        assert len(blob_paths) == 1
        shared_blob = blob_paths[0]

        def fail_after_publication():
            raise RuntimeError("injected deduplicated publication failure")

        persistence._after_blob_publish = fail_after_publication
        failed = _upload(client, project_id, BRACKET_STEP)
        assert failed.status_code == 500
        assert shared_blob.is_file()
        with persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(ModelVersion)
            ) == 1

        persistence._after_blob_publish = None
        retried = _upload(client, project_id, BRACKET_STEP)
        assert retried.status_code == 201, retried.text
        assert shared_blob.is_file()


@pytest.mark.parametrize("failure_point", ["artifact_insert", "commit"])
def test_cleanup_failure_cannot_replace_database_write_problem(
    tmp_path, monkeypatch, failure_point
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        persistence = app.state.persistence
        project_id = _project(client)
        removers = []

        def database_failure(*_args, **_kwargs):
            raise OperationalError(
                "private SQL", {}, RuntimeError("private database detail")
            )

        if failure_point == "artifact_insert":
            def fail_artifact_insert(
                _connection, _cursor, statement, _parameters, _context, _many
            ):
                if statement.lstrip().upper().startswith(
                    "INSERT INTO GEOMETRY_IDENTITY_ARTIFACTS"
                ):
                    database_failure()

            event.listen(
                persistence.engine,
                "before_cursor_execute",
                fail_artifact_insert,
            )
            removers.append(
                lambda: event.remove(
                    persistence.engine,
                    "before_cursor_execute",
                    fail_artifact_insert,
                )
            )
        else:
            event.listen(persistence.engine, "commit", database_failure)
            removers.append(
                lambda: event.remove(
                    persistence.engine, "commit", database_failure
                )
            )

        original_cleanup = persistence._cleanup_failed_publication

        def fail_cleanup(**_kwargs):
            raise RuntimeError("private cleanup path")

        monkeypatch.setattr(
            persistence, "_cleanup_failed_publication", fail_cleanup
        )
        failed = _upload(client, project_id, BRACKET_STEP)
        assert failed.status_code == 500
        assert failed.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert failed.json()["code"] == "database_write_failed"
        assert failed.json()["retryable"] is True
        assert "private" not in failed.text
        with persistence.sessions() as session:
            assert session.scalar(
                select(func.count()).select_from(ModelVersion)
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(
                    GeometryIdentityArtifactRecord
                )
            ) == 0

        for remove in removers:
            remove()
        monkeypatch.setattr(
            persistence,
            "_cleanup_failed_publication",
            original_cleanup,
        )
        retried = _upload(client, project_id, BRACKET_STEP)
        assert retried.status_code == 201, retried.text


def test_concurrent_identical_step_ingestion_publishes_one_artifact_per_version(
    tmp_path
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project_id = _project(client)
        responses = []
        barrier = threading.Barrier(3)

        def upload():
            barrier.wait()
            responses.append(_upload(client, project_id, BRACKET_STEP))

        threads = [threading.Thread(target=upload) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(30)
            assert not thread.is_alive()
        assert [response.status_code for response in responses] == [201, 201]
        with app.state.persistence.sessions() as session:
            versions = session.scalar(
                select(func.count()).select_from(ModelVersion)
            )
            artifacts = session.scalar(
                select(func.count()).select_from(
                    GeometryIdentityArtifactRecord
                )
            )
            assert versions == artifacts == 2
            assert session.scalar(
                select(func.count(func.distinct(ModelVersion.source_sha256)))
            ) == 1


def test_database_enforces_one_artifact_per_step_version(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        uploaded = _upload(client, _project(client), BRACKET_STEP)
        assert uploaded.status_code == 201
        version_id = uploaded.json()["model_version"]["id"]
        with pytest.raises(IntegrityError):
            with app.state.persistence.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO geometry_identity_artifacts "
                        "(model_version_id, model_id, source_sha256, "
                        "artifact_version, hash_domain, canonical_bytes, "
                        "integrity_sha256, created_at) "
                        "SELECT model_version_id, model_id, source_sha256, "
                        "artifact_version, hash_domain, canonical_bytes, "
                        "integrity_sha256, created_at "
                        "FROM geometry_identity_artifacts "
                        "WHERE model_version_id=:id"
                    ),
                    {"id": version_id},
                )
