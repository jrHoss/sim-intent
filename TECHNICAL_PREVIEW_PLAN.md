# TECHNICAL_PREVIEW_PLAN.md — Simulation Copilot

**Status:** Active execution plan; final validation approved 2026-07-24
**Authority:** `release-goal.md` defines the release target.
**Baseline:** Completed 15-task prototype at `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`
**Execution begins at:** Task 16
**Release type:** Local or on-premises, single-user technical preview
**Primary rule:** Design for later expansion, but implement only the technical-preview scope.

---

## 1. Release outcome

Ship a locally deployable technical preview that lets an FEA-capable mechanical engineer turn:

- one supported single-solid STEP part; or
- one supported CalculiX/Abaqus INP model

into a reviewed, executed, reproducible **linear static structural analysis** without manually editing generated JSON or solver input.

The product helps an engineer construct, inspect, execute, and review a simulation. It does not replace engineering judgment, certify a design, or claim that solver completion proves physical validity.

The primary product flow is:

```text
project
→ source model
→ natural-language and click evidence
→ explicit engineering setup
→ engineer confirmation
→ mesh and boundary mapping
→ local CalculiX execution
→ numerical checks and results
→ immutable rerun/revision
→ reproducibility bundle
```

---

## 2. Supported release envelope

### Included

- Single-solid, three-dimensional STEP parts.
- Existing INP models using explicitly supported first-order solid elements and declared node/element sets.
- Linear elastic isotropic materials with explicit units and engineer confirmation.
- Static, small-displacement analysis.
- Fixed and prescribed translational displacements.
- Resultant surface force, traction, pressure, concentrated nodal force, and gravity.
- Natural-language plus direct viewer-click grounding.
- Deterministic tetrahedral meshing for STEP models.
- Global mesh-size control and remeshing.
- Documented mesh-quality and CAD-to-mesh mapping metrics.
- Automatic isolated local CalculiX execution.
- Displacement, von Mises stress, reaction balance, extrema, logs, and warnings.
- Immutable revisions, reruns, bounded comparison, audit lineage, and reproducibility bundles.
- Inspectable Abaqus Python export validated on one documented Abaqus version.
- Single-user persistence with multi-tab optimistic concurrency.

### Explicitly excluded

- Assemblies, contact, shells, beams, connectors, composites.
- Plasticity, hyperelasticity, large deformation, buckling, dynamics, thermal coupling, fatigue, topology optimization, and certification.
- Automatic geometry repair beyond diagnosed import failure.
- INP remeshing.
- General photographs, arbitrary sketches, and point-cloud reconstruction.
- Multi-tenant SaaS, billing, organization administration, and cloud solver infrastructure.
- Customer-side solver runners, connected Abaqus execution, and HPC schedulers.
- Learned geometry classifiers as a release dependency.
- Autonomous approval or solver submission.

Unsupported requests must return a specific capability error and must never be silently approximated.

---

## 3. Product invariants

1. The language model never emits, guesses, or directly selects CAD or mesh entity IDs.
2. Entity IDs are produced only by deterministic geometry tools or verified viewer interactions.
3. No proposed, rejected, stale, or unconfirmed region reaches mesh-bound setup, export, or solve.
4. Every unit-bearing value stores original text, normalized value, internal unit, and conversion rule.
5. Every solver artifact is bound to immutable hashes of source model, setup revision, mesh, mapping, adapter, and solver version.
6. A changed source model invalidates previous entity confirmations and all derived artifacts.
7. A run is immutable. Editing any input creates a successor setup or run revision.
8. Execution completion, numerical checks, and engineer approval are separate states.
9. Provider failure never corrupts project state; REPLAY and fixtures are never presented as LIVE.
10. Uploads and solver jobs are bounded, sanitized, parsed defensively, and executed without shell interpolation.
11. Source CAD, meshes, solver decks, results, and credentials remain local unless a separate explicit transfer capability is enabled.
12. Chat is not engineering truth. One typed, versioned backend setup aggregate is authoritative.
13. Mapping uncertainty, unsupported physics, invalid units, missing material, and insufficient constraints fail closed.
14. No fixture name, expected entity ID, frozen phrase, or replay output may appear in production logic.
15. The active release does not depend on any post-preview capability.
16. Existing `ir`, `ground`, `geom`, `app/orchestration.py`, and `export` owners are extended or wrapped behind versioned interfaces; they are not silently duplicated.

---

## 4. Product architecture

### 4.1 Local persistence

Use a relational SQLite database with migrations and foreign-key constraints unless Task 17 approves an equivalent local alternative.

SQLite stores:

- projects;
- source-model versions;
- setup revisions;
- confirmations and assumption decisions;
- conversations and clarifications;
- mesh revisions;
- solver runs;
- artifact metadata;
- audit events.

Large binary artifacts remain content-addressed on local disk:

- STEP and INP files;
- glTF assets;
- meshes;
- solver decks;
- solver outputs;
- result fields;
- reproducibility bundles.

The database stores hashes, lineage, size, media type, creation metadata, and relative storage location.

### 4.2 Geometry representation

The technical preview uses a deterministic B-rep topology graph, not a graph database.

Minimum supported structure:

```text
Body
→ Shell
→ Face
→ Wire/loop
→ Edge
→ Vertex
```

Minimum relationships:

- ownership;
- face adjacency;
- shared edges;
- loop membership;
- orientation;
- tangent, smooth, and sharp transitions;
- analytic surface parameters;
- repeated-feature relationships.

The graph may be held in normal Python domain structures and persisted through versioned SQLite-compatible records or content-addressed serialized artifacts.

### 4.3 Progressive retrieval

Natural-language grounding uses bounded deterministic drill-down:

```text
model
→ body
→ feature group
→ semantic patch
→ exact faces or mesh entities
```

The system must not send an unbounded raw face inventory to the model.

Progressive retrieval is implemented with deterministic queries, candidate handles, requirement verification, and at most one bounded orchestration agent. No vector database, graph database, or learned retriever is required.

### 4.4 Frontend

The primary interaction is:

```text
ChatGPT-style conversation
+ integrated 3D engineering workspace
+ authoritative structured setup inspector
```

Backend state remains authoritative. React owns only transient interface state, viewer lifecycle, and drafts.

### 4.5 Solver

The active release uses one application-owned isolated local CalculiX worker.

The active release does not include:

- customer-side runners;
- connected Abaqus;
- remote leasing;
- HPC scheduling.

Abaqus support is export-only, with one golden exported script executed manually or in a controlled licensed test environment for release validation.

---

## 5. Explicit STEP and INP workflows

### 5.1 STEP workflow

```text
STEP upload
→ bounded parse and geometry inspection
→ immutable model version
→ deterministic grounding and engineer confirmation
→ tetrahedral mesh revision
→ verified CAD-boundary-to-mesh mapping
→ complete CalculiX deck
→ isolated local execution
→ normalized results
→ numerical checks
→ engineer review
```

Remeshing creates a new mesh revision and invalidates:

- mesh-bound nodes;
- mesh-bound surfaces;
- solver artifacts;
- solver runs;
- result fields.

### 5.2 INP workflow

```text
INP upload
→ validate supported first-order solid elements
→ validate declared sets and supported sections
→ immutable model version
→ inspect existing mesh and sets
→ edit supported materials, loads, and BCs
→ complete CalculiX deck
→ isolated local execution
→ normalized results
→ numerical checks
→ engineer review
```

INP remeshing is excluded from this release.

### 5.3 Concentrated nodal force

For STEP models:

- concentrated nodal force is available only after meshing;
- the target must be an explicitly selected mesh node or a deterministic geometric-point-to-node mapping;
- the target is bound to one exact mesh revision;
- remeshing invalidates it.

For INP models:

- the target must be an explicit supported node or node set.

---

## 6. Release milestones

### Milestone 0 — Governance and reproducible baseline

Establish the frozen V1 reference, dependency locking, CI, release architecture, supported-envelope decisions, and versioning rules.

### Milestone 1 — Persistent product foundation and chat-first preview

Create durable projects and revisions, bounded upload/parsing, the additive React application, lifecycle-safe 3D viewer, persistent conversations, and a coherent setup read model.

### Milestone 2 — Trustworthy engineering setup, geometry grounding, meshing, and closed CalculiX loop

Implement explicit materials and coordinates, correction commands, deterministic complex-geometry grounding, progressive retrieval, meshing, verified boundary mapping, complete CalculiX generation, execution, results, reruns, comparison, and reproducibility bundles.

### Milestone 3 — Hardening, Abaqus validation, release evidence, documentation, and packaging

Complete the frozen evaluation corpus, analytical verification, repeated end-to-end runs, crash recovery, security work, Abaqus export validation, Linux packaging, documentation, and tagged release candidate.

---

# MILESTONE 0 — GOVERNANCE AND REPRODUCIBLE BASELINE

## Task 16 — Adopt technical-preview governance and freeze V1

**Objective**
Preserve the completed prototype exactly and make this plan authoritative.

**Dependencies**
Completed Tasks 1–15 and baseline commit `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.

**In scope**

- Add this reviewed plan and `PROGRESS_TECHNICAL_PREVIEW.md`.
- Update repository governance so future work follows this plan.
- Create an immutable annotated `demo-v1` tag at the exact baseline.
- Record fixture hashes, evaluation hashes, commands, versions, and known limitations.
- Preserve the legacy application and tests.

**Out of scope**

- Product behavior or implementation.

**Prohibited shortcuts**

- Moving the baseline tag.
- Rebaselining tests to hide failures.
- Editing fixtures during baseline capture.

**Tests**

- Full Python suite.
- LIVE and REPLAY evaluation separation.
- Legacy JavaScript syntax.
- Fixture and artifact hash checks.
- Clean archive reproduction.

**Definition of Done**

- V1 is reproducibly frozen.
- This plan is the active implementation authority.
- No product behavior changed.

**Required evidence**

- Baseline SHA and tag.
- Test summary.
- Fixture hashes.
- Clean worktree.
- Independent review.

---

## Task 17 — Approve release architecture and decision-complete ADRs

**Objective**
Resolve the architecture decisions needed by all active tasks before implementation.

**Dependencies**
Task 16 and the completed repository audit.

**In scope**

Approve ADRs for:

- active supported envelope;
- `/`, `/legacy`, and `/app-v2` route policy;
- React state versus backend authority;
- SQLite, migrations, and local artifact storage;
- model, setup, mesh, artifact, and run identity;
- API and schema versioning;
- package managers and lock tooling;
- upload/parser isolation;
- Gmsh concurrency;
- local CalculiX worker isolation;
- error envelopes;
- capability and unsupported-state handling;
- release evidence ownership.

**Out of scope**

- Detailed Geometry V2 implementation.
- Detailed meshing or solver code.

**Prohibited shortcuts**

- Unresolved gating decisions hidden as implementation TODOs.
- Multi-tenant architecture in the active release.
- Second sources of truth.

**Tests**

- Dependency graph review.
- State-writer matrix.
- Route matrix.
- threat-model review.
- release-goal traceability review.

**Definition of Done**

- Tasks 18–45 can execute without an unresolved foundational decision.
- Every persistent state type has one authoritative owner.

**Required evidence**

- Approved ADRs.
- Ownership table.
- Route and deployment diagram.
- Risk register.
- Independent review.

---

## Task 18 — Reproducible backend/frontend environments and bounded CI

**Objective**
Make clean installation and testing reproducible on the supported Linux target.

**Dependencies**
Tasks 16–17.

**In scope**

- Pin Python dependencies and required Gmsh/CalculiX system packages.
- Add a reproducible backend container or equivalent package.
- Create the React/TypeScript lockfile policy.
- Add Linux CI with suite-level and per-test timeouts.
- Separate production, LIVE evaluation, REPLAY, and test modes.
- Physically exclude fixtures and replay routes from production.

**Out of scope**

- Database schema and product features.

**Prohibited shortcuts**

- Floating direct or transitive dependencies.
- Production fallback to REPLAY.
- Tests that can hang indefinitely.

**Tests**

- Clean locked install.
- Container smoke.
- Production route/mode scan.
- Full backend suite.
- Frontend toolchain smoke.
- Dependency drift and secret scan.

**Definition of Done**

- A clean supported Linux environment can install and run every baseline suite with bounded runtime.

**Required evidence**

- Lockfiles.
- Image/package digest.
- CI links.
- Route-mode matrix.
- SBOM baseline.

---

## Task 19 — Version API, IR, fixtures, and migration contracts

**Objective**
Introduce explicit schema versions before persistent product state is created.

**Dependencies**
Tasks 17–18.

**In scope**

- Version the API contracts and `SimulationIntent`.
- Create sequential migration registries.
- Version examples, evaluation cases, replay records, manifests, and future persisted JSON.
- Publish the authoritative backend schema plus the frontend code-generation and drift-check contract.
- Add API/schema drift checks.

**Out of scope**

- New material or coordinate semantics.
- Product persistence.

**Prohibited shortcuts**

- Shape guessing.
- Unsafe defaulting during migration.
- Frontend-owned migrations.

**Tests**

- Golden old-to-current migrations.
- Unsupported future version.
- Malformed/partial payload.
- Migration idempotency.
- API/schema drift and frontend generation-contract validation.

**Definition of Done**

- Every checked-in or persisted setup payload declares a version and has a deterministic migration or typed unsupported result.

**Required evidence**

- Migration matrix.
- Golden hashes.
- OpenAPI drift report.
- Full regression.

---

# MILESTONE 1 — PERSISTENT PRODUCT FOUNDATION AND CHAT-FIRST PREVIEW

## Task 20 — Contain uploads, parsers, and Gmsh concurrency

**Objective**
Treat STEP and INP uploads as untrusted inputs and isolate parser failures.

**Dependencies**
Tasks 17–19.

**In scope**

- Streaming size limits.
- Filename and media-type sanitization.
- Safe temporary directories.
- Parser CPU, memory, time, and disk limits.
- No-network parsing.
- Gmsh serialization or isolated worker execution.
- Typed parse failures and cleanup.

**Out of scope**

- Geometry repair.
- Product background-job framework.

**Prohibited shortcuts**

- Unbounded request-body reads.
- Shared unsafe Gmsh state.
- Shell interpolation.
- leaking host paths.

**Tests**

- Oversized, truncated, malformed, and spoofed uploads.
- Parser timeout/crash.
- Concurrent upload behavior.
- Gmsh exclusivity.
- cleanup and restart after failure.

**Definition of Done**

- Bad input cannot crash or poison the web process.
- Concurrent parsing queues or fails deterministically.

**Required evidence**

- Threat-case report.
- Resource measurements.
- Cleanup verification.
- Security review.

---

## Task 21 — Persist projects, models, and immutable source-model versions

**Objective**
Create named projects and durable source-model identity.

**Dependencies**
Tasks 17–20.

**In scope**

- SQLite migrations and foreign keys.
- `Project`, `Model`, and immutable `ModelVersion`.
- Exact-byte SHA-256 content hashes.
- Original filename and parser metadata.
- Content-addressed local artifact store.
- Inventory and glTF cache lineage.
- Create-version idempotency.

**Out of scope**

- Setup, mesh, run, and result records.

**Prohibited shortcuts**

- Content hash used as every domain ID.
- Filename-dependent identity.
- Mutable source bytes.
- Database/filesystem dual truth.

**Tests**

- Same bytes, different filenames.
- Changed bytes.
- concurrent create-version.
- restart recovery.
- corrupted/missing artifact.
- migration rollback.

**Definition of Done**

- A project can be created, closed, reopened, and display the exact immutable source-model version.

**Required evidence**

- Identity examples.
- Database migrations.
- Hash verification.
- Restart tests.

---

## Task 22 — Persist immutable setup revisions, decisions, and audit lineage

**Objective**
Replace process-memory setup state with one durable revisioned aggregate.

**Dependencies**
Tasks 19 and 21.

**In scope**

- `SimulationSetup`, `SetupRevision`, `Decision`, and `AuditEvent`.
- Immutable revision hash.
- actor, timestamp, reason, and source evidence.
- optimistic concurrency and ETags.
- idempotency keys.
- transactional autosave and restart recovery.
- approval invalidation after any successor revision.
- no-dual-write migration from current session state.

**Out of scope**

- Conversations and mesh/run records.

**Prohibited shortcuts**

- Mutable current-row truth without history.
- last-write-wins.
- copying approval to changed content.

**Tests**

- restart and multi-process access.
- two-tab stale writes.
- idempotent retry.
- approval invalidation.
- transaction failure and rollback.

**Definition of Done**

- Setup state survives restart, preserves immutable history, and rejects stale edits.

**Required evidence**

- Revision and audit traces.
- concurrency results.
- migration/rollback rehearsal.

---

## Task 23 — Persist conversations and clarification lifecycle

**Objective**
Separate append-only conversation history from authoritative setup revisions.

**Dependencies**
Tasks 19, 21, and 22.

**In scope**

- `Conversation`, `Message`, and `PendingClarification`.
- Stable turn and clarification IDs.
- append-only messages.
- model-version and setup-revision binding.
- consume-on-success clarification answers.
- cancel, expire, retry, and direct-click evidence.
- provider-failure preservation.

**Out of scope**

- Geometry V2 and agent retrieval.

**Prohibited shortcuts**

- LocalStorage engineering truth.
- pop-before-validation.
- chat text as approval.
- mutable message history.

**Tests**

- restart and hydration.
- invalid then valid answer.
- stale model/revision.
- duplicate answer retry.
- provider failure and recovery.
- concurrent turns.

**Definition of Done**

- Conversation and clarification survive restart without corrupting setup state.

**Required evidence**

- lifecycle diagrams.
- persistence traces.
- failure taxonomy.

---

## Task 24 — Build the additive React/TypeScript application foundation

**Objective**
Create the new product frontend without replacing the legacy application.

**Dependencies**
Tasks 17–19.

**In scope**

- `frontend/` with React, TypeScript, Vite, testing, linting, formatting, and Playwright.
- `/app-v2` serving.
- `/legacy` rollback alias.
- frontend types and generated API client produced from the Task 19 authoritative backend schema.
- normalized error handling.
- typed upload, binary download, and scoped update transports.
- CI integration.

**Out of scope**

- Product workflow and Three.js port.

**Prohibited shortcuts**

- importing legacy `app.js` or `audit.js`.
- handwritten duplicate domain schemas.
- production fixtures.
- replacing `/`.

**Tests**

- clean install.
- strict typecheck.
- unit and browser route smoke.
- deep links.
- missing-build behavior.
- API drift.

**Definition of Done**

- `/` remains legacy and `/app-v2` loads independently from a clean build.

**Required evidence**

- build manifest.
- lockfile.
- route tests.
- browser trace.

---

## Task 25 — Implement lifecycle-safe ViewerController and declarative layers

**Objective**
Port the useful viewer behavior behind an explicit lifecycle and deterministic visual state.

**Dependencies**
Task 24 and the accepted frontend audit.

**In scope**

- glTF loading and `face_{id}` resolution.
- orbit, pan, zoom, framing, axes, picking, resize, unload, and disposal.
- generation tokens for stale-load prevention.
- declarative base, selected, candidate, proposed, confirmed, rejected, BC, load, and hover layers.
- focus, isolate, hide/show, camera capture.
- two-tab/two-model isolation.

**Out of scope**

- business API calls and result contours.

**Prohibited shortcuts**

- Three.js objects in global or React domain state.
- imperative highlight commands as truth.
- missing dispose paths.
- cross-model events.

**Tests**

- repeated mount/unmount.
- stale load.
- double disposal.
- pick versus orbit threshold.
- layer precedence.
- two simultaneous viewers.
- WebGL error state and resource-leak checks.

**Definition of Done**

- Viewer state is reconstructable from plain versioned snapshots and all GPU resources have a tested owner.

**Required evidence**

- browser traces.
- lifecycle counters.
- visual regression.
- no console/WebGL errors.

---

## Task 26 — Create chat-first shell and coherent setup read model

**Objective**
Deliver the first visible product surface with honest states.

**Dependencies**
Tasks 22–25.

**In scope**

- conversation-first layout.
- fixed composer.
- integrated viewer.
- structured setup inspector.
- project/model header.
- empty, loading, offline, unsupported, and error states.
- one backend `SetupView` containing exact revision, setup, decisions, validation, eligibility, clarification summary, and visual projection.
- scoped invalidation/update stream that causes refetch, not client-side truth reconstruction.

**Out of scope**

- setup editing, Geometry V2, meshing, or solver execution.

**Prohibited shortcuts**

- fake success or result cards.
- client-owned setup copy.
- global SSE.
- unversioned click evidence.

**Tests**

- coherent hydration under concurrent updates.
- two-tab stale event.
- accessibility and keyboard flow.
- responsive layout.
- production-bundle fixture scan.

**Definition of Done**

- The UI is visibly chat-first, state is coherent and revisioned, and unsupported capabilities are honest.

**Required evidence**

- screenshots.
- accessibility report.
- SetupView contract.
- two-tab browser test.

---

## Task 27 — Milestone 1 preview gate

**Objective**
Release an honest persistent preview without claiming solver readiness.

**Dependencies**
Tasks 20–26.

**In scope**

- create project.
- upload supported source model.
- close and reopen.
- view geometry and current setup state.
- persist conversation and clarification history.
- verify rollback to legacy.

**Out of scope**

- trusted materials, Geometry V2, meshing, export, or solve.

**Tests**

- clean install.
- restart recovery.
- two-tab concurrency.
- upload failure recovery.
- browser/WebGL smoke.
- full regression.

**Definition of Done**

- Milestone 1 is demonstrable from a clean install and all limitations are visible.

**Required evidence**

- preview checklist.
- rollback rehearsal.
- browser traces.
- release notes.

---

# MILESTONE 2 — TRUSTWORTHY SETUP, GEOMETRY, MESHING, AND CLOSED CALCULIX LOOP

## Task 28 — Implement explicit materials, units, coordinate systems, and analysis physics

**Objective**
Remove implicit material, direction, physics, and solver-setting defaults.

**Dependencies**
Tasks 19, 22, and 26.

**In scope**

- explicit linear elastic isotropic material assignments.
- Young’s modulus, Poisson ratio, and density where required.
- original and normalized units with conversion provenance.
- model/world and named Cartesian coordinate systems.
- condition-level vector references.
- explicit named analysis revision metadata.
- explicit physics fixed to static structural, small displacement, and linear elastic isotropic behavior; any other physics is a typed unsupported capability.
- versioned CalculiX solver settings limited to the supported linear-static step and output controls.
- natural-language material proposals requiring explicit confirmation.
- migration of V1 demonstration defaults to unapproved review state.

**Out of scope**

- nonlinear materials and assembly sections.

**Prohibited shortcuts**

- silent steel.
- hidden Y/Z conventions.
- client-side canonical conversion.
- approval surviving value changes.

**Tests**

- unit conversion.
- Poisson bounds.
- density requirements.
- coordinate rotation.
- gravity.
- analysis-physics envelope and solver-setting bounds.
- stale approval and migration.

**Definition of Done**

- Every material, directional condition, analysis-physics choice, and solver setting is explicit, versioned, visible, and confirmed before solve.

**Required evidence**

- schema examples.
- migration report.
- rotation and unit tests.
- UI screenshots.

---

## Task 29 — Implement correction commands and deterministic readiness validation

**Objective**
Let engineers safely edit the supported setup without editing JSON.

**Dependencies**
Tasks 22, 26, and 28.

**In scope**

- command-backed changes for:
  - region membership;
  - BC components;
  - load type, magnitude, unit, and vector;
  - material assignment;
  - coordinate reference;
  - analysis name and supported solver settings;
  - assumption decisions;
  - removal and supersession.
- deterministic validation for:
  - missing material;
  - dimensional incompatibility;
  - conflicting or duplicate conditions;
  - load/BC overlap;
  - rigid-body-mode heuristics;
  - gravity density;
  - net-load summary;
  - analysis-physics envelope and solver-setting bounds;
  - unsupported capability.
- revision diff UI.

**Out of scope**

- Geometry V2 patch growth and solver execution.

**Prohibited shortcuts**

- whole-document PUT.
- frontend canonical IR.
- last-write-wins.
- warning-only false readiness.

**Tests**

- every command.
- stale writes.
- idempotent retry.
- approval invalidation.
- unsupported and conflicting conditions.
- two-tab correction.

**Definition of Done**

- Supported setup corrections create traceable successor revisions and invalid states fail closed.

**Required evidence**

- command schemas.
- revision/audit traces.
- validation matrix.
- browser tests.

---

## Task 30 — Build frozen geometry benchmark and measured baseline

**Objective**
Measure the current geometry system before changing it.

**Dependencies**
Tasks 18, 20, and 21.

**In scope**

- legally redistributable model corpus.
- procedural and public parts.
- multiple hole families, planar/cylindrical/freeform faces, repeated features, rotations, and re-exports.
- geometry ground truth and failure taxonomy.
- current precision, recall, silent-wrong rate, latency, memory, and stability.
- kernel and tolerance decision.

**Out of scope**

- new graph or feature logic.

**Prohibited shortcuts**

- two-fixture claims.
- unlicensed data.
- tuning on frozen holdout models.
- excluding failures after evaluation.

**Tests**

- corpus integrity.
- deterministic ordering.
- transformations.
- scale tiers.
- current fixture regressions.

**Definition of Done**

- The geometry rewrite is justified by measured evidence and the frozen benchmark is versioned.

**Required evidence**

- corpus manifest and licenses.
- baseline report.
- frozen hashes.
- kernel ADR.

---

## Task 31 — Implement immutable B-rep graph, descriptors, and fingerprints

**Objective**
Represent supported STEP topology and deterministic geometric evidence.

**Dependencies**
Task 30.

**In scope**

- body, shell, face, loop/wire, edge, and vertex ownership.
- oriented adjacency and shared-edge relationships.
- analytic surface type and parameters.
- area, perimeter, centroid, local frame, curvature and normal statistics.
- intrinsic transform-stable fingerprints.
- compatibility projection to the existing face inventory.

**Out of scope**

- semantic features, retrieval, or learned classification.

**Prohibited shortcuts**

- tag-only identity.
- positional face mapping.
- fixture-specific tolerances.
- duplicate parser truth.

**Tests**

- graph invariants.
- transform and re-export stability.
- face-tag permutation.
- analytic ground truth.
- degenerate and unsupported topology.
- benchmark performance.

**Definition of Done**

- Every supported STEP model produces a valid immutable graph or a typed unsupported/invalid outcome.

**Required evidence**

- schema.
- invariance report.
- benchmark results.
- compatibility report.

---

## Task 32 — Implement patches, conservative features, patterns, and hierarchy

**Objective**
Create deterministic semantic regions sufficient for the release corpus.

**Dependencies**
Task 31.

**In scope**

- tangent and coplanar patch growth.
- sharp-edge termination.
- through/blind holes, bores, planar pads, and simple cylindrical bosses where verified.
- repeated equal-geometry groups.
- linear and circular hole patterns.
- model→body→feature group→patch→face hierarchy.
- explicit `verified`, `partially_verified`, and `unknown`.

**Out of scope**

- universal machining-history reconstruction.
- learned classifiers.
- assemblies.

**Prohibited shortcuts**

- unbounded flood fill.
- fillet/hole conflation.
- aggregate metrics hiding weak feature families.
- auto-confirmation.

**Tests**

- unseen positives and negatives.
- multiple hole families.
- incomplete patterns.
- ambiguous symmetry.
- split surfaces.
- rotations and re-exports.
- per-family precision and silent-wrong metrics.

**Definition of Done**

- Required feature families meet approved conservative thresholds; uncertain cases remain unknown.

**Required evidence**

- per-family benchmark.
- visual examples.
- threshold rationale.
- failure taxonomy.

---

## Task 33 — Implement requirement parsing, progressive retrieval, and one bounded agent

**Objective**
Ground complete engineering instructions without raw face dumps or silent clause loss.

**Dependencies**
Tasks 23, 28–32.

**In scope**

- typed requirement schema for:
  - concept;
  - count;
  - dimensions;
  - orientation;
  - position;
  - pattern;
  - inclusion/exclusion;
  - material;
  - coordinate system;
  - condition semantics.
- bounded retrieval tools over model, body, feature group, patch, and exact entities.
- server-issued candidate handles.
- clause-by-clause status: `verified`, `contradicted`, `unresolved`, `unsupported`.
- one bounded inspect–narrow–compare–verify agent.
- one targeted clarification when needed.
- complete evidence trace.

**Out of scope**

- classifier.
- arbitrary code execution.
- solver actions.
- agent approval.

**Prohibited shortcuts**

- LLM-provided IDs.
- unbounded inventory prompts.
- dropped clauses.
- aggregate score used as proof.
- multiple autonomous agents.

**Tests**

- bracket with several hole families.
- “six smaller holes around the main bore”.
- exclusions.
- direct clicks.
- ambiguous and unsupported requests.
- prompt injection.
- provider failure.
- latency and token/cost measurement.

**Definition of Done**

- Every critical instruction clause is verified or blocks with one precise clarification or unsupported result.

**Required evidence**

- tool catalog.
- traces.
- frozen-case metrics.
- silent-wrong report.
- latency report.

---

## Task 34 — Create versioned deterministic STEP meshing

**Objective**
Generate inspectable tetrahedral mesh revisions for supported STEP parts.

**Dependencies**
Tasks 20, 21, 31, and 32.

**In scope**

- automatic deterministic first-order tetrahedral meshing for STEP inputs.
- exposed global target size.
- documented internal minimum/maximum size and curvature controls.
- bounded local refinement only when required by the release corpus and approved by ADR.
- mesh revision identity.
- node and element counts.
- quality percentiles.
- aspect ratio.
- inverted and degenerate element blocking.
- remesh invalidation rules.

**Out of scope**

- INP remeshing.
- adaptive error-driven refinement.
- high-order elements.

**Prohibited shortcuts**

- hidden fixture mesh settings.
- unversioned mesh files.
- proceeding with invalid elements.
- retaining mesh-bound selections after remesh.

**Tests**

- deterministic repeat.
- three golden parts.
- thin/small supported features.
- invalid geometry.
- remesh invalidation.
- memory/time limits.
- quality metrics.

**Definition of Done**

- Supported STEP parts produce versioned valid meshes or stable diagnostics.

**Required evidence**

- mesh manifests.
- quality reports.
- deterministic hashes.
- failure cases.

---

## Task 35 — Implement verified source-to-mesh boundary mapping

**Objective**
Map confirmed CAD regions to exact non-empty mesh boundaries with measurable evidence.

**Dependencies**
Tasks 31–34.

**In scope**

- source and target fingerprints.
- explicit CAD-face/patch to mesh-boundary map.
- area and residual measurements.
- unmapped boundary area.
- stale-topology detection.
- mapping confidence categories.
- manual visual confirmation for required regions.
- separate trusted INP set identity path that consumes the existing supported mesh and declared sets without remeshing.

**Out of scope**

- Abaqus positional mapping.
- warning-only uncertain mapping.

**Prohibited shortcuts**

- face-count proof.
- tag/index arithmetic.
- unchecked manual mapping.
- solve with missing required-region mapping.

**Tests**

- permuted face order.
- changed geometry.
- partial and empty mapping.
- repeated similar faces.
- INP declared sets.
- remesh invalidation.
- all release-corpus required regions.

**Definition of Done**

- Every required confirmed region has non-empty accepted mapping evidence or solve is blocked.

**Required evidence**

- mapping manifests.
- residual reports.
- permutation regressions.
- visual confirmation screenshots.

---

## Task 36 — Generate complete CalculiX decks and immutable artifact manifests

**Objective**
Create complete solver-ready CalculiX artifacts from one exact approved setup and mesh revision.

**Dependencies**
Tasks 28–35.

**In scope**

- supported element and set generation.
- materials and sections.
- BCs and loads.
- output requests.
- concentrated nodal force semantics.
- gravity.
- deterministic filenames and formatting.
- artifact manifest with all source, setup, mesh, mapping, adapter, and solver hashes.
- unsupported-capability preflight.

**Out of scope**

- execution and results.

**Prohibited shortcuts**

- current mutable state lookup during generation.
- manual solver-file repair.
- incomplete deck hidden behind warnings.
- artifact emission after failed preflight.

**Tests**

- deterministic byte output.
- all supported load and BC types.
- changed setup/mesh/mapping.
- invalid and unsupported cases.
- analytical deck fixtures.
- clean-container generation.

**Definition of Done**

- Supported setups produce complete deterministic CalculiX decks without manual repair.

**Required evidence**

- deck hashes.
- manifests.
- preflight matrix.
- golden deck review.

---

## Task 37 — Define result schema, parser, and numerical checks

**Objective**
Normalize CalculiX outputs before automatic execution becomes a product capability.

**Dependencies**
Task 36.

**In scope**

- versioned `ResultBundle`.
- job status and solver warnings.
- displacements.
- stress with explicit raw versus nodally averaged semantics.
- reactions.
- extrema.
- finite-value checks.
- mesh/result cardinality.
- reaction-force balance.
- unconstrained-mode and missing-output diagnostics.
- deformation and scalar-field visualization artifacts.

**Out of scope**

- automatic execution.
- engineering certification.
- general cross-mesh field transfer.

**Prohibited shortcuts**

- treating nodal averaging as raw integration-point stress.
- solver exit code as analysis validity.
- silent missing outputs.
- result records without exact lineage.

**Tests**

- known CalculiX outputs.
- malformed and partial files.
- nonconvergence and warnings.
- NaN/Inf.
- reaction imbalance.
- cardinality mismatch.
- analytical verification fixtures.

**Definition of Done**

- CalculiX outputs can be parsed into immutable, versioned, auditable result records with clear numerical status.

**Required evidence**

- schema.
- parser fixtures.
- numerical-check matrix.
- visualization examples.

---

## Task 38 — Implement durable local JobService and isolated CalculiX worker

**Objective**
Execute CalculiX locally under bounded, recoverable job control.

**Dependencies**
Tasks 18, 21–22, and 35–37.

**In scope**

- one durable `JobService`.
- immutable input package.
- isolated working directory.
- argument-vector subprocess invocation.
- timeout, memory, disk, and output limits.
- cancellation.
- captured stdout/stderr.
- process-group cleanup.
- restart reconciliation for interrupted jobs.
- job-state idempotency.
- no-network execution by default.

**Out of scope**

- remote runners.
- Abaqus execution.
- HPC.

**Prohibited shortcuts**

- shell interpolation.
- execution before mapping and artifact preflight.
- volatile-only job state.
- leaked child processes.
- mutable job inputs.

**Tests**

- success, failure, timeout, cancellation, crash, and restart.
- low disk.
- malformed deck.
- 20 sequential and concurrent bounded runs.
- process-leak detection.
- immutable-package tampering.

**Definition of Done**

- The application can safely run and recover local CalculiX jobs with no manual file movement.

**Required evidence**

- job state diagram.
- crash/restart traces.
- resource-limit report.
- process-leak report.
- security review.

---

## Task 39 — Build solver workflow, result UI, rerun, comparison, and reproducibility bundle

**Objective**
Complete the engineer-facing closed loop.

**Dependencies**
Tasks 26, 29, and 34–38.

**In scope**

- mesh generation and remesh UI.
- mesh quality and mapping inspection.
- browser inspection of geometry hierarchy, deterministic dimensions, model units, coordinate systems, and existing INP meshes/declared sets.
- existing supported INP solve path without remeshing.
- authoritative setup inspection and editors for materials, loads, BCs, coordinate systems, analysis physics, and supported solver settings.
- run, cancel, and job-progress UI.
- undeformed and deformed views.
- displacement and von Mises contours.
- reaction balance, extrema, warnings, and numerical-status separation.
- editing an input creates a successor setup revision.
- rerun creates an immutable run.
- bounded comparison:
  - setup differences;
  - material/load/BC/mesh-setting differences;
  - scalar-result differences;
  - separate or side-by-side contours.
- reproducibility bundle containing:
  - source manifest;
  - intent/setup JSON;
  - validation report;
  - mesh manifest;
  - solver deck;
  - job logs;
  - normalized results;
  - artifact hashes;
  - human-readable run report.

**Out of scope**

- general field transfer between unrelated meshes.
- automatic engineering acceptance.

**Prohibited shortcuts**

- optimistic run success.
- client-inferred readiness.
- mutable run records.
- hidden warnings.
- bundle without hashes and versions.

**Tests**

- full STEP and INP paths.
- restart during run.
- edit/rerun/compare.
- download and checksum.
- accessibility and browser/WebGL.
- stale revision and two-tab conflicts.

**Definition of Done**

- An engineer can complete the release demonstration without JSON edits or solver-file repair.

**Required evidence**

- three-part end-to-end traces.
- screenshots.
- bundle examples.
- comparison report.
- accessibility results.

---

## Task 40 — Milestone 2 closed-loop gate

**Objective**
Prove the complete local CalculiX vertical slice before hardening and release claims.

**Dependencies**
Tasks 28–39.

**In scope**

- three previously unseen golden STEP parts, including a bracket with multiple hole families.
- supported INP golden path.
- no fixture-specific branch scan.
- no manual solver-file repair.
- analytical axial-bar and constant-stress checks.
- predeclared mesh-refinement studies for representative displacement and stress quantities, with singular peak stress documented as non-convergent where applicable.
- reaction-balance checks.
- recovery after solver and backend failure.

**Tests**

- clean-install end-to-end runs.
- golden models.
- analytical cases.
- restart/cancellation.
- all active capability blockers.

**Definition of Done**

- Three unseen golden parts complete upload-to-results from a clean installation.
- Analytical tolerances are met.
- Remaining failures are explicit and recoverable.

**Required evidence**

- signed gate report.
- golden model hashes.
- solver and numerical reports.
- failure taxonomy.
- independent engineering review.

---

# MILESTONE 3 — HARDENING, ABAQUS VALIDATION, RELEASE EVIDENCE, AND PACKAGING

## Task 41 — Freeze the 100-case, 20-model reliability corpus

**Objective**
Create the release evaluation set and formal scoring definitions.

**Dependencies**
Tasks 30–40.

**In scope**

- at least 100 frozen cases.
- at least 20 legally redistributable supported-envelope models.
- held-out golden parts.
- planar, cylindrical, freeform, repeated features, rotations, units, axes, ambiguity, clicks, unsupported requests, malformed files, and prompt injection.
- separate interpretation, grounding, unit, clarification, mapping, solver-generation, and end-to-end metrics.
- provider/model, prompt/schema, random controls, latency, token, and cost recording.

**Out of scope**

- classifier training.
- moving failed cases out of the frozen corpus.

**Prohibited shortcuts**

- post-freeze relabeling without governance.
- replay counted as LIVE.
- aggregate accuracy hiding false-ready cases.
- fixture-specific production branches.

**Tests**

- corpus integrity and hashes.
- reference labels.
- equivalent acceptable regions.
- evaluator determinism.
- holdout isolation.

**Definition of Done**

- The release corpus is frozen, licensed, versioned, and independently reviewable.

**Required evidence**

- manifest.
- licenses.
- label review.
- frozen hashes.
- evaluator report.

---

## Task 42 — Validate Abaqus export in one supported release

**Objective**
Prove export-only Abaqus support without adding connected execution.

**Dependencies**
Tasks 28–37 and a licensed approved Abaqus environment.

**In scope**

- inspectable Abaqus Python export.
- verified geometric/set mapping path only.
- one documented Abaqus version.
- one golden model:
  - imports;
  - creates regions;
  - assigns material;
  - applies BCs and loads;
  - creates and completes a job.
- capture logs, versions, limitations, and evidence.

**Out of scope**

- product-submitted Abaqus jobs.
- ODB result integration.
- customer-side runners.

**Prohibited shortcuts**

- positional face mapping.
- claiming general Abaqus execution.
- unrecorded manual script repair.

**Tests**

- deterministic generation.
- face-order permutation safety.
- licensed golden execution.
- unsupported-capability behavior.

**Definition of Done**

- Abaqus support is honestly documented as validated export-only for one supported version.

**Required evidence**

- generated script hash.
- Abaqus version.
- execution log.
- screenshots/results.
- limitation matrix.

---

## Task 43 — Complete reliability, security, recovery, and performance hardening

**Objective**
Pass the nonfunctional release gates.

**Dependencies**
Tasks 38–42.

**In scope**

- 20 consecutive golden end-to-end runs.
- no leaked solver processes or corrupted state.
- forced backend crash during a run and restart reconciliation.
- low-disk and storage-failure behavior.
- provider outage and invalid CAD.
- solver timeout and cancellation.
- upload/request/storage/job limits.
- dependency and container vulnerability scans.
- SBOM, license inventory, and secret scan.
- structured logs and metrics with correlation IDs across request, setup revision, mesh, artifact, and run lineage.
- diagnostic bundle excluding secrets and source-model content by default.
- proposal latency and provider-cost measurement.

**Out of scope**

- SaaS operations and remote-runner security.

**Prohibited shortcuts**

- reporting unmeasured performance.
- hiding failed security checks.
- diagnostics that leak CAD or credentials.
- unbounded retries.

**Tests**

- all stated failure modes.
- browser and backend soak.
- process and file-descriptor leak checks.
- performance on documented reference workstation.
- backup/restore and upgrade.

**Definition of Done**

- No open critical/high findings.
- Recovery and performance gates pass or release is blocked.

**Required evidence**

- 20-run report.
- crash-recovery trace.
- vulnerability reports.
- latency percentiles.
- license inventory.
- diagnostic bundle example.

---

## Task 44 — Package, document, and create the release candidate

**Objective**
Produce one clean, reproducible Linux technical-preview package.

**Dependencies**
Tasks 40–43.

**In scope**

- pinned Linux installation path.
- versioned container image or equivalent package.
- startup dependency checks.
- automatic database migration.
- implemented backup, restore, project import/export, and configurable retention/deletion controls with published instructions.
- health/readiness endpoints.
- concise user guide.
- supported-envelope matrix.
- tutorial and sample projects.
- engineering-safety disclaimer.
- limitations and recovery instructions.
- release manifest and checksums.

**Out of scope**

- cloud deployment and enterprise administration.

**Prohibited shortcuts**

- undocumented host dependencies.
- mutable “latest” as the only release artifact.
- missing migration or rollback procedure.

**Tests**

- clean install.
- upgrade from prior preview schema.
- backup/restore.
- sample-project import.
- project export/import round trip.
- retention and deletion controls.
- uninstall/delete.
- package checksum.
- offline local operation where configured.

**Definition of Done**

- A new user can install, run the golden demonstration, recover from failure, and remove local data using published instructions.

**Required evidence**

- package digest.
- clean-install recording.
- upgrade/restore report.
- documentation review.
- release manifest.

---

## Task 45 — Final tagged technical-preview release gate

**Objective**
Decide whether the tagged release candidate satisfies every release goal.

**Dependencies**
Tasks 16–44.

**Release gates**

### Intent and safety

- At least 95/100 frozen cases correct after at most one clarification.
- 100% correct entity set, condition type, and normalized quantity on the golden path.
- Zero observed false-ready outcomes across the frozen release corpus.
- LIVE and REPLAY metrics reported separately.

### Geometry, solver, and numerical checks

- 100% of required confirmed regions map to non-empty mesh boundaries on the release corpus.
- CalculiX decks execute without manual repair for at least 18/20 supported models.
- Axial-bar displacement and reaction are within 2%.
- Constant-stress value is within 3% on the declared adequate mesh.
- Reaction-force imbalance is below 1% for applicable converged golden cases, or the run is visibly flagged.
- One Abaqus golden model completes from the generated export in the documented version.

### Software quality and operability

- Full unit, integration, migration, browser, security-negative, and solver-golden suites pass from a clean checkout.
- 20 consecutive golden end-to-end runs complete without process restart, leaked solver processes, or corrupted state.
- Forced backend crash preserves the last committed revision and reports the interrupted run after restart.
- Median interaction-to-proposal latency is at most 8 seconds and p95 at most 20 seconds on the documented workstation, excluding provider outage.
- No open critical/high security findings.
- No committed secrets.
- Complete third-party license inventory.

**Definition of Done**

The release is approved only when:

- every gate passes on the tagged candidate;
- the three-part golden demonstration succeeds from a clean installation;
- restart and solver failure do not lose committed project state;
- supported scope, limitations, numerical evidence, data flow, and recovery instructions are published.

If any gate fails, the release remains blocked with a named owner and corrective task.

**Required evidence**

- signed release checklist.
- tagged SHA.
- package digest.
- complete traceability matrix.
- independent engineering, security, and release reviews.

---

## 7. Evaluation definitions

### Correct evaluation case

A case is correct only when all required outputs match the reviewed reference:

- supported or unsupported classification;
- complete interpreted requirements;
- condition type;
- normalized quantity and direction;
- selected entity set or accepted equivalent region;
- required clarification behavior;
- no unsafe readiness decision.

### Reference entity set

The exact CAD or mesh entities independently labeled as satisfying the instruction for one frozen model version.

### Equivalent acceptable region

A different entity set accepted in advance because it is geometrically and physically equivalent for the supported setup. Equivalence must be documented before release scoring.

### One clarification

One system question followed by one engineer answer. Internal tool calls do not count. A second user-facing question means the case exceeded the gate.

### Frozen holdout model

A model whose geometry, labels, expected entities, and test instructions are not used to tune production logic after the corpus freeze.

### False-ready outcome

Any state marked ready for mesh-bound setup, export, or solve when an unsupported, ambiguous, stale, invalid-unit, unconfirmed, incompletely mapped, or otherwise blocking condition exists.

### Mapping residual

A documented geometric discrepancy between a source CAD boundary and the mapped mesh boundary, computed by the approved mapping algorithm and reported with units and thresholds.

### Supported-envelope solver completion

A CalculiX job that:

- uses only supported elements, materials, loads, and BCs;
- starts from a complete generated deck;
- requires no manual deck repair;
- terminates with a stable parsed status;
- leaves the project recoverable whether successful or failed.

### Adequate mesh

A mesh that passes release quality gates and the predeclared refinement criterion for the analytical verification quantity. The criterion is frozen before the final release run.

---

## 8. Traceability summary

### Release demonstration

| Step | Responsible tasks | Required evidence |
|---|---|---|
| Create project and upload | 20–21, 24–27 | clean-install project trace |
| Inspect geometry, units, sets | 25–26, 28, 31–32 | browser and geometry reports |
| Define/edit materials, BCs, loads | 28–29, 33 | revision and command traces |
| Resolve ambiguity and review evidence | 23, 26, 33 | clarification and SetupView traces |
| Mesh, inspect quality, remesh | 34–35, 39 | mesh and mapping reports |
| Run isolated CalculiX job | 36–38 | job package and execution logs |
| View results and warnings | 37, 39 | result bundle and screenshots |
| Revise, rerun, compare | 22, 29, 39 | immutable revision comparison |
| Download reproducibility bundle | 36–39 | checksum-verified bundle |
| Close and reopen without loss | 21–23, 27, 38–39 | restart recovery trace |

### Workstreams

| Release-goal workstream | Responsible tasks |
|---|---|
| Durable project and revision model | 19, 21–23 |
| Engineering setup editor | 26, 28–29 |
| Stable geometry and mesh lineage | 30–35 |
| Verified solver execution and results | 36–39 |
| Reliability evaluation | 40–43, 45 |
| Packaging, security, and operations | 18, 20, 38, 43–45 |

### Product invariants

| Invariant area | Responsible tasks |
|---|---|
| No LLM entity IDs | 31–33 |
| No unconfirmed region reaches solve | 22, 29, 35–38 |
| Unit provenance | 19, 28 |
| Immutable artifact lineage | 21–22, 34–39 |
| Source change invalidation | 21–22, 31, 34–35 |
| Immutable runs and revisions | 22, 38–39 |
| Execution versus validity versus approval | 29, 37–39 |
| Provider failure and LIVE/REPLAY separation | 18, 23, 33 |
| Upload and solver isolation | 20, 38, 43 |
| Local data-egress control | 17–18, 20, 43–44 |

---

## 9. Post-preview roadmap

The following items are intentionally non-blocking and require separate approved plans after the technical preview is evaluated with engineers:

1. Customer-side signed solver runners.
2. Connected CalculiX outside the local application worker.
3. Connected Abaqus execution and trusted ODB postprocessing.
4. HPC scheduler integrations.
5. Learned geometry classifiers and graph neural models.
6. Assemblies and contact.
7. Shells, beams, connectors, and composites.
8. Nonlinear, thermal, dynamic, fatigue, and optimization workflows.
9. Multi-user collaboration and enterprise identity.
10. Multi-tenant SaaS, billing, and cloud solver infrastructure.

---

## 10. Plan-change rule

Any proposed expansion of the active release scope must:

1. identify the release-goal requirement it satisfies;
2. show why an existing task cannot satisfy it;
3. provide dependency, safety, schedule, and test impact;
4. receive explicit product and engineering approval;
5. update the traceability matrix before implementation.

No post-preview capability may become an active dependency through implementation convenience alone.
