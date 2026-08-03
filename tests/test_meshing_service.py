"""Focused R5.2 application-service and R5.1 publication integration."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import uuid
from pathlib import Path
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.blob_store import (
    BlobCoordinationPathError,
    BlobCoordinationTimeoutError,
    BlobIntegrityError,
)
from app.config import LocalDataConfig
from app.ingestion import QuarantinedUpload
from app.meshing import MeshingServiceError
from app.persistence import MeshRequestConflictError, MeshRevision
from app.runtime_mode import RuntimeMode
from app.server import create_app
from ir.schema import SimulationIntent
from mesh.artifacts import canonical_quality_bytes
from mesh.profile import GMSH_TET_V1
from tests.test_engineering_setup import payload

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_WORKER = Path(__file__).parent / "fake_mesh_worker.py"


def ready_intent(size: float = 10.0) -> SimulationIntent:
    document = copy.deepcopy(payload())
    document["mesh_settings"] = {
        "global_element_size_mm": size,
        "element_type": "tetrahedral", "element_order": "first_order",
        "mesher": "gmsh", "mesher_preset": "gmsh_tet_v1",
        "target_size_original": {"value": size, "unit": "mm"},
    }
    return SimulationIntent.model_validate(document)


def parents(client, app, *, source="bracket.step"):
    assert app.state.ingestion.gmsh_coordinator is app.state.meshing.coordinator
    project_response = client.post("/api/v1/projects", json={"name": "mesh"})
    assert project_response.status_code == 201
    project = project_response.json()
    upload = client.post(
        f"/api/v1/projects/{project['id']}/models",
        files={"file": (source, (FIXTURES / source).read_bytes(), "application/step")},
    )
    assert upload.status_code == 201, upload.text
    model = upload.json()
    version_id = model["model_version"]["id"]
    artifact_response = client.get(
        f"/api/v1/model-versions/{version_id}/geometry-identity"
    )
    assert artifact_response.status_code == 200, artifact_response.text
    artifact = artifact_response.json()
    unique_faces = [face for face in artifact["faces"] if not face["ambiguous"]]
    document = ready_intent().model_dump(mode="json")
    assert len(unique_faces) >= len(document["regions"])
    for region, face in zip(
        document["regions"], unique_faces[: len(document["regions"])], strict=True
    ):
        region["cad_face_target"] = {
            "model_version_id": version_id,
            "artifact_sha256": artifact["artifact_sha256"],
            "resolution": "resolved",
            "stable_identities": [face["stable_identity"]],
            "source_face_tags": [face["source_ref"]],
        }
    setup, revision = app.state.persistence.create_setup(
        project_id=project["id"], model_id=model["model_id"],
        model_version_id=version_id,
        intent=SimulationIntent.model_validate(document), request_id="setup-create",
    )
    return project, model, setup, revision


def resized_intent(app, revision, size: float) -> SimulationIntent:
    document = app.state.persistence.revision_intent(revision).model_dump(mode="json")
    document["mesh_settings"] = {
        "global_element_size_mm": size,
        "element_type": "tetrahedral", "element_order": "first_order",
        "mesher": "gmsh", "mesher_preset": "gmsh_tet_v1",
        "target_size_original": {"value": size, "unit": "mm"},
    }
    return SimulationIntent.model_validate(document)


def generate(service, project, model, setup, revision, *, request="mesh-1", predecessor=None):
    return asyncio.run(service.generate_and_publish(
        project_id=project["id"], model_id=model["model_id"],
        model_version_id=model["model_version"]["id"], setup_id=setup.id,
        setup_revision_id=revision.id, request_id=request,
        predecessor_mesh_revision_id=predecessor,
    ))


def test_first_mesh_replay_restart_and_exact_read(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        first = generate(app.state.meshing, project, model, setup, revision)
        assert first.predecessor_mesh_revision_id is None
        assert first.mesher_profile_id == GMSH_TET_V1.profile_id
        assert first.mesher_profile_version == GMSH_TET_V1.profile_version
        assert len(GMSH_TET_V1.profile_id) <= (
            MeshRevision.__table__.c.mesher_profile_id.type.length
        )
        assert len(GMSH_TET_V1.profile_version) <= (
            MeshRevision.__table__.c.mesher_profile_version.type.length
        )
        app.state.meshing._worker_command_prefix = [
            sys.executable, str(FAKE_WORKER), "crash",
        ]
        replay = generate(app.state.meshing, project, model, setup, revision)
        assert replay.id == first.id
        assert replay.topology_sha256 == first.topology_sha256
        assert replay.quality_sha256 == first.quality_sha256
        reopened, topology, quality = app.state.persistence.read_mesh_revision(
            first.id, project_id=project["id"], model_id=model["model_id"],
            model_version_id=model["model_version"]["id"], setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        assert reopened.id == first.id and topology and quality
        topology_document = json.loads(topology)
        quality_document = json.loads(quality)
        assert topology_document["mesher_profile_id"] == (
            GMSH_TET_V1.profile_id
        )
        assert quality_document["mesher_profile_id"] == (
            GMSH_TET_V1.profile_id
        )
        assert topology_document["mesher_profile_version"] == (
            GMSH_TET_V1.profile_version
        )
        assert quality_document["mesher_profile_version"] == (
            GMSH_TET_V1.profile_version
        )
        identifiers = (project, model, setup.id, revision.id, first.id)

    restarted = create_app(
        tmp_path / "legacy-restart", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted):
        project, model, setup_id, revision_id, first_id = identifiers
        setup = restarted.state.persistence.get_setup(setup_id)
        revision = restarted.state.persistence.get_setup_revision_by_id(revision_id)
        replay = generate(
            restarted.state.meshing, project, model, setup, revision
        )
        assert replay.id == first_id
        assert replay.topology_sha256 == first.topology_sha256
        assert replay.quality_sha256 == first.quality_sha256


def test_replay_conflicts_for_a_materially_different_resolved_profile(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        first = generate(app.state.meshing, project, model, setup, revision)
        existing = app.state.persistence.get_mesh_revision_by_request(
            project_id=project["id"], request_id="mesh-1"
        )
        assert existing is not None and existing.id == first.id
        existing.mesher_profile_version = f"2:{'f' * 64}"
        monkeypatch.setattr(
            app.state.persistence,
            "get_mesh_revision_by_request",
            lambda **_kwargs: existing,
        )
        app.state.meshing._worker_command_prefix = [
            sys.executable, str(FAKE_WORKER), "crash",
        ]
        with pytest.raises(MeshingServiceError) as failure:
            generate(app.state.meshing, project, model, setup, revision)
        assert failure.value.code == "mesh_request_conflict"
        assert _mesh_count(app.state.persistence) == 1


def test_successor_remesh_conflict_stale_revision_and_old_readability(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        first = generate(app.state.meshing, project, model, setup, revision)
        with pytest.raises(MeshingServiceError, match="mesh_request_conflict"):
            generate(app.state.meshing, project, model, setup, revision,
                     predecessor=first.id)
        second_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id, expected_revision=1, request_id="mesh-size-12",
            mutation_type="mesh_settings", intent=resized_intent(app, revision, 12.0),
        )
        with pytest.raises(MeshingServiceError, match="stale_setup_revision"):
            generate(app.state.meshing, project, model, setup, revision,
                     request="stale-revision")
        successor = generate(
            app.state.meshing, project, model, setup, second_revision,
            request="mesh-2", predecessor=first.id,
        )
        assert successor.predecessor_mesh_revision_id == first.id
        old, _, _ = app.state.persistence.read_mesh_revision(
            first.id, project_id=project["id"], model_id=model["model_id"],
            model_version_id=model["model_version"]["id"], setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        assert old.id == first.id


def test_stale_source_wrong_owner_and_failed_generation_publish_nothing(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        other = client.post("/api/v1/projects", json={"name": "other"}).json()
        with pytest.raises(MeshingServiceError, match="wrong_owner_setup"):
            asyncio.run(app.state.meshing.generate_and_publish(
                project_id=other["id"], model_id=model["model_id"],
                model_version_id=model["model_version"]["id"], setup_id=setup.id,
                setup_revision_id=revision.id, request_id="wrong-owner",
            ))
        before_blobs = set(app.state.persistence.blobs.iter_final_blobs())
        app.state.meshing._worker_command_prefix = [
            sys.executable, str(FAKE_WORKER), "unavailable",
        ]
        with pytest.raises(MeshingServiceError, match="gmsh_unavailable"):
            generate(app.state.meshing, project, model, setup, revision,
                     request="failed-generation")
        with app.state.persistence.sessions() as session:
            assert session.scalar(select(func.count()).select_from(MeshRevision)) == 0
        assert set(app.state.persistence.blobs.iter_final_blobs()) == before_blobs
        assert list(config.worker_root.iterdir()) == []

        app.state.meshing._worker_command_prefix = None
        replacement = client.post(
            f"/api/v1/projects/{project['id']}/models/{model['model_id']}/versions",
            files={"file": ("plate_hole.step",
                (FIXTURES / "plate_hole.step").read_bytes(), "application/step")},
        )
        assert replacement.status_code == 201, replacement.text
        with pytest.raises(MeshingServiceError, match="stale_source"):
            generate(app.state.meshing, project, model, setup, revision,
                     request="stale-source")

def _raw_mesh(target_size: float = 10.0):
    return {
        "gmsh_version": GMSH_TET_V1.gmsh_version,
        "profile_id": GMSH_TET_V1.profile_id,
        "profile_version": GMSH_TET_V1.profile_version,
        "target_size_mm": target_size,
        "nodes": [
            {"tag": 1, "coordinates": [0.0, 0.0, 0.0]},
            {"tag": 2, "coordinates": [1.0, 0.0, 0.0]},
            {"tag": 3, "coordinates": [0.0, 1.0, 0.0]},
            {"tag": 4, "coordinates": [0.0, 0.0, 1.0]},
        ],
        "tetrahedra": [[1, 2, 3, 4]],
    }


def _mesh_count(store):
    with store.sessions() as session:
        return session.scalar(select(func.count()).select_from(MeshRevision))


def _drop_immutability_and_update(store, statement, parameters):
    with store.engine.begin() as connection:
        connection.execute(text(
            "DROP TRIGGER mesh_revisions_immutable"
        ))
        connection.execute(text(statement), parameters)


def _tamper_replay_artifact(store, record, mode):
    topology_path = store.blobs.path_for_key(
        record.topology_artifact_key
    )
    quality_path = store.blobs.path_for_key(record.quality_artifact_key)
    if mode == "missing_topology":
        topology_path.unlink()
    elif mode == "missing_quality":
        quality_path.unlink()
    elif mode == "corrupt_topology":
        topology_path.write_bytes(b"{\"corrupt\":true}\n")
    elif mode == "corrupt_quality":
        quality_path.write_bytes(b"{\"corrupt\":true}\n")
    elif mode == "hash_mismatch":
        _drop_immutability_and_update(
            store,
            "UPDATE mesh_revisions SET topology_sha256=:digest WHERE id=:id",
            {"digest": "f" * 64, "id": record.id},
        )
    elif mode == "binding_mismatch":
        quality = json.loads(quality_path.read_bytes())
        quality["setup_revision_id"] = (
            "00000000-0000-4000-8000-000000000001"
        )
        changed = canonical_quality_bytes(quality)
        digest = hashlib.sha256(changed).hexdigest()
        key = store.blobs.publish(changed, digest)
        _drop_immutability_and_update(
            store,
            "UPDATE mesh_revisions SET "
            "quality_artifact_key=:key, quality_sha256=:digest, "
            "quality_size_bytes=:size WHERE id=:id",
            {
                "key": key,
                "digest": digest,
                "size": len(changed),
                "id": record.id,
            },
        )
    else:
        raise AssertionError(mode)


@pytest.mark.parametrize(
    "mode",
    [
        "missing_topology",
        "missing_quality",
        "corrupt_topology",
        "corrupt_quality",
        "hash_mismatch",
        "binding_mismatch",
    ],
)
def test_restart_replay_integrity_failures_do_not_regenerate_or_publish(
    tmp_path, monkeypatch, mode
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def deterministic_worker(*_args):
            return _raw_mesh()

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        record = generate(
            app.state.meshing, project, model, setup, revision
        )
        assert record.mesher_profile_version == GMSH_TET_V1.profile_version
        _tamper_replay_artifact(app.state.persistence, record, mode)
        expected_blobs = set(
            app.state.persistence.blobs.iter_final_blobs()
        )
        identifiers = (
            project,
            model,
            setup.id,
            revision.id,
            record.id,
        )

    restarted = create_app(
        tmp_path / "legacy-restart",
        mode=RuntimeMode.TEST,
        data_config=config,
    )
    with TestClient(restarted):
        project, model, setup_id, revision_id, record_id = identifiers
        setup = restarted.state.persistence.get_setup(setup_id)
        revision = restarted.state.persistence.get_setup_revision_by_id(
            revision_id
        )
        restarted.state.meshing._worker_command_prefix = [
            sys.executable,
            str(FAKE_WORKER),
            "crash",
        ]
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                restarted.state.meshing,
                project,
                model,
                setup,
                revision,
            )
        assert failure.value.code == "mesh_replay_integrity_failure"
        assert str(tmp_path) not in str(failure.value)
        assert _mesh_count(restarted.state.persistence) == 1
        assert set(
            restarted.state.persistence.blobs.iter_final_blobs()
        ) == expected_blobs
        if config.worker_root.exists():
            assert list(config.worker_root.iterdir()) == []
        with restarted.state.persistence.sessions() as session:
            assert session.get(MeshRevision, record_id) is not None


def test_race_replay_also_uses_exact_read_without_retry(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def initial_worker(*_args):
            return _raw_mesh()

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", initial_worker
        )
        record = generate(
            app.state.meshing, project, model, setup, revision
        )
        app.state.persistence.blobs.path_for_key(
            record.quality_artifact_key
        ).write_bytes(b"corrupt")
        expected_blobs = set(
            app.state.persistence.blobs.iter_final_blobs()
        )
        real_get = app.state.persistence.get_mesh_revision_by_request
        lookup_count = 0

        def race_lookup(**kwargs):
            nonlocal lookup_count
            lookup_count += 1
            return None if lookup_count == 1 else real_get(**kwargs)

        worker_calls = 0

        async def racing_worker(*_args):
            nonlocal worker_calls
            worker_calls += 1
            return _raw_mesh()

        def lost_race(**_kwargs):
            raise MeshRequestConflictError("request_id_conflict")

        monkeypatch.setattr(
            app.state.persistence,
            "get_mesh_revision_by_request",
            race_lookup,
        )
        monkeypatch.setattr(
            app.state.persistence,
            "create_mesh_revision",
            lost_race,
        )
        monkeypatch.setattr(
            app.state.meshing, "_run_worker", racing_worker
        )
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing, project, model, setup, revision
            )
        assert failure.value.code == "mesh_replay_integrity_failure"
        assert worker_calls == 1
        assert lookup_count == 2
        assert _mesh_count(app.state.persistence) == 1
        assert set(
            app.state.persistence.blobs.iter_final_blobs()
        ) == expected_blobs
        assert str(tmp_path) not in str(failure.value)


@pytest.mark.parametrize(
    ("worker_mode", "expected_code"),
    [
        ("huge", "mesh_numeric_range_failure"),
        ("duplicate", "duplicate_node_coordinates"),
    ],
)
def test_generation_boundary_failures_publish_nothing_and_cleanup(
    tmp_path, worker_mode, expected_code
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        before_blobs = set(
            app.state.persistence.blobs.iter_final_blobs()
        )
        app.state.meshing._worker_command_prefix = [
            sys.executable,
            str(FAKE_WORKER),
            worker_mode,
        ]
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing,
                project,
                model,
                setup,
                revision,
                request=f"mesh-{worker_mode}",
            )
        assert failure.value.code == expected_code
        assert str(tmp_path) not in str(failure.value)
        assert _mesh_count(app.state.persistence) == 0
        assert set(
            app.state.persistence.blobs.iter_final_blobs()
        ) == before_blobs
        assert list(config.worker_root.iterdir()) == []


def test_root_rule_and_non_leaf_predecessor_fail_closed(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def deterministic_worker(
            _source, _suffix, target_size
        ):
            await asyncio.sleep(0)
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        root = generate(
            app.state.meshing, project, model, setup, revision
        )
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing,
                project,
                model,
                setup,
                revision,
                request="parallel-root",
            )
        assert failure.value.code == "mesh_lineage_conflict"
        assert _mesh_count(app.state.persistence) == 1

        second_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id,
            expected_revision=1,
            request_id="mesh-size-12",
            mutation_type="mesh_settings",
            intent=resized_intent(app, revision, 12.0),
        )
        successor = generate(
            app.state.meshing,
            project,
            model,
            setup,
            second_revision,
            request="successor",
            predecessor=root.id,
        )
        third_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id,
            expected_revision=2,
            request_id="mesh-size-14",
            mutation_type="mesh_settings",
            intent=resized_intent(app, second_revision, 14.0),
        )
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing,
                project,
                model,
                setup,
                third_revision,
                request="non-leaf",
                predecessor=root.id,
            )
        assert failure.value.code == "mesh_lineage_conflict"
        assert _mesh_count(app.state.persistence) == 2
        previous, _, _ = app.state.persistence.read_mesh_revision(
            root.id,
            project_id=project["id"],
            model_id=model["model_id"],
            model_version_id=model["model_version"]["id"],
            setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        assert previous.id == root.id
        assert successor.predecessor_mesh_revision_id == root.id


def test_concurrent_root_attempts_create_exactly_one_root(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def deterministic_worker(
            _source, _suffix, target_size
        ):
            await asyncio.sleep(0)
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )

        async def scenario():
            common = {
                "project_id": project["id"],
                "model_id": model["model_id"],
                "model_version_id": model["model_version"]["id"],
                "setup_id": setup.id,
                "setup_revision_id": revision.id,
            }
            return await asyncio.gather(
                app.state.meshing.generate_and_publish(
                    **common, request_id="root-a"
                ),
                app.state.meshing.generate_and_publish(
                    **common, request_id="root-b"
                ),
                return_exceptions=True,
            )

        results = asyncio.run(scenario())
        successes = [
            item for item in results if isinstance(item, MeshRevision)
        ]
        failures = [
            item for item in results
            if isinstance(item, MeshingServiceError)
        ]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == "mesh_lineage_conflict"
        assert successes[0].predecessor_mesh_revision_id is None
        assert _mesh_count(app.state.persistence) == 1


def test_concurrent_successors_create_one_leaf_and_preserve_root(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def deterministic_worker(
            _source, _suffix, target_size
        ):
            await asyncio.sleep(0)
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        root = generate(
            app.state.meshing, project, model, setup, revision
        )
        second_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id,
            expected_revision=1,
            request_id="mesh-size-12",
            mutation_type="mesh_settings",
            intent=resized_intent(app, revision, 12.0),
        )

        async def scenario():
            common = {
                "project_id": project["id"],
                "model_id": model["model_id"],
                "model_version_id": model["model_version"]["id"],
                "setup_id": setup.id,
                "setup_revision_id": second_revision.id,
                "predecessor_mesh_revision_id": root.id,
            }
            return await asyncio.gather(
                app.state.meshing.generate_and_publish(
                    **common, request_id="successor-a"
                ),
                app.state.meshing.generate_and_publish(
                    **common, request_id="successor-b"
                ),
                return_exceptions=True,
            )

        results = asyncio.run(scenario())
        successes = [
            item for item in results if isinstance(item, MeshRevision)
        ]
        failures = [
            item for item in results
            if isinstance(item, MeshingServiceError)
        ]
        assert len(successes) == len(failures) == 1
        assert failures[0].code == "mesh_lineage_conflict"
        assert successes[0].predecessor_mesh_revision_id == root.id
        assert _mesh_count(app.state.persistence) == 2
        reopened, _, _ = app.state.persistence.read_mesh_revision(
            root.id,
            project_id=project["id"],
            model_id=model["model_id"],
            model_version_id=model["model_version"]["id"],
            setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        assert reopened.id == root.id


def test_ambiguous_existing_lineage_fails_closed(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def deterministic_worker(
            _source, _suffix, target_size
        ):
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        root = generate(
            app.state.meshing, project, model, setup, revision
        )
        with app.state.persistence.transaction() as session:
            session.add(MeshRevision(
                id=str(uuid.uuid4()),
                project_id=root.project_id,
                model_id=root.model_id,
                model_version_id=root.model_version_id,
                setup_id=root.setup_id,
                setup_revision_id=root.setup_revision_id,
                predecessor_mesh_revision_id=None,
                topology_artifact_key=root.topology_artifact_key,
                topology_sha256=root.topology_sha256,
                topology_size_bytes=root.topology_size_bytes,
                topology_media_type=root.topology_media_type,
                topology_schema_version=root.topology_schema_version,
                quality_artifact_key=root.quality_artifact_key,
                quality_sha256=root.quality_sha256,
                quality_size_bytes=root.quality_size_bytes,
                quality_media_type=root.quality_media_type,
                quality_schema_version=root.quality_schema_version,
                source_model_sha256=root.source_model_sha256,
                mesh_settings_hash=root.mesh_settings_hash,
                mesher_profile_id=root.mesher_profile_id,
                mesher_profile_version=root.mesher_profile_version,
                request_id="injected-ambiguous-root",
                canonical_request_hash="e" * 64,
                created_at=root.created_at,
            ))
        second_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id,
            expected_revision=1,
            request_id="mesh-size-12",
            mutation_type="mesh_settings",
            intent=resized_intent(app, revision, 12.0),
        )
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing,
                project,
                model,
                setup,
                second_revision,
                request="ambiguous-successor",
                predecessor=root.id,
            )
        assert failure.value.code == "mesh_lineage_conflict"
        assert _mesh_count(app.state.persistence) == 2


def test_setup_timestamp_provenance_is_stable_across_replay_and_remesh(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)

        async def deterministic_worker(
            _source, _suffix, target_size
        ):
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        first = generate(
            app.state.meshing, project, model, setup, revision
        )
        _, first_topology, _ = app.state.persistence.read_mesh_revision(
            first.id,
            project_id=project["id"],
            model_id=model["model_id"],
            model_version_id=model["model_version"]["id"],
            setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        first_provenance = json.loads(first_topology)["provenance"]
        expected_first = revision.created_at.replace(
            tzinfo=revision.created_at.tzinfo or timezone.utc
        ).astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        assert first_provenance["created_at"] == expected_first
        replay = generate(
            app.state.meshing, project, model, setup, revision
        )
        assert replay.id == first.id
        _, replay_topology, _ = app.state.persistence.read_mesh_revision(
            replay.id,
            project_id=project["id"],
            model_id=model["model_id"],
            model_version_id=model["model_version"]["id"],
            setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        assert json.loads(replay_topology)["provenance"] == first_provenance

        second_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id,
            expected_revision=1,
            request_id="mesh-size-12",
            mutation_type="mesh_settings",
            intent=resized_intent(app, revision, 12.0),
        )
        second = generate(
            app.state.meshing,
            project,
            model,
            setup,
            second_revision,
            request="mesh-2",
            predecessor=first.id,
        )
        _, second_topology, _ = app.state.persistence.read_mesh_revision(
            second.id,
            project_id=project["id"],
            model_id=model["model_id"],
            model_version_id=model["model_version"]["id"],
            setup_id=setup.id,
            setup_revision_id=second_revision.id,
        )
        second_provenance = json.loads(second_topology)["provenance"]
        expected_second = second_revision.created_at.replace(
            tzinfo=second_revision.created_at.tzinfo or timezone.utc
        ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        assert second_provenance["created_at"] == expected_second


def test_integrated_parse_mesh_coordination_timeout_releases_permit(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(
        tmp_path / "data",
        gmsh_slot_wait_seconds=0.02,
        gmsh_slot_max_pending=2,
    )
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        coordinator = app.state.gmsh_coordinator
        assert app.state.ingestion.gmsh_coordinator is coordinator
        assert app.state.meshing.coordinator is coordinator
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holding_parse(_upload, _trace_id=None):
            entered.set()
            await release.wait()
            return {"held": True}

        async def deterministic_worker(
            _source, _suffix, target_size
        ):
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.ingestion, "_parse_isolated", holding_parse
        )
        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        upload = QuarantinedUpload(
            path=tmp_path / "held.step",
            source_name="held.step",
            kind="step",
            size=4,
            sha256="a" * 64,
        )

        async def scenario():
            parse_task = asyncio.create_task(
                app.state.ingestion.parse(upload)
            )
            await entered.wait()
            with pytest.raises(MeshingServiceError) as failure:
                await app.state.meshing.generate_and_publish(
                    project_id=project["id"],
                    model_id=model["model_id"],
                    model_version_id=model["model_version"]["id"],
                    setup_id=setup.id,
                    setup_revision_id=revision.id,
                    request_id="mesh-timeout",
                )
            assert failure.value.code == "gmsh_slot_timeout"
            assert coordinator.pending == 1
            release.set()
            assert await parse_task == {"held": True}
            assert coordinator.pending == 0
            record = await app.state.meshing.generate_and_publish(
                project_id=project["id"],
                model_id=model["model_id"],
                model_version_id=model["model_version"]["id"],
                setup_id=setup.id,
                setup_revision_id=revision.id,
                request_id="mesh-after-release",
            )
            assert coordinator.pending == 0
            return record

        record = asyncio.run(scenario())
        assert record.predecessor_mesh_revision_id is None
        assert _mesh_count(app.state.persistence) == 1


@pytest.mark.parametrize(
    "error_type",
    [BlobCoordinationTimeoutError, BlobCoordinationPathError],
    ids=["timeout", "unsafe-path"],
)
def test_mesh_publication_coordination_failure_is_sanitized_and_atomic(
    tmp_path, monkeypatch, error_type
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        before = {
            path.relative_to(app.state.persistence.blobs.root).as_posix()
            for path in app.state.persistence.blobs.iter_final_blobs()
        }

        async def deterministic_worker(_source, _suffix, target_size):
            return _raw_mesh(target_size)

        sensitive_path = str(tmp_path.resolve() / "private" / "cas.lock")
        error = error_type(f"coordination failed at {sensitive_path}")

        def fail():
            raise error

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        monkeypatch.setattr(
            app.state.persistence.blobs.coordination_lock, "acquire", fail
        )
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing,
                project,
                model,
                setup,
                revision,
                request=f"coordination-{type(error).__name__}",
            )
        assert failure.value.code == "mesh_publication_failed"
        assert sensitive_path not in str(failure.value)
        assert _mesh_count(app.state.persistence) == 0
        after = {
            path.relative_to(app.state.persistence.blobs.root).as_posix()
            for path in app.state.persistence.blobs.iter_final_blobs()
        }
        assert after == before


def test_mesh_publication_blob_integrity_failure_is_sanitized_and_atomic(
    tmp_path, monkeypatch
):
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "data"),
    )
    with TestClient(app) as client:
        project, model, setup, revision = parents(client, app)
        before = {
            path.relative_to(app.state.persistence.blobs.root).as_posix()
            for path in app.state.persistence.blobs.iter_final_blobs()
        }

        async def deterministic_worker(_source, _suffix, target_size):
            return _raw_mesh(target_size)

        def fail(*_args, **_kwargs):
            raise BlobIntegrityError(f"corrupt {tmp_path.resolve() / 'private' / 'mesh.bin'}")

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        monkeypatch.setattr(
            app.state.persistence.blobs, "publish_with_status", fail
        )
        with pytest.raises(MeshingServiceError) as failure:
            generate(
                app.state.meshing,
                project,
                model,
                setup,
                revision,
                request="mesh-integrity-failure",
            )
        assert failure.value.code == "mesh_publication_failed"
        assert str(tmp_path.resolve()) not in str(failure.value)
        assert _mesh_count(app.state.persistence) == 0
        after = {
            path.relative_to(app.state.persistence.blobs.root).as_posix()
            for path in app.state.persistence.blobs.iter_final_blobs()
        }
        assert after == before


def test_hydrated_cad_mesh_remesh_source_replacement_and_restart(
    tmp_path, monkeypatch
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(
        tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(app) as client:
        project_response = client.post(
            "/api/v1/projects", json={"name": "cross-feature"}
        )
        project_id = project_response.json()["id"]
        upload = client.post(
            f"/api/v1/projects/{project_id}/models",
            files={
                "file": (
                    "bracket.step",
                    (FIXTURES / "bracket.step").read_bytes(),
                    "application/step",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        model = upload.json()
        version_id = model["model_version"]["id"]
        artifact = client.get(
            f"/api/v1/model-versions/{version_id}/geometry-identity"
        ).json()
        unique_faces = [
            face for face in artifact["faces"] if not face["ambiguous"]
        ]
        document = ready_intent().model_dump(mode="json")
        assert len(unique_faces) >= len(document["regions"])
        for region, face in zip(
            document["regions"],
            unique_faces[: len(document["regions"])],
            strict=True,
        ):
            region["status"] = "proposed"
            region["cad_face_target"] = {
                "resolution": "unresolved",
                "model_version_id": version_id,
                "source_face_tags": [face["source_ref"]],
            }
        created = client.post(
            f"/api/v1/projects/{project_id}/setups",
            json={
                "model_id": model["model_id"],
                "model_version_id": version_id,
                "request_id": "hydrated-create",
                "intent": document,
            },
        )
        assert created.status_code == 201, created.text
        setup_id = created.json()["setup"]["id"]
        current = created.json()["current"]
        assert all(
            region["cad_face_target"]["resolution"] == "resolved"
            for region in current["intent"]["regions"]
        )
        for region in current["intent"]["regions"]:
            confirmed = client.post(
                f"/api/v1/setups/{setup_id}/regions/{region['id']}/confirm",
                json={
                    "expected_revision": current["revision"],
                    "request_id": f"confirm-{region['id']}",
                },
            )
            assert confirmed.status_code == 201, confirmed.text
            current = confirmed.json()
        assert current["engineering_ready"] is True
        initial_targets = {
            region["id"]: region["cad_face_target"]
            for region in current["intent"]["regions"]
        }
        initial_evidence = current["cad_selection_evidence"]
        setup = app.state.persistence.get_setup(setup_id)
        revision = app.state.persistence.get_setup_revision_by_id(current["id"])
        assert setup is not None and revision is not None
        initial_bytes = revision.intent_json

        async def deterministic_worker(_source, _suffix, target_size):
            return _raw_mesh(target_size)

        monkeypatch.setattr(
            app.state.meshing, "_run_worker", deterministic_worker
        )
        first = generate(
            app.state.meshing,
            {"id": project_id},
            model,
            setup,
            revision,
            request="hydrated-mesh-1",
        )
        unchanged = app.state.persistence.get_setup_revision_by_id(revision.id)
        assert unchanged is not None and unchanged.intent_json == initial_bytes
        projected = client.get(f"/api/v1/setups/{setup_id}")
        assert projected.status_code == 200, projected.text
        assert projected.json()["current"]["cad_selection_evidence"] == initial_evidence

        second_revision = app.state.persistence.mutate_setup(
            setup_id=setup.id,
            expected_revision=revision.revision,
            request_id="hydrated-size-12",
            mutation_type="mesh_settings",
            intent=resized_intent(app, revision, 12.0),
        )
        second = generate(
            app.state.meshing,
            {"id": project_id},
            model,
            setup,
            second_revision,
            request="hydrated-mesh-2",
            predecessor=first.id,
        )
        assert second.predecessor_mesh_revision_id == first.id
        first_read, topology_bytes, _ = app.state.persistence.read_mesh_revision(
            first.id,
            project_id=project_id,
            model_id=model["model_id"],
            model_version_id=version_id,
            setup_id=setup.id,
            setup_revision_id=revision.id,
        )
        second_read, _, _ = app.state.persistence.read_mesh_revision(
            second.id,
            project_id=project_id,
            model_id=model["model_id"],
            model_version_id=version_id,
            setup_id=setup.id,
            setup_revision_id=second_revision.id,
        )
        assert first_read.id == first.id and second_read.id == second.id
        assert "stable_identity" not in topology_bytes.decode("utf-8")
        assert "source_face_tag" not in topology_bytes.decode("utf-8")

        replacement = client.post(
            f"/api/v1/projects/{project_id}/models/{model['model_id']}/versions",
            files={
                "file": (
                    "plate_hole.step",
                    (FIXTURES / "plate_hole.step").read_bytes(),
                    "application/step",
                )
            },
        )
        assert replacement.status_code == 201, replacement.text
        with pytest.raises(MeshingServiceError) as stale:
            generate(
                app.state.meshing,
                {"id": project_id},
                model,
                setup,
                second_revision,
                request="mesh-after-source-replacement",
                predecessor=second.id,
            )
        assert stale.value.code == "stale_source"
        identifiers = (
            project_id,
            model["model_id"],
            version_id,
            setup.id,
            revision.id,
            second_revision.id,
            first.id,
            second.id,
        )

    restarted = create_app(
        tmp_path / "legacy-restart", mode=RuntimeMode.TEST, data_config=config
    )
    with TestClient(restarted):
        (
            project_id,
            model_id,
            version_id,
            setup_id,
            first_setup_revision_id,
            second_setup_revision_id,
            first_mesh_id,
            second_mesh_id,
        ) = identifiers
        stale_setup = restarted.state.persistence.get_setup(setup_id)
        assert stale_setup is not None and stale_setup.is_stale is True
        restored_intent = restarted.state.persistence.revision_intent(
            restarted.state.persistence.get_setup_revision_by_id(
                first_setup_revision_id
            )
        )
        assert {
            region.id: region.cad_face_target.model_dump(mode="json")
            for region in restored_intent.regions
        } == initial_targets
        first_read, _, _ = restarted.state.persistence.read_mesh_revision(
            first_mesh_id,
            project_id=project_id,
            model_id=model_id,
            model_version_id=version_id,
            setup_id=setup_id,
            setup_revision_id=first_setup_revision_id,
        )
        second_read, _, _ = restarted.state.persistence.read_mesh_revision(
            second_mesh_id,
            project_id=project_id,
            model_id=model_id,
            model_version_id=version_id,
            setup_id=setup_id,
            setup_revision_id=second_setup_revision_id,
        )
        assert second_read.predecessor_mesh_revision_id == first_read.id
