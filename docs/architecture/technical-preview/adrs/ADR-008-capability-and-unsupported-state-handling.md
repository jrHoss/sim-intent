# ADR-008: Capability and unsupported-state handling

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Decision owners:** Release Owner Maein, subject to acceptance; Technical
Review Owner Ahmed Yassin

## Context

A boolean “supported” flag cannot distinguish excluded physics, missing local
dependencies, missing approvals, unproven mapping, or stale lineage. These
conditions require different recovery actions but must all fail closed.

## Options considered

1. Central typed states: `supported`, `unsupported`, `unavailable`, `blocked`,
   `insufficient_evidence`, and `stale`.
2. Boolean capability plus free-form messages.
3. Endpoint-specific error categories.
4. Continue with warnings when prerequisites are absent.

Option 1 provides deterministic machine handling and honest recovery.
Options 2–4 conflate product scope with environment/setup failures and permit
warning-only false readiness.

## Decision

- `unsupported`: outside the approved release envelope.
- `unavailable`: supported, but an environment or licensed dependency is
  absent.
- `blocked`: supported, but a required approval, input, or validation is
  missing or failed.
- `insufficient_evidence`: deterministic grounding or mapping cannot be
  proven.
- `stale`: an identity or revision no longer matches.
- `supported`: all capability prerequisites are satisfied.

Only `supported` may proceed. Every other state fails closed and emits no
solver artifact or job. The backend capability registry and deterministic
validators own these states; the frontend renders but never upgrades them.

The precedence and detailed matrix are defined in
[`capability-matrix.md`](../capability-matrix.md). Positional STEP-to-Abaqus
mapping remains `insufficient_evidence`; product-submitted Abaqus execution,
remote runners, HPC, excluded elements/physics, and SaaS remain `unsupported`.
A missing licensed Abaqus validation environment is `unavailable`.

## Consequences

- API errors and read models carry a stable capability state and actionable
  typed details.
- The same request can move from `blocked`, `stale`,
  `insufficient_evidence`, or `unavailable` to `supported` only after a new
  validated command/environment check.
- `unsupported` requires scope change-control, not installation or user
  approval.
- Capability evaluation must be complete before artifact bytes or a Job record
  intended for execution are created.

## Downstream gate

This decision blocks Tasks 19, 20, 28–40, 42, and 45 if a non-`supported`
state can be warning-only, frontend-overridden, or produce an artifact/job.
