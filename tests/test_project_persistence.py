"""Focused R1 tests for durable projects and immutable source-model versions."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select
from sqlalchemy.exc import IntegrityError

from app.blob_store import BlobIntegrityError, BlobStore
from app.config import LocalDataConfig
from app.persistence import (
    Model,
    ModelVersion,
    Persistence,
    PersistenceConflictError,
    Project,
    create_sqlite_engine,
)
from app.problems import ApiProblem
from app.runtime_mode import RuntimeMode
from app.server import ModelStore, create_app


def minimal_inp(scale: int = 1) -> bytes:
    return f"""*HEADING
single tetrahedron
*NODE
10, 0, 0, 0
20, {scale}, 0, 0
30, 0, {scale}, 0
40, 0, 0, {scale}
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
*NSET, NSET=FIXED
10
""".encode()


def raw_model_version(
    model_id: str,
    version: int,
    *,
    media_type: str | None = "application/octet-stream",
) -> ModelVersion:
    digest = f"{version % 10}" * 64
    return ModelVersion(
        id=str(uuid.uuid4()),
        model_id=model_id,
        version=version,
        source_sha256=digest,
        source_name=f"raw-{version}.inp",
        size_bytes=1,
        media_type=media_type,
        model_kind="inp",
        blob_key=f"sha256/{digest[:2]}/{digest[2:4]}/{digest}",
        created_at=datetime.now(timezone.utc),
    )


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


@pytest.fixture
def durable(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app):
        yield app, config


def create_project(app, name: str = "Bracket study") -> dict:
    response = request(app, "POST", "/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def upload(app, project_id: str, content: bytes, filename: str = "part.inp"):
    response = request(
        app,
        "POST",
        f"/api/v1/projects/{project_id}/models",
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_read_and_restart_reopen(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app):
        project = create_project(app)
        uploaded = upload(app, project["id"], minimal_inp())
        version = uploaded["model_version"]

        assert request(app, "GET", "/api/v1/projects").json() == [project]
        assert request(app, "GET", f"/api/v1/projects/{project['id']}").json() == project

    restarted = create_app(
        tmp_path / "other-legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted):
        assert request(restarted, "GET", f"/api/v1/projects/{project['id']}").json() == project
        reopened = request(
            restarted, "GET", f"/api/v1/model-versions/{version['id']}"
        )
        assert reopened.status_code == 200
        assert reopened.json() == version
        inventory = request(
            restarted, "GET", f"/api/v1/model-versions/{version['id']}/inventory"
        )
        assert inventory.status_code == 200
        assert inventory.json()["file_sha256"] == version["source_sha256"]
        gltf = request(
            restarted, "GET", f"/api/v1/model-versions/{version['id']}/gltf"
        )
        assert gltf.status_code == 200
        assert gltf.headers["content-type"].startswith("model/gltf+json")
        assert gltf.json()["nodes"]


def test_versions_are_immutable_numbered_and_domain_ids_are_uuids(durable):
    app, _ = durable
    project = create_project(app)
    first = upload(app, project["id"], minimal_inp(), "first.inp")
    model_id = first["model_id"]
    second_response = request(
        app,
        "POST",
        f"/api/v1/projects/{project['id']}/models/{model_id}/versions",
        files={"file": ("second.inp", minimal_inp(2), "application/octet-stream")},
    )
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    versions = request(app, "GET", f"/api/v1/models/{model_id}/versions").json()
    assert [item["version"] for item in versions] == [1, 2]
    assert versions[0]["source_name"] == "first.inp"
    assert versions[1]["source_name"] == "second.inp"
    for domain_id in (project["id"], model_id, versions[0]["id"], versions[1]["id"]):
        assert str(uuid.UUID(domain_id)) == domain_id
        assert domain_id not in {item["source_sha256"] for item in versions}

    persistence: Persistence = app.state.persistence
    with pytest.raises(ValueError, match="immutable"):
        with persistence.transaction() as session:
            record = session.get(ModelVersion, versions[0]["id"])
            record.source_name = "mutated.inp"


def test_identical_bytes_deduplicate_storage_without_conflating_identity(durable):
    app, config = durable
    project = create_project(app)
    content = minimal_inp()
    first = upload(app, project["id"], content, "one.inp")
    second = upload(app, project["id"], content, "two.inp")
    a, b = first["model_version"], second["model_version"]
    assert a["source_sha256"] == b["source_sha256"]
    assert a["id"] != b["id"]
    assert first["model_id"] != second["model_id"]
    assert a["source_name"] != b["source_name"]
    assert len(list(config.blob_root.glob("sha256/*/*/*"))) == 1


def test_transaction_rollback_and_foreign_key_rejection(durable):
    app, _ = durable
    persistence: Persistence = app.state.persistence
    with pytest.raises(RuntimeError):
        with persistence.transaction() as session:
            session.add(Project(name="rolled back"))
            raise RuntimeError("force rollback")
    assert all(project.name != "rolled back" for project in persistence.list_projects())

    with pytest.raises(IntegrityError):
        with persistence.transaction() as session:
            session.execute(
                insert(Model).values(
                    id=str(uuid.uuid4()),
                    project_id=str(uuid.uuid4()),
                )
            )


def test_failed_database_write_creates_no_version_and_cleanup_is_bounded(durable):
    app, config = durable
    persistence: Persistence = app.state.persistence
    content = minimal_inp()
    with pytest.raises(LookupError):
        persistence.create_model_version(
            project_id=str(uuid.uuid4()),
            source_name="orphan.inp",
            content=content,
            model_kind="inp",
        )
    with persistence.sessions() as session:
        assert session.scalar(select(ModelVersion)) is None
    assert len(list(config.blob_root.glob("sha256/*/*/*"))) == 1
    unexpected = config.blob_root / "sha256" / "aa" / "bb" / "unexpected"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_bytes(b"do not remove")
    assert persistence.cleanup_unreferenced_blobs(limit=1) == 1
    assert unexpected.read_bytes() == b"do not remove"


def test_blob_atomicity_hash_verification_and_path_defense(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    content = b"exact bytes"
    digest = hashlib.sha256(content).hexdigest()
    key = store.publish(content, digest)
    assert store.read(key, digest, len(content)) == content
    assert store.publish(content, digest) == key
    assert not list(store.root.glob("sha256/*/*/.upload-*"))
    with pytest.raises(BlobIntegrityError):
        store.publish(content, "0" * 64)
    with pytest.raises(BlobIntegrityError):
        store.path_for_key("../../escape")
    store.path_for_key(key).write_bytes(b"corrupt")
    with pytest.raises(BlobIntegrityError):
        store.read(key, digest, len(content))
    with pytest.raises(BlobIntegrityError):
        store.publish(content, digest)


def test_cleanup_cannot_race_published_blob_before_commit(durable):
    app, config = durable
    persistence: Persistence = app.state.persistence
    project = persistence.create_project("coordinated")
    published = threading.Event()
    release = threading.Event()
    cleanup_started = threading.Event()
    results: dict[str, object] = {}

    def pause_after_publish() -> None:
        published.set()
        assert release.wait(5)

    persistence._after_blob_publish = pause_after_publish

    def create() -> None:
        try:
            results["created"] = persistence.create_model_version(
                project_id=project.id,
                source_name="race.inp",
                content=minimal_inp(),
                model_kind="inp",
            )
        except BaseException as exc:  # captured for deterministic thread assertion
            results["create_error"] = exc

    def cleanup() -> None:
        cleanup_started.set()
        results["removed"] = persistence.cleanup_unreferenced_blobs()

    create_thread = threading.Thread(target=create)
    cleanup_thread = threading.Thread(target=cleanup)
    create_thread.start()
    assert published.wait(5)
    cleanup_thread.start()
    assert cleanup_started.wait(5)
    cleanup_thread.join(0.1)
    assert cleanup_thread.is_alive(), "cleanup must wait for publication + commit"
    release.set()
    create_thread.join(5)
    cleanup_thread.join(5)
    assert "create_error" not in results
    assert results["removed"] == 0
    _, version = results["created"]
    assert persistence.read_version_bytes(version) == minimal_inp()
    assert len(list(config.blob_root.glob("sha256/*/*/*"))) == 1


def test_concurrent_distinct_services_allocate_sequential_versions(durable):
    app, config = durable
    primary: Persistence = app.state.persistence
    project = primary.create_project("concurrent versions")
    model, first = primary.create_model_version(
        project_id=project.id,
        source_name="v1.inp",
        content=minimal_inp(),
        model_kind="inp",
    )
    services = [
        Persistence(create_sqlite_engine(config.database_url), BlobStore(config.blob_root))
        for _ in range(2)
    ]
    barrier = threading.Barrier(3)
    versions: list[int] = []
    errors: list[BaseException] = []

    def create(service: Persistence, scale: int) -> None:
        barrier.wait()
        try:
            _, version = service.create_model_version(
                project_id=project.id,
                model_id=model.id,
                source_name=f"v{scale}.inp",
                content=minimal_inp(scale),
                model_kind="inp",
            )
            versions.append(version.version)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=create, args=(service, index))
        for service, index in zip(services, (2, 3))
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(5)
    for service in services:
        service.dispose()
    assert errors == []
    assert first.version == 1
    assert sorted(versions) == [2, 3]


def test_targeted_model_version_collision_becomes_conflict(durable):
    app, _ = durable
    persistence: Persistence = app.state.persistence
    project = persistence.create_project("targeted conflict")
    model, first = persistence.create_model_version(
        project_id=project.id,
        source_name="first.inp",
        content=minimal_inp(),
        model_kind="inp",
    )
    collision = raw_model_version(model.id, first.version)
    with pytest.raises(PersistenceConflictError):
        with persistence.transaction() as session:
            persistence._insert_model_version(session, collision)
    assert [item.version for item in persistence.list_versions(model.id)] == [1]


def test_not_null_integrity_error_propagates_and_rolls_back(durable):
    app, _ = durable
    persistence: Persistence = app.state.persistence
    project = persistence.create_project("not null")
    model, _ = persistence.create_model_version(
        project_id=project.id,
        source_name="first.inp",
        content=minimal_inp(),
        model_kind="inp",
    )
    invalid = raw_model_version(model.id, 2, media_type=None)
    with pytest.raises(IntegrityError):
        with persistence.transaction() as session:
            persistence._insert_model_version(session, invalid)
    with persistence.sessions() as session:
        assert session.get(ModelVersion, invalid.id) is None
    assert [item.version for item in persistence.list_versions(model.id)] == [1]


def test_foreign_key_integrity_error_propagates_and_rolls_back(durable):
    app, _ = durable
    persistence: Persistence = app.state.persistence
    invalid = raw_model_version(str(uuid.uuid4()), 1)
    with pytest.raises(IntegrityError):
        with persistence.transaction() as session:
            persistence._insert_model_version(session, invalid)
    with persistence.sessions() as session:
        assert session.get(ModelVersion, invalid.id) is None


def test_problem_details_for_validation_not_found_conflict_and_blob_failure(durable):
    app, _ = durable
    malformed = request(app, "GET", "/api/v1/projects/not-a-uuid")
    assert_problem(malformed, 422, "request_validation_failed")

    missing = request(app, "GET", f"/api/v1/projects/{uuid.uuid4()}")
    assert_problem(missing, 404, "project_not_found")

    first_project = create_project(app, "first")
    second_project = create_project(app, "second")
    uploaded = upload(app, first_project["id"], minimal_inp())
    conflict = request(
        app,
        "POST",
        f"/api/v1/projects/{second_project['id']}/models/{uploaded['model_id']}/versions",
        files={"file": ("conflict.inp", minimal_inp(2), "application/octet-stream")},
    )
    assert_problem(conflict, 409, "model_project_conflict")

    version = app.state.persistence.get_version(
        uploaded["model_version"]["id"]
    )
    app.state.persistence.blobs.path_for_key(version.blob_key).write_bytes(b"bad")
    corrupt = request(
        app,
        "GET",
        f"/api/v1/model-versions/{version.id}/inventory",
    )
    assert_problem(corrupt, 500, "source_blob_integrity_failed")


def test_parser_failure_is_safe_and_original_error_is_not_exposed(
    durable, monkeypatch
):
    app, _ = durable
    project = create_project(app, "parser safety")
    secret = "sensitive-customer-sentinel"
    absolute_path = r"C:\private\models\customer.inp"

    async def fail_parse(_upload, _trace_id=None):
        raise ApiProblem(
            status=422,
            code="parser_crash",
            title="Parser failed",
            detail="The isolated source parser failed.",
        ) from RuntimeError(f"parser failed at {absolute_path} with {secret}")

    monkeypatch.setattr(app.state.ingestion, "parse", fail_parse)
    correlation_id = "parser-test-correlation"
    response = request(
        app,
        "POST",
        f"/api/v1/projects/{project['id']}/models",
        files={"file": ("unsafe.inp", minimal_inp(), "application/octet-stream")},
        headers={"x-correlation-id": correlation_id},
    )
    assert_problem(response, 422, "parser_crash")
    assert response.json()["detail"] == "The isolated source parser failed."
    assert response.json()["trace_id"] == correlation_id
    assert secret not in response.text
    assert absolute_path not in response.text


def assert_problem(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == status
    assert payload["code"] == code
    assert payload["trace_id"]
    assert payload["retryable"] is False


def test_openapi_declares_problem_json_for_new_endpoints(durable):
    app, _ = durable
    document = app.openapi()
    for path, methods in document["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for operation in methods.values():
            for status in ("404", "409", "413", "422", "500"):
                content = operation["responses"][status]["content"]
                assert set(content) == {"application/problem+json"}
                assert content["application/problem+json"]["schema"] == {
                    "$ref": "#/components/schemas/ProblemDetails"
                }
    assert document["components"]["schemas"]["ProblemDetails"]["properties"]["code"]


def test_stale_temporary_cleanup_is_count_bounded_and_does_not_follow_symlinks(
    tmp_path,
):
    store = BlobStore(tmp_path / "blobs")
    leaf = store.root / "sha256" / "aa" / "bb"
    leaf.mkdir(parents=True)
    temporary_files = [leaf / f".upload-{index}" for index in range(3)]
    for path in temporary_files:
        path.write_bytes(b"temporary")
        os.utime(path, (0, 0))
    unexpected = leaf / ".upload-directory"
    unexpected.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    symlink = leaf / ".upload-link"
    try:
        symlink.symlink_to(outside)
    except OSError:
        symlink = None

    assert store.cleanup_temporary(older_than_seconds=0, limit=2) == 2
    assert len([path for path in temporary_files if path.exists()]) == 1
    assert unexpected.is_dir()
    assert outside.read_bytes() == b"outside"
    if symlink is not None:
        assert symlink.is_symlink()


def test_temporary_cleanup_tolerates_disappearing_entry(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    leaf = store.root / "sha256" / "aa" / "bb"
    leaf.mkdir(parents=True)
    disappearing = leaf / ".upload-disappearing"
    surviving = leaf / ".upload-surviving"
    for path in (disappearing, surviving):
        path.write_bytes(b"temporary")
        os.utime(path, (0, 0))
    original_stat = Path.stat
    raced = False

    def racing_stat(path, *args, **kwargs):
        nonlocal raced
        if path == disappearing and not raced:
            raced = True
            path.unlink(missing_ok=True)
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert store.cleanup_temporary(older_than_seconds=0, limit=10) == 1
    assert raced
    assert not surviving.exists()


def test_orphan_cleanup_tolerates_disappearing_entry(durable, monkeypatch):
    app, _ = durable
    persistence: Persistence = app.state.persistence
    for scale in (7, 8):
        with pytest.raises(LookupError):
            persistence.create_model_version(
                project_id=str(uuid.uuid4()),
                source_name=f"orphan-{scale}.inp",
                content=minimal_inp(scale),
                model_kind="inp",
            )
    candidates = list(persistence.blobs.iter_final_blobs())
    assert len(candidates) == 2
    disappearing = candidates[0]
    original_unlink = Path.unlink
    raced = False

    def racing_unlink(path, *args, **kwargs):
        nonlocal raced
        if path == disappearing and not raced:
            raced = True
            original_unlink(path, missing_ok=True)
            raise FileNotFoundError(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", racing_unlink)
    assert persistence.cleanup_unreferenced_blobs(limit=10) == 1
    assert raced
    assert not list(persistence.blobs.iter_final_blobs())


def test_default_data_root_is_cwd_independent_and_engine_is_disposed(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("SIM_INTENT_DATA_ROOT", raising=False)
    first_cwd = tmp_path / "one"
    second_cwd = tmp_path / "two"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.chdir(first_cwd)
    first = LocalDataConfig.from_env()
    monkeypatch.chdir(second_cwd)
    second = LocalDataConfig.from_env()
    assert first.root == second.root
    assert first.root.is_absolute()
    assert tmp_path not in first.root.parents

    explicit = LocalDataConfig(tmp_path / "explicit")
    lifecycle_app = create_app(
        tmp_path / "legacy-lifecycle",
        mode=RuntimeMode.TEST,
        data_config=explicit,
    )
    assert not explicit.database_path.exists()
    disposed = threading.Event()
    with TestClient(lifecycle_app):
        persistence = lifecycle_app.state.persistence
        event.listen(
            persistence.engine,
            "engine_disposed",
            lambda _engine: disposed.set(),
        )
        persistence.create_project("lifecycle")
    assert disposed.is_set()
    assert not hasattr(lifecycle_app.state, "persistence")


def test_environment_data_root_is_explicit_and_cwd_safe(tmp_path, monkeypatch):
    configured = tmp_path / "configured-data"
    monkeypatch.setenv("SIM_INTENT_DATA_ROOT", str(configured))
    monkeypatch.chdir(tmp_path)
    assert LocalDataConfig.from_env().root == configured.resolve()
    monkeypatch.setenv("SIM_INTENT_DATA_ROOT", "relative-data")
    with pytest.raises(ValueError, match="absolute"):
        LocalDataConfig.from_env()


@pytest.mark.parametrize("filename", ["../part.inp", "..\\part.inp", "/part.inp"])
def test_project_upload_rejects_traversal_names(durable, filename):
    app, _ = durable
    project = create_project(app)
    response = request(
        app,
        "POST",
        f"/api/v1/projects/{project['id']}/models?filename={filename}",
        content=minimal_inp(),
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 400
