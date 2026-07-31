"""Add immutable exact-revision mesh artifact persistence."""

from alembic import op
import sqlalchemy as sa

revision = "0005_mesh_domain_persistence"
down_revision = "0004_geometry_identity_artifacts"
branch_labels = None
depends_on = None

TABLE = "mesh_revisions"
IMMUTABLE_TRIGGER = "mesh_revisions_immutable"
OWNERSHIP_TRIGGER = "mesh_revisions_ownership_currentness"
LINEAGE_TRIGGER = "mesh_revisions_exact_lineage"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(36),
            sa.ForeignKey("models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_version_id",
            sa.String(36),
            sa.ForeignKey("model_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "setup_id",
            sa.String(36),
            sa.ForeignKey("simulation_setups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "setup_revision_id",
            sa.String(36),
            sa.ForeignKey("setup_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "predecessor_mesh_revision_id",
            sa.String(36),
            sa.ForeignKey("mesh_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("topology_artifact_key", sa.String(160), nullable=False),
        sa.Column("topology_sha256", sa.String(64), nullable=False),
        sa.Column("topology_size_bytes", sa.Integer(), nullable=False),
        sa.Column("topology_media_type", sa.String(100), nullable=False),
        sa.Column("topology_schema_version", sa.Integer(), nullable=False),
        sa.Column("quality_artifact_key", sa.String(160), nullable=False),
        sa.Column("quality_sha256", sa.String(64), nullable=False),
        sa.Column("quality_size_bytes", sa.Integer(), nullable=False),
        sa.Column("quality_media_type", sa.String(100), nullable=False),
        sa.Column("quality_schema_version", sa.Integer(), nullable=False),
        sa.Column("source_model_sha256", sa.String(64), nullable=False),
        sa.Column("mesh_settings_hash", sa.String(64), nullable=False),
        sa.Column("mesher_profile_id", sa.String(120), nullable=False),
        sa.Column("mesher_profile_version", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(200), nullable=False),
        sa.Column("canonical_request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "topology_size_bytes >= 0 AND quality_size_bytes >= 0",
            name="ck_mesh_artifact_sizes",
        ),
        sa.CheckConstraint(
            "topology_schema_version = 1 AND quality_schema_version = 1",
            name="ck_mesh_schema_versions",
        ),
        sa.CheckConstraint(
            "predecessor_mesh_revision_id IS NULL "
            "OR predecessor_mesh_revision_id <> id",
            name="ck_mesh_not_self_predecessor",
        ),
        sa.UniqueConstraint(
            "project_id", "request_id", name="uq_project_mesh_request_id"
        ),
        sa.UniqueConstraint(
            "predecessor_mesh_revision_id",
            name="uq_mesh_revision_predecessor_successor",
        ),
    )
    for column in (
        "project_id",
        "model_id",
        "model_version_id",
        "setup_id",
        "setup_revision_id",
    ):
        op.create_index(f"ix_mesh_revisions_{column}", TABLE, [column])
    op.execute(
        f"""
        CREATE TRIGGER {IMMUTABLE_TRIGGER}
        BEFORE UPDATE ON {TABLE}
        BEGIN
          SELECT RAISE(ABORT, 'mesh revisions are immutable');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {OWNERSHIP_TRIGGER}
        BEFORE INSERT ON {TABLE}
        BEGIN
          SELECT CASE
            WHEN NOT EXISTS (
              SELECT 1
              FROM projects p
              JOIN models m ON m.project_id=p.id
              JOIN model_versions v ON v.model_id=m.id
              JOIN simulation_setups s
                ON s.project_id=p.id
                AND s.model_id=m.id
                AND s.model_version_id=v.id
              JOIN setup_revisions r ON r.setup_id=s.id
              WHERE p.id=NEW.project_id
                AND m.id=NEW.model_id
                AND v.id=NEW.model_version_id
                AND v.source_sha256=NEW.source_model_sha256
                AND s.id=NEW.setup_id
                AND r.id=NEW.setup_revision_id
            )
            THEN RAISE(ABORT, 'invalid mesh revision ownership')
            WHEN NOT EXISTS (
              SELECT 1
              FROM models m
              JOIN model_versions v
                ON v.id=NEW.model_version_id AND v.model_id=m.id
              JOIN simulation_setups s
                ON s.id=NEW.setup_id
                AND s.project_id=NEW.project_id
                AND s.model_id=m.id
                AND s.model_version_id=v.id
              WHERE m.id=NEW.model_id
                AND m.project_id=NEW.project_id
                AND m.current_version_id=v.id
                AND v.is_superseded=0
                AND s.is_stale=0
            )
            THEN RAISE(ABORT, 'mesh source is stale')
          END;
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {LINEAGE_TRIGGER}
        BEFORE INSERT ON {TABLE}
        WHEN NEW.predecessor_mesh_revision_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM mesh_revisions p
            WHERE p.id=NEW.predecessor_mesh_revision_id
              AND p.project_id=NEW.project_id
              AND p.model_id=NEW.model_id
              AND p.model_version_id=NEW.model_version_id
              AND p.source_model_sha256=NEW.source_model_sha256
          )
        BEGIN
          SELECT RAISE(ABORT, 'invalid mesh revision lineage');
        END
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {LINEAGE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {OWNERSHIP_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {IMMUTABLE_TRIGGER}")
    for column in reversed(
        (
            "project_id",
            "model_id",
            "model_version_id",
            "setup_id",
            "setup_revision_id",
        )
    ):
        op.drop_index(f"ix_mesh_revisions_{column}", table_name=TABLE)
    op.drop_table(TABLE)
