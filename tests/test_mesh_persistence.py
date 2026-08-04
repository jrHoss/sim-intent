"""Focused durable exact-revision mesh persistence tests."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from alembic import command
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.blob_store import BlobStore
from app.migrations import alembic_config
from app.persistence import (
    MeshLineageConflictError,
    MeshPersistenceError,
    MeshRequestConflictError,
    MeshRevision,
    Persistence,
    create_sqlite_engine,
)
from ir.schema import SimulationIntent
from mesh.artifacts import canonical_topology_bytes
from tests.test_mesh_artifacts import quality, topology
from tests.test_session import intent_payload


def persistence(tmp_path):
    database = tmp_path / "mesh.sqlite3"
    command.upgrade(alembic_config(f"sqlite:///{database}"), "head")
    return Persistence(create_sqlite_engine(f"sqlite:///{database}"), BlobStore(tmp_path / "blobs"))


def parents(store):
    project = store.create_project("mesh")
    model, version = store.create_model_version(
        project_id=project.id, source_name="part.inp", content=b"source",
        model_kind="inp",
    )
    setup, revision = store.create_setup(
        project_id=project.id, model_id=model.id, model_version_id=version.id,
        intent=SimulationIntent.model_validate(intent_payload()), request_id="setup-create",
    )
    return project, model, version, setup, revision


def documents(ids, mesh_id=None, settings_hash="b" * 64):
    mesh_id = mesh_id or str(uuid.uuid4())
    project, model, version, setup, revision = ids
    top = topology(
        mesh_revision_id=mesh_id, project_id=project.id, model_id=model.id,
        model_version_id=version.id, setup_id=setup.id, setup_revision_id=revision.id,
        source_model_sha256=version.source_sha256, mesh_settings_hash=settings_hash,
    )
    digest = hashlib.sha256(canonical_topology_bytes(top)).hexdigest()
    report = quality(
        digest, mesh_revision_id=mesh_id, project_id=project.id, model_id=model.id,
        model_version_id=version.id, setup_id=setup.id, setup_revision_id=revision.id,
        source_model_sha256=version.source_sha256, mesh_settings_hash=settings_hash,
    )
    return top, report


def create(store, ids, *, request_id="mesh-request", predecessor=None, mesh_id=None, settings_hash="b" * 64):
    top, report = documents(ids, mesh_id, settings_hash)
    project, model, version, setup, revision = ids
    return store.create_mesh_revision(
        project_id=project.id, model_id=model.id, model_version_id=version.id,
        setup_id=setup.id, setup_revision_id=revision.id,
        predecessor_mesh_revision_id=predecessor, request_id=request_id,
        topology=top, quality=report,
    )


def test_create_replay_exact_read_restart_and_immutability(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    mesh_id = str(uuid.uuid4())
    first = create(store, ids, mesh_id=mesh_id)
    replay = create(store, ids, mesh_id=mesh_id)
    assert replay.id == first.id
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MeshRevision)) == 1
        first.mesh_settings_hash = "c" * 64
        session.add(first)
        with pytest.raises(ValueError, match="immutable"):
            session.flush()
    restarted = Persistence(create_sqlite_engine(str(store.engine.url)), store.blobs)
    project, model, version, setup, revision = ids
    reopened, top, report = restarted.read_mesh_revision(
        mesh_id, project_id=project.id, model_id=model.id,
        model_version_id=version.id, setup_id=setup.id, setup_revision_id=revision.id,
    )
    assert reopened.id == mesh_id and top and report
    with pytest.raises(IntegrityError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(text("UPDATE mesh_revisions SET request_id='changed' WHERE id=:id"), {"id": mesh_id})


def test_request_conflict_linear_successor_and_no_branching(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    first = create(store, ids)
    with pytest.raises(MeshRequestConflictError):
        create(store, ids, mesh_id=str(uuid.uuid4()))
    successor = create(store, ids, request_id="successor", predecessor=first.id, settings_hash="c" * 64)
    assert successor.predecessor_mesh_revision_id == first.id
    with pytest.raises(MeshLineageConflictError):
        create(store, ids, request_id="branch", predecessor=first.id, settings_hash="d" * 64)


def test_corrupt_or_missing_blob_fails_closed(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    record = create(store, ids)
    store.blobs.path_for_key(record.quality_artifact_key).write_bytes(b"{}")
    project, model, version, setup, revision = ids
    with pytest.raises(MeshPersistenceError, match="mesh_artifact_integrity_failure"):
        store.read_mesh_revision(
            record.id, project_id=project.id, model_id=model.id,
            model_version_id=version.id, setup_id=setup.id, setup_revision_id=revision.id,
        )


def test_migration_head_constraints_triggers_downgrade_reupgrade(tmp_path):
    database = tmp_path / "migration.sqlite3"
    config = alembic_config(f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_sqlite_engine(f"sqlite:///{database}")
    schema = inspect(engine)
    assert "mesh_revisions" in schema.get_table_names()
    assert len(schema.get_foreign_keys("mesh_revisions")) == 6
    assert {item["name"] for item in schema.get_indexes("mesh_revisions")} == {
        "ix_mesh_revisions_project_id", "ix_mesh_revisions_model_id",
        "ix_mesh_revisions_model_version_id", "ix_mesh_revisions_setup_id",
        "ix_mesh_revisions_setup_revision_id",
    }
    triggers = engine.connect().execute(text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='mesh_revisions'")).scalars().all()
    assert set(triggers) == {
        "mesh_revisions_immutable",
        "mesh_revisions_ownership_currentness",
        "mesh_revisions_exact_lineage",
    }
    command.downgrade(config, "0004_geometry_identity_artifacts")
    assert "mesh_revisions" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "mesh_revisions" in inspect(engine).get_table_names()
