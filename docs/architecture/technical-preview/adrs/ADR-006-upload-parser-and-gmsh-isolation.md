# ADR-006: Upload, parser, and Gmsh isolation

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Security Review:** Named owner required before Task 18 approval

## Context

V1 reads complete uploads and performs parser/Gmsh work in the web process.
Gmsh exposes process-global lifecycle state, and malformed files must not crash
or poison request handling.

## Options considered

1. Fresh bounded no-network subprocess per parse/mesh operation with one shared
   Gmsh concurrency slot initially.
2. Long-lived geometry worker with multiple concurrent Gmsh sessions.
3. Parse synchronously in the web process under a thread lock.
4. Unbounded subprocesses per request.

Option 1 provides process cleanup, failure isolation, and deterministic
serialization. Option 2 retains poisoned-process risk and unsafe global-state
concurrency. Option 3 allows parser crashes/resource exhaustion to affect the
web process. Option 4 permits host exhaustion.

## Decision

- Stream and enforce upload size before complete body buffering.
- Sanitize names and validate permitted media/suffix/content relationships.
- Create a safe operation-specific temporary directory.
- Run every STEP/INP parse and Gmsh mesh/tessellation operation in a fresh
  local subprocess with no network.
- Apply bounded CPU, memory, elapsed-time, disk, output, and input limits.
- Invoke subprocesses by argument vector; never interpolate a shell command.
- Use one shared application-level Gmsh concurrency slot initially across
  parse, tessellation, and mesh operations.
- Concurrent operations queue within a bounded limit or fail deterministically
  with a typed response.
- Publish results only after validation; always perform deterministic cleanup
  after success, failure, timeout, or cancellation.
- Return RFC 9457 problem details without local paths.

Task 20 owns concrete resource values and implementation evidence. Task 34
reuses this boundary for production meshing rather than creating a second Gmsh
owner.

## Consequences

- Fresh process startup adds latency but avoids contaminated global state.
- Initial Gmsh throughput is one operation at a time per application
  deployment.
- Resource-limit portability is defined for the supported Linux OCI target;
  other hosts are not release evidence.
- Large uploads and queue saturation fail explicitly rather than degrading the
  web process.

## Downstream gate

This decision blocks Tasks 20–21, 30, and 34 if parsing/Gmsh can occur in the
web process, bypass the shared slot, or run without limits and cleanup.
