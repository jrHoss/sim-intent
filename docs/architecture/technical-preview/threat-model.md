# Technical-preview architecture threat model

## Scope

This Task 17 threat model covers foundational trust boundaries and required
controls. It does not claim that controls are implemented. Implementation and
negative-test evidence belong to Tasks 18, 20, 21–23, 34–38, and 43–44.

## Assets

- Source STEP and INP model bytes.
- Project, setup, confirmation, and audit state.
- Mesh, mapping, solver deck, job, log, and result artifacts.
- Provider credentials and prompt/response data.
- Local host resources and solver executable.
- Release fixtures, evaluation evidence, and result integrity.

## Trust boundaries

```text
Untrusted browser/upload
        │
        ▼
HTTP/API validation boundary
        │
        ├──► authoritative application/domain services
        │       ├──► SQLite transaction boundary
        │       └──► local artifact-store boundary
        │
        ├──► untrusted parser/Gmsh subprocess boundary
        │
        └──► immutable job-package verification boundary
                 └──► CalculiX subprocess boundary

Optional configured model provider is a separate explicit egress boundary.
```

## Threat and control matrix

| ID | Threat | Required architecture control | Failure state | Implementation/evidence owner |
|---|---|---|---|---|
| TM-01 | Oversized or malformed upload exhausts web memory | Streaming limit before buffering; bounded body, filename/media checks | `blocked` | Tasks 20, 43 |
| TM-02 | Parser crash or malicious CAD corrupts web process | Fresh no-network subprocess, safe temp directory, CPU/memory/time/disk limits | `blocked` | Task 20 |
| TM-03 | Concurrent Gmsh use corrupts global state | One shared Gmsh concurrency slot initially; deterministic queue/failure | `blocked` | Tasks 20, 34 |
| TM-04 | Path traversal, unsafe filename, or host-path disclosure | Server-generated names, relative store keys, normalized safe paths, safe errors | `blocked` | Tasks 20–21, 43 |
| TM-05 | Artifact/database dual truth | One transactional metadata owner, SHA-256 verification, atomic publication | `blocked` | Tasks 21–22, 34–39 |
| TM-06 | Stale tab overwrites accepted setup | Expected revision/ETag, idempotency, immutable successor revisions | `stale` | Tasks 22, 29 |
| TM-07 | Chat, replay, or frontend state becomes engineering truth | Backend aggregate/validator authority; physical production mode exclusion | `blocked` | Tasks 18, 22–29 |
| TM-08 | Model invents or selects entity IDs | Typed requirements and server-issued handles; deterministic/viewer resolution | `insufficient_evidence` | Tasks 31–33 |
| TM-09 | Wrong CAD face maps to solver entity | Explicit MappingEvidence, residuals, non-empty mapping, no positional Abaqus mapping | `insufficient_evidence` | Tasks 35–36, 42 |
| TM-10 | Unapproved/stale region reaches artifact or solve | Exact revision approvals and fail-closed preflight | `blocked`/`stale` | Tasks 22, 29, 35–38 |
| TM-11 | Shell injection or arbitrary executable invocation | Argument-vector process creation; fixed executable/profile; no shell interpolation | `blocked` | Tasks 36, 38, 43 |
| TM-12 | Solver consumes mutable or swapped inputs | Immutable job package with exact hashes and verified working directory | `stale`/`blocked` | Task 38 |
| TM-13 | Solver escapes or exhausts host | No network; separate process group; time/memory/disk/output limits; cleanup | `blocked` | Tasks 38, 43 |
| TM-14 | Cancellation/timeout leaves child process | Process-group termination and restart reconciliation | `blocked` | Tasks 38, 43 |
| TM-15 | Replay/fallback represented as LIVE | Startup-fixed modes, production physical exclusion, labeled evidence | `unsupported` for production route | Tasks 18, 41, 45 |
| TM-16 | Credential, CAD, prompt, or artifact leaves host unexpectedly | Default-local data paths; explicit provider enablement; redacted diagnostics | `blocked` | Tasks 18, 43–44 |
| TM-17 | Error response leaks secrets or local paths | RFC 9457 safe typed details and correlation ID; server-side full log | `blocked` | Tasks 19–20, 43 |
| TM-18 | Database upgrade causes irreversible loss | Quiesce, integrity check, hashed pre-upgrade backup, restore rollback | `blocked` | Tasks 21–23, 44 |
| TM-19 | Missing dependency is treated as supported execution | Capability registry returns `unavailable`; no artifact/job side effect | `unavailable` | Tasks 18, 36–38, 42 |
| TM-20 | Post-preview service becomes an active dependency | Local deployment boundary and dependency-matrix review | `unsupported` | Tasks 17, 45 |

## Security review gate

The accountable Security Review Owner role must be assigned to a named person
before Task 18 may be approved. Until assignment:

- Task 17 architecture drafting and independent technical review may complete;
- Task 18 must not receive approval;
- no document may represent the security review as signed off.

The role assignment gate is tracked as risk `R-SEC-01` in the
[`risk register`](risk-register.md).
