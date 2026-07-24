# Persistence, payload, route, and rollback migration rules

## Migration owners

| Migration kind | Sole owner | Rule |
|---|---|---|
| Relational schema | Alembic | Ordered forward revisions; SQLite foreign keys enabled and checked |
| Versioned API/persisted payload | Backend migration registry | Explicit integer versions and sequential pure transforms |
| Frontend generated contract | Backend OpenAPI plus generation command | Checked-in output must match the authoritative schema |
| Domain state cutover | Owning aggregate/service | One record is written by either legacy or persistent owner, never both |
| Artifact format | Artifact-specific backend registry | New immutable artifact/version; never in-place reinterpretation |
| Default route | Task 45 release configuration | No route cutover before the final approved release gate |

## Single-writer cutover

For each aggregate introduced by Tasks 21–23:

1. Introduce the persistent schema and repository behind the approved backend
   interface.
2. Prove migrations, foreign keys, transactions, concurrency, and restart
   behavior before routing new V2 commands to it.
3. Keep the legacy compatibility route delegated to its existing volatile
   owner where required.
4. Select the owner once at the request/record boundary. Never write the same
   project, setup, conversation, decision, or audit event to both stores.
5. Switch V2 traffic to the persistent owner only after the task’s cutover
   tests pass.
6. Remove any temporary compatibility adapter at the removal point named by
   its creating task.

Volatile legacy sessions are not migrated. They have no durable revision or
transaction boundary that can be converted safely. A user starts a new
persistent V2 project; the legacy application remains available as a separate
compatibility surface during migration.

Compatibility reads are allowed only when a task documents:

- the exact legacy source and target projection;
- that the read cannot mutate or approve V2 state;
- how absence, ambiguity, and stale content are reported;
- the removal task or retained compatibility reason.

## Transaction and artifact publication

- One application command owns one SQLAlchemy unit of work and database
  transaction.
- Repositories do not commit independently inside a domain command.
- SQLite foreign-key enforcement is enabled on every connection and verified
  at startup/tests.
- Content-addressed writes use safe staging, size/hash verification, atomic
  publication, and deterministic cleanup.
- Domain metadata references immutable hashes and relative store keys, never
  absolute host paths.
- A failed database transaction leaves no authoritative metadata. A staged but
  unreferenced blob is cleanup-eligible and cannot be treated as a domain
  record.
- Idempotency keys return the original accepted result for the same command
  identity and reject conflicting reuse.

## Payload migration

- Every versioned persisted JSON and product API payload declares a positive
  integer `schema_version`.
- Reads validate the declared version before migration.
- Migrations are explicit `n → n+1` backend transforms with golden evidence.
- Writes emit only the current schema version.
- Historical immutable source artifacts are never rewritten merely to update a
  schema.
- Unsafe missing information migrates to an explicit unapproved or blocked
  state; it is never defaulted to approved.
- Unsupported future versions return a typed error without partial parsing.
- Migration is idempotent at the current version.
- The frontend does not migrate engineering payloads.

## Database upgrade and rollback

Before an automatic production database migration:

1. stop or quiesce writers;
2. verify the current schema version and database integrity;
3. create a versioned pre-upgrade backup in the local data volume;
4. record application version, migration range, backup hash, and artifact-store
   compatibility;
5. apply Alembic forward migrations in one controlled startup step;
6. verify foreign keys, schema version, and startup readiness before accepting
   commands.

Rollback restores both the previous application and its matching pre-upgrade
database backup. Production rollback does not rely on down migrations and does
not ask older code to interpret a newer schema. Immutable artifacts remain
content-addressed; the backup manifest identifies which artifact versions are
reachable by the restored database.

## Route cutover and rollback

- Tasks 18–44 leave `/` on the legacy application.
- Task 45 may switch `/` to V2 only after all release gates and human approval.
- `/legacy` remains available throughout the documented rollback window.
- `/app-v2` remains a V2 compatibility route during that window.
- A Task 45 rollback restores the previous route configuration, previous
  application package, and compatible database backup as one rehearsed
  operation.

## Prohibited migration patterns

- Dual writes for comparison or “safety.”
- Frontend-owned engineering migrations.
- Database rows whose authoritative bytes exist only at an unverified path.
- Approval copied to migrated content whose semantic hash changed.
- In-place mutation of a run, mesh, artifact, result, or immutable revision.
- Silent fallback to V1 process-memory state after a persistent write fails.
- Route cutover used as a data-migration mechanism.
