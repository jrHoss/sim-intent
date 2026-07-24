# ADR-002: Backend authority and single state writers

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Technical Review Owner:** Ahmed Yassin

## Context

The V1 application combines process-memory sessions, browser globals, and
partial projections. Durable product work requires one writer for every state
type and a clear boundary between engineering truth and interface state.

## Options considered

1. Backend versioned aggregates own engineering state; React owns transient
   presentation state and a read-only query cache.
2. React owns a canonical setup and synchronizes it to the backend.
3. Legacy and persistent stores dual-write during migration.
4. Separate services reproduce validation, semantics, or export logic.

Option 1 makes concurrency and audit behavior deterministic. Options 2–4
create second sources of truth, conflicting migrations, or duplicated
engineering rules.

## Decision

- The backend versioned setup aggregate owns regions, materials, loads,
  constraints, coordinates, assumptions, decisions, approvals, and revision
  history.
- Deterministic backend validators own validity, readiness, and capability
  evaluation.
- React owns drafts, layout, viewer lifecycle, focus state, and a read-only
  server-response cache only.
- ViewerController owns camera, renderers, scene objects, controls, listeners,
  GPU resources, and transient visual reconciliation.
- Conversation text is append-only evidence, not setup truth.
- Browser click evidence becomes engineering evidence only after a
  revision-bound backend command validates it.
- Each persistent state type has the sole writer in the
  [`state-ownership matrix`](../state-ownership-matrix.md).
- Existing `ir`, `ground`, `geom`, `app/orchestration.py`, and `export` owners
  are extended or wrapped; they are not duplicated in React or a parallel
  service.

## Consequences

- Frontend updates use narrow commands, expected revisions, and idempotency
  keys, then invalidate/refetch.
- Events may signal invalidation or progress but cannot reconstruct truth.
- Optimistic UI may render a pending command state but cannot render
  engineering approval or job success before acknowledgement.
- Compatibility adapters must delegate to one owner.
- Some UI interactions require an additional round trip, accepted in exchange
  for coherent revision and audit semantics.

## Downstream gate

This decision blocks Tasks 19, 22–29, 33, and 38–39 if a second writer or
frontend-owned engineering model is introduced.
