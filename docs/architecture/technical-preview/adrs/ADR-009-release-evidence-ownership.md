# ADR-009: Release evidence and review ownership

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Decision date:** 2026-07-24

## Context

Task evidence, security review, architecture review, and final release approval
must have distinct accountability. Task authors cannot silently self-approve,
and a missing security owner must be visible before implementation begins.

## Options considered

1. Role-based evidence preparation, independent read-only review, named release
   and technical owners, and a named-security-owner entry gate.
2. Task author prepares and self-approves all evidence.
3. One centralized reviewer owns every technical, security, and release
   decision.
4. Leave owner assignment implicit until release.

Option 1 separates preparation, independent verification, and gate
accountability. Options 2–4 create conflicts, bottlenecks, or unowned risk.

## Decision

- Release Owner: Maein, subject to his acceptance of this responsibility.
- Technical Review Owner: Ahmed Yassin.
- Independent technical evidence: a separate read-only reviewer reviews each
  task before commit.
- Security Review Owner: an accountable role must be assigned to a named person
  before Task 18 may be approved.
- The missing named security reviewer does not block Task 17 drafting or
  independent technical review. It is an explicit Task 18 entry gate.
- Implementing task owners prepare exact evidence in
  [`PROGRESS_TECHNICAL_PREVIEW.md`](../../../../PROGRESS_TECHNICAL_PREVIEW.md).
- Review findings are resolved or become named blockers; they are not omitted
  to preserve a completion claim.
- Task 45 verifies release evidence on the tagged candidate, not merely on a
  development worktree.

## Consequences

- Task 17 must stop before commit for separate independent review.
- Task 18 cannot be approved while the Security Review Owner is unnamed.
- Maein’s release ownership remains conditional until he accepts it.
- Ahmed Yassin coordinates technical review but does not satisfy the separate
  reviewer requirement by reviewing his own work.
- Security-sensitive tasks require the assigned security owner in addition to
  normal independent technical evidence.

## Downstream gate

The independent Task 17 review blocks the Task 17 commit. The missing named
Security Review Owner blocks Task 18 approval. Later tasks cannot start when
their dependency evidence is unapproved.
