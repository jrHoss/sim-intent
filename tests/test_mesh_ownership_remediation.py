"""Ownership/currentness and exact-read pair remediation for R5.1."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app.persistence import (
    MeshOwnershipMismatchError,
    MeshPersistenceError,
    MeshRevision,
    SetupSourceSupersededError,
)
from mesh.artifacts import (
    MESH_ARTIFACT_SCHEMA_VERSION,
    MESH_MEDIA_TYPE,
    canonical_quality_bytes,
    canonical_topology_bytes,
)
from tests.test_mesh_artifacts import quality, topology
from tests.test_mesh_persistence import create, documents, parents, persistence


def _mark_setup_stale(store, setup_id: str) -> None:
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE simulation_setups "
                "SET is_stale=1, stale_reason='test_invalidation', "
                "stale_at=CURRENT_TIMESTAMP WHERE id=:setup_id"
            ),
            {"setup_id": setup_id},
        )


def _mark_version_superseded(store, version_id: str) -> None:
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE model_versions SET is_superseded=1 "
                "WHERE id=:version_id"
            ),
            {"version_id": version_id},
        )


def _make_version_non_current(store, model_id: str) -> str:
    replacement_id = str(uuid.uuid4())
    digest = "c" * 64
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_versions "
                "(id, model_id, version, source_sha256, source_name, "
                "size_bytes, media_type, model_kind, blob_key, created_at, "
                "is_superseded) VALUES "
                "(:id, :model_id, 2, :digest, 'replacement.inp', 1, "
                "'application/octet-stream', 'inp', :blob_key, "
                "CURRENT_TIMESTAMP, 0)"
            ),
            {
                "id": replacement_id,
                "model_id": model_id,
                "digest": digest,
                "blob_key": f"sha256/cc/cc/{digest}",
            },
        )
        connection.execute(
            text(
                "UPDATE models SET current_version_id=:replacement_id "
                "WHERE id=:model_id"
            ),
            {"replacement_id": replacement_id, "model_id": model_id},
        )
    return replacement_id


def test_stale_setup_is_rejected_by_authoritative_creation_transaction(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    _mark_setup_stale(store, ids[3].id)
    with pytest.raises(SetupSourceSupersededError):
        create(store, ids)


def test_superseded_model_version_is_rejected_by_creation_transaction(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    _mark_version_superseded(store, ids[2].id)
    with pytest.raises(SetupSourceSupersededError):
        create(store, ids)


def test_non_current_model_version_is_rejected_by_creation_transaction(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    _make_version_non_current(store, ids[1].id)
    with pytest.raises(SetupSourceSupersededError):
        create(store, ids)


def test_source_replacement_before_mesh_creation_rejects_old_setup(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    project, model, _version, _setup, _revision = ids
    store.create_model_version(
        project_id=project.id,
        model_id=model.id,
        source_name="replacement.inp",
        content=b"replacement source",
        model_kind="inp",
    )
    with pytest.raises(SetupSourceSupersededError):
        create(store, ids)


def test_invalidation_between_validation_and_insert_is_typed(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    setup_id = ids[3].id

    def invalidate_before_mesh_flush(session, _flush_context, _instances):
        if any(isinstance(item, MeshRevision) for item in session.new):
            session.execute(
                text(
                    "UPDATE simulation_setups "
                    "SET is_stale=1, stale_reason='raced_invalidation', "
                    "stale_at=CURRENT_TIMESTAMP WHERE id=:setup_id"
                ),
                {"setup_id": setup_id},
            )

    event.listen(
        store.sessions.class_,
        "before_flush",
        invalidate_before_mesh_flush,
        once=True,
    )
    with pytest.raises(SetupSourceSupersededError):
        create(store, ids)


def _clone_mesh_row(store, existing_id: str) -> None:
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mesh_revisions "
                "SELECT :new_id, project_id, model_id, model_version_id, "
                "setup_id, setup_revision_id, NULL, topology_artifact_key, "
                "topology_sha256, topology_size_bytes, topology_media_type, "
                "topology_schema_version, quality_artifact_key, quality_sha256, "
                "quality_size_bytes, quality_media_type, quality_schema_version, "
                "source_model_sha256, mesh_settings_hash, mesher_profile_id, "
                "mesher_profile_version, :request_id, :request_hash, "
                "CURRENT_TIMESTAMP FROM mesh_revisions WHERE id=:existing_id"
            ),
            {
                "new_id": str(uuid.uuid4()),
                "request_id": str(uuid.uuid4()),
                "request_hash": "e" * 64,
                "existing_id": existing_id,
            },
        )


def test_direct_sql_insert_rejects_stale_setup(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    existing = create(store, ids)
    _mark_setup_stale(store, ids[3].id)
    with pytest.raises(IntegrityError, match="mesh source is stale"):
        _clone_mesh_row(store, existing.id)


@pytest.mark.parametrize("invalid_state", ["superseded", "non_current"])
def test_direct_sql_insert_rejects_superseded_or_non_current_version(
    tmp_path, invalid_state
):
    store = persistence(tmp_path)
    ids = parents(store)
    existing = create(store, ids)
    if invalid_state == "superseded":
        _mark_version_superseded(store, ids[2].id)
    else:
        _make_version_non_current(store, ids[1].id)
    with pytest.raises(IntegrityError, match="mesh source is stale"):
        _clone_mesh_row(store, existing.id)


def _insert_artifact_row(
    store,
    ids,
    *,
    row_id: str,
    top: dict,
    report: dict,
    request_id: str,
    row_profile_id: str | None = None,
    row_profile_version: str | None = None,
) -> None:
    top_bytes = canonical_topology_bytes(top)
    report_bytes = canonical_quality_bytes(report)
    top_digest = hashlib.sha256(top_bytes).hexdigest()
    report_digest = hashlib.sha256(report_bytes).hexdigest()
    top_key = store.blobs.publish(top_bytes, top_digest)
    report_key = store.blobs.publish(report_bytes, report_digest)
    project, model, version, setup, revision = ids
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mesh_revisions ("
                "id, project_id, model_id, model_version_id, setup_id, "
                "setup_revision_id, predecessor_mesh_revision_id, "
                "topology_artifact_key, topology_sha256, topology_size_bytes, "
                "topology_media_type, topology_schema_version, "
                "quality_artifact_key, quality_sha256, quality_size_bytes, "
                "quality_media_type, quality_schema_version, "
                "source_model_sha256, mesh_settings_hash, mesher_profile_id, "
                "mesher_profile_version, request_id, canonical_request_hash, "
                "created_at) VALUES ("
                ":id, :project_id, :model_id, :model_version_id, :setup_id, "
                ":setup_revision_id, NULL, :topology_key, :topology_sha256, "
                ":topology_size, :media_type, :schema_version, :quality_key, "
                ":quality_sha256, :quality_size, :media_type, :schema_version, "
                ":source_sha256, :settings_hash, :profile_id, "
                ":profile_version, :request_id, :request_hash, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": row_id,
                "project_id": project.id,
                "model_id": model.id,
                "model_version_id": version.id,
                "setup_id": setup.id,
                "setup_revision_id": revision.id,
                "topology_key": top_key,
                "topology_sha256": top_digest,
                "topology_size": len(top_bytes),
                "media_type": MESH_MEDIA_TYPE,
                "schema_version": MESH_ARTIFACT_SCHEMA_VERSION,
                "quality_key": report_key,
                "quality_sha256": report_digest,
                "quality_size": len(report_bytes),
                "source_sha256": version.source_sha256,
                "settings_hash": top["mesh_settings_hash"],
                "profile_id": row_profile_id or top["mesher_profile_id"],
                "profile_version": (
                    row_profile_version or top["mesher_profile_version"]
                ),
                "request_id": request_id,
                "request_hash": "d" * 64,
            },
        )


@pytest.mark.parametrize(
    ("bypass", "error_type", "error_code"),
    [
        (
            "embedded_binding",
            MeshOwnershipMismatchError,
            "mesh_ownership_mismatch",
        ),
        ("source_hash", MeshPersistenceError, "source_hash_mismatch"),
        ("settings_hash", MeshPersistenceError, "settings_hash_mismatch"),
        (
            "topology_link",
            MeshPersistenceError,
            "mesh_artifact_integrity_failure",
        ),
        (
            "topology_quality_profile_id",
            MeshPersistenceError,
            "mesh_artifact_integrity_failure",
        ),
        (
            "topology_quality_profile_version",
            MeshPersistenceError,
            "mesh_artifact_integrity_failure",
        ),
        (
            "row_mesher_profile",
            MeshPersistenceError,
            "mesh_artifact_integrity_failure",
        ),
        (
            "row_mesher_profile_version",
            MeshPersistenceError,
            "mesh_artifact_integrity_failure",
        ),
        ("element_count", MeshPersistenceError, "malformed_mesh_artifact"),
    ],
)
def test_exact_read_repeats_every_bypassable_pair_invariant(
    tmp_path, bypass, error_type, error_code
):
    store = persistence(tmp_path)
    ids = parents(store)
    row_id = str(uuid.uuid4())
    project, model, version, setup, revision = ids
    artifact_id = (
        str(uuid.uuid4()) if bypass == "embedded_binding" else row_id
    )
    top = topology(
        mesh_revision_id=artifact_id,
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        setup_id=setup.id,
        setup_revision_id=revision.id,
        source_model_sha256=version.source_sha256,
    )
    top_bytes = canonical_topology_bytes(top)
    report = quality(
        hashlib.sha256(top_bytes).hexdigest(),
        mesh_revision_id=artifact_id,
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        setup_id=setup.id,
        setup_revision_id=revision.id,
        source_model_sha256=version.source_sha256,
    )
    if bypass == "source_hash":
        report["source_model_sha256"] = "c" * 64
    elif bypass == "settings_hash":
        report["mesh_settings_hash"] = "c" * 64
    elif bypass == "topology_link":
        report["topology_artifact_sha256"] = "f" * 64
    elif bypass == "topology_quality_profile_id":
        report["mesher_profile_id"] = "different-quality-profile"
    elif bypass == "topology_quality_profile_version":
        report["mesher_profile_version"] = "different-quality-version"
    elif bypass == "element_count":
        report["element_count"] = 2
    _insert_artifact_row(
        store,
        ids,
        row_id=row_id,
        top=top,
        report=report,
        request_id=f"direct-{bypass}",
        row_profile_id=(
            "different-profile"
            if bypass == "row_mesher_profile"
            else None
        ),
        row_profile_version=(
            "different-version"
            if bypass == "row_mesher_profile_version"
            else None
        ),
    )
    with pytest.raises(error_type, match=error_code):
        store.read_mesh_revision(
            row_id,
            project_id=project.id,
            model_id=model.id,
            model_version_id=version.id,
            setup_id=setup.id,
            setup_revision_id=revision.id,
        )


@pytest.mark.parametrize(
    ("profile_field", "different_value"),
    [
        ("mesher_profile_id", "different-quality-profile"),
        ("mesher_profile_version", "different-quality-version"),
    ],
)
def test_creation_rejects_topology_quality_profile_mismatch_before_publication(
    tmp_path, profile_field, different_value
):
    store = persistence(tmp_path)
    ids = parents(store)
    top, report = documents(ids)
    report[profile_field] = different_value
    top_bytes = canonical_topology_bytes(top)
    report_bytes = canonical_quality_bytes(report)
    top_path = store.blobs.path_for_key(
        store.blobs.key(hashlib.sha256(top_bytes).hexdigest())
    )
    report_path = store.blobs.path_for_key(
        store.blobs.key(hashlib.sha256(report_bytes).hexdigest())
    )
    project, model, version, setup, revision = ids

    with pytest.raises(
        MeshPersistenceError, match="mesh_artifact_integrity_failure"
    ):
        store.create_mesh_revision(
            project_id=project.id,
            model_id=model.id,
            model_version_id=version.id,
            setup_id=setup.id,
            setup_revision_id=revision.id,
            predecessor_mesh_revision_id=None,
            request_id=f"profile-mismatch-{profile_field}",
            topology=top,
            quality=report,
        )

    assert not top_path.exists()
    assert not report_path.exists()


def test_exact_read_accepts_three_way_mesher_profile_agreement(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    record = create(store, ids)
    project, model, version, setup, revision = ids
    reopened, top_bytes, report_bytes = store.read_mesh_revision(
        record.id,
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        setup_id=setup.id,
        setup_revision_id=revision.id,
    )
    assert reopened.mesher_profile_id == "hand-authored-test"
    assert reopened.mesher_profile_version == "1"
    assert b'"mesher_profile_id":"hand-authored-test"' in top_bytes
    assert b'"mesher_profile_id":"hand-authored-test"' in report_bytes
