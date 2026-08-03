"""Migration-level remediation evidence for R5.1 mesh persistence."""

from __future__ import annotations

import uuid

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.blob_store import BlobStore
from app.migrations import alembic_config
from app.persistence import Persistence, create_sqlite_engine
from tests.test_mesh_persistence import create, parents, persistence


MESH_COLUMNS = (
    "id",
    "project_id",
    "model_id",
    "model_version_id",
    "setup_id",
    "setup_revision_id",
    "predecessor_mesh_revision_id",
    "topology_artifact_key",
    "topology_sha256",
    "topology_size_bytes",
    "topology_media_type",
    "topology_schema_version",
    "quality_artifact_key",
    "quality_sha256",
    "quality_size_bytes",
    "quality_media_type",
    "quality_schema_version",
    "source_model_sha256",
    "mesh_settings_hash",
    "mesher_profile_id",
    "mesher_profile_version",
    "request_id",
    "canonical_request_hash",
    "created_at",
)


def _clone_row(store, existing_id: str, **changes) -> None:
    with store.engine.begin() as connection:
        row = dict(
            connection.execute(
                text("SELECT * FROM mesh_revisions WHERE id=:id"),
                {"id": existing_id},
            ).mappings().one()
        )
        row.update(
            id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            canonical_request_hash="e" * 64,
        )
        row.update(changes)
        columns = ", ".join(MESH_COLUMNS)
        values = ", ".join(f":{column}" for column in MESH_COLUMNS)
        connection.execute(
            text(
                f"INSERT INTO mesh_revisions ({columns}) "
                f"VALUES ({values})"
            ),
            row,
        )


@pytest.mark.parametrize("invalid_state", ["stale", "superseded", "non_current"])
def test_insertion_trigger_rejects_every_stale_chain_state(
    tmp_path, invalid_state
):
    store = persistence(tmp_path)
    ids = parents(store)
    existing = create(store, ids)
    project, model, version, setup, _revision = ids
    with store.engine.begin() as connection:
        if invalid_state == "stale":
            connection.execute(
                text(
                    "UPDATE simulation_setups SET is_stale=1, "
                    "stale_reason='migration_test', stale_at=CURRENT_TIMESTAMP "
                    "WHERE id=:id"
                ),
                {"id": setup.id},
            )
        elif invalid_state == "superseded":
            connection.execute(
                text(
                    "UPDATE model_versions SET is_superseded=1 WHERE id=:id"
                ),
                {"id": version.id},
            )
        else:
            replacement_id = str(uuid.uuid4())
            digest = "c" * 64
            connection.execute(
                text(
                    "INSERT INTO model_versions "
                    "(id, model_id, version, source_sha256, source_name, "
                    "size_bytes, media_type, model_kind, blob_key, created_at, "
                    "is_superseded) VALUES "
                    "(:id, :model_id, 2, :digest, 'new.inp', 1, "
                    "'application/octet-stream', 'inp', :blob_key, "
                    "CURRENT_TIMESTAMP, 0)"
                ),
                {
                    "id": replacement_id,
                    "model_id": model.id,
                    "digest": digest,
                    "blob_key": f"sha256/cc/cc/{digest}",
                },
            )
            connection.execute(
                text(
                    "UPDATE models SET current_version_id=:version_id "
                    "WHERE id=:model_id"
                ),
                {"version_id": replacement_id, "model_id": model.id},
            )
    with pytest.raises(IntegrityError, match="mesh source is stale"):
        _clone_row(store, existing.id)


IMMUTABLE_COLUMNS = [
    pytest.param(column, id=column)
    for column in MESH_COLUMNS
]


@pytest.mark.parametrize("column", IMMUTABLE_COLUMNS)
def test_every_mesh_field_category_is_database_immutable(tmp_path, column):
    store = persistence(tmp_path)
    existing = create(store, parents(store))
    with pytest.raises(IntegrityError, match="mesh revisions are immutable"):
        with store.engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE mesh_revisions SET {column}={column} "
                    "WHERE id=:id"
                ),
                {"id": existing.id},
            )


def test_request_and_predecessor_uniqueness_are_database_enforced(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    predecessor = create(store, ids, request_id="predecessor")
    successor = create(
        store,
        ids,
        request_id="successor",
        predecessor=predecessor.id,
        settings_hash="c" * 64,
    )
    with pytest.raises(IntegrityError, match="project_id, mesh_revisions.request_id"):
        _clone_row(
            store,
            predecessor.id,
            request_id=predecessor.request_id,
        )
    with pytest.raises(
        IntegrityError,
        match="predecessor_mesh_revision_id",
    ):
        _clone_row(store, successor.id)


@pytest.mark.parametrize(
    "mismatch",
    [
        "project_id",
        "model_id",
        "model_version_id",
        "setup_id",
        "setup_revision_id",
        "source_model_sha256",
    ],
)
def test_all_ownership_mismatch_branches_reject_direct_insert(
    tmp_path, mismatch
):
    store = persistence(tmp_path)
    first_ids = parents(store)
    second_ids = parents(store)
    existing = create(store, first_ids)
    other_project, other_model, other_version, other_setup, other_revision = (
        second_ids
    )
    replacement = {
        "project_id": other_project.id,
        "model_id": other_model.id,
        "model_version_id": other_version.id,
        "setup_id": other_setup.id,
        "setup_revision_id": other_revision.id,
        "source_model_sha256": "c" * 64,
    }[mismatch]
    with pytest.raises(IntegrityError, match="invalid mesh revision ownership"):
        _clone_row(store, existing.id, **{mismatch: replacement})


def test_predecessor_ownership_mismatch_rejects_direct_insert(tmp_path):
    store = persistence(tmp_path)
    first_ids = parents(store)
    second_ids = parents(store)
    existing = create(store, first_ids)
    unrelated = create(
        store,
        second_ids,
        request_id="other-project-predecessor",
    )
    with pytest.raises(IntegrityError, match="invalid mesh revision lineage"):
        _clone_row(
            store,
            existing.id,
            predecessor_mesh_revision_id=unrelated.id,
        )


def _populate_0004(database_url: str):
    project_id, model_id, version_id, setup_id, revision_id = (
        str(uuid.uuid4()) for _ in range(5)
    )
    engine = create_sqlite_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES (:id, 'populated-0004', CURRENT_TIMESTAMP)"
            ),
            {"id": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO models (id, project_id, created_at) "
                "VALUES (:id, :project_id, CURRENT_TIMESTAMP)"
            ),
            {"id": model_id, "project_id": project_id},
        )
        digest = "a" * 64
        connection.execute(
            text(
                "INSERT INTO model_versions "
                "(id, model_id, version, source_sha256, source_name, "
                "size_bytes, media_type, model_kind, blob_key, created_at, "
                "is_superseded) VALUES "
                "(:id, :model_id, 1, :digest, 'part.inp', 1, "
                "'application/octet-stream', 'inp', :blob_key, "
                "CURRENT_TIMESTAMP, 0)"
            ),
            {
                "id": version_id,
                "model_id": model_id,
                "digest": digest,
                "blob_key": f"sha256/aa/aa/{digest}",
            },
        )
        connection.execute(
            text(
                "UPDATE models SET current_version_id=:version_id "
                "WHERE id=:model_id"
            ),
            {"version_id": version_id, "model_id": model_id},
        )
        connection.execute(
            text(
                "INSERT INTO simulation_setups "
                "(id, project_id, model_id, model_version_id, current_revision, "
                "create_request_id, create_request_sha256, created_at, "
                "updated_at, is_stale) VALUES "
                "(:id, :project_id, :model_id, :version_id, NULL, "
                "'setup-request', :digest, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, 0)"
            ),
            {
                "id": setup_id,
                "project_id": project_id,
                "model_id": model_id,
                "version_id": version_id,
                "digest": "b" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO setup_revisions "
                "(id, setup_id, revision, parent_revision_id, schema_version, "
                "intent_json, intent_sha256, mutation_type, request_id, "
                "mutation_sha256, created_at) VALUES "
                "(:id, :setup_id, 1, NULL, 1, '{}', :digest, 'create', "
                "'setup-request', :digest, CURRENT_TIMESTAMP)"
            ),
            {
                "id": revision_id,
                "setup_id": setup_id,
                "digest": "b" * 64,
            },
        )
        connection.execute(
            text(
                "UPDATE simulation_setups SET current_revision=1 "
                "WHERE id=:setup_id"
            ),
            {"setup_id": setup_id},
        )
    engine.dispose()
    return project_id, model_id, version_id, setup_id, revision_id


def test_populated_0004_upgrade_preserves_data_downgrades_and_reupgrades(
    tmp_path,
):
    database = tmp_path / "populated.sqlite3"
    database_url = f"sqlite:///{database}"
    config = alembic_config(database_url)
    command.upgrade(config, "0004_geometry_identity_artifacts")
    identifiers = _populate_0004(database_url)

    command.upgrade(config, "head")
    engine = create_sqlite_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 1
        assert connection.scalar(text("SELECT count(*) FROM models")) == 1
        assert connection.scalar(text("SELECT count(*) FROM model_versions")) == 1
        assert connection.scalar(text("SELECT count(*) FROM simulation_setups")) == 1
        assert connection.scalar(text("SELECT count(*) FROM setup_revisions")) == 1
        assert connection.scalar(text("SELECT count(*) FROM mesh_revisions")) == 0
        triggers = set(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND tbl_name='mesh_revisions'"
                )
            ).scalars()
        )
        assert triggers == {
            "mesh_revisions_immutable",
            "mesh_revisions_ownership_currentness",
            "mesh_revisions_exact_lineage",
        }
    engine.dispose()

    command.downgrade(config, "0004_geometry_identity_artifacts")
    engine = create_sqlite_engine(database_url)
    assert "mesh_revisions" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 1
        assert connection.scalar(text("SELECT count(*) FROM setup_revisions")) == 1
        assert connection.scalar(
            text(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='trigger' AND tbl_name='mesh_revisions'"
            )
        ) == 0
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_sqlite_engine(database_url)
    assert "mesh_revisions" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0006_merge_r4_r5_heads"
        )
    engine.dispose()
    assert ScriptDirectory.from_config(config).get_heads() == [
        "0006_merge_r4_r5_heads"
    ]
