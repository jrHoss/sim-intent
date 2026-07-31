# Current release slice

- **Slice:** R5.1 — Mesh domain and durable persistence
- **Starting merged main:** `4e0ae349d26429c32aa44262e61ad1606580f0f2`
- **Implementation branch:** `r5-1-mesh-domain-persistence`
- **Objective:** Store, reopen, validate, and audit immutable tetrahedral topology and quality artifacts bound to exact persisted source and setup revisions.
- **Dependencies available:** R1 durable projects/models/setups, R2 CAS and integrity, R4 stable geometry foundation, Alembic through `0004`.
- **In scope:** v1 topology and quality contracts; canonical JSON; MeshRevision ownership, CAS publication, exact reads, linear lineage, idempotency, migration `0005`, focused and regression evidence.
- **Excluded:** real meshing or Gmsh use, CAD-to-mesh mapping, API/OpenAPI/frontend work, workers/queues, solver/export/result work, and all R6 behavior.
- **Acceptance:** strict deterministic artifacts; fail-closed integrity and ownership; immutable persistence; replay-safe requests; one migration head with downgrade/re-upgrade evidence.
- **Verification:** focused, affected, migration, schema, full Python and applicable JavaScript checks; drift, integrity, dependency, secret, path, scope, and Git checks.
- **Status:** The fifth independent read-only verification completed with verdict `APPROVE` and no unresolved BLOCKER, HIGH, MEDIUM, or LOW findings. R5.1 is approved and ready for a local commit. No remote publication has occurred; any later merge or publication remains a separate explicit user decision.
