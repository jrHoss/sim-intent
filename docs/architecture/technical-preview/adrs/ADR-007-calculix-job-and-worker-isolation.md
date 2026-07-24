# ADR-007: Durable local CalculiX job and worker isolation

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Security Review:** Named owner required before Task 18 approval

## Context

The active release must execute CalculiX locally without remote runners,
volatile-only job state, shell interpolation, mutable inputs, leaked child
processes, or solver completion being treated as engineering validity.

## Options considered

1. One durable application-owned JobService plus an isolated local
   no-network subprocess with default concurrency one.
2. Execute `ccx` directly in the request/web process.
3. Add a remote/customer runner or general distributed queue.
4. Create separate job-state stores for worker and UI.

Option 1 satisfies the local release and restart-recovery requirements.
Option 2 cannot enforce safe lifecycle or durable status. Option 3 is
post-preview scope. Option 4 creates conflicting job truth.

## Decision

- One durable application-owned JobService is the only job transition writer.
- Job commands and events persist in SQLite and reconcile interrupted states
  after restart.
- Every execution consumes one immutable package containing exact source,
  setup, mesh, mapping, artifact, adapter, and solver identities/hashes.
- The worker uses a fresh isolated working directory and invokes a fixed
  supported `ccx` executable by argument vector.
- The subprocess has no network and runs in its own process group.
- Enforce time, memory, disk, and output limits.
- Default solver concurrency is one; any later increase requires bounded
  evidence and must not change state ownership.
- Support cancellation, timeout, process-tree cleanup, captured stdout/stderr,
  and restart reconciliation.
- Native outputs publish through the artifact owner; normalized results publish
  through the ResultBundle owner.
- Execution state, numerical-check state, and engineer approval remain
  separate.
- Remote runners, connected Abaqus, and HPC are not supported.

## Consequences

- Task 38 must implement durable job records before product execution.
- A missing supported `ccx` installation returns `unavailable`.
- A failed capability, approval, mapping, or package preflight creates no
  executable job.
- Concurrency one limits throughput but bounds local resource competition and
  simplifies release evidence.
- Process/resource controls target the supported Debian-stable environment.

## Downstream gate

This decision blocks Tasks 38–40 and 43–45 if solver execution bypasses
JobService, mutates package inputs, uses shell interpolation, or lacks bounded
cleanup/reconciliation.
