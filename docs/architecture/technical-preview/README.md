# Technical-preview release architecture

**Task:** 17 — Approve release architecture and decision-complete ADRs
**Decision date:** 2026-07-24
**Decision authority:** Human-approved Task 17 decisions recorded in the task
handoff
**Review state:** Ready for independent read-only review; no Task 17 commit may
be created until that review is complete

This directory is the decision-complete architecture baseline for active
technical-preview Tasks 18–45. It interprets, but does not expand,
[`release-goal.md`](../../../release-goal.md) or
[`TECHNICAL_PREVIEW_PLAN.md`](../../../TECHNICAL_PREVIEW_PLAN.md). The accepted
repository facts are in the
[`product-v2 repository audit`](../../audits/product-v2-repository-audit-2026-07-23.md).
The preserved
[`Product V2 roadmap`](../../roadmap/PRODUCT_V2_ROADMAP.md) remains
non-blocking future direction.

## Decision set

| Decision | Approved outcome | ADR |
|---|---|---|
| Supported envelope and route cutover | The active envelope is unchanged. `/` remains legacy until the final Task 45 gate approves V2 cutover. | [ADR-001](adrs/ADR-001-supported-envelope-routes-and-deployment.md) |
| State authority | Backend versioned aggregates own engineering truth; React owns transient interface state only. | [ADR-002](adrs/ADR-002-state-authority-and-ownership.md) |
| Persistence, identity, and migration | SQLAlchemy 2, Alembic, SQLite foreign keys, UUIDv4 domain IDs, SHA-256 content storage, and single-writer cutover. | [ADR-003](adrs/ADR-003-persistence-identity-and-migration.md) |
| API, schema, client, and errors | `/api/v1`, integer schema versions, backend OpenAPI authority, generated TypeScript client, and RFC 9457 errors. | [ADR-004](adrs/ADR-004-api-schema-client-and-errors.md) |
| Tooling and runtime modes | `uv`/`uv.lock`, npm/`package-lock.json`, Debian-stable OCI baseline, and one startup-fixed runtime mode. | [ADR-005](adrs/ADR-005-tooling-locks-and-runtime-modes.md) |
| Upload, parser, and Gmsh containment | Fresh bounded no-network subprocesses and one shared Gmsh concurrency slot initially. | [ADR-006](adrs/ADR-006-upload-parser-and-gmsh-isolation.md) |
| CalculiX execution | One durable application-owned JobService and isolated local subprocess execution. | [ADR-007](adrs/ADR-007-calculix-job-and-worker-isolation.md) |
| Capability states | `supported`, `unsupported`, `unavailable`, `blocked`, `insufficient_evidence`, and `stale`, all fail-closed except `supported`. | [ADR-008](adrs/ADR-008-capability-and-unsupported-state-handling.md) |
| Release evidence | Named release and technical owners, independent read-only task review, and a Task 18 security-owner entry gate. | [ADR-009](adrs/ADR-009-release-evidence-ownership.md) |

Task 18 owns actual dependency installation, version selection, lockfile
generation, OCI implementation, and CI implementation. These documents do not
perform or authorize that work.

## Required Task 17 evidence

- [Release architecture](release-architecture.md)
- [State-writer and ownership matrix](state-ownership-matrix.md)
- [Route matrix and deployment decision](route-and-deployment.md)
- [Capability boundaries](capability-matrix.md)
- [Migration and rollback rules](migration-rules.md)
- [Task dependency validation](dependency-traceability.md)
- [Threat model](threat-model.md)
- [Risk register](risk-register.md)
- [Release requirement and evidence ownership](release-evidence-ownership.md)

## Approval and review boundary

The architecture choices above were approved by the user on 2026-07-24.
Release Owner Maein remains subject to his acceptance of that responsibility.
Ahmed Yassin is the Technical Review Owner. A separate read-only reviewer must
review Task 17 before commit. A named Security Review Owner must be assigned
before Task 18 may be approved. That assignment is a visible Task 18 entry
gate; it is not an unresolved architecture choice and does not authorize Task
18 to begin.
