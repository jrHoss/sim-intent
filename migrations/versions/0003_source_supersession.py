"""Add authoritative source supersession and setup staleness."""

from alembic import op
import sqlalchemy as sa

revision = "0003_source_supersession"
down_revision = "0002_setup_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS model_versions_immutable")
    op.add_column("models", sa.Column("current_version_id", sa.String(36), nullable=True))
    op.add_column("model_versions", sa.Column("is_superseded", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("model_versions", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("superseded_by_version_id", sa.String(36), nullable=True))
    op.add_column("simulation_setups", sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("simulation_setups", sa.Column("stale_reason", sa.String(80), nullable=True))
    op.add_column("simulation_setups", sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("""
      UPDATE models SET current_version_id=(
        SELECT id FROM model_versions
        WHERE model_versions.model_id=models.id
        ORDER BY version DESC, created_at DESC, id DESC LIMIT 1
      )
    """)
    op.execute("""
      UPDATE model_versions SET is_superseded=1,
        superseded_at=(
          SELECT current.created_at FROM model_versions current
          WHERE current.id=(
            SELECT current_version_id FROM models
            WHERE models.id=model_versions.model_id
          )
        ),
        superseded_by_version_id=(
          SELECT current_version_id FROM models
          WHERE models.id=model_versions.model_id
        )
      WHERE id <> (SELECT current_version_id FROM models WHERE models.id=model_versions.model_id)
    """)
    op.execute("""
      UPDATE simulation_setups SET is_stale=1, stale_reason='source_replaced',
        stale_at=CURRENT_TIMESTAMP
      WHERE model_version_id <> (
        SELECT current_version_id FROM models WHERE models.id=simulation_setups.model_id
      )
    """)
    _create_triggers()


def _create_triggers() -> None:
    op.execute("""
    CREATE TRIGGER model_versions_immutable
    BEFORE UPDATE OF model_id, version, source_sha256, source_name, size_bytes,
      media_type, model_kind, blob_key, created_at ON model_versions
    BEGIN SELECT RAISE(ABORT, 'model_versions are immutable'); END
    """)
    op.execute("""
    CREATE TRIGGER models_current_version_insert_integrity
    BEFORE INSERT ON models
    WHEN NEW.current_version_id IS NOT NULL
    BEGIN SELECT RAISE(ABORT, 'invalid current model version'); END
    """)
    op.execute("""
    CREATE TRIGGER models_current_version_integrity
    BEFORE UPDATE OF current_version_id ON models
    WHEN (NEW.current_version_id IS NULL AND EXISTS (
      SELECT 1 FROM model_versions v WHERE v.model_id=NEW.id
    )) OR (NEW.current_version_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM model_versions v WHERE v.id=NEW.current_version_id
        AND v.model_id=NEW.id AND v.is_superseded=0
    ))
    BEGIN SELECT RAISE(ABORT, 'invalid current model version'); END
    """)
    op.execute("""
    CREATE TRIGGER model_versions_current_delete_integrity
    AFTER DELETE ON model_versions
    WHEN EXISTS (
      SELECT 1 FROM models m
      WHERE m.id=OLD.model_id AND m.current_version_id=OLD.id
    )
    BEGIN SELECT RAISE(ABORT, 'cannot delete current model version'); END
    """)
    op.execute("""
    CREATE TRIGGER model_versions_supersession_integrity
    BEFORE UPDATE OF is_superseded, superseded_at, superseded_by_version_id
    ON model_versions
    WHEN (NEW.is_superseded=0 AND (
      NEW.superseded_at IS NOT NULL OR NEW.superseded_by_version_id IS NOT NULL
      OR NEW.id <> (SELECT current_version_id FROM models WHERE id=NEW.model_id)
    )) OR (NEW.is_superseded=1 AND NEW.superseded_by_version_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM model_versions n
        WHERE n.id=NEW.superseded_by_version_id AND n.model_id=NEW.model_id
          AND n.version>NEW.version
      ))
    BEGIN SELECT RAISE(ABORT, 'invalid model version supersession'); END
    """)
    op.execute("""
    CREATE TRIGGER simulation_setups_current_source_insert
    BEFORE INSERT ON simulation_setups
    WHEN NEW.is_stale=0 AND NOT EXISTS (
      SELECT 1 FROM models m JOIN model_versions v ON v.id=NEW.model_version_id
      WHERE m.id=NEW.model_id AND m.current_version_id=v.id
        AND v.model_id=m.id AND v.is_superseded=0
    )
    BEGIN SELECT RAISE(ABORT, 'setup source is superseded'); END
    """)
    op.execute("""
    CREATE TRIGGER simulation_setups_stale_insert_integrity
    BEFORE INSERT ON simulation_setups
    WHEN (NEW.is_stale=0 AND (
      NEW.stale_reason IS NOT NULL OR NEW.stale_at IS NOT NULL
    )) OR (NEW.is_stale=1 AND (
      NEW.stale_reason IS NULL OR NEW.stale_at IS NULL
    ))
    BEGIN SELECT RAISE(ABORT, 'invalid setup staleness'); END
    """)
    op.execute("""
    CREATE TRIGGER simulation_setups_stale_integrity
    BEFORE UPDATE OF is_stale, stale_reason, stale_at ON simulation_setups
    WHEN (OLD.is_stale=1 AND (
      NEW.is_stale<>1 OR NEW.stale_reason IS NOT OLD.stale_reason
        OR NEW.stale_at IS NOT OLD.stale_at
    )) OR (OLD.is_stale=0 AND (
      (NEW.is_stale=0 AND (
        NEW.stale_reason IS NOT NULL OR NEW.stale_at IS NOT NULL
      )) OR (NEW.is_stale=1 AND (
        NEW.stale_reason IS NULL OR NEW.stale_at IS NULL
      ))
    ))
    BEGIN SELECT RAISE(ABORT, 'invalid setup staleness'); END
    """)


def downgrade() -> None:
    for trigger in (
        "simulation_setups_stale_integrity",
        "simulation_setups_stale_insert_integrity",
        "simulation_setups_current_source_insert",
        "model_versions_supersession_integrity",
        "model_versions_current_delete_integrity",
        "models_current_version_integrity",
        "models_current_version_insert_integrity",
        "model_versions_immutable",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_column("simulation_setups", "stale_at")
    op.drop_column("simulation_setups", "stale_reason")
    op.drop_column("simulation_setups", "is_stale")
    op.drop_column("model_versions", "superseded_by_version_id")
    op.drop_column("model_versions", "superseded_at")
    op.drop_column("model_versions", "is_superseded")
    op.drop_column("models", "current_version_id")
    op.execute("""
    CREATE TRIGGER model_versions_immutable
    BEFORE UPDATE ON model_versions
    BEGIN SELECT RAISE(ABORT, 'model_versions are immutable'); END
    """)
