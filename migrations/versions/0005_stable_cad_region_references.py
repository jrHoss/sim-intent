"""Adopt SimulationIntent v3 stable CAD-region references.

Region data is owned by the immutable, versioned ``setup_revisions.intent_json``
aggregate. The authoritative 2 -> 3 migration therefore lives in
``ir.versioning`` and is applied by the strict loader. Rewriting immutable
historical revision bytes here would invalidate their recorded hashes and
idempotency fingerprints. This head revision deliberately performs no DDL.

Downgrade is permitted only when no v3 revision exists. In an integrated
two-head database it is also refused while immutable mesh revisions exist, so
safety does not depend on Alembic's predecessor traversal order. Such data must
be restored from a compatible backup rather than stamped as an older runtime.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_stable_cad_region_references"
down_revision = "0004_geometry_identity_artifacts"
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
    inspector = sa.inspect(connection)
    incompatible_mesh = None
    if inspector.has_table("mesh_revisions"):
        incompatible_mesh = connection.scalar(
            sa.text("SELECT 1 FROM mesh_revisions LIMIT 1")
        )
    if incompatible_setup is not None or incompatible_mesh is not None:
        raise RuntimeError(
            "integrated R4/R5 downgrade blocked: immutable setup or mesh "
            "revisions require backup/restore"
        )
