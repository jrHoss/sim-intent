# Technical-preview release architecture

## Scope and principles

The technical preview is a single-user local or on-premises modular monolith
for a reviewed, reproducible, linear-static structural analysis of one
supported single-solid STEP part or one supported first-order-solid INP model.
The exact active capability boundary is defined in
[`release-goal.md`](../../../release-goal.md) and summarized in the
[`capability matrix`](capability-matrix.md).

The architecture preserves these governing principles:

1. The backend owns one typed, versioned engineering setup aggregate.
2. The language model cannot emit or select CAD or mesh entity IDs.
3. Deterministic services resolve geometry, units, validation, mapping,
   artifacts, execution status, and results.
4. Every mutation creates or references immutable domain identities and
   revisions.
5. Proposed, rejected, stale, unsupported, blocked, unavailable, or
   insufficient-evidence state cannot produce a solver artifact or job.
6. Large bytes are content-addressed on local disk; relational metadata and
   state are transactionally owned in SQLite.
7. Upload parsing, Gmsh work, and CalculiX execution occur outside the web
   process under bounded local subprocess isolation.
8. The active release has no dependency on customer runners, remote solver
   services, connected Abaqus, HPC, multi-user collaboration, or SaaS.

## Logical components

| Component | Responsibility | Must not own |
|---|---|---|
| HTTP/application layer | `/api/v1`, legacy compatibility routes, V2 asset serving, single-user local request boundary, correlation IDs | Engineering invariants, direct filesystem truth, process execution |
| Application services | Commands, idempotency, optimistic concurrency, orchestration, transactions, read projections | Frontend state, solver-native IDs guessed by models |
| Domain owners | Model/version, setup/revision, conversation/clarification, mesh/mapping, artifact, job, and result invariants | Transport or UI lifecycle |
| SQLAlchemy repositories | Transactional access to SQLite records with explicit unit-of-work ownership | Binary artifact bytes, migrations outside Alembic |
| Alembic migration owner | Ordered database schema evolution | Payload migration or frontend migration |
| Local artifact store | Atomic SHA-256-addressed blob publication and verified reads | Domain identity, approval state, job status |
| Geometry execution boundary | Bounded STEP/INP parsing, tessellation, and later Gmsh meshing in fresh subprocesses | Durable job state, web request state |
| Existing deterministic owners | `ir`, `ground`, `geom`, `app/orchestration.py`, and `export` evolved or wrapped behind versioned interfaces | Parallel replacement pipelines |
| JobService | Durable job commands, transitions, events, cancellation, and restart reconciliation | Solver result interpretation or frontend progress inference |
| CalculiX worker boundary | Execute one immutable package by argument vector under local resource controls | Job status truth, mutable setup lookup, network access |
| React application | Drafts, layout, viewer lifecycle, accessibility, and read-only server cache | Canonical setup, validation, capability, migration, or job transitions |
| ViewerController | Three.js/WebGL resources and rendering of server projections | Engineering state or API orchestration |

## Process and storage view

The deployment is one local application distribution with separate operating
system processes:

```text
Browser
  │ same-origin HTTP
  ▼
Application/API process
  ├── application services and deterministic domain owners
  ├── SQLAlchemy repositories ───────► SQLite database
  ├── artifact-store interface ──────► local SHA-256 artifact tree
  ├── fresh parser/Gmsh subprocess ──► safe temporary directory
  └── durable JobService
         └── local CalculiX worker subprocess
                └── immutable job directory and bounded outputs
```

The parser/Gmsh and CalculiX subprocesses have no network by default. The
configured model provider is the only optional external data path and remains
subject to explicit configuration and the data-egress invariant. No remote
solver or storage service is part of the active release.

## Data and control flow

```text
upload bytes
→ bounded parser subprocess
→ immutable ModelVersion and source artifact
→ versioned SetupRevision and explicit engineer decisions
→ deterministic validation and capability evaluation
→ immutable MeshRevision and MappingEvidence, when applicable
→ immutable ArtifactManifest and solver deck
→ approved immutable Job input package
→ durable JobService transition
→ isolated CalculiX subprocess
→ immutable ResultBundle and numerical checks
→ engineer review and reproducibility bundle
```

Each arrow accepts exact upstream identities. No downstream service looks up a
mutable “current” setup while creating an immutable derivative.

## Consistency model

- SQLite foreign keys are enabled on every connection.
- One application command owns one explicit transaction.
- Domain writes use expected revision/ETag and idempotency keys where retries
  are possible.
- Binary artifacts publish atomically only after hash and size validation.
- Database metadata references only an already durable blob, or a staged blob
  is cleaned if the owning transaction fails.
- Events and progress streams are invalidation/notification mechanisms. They
  never reconstruct engineering truth; clients refetch authoritative views.
- A run and its package are immutable. Any changed input creates a successor
  setup, mesh, artifact, or run identity as appropriate.

## Version boundaries

- New product HTTP routes are under `/api/v1`.
- Payloads and persisted JSON declare an integer `schema_version`.
- Backend OpenAPI is the API contract authority.
- The frontend consumes checked-in generated TypeScript output and a drift
  check; it does not maintain domain migrations.
- Alembic owns relational schema versions.
- Explicit sequential backend registries own payload migration.
- Artifact manifests record adapter, schema, source, setup, mesh, mapping,
  solver, and result versions and hashes.

## Deferred detail

Task 17 intentionally does not design Geometry V2 algorithms, mesh algorithms,
solver decks, result parsing, or user experience behavior. Tasks 30–38 own
those details after their declared prerequisites. Deferral of implementation
detail does not alter the foundational ownership, identity, versioning,
capability, process-isolation, or fail-closed decisions in this architecture.
