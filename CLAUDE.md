# CLAUDE.md — sim-intent technical preview

## Authority and release state

Repository work follows this authority order:

1. `release-goal.md` is the authoritative technical-preview release definition.
2. `TECHNICAL_PREVIEW_PLAN.md` is the active execution plan. Execute its tasks in order and do not begin a task until its dependencies and the preceding gate are complete.
3. `PROGRESS_TECHNICAL_PREVIEW.md` is the evidence ledger for Tasks 16–45.
4. `docs/roadmap/PRODUCT_V2_ROADMAP.md` is preserved as non-blocking future direction. It does not authorize work, expand the active release, or override the technical-preview plan.
5. `sprint-goal.md`, `EXECUTION_PLAN.md`, and `PROGRESS.md` are the frozen historical V1 scope, plan, and evidence.

The completed V1 prototype is frozen at commit
`154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d` and local annotated tag
`demo-v1`. Do not move or recreate that tag at another commit.

Only the currently approved task is in scope. Never begin the next task
opportunistically.

## Task execution protocol

For every technical-preview task:

1. Read this file, `release-goal.md`, `TECHNICAL_PREVIEW_PLAN.md`, the relevant approved decisions, and `PROGRESS_TECHNICAL_PREVIEW.md`.
2. Verify the exact branch, worktree, baseline, declared dependencies, and previous-task evidence before editing.
3. Work on a dedicated task branch and change only the task’s approved scope.
4. Preserve one authoritative owner for every model, unit, geometry, setup, mapping, artifact, job, and result contract. Extend or wrap existing owners; do not create parallel truth.
5. Add focused tests with implementation work, then run focused, affected-phase, full-regression, formatting, integrity, and security checks required by the task.
6. Record commands, versions, hashes, results, risks, and limitations in `PROGRESS_TECHNICAL_PREVIEW.md`.
7. Obtain independent read-only review before the final task commit.
8. Stop and report a blocker instead of redefining a dependency, gate, supported capability, or Definition of Done.

Do not commit, merge, tag, or push beyond the authority granted for the current
task. Protected-branch changes require review. Never push a tag unless the user
explicitly authorizes it.

## Governance and CI expectations

- Preserve `main` and the V1 baseline; technical-preview work merges through reviewed task changes.
- Keep production, LIVE evaluation, REPLAY, test, and fixture behavior visibly and configurationally distinct.
- Required task evidence includes focused tests, affected integration tests, the complete Python regression suite, applicable browser/JavaScript checks, `git diff --check`, fixture and artifact integrity, dependency review, secret scanning, and scope scans.
- CI must use pinned environments and bounded per-test and suite timeouts once Task 18 establishes them. A hang, skipped required check, replay substituted for LIVE, or unreviewed baseline change blocks progress.
- `tests/fixtures/` and the frozen 15-case evaluation corpus are immutable baseline evidence. Never rebaseline them to hide a failure.
- No dependency, service, persistent record, API, or artifact may be consumed before its creating task is complete.
- The final task commit requires a reviewable diff, recorded evidence, no unintended generated files, and a clean worktree.

## Preserved V1 safety invariants

1. The LLM never emits, guesses, or directly selects CAD, mesh, face, edge, node, element, NSET, or ELSET IDs. It composes typed requests; deterministic code or verified viewer interaction resolves IDs.
2. Every region retains entity IDs, selection method, confidence, verbatim source instruction, and `proposed|confirmed|rejected` status.
3. Proposed, rejected, stale, or otherwise unconfirmed regions cannot reach mesh-bound setup, export, or solve.
4. Ambiguity returns candidates and a clarification. It is never silently auto-selected.
5. `ground/semantics.py` remains the sole current mm-N-MPa conversion and load-semantics owner until an approved task evolves it.
6. Backend validation and confirmation gates are authoritative; frontend state and chat text are not engineering truth.
7. Deterministic code is fully tested. Model behavior is mocked in CI; LIVE provider calls remain separate and explicitly labeled.
8. The frozen legacy viewer contracts remain additive-only compatibility contracts until an approved task changes the route policy.
9. No fixture name, expected entity ID, frozen phrase, or replay result may enter production logic.
10. Provider failure must preserve project/setup state, and REPLAY must never be represented as LIVE.

## Active scope guards

The technical preview is limited to the supported envelope in
`release-goal.md` and `TECHNICAL_PREVIEW_PLAN.md`: single-solid STEP or
supported first-order solid INP input, linear-elastic isotropic small-
displacement static structural analysis, explicit engineer review, automatic
STEP tetrahedral meshing, existing INP meshes without remeshing, isolated local
CalculiX execution, deterministic checks/results, and export-only Abaqus
validation.

Meshing, persistence, solver execution, results, and packaging are now permitted
only when their numbered technical-preview tasks become active. Their presence
in the release goal is not permission to implement them early.

Post-preview capabilities remain non-blocking and out of scope: customer-side
or remote runners, connected Abaqus execution, HPC, classifiers, assemblies,
contact, shells, beams, connectors, composites, nonlinear or advanced physics,
multi-user collaboration, and SaaS.

## Baseline environment facts

- Since Task 18, `uv.lock` (uv 0.11.32, CPython 3.13.14) is the authoritative Python dependency lock; `requirements.txt` is a generated compatibility export verified by `scripts/export_requirements.py --check`. The supported Linux environment is the digest-pinned Debian-trixie container in `Containerfile` with snapshot-pinned native packages; see `docs/environment.md`.
- Runtime modes are startup-fixed via `SIM_INTENT_MODE` (`production` default, `live_evaluation`, `replay`, `test`); production never registers REPLAY fallback routes and the runtime image physically excludes `eval/`, `tests/`, and `fixtures/`.
- `gmsh` on headless Linux requires the documented GLU system library; the full measured shared-library closure is recorded in `docs/environment.md` and pinned in `Containerfile`.
- No compatible `calculix-ccx` package is available directly from Debian trixie, so Task 18 builds CalculiX `ccx 2.23` from official hash-verified CalculiX (dhondt.de) and SPOOLES (netlib) source archives in a dedicated builder stage on the unchanged frozen trixie environment; no cross-release Debian packages and no third-party prebuilt solver binary are used. The supported runtime and CI images carry the stripped `ccx` executable and its pinned runtime-library closure; the source-built binary embeds its build date, so byte-identical `ccx` binaries across rebuilds are not claimed, and each generated executable and image is identified by a recorded digest. See `docs/environment.md`.
- Since Task 19, every setup-bearing checked-in payload declares a positive integer `schema_version`, and `ir/schema_version.py` is the single central version table (`simulation_intent`, `evaluation_case`, `fallback_record`, `replay_record`, API contract, reserved artifact metadata — all currently version 1). Sequential `n → n+1` migration registries, typed version failures, and the authoritative loaders live in `ir/versioning.py`, `eval/versioning.py`, and `app/record_versions.py`; the production registries are legitimately empty because the pre-Task-19 shape *is* version 1. `PUT /session/{session_id}/intent` is the sole, temporary, route-scoped compatibility exception that normalises an absent version to 1. `schema/openapi.json` is the authoritative API contract, `schema/generated/typescript/api-types.ts` is generated from it by pinned tooling in `tools/openapi-types/`, and both are drift-checked in CI. See `docs/schema-versioning.md`.
- Gmsh OCC face tags are stable only for identical source bytes, not regenerated geometry; current inventories are keyed by file hash.
- Current viewer tessellation preserves one glTF node per CAD face named `face_{tag}`.
- `meshio` reads Abaqus INP meshes and existing NSET/ELSET names remain first-class regions.
- The current V1 CalculiX adapter emits an appendable fragment; no production solver worker or result pipeline exists at the frozen baseline.
