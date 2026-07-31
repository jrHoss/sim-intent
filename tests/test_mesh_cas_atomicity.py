"""Failure-atomic CAS publication regressions for R5.1 mesh creation."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app.persistence import (
    MeshLineageConflictError,
    PersistenceNotFoundError,
)
from mesh.artifacts import canonical_quality_bytes, canonical_topology_bytes
from tests.test_mesh_persistence import create, documents, parents, persistence


def _artifact_paths(store, top: dict, report: dict):
    top_bytes = canonical_topology_bytes(top)
    report_bytes = canonical_quality_bytes(report)
    return (
        store.blobs.path_for_key(
            store.blobs.key(hashlib.sha256(top_bytes).hexdigest())
        ),
        store.blobs.path_for_key(
            store.blobs.key(hashlib.sha256(report_bytes).hexdigest())
        ),
    )


def _fail_second_publication(store, monkeypatch):
    original = store.blobs.publish_with_status
    call_count = 0

    def publish(content, digest):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("quality publication failed")
        return original(content, digest)

    monkeypatch.setattr(store.blobs, "publish_with_status", publish)



def test_publication_status_tracks_preexistence_and_exact_creation(tmp_path):
    store = persistence(tmp_path)
    content = b"mesh CAS status"
    digest = hashlib.sha256(content).hexdigest()
    key, existed_before, created = store.blobs.publish_with_status(
        content, digest
    )
    assert (existed_before, created) == (False, True)
    replay_key, existed_before, created = store.blobs.publish_with_status(
        content, digest
    )
    assert replay_key == key
    assert (existed_before, created) == (True, False)


def test_topology_orphan_removed_when_quality_publication_fails(
    tmp_path, monkeypatch
):
    store = persistence(tmp_path)
    ids = parents(store)
    top, report = documents(ids)
    top_path, report_path = _artifact_paths(store, top, report)
    _fail_second_publication(store, monkeypatch)

    with pytest.raises(RuntimeError, match="quality publication failed"):
        create(store, ids, mesh_id=top["mesh_revision_id"])
    assert not top_path.exists()
    assert not report_path.exists()


def test_both_new_blobs_removed_when_ownership_validation_fails(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    missing_model_id = str(uuid.uuid4())
    project, _model, version, setup, revision = ids
    top, report = documents(ids)
    top["model_id"] = missing_model_id
    report["model_id"] = missing_model_id
    report["topology_artifact_sha256"] = hashlib.sha256(
        canonical_topology_bytes(top)
    ).hexdigest()
    top_path, report_path = _artifact_paths(store, top, report)

    with pytest.raises(PersistenceNotFoundError, match="model"):
        store.create_mesh_revision(
            project_id=project.id,
            model_id=missing_model_id,
            model_version_id=version.id,
            setup_id=setup.id,
            setup_revision_id=revision.id,
            predecessor_mesh_revision_id=None,
            request_id="missing-owner",
            topology=top,
            quality=report,
        )
    assert not top_path.exists()
    assert not report_path.exists()


def test_new_blobs_removed_after_sql_rollback_and_existing_blobs_preserved(
    tmp_path,
):
    store = persistence(tmp_path)
    ids = parents(store)
    mesh_id = str(uuid.uuid4())
    committed = create(store, ids, mesh_id=mesh_id)
    committed_paths = (
        store.blobs.path_for_key(committed.topology_artifact_key),
        store.blobs.path_for_key(committed.quality_artifact_key),
    )
    top, report = documents(ids, mesh_id=mesh_id, settings_hash="c" * 64)
    new_paths = _artifact_paths(store, top, report)

    with pytest.raises(MeshLineageConflictError, match="mesh_revision_id_conflict"):
        create(
            store,
            ids,
            request_id="different-request",
            mesh_id=mesh_id,
            settings_hash="c" * 64,
        )
    assert all(path.exists() for path in committed_paths)
    assert all(not path.exists() for path in new_paths)


def test_cleanup_failure_never_masks_publication_failure(tmp_path, monkeypatch):
    store = persistence(tmp_path)
    ids = parents(store)
    _fail_second_publication(store, monkeypatch)

    def fail_cleanup(**_kwargs):
        raise OSError("cleanup failure must be suppressed")

    monkeypatch.setattr(store, "_cleanup_failed_mesh_publication", fail_cleanup)
    with pytest.raises(RuntimeError, match="quality publication failed") as caught:
        create(store, ids)
    assert "cleanup failure" not in str(caught.value)


@pytest.mark.parametrize("preexisting", ["topology", "both"])
def test_preexisting_blobs_are_preserved_on_failure(
    tmp_path, preexisting
):
    store = persistence(tmp_path)
    ids = parents(store)
    top, report = documents(ids)
    top_bytes = canonical_topology_bytes(top)
    report_bytes = canonical_quality_bytes(report)
    top_digest = hashlib.sha256(top_bytes).hexdigest()
    report_digest = hashlib.sha256(report_bytes).hexdigest()
    top_key = store.blobs.publish(top_bytes, top_digest)
    report_key = None
    if preexisting == "both":
        report_key = store.blobs.publish(report_bytes, report_digest)

    project, _model, version, setup, revision = ids
    missing_model_id = str(uuid.uuid4())
    top["model_id"] = missing_model_id
    report["model_id"] = missing_model_id
    # The binding change changes both digests, so republish the exact failing
    # documents when testing pre-existing operation inputs.
    top_bytes = canonical_topology_bytes(top)
    top_digest = hashlib.sha256(top_bytes).hexdigest()
    report["topology_artifact_sha256"] = top_digest
    report_bytes = canonical_quality_bytes(report)
    report_digest = hashlib.sha256(report_bytes).hexdigest()
    top_key = store.blobs.publish(top_bytes, top_digest)
    if preexisting == "both":
        report_key = store.blobs.publish(report_bytes, report_digest)

    with pytest.raises(PersistenceNotFoundError):
        store.create_mesh_revision(
            project_id=project.id,
            model_id=missing_model_id,
            model_version_id=version.id,
            setup_id=setup.id,
            setup_revision_id=revision.id,
            predecessor_mesh_revision_id=None,
            request_id=f"preexisting-{preexisting}",
            topology=top,
            quality=report,
        )
    assert store.blobs.path_for_key(top_key).exists()
    if report_key is not None:
        assert store.blobs.path_for_key(report_key).exists()


def test_shared_blobs_referenced_by_committed_record_are_preserved(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    mesh_id = str(uuid.uuid4())
    committed = create(store, ids, mesh_id=mesh_id)
    paths = (
        store.blobs.path_for_key(committed.topology_artifact_key),
        store.blobs.path_for_key(committed.quality_artifact_key),
    )

    with pytest.raises(MeshLineageConflictError):
        create(
            store,
            ids,
            request_id="same-content-new-request",
            mesh_id=mesh_id,
        )
    assert all(path.exists() for path in paths)


def test_unrelated_older_orphan_is_untouched(tmp_path, monkeypatch):
    store = persistence(tmp_path)
    ids = parents(store)
    orphan_content = b"unrelated historical orphan"
    orphan_digest = hashlib.sha256(orphan_content).hexdigest()
    orphan_key = store.blobs.publish(orphan_content, orphan_digest)
    orphan_path = store.blobs.path_for_key(orphan_key)
    _fail_second_publication(store, monkeypatch)

    with pytest.raises(RuntimeError, match="quality publication failed"):
        create(store, ids)
    assert orphan_path.read_bytes() == orphan_content
