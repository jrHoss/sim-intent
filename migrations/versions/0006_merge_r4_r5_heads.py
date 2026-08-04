"""Merge R4 stable CAD-region references and R5 mesh persistence heads.

This integration revision reconciles the two independently completed migration
branches. Both predecessor migrations retain full ownership of their DDL,
validation, trigger, and downgrade behavior; this revision performs no DDL.

Downgrade is refused before the merge revision is removed when either branch
owns immutable data. This migration graph cannot rely on rollback of the
relevant SQLite DDL sequence, so predecessor-local guards repeat this preflight
for databases stamped with both independent heads.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_merge_r4_r5_heads"
down_revision = (
    "0005_stable_cad_region_references",
    "0005_mesh_domain_persistence",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    connection = op.get_bind()
    incompatible_setup = connection.scalar(
        sa.text(
            "SELECT 1 FROM setup_revisions "
            "WHERE schema_version >= 3 LIMIT 1"
        )
    )
    incompatible_mesh = connection.scalar(
        sa.text("SELECT 1 FROM mesh_revisions LIMIT 1")
    )
    if incompatible_setup is not None or incompatible_mesh is not None:
        raise RuntimeError(
            "integrated R4/R5 downgrade blocked: immutable setup or mesh "
            "revisions require backup/restore"
        )
