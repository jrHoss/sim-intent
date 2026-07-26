# Lean Technical Preview Release Plan

## Status

This document replaces `TECHNICAL_PREVIEW_PLAN.md` as the active execution roadmap.

`release-goal.md` remains authoritative for the final technical-preview release.

The older technical-preview plan is retained as architectural reference and future backlog material.

## Baseline

- Task 19 merged through PR #5
- Current `main`: `a84eaec4ab27d796ba1a104e89a327f2e0fa2394`
- `demo-v1` remains fixed at:
  `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`
- Task 20 from the former roadmap has not started

## Product Promise

A local single-user technical preview that allows an engineer to:

1. upload a supported STEP or INP model;
2. define supports and loads through text and visual selection;
3. review and confirm the interpreted engineering setup;
4. assign a supported material;
5. generate a mesh;
6. run one linear-static CalculiX analysis;
7. inspect displacement and von Mises stress results;
8. revise and rerun the setup;
9. download a solver-ready and human-readable bundle;
10. close and reopen the project without losing state.

## Supported Envelope

- Single local user
- One active project workflow at a time
- STEP, STP, and supported CalculiX INP input
- Linear static structural analysis
- One consistent unit system per setup
- Isotropic linear elastic material
- Fixed and prescribed displacement
- Resultant force, traction, pressure, nodal force, and gravity
- One deterministic tetrahedral meshing path
- Local CalculiX execution
- Displacement, stress, reactions, extrema, warnings, and balance checks
- Local persistence and project reopen
- Versioned solver and result artifacts

## Explicitly Deferred

Unless a release gate proves them necessary:

- Multi-user support
- Authentication and administration
- Cloud deployment
- Distributed queues or workers
- Microservices
- Assemblies and contact
- Nonlinear analysis
- Additional physics
- General CAD repair
- Plugin frameworks
- Connected Abaqus execution
- ODB result integration
- Screenshot-annotation systems
- Broad frontend rewrites
- Infrastructure added only for hypothetical future needs

---

# Execution Plan

## R0 — Activate the Lean Plan

### Scope

- Add this plan to the repository.
- Mark the former technical-preview plan as superseded for active execution.
- Keep `release-goal.md` authoritative.
- Record the Task 19 merged baseline.
- Establish the lightweight operating process below.

### Done when

- The repository clearly identifies this file as the active plan.
- No product behavior changes.
- No branch for former Task 20 is created.

---

# Phase 1 — Durable Local Product

## R1 — Projects, Revisions, and Artifact Storage

### Scope

Implement only the persistence required by the release flow:

- SQLite with bounded migrations
- Named projects
- Source-model versions
- Immutable intent revisions
- Confirmation decisions
- Mesh records
- Solver-run records
- Result and export references
- Content-addressed binary storage
- Simple stale-write protection for multiple browser tabs

### Out of scope

- Accounts
- Roles
- Collaboration
- Remote databases
- Distributed storage
- General event sourcing

### Done when

A project and its reviewed setup survive application restart and reopen correctly.

---

## R2 — Safe STEP and INP Ingestion

### Scope

- Accept `.step`, `.stp`, and supported `.inp`
- Enforce upload, request, and storage limits
- Sanitize names and paths
- Compute source hashes
- Run parsing in an isolated subprocess
- Enforce parser timeout and termination
- Return defensive, understandable failures
- Replace source models safely
- Invalidate confirmations and derived artifacts when the source changes

### Out of scope

- General CAD healing
- Assemblies
- Automatic repair of arbitrary invalid geometry

### Done when

Valid demo models load repeatedly, malformed or oversized files fail safely, and existing project state remains recoverable.

---

## R3 — Complete Engineering Setup Revision

### Scope

Extend the existing simulation intent with:

- Material assignment
- Coordinate-system declaration
- Global mesh control
- Solver settings
- Deterministic editors for boundary conditions and loads
- Structured confirmation
- Unsupported-capability messages
- Dimensional validation
- Conflict validation
- Immutable revision creation after edits

### Out of scope

- Nonlinear materials
- Contact
- Assemblies
- Advanced local mesh controls
- General coordinate-system authoring

### Done when

An engineer can create and confirm a complete linear-static setup without editing JSON.

---

## Foundation Gate

A clean installation must complete:

```text
create project
→ upload model
→ define and confirm setup
→ close application
→ reopen with state intact
```

---

# Phase 2 — Closed CalculiX Loop

## R4 — Stable Geometry Identity

### Scope

- Geometric and topological face fingerprints
- Explicit entity provenance
- Detection of topology changes
- Confirmed-region invalidation
- Independence from positional face ordering
- Visible source evidence for selections

### Out of scope

- General semantic CAD understanding
- Automatic repair
- Guaranteed support for arbitrary industrial models

### Done when

A selected region remains stable across reloads and is invalidated safely after a source change.

---

## R5 — Tetrahedral Meshing and Quality

### Scope

- One deterministic Gmsh tetrahedral path
- Global mesh-size control
- Remeshing
- Element and node counts
- Minimum and percentile quality
- Aspect ratio
- Inverted or degenerate element rejection
- Clear meshing failures

### Out of scope

- Multiple meshers
- Hex meshing
- Automatic convergence studies
- Advanced local refinement systems

### Done when

The golden parts mesh repeatedly and mesh quality is inspectable before solve.

---

## R6 — CAD-to-Mesh Boundary Mapping

### Scope

- Explicit source-face to mesh-boundary mapping
- Non-empty mapping requirement
- Residual measurement
- Unmapped-area measurement
- Solve blocking for incomplete required regions
- Mapping report visible before execution

### Out of scope

- Approximate silent fallback
- Positional-ID assumptions
- Solver execution with incomplete required mappings

### Done when

Every confirmed golden-region maps to the intended non-empty mesh boundary.

---

## R7 — Complete CalculiX Deck Generation

### Scope

Support:

- Linear elastic isotropic material
- Fixed displacement
- Prescribed displacement
- Resultant force
- Traction
- Pressure
- Nodal force
- Gravity
- Requested result outputs
- Deterministic deck generation
- Deterministic run manifest
- Source, intent, mesh, adapter, and solver hashes
- No manual solver-file repair

### Out of scope

- Additional analysis types
- Contact
- Nonlinear behavior
- Abaqus execution
- User-authored raw solver snippets

### Done when

Generated CalculiX decks are complete, deterministic, and executable without manual repair.

---

## R8 — Isolated CalculiX Execution

### Scope

- Argument-vector invocation without shell interpolation
- Isolated run directories
- Timeout
- Cancellation
- Memory, disk, and output limits
- Standard output and error capture
- Stable terminal states
- Interrupted-run reconciliation after restart

### Terminal states

- queued
- running
- succeeded
- failed
- timed_out
- cancelled
- interrupted

### Done when

Success, failure, timeout, cancellation, and restart all leave the project recoverable and auditable.

---

## R9 — Results and Engineering Checks

### Scope

Parse and validate:

- Displacement vectors
- Displacement magnitude
- Stress tensors where available
- Von Mises stress
- Reactions
- Extrema
- Solver warnings
- Finite-value checks
- Result-cardinality checks
- Reaction-force balance
- Missing-output detection
- Unconstrained-mode warnings
- Separate execution, numerical-adequacy, and engineer-approval states

### Out of scope

- Fatigue
- Buckling
- Modal analysis
- Advanced post-processing
- Claims of engineering adequacy without evidence

### Done when

The backend produces a versioned, auditable result bundle with clear warnings and validation status.

---

## R10 — Results UI, Rerun, Compare, and Bundle

### Scope

Extend the existing UI rather than performing a speculative rewrite:

- Undeformed geometry
- Deformed geometry
- Displacement field
- Von Mises stress field
- Deformation scale
- Extrema
- Solver warnings
- Edit → new immutable revision → rerun
- Compare two revisions
- Download:
  - solver deck
  - solver outputs
  - simulation intent
  - validation report
  - human-readable report
- Reopen without loss

### Out of scope

- General study management
- Collaborative review
- Advanced plotting systems
- Broad frontend platform replacement

### Done when

One engineer completes the full release demonstration without manual JSON or solver-file editing.

---

## Demo Candidate Gate

Three previously unseen supported parts, including the multi-hole bracket, must complete:

```text
clean install
→ upload
→ setup
→ confirm
→ mesh
→ solve
→ inspect results
→ revise and rerun
→ download bundle
→ restart and reopen
```

This is the first meaningful finish line: a production-ish prototype suitable for demonstrations and user evaluation.

---

# Phase 3 — Release Evidence

## R11 — Numerical Verification

### Scope

- Axial-bar analytical case
- Constant-stress analytical case
- Adequate-mesh criterion frozen before testing
- Displacement verification
- Reaction verification
- Stress verification
- Reaction imbalance measurement
- Representative mesh-refinement study
- Clear stress-singularity limitations

### Required thresholds

- Displacement within 2%
- Reactions within 2%
- Stress within 3%
- Reaction imbalance below 1%, or clearly flagged

### Done when

Numerical claims are measured and recorded rather than assumed.

---

## R12 — Evaluation Corpus and Metrics

### Scope

Expand the existing evaluation corpus to:

- 100 frozen cases
- At least 20 legally usable models
- Geometry variety
- Ambiguous instructions
- Clarification cases
- Invalid units
- Unsupported requests
- Stale topology
- Malformed uploads
- Prompt-injection attempts
- Separate LIVE and REPLAY reports
- Latency measurement
- Provider-cost measurement

### Required thresholds

- At least 95/100 correct after no more than one clarification
- Golden path 100% correct
- Zero false-ready outcomes
- At least 18/20 CalculiX models run without manual deck repair

### Done when

The required release metrics are reproduced from frozen inputs and evidence.

---

## R13 — Abaqus Export Validation

### Scope

- One documented Abaqus version
- One golden model
- Generated Python script imports the model
- Regions are created
- Material is created
- Boundary conditions and loads are created
- A job completes
- Export-only status is documented honestly

### Out of scope

- Connected Abaqus execution in the product
- Abaqus worker service
- ODB result integration
- Multi-version Abaqus support

### Done when

Abaqus is accurately described as a validated export target, not a connected solver backend.

---

# Phase 4 — Hardening and Release

## R14 — Reliability and Security Gate

### Scope

- 20 consecutive golden runs
- Process-leak checks
- File-descriptor leak checks
- Forced backend crash and recovery
- Solver timeout and cancellation
- Invalid CAD
- Provider outage
- Low-disk and storage failures
- Upload, request, storage, and job limits
- Dependency scan
- Container vulnerability scan
- SBOM
- License inventory
- Secret scan
- Diagnostic bundle excluding secrets and model contents
- Median and p95 proposal-latency measurement

### Done when

- Recovery gates pass
- No unresolved critical or high security finding remains
- Limits are enforced
- Diagnostic evidence is usable without leaking protected content

---

## R15 — Package and Tag

### Scope

- One pinned Linux installation path
- Versioned container image
- Startup checks
- Health and readiness checks
- Automatic database migration
- Backup, restore, import, export, and delete instructions
- Supported-envelope matrix
- Short tutorial
- Three sample projects
- Limitations
- Engineering-safety disclaimer
- Release manifest and checksums
- Clean-install release rehearsal
- Final independent release review
- Tagged candidate release

### Done when

A new user can install the product, execute the golden demonstration, recover from common failures, and remove local data using the documentation.

---

# Operating Process

Each release slice follows:

```text
short NOW.md
→ implementation
→ focused tests
→ full suite
→ independent risk-based review
→ pull request and CI
→ merge
```

## Size limits

- One task should target 2–5 working days.
- One task should produce one coherent user-visible or engineering capability.
- One task should normally use one PR.
- Split a task before implementation when it exceeds the time or scope limit.

## Communication limits

- Planning report: maximum 400 words
- Implementation report: maximum 600 words
- Review report: maximum 400 words
- Normal execution prompt: approximately 800 words maximum
- Paste full logs only for failures or disputed evidence

## Review depth

### Normal review

Use for ordinary product work:

- acceptance criteria
- focused tests
- full regression
- independent diff review
- PR and CI

### Heavy review

Reserve for:

- geometry identity
- CAD-to-mesh mapping
- solver-deck correctness
- result interpretation
- persistence migrations
- security boundaries
- numerical verification
- final release evidence

## Scope rule

Before accepting additional work, ask:

> Does the supported release workflow fail without this?

If not, defer it.

## Evidence rule

Do not build large evidence systems for ordinary features.

Exhaustive evidence is reserved for:

- migrations
- geometry identity
- solver correctness
- numerical verification
- security
- frozen release fixtures
- final release gates

---

# Finish Lines

## Finish Line A — Demo Candidate

Reached after R10.

A credible production-ish prototype that completes the supported workflow on three representative models.

## Finish Line B — Technical Preview Release

Reached after R15.

The same product with the quantitative evaluation, numerical evidence, Abaqus validation, reliability, security, packaging, and tagged-release requirements required by `release-goal.md`.

---

# Guiding Principle

Build the smallest trustworthy vertical product first.

Harden what users actually touch.

Do not allow the process, plan, or evidence machinery to become larger than the feature being delivered.
