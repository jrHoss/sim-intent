# Technical-preview architecture risk register

Likelihood and impact are qualitative Task 17 planning assessments. “Open”
means the risk requires later implementation evidence, not that its
architecture decision is unresolved.

| ID | Risk | Likelihood | Impact | Mitigation/decision | Accountable owner | Evidence/closure task | Status |
|---|---|---|---|---|---|---|---|
| R-SEC-01 | No named Security Review Owner is assigned | High | High | Assign a named person before Task 18 approval; do not represent security sign-off earlier | Release Owner Maein, subject to acceptance | Task 18 entry gate | Open entry gate |
| R-REV-01 | Task 17 receives no independent read-only review | Medium | High | Ahmed Yassin coordinates a separate reviewer before commit; stop uncommitted until findings are resolved | Technical Review Owner Ahmed Yassin | Task 17 pre-commit review | CLOSED — independent read-only review on 2026-07-24 returned APPROVE with no blocking, major, or minor findings; Task 17 is approved for commit |
| R-ROUTE-01 | V2 becomes `/` before it passes the final gate | Medium | High | Hard route matrix; Tasks 18–44 preserve legacy `/`; Task 45 alone may cut over | Release Owner | Tasks 24, 27, 44–45 | Open control |
| R-STATE-01 | Frontend, compatibility code, or a new service becomes a second setup truth | Medium | Critical | Backend aggregate sole writer; query cache is read-only; compatibility delegates to one owner | Technical Review Owner | Tasks 22, 26, 29, 39 | Open control |
| R-MIG-01 | Legacy/persistent dual writes diverge | Medium | High | Per-aggregate single-writer cutover; no volatile-session migration; compatibility reads only | Setup/persistence task owner | Tasks 21–23, 44 | Open control |
| R-DB-01 | SQLite concurrency or migration failure loses state | Medium | High | Explicit transactions, foreign keys, optimistic concurrency, integrity checks, pre-upgrade backup/restore | Persistence task owner | Tasks 21–23, 43–44 | Open control |
| R-ART-01 | Database metadata and local artifact bytes diverge | Medium | High | Atomic SHA-256 publication, verified reads, relative paths, cleanup of abandoned staging | Artifact task owner | Tasks 21, 34–39, 43 | Open control |
| R-GMSH-01 | Process-global Gmsh state corrupts concurrent work | High | High | Fresh subprocess per operation and one shared concurrency slot initially | Parser/mesh task owner | Tasks 20, 34 | Open control |
| R-UPLOAD-01 | Malicious or malformed uploads exhaust resources or expose paths | High | High | Streaming and parser limits, safe temp directories, no-network child, typed safe errors | Security Review Owner once assigned | Tasks 20, 43 | Open control |
| R-TOPO-01 | Topology or face ordering maps a condition to the wrong boundary | High | Critical | Fingerprints, MappingEvidence, residuals, stale invalidation, mandatory confirmation; block positional Abaqus mapping | Geometry/mapping task owner | Tasks 30–35, 42 | Open control |
| R-CAP-01 | Unsupported capability is approximated or warning-only | Medium | Critical | Central typed capability states; only `supported` may create artifact/job | Technical Review Owner | Tasks 19, 28–39, 42, 45 | Open control |
| R-SOLVER-01 | Solver child escapes limits, leaks processes, or corrupts job state | Medium | Critical | Durable JobService, immutable package, no-network process group, limits, cancellation/reconciliation | Job/security task owners | Tasks 38, 43 | Open control |
| R-VALID-01 | Solver completion is presented as engineering validity | Medium | Critical | Separate execution, numerical checks, and engineer approval in schema/UI | Result/release task owners | Tasks 37–40, 45 | Open control |
| R-LOCK-01 | Unpinned tools or system packages break reproducibility | High | High | `uv.lock`, `package-lock.json`, versioned Debian-stable OCI baseline, drift checks | Task 18 owner | Tasks 18, 44–45 | Open control |
| R-MODE-01 | REPLAY, fallback, or fixtures enter production or LIVE evidence | Medium | Critical | Startup-fixed mode and physical production exclusion; separate scoring | Release Owner | Tasks 18, 41, 45 | Open control |
| R-EGRESS-01 | Source model, prompt, artifact, or credential leaves the machine unexpectedly | Medium | High | Explicit provider enablement, local defaults, redacted diagnostics, no-network workers | Security Review Owner once assigned | Tasks 18, 38, 43–44 | Open control |
| R-EVAL-01 | Evaluation overfits fixtures or hides false-ready cases | Medium | High | Frozen independent corpus, hashes, holdouts, source scans, separate metrics | Evaluation task owner | Tasks 40–43, 45 | Open control |
| R-NUM-01 | Singular peak stress or mesh dependence creates false confidence | High | High | Predeclared refinement criteria, result semantics, warnings, no certification claim | Engineering/release reviewer | Tasks 37, 40, 45 | Open control |
| R-LICENSE-01 | Abaqus environment/license is absent or incompatible | High | Medium | Return `unavailable`; retain export-only claim; validation evidence names exact version | Abaqus validation owner | Task 42 | Open control |
| R-SCOPE-01 | Remote runners, HPC, classifiers, assemblies, or advanced physics enter active dependencies | Medium | High | Formal change-control and dependency scan; post-preview references are exclusions only | Release Owner | Tasks 17, 45 | Open control |

## Escalation rules

- A critical risk with failed mitigation evidence blocks the consuming task.
- A missing named owner blocks approval when the responsible entry gate is
  reached.
- Risk acceptance cannot silently change a capability from a blocking state to
  `supported`.
- Scope expansion follows the plan-change rule in
  [`TECHNICAL_PREVIEW_PLAN.md`](../../../TECHNICAL_PREVIEW_PLAN.md).
