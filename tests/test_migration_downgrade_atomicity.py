"""Atomic downgrade refusal for the integrated R4/R5 migration graph."""

from __future__ import annotations

import hashlib
import uuid

from alembic import command
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.blob_store import BlobStore
from app.migrations import alembic_config
from app.persistence import (
    Model,
    ModelVersion,
    Persistence,
    Project,
    SetupRevision,
    SimulationSetup,
    create_sqlite_engine,
)
from geom.identity import GeometryFaceInput, build_geometry_identity
from ir.schema import SimulationIntent
from tests.test_mesh_migration_remediation import _populate_0004
from tests.test_mesh_persistence import create
from tests.test_session import intent_payload


R4_HEAD = "0005_stable_cad_region_references"
R5_HEAD = "0005_mesh_domain_persistence"
MERGED_HEAD = "0006_merge_r4_r5_heads"
TARGET = "0004_geometry_identity_artifacts"
BLOCKED = (
    "integrated R4/R5 downgrade blocked: immutable setup or mesh revisions "
    "require backup/restore"
)
MESH_INDEXES = {
    "ix_mesh_revisions_project_id",
    "ix_mesh_revisions_model_id",
    "ix_mesh_revisions_model_version_id",
    "ix_mesh_revisions_setup_id",
    "ix_mesh_revisions_setup_revision_id",
}
MESH_TRIGGERS = {
    "mesh_revisions_immutable",
    "mesh_revisions_ownership_currentness",
    "mesh_revisions_exact_lineage",
}
PRESERVED_TABLES = (
    "projects",
    "models",
    "model_versions",
    "geometry_identity_artifacts",
    "simulation_setups",
    "setup_revisions",
    "mesh_revisions",
)


def _upgrade(config, starting_state: str) -> None:
    if starting_state == "merged":
        command.upgrade(config, "head")
    else:
        assert starting_state == "two_heads"
        command.upgrade(config, R4_HEAD)
        command.upgrade(config, R5_HEAD)


def _versions(engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        return tuple(
            sorted(
                connection.scalars(
                    text("SELECT version_num FROM alembic_version")
                )
            )
        )


def _rows(connection, table: str) -> tuple[dict, ...]:
    return tuple(
        dict(row)
        for row in connection.execute(
            text(f"SELECT * FROM {table} ORDER BY rowid")
        ).mappings()
    )


def _snapshot(store: Persistence, mesh_keys: tuple[str, str] | None) -> dict:
    with store.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        result = {
            "versions": tuple(
                sorted(
                    connection.scalars(
                        text("SELECT version_num FROM alembic_version")
                    )
                )
            ),
            "rows": {
                table: _rows(connection, table)
                for table in PRESERVED_TABLES
                if table in tables
            },
            "mesh_schema": tuple(
                tuple(row)
                for row in connection.execute(text(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE tbl_name='mesh_revisions' "
                    "ORDER BY type, name"
                ))
            ),
        }
    result["blobs"] = (
        None
        if mesh_keys is None
        else {
            key: store.blobs.path_for_key(key).read_bytes()
            for key in mesh_keys
        }
    )
    return result


def _step_setup(store: Persistence, label: str):
    source = f"synthetic STEP source for {label}".encode()
    source_sha256 = hashlib.sha256(source).hexdigest()
    version_id = str(uuid.uuid4())
    geometry = build_geometry_identity(
        model_version_id=version_id,
        source_sha256=source_sha256,
        faces=(
            GeometryFaceInput(
                source_ref=1,
                surface_type="plane",
                area=10.0,
                centroid=(0.0, 0.0, 0.0),
                normal=(0.0, 0.0, 1.0),
                boundary_loop_count=1,
            ),
        ),
    )
    project = store.create_project(label)
    model, version = store.create_model_version(
        project_id=project.id,
        source_name="synthetic.step",
        content=source,
        model_kind="step",
        version_id=version_id,
        geometry_identity_bytes=geometry.canonical_bytes(),
    )
    setup, revision = store.create_setup(
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        intent=SimulationIntent.model_validate(intent_payload()),
        request_id=f"setup-{label}",
    )
    return project, model, version, setup, revision


def _assert_mesh_schema_and_teeth(store: Persistence, mesh_id: str) -> None:
    schema = inspect(store.engine)
    assert len(schema.get_foreign_keys("mesh_revisions")) == 6
    assert {item["name"] for item in schema.get_indexes("mesh_revisions")} == (
        MESH_INDEXES
    )
    assert {item["name"] for item in schema.get_check_constraints(
        "mesh_revisions"
    )} == {
        "ck_mesh_artifact_sizes",
        "ck_mesh_schema_versions",
        "ck_mesh_not_self_predecessor",
    }
    assert {item["name"] for item in schema.get_unique_constraints(
        "mesh_revisions"
    )} == {
        "uq_project_mesh_request_id",
        "uq_mesh_revision_predecessor_successor",
    }
    with store.engine.connect() as connection:
        assert set(connection.scalars(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name='mesh_revisions'"
        ))) == MESH_TRIGGERS
        original = dict(connection.execute(text(
            "SELECT * FROM mesh_revisions WHERE id=:id"
        ), {"id": mesh_id}).mappings().one())
    with pytest.raises(IntegrityError, match="mesh revisions are immutable"):
        with store.engine.begin() as connection:
            connection.execute(text(
                "UPDATE mesh_revisions SET request_id=request_id WHERE id=:id"
            ), {"id": mesh_id})
    clone = dict(original)
    clone.update(
        id=str(uuid.uuid4()),
        request_id=f"invalid-size-{uuid.uuid4()}",
        canonical_request_hash="f" * 64,
        topology_size_bytes=-1,
    )
    columns = tuple(clone)
    with pytest.raises(IntegrityError, match="ck_mesh_artifact_sizes"):
        with store.engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO mesh_revisions ({', '.join(columns)}) "
                    f"VALUES ({', '.join(':' + column for column in columns)})"
                ),
                clone,
            )
    with store.engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM mesh_revisions"
        )) == 1


@pytest.mark.parametrize(
    ("starting_state", "expected_versions"),
    [
        pytest.param("merged", (MERGED_HEAD,), id="merged-0006"),
        pytest.param(
            "two_heads",
            tuple(sorted((R4_HEAD, R5_HEAD))),
            id="two-0005-heads",
        ),
    ],
)
def test_rejected_integrated_downgrade_preserves_every_revision_and_artifact(
    tmp_path, starting_state, expected_versions
):
    database = tmp_path / f"{starting_state}.sqlite3"
    database_url = f"sqlite:///{database}"
    config = alembic_config(database_url)
    _upgrade(config, starting_state)
    blob_root = tmp_path / f"{starting_state}-blobs"
    store = Persistence(create_sqlite_engine(database_url), BlobStore(blob_root))
    parents = _step_setup(store, starting_state)
    mesh = create(store, parents, request_id=f"mesh-{starting_state}")
    project, model, version, setup, revision = parents
    mesh_keys = (mesh.topology_artifact_key, mesh.quality_artifact_key)
    mesh_read_before = store.read_mesh_revision(
        mesh.id,
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        setup_id=setup.id,
        setup_revision_id=revision.id,
    )[1:]
    geometry_read_before = store.read_geometry_identity(version.id)[1:]
    before = _snapshot(store, mesh_keys)
    assert before["versions"] == expected_versions
    store.engine.dispose()

    with pytest.raises(RuntimeError) as caught:
        command.downgrade(config, TARGET)
    assert str(caught.value) == BLOCKED

    restarted = Persistence(create_sqlite_engine(database_url), BlobStore(blob_root))
    assert _snapshot(restarted, mesh_keys) == before
    assert restarted.read_mesh_revision(
        mesh.id,
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        setup_id=setup.id,
        setup_revision_id=revision.id,
    )[1:] == mesh_read_before
    assert restarted.read_geometry_identity(version.id)[1:] == geometry_read_before
    _assert_mesh_schema_and_teeth(restarted, mesh.id)
    restarted.engine.dispose()


def test_two_head_mesh_history_without_v3_setup_is_not_silently_dropped(tmp_path):
    database = tmp_path / "mesh-only.sqlite3"
    database_url = f"sqlite:///{database}"
    config = alembic_config(database_url)
    command.upgrade(config, TARGET)
    identifiers = _populate_0004(database_url)
    command.upgrade(config, R4_HEAD)
    command.upgrade(config, R5_HEAD)
    blob_root = tmp_path / "mesh-only-blobs"
    store = Persistence(create_sqlite_engine(database_url), BlobStore(blob_root))
    with store.sessions() as session:
        parents = (
            session.get(Project, identifiers[0]),
            session.get(Model, identifiers[1]),
            session.get(ModelVersion, identifiers[2]),
            session.get(SimulationSetup, identifiers[3]),
            session.get(SetupRevision, identifiers[4]),
        )
    mesh = create(store, parents, request_id="mesh-with-legacy-setup")
    before = _snapshot(
        store, (mesh.topology_artifact_key, mesh.quality_artifact_key)
    )
    assert before["rows"]["setup_revisions"][0]["schema_version"] == 1
    store.engine.dispose()
    with pytest.raises(RuntimeError, match="immutable setup or mesh revisions"):
        command.downgrade(config, TARGET)
    restarted = Persistence(create_sqlite_engine(database_url), BlobStore(blob_root))
    assert _snapshot(
        restarted, (mesh.topology_artifact_key, mesh.quality_artifact_key)
    ) == before
    restarted.engine.dispose()


def test_two_head_v3_setup_blocks_before_empty_mesh_schema_is_destroyed(tmp_path):
    database = tmp_path / "setup-only.sqlite3"
    database_url = f"sqlite:///{database}"
    config = alembic_config(database_url)
    _upgrade(config, "two_heads")
    store = Persistence(
        create_sqlite_engine(database_url), BlobStore(tmp_path / "setup-only-blobs")
    )
    _step_setup(store, "setup-only")
    before = _snapshot(store, None)
    assert before["rows"]["mesh_revisions"] == ()
    store.engine.dispose()
    with pytest.raises(RuntimeError, match="immutable setup or mesh revisions"):
        command.downgrade(config, TARGET)
    restarted = Persistence(
        create_sqlite_engine(database_url), BlobStore(tmp_path / "setup-only-blobs")
    )
    assert _snapshot(restarted, None) == before
    assert "mesh_revisions" in inspect(restarted.engine).get_table_names()
    restarted.engine.dispose()


@pytest.mark.parametrize("starting_state", ["merged", "two_heads"])
def test_empty_integrated_database_can_downgrade_safely(tmp_path, starting_state):
    database = tmp_path / f"empty-{starting_state}.sqlite3"
    database_url = f"sqlite:///{database}"
    config = alembic_config(database_url)
    _upgrade(config, starting_state)
    command.downgrade(config, TARGET)
    engine = create_sqlite_engine(database_url)
    assert _versions(engine) == (TARGET,)
    assert "mesh_revisions" not in inspect(engine).get_table_names()
    assert "geometry_identity_artifacts" in inspect(engine).get_table_names()
    engine.dispose()
