# ADR-001: Supported envelope, routes, and local deployment

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Decision owners:** Release Owner Maein, subject to acceptance; Technical
Review Owner Ahmed Yassin

## Context

The active plan requires an exact supported envelope, additive V2 routing, a
rollback surface, and a local/on-premises deployment without importing
post-preview infrastructure.

## Options considered

1. Adopt the active envelope unchanged, preserve legacy `/` during development,
   and permit V2 cutover only at Task 45.
2. Narrow the release envelope and revise active requirements.
3. Expand the envelope or depend on remote services.
4. Change `/` before the final release gate.

Option 1 preserves the approved plan and provides a rehearsable rollback.
Option 2 would require formal plan change-control and invalidate traceability.
Option 3 would add unapproved physics, tenancy, runner, or infrastructure
dependencies. Option 4 would contradict Tasks 24 and 27 and expose an
unapproved default product.

## Decision

- Adopt the supported envelope in
  [`release-goal.md`](../../../../release-goal.md) unchanged.
- Any expansion requires the formal plan-change rule in
  [`TECHNICAL_PREVIEW_PLAN.md`](../../../../TECHNICAL_PREVIEW_PLAN.md).
- During development and migration through Task 44:
  - `/` remains the legacy application;
  - `/legacy` is the explicit rollback route;
  - `/app-v2` is the new technical-preview application.
- At Task 45 only, after every gate and human approval:
  - `/` becomes the approved V2 application;
  - `/legacy` remains during the rollback window;
  - `/app-v2` remains as a compatibility route.
- Deploy a local modular monolith using SQLite and local content-addressed
  artifacts, with isolated parser and solver child processes.
- No active-release remote service, customer runner, connected Abaqus, HPC,
  multi-user, or SaaS dependency is permitted.

## Consequences

- The golden technical-preview workflow is reached at `/app-v2` until final
  release approval.
- Task 44 may package and rehearse route cutover but cannot activate it.
- A failed Task 45 leaves `/` on legacy without an intermediate state.
- The local deployment remains operationally simple, but vertical resource
  scaling and one-host storage limits must be documented.
- Future remote or multi-user architecture requires a new approved plan.

## Downstream gate

This decision blocks Tasks 18, 24, 27, 44, and 45 if contradicted. It resolves
the foundational route/deployment decision for Tasks 18–45.
