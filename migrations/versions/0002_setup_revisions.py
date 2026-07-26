"""Add durable immutable simulation setup revisions."""

from alembic import op
import sqlalchemy as sa

revision = "0002_setup_revisions"
down_revision = "0001_projects_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_setups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=True),
        sa.Column("create_request_id", sa.String(200), nullable=False),
        sa.Column("create_request_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_revision IS NULL OR current_revision >= 1", name="ck_setup_current_revision"),
        sa.UniqueConstraint("project_id", "create_request_id", name="uq_project_setup_request_id"),
    )
    op.create_index("ix_simulation_setups_project_id", "simulation_setups", ["project_id"])
    op.create_table(
        "setup_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("setup_id", sa.String(36), sa.ForeignKey("simulation_setups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36), sa.ForeignKey("setup_revisions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("intent_json", sa.Text(), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("mutation_type", sa.String(40), nullable=False),
        sa.Column("request_id", sa.String(200), nullable=False),
        sa.Column("mutation_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_setup_revision_positive"),
        sa.UniqueConstraint("setup_id", "revision", name="uq_setup_revision_number"),
        sa.UniqueConstraint("setup_id", "request_id", name="uq_setup_request_id"),
    )
    op.create_index("ix_setup_revisions_setup_id", "setup_revisions", ["setup_id"])
    op.execute("""
    CREATE TRIGGER simulation_setups_lineage_insert
    BEFORE INSERT ON simulation_setups
    WHEN NOT EXISTS (
      SELECT 1 FROM models m JOIN model_versions v ON v.model_id=m.id
      WHERE m.id=NEW.model_id AND m.project_id=NEW.project_id
        AND v.id=NEW.model_version_id
    )
    BEGIN SELECT RAISE(ABORT, 'invalid setup lineage'); END
    """)
    op.execute("""
    CREATE TRIGGER simulation_setups_lineage_update
    BEFORE UPDATE OF project_id, model_id, model_version_id ON simulation_setups
    BEGIN SELECT RAISE(ABORT, 'setup lineage is immutable'); END
    """)
    op.execute("""
    CREATE TRIGGER simulation_setups_current_revision_insert
    BEFORE INSERT ON simulation_setups
    WHEN NEW.current_revision IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM setup_revisions r
      WHERE r.setup_id=NEW.id AND r.revision=NEW.current_revision
    )
    BEGIN SELECT RAISE(ABORT, 'invalid current setup revision'); END
    """)
    op.execute("""
    CREATE TRIGGER simulation_setups_current_revision
    BEFORE UPDATE OF current_revision ON simulation_setups
    WHEN NEW.current_revision IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM setup_revisions r
      WHERE r.setup_id=NEW.id AND r.revision=NEW.current_revision
    )
    BEGIN SELECT RAISE(ABORT, 'invalid current setup revision'); END
    """)
    op.execute("""
    CREATE TRIGGER setup_revisions_immutable
    BEFORE UPDATE ON setup_revisions
    BEGIN SELECT RAISE(ABORT, 'setup_revisions are immutable'); END
    """)
    op.execute("""
    CREATE TRIGGER setup_revisions_sequential
    BEFORE INSERT ON setup_revisions
    WHEN (NEW.revision=1 AND NEW.parent_revision_id IS NOT NULL)
      OR (NEW.revision>1 AND NOT EXISTS (
        SELECT 1 FROM setup_revisions p
        WHERE p.id=NEW.parent_revision_id AND p.setup_id=NEW.setup_id
          AND p.revision=NEW.revision-1
      ))
    BEGIN SELECT RAISE(ABORT, 'invalid setup revision parent'); END
    """)


def downgrade() -> None:
    for trigger in (
        "setup_revisions_sequential", "setup_revisions_immutable",
        "simulation_setups_current_revision", "simulation_setups_current_revision_insert",
        "simulation_setups_lineage_update",
        "simulation_setups_lineage_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_index("ix_setup_revisions_setup_id", table_name="setup_revisions")
    op.drop_table("setup_revisions")
    op.drop_index("ix_simulation_setups_project_id", table_name="simulation_setups")
    op.drop_table("simulation_setups")
