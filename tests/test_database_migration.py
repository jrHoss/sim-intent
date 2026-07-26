"""Bounded Alembic migration evidence for the R1 relational schema."""

from __future__ import annotations

from alembic import command
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import LocalDataConfig
from app.migrations import alembic_config
from app.persistence import Base, create_sqlite_engine


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
