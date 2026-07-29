"""Bounded Alembic migration evidence for the R1 relational schema."""

from __future__ import annotations

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
import importlib.util
from pathlib import Path
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import LocalDataConfig
from app.migrations import alembic_config
from app.persistence import Base, create_sqlite_engine


def _migration_0004_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0004_geometry_identity_artifacts.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0004_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_database_upgrade_duplicate_upgrade_and_model_schema(tmp_path):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    inspector = inspect(create_sqlite_engine(local.database_url))
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "projects",
        "models",
        "model_versions",
        "geometry_identity_artifacts",
        "simulation_setups",
        "setup_revisions",
    }
    for table in Base.metadata.sorted_tables:
        assert {column.name for column in table.columns} == {
            column["name"] for column in inspector.get_columns(table.name)
        }


def test_initial_migration_downgrade_and_reupgrade(tmp_path):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert set(inspect(create_sqlite_engine(local.database_url)).get_table_names()) == {
        "alembic_version"
    }
    command.upgrade(config, "head")
    assert "model_versions" in inspect(
        create_sqlite_engine(local.database_url)
    ).get_table_names()


def test_database_rejects_model_version_updates_but_allows_project_cascade(tmp_path):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    command.upgrade(alembic_config(local.database_url), "head")
    engine = create_sqlite_engine(local.database_url)
    project_id, model_id, version_id = (str(uuid.uuid4()) for _ in range(3))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES (:id, 'project', CURRENT_TIMESTAMP)"
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
        connection.execute(
            text(
                "INSERT INTO model_versions "
                "(id, model_id, version, source_sha256, source_name, size_bytes, "
                "media_type, model_kind, blob_key, created_at) VALUES "
                "(:id, :model_id, 1, :digest, 'part.inp', 1, "
                "'application/octet-stream', 'inp', :blob_key, CURRENT_TIMESTAMP)"
            ),
            {
                "id": version_id,
                "model_id": model_id,
                "digest": "a" * 64,
                "blob_key": f"sha256/aa/aa/{'a' * 64}",
            },
        )

    with pytest.raises(IntegrityError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE model_versions SET source_name='changed.inp' WHERE id=:id"),
                {"id": version_id},
            )

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM projects WHERE id=:id"), {"id": project_id}
        )
        assert connection.scalar(
            text("SELECT count(*) FROM models WHERE id=:id"), {"id": model_id}
        ) == 0
        assert connection.scalar(
            text("SELECT count(*) FROM model_versions WHERE id=:id"),
            {"id": version_id},
        ) == 0
    engine.dispose()


@pytest.mark.parametrize(
    "failure_point",
    ["table", "index", "trigger"],
)
def test_geometry_identity_migration_validates_partial_failure_and_retries(
    tmp_path, monkeypatch, failure_point
):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "0003_source_supersession")
    engine = create_sqlite_engine(local.database_url)
    migration = _migration_0004_module()
    validation_name = {
        "table": "_validate_table",
        "index": "_validate_index",
        "trigger": "_validate_trigger",
    }[failure_point]
    original_validation = getattr(migration, validation_name)
    injected = False

    def fail_after_valid_object(connection):
        nonlocal injected
        original_validation(connection)
        if not injected:
            injected = True
            raise RuntimeError(f"injected failure after {failure_point}")

    with engine.connect() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(
            migration, validation_name, fail_after_valid_object
        )
        with pytest.raises(
            RuntimeError, match=f"injected failure after {failure_point}"
        ):
            migration.upgrade()
        connection.commit()
        monkeypatch.setattr(migration, validation_name, original_validation)
        migration.upgrade()
        connection.commit()
        migration._validate_table(connection)
        migration._validate_index(connection)
        migration._validate_trigger(connection)
    inspector = inspect(engine)
    assert "geometry_identity_artifacts" in inspector.get_table_names()
    assert {
        item["name"]
        for item in inspector.get_indexes("geometry_identity_artifacts")
    } == {"ix_geometry_identity_artifacts_model_id"}
    engine.dispose()


def test_geometry_identity_migration_rejects_mismatched_partial_object(tmp_path):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "0003_source_supersession")
    engine = create_sqlite_engine(local.database_url)
    migration = _migration_0004_module()
    with engine.connect() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.exec_driver_sql(
            "DROP INDEX ix_geometry_identity_artifacts_model_id"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_geometry_identity_artifacts_model_id "
            "ON geometry_identity_artifacts(source_sha256)"
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="incompatible pre-existing"):
            migration.upgrade()
    engine.dispose()


@pytest.mark.parametrize(
    "statements",
    [
        (
            "CREATE UNIQUE INDEX extra_geometry_model_unique "
            "ON geometry_identity_artifacts(model_id)",
        ),
        (
            "CREATE INDEX extra_geometry_source_index "
            "ON geometry_identity_artifacts(source_sha256)",
        ),
        (
            "CREATE TRIGGER extra_geometry_before_delete "
            "BEFORE DELETE ON geometry_identity_artifacts "
            "BEGIN SELECT RAISE(ABORT, 'do not delete'); END",
        ),
        (
            "DROP TRIGGER geometry_identity_artifacts_immutable",
            "CREATE TRIGGER geometry_identity_artifacts_immutable "
            "BEFORE UPDATE ON geometry_identity_artifacts "
            "BEGIN SELECT RAISE(ABORT, 'altered behavior'); END",
        ),
        (
            "DROP INDEX ix_geometry_identity_artifacts_model_id",
            "CREATE UNIQUE INDEX ix_geometry_identity_artifacts_model_id "
            "ON geometry_identity_artifacts(model_id)",
        ),
        (
            "DROP INDEX ix_geometry_identity_artifacts_model_id",
            "CREATE INDEX ix_geometry_identity_artifacts_model_id "
            "ON geometry_identity_artifacts(source_sha256, model_id)",
        ),
    ],
    ids=[
        "extra_unique_index",
        "extra_non_unique_index",
        "extra_before_delete_trigger",
        "altered_expected_trigger",
        "altered_index_uniqueness",
        "altered_index_column_order",
    ],
)
def test_geometry_identity_migration_rejects_all_attached_behavior_changes(
    tmp_path, statements
):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "0003_source_supersession")
    engine = create_sqlite_engine(local.database_url)
    migration = _migration_0004_module()
    with engine.connect() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.commit()
        with pytest.raises(RuntimeError, match="incompatible pre-existing"):
            migration.upgrade()
    engine.dispose()


def test_geometry_identity_migration_ignores_objects_on_unrelated_tables(
    tmp_path
):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "0003_source_supersession")
    engine = create_sqlite_engine(local.database_url)
    migration = _migration_0004_module()
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX unrelated_project_name ON projects(name)"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER unrelated_project_delete BEFORE DELETE ON projects "
            "BEGIN SELECT 1; END"
        )
        connection.commit()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.commit()
        migration._validate_table(connection)
        migration._validate_index(connection)
        migration._validate_trigger(connection)
    engine.dispose()


def test_geometry_identity_migration_preserves_historical_step_and_inp_rows(
    tmp_path
):
    local = LocalDataConfig(tmp_path / "data")
    local.root.mkdir(parents=True)
    config = alembic_config(local.database_url)
    command.upgrade(config, "0003_source_supersession")
    engine = create_sqlite_engine(local.database_url)
    project_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
    version_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES (:id, 'historical', CURRENT_TIMESTAMP)"
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
        for number, (version_id, kind) in enumerate(
            zip(version_ids, ("step", "inp")), start=1
        ):
            digest = str(number) * 64
            connection.execute(
                text(
                    "INSERT INTO model_versions "
                    "(id, model_id, version, source_sha256, source_name, "
                    "size_bytes, media_type, model_kind, blob_key, created_at) "
                    "VALUES (:id, :model_id, :version, :digest, :name, 1, "
                    "'application/octet-stream', :kind, :blob_key, "
                    "CURRENT_TIMESTAMP)"
                ),
                {
                    "id": version_id,
                    "model_id": model_id,
                    "version": number,
                    "digest": digest,
                    "name": f"historical.{kind}",
                    "kind": kind,
                    "blob_key": f"sha256/{digest[:2]}/{digest[2:4]}/{digest}",
                },
            )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_sqlite_engine(local.database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM model_versions")) == 2
        assert connection.scalar(
            text("SELECT count(*) FROM geometry_identity_artifacts")
        ) == 0
    engine.dispose()
    command.downgrade(config, "0003_source_supersession")
    engine = create_sqlite_engine(local.database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM model_versions")) == 2
    engine.dispose()
