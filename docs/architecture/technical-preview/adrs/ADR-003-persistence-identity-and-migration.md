# ADR-003: Persistence, identity, artifact storage, and migration

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Technical Review Owner:** Ahmed Yassin

## Context

The active release needs durable projects, immutable domain revisions,
content-addressed binary storage, explicit migrations, and restart-safe
transactions. Current V1 model IDs conflate filename/content and current setup
state is volatile.

## Options considered

1. SQLAlchemy 2 repositories, Alembic, SQLite foreign keys, UUIDv4 domain IDs,
   SHA-256 local artifact storage, and single-writer cutover.
2. Raw `sqlite3` plus a custom migration framework.
3. SQLModel or another combined validation/persistence model.
4. Content hashes as domain IDs.
5. Legacy/persistent dual writes during transition.

Option 1 provides explicit repository and transaction boundaries with mature
migration tooling. Option 2 minimizes dependencies but adds custom migration
and mapping code. Option 3 risks coupling API validation and persistence
schemas. Option 4 confuses deduplication with user/domain intent. Option 5
violates the single-authority invariant.

## Decision

- Use a SQLAlchemy 2 repository layer and explicit unit-of-work transactions.
- Use Alembic for ordered relational migrations.
- Enable and test SQLite foreign keys on every connection.
- Use opaque UUIDv4 IDs for Projects, Models, ModelVersions, Setups,
  Conversations, MeshRevisions, MappingEvidence, Artifacts, Jobs, Runs, and
  Results.
- Use immutable revision numbers plus canonical hashes for revision lineage.
- Use SHA-256 over exact bytes for blob/artifact identity and deduplication.
- Keep domain identity distinct from content hash and filename metadata.
- Store large bytes in an atomic local content-addressed artifact store;
  SQLite stores verified hashes, sizes, media types, lineage, and relative
  store keys.
- Cut over each aggregate to one writer. Do not migrate volatile legacy
  sessions and do not dual-write.
- Permit compatibility reads only when explicitly documented and incapable of
  mutating or approving V2 state.
- Use forward database migrations with a verified pre-upgrade backup. Rollback
  restores the previous application and matching database backup.

## Consequences

- Task 18 must lock SQLAlchemy and Alembic but does not install them in Task 17.
- The application needs explicit repository/domain mapping rather than using
  ORM objects as API contracts.
- Same source bytes may back multiple intentional ModelVersions while sharing
  one blob.
- Production rollback consumes storage for backups and requires writer
  quiescence.
- Volatile V1 sessions cannot be reopened as V2 projects; this is explicit,
  safer than inventing missing lineage.

## Downstream gate

This decision blocks Tasks 19 and 21–23 if transaction ownership, identity
separation, migration, backup, or single-writer rules are absent. It is also a
lineage prerequisite for Tasks 34–39 and 44.
