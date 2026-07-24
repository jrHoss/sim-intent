# Active-task dependency and foundational-decision review

This matrix walks active Tasks 18–45 in execution order. Every dependency is an
earlier active task or approved pre-existing evidence. No active task consumes
a post-preview capability.

| Task | Declared direct dependencies | Foundational decisions consumed | Creates for later tasks | Post-preview dependency |
|---|---|---|---|---|
| 18 | 16–17 | Tooling/locks, Debian OCI baseline, runtime modes, route/deployment boundary | Reproducible environments and bounded CI | None |
| 19 | 17–18 | `/api/v1`, integer schema versions, OpenAPI/client generation, RFC 9457, migration ownership | Versioned API/IR/migration contracts | None |
| 20 | 17–19 | Upload limits boundary, fresh subprocess, shared Gmsh slot, error states | Safe parser execution contract | None |
| 21 | 17–20 | SQLAlchemy/Alembic/SQLite, UUIDv4 identities, SHA-256 artifact store, transactions | Project/Model/ModelVersion and source lineage | None |
| 22 | 19, 21 | Backend aggregate authority, immutable revisions, single-writer cutover | Durable setup/decision/audit state | None |
| 23 | 19, 21–22 | Conversation/setup separation, consume-on-success, backend authority | Durable conversation/clarification | None |
| 24 | 17–19 | Route policy, npm lock, generated client, errors, frontend authority limits | Additive React foundation at `/app-v2` | None |
| 25 | 24 plus accepted frontend audit | ViewerController-only GPU ownership, legacy concept porting | Lifecycle-safe declarative viewer | None |
| 26 | 22–25 | SetupView/backend authority, read-only cache, scoped invalidation | Chat-first shell/read projection | None |
| 27 | 20–26 | Development route matrix and persistence boundaries | Milestone 1 evidence; no solver claim | None |
| 28 | 19, 22, 26 | Explicit units/materials/coordinates/physics and capability states | Trusted engineering setup semantics | None |
| 29 | 22, 26, 28 | Narrow commands, stale handling, backend validation | Deterministic correction/readiness | None |
| 30 | 18, 20–21 | Isolated geometry boundary and immutable ModelVersion | Frozen geometry benchmark/baseline | None |
| 31 | 30 | Existing-owner extension, immutable identity | B-rep graph/descriptors/fingerprints | None |
| 32 | 31 | Deterministic evidence and fail-closed unknowns | Patches/features/hierarchy | None |
| 33 | 23, 28–32 | Bounded agent, candidate handles, no IDs, clarification lifecycle | Requirement/retrieval evidence | None |
| 34 | 20–21, 31–32 | Shared Gmsh boundary, immutable MeshRevision, remesh invalidation | Versioned STEP meshes | None |
| 35 | 31–34 | MappingEvidence and `insufficient_evidence`/`stale` states | Verified CAD/INP boundary mapping | None |
| 36 | 28–35 | Immutable artifacts, capability preflight, exact lineage | Complete CalculiX decks/manifests | None |
| 37 | 36 | Versioned results and solver-validity separation | ResultBundle/parser/check contract | None |
| 38 | 18, 21–22, 35–37 | Durable JobService, isolated CalculiX worker, immutable package | Local execution and durable runs | None |
| 39 | 26, 29, 34–38 | Backend authority, immutable revisions/runs, route UI boundary | Closed-loop UI/comparison/bundle | None |
| 40 | 28–39 | All active capability gates and numerical separation | Milestone 2 gate evidence | None |
| 41 | 30–40 | Mode separation, immutable corpus evidence | Frozen 100-case/20-model corpus | None |
| 42 | 28–37 plus licensed approved Abaqus environment | Export-only capability; verified mapping; `unavailable` if license absent | Abaqus validation evidence | None; licensed validation is active release evidence, not connected execution |
| 43 | 38–42 | Local-only operations/security/evidence ownership | Hardening evidence | None |
| 44 | 40–43 | Debian package, migration backup/restore, route rehearsal only | Release candidate/package/docs | None |
| 45 | 16–44 | Final route-cutover authority, release evidence ownership | Tagged technical-preview release decision | None |

## End-to-end dependency assertions

1. Environment and schema versioning precede persistence.
2. Parser/Gmsh isolation precedes durable model ingestion and meshing.
3. Model identity precedes setup, conversation, mesh, mapping, artifact, job,
   and result identity.
4. Setup persistence precedes real V2 read models and edits.
5. Explicit material/coordinate/physics semantics and correction validation
   precede meshing and artifact generation.
6. Geometry evidence precedes mesh mapping.
7. Accepted mapping precedes a solver artifact.
8. A complete artifact and result schema precede automatic execution.
9. JobService ownership and local isolation precede run UI.
10. Closed-loop evidence precedes corpus freeze, Abaqus validation, hardening,
    packaging, and final release.
11. Task 45 alone may approve default-route cutover.

## Post-preview exclusion review

The active dependency graph has no edge to remote/customer runners, connected
Abaqus execution, HPC, classifiers, assemblies, contact, broader elements or
materials, advanced physics, multi-user collaboration, or SaaS. References to
those capabilities in architecture documents are exclusion boundaries only.
