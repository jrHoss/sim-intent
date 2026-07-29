"""Persist immutable geometry-identity artifacts owned by STEP model versions."""

from alembic import op
import sqlalchemy as sa

revision = "0004_geometry_identity_artifacts"
down_revision = "0003_source_supersession"
branch_labels = None
depends_on = None

TABLE_NAME = "geometry_identity_artifacts"
INDEX_NAME = "ix_geometry_identity_artifacts_model_id"
TRIGGER_NAME = "geometry_identity_artifacts_immutable"
TRIGGER_SQL = f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE UPDATE ON {TABLE_NAME}
BEGIN
    SELECT RAISE(ABORT, 'geometry identity artifacts are immutable');
END
"""


def _fail_mismatch(object_name: str) -> None:
    raise RuntimeError(
        f"migration 0004 found an incompatible pre-existing {object_name}"
    )


def _validate_table(connection) -> None:
    inspector = sa.inspect(connection)
    columns = inspector.get_columns(TABLE_NAME)
    expected = {
        "model_version_id": (sa.String, 36, False, 1, None),
        "model_id": (sa.String, 36, False, 0, None),
        "source_sha256": (sa.String, 64, False, 0, None),
        "artifact_version": (sa.Integer, None, False, 0, None),
        "hash_domain": (sa.String, 100, False, 0, None),
        "canonical_bytes": (sa.LargeBinary, None, False, 0, None),
        "integrity_sha256": (sa.String, 64, False, 0, None),
        "created_at": (sa.DateTime, None, False, 0, None),
    }
    if set(column["name"] for column in columns) != set(expected):
        _fail_mismatch("geometry identity artifact table")
    for column in columns:
        (
            expected_type,
            expected_length,
            nullable,
            primary_key,
            default,
        ) = expected[column["name"]]
        if (
            not isinstance(column["type"], expected_type)
            or getattr(column["type"], "length", None) != expected_length
            or column["nullable"] is not nullable
            or column["primary_key"] != primary_key
            or column.get("default") != default
        ):
            _fail_mismatch("geometry identity artifact table")
    primary_key = inspector.get_pk_constraint(TABLE_NAME)
    if primary_key.get("constrained_columns") != ["model_version_id"]:
        _fail_mismatch("geometry identity artifact table")
    foreign_keys = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
            str((item.get("options") or {}).get("ondelete", "")).upper() or None,
            str((item.get("options") or {}).get("onupdate", "")).upper() or None,
            (item.get("options") or {}).get("deferrable"),
            (item.get("options") or {}).get("initially"),
        )
        for item in inspector.get_foreign_keys(TABLE_NAME)
    }
    if foreign_keys != {
        (
            ("model_version_id",),
            "model_versions",
            ("id",),
            "CASCADE",
            None,
            None,
            None,
        ),
        (("model_id",), "models", ("id",), "CASCADE", None, None, None),
    }:
        _fail_mismatch("geometry identity artifact table")
    if inspector.get_check_constraints(TABLE_NAME):
        _fail_mismatch("geometry identity artifact table")


def _index_columns(connection, index_name: str) -> list[str]:
    quoted_name = connection.dialect.identifier_preparer.quote_identifier(
        index_name
    )
    rows = connection.exec_driver_sql(
        f"PRAGMA index_info({quoted_name})"
    ).mappings()
    return [row["name"] for row in rows]


def _validate_index_state(connection, *, expected_required: bool) -> bool:
    rows = list(
        connection.exec_driver_sql(
            f"PRAGMA index_list('{TABLE_NAME}')"
        ).mappings()
    )
    expected_seen = False
    primary_key_seen = False
    for row in rows:
        name = row["name"]
        columns = _index_columns(connection, name)
        if name == INDEX_NAME:
            if (
                expected_seen
                or row["unique"] not in (False, 0)
                or row["origin"] != "c"
                or row["partial"] not in (False, 0)
                or columns != ["model_id"]
            ):
                _fail_mismatch("geometry identity artifact index")
            expected_seen = True
        elif (
            row["origin"] == "pk"
            and row["unique"] in (True, 1)
            and row["partial"] in (False, 0)
            and columns == ["model_version_id"]
        ):
            if primary_key_seen:
                _fail_mismatch("geometry identity artifact index")
            primary_key_seen = True
        else:
            _fail_mismatch("geometry identity artifact index")
    if not primary_key_seen or expected_required and not expected_seen:
        _fail_mismatch("geometry identity artifact index")
    return expected_seen


def _validate_index(connection) -> None:
    _validate_index_state(connection, expected_required=True)


def _normalized_sql(value: str) -> str:
    return "".join(value.lower().replace(";", "").split())


def _validate_trigger_state(connection, *, expected_required: bool) -> bool:
    rows = connection.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name=:table_name"
        ),
        {"table_name": TABLE_NAME},
    ).all()
    if not rows:
        if expected_required:
            _fail_mismatch("geometry identity artifact trigger")
        return False
    if (
        len(rows) != 1
        or rows[0][0] != TRIGGER_NAME
        or _normalized_sql(rows[0][1]) != _normalized_sql(TRIGGER_SQL)
    ):
        _fail_mismatch("geometry identity artifact trigger")
    return True


def _validate_trigger(connection) -> None:
    _validate_trigger_state(connection, expected_required=True)


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if TABLE_NAME not in inspector.get_table_names():
        op.create_table(
            TABLE_NAME,
            sa.Column(
                "model_version_id",
                sa.String(36),
                sa.ForeignKey("model_versions.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "model_id",
                sa.String(36),
                sa.ForeignKey("models.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source_sha256", sa.String(64), nullable=False),
            sa.Column("artifact_version", sa.Integer(), nullable=False),
            sa.Column("hash_domain", sa.String(100), nullable=False),
            sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
            sa.Column("integrity_sha256", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _validate_table(connection)
    if not _validate_index_state(connection, expected_required=False):
        op.create_index(INDEX_NAME, TABLE_NAME, ["model_id"])
    _validate_index(connection)
    if not _validate_trigger_state(connection, expected_required=False):
        op.execute(TRIGGER_SQL)
    _validate_trigger(connection)


def downgrade() -> None:
    connection = op.get_bind()
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
    if TABLE_NAME in sa.inspect(connection).get_table_names():
        index_names = {
            item["name"]
            for item in sa.inspect(connection).get_indexes(TABLE_NAME)
        }
        if INDEX_NAME in index_names:
            op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
        op.drop_table(TABLE_NAME)
