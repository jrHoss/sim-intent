"""Adopt SimulationIntent v3 stable CAD-region references.

Region data is owned by the immutable, versioned ``setup_revisions.intent_json``
aggregate. The authoritative 2 -> 3 migration therefore lives in
``ir.versioning`` and is applied by the strict loader. Rewriting immutable
historical revision bytes here would invalidate their recorded hashes and
idempotency fingerprints. This head revision deliberately performs no DDL.

Downgrade is permitted only when no v3 revision exists. A database containing
v3 durable targets must be restored from a pre-R4b.2 backup rather than stamped
as R4b.1, whose runtime cannot read those records.
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
    incompatible = connection.scalar(
        sa.text(
            "SELECT 1 FROM setup_revisions "
            "WHERE schema_version >= 3 LIMIT 1"
        )
    )
    if incompatible is not None:
        raise RuntimeError(
            "migration 0005 downgrade blocked: v3 setup revisions require "
            "backup/restore, not an R4b.1 schema stamp"
        )
