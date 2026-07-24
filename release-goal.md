# Simulation Copilot technical-preview release goal

## Outcome

Ship a locally deployable technical preview that lets a mechanical engineer
turn a single-solid STEP part or supported CalculiX/Abaqus INP model into a
reviewed, executed, and reproducible **linear static structural analysis**
without manually editing generated JSON or solver input.

The product must help an engineer construct and inspect a simulation; it must
not claim to replace engineering judgment or certify that a result is safe.
Every probabilistic interpretation remains a proposal. Geometry resolution,
unit conversion, model construction, solver execution, and readiness checks
remain deterministic and auditable.

This is a product-development milestone. The target user is an FEA-capable
engineer evaluating the tool on non-safety-critical parts in a local or
on-premises environment.

## Release demonstration

Starting from a clean installation, an engineer can:

1. Create a named project and upload a supported STEP or INP model.
2. Inspect geometry, coordinate systems, dimensions, existing sets, and model
   units in the browser.
3. Define or edit materials, constraints, and loads through natural language
   plus direct face selection.
4. Resolve ambiguities and review entity IDs, units, assumptions, and source
   evidence before accepting each condition.
5. Generate a tetrahedral mesh, inspect quality and boundary mapping, adjust
   global mesh size, and remesh.
6. Run a CalculiX linear-static job in an isolated local worker with time,
   memory, and output limits.
7. View undeformed/deformed geometry, displacement magnitude, von Mises stress,
   reaction-force balance, extrema, and solver warnings.
8. Revise an input, rerun, and compare two immutable analysis revisions.
9. Download the solver deck, results, intent JSON, validation report, and a
   human-readable run report as one reproducibility bundle.
10. Close and reopen the application without losing the project, confirmation
    state, artifacts, or audit trail.

The golden demo uses at least three previously unseen parts, including a
bracket with multiple hole families, and completes without database edits,
fixture-specific branches, or manual solver-file repair.

## Supported release envelope

### Included

- Single solid, three-dimensional STEP parts.
- Existing INP models limited to supported first-order solid elements and
  explicitly declared node/element sets.
- Linear elastic isotropic materials with engineer-entered properties and
  explicit unit conversion.
- Static small-displacement analysis.
- Fixed and prescribed translational displacement constraints.
- Resultant surface force, surface traction, pressure, concentrated nodal
  force, and gravity.
- Deterministic tetrahedral meshing with global size control and documented
  quality metrics.
- CalculiX generation, isolated execution, result parsing, and visualization.
- Inspectable Abaqus Python export, tested in a supported Abaqus version but
  not automatically submitted by the product.
- Text and viewer-click grounding. Screenshot markup remains optional only if
  it can use the recorded camera projection and deterministic ray casting.
- Single-user local deployment with persistent projects and versioned runs.

### Explicitly excluded

- Contact, assemblies, shells, beams, connectors, composites, plasticity,
  hyperelasticity, large deformation, buckling, dynamics, thermal coupling,
  fatigue, topology optimization, or certification workflows.
- Automatic geometry repair beyond clearly diagnosed import failures.
- General photographs, arbitrary sketches, or point-cloud reconstruction.
- Multi-tenant SaaS, organization administration, billing, and cloud solver
  infrastructure.
- Autonomous approval, solver submission, or claims of physical correctness.

Requests outside this envelope must produce a specific unsupported-capability
message and must not be silently approximated.

## Product invariants

1. The model never emits or chooses CAD/mesh entity IDs.
2. No proposed or rejected region reaches meshing, export, or solve.
3. Every unit-bearing value stores original text, normalized value, internal
   unit, and conversion rule.
4. Every solver artifact is bound to immutable hashes of source geometry,
   intent revision, mesh, adapter version, and solver version.
5. A changed source model invalidates prior entity confirmations and derived
   artifacts.
6. A run is immutable; edits create a new revision.
7. Solver success is not analysis validity. The UI distinguishes execution
   completion, numerical checks, and engineer approval.
8. LLM/provider failure never corrupts the current project and replay output is
   never labeled live.
9. Untrusted uploads and solver decks are size-limited, name-sanitized, parsed
   defensively, and executed without shell interpolation.
10. No credential, source model, prompt, or solver artifact leaves the machine
    unless the user explicitly enables the configured model provider.

## Workstreams and deliverables

### 1. Durable project and revision model

- Introduce a small explicit persistence layer, initially SQLite with schema
  migrations and foreign-key constraints.
- Store projects, source-model versions, intent revisions, confirmation and
  assumption decisions, meshes, runs, artifacts, and audit events.
- Keep large binary artifacts content-addressed on disk; store their hashes and
  metadata transactionally.
- Add autosave, restart recovery, project import/export, retention controls,
  and optimistic concurrency for multiple browser tabs.
- Document lineage from user instruction to result artifact.

### 2. Engineering setup editor

- Extend the IR with explicit material assignments, coordinate systems, mesh
  controls, solver settings, named analysis revisions, and schema versioning.
- Add deterministic UI editors for material properties, vectors, magnitudes,
  components, region replacement, and force/traction/pressure semantics.
- Support natural-language material proposals, but require structured
  validation and explicit engineer confirmation.
- Add dimensional checks, Poisson-ratio bounds, density requirements, duplicate
  and conflicting-condition detection, rigid-body-mode heuristics, load/BC
  overlap warnings, and net-load summaries.

### 3. Stable geometry and mesh lineage

- Replace face-order mapping with geometric/topological fingerprints plus an
  explicit source-to-mesh boundary map and measured mapping residuals.
- Detect topology changes and refuse stale confirmations.
- Port the useful base `simcopilot` meshing, quality, and CAD-to-mesh mapping
  ideas behind tested `sim-intent` interfaces; do not copy fixture-specific
  logic.
- Expose mesh size, element/node counts, min/percentile quality, aspect ratio,
  unmapped boundary area, and mapping confidence.
- Block solve on inverted/degenerate elements or incomplete required-region
  mapping; warn on configurable marginal quality thresholds.

### 4. Verified solver execution and results

- Generate complete CalculiX decks from confirmed, versioned intent and mesh.
- Execute jobs in a dedicated subprocess/worker with an argument vector,
  isolated working directory, timeout, memory/disk limits, cancellation, and
  captured stdout/stderr.
- Parse job status, displacement, stress, reactions, and solver warnings into a
  versioned results schema.
- Visualize deformed shape and scalar fields without treating nodal averaging
  as raw integration-point stress.
- Check force/reaction balance, finite values, unconstrained modes, missing
  outputs, and mesh/result cardinality.
- Validate the Abaqus adapter by executing golden generated scripts in one
  documented Abaqus release. Treat Abaqus support as export-only until that
  environment is part of repeatable release testing.

### 5. Reliability evaluation

- Expand from 15 cases on two fixtures to at least 100 frozen cases across at
  least 20 legally redistributable models.
- Stratify the corpus by planar/cylindrical/freeform faces, repeated features,
  units, axes, ambiguity, clicks, unsupported requests, malformed files, and
  prompt-injection attempts.
- Maintain separate scores for interpretation, grounding, unit semantics,
  clarification, false-safe rate, mesh mapping, solver generation, and
  end-to-end completion.
- Add analytical verification problems and CalculiX regression decks. Include
  mesh-refinement studies for representative stress and displacement outputs;
  document quantities expected not to converge, such as singular peak stress.
- Record provider/model version, prompt/schema version, fixture hash, random
  controls where applicable, latency, and token/cost measurements.

### 6. Packaging, security, and operations

- Provide one supported Linux installation path using a pinned lockfile and a
  versioned container image or equivalent reproducible package.
- Add startup dependency checks, database migrations, health/readiness
  endpoints, structured logs with correlation IDs, and user-downloadable
  diagnostic bundles with secrets and model content excluded by default.
- Enforce upload, request, storage, and job limits; document local data flow and
  deletion behavior.
- Add dependency and container vulnerability scanning, a software bill of
  materials, license inventory, and a secret scan.
- Test clean install, upgrade, backup/restore, cancellation, crash recovery,
  low-disk behavior, provider outage, invalid CAD, and solver timeout.
- Publish a concise user guide, supported-envelope matrix, tutorial, sample
  projects, limitations, and engineering-safety disclaimer.

## Quantitative release gates

All gates apply to the tagged release candidate, not a development worktree.

### Intent and safety

- At least 95/100 frozen evaluation cases correct after at most one
  clarification.
- 100% correct entity set, condition type, and normalized quantity on the
  release golden path.
- Zero false-ready outcomes across all ambiguous, unsupported, invalid-unit,
  unconfirmed, stale-topology, malformed-file, and prompt-injection cases.
- All live metrics are reported separately from replay; no fallback counts
  toward live accuracy.

### Geometry, solver, and numerical checks

- 100% of required confirmed boundary regions mapped to non-empty mesh
  boundaries on the release corpus; residual and unmapped-area thresholds are
  recorded per run.
- Generated CalculiX decks execute without manual repair for at least 18 of 20
  supported-envelope models; every failure has a stable diagnostic and leaves
  the project recoverable.
- Analytical verification cases meet documented tolerances chosen before the
  run. At minimum: axial bar displacement and reaction within 2%, and
  constant-stress value within 3% on an adequate mesh.
- Reaction-force imbalance is below 1% for applicable converged golden cases,
  or the run is visibly flagged.
- One Abaqus golden model imports, creates regions/material/BCs/loads, and
  completes a job from the generated script in the documented supported
  version.

### Software quality and operability

- The full test suite completes without hangs on supported Linux CI. The
  existing frontend test stall is fixed and protected by per-test and
  suite-level timeouts.
- Unit, integration, browser end-to-end, migration, security-negative, and
  solver golden tests all pass from a clean checkout.
- At least 20 consecutive golden end-to-end runs complete without process
  restart, leaked solver processes, or corrupted state.
- A forced backend crash during a run preserves the last committed project
  revision and reports the interrupted run after restart.
- Median interaction-to-proposal latency is at most 8 seconds and p95 at most
  20 seconds on the documented reference workstation, excluding model-provider
  outages. Latency and provider cost are reported, not assumed.
- No open critical/high security findings; no committed secrets; complete
  third-party license inventory.

## Delivery sequence

### Milestone 1 — Reproducible foundation

Fix Linux CI completion, pin dependencies, add schema migrations and durable
projects, formalize the supported envelope, and preserve all current 15-case
behavior.

Exit: a clean install can create, close, reopen, and export a reviewed project,
and every test suite has a bounded runtime.

### Milestone 2 — Closed CalculiX loop

Add the versioned engineering setup, meshing, stable boundary mapping, isolated
CalculiX execution, result parsing, visualization, and reproducibility bundle.

Exit: three golden parts complete upload-to-results with no manual file edits,
and analytical checks meet predeclared tolerances.

### Milestone 3 — Hardening and evidence

Expand the evaluation corpus, validate Abaqus export, add browser workflows,
security/operational controls, failure recovery, documentation, and release
packaging.

Exit: every quantitative release gate passes on the tagged candidate and the
remaining limitations are published.

## Alternatives considered

### Extend the base `simcopilot` application directly

This would expose meshing and results sooner, but would weaken the more rigorous
LLM boundary, session state machine, provenance, and evaluation design already
present in `sim-intent`. Use the base as an experimental reference instead.

### Launch a multi-tenant cloud SaaS next

Cloud collaboration may become commercially valuable, but tenancy, billing,
data governance, cloud solver isolation, and enterprise identity would consume
the milestone without resolving the core engineering-validity gaps. Preserve a
service boundary, but validate the local technical product first.

### Add more physics before solver execution is hardened

Contact, nonlinear, thermal, and dynamic analysis expand the failure surface
faster than the current evidence base. A complete, measured linear-static loop
has higher learning value and creates reusable persistence, meshing, execution,
results, and validation infrastructure.

## Principal risks

- **Topology identity:** CAD kernels and meshing can change entity ordering.
  Mitigation: content hashes, geometry fingerprints, residual-based mapping,
  stale-state invalidation, and mandatory visual confirmation.
- **False confidence from solver completion:** a converged job can still model
  the wrong physics. Mitigation: separate readiness/execution/adequacy states,
  equilibrium and constraint checks, visible assumptions, and no certification
  claims.
- **Stress singularities and mesh dependence:** peak stress can be misleading.
  Mitigation: refinement studies, percentile/path reporting, singularity
  warnings, and documented result semantics.
- **Evaluation overfitting:** fixture-specific rules can inflate accuracy.
  Mitigation: unseen holdout parts, corpus hashes, branch scans, frozen cases,
  and independent live/replay reporting.
- **Solver and CAD dependency portability:** gmsh/OCC/CalculiX behavior varies
  by platform and version. Mitigation: one pinned Linux target first, recorded
  versions, golden artifacts, and an explicit compatibility matrix.
- **Scope pressure:** product polish and broader physics can dilute core
  reliability. Mitigation: treat the supported envelope and release gates as
  change-controlled; record any expansion in an ADR.

## Definition of ready to release

The technical preview is ready only when a tagged, reproducibly packaged build
passes every release gate, completes the three-part golden demonstration from a
clean installation, survives restart and solver failure without data loss, and
ships with its supported envelope, limitations, numerical evidence, data-flow
description, and recovery instructions.
