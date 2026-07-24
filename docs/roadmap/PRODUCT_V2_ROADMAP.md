# PRODUCT_V2_ROADMAP.md — Simulation Copilot Product V2

**Status:** Restructured master execution plan for approval
**Baseline:** Completed 15-task sprint prototype at `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`
**Product work begins at:** Task 16
**Repository audit:** Task 18 planning evidence completed on 2026-07-23
**Primary rule:** The V1 prototype remains preserved and reproducible. Product V2 advances only through the dependencies and release gates in this plan.

---

## 1. Purpose and preserved product vision

The sprint proved this controlled workflow:

```text
natural-language or click evidence
→ typed intent operations
→ deterministic geometry grounding
→ engineer review and confirmation
→ solver-specific artifact generation
```

Product V2 must preserve that safety boundary while growing into a product that provides:

- a ChatGPT-style conversation interface;
- an integrated 3D engineering workspace;
- typed, versioned simulation setup state;
- complex unseen single-part STEP geometry;
- a complete B-rep topology graph;
- deterministic feature recognition and semantic patches;
- progressive geometry retrieval;
- one bounded tool-using orchestration agent;
- verified CAD-to-solver entity mapping;
- automatic CalculiX and connected Abaqus execution without manual file movement;
- normalized simulation results and 3D result visualization;
- learned geometry classifiers only after deterministic readiness;
- assemblies and advanced physics only after the single-part vertical slice is reliable.

The first supported engineering scope remains static structural, linear-elastic isotropic, single-part geometry or trustworthy existing FE meshes, with explicit engineer approval. Contact, assemblies, nonlinear behavior, dynamics, thermal coupling, fatigue, fracture, and autonomous convergence diagnosis remain deferred.

---

## 2. Repository-grounded starting point

The accepted Task 18 audit established these facts:

- The current frontend is vanilla JavaScript in `app/static/`.
- `app.js` and `audit.js` eagerly query fixed DOM nodes and mix network, DOM, and Three.js lifecycle state. They must not be imported into React.
- Reusable viewer concepts are the `face_{id}` glTF naming contract, named-ancestor face registration, camera framing, ray picking, click/orbit thresholds, axes behavior, and current visual appearance.
- The backend `SimulationIntent`, session transitions, validation, and export gates are authoritative. React must not become a second setup store.
- `/select` is telemetry only; browser click evidence is currently local and unversioned.
- `/events` is global, transient, and model-unscoped.
- Pending clarification is process-memory state and is destructively consumed before full validation.
- The current session ID is the content-derived model ID. There is no project, conversation, setup revision, tenant, or durable multi-user state.
- Audit and intent are partial projections without a shared revision token.
- The current frontend has no JavaScript execution, browser, WebGL lifecycle, two-tab, or visual-regression test suite.
- The current STEP-to-Abaqus path uses a positional face assumption that Product V2 must block until mapping evidence exists.
- Current orchestration inserts demonstration steel and uses implicit direction conventions. Product V2 must replace both with explicit approved state.
- The current Python dependencies are not locked, there is no backend container contract, and the uploaded file is parsed in the web process without product-grade containment.

Task 18 is completed planning evidence. Future agents must use its accepted findings; they must not schedule another generic frontend/API audit.

---

## 3. Honest release milestones

### V2.1 — Chat-first preview UI

- React/TypeScript/Vite foundation;
- additive `/app-v2` route;
- legacy `/` retained;
- chat-first visual shell with honest empty states;
- lifecycle-safe viewer parity and declarative visual layers;
- test-only card fixtures physically excluded from production builds.

**Claim boundary:** V2.1 is a preview, not a production-ready conversation or engineering workflow.

### V2.2 — Trustworthy structured workflow

- versioned APIs and setup read models;
- immutable model versions and durable setup revisions;
- persistent conversation/setup separation;
- safe clarification and scoped selection/highlight contracts;
- explicit materials and coordinate systems;
- granular correction commands;
- unsafe STEP-to-Abaqus capability blocked;
- safe, capability-gated real conversation/review/export flow;
- explicit default-route cutover gate.

### V2.3 — Complex single-part deterministic geometry and progressive retrieval

- benchmark and measured current baseline;
- complete immutable B-rep graph;
- rich deterministic descriptors;
- semantic patches;
- conservative analytic and compound feature candidates;
- hierarchical geometry index;
- requirement parsing, typed retrieval, and clause-coverage verification.

### V2.4 — Bounded orchestration agent

- one agent over deterministic typed tools;
- bounded inspect–narrow–compare–verify loop;
- safe clarification and correction continuation;
- complete tool/evidence traces;
- production gate based on silent-wrong-selection metrics.

### V2.5 — Verified automatic CalculiX pipeline

- controlled meshing and verified named solver sets;
- complete CalculiX artifacts;
- normalized result schema and trusted postprocessor;
- one durable job-state owner;
- signed immutable job packages;
- managed isolated CalculiX execution;
- no manual file movement.

### V2.6 — Connected Abaqus runner and normalized result visualization

- secure customer-side runner;
- connected CalculiX and Abaqus execution;
- licensed Abaqus capability/version handling;
- normalized displacement, stress, reactions, logs, and warnings;
- 3D result visualization;
- operations and security gate.

### Later

- advisory learned geometry classifiers;
- assemblies and contact;
- nonlinear and advanced physics;
- additional solver and HPC profiles.

---

## 4. Non-negotiable architecture and safety rules

1. The LLM never invents model or solver entity IDs.
2. Chat is not engineering truth. One typed, versioned setup aggregate is authoritative.
3. The agent proposes; deterministic gates and humans approve.
4. Every entity belongs to one immutable model version.
5. Model content hash and model-version ID are separate concepts.
6. Uncertain geometry, mapping, and solver capability fail closed.
7. No fixture name, expected entity ID, frozen phrase, or replay output appears in production logic.
8. No implicit material or direction default is accepted as approved product state.
9. No positional CAD-tag-to-solver-face mapping is a supported V2 capability.
10. No arbitrary LLM- or user-generated code executes on a server or runner.
11. Production, LIVE evaluation, REPLAY, test, and fixture modes are physically/configurationally separated.
12. Important state transitions carry actor, revision, timestamp, and lineage.
13. Uploaded files are untrusted and parsed under limits and isolation.
14. Production state survives restart and concurrent multi-worker use.
15. Every write supports idempotency or optimistic concurrency appropriate to its semantics.
16. No dual write is allowed during migration. Every record has one authoritative writer.
17. OpenAPI and generated clients, API schemas, and `SimulationIntent` schemas are versioned and drift-checked.
18. Dependencies are pinned and backend/frontend environments are reproducible.
19. The existing interpreter, queries, semantics, grounding, validation, session, and export code is extended or wrapped; it is not silently duplicated.
20. The simplest safe modular monolith plus workers is preferred over premature services or databases.

---

## 5. Route, state, and migration invariants

### Route policy

```text
During migration:
/                 → existing legacy UI
/static/*         → existing legacy assets
/legacy           → optional legacy alias / rollback route
/app-v2/*         → Product V2 preview/application

After an explicit Task 37 release gate:
/                 → approved Product V2 application
/legacy           → retained rollback UI
/app-v2/*         → compatibility route during the rollback window
```

No earlier task may change the default `/` route.

### State ownership

| State | Authoritative owner |
|---|---|
| Source bytes, content hash, model version | Backend model/version service |
| Setup, regions, materials, BCs, loads, assumptions, approvals | Versioned setup aggregate |
| Conversation and messages | Append-only conversation service |
| Pending clarification | Durable clarification record tied to conversation and setup revision |
| Validation and export eligibility | Deterministic backend validator |
| Viewer camera and GPU objects | ViewerController only |
| UI layout, composer draft, drawer state | React UI state |
| Server response cache | Read-only query cache; invalidated after commands |
| Solver job transitions | One durable JobService |
| Results | Versioned ResultBundle tied to one job/model/setup/artifact |

React may hold transient model-bound click evidence, but it becomes engineering truth only through a server command that validates the model version and revision.

### Migration rule

When a persistent repository replaces an in-memory store, the plan must name the cutover transaction, compatibility adapter, rollback boundary, and removal point. The implementation must never dual-write the same setup to both stores.

---

## 6. Common task protocol

Every implementation task:

1. reads `CLAUDE.md`, this plan, relevant approved ADRs, and `PROGRESS_V2.md`;
2. confirms all declared dependencies and the previous task’s evidence;
3. works on a dedicated reviewed task branch;
4. records a brief implementation design before editing;
5. changes only approved scope;
6. adds focused unit/integration/browser tests in the same task;
7. runs focused, affected-phase, and complete regression suites;
8. runs formatting, typing, dependency, secret, migration, and repository-integrity checks;
9. receives independent read-only review;
10. records commands, results, risks, and surprises in `PROGRESS_V2.md`;
11. commits only from a clean worktree and merges through review.

A task is blocked rather than redefined when a dependency, human decision, licensed environment, or safety gate is unavailable.

---

# PHASE 0 — Governance and decision-complete architecture

## Task 16 — Adopt Product V2 governance and freeze the V1 baseline

**Objective.** Adopt this plan, preserve the exact V1 implementation, and establish enforceable Product V2 controls.

**Why it exists.** Product work cannot safely begin while the current governance still mandates only the completed sprint plan and while no immutable baseline or V2 progress record exists.

**Dependencies.** Completed V1 Tasks 1–15; baseline commit `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`; accepted plan review; repository-admin access for protected refs.

**In scope.**
- Add this reviewed plan and `PROGRESS_V2.md`.
- Update `CLAUDE.md` so V2 tasks are governed here while V1 remains frozen.
- Permit the approved `frontend/` build toolchain without changing legacy rules.
- Create an annotated protected `demo-v1` tag at the exact baseline commit.
- Record fixture/evaluation hashes, 318 collected tests, 317 passes, one optional solver skip, commands, versions, and known limitations.
- Establish required CI and independent review checks.

**Out of scope.**
- Product behavior, frontend scaffolding, IR changes, geometry, agents, exports, or solver execution.

**Architecture and contracts.**
- `demo-v1` identifies historical V1 code; it is never moved.
- V1 routes and payloads remain additive-only compatibility contracts.
- Product task branches merge only through protected review.

**Prohibited shortcuts.**
- No moving tag, history rewrite, fixture edit, result rebaseline, direct protected-branch push, or claim that 317 is the collected count.

**Tests.**
- Run environment gate, full Python suite, REPLAY evaluation, legacy JavaScript syntax, fixture/hash verification, `git archive` reproduction, and `git diff --check`.

**Definition of Done.**
- The exact V1 baseline is identifiable and protected; V2 governance is authoritative; baseline evidence is reproducible; no product behavior changed.

**Required evidence.**
- Tag SHA, remote protections, CI links, hashes, full test summary, independent review, and clean worktree.

**Documentation updates.**
- `CLAUDE.md`, `PRODUCT_V2_PLAN.md`, `PROGRESS_V2.md`, and a V1 baseline record under `docs/product/`.

## Task 17 — Complete Phase-1 architecture, ownership, routing, migration, and safety decisions

**Objective.** Make every architectural decision required by Tasks 19–37 before implementation agents encounter it.

**Why it exists.** The previous draft put persistence, safety, API, and routing decisions inside implementation tasks and requested detailed future-domain documents before their audits.

**Dependencies.** Task 16; accepted completed Task 18 audit evidence; named frontend, backend, security, and release reviewers.

**In scope.**
- Approve product requirements, modular-monolith boundaries, state ownership, route/cutover policy, API/client generation, schema versioning, runtime modes, persistence migration, dependency locking, upload containment, and capability gates.
- Decide Node/package manager, Python lock tooling, container baseline, database/ORM/migrations, object-store abstraction, auth boundary, and CI ownership.
- Define no-dual-write migration and rollback rules.
- Record that detailed Geometry V2 and solver-runner designs follow their own audits.

**Out of scope.**
- Application code, full database schema, detailed B-rep design, detailed runner protocol, or UI implementation.

**Architecture and contracts.**
- Approve ADRs for routing, generated API types, state ownership, runtime modes, versioning/migrations, persistence cutover, and unsafe capability blocking.
- Define `/`, `/legacy`, and `/app-v2` behavior exactly.

**Prohibited shortcuts.**
- No unresolved gating “TBD”, frontend-owned setup, production REPLAY, second engineering pipeline, or design fiction about unavailable services.

**Tests.**
- Review dependency graph, state-writer matrix, API reuse matrix, route matrix, capability gates, migration/rollback sequence, threat cases, and all document links; run the unchanged full regression suite.

**Definition of Done.**
- Task 19 can execute without a major architecture decision; every Phase-1 state has one writer; all unsafe capabilities have an explicit gate.

**Required evidence.**
- Approved ADRs, ownership table, API matrix, route/deployment diagram, risk register, security review, test summary, and independent review.

**Documentation updates.**
- Phase-1 product requirements, architecture, domain/state model, frontend migration, API contract, security/threat model, risk register, ADRs, this plan, and `PROGRESS_V2.md`.

## Task 18 — Repository-grounded frontend and API audit — COMPLETED PLANNING EVIDENCE

**Objective.** Preserve the accepted read-only audit as the factual baseline for subsequent tasks.

**Why it exists.** Future implementation must not rediscover or contradict the current frontend lifecycle, endpoint contracts, state risks, test limits, or dependency gaps.

**Dependencies.** Completed V1 repository at the audited commit. This is already-completed evidence, not a future implementation dependency.

**In scope.**
- Record accepted findings about static files, Three.js reuse, DOM coupling, endpoints, errors, persistence, tests, packaging, unsafe mapping, and plan ordering.

**Out of scope.**
- Re-running a generic audit, editing application code, or designing later geometry/solver internals.

**Architecture and contracts.**
- The audit is an input to Task 17 and the reuse/capability matrix for Tasks 19–37.

**Prohibited shortcuts.**
- No replacing audit facts with assumptions or claiming that current ephemeral sessions are conversations.

**Tests.**
- Read-only Git state, live remote comparison, 318-node collection without caches, endpoint/source inspection, and independent frontend/backend/plan reviews were completed.

**Definition of Done.**
- The audit is accepted as planning evidence and is not rescheduled.

**Required evidence.**
- Audit report, repository SHA, route/schema matrix, test inventory, and review conclusions.

**Documentation updates.**
- Record the accepted audit summary in Task 16/17 documentation and `PROGRESS_V2.md`; no application documentation is created by Task 18 itself.

---

# PHASE 1 — V2.1 chat-first preview UI

## Task 19 — Reproducible frontend foundation, additive serving, and typed transport

**Objective.** Create a strict React/TypeScript/Vite foundation and typed API transport without changing engineering behavior.

**Why it exists.** The repository has no frontend package, lockfile, type system, build, browser tests, CI, V2 route, or reliable generated client.

**Dependencies.** Tasks 16 and 17; approved runtime/tool versions, route ADR, OpenAPI strategy, and build packaging policy.

**In scope.**
- Create `frontend/` with pinned React, TypeScript, Vite, Three.js 0.180.0, lint, formatting, Vitest, Testing Library, and Playwright foundations.
- Commit a lockfile and runtime version declaration.
- Add `/app-v2` production serving and `/legacy` alias while preserving `/`.
- Configure Vite base path and same-origin development proxy, including SSE.
- Add precise response metadata needed for OpenAPI without changing endpoint semantics.
- Generate client types, add drift checks, normalize all current error envelopes, and provide typed multipart, glTF, SSE, and binary-export transports.
- Add frontend CI.

**Out of scope.**
- Chat shell, Three.js port, real workflow, setup editing, persistence, backend business rules, or default-route change.

**Architecture and contracts.**
- Generated backend types are the TypeScript domain boundary.
- Handwritten code owns transport only.
- Missing V2 assets fail honestly and never serve mock or legacy content under `/app-v2`.
- The global legacy `/events` transport is isolated as compatibility code and is not consumed by `/app-v2`; the scoped V2 update contract arrives in Task 30.

**Prohibited shortcuts.**
- No `any` API layer, copied `SimulationIntent`, broad CORS workaround, committed secrets, production mock/replay, generated bundles in `app/static`, or replacement of `/`.

**Tests.**
- Clean lockfile install; format, lint, strict typecheck, unit, build, OpenAPI drift, multipart, SSE, binary download, every error envelope, abort handling, deep-link, missing-build, and Playwright route smoke tests; full Python regression.

**Definition of Done.**
- `/` remains legacy; `/legacy` works; `/app-v2` loads independently; client generation is reproducible; CI passes; engineering semantics are unchanged.

**Required evidence.**
- Version/lock output, build manifest, schema drift output, CI links, route responses, browser trace, bundle mock scan, full regression, and independent review.

**Documentation updates.**
- Frontend development/build/deployment, generated-client procedure, route behavior, and `PROGRESS_V2.md`.

## Task 20 — Chat-first visual shell with honest states

**Objective.** Build the conversation-first layout and component system without pretending unsupported backend capabilities exist.

**Why it exists.** The desired interaction model needs an accessible timeline, composer, engineering workspace, and drawer before real integration, but production mocks would create a false product and parallel domain model.

**Dependencies.** Task 19; approved UX/accessibility direction and fixture-exclusion policy.

**In scope.**
- Workspace/model header presentation, conversation timeline, fixed composer, resizable viewer placeholder, setup drawer, responsive desktop behavior, keyboard/focus foundations, and empty/loading/offline/error states. The shell does not define Project or ModelVersion domain identity before Task 26.
- Define a discriminated presentation-block union for text, region, BC, load, material, clarification, validation, solver, job, and result cards.
- Keep all populated fixtures under test/story-only entry points.

**Out of scope.**
- API submission, Three/WebGL, persistent messages, setup mutation, localStorage domain state, active material/solver/job/result behavior, or production demo data.

**Architecture and contracts.**
- Cards are immutable projections with callbacks, not engineering entities.
- Production routes render only honest empty/unavailable states until integration.
- Layout state is React-owned; no setup copy exists in React.

**Prohibited shortcuts.**
- No production fixture imports, localStorage transcript, fake success/job/results, copied IR validation, or editable card-owned domain state.

**Tests.**
- Exhaustive block-render switch, production-bundle fixture exclusion, composer keyboard behavior, resize/drawer persistence within one mount, responsive layouts, focus order, accessibility checks, visual regression, and full Task 19/Python suites.

**Definition of Done.**
- The interface is visibly conversation-first; unsupported functions are honest; no fixture data ships; primary layouts are accessible and visually covered.

**Required evidence.**
- Screenshots/traces, accessibility report, keyboard checklist, bundle scan, component/visual results, and independent review.

**Documentation updates.**
- UI architecture, presentation-block contract, fixture policy, accessibility baseline, and `PROGRESS_V2.md`.

## Task 21A — Lifecycle-safe ViewerController parity port

**Objective.** Port existing viewer parity behind an imperative controller that React can construct and dispose safely.

**Why it exists.** `app.js` is eager, DOM-bound, and lifecycle-unsafe, but its tested face naming, framing, picking, and visual concepts are valuable.

**Dependencies.** Tasks 19 and 20; accepted ViewerController interface; Three.js version pinned to the V1 baseline.

**In scope.**
- glTF loading through `face_{id}` nodes; orbit, pan, zoom, axes, resize, camera framing, face picking, click/orbit threshold, load-generation token, caller-supplied opaque model binding, selection callback, unload, and idempotent disposal.
- React `ThreeViewport` wrapper with one controller per mounted viewport.

**Out of scope.**
- API/business calls, SSE ownership, advanced overlays, focus/isolate/hide, camera persistence, chat integration, or setup decisions.

**Architecture and contracts.**
- The controller owns canvases, renderers, scene, controls, listeners, animation frame, GPU resources, and face map.
- It emits plain `{modelBinding, entityType, entityId}` events and never mutates DOM outside owned canvases. `modelBinding` is an opaque value supplied by the host; ViewerController does not define project or ModelVersion identity before Task 26.

**Prohibited shortcuts.**
- No import of legacy `app.js`, Three objects in React/global state, stale-load completion, fixture face IDs, or missing dispose path.

**Tests.**
- Unit tests for face-name resolution, framing, selection thresholds, load-generation guards, and double-dispose; browser tests for bracket/plate load, pick, orbit, resize, repeated mount/unmount, WebGL error state, and resource/listener cleanup; preserve server glTF regressions.

**Definition of Done.**
- V1 viewer parity works in `/app-v2`; every controller resource has a tested owner and cleanup path; selection events carry the exact caller-supplied model binding.

**Required evidence.**
- Browser traces, lifecycle/resource counters, selected event payloads, no console/WebGL errors, full frontend/Python regression, and independent review.

**Documentation updates.**
- ViewerController interface/lifecycle, reuse-versus-port decisions, resource ownership, and `PROGRESS_V2.md`.

## Task 21B — Declarative viewer layers and engineering-workspace enhancements

**Objective.** Add deterministic visual layering and the bounded viewer enhancements required by later workflows.

**Why it exists.** V1 last-write material replacement leaves stale candidates, loses BC/load meaning, and cannot reconstruct visuals reliably.

**Dependencies.** Task 21A; approved visual-layer vocabulary and precedence.

**In scope.**
- Declarative base, confirmed, proposed, rejected, candidate, selected, BC, load, and hover layers keyed by source/revision.
- Clear-by-layer/source, focus region, isolate/hide/show, reset camera, camera capture, stale-command rejection, and model-switch cleanup.
- Two-tab/two-model controller isolation.

**Out of scope.**
- Authoritative setup state, global unscoped SSE, result contours, annotation grounding, or business validation.

**Architecture and contracts.**
- `setVisualState(snapshot)` reconciles plain data into render state.
- Persistent layers derive from versioned server snapshots later; transient layers remain separately removable.

**Prohibited shortcuts.**
- No arbitrary imperative color command as truth, cross-model command application, hidden overlay precedence, or controller-owned API/session state.

**Tests.**
- Layer precedence and clearing; BC plus region co-display; load arrow/vector; rejected/candidate cleanup; focus/isolate/hide/reset/camera; stale revision/model rejection; repeated model swaps; two simultaneous viewports; browser visual regressions and leak checks.

**Definition of Done.**
- Visual state is deterministic and reconstructable; enhancements are lifecycle-safe; simultaneous models cannot affect each other.

**Required evidence.**
- Visual matrix/screenshots, two-tab trace, lifecycle measurements, precedence contract results, and independent review.

**Documentation updates.**
- Visual-layer contract, precedence, camera-state schema, performance budget, and `PROGRESS_V2.md`.

## Task 22 — V2.1 preview release gate

**Objective.** Release the first visible chat-first preview without implying a production conversation or engineering workflow.

**Why it exists.** UI work should be visible early, but real workflow integration must wait for safety, state, revision, and mapping dependencies.

**Dependencies.** Tasks 19, 20, 21A, and 21B; preview release approval.

**In scope.**
- Integrate the shell and viewer lifecycle with an honest no-model production state and test-only viewer fixtures. An operator may enable current V1 upload/view compatibility only in an explicitly non-production preview mode, where the model session is labeled ephemeral.
- Verify `/`, `/legacy`, and `/app-v2`; publish preview limitations and rollback.

**Out of scope.**
- Public/arbitrary V2 upload before Tasks 25–26, real conversation, persistent setup, material approval, solver artifact generation, automatic execution, default-route change, or production readiness claim.

**Architecture and contracts.**
- V2.1 uses no production mock data and does not create V2 conversation or setup truth. Optional non-production V1 compatibility state remains backend-owned, ephemeral, and visibly non-persistent.
- Legacy remains the default and complete controlled demo.

**Prohibited shortcuts.**
- No “beta production” label, public uncontained upload, hidden fallback, fake cards, route flip, or exposure of unsafe export.

**Tests.**
- Complete frontend CI, browser/WebGL smoke, two-tab/two-model isolation, stale request cancellation, legacy compatibility, missing-build, rollback, accessibility, and full Python regression.

**Definition of Done.**
- V2.1 is an honest preview with safe viewer foundation; legacy remains default; limitations and rollback are explicit.

**Required evidence.**
- Release checklist, browser matrix, performance/accessibility results, rollback rehearsal, CI links, and human sign-off.

**Documentation updates.**
- V2.1 release notes, preview limitations, rollback instructions, and `PROGRESS_V2.md`.

---

# PHASE 2 — V2.2 trustworthy state, safety, and real workflow

## Task 23 — Locked backend environment, reproducible container, and runtime-mode separation

**Objective.** Make backend execution reproducible and physically separate production, LIVE evaluation, REPLAY, and test modes.

**Why it exists.** Current requirements are unpinned and replay endpoints are always mounted, contradicting dependency and no-fallback rules.

**Dependencies.** Tasks 16 and 17; approved Python lock/container and runtime-mode ADRs.

**In scope.**
- Add pinned direct/transitive Python dependencies and reproducible backend container with required Gmsh system libraries.
- Define explicit application modes and configuration validation.
- Exclude fallback/replay routes and fixtures from production mode.
- Add dependency, secret, SBOM/license, and container smoke checks.

**Out of scope.**
- Database, upload isolation, frontend features, geometry changes, or solver execution.

**Architecture and contracts.**
- One settings object selects an immutable mode at startup.
- Production startup fails on test/replay configuration or missing required secrets.

**Prohibited shortcuts.**
- No floating dependencies, production route flag hidden in UI, runtime fallback to replay, or secrets in files/images.

**Tests.**
- Clean locked install/container build, production route absence, test/replay route presence only in approved modes, configuration failure, dependency drift, container environment gate, and full regression.

**Definition of Done.**
- Backend builds reproducibly; production cannot access REPLAY/fallback paths; modes are visible and tested.

**Required evidence.**
- Lock and image digest, SBOM/license report, route matrices, clean build logs, regression, and security review.

**Documentation updates.**
- Dependency/update policy, container runbook, runtime-mode matrix, and `PROGRESS_V2.md`.

## Task 24 — API and SimulationIntent versioning and migration framework

**Objective.** Introduce explicit schema versions and deterministic migrations before changing setup semantics.

**Why it exists.** Materials, coordinates, revisions, identities, and future storage will break unversioned examples, eval cases, replay records, artifacts, and clients.

**Dependencies.** Tasks 17, 19, and 23; approved versioning ADR.

**In scope.**
- Version API contracts and `SimulationIntent`.
- Add migration registry, compatibility policy, OpenAPI version metadata, generated-client drift gate, and fixture/artifact migration harness.
- Cover examples, eval cases, fallback/replay records, manifests, and future stored rows.

**Out of scope.**
- New material/coordinate fields, database persistence, or UI behavior.

**Architecture and contracts.**
- Reads validate declared versions and migrate through explicit sequential transforms.
- Writes emit only the current version.
- No silent field default may convert an unsafe old setup into approved state.

**Prohibited shortcuts.**
- No shape guessing, in-place historical fixture rewrite without migration evidence, unversioned stored JSON, or client-maintained migrations.

**Tests.**
- Golden v1→current migrations, idempotency, unsupported-future version, malformed/partial payload, replay/eval/example migration, OpenAPI/client drift, and round-trip regression.

**Definition of Done.**
- Every persisted or checked-in setup payload has a declared version and deterministic migration or explicit unsupported outcome.

**Required evidence.**
- Migration matrix, golden hashes, compatibility report, generated-client check, full regression, and independent review.

**Documentation updates.**
- API/versioning policy, `SimulationIntent` migration guide, deprecation policy, and `PROGRESS_V2.md`.

## Task 25 — Upload limits, parser containment, and Gmsh concurrency isolation

**Objective.** Treat model files as untrusted and prevent web-process/parser failures from affecting other users.

**Why it exists.** The current server reads the whole request body and invokes Gmsh/mesh parsers without product-grade size, time, memory, concurrency, or isolation controls.

**Dependencies.** Tasks 17, 23, and 24; approved threat model and resource budgets.

**In scope.**
- Streaming size enforcement, filename/type/content checks, safe temporary directories, parser timeout/memory/CPU limits, no-network parser execution, cleanup, and structured failure codes.
- Serialize or isolate Gmsh global-state work so concurrent calls cannot overlap unsafely.
- Add malicious/truncated/oversized and concurrency/failure-injection tests.

**Out of scope.**
- General CAD repair, durable background jobs, object storage, or geometry V2.

**Architecture and contracts.**
- Web handlers submit to a bounded parser execution boundary and receive typed results/errors.
- Parser crashes/timeouts do not crash or poison the web process.

**Prohibited shortcuts.**
- No unbounded `request.body()`, shared unsafe Gmsh session, shell command construction, broad writable directory, or leaking local paths.

**Tests.**
- Boundary-size uploads, malformed multipart, spoofed extension, zip/path abuse where applicable, parser crash/timeout, concurrent uploads, Gmsh exclusivity, cleanup, restart after failure, and full regression.

**Definition of Done.**
- Untrusted inputs are bounded and isolated; concurrent uploads fail or queue deterministically without corrupting state.

**Required evidence.**
- Threat cases, resource measurements, concurrency logs, cleanup verification, security review, and regression.

**Documentation updates.**
- Upload contract, limits, parser sandbox model, operational settings, and `PROGRESS_V2.md`.

## Task 26 — Project and immutable model-version identity with object storage

**Objective.** Establish minimal persistent identity and storage before setup revisions or artifact lineage depend on them.

**Why it exists.** Current model IDs hash filename plus bytes; same bytes under different names do not deduplicate, and the model ID is also used as a session ID.

**Dependencies.** Tasks 17, 23, 24, and 25; approved database/object-store choices.

**In scope.**
- Minimal Workspace, Project, Model, and ModelVersion identities.
- Separate source content hash, original filename metadata, and immutable model-version ID.
- Local-development and S3-compatible object-store abstraction.
- Exact source bytes, parser/inventory version metadata, inventory/glTF cache lineage, authorized paths, and legacy model-record migration/adapter.

**Out of scope.**
- Setup, conversation, solver job/result tables, auth provider, or model-change remapping.

**Architecture and contracts.**
- SHA-256 of exact bytes identifies and deduplicates the blob only. Every intentional create-version command produces a new immutable ModelVersion domain ID; an idempotent retry of that same command returns the existing ModelVersion.
- Filename is metadata and never changes the content hash. Same bytes uploaded under different filenames may create distinct ModelVersions that reference the same blob.
- Every viewer/inventory request names an immutable model version.
- One object-store writer owns source/generated model artifacts.

**Prohibited shortcuts.**
- No reuse of content hash as all domain IDs, filename-dependent dedup ambiguity, filesystem/database dual write, or mutable source bytes.

**Tests.**
- Same bytes/same filename, same bytes/different filename, changed bytes, concurrent creation, restart/multi-worker access, authorization placeholder boundary, cache versioning, legacy migration, and object-store failure.

**Definition of Done.**
- Model identity semantics are explicit; exact bytes and metadata are durable; every selection can bind to one model version.

**Required evidence.**
- Identity examples, storage/migration tests, restart/multi-worker results, hashes, failure report, and independent review.

**Documentation updates.**
- Identity ADR, object-storage contract, legacy migration, retention baseline, and `PROGRESS_V2.md`.

## Task 27 — Persistent setup aggregate, immutable revisions, approvals, and concurrency

**Objective.** Replace model-keyed process-memory setup state with one durable versioned setup writer.

**Why it exists.** Current sessions disappear on restart, collide for identical uploaded models, and allow non-atomic read/merge/save behavior.

**Dependencies.** Tasks 23, 24, and 26; approved minimal persistence/domain model.

**In scope.**
- SimulationSetup, SetupRevision, Approval, and AuditEvent only.
- Immutable revision payload/hash, actor/time/reason, optimistic concurrency/ETag, idempotency keys, stale-write detection, restart recovery, and multi-worker consistency.
- Compatibility facade/cutover for current session commands without dual write.

**Out of scope.**
- Conversation/messages, material/coordinate redesign, solver jobs/results, or all future database entities.

**Architecture and contracts.**
- One transaction creates each successor revision and audit event.
- Approval binds to one exact revision and is invalidated by a successor.
- During cutover, a record is owned by either the legacy compatibility store or persistent repository, never both.

**Prohibited shortcuts.**
- No mutable current-row truth without revision history, last-write-wins, client-supplied validation status, dual write, or accepted approval copied to changed content.

**Tests.**
- Restart, multiple workers, two users/setups for the same model version, concurrent stale writes, idempotent retry, approval invalidation, compatibility cutover/rollback, transaction failure, and full regression.

**Definition of Done.**
- Setup state survives restart, detects stale mutations, preserves immutable history, and has one authoritative writer.

**Required evidence.**
- Migration/cutover log, concurrency traces, database migrations, revision/hash examples, rollback rehearsal, and independent review.

**Documentation updates.**
- Setup aggregate/revision contract, concurrency policy, repository cutover runbook, and `PROGRESS_V2.md`.

## Task 28 — Persistent conversations, messages, and safe clarification lifecycle

**Objective.** Separate append-only conversation history from authoritative setup revisions and make clarification durable and retry-safe.

**Why it exists.** The current pending map is unhydrated process memory, can be overwritten, and is popped before candidate validation.

**Dependencies.** Tasks 24, 26, and 27; approved conversation/setup separation.

**In scope.**
- Conversation, Message, and PendingClarification records.
- Stable turn/clarification IDs, model/setup revision binding, append-only messages, consume-on-success, hydration, cancel/expire, idempotent answer, and direct-click evidence command.
- One pending-clarification policy per turn with explicit concurrent-turn behavior.

**Out of scope.**
- Geometry V2 retrieval, bounded agent, frontend integration, or approval through chat text.

**Architecture and contracts.**
- Messages describe intent and outcomes; only typed commands create setup revisions.
- Clarification answers validate returned candidate/evidence plus model version and base revision before consumption.

**Prohibited shortcuts.**
- No frontend-only durable transcript, pop-before-validate, arbitrary click acceptance, message mutation, or chat-based approval.

**Tests.**
- Restart/hydration, invalid then valid answer, stale revision, wrong model, direct click, candidate-set answer, cancel/expiry, duplicate retry, concurrent turns, provider failure preservation, and multi-worker behavior.

**Definition of Done.**
- Conversation and clarification survive restart; invalid/retried answers cannot lose or corrupt state; setup remains separately authoritative.

**Required evidence.**
- State diagrams, restart/concurrency tests, example traces, failure taxonomy, and independent review.

**Documentation updates.**
- Conversation/clarification API, lifecycle diagram, retention policy, and `PROGRESS_V2.md`.

## Task 29 — Authentication, authorization, and workspace isolation

**Objective.** Enforce tenant and actor boundaries before persistent projects and conversations become a production workflow.

**Why it exists.** Durable IDs and object paths are unsafe if guessed IDs can expose another workspace or runner.

**Dependencies.** Tasks 17, 23, 26, 27, and 28; approved authentication provider and role policy.

**In scope.**
- Authentication abstraction, Workspace membership/roles, server-side authorization for projects, model versions, setups, conversations, downloads, and audit events.
- Test/service principals for automation and explicit actor propagation.

**Out of scope.**
- Runner enrollment, external customer administration portal, billing, or solver permissions.

**Architecture and contracts.**
- Authorization is enforced in service/repository boundaries, not only routes.
- Every write/audit event carries authenticated actor/workspace.

**Prohibited shortcuts.**
- No client-only checks, insecure development bypass in production, guessed-ID access, shared object URLs, or actor supplied by request body.

**Tests.**
- Cross-tenant read/write/download denial, role matrix, revoked membership, service principal, audit actor, object-store authorization, and multi-worker behavior.

**Definition of Done.**
- A user can access only authorized workspace resources; all sensitive writes/downloads are server-enforced and audited.

**Required evidence.**
- Authorization matrix, denial tests, threat review, audit examples, and independent security review.

**Documentation updates.**
- Auth/authorization architecture, role matrix, development identity policy, and `PROGRESS_V2.md`.

## Task 30 — Versioned setup read model and scoped selection/highlight transport

**Objective.** Provide one coherent server snapshot for cards, inspector, and viewer, plus model/session-scoped transient updates.

**Why it exists.** Current audit and intent reads can tear, omit fields, and coexist with unscoped global SSE and ambiguous click meanings.

**Dependencies.** Tasks 24, 26, 27, 28, and 29; ViewerController layer contract from Task 21B.

**In scope.**
- Composite SetupView with project/model version, setup revision, full setup, validation, eligibility, pending clarification summary, and visual-layer projection.
- Revision/ETag and scoped event stream carrying model/setup/revision identifiers and reconnect/invalidation semantics.
- Model-bound selection evidence schema; retain legacy `/select`, `/highlight`, and `/events` only for compatibility.

**Out of scope.**
- Setup edits, material/coordinate redesign, result fields, or making SSE authoritative.

**Architecture and contracts.**
- SetupView is authoritative read projection.
- SSE/WebSocket is an invalidation/progress transport; clients refetch by revision and never reconstruct truth from missed events.

**Prohibited shortcuts.**
- No global browser event bus, partial dual GET as one snapshot, unversioned click, or frontend-derived validation/visual truth.

**Tests.**
- Snapshot consistency under concurrent mutation, ETag/revision, reconnect/missed event, wrong model/workspace, two-tab/two-model isolation, stale event, legacy compatibility, and viewer-layer reconstruction.

**Definition of Done.**
- Cards, inspector, and viewer can hydrate coherently from one revision; updates cannot cross model/workspace boundaries.

**Required evidence.**
- OpenAPI schema, revision/event traces, two-tab browser test, concurrency results, and independent review.

**Documentation updates.**
- SetupView, event/selection contract, legacy compatibility/deprecation, and `PROGRESS_V2.md`.

## Task 31 — Explicit material ownership, assignment, and approval

**Objective.** Replace implicit demonstration steel with explicit versioned material state and approval.

**Why it exists.** Current orchestration inserts steel and explicit material instructions are rejected, conflicting with Product V2’s material and gravity safety requirements.

**Dependencies.** Tasks 24, 27, and 30; approved material/domain migration.

**In scope.**
- Stable material ID, constitutive type, values with source units, canonical values, density, source, body/region assignment, status, provenance, revision, and approval.
- Typed material create/edit/assign/approve/reject commands.
- Remove implicit material from V2 paths; gravity requires approved density.
- Migrate V1 examples/eval/replay through explicit unapproved/default-review state.

**Out of scope.**
- Nonlinear materials, broad material library, agent material approval, or multiple-section assembly support.

**Architecture and contracts.**
- Server semantics owns conversion; approval binds to material and setup revision; adapters consume approved assigned materials only.

**Prohibited shortcuts.**
- No silent steel insertion, client-side conversion, export after rejection/incomplete assignment, or approval inherited after value change.

**Tests.**
- Units, source/provenance, assignment, gravity density, rejection/supersession, stale edit, migration, Abaqus/CalculiX preflight blocking, and full regression.

**Definition of Done.**
- Every V2 material is explicit, assigned, versioned, and approved before export/execution.

**Required evidence.**
- Schema/migration examples, command/API tests, gravity/export gates, adapter preflight report, and independent review.

**Documentation updates.**
- Material domain/API, unit conventions, migration guide, and `PROGRESS_V2.md`.

## Task 32 — Explicit coordinate systems and directional semantics

**Objective.** Bind every directional BC/load and qualitative direction to a visible coordinate system.

**Why it exists.** V1 geometry labels and “vertical/downward” semantics can use hidden or inconsistent axis conventions.

**Dependencies.** Tasks 24, 27, and 30; approved coordinate-system model.

**In scope.**
- Model/world, named Cartesian and cylindrical systems; feature-local axes when deterministically proven; condition-level references; typed create/select commands; vector preview; unresolved qualitative assumptions requiring approval.
- Migrate old directions into explicit pending-review coordinate assumptions.

**Out of scope.**
- Arbitrary curvilinear systems, assembly instance transforms, or agent approval.

**Architecture and contracts.**
- Stored canonical vector plus coordinate-system reference and source wording.
- Server transforms/conversions; viewer only displays returned axes/vectors.

**Prohibited shortcuts.**
- No hidden global Y/Z convention, client-side transform as truth, silent radial/axial choice, or approved status carried across coordinate change.

**Tests.**
- Rigid rotations, Cartesian/cylindrical transforms, ambiguous qualitative directions, stale revision, migration, viewer preview, adapter mapping, and full regression.

**Definition of Done.**
- Every directional condition is coordinate-explicit; ambiguous terms block until reviewed; rotation tests are consistent.

**Required evidence.**
- Schema/transform examples, rotation matrix tests, UI vector screenshots, migration report, and independent review.

**Documentation updates.**
- Coordinate-system model/API, semantics policy, migration guide, and `PROGRESS_V2.md`.

## Task 33 — Fail-closed CAD-to-solver mapping evidence contract

**Objective.** Remove positional STEP-to-Abaqus face mapping from every Product V2 capability.

**Why it exists.** Matching OCC/Gmsh tag `n` to Abaqus `faces[n-1]` can silently apply loads or BCs to the wrong face.

**Dependencies.** Tasks 24, 26, 27, and 30; accepted mapping threat model.

**In scope.**
- MappingEvidence schema with source/target model versions, method, entity pairs/sets, hashes, verification results, confidence category, limitations, and approval/reconfirmation state.
- Detect and block positional mappings.
- Capability-gate STEP-to-Abaqus export until controlled named sets or verified geometric matching exists.
- Keep trustworthy INP/set paths when preflight proves identity.

**Out of scope.**
- Controlled meshing implementation or geometric matcher; those begin in Task 53.

**Architecture and contracts.**
- Export/preflight requires accepted MappingEvidence for every referenced region.
- Absence or contradiction is `unsupported`/`insufficient_evidence`, never a warning-only success.

**Prohibited shortcuts.**
- No face-count proof, tag/index arithmetic, manual unchecked mapping, or UI override that bypasses evidence.

**Tests.**
- Intentionally permuted solver face order, equal face count/wrong order, changed CAD bytes, missing/partial evidence, trusted INP sets, stale evidence, and adapter capability responses.

**Definition of Done.**
- No V2 artifact claims verified STEP face placement without explicit mapping evidence; the current unsafe path is blocked.

**Required evidence.**
- Source scan, permuted-order regression, capability matrix, blocked endpoint/UI behavior, and independent safety review.

**Documentation updates.**
- Mapping evidence contract, blocked capabilities, legacy warning/deprecation, and `PROGRESS_V2.md`.

## Task 34 — Granular versioned setup correction commands

**Objective.** Support engineer corrections without whole-document PUTs or copied frontend business rules.

**Why it exists.** Current APIs cannot safely replace a region, change magnitude/units/vector/type, or remove a rejected condition under concurrency.

**Dependencies.** Tasks 27, 30, 31, 32, and 33; approved command and concurrency conventions.

**In scope.**
- Typed commands for region replace/add/remove membership, BC components, load magnitude/value+unit/vector/type, coordinate reference, material assignment, condition removal, assumption decision, and reopen/supersede.
- Base revision and idempotency key on every mutation; server conversion, validation, audit event, and successor revision.

**Out of scope.**
- Geometry V2 patch growth, agent-generated edits, solver execution, or client whole-IR editing.

**Architecture and contracts.**
- Commands express user intent; aggregate methods own invariants and emit one successor revision.
- Chat may request a command but cannot mutate by text alone.

**Prohibited shortcuts.**
- No frontend-generated canonical IR, whole-document overwrite, last-write-wins, client unit conversion, mutation without audit, or removal of historical evidence.

**Tests.**
- Every command success/failure, stale revision, idempotent retry, concurrent edits, rejected-region correction, unit/type conversion, approval invalidation, validation recomputation, and migration compatibility.

**Definition of Done.**
- Required corrections are possible through narrow server commands; stale or unsafe edits fail without changing state.

**Required evidence.**
- OpenAPI command schemas, revision/audit traces, concurrency tests, UI-neutral examples, and independent review.

**Documentation updates.**
- Command API, aggregate invariants, error codes, correction workflow, and `PROGRESS_V2.md`.

## Task 35 — Artifact manifest and setup/model lineage

**Objective.** Bind generated artifacts to existing project, model version, setup revision, mappings, adapters, and environment.

**Why it exists.** Artifact lineage must use real identities created in earlier tasks, not placeholders or future database entities.

**Dependencies.** Tasks 23, 24, 26, 27, 31, 32, 33, and 34.

**In scope.**
- Manifest with project/model-version/source hash/setup-revision/material/coordinate/mapping hashes, adapter name/version, solver capability range, artifact hashes, generation environment, warnings, and unsupported report.
- Persist immutable artifact records and deterministic regeneration evidence.

**Out of scope.**
- Solver execution, job state, result lineage, or new mapping implementation.

**Architecture and contracts.**
- Artifact generation consumes one approved setup revision and accepted mapping evidence.
- Artifact bytes and manifest are immutable and content-hashed.

**Prohibited shortcuts.**
- No current-state lookup during regeneration, missing identity placeholder, absolute path/timestamp nondeterminism, or artifact without preflight.

**Tests.**
- Byte-repeat generation, changed revision/model/mapping, missing approval, source mismatch, legacy migration, clean checkout/container, and manifest schema validation.

**Definition of Done.**
- Every V2 artifact is reproducible and traceable to exact approved inputs; unsupported capability fails before bytes are emitted.

**Required evidence.**
- Example manifests, deterministic hashes, clean-container regeneration, failure cases, and independent review.

**Documentation updates.**
- Artifact/manifest schema, regeneration procedure, retention policy, and `PROGRESS_V2.md`.

## Task 36A — Real persistent conversation and engineering review integration

**Objective.** Connect the chat-first V2 UI to durable conversations and the authoritative review workflow.

**Why it exists.** Only after Tasks 23–34 can “real conversation” avoid process-memory loss, parallel truth, unsafe defaults, unsafe mapping exposure, and unscoped interaction state.

**Dependencies.** Tasks 19–21B and 23–34.

**In scope.**
- Project/model selection, persistent message timeline, instruction submission, provider/tool errors, proposal and clarification cards, scoped direct-click evidence, confirm/reject/assumption/material decisions, and versioned SetupView hydration.
- Preserve click/clarification state across recoverable provider failures; production never offers REPLAY.

**Out of scope.**
- Editable setup drawer, artifact generation/download, Geometry V2, bounded agent, automatic solver execution, result cards, or unsafe STEP-to-Abaqus export.

**Architecture and contracts.**
- Query cache projects server state; mutations carry revision/idempotency; ViewerController consumes the server visual projection; messages reference exact setup revisions.
- Unsupported capability cards are explicit and non-actionable.

**Prohibited shortcuts.**
- No ephemeral “real” transcript, optimistic approval, local IR edit, fallback substitution, global event use, or legacy unsafe endpoint exposure.

**Tests.**
- Persistent upload→instruction→clarify/direct-click→confirm/reject; restart/multi-worker; two users/same model; stale requests; two-tab events; provider failure/recovery; production replay exclusion; browser/WebGL; and full regression.

**Definition of Done.**
- Conversation and engineering review work in V2 with durable history and one authoritative setup; no edit/export capability is implied.

**Required evidence.**
- End-to-end traces, revision/audit lineage, restart/concurrency results, browser screenshots, CI/performance/accessibility results, and independent review.

**Documentation updates.**
- Persistent V2 conversation/review workflow, error/recovery, capability limits, and `PROGRESS_V2.md`.

## Task 36B — Structured setup inspector and command-backed corrections

**Objective.** Render one coherent setup drawer and expose granular corrections only through versioned backend commands.

**Why it exists.** A useful review workflow needs regions, BCs, loads, materials, coordinates, assumptions, and validation in one projection without making React a second SimulationIntent writer.

**Dependencies.** Tasks 30–34 and 36A.

**In scope.**
- Read-only inspector sections for regions, loads, BCs, materials, coordinates, assumptions, approvals, validation, and eligibility.
- Task 34 correction commands for region membership/replacement, load/BC values, units/vectors/types, coordinate references, material assignment, assumption decisions, and removal/supersession.
- Explicit stale-revision/conflict UI, refetch/reconcile behavior, focus/highlight links, and revision diffs.

**Out of scope.**
- Whole-document editing, patch growth, artifact generation, solver/job/result UI, or client-owned validation.

**Architecture and contracts.**
- SetupView is the only drawer read model. Commands carry expected revision and idempotency key and create backend revisions; the query cache invalidates/refetches after acknowledged writes.

**Prohibited shortcuts.**
- No canonical setup copy in React, whole-IR PUT, local unit/coordinate conversion as truth, last-write-wins, optimistic success, or card-owned engineering state.

**Tests.**
- Every supported inspector section and correction; 409/412 conflict and reconciliation; stale model/revision; idempotent retry; approval invalidation; focus/highlight binding; two-tab concurrent edits; accessibility; browser/WebGL; and full regression.

**Definition of Done.**
- Users can inspect and safely correct the supported setup without JSON/code edits; every accepted change is a traceable backend revision and stale writes preserve prior state.

**Required evidence.**
- Browser traces/screenshots, command/revision diffs, conflict traces, accessibility report, no-client-authority review, and independent review.

**Documentation updates.**
- Setup inspector information architecture, correction UX/API mapping, conflict/recovery behavior, and `PROGRESS_V2.md`.

## Task 36C — Capability-gated artifact generation and download UI

**Objective.** Expose artifact preflight, generation, lineage, and download only for capabilities proven safe by Tasks 33 and 35.

**Why it exists.** The V2 UI must not expose unsafe STEP-to-Abaqus export merely because a legacy endpoint exists.

**Dependencies.** Tasks 33, 35, 36A, and 36B.

**In scope.**
- Authoritative validation/eligibility and capability report; explicit unsupported blockers; generation command for safe current routes; immutable manifest/hash display; authorized checksum-verified download; regeneration lineage.

**Out of scope.**
- Automatic solver execution, job cards, results, controlled meshing not yet implemented, or any blocked positional STEP-to-Abaqus route.

**Architecture and contracts.**
- UI renders backend preflight and manifest state and never infers export eligibility. Artifact commands bind exact ModelVersion, SetupRevision, MappingEvidence, expected revision, and idempotency key.

**Prohibited shortcuts.**
- No optimistic export, hidden legacy endpoint, warning-only unsupported content, client-built artifact, manual set correction, unsafe face-index mapping, or production fixture download.

**Tests.**
- Safe supported generation/download, blocked STEP-to-Abaqus, missing/stale mapping or approval, changed setup/model, checksum/authorization failure, idempotent retry, restart/multi-worker, two-tab stale request cancellation, browser, and full regression.

**Definition of Done.**
- V2 exposes only capability-proven artifacts with exact lineage; unsafe or unsupported routes remain visibly blocked and emit no bytes.

**Required evidence.**
- Capability-block screenshots, safe artifact/manifest hashes, authorization and stale-state traces, browser evidence, full regression, and independent safety review.

**Documentation updates.**
- Artifact UX, capability matrix, download/lineage behavior, blocked-route explanation, and `PROGRESS_V2.md`.

## Task 37 — V2.2 production-readiness and default-route cutover gate

**Objective.** Decide whether the trustworthy V2 workflow may become the default route.

**Why it exists.** UI parity alone cannot justify cutover; material, coordinate, mapping, persistence, authorization, correction, and migration gates must all pass.

**Dependencies.** Tasks 22–35 and 36A–36C; approved release thresholds, support plan, and rollback owner.

**In scope.**
- Functional parity, real browser/WebGL coverage, accessibility, performance, security, migration/rollback rehearsal, operations monitoring, and explicit safe-capability matrix.
- With human approval only, switch `/` to V2 while retaining `/legacy` and `/app-v2` during the rollback window.

**Out of scope.**
- Claiming complex geometry, bounded agent, solver execution, or result visualization.

**Architecture and contracts.**
- Cutover is configuration/release controlled and reversible without data dual write.
- Legacy access does not bypass V2 authorization or safety for V2 records.

**Prohibited shortcuts.**
- No cutover with open critical risks, process-memory production state, production replay, unsafe mapping, missing rollback, or preview-only test evidence.

**Tests.**
- Complete Python/frontend/browser suites; migration and rollback; restart/multi-worker; two-tab/two-model; cross-tenant denial; stale mutations; accessibility; load/performance; production route/mode scans.

**Definition of Done.**
- All V2.2 gates pass and humans approve cutover, or `/` remains legacy with an explicit blocker. No intermediate silent state is allowed.

**Required evidence.**
- Signed gate checklist, risk closure, CI/security/accessibility/performance reports, rollback rehearsal, route matrix, and release approval.

**Documentation updates.**
- V2.2 release notes, support/capability matrix, cutover/rollback runbook, and `PROGRESS_V2.md`.

---

# PHASE 3 — V2.3 deterministic Geometry V2 and progressive retrieval

## Task 38 — Geometry V2 audit, representative benchmark, and measured baseline

**Objective.** Establish the benchmark harness, licensed corpus, and current performance/correctness baseline before any geometry rewrite.

**Why it exists.** Complex-model claims and architecture choices cannot be based on the two sprint fixtures or unmeasured Gmsh/OCC assumptions.

**Dependencies.** Tasks 16–18 and 23–26; approved dataset licenses, safety metrics, geometry-kernel decision process, and performance budgets.

**In scope.**
- Audit parser, inventory, cylinders, labels, meshes, queries, caching, Gmsh thread/global-state constraints, and reusable contracts.
- Spike whether the current Gmsh/OCC boundary can expose bodies, shells, wires, oriented coedges, edges, vertices, and face orientation reliably; approve the extraction kernel, isolation model, tolerance policy, and fallback/unsupported boundary before Task 39.
- Build benchmark harness and corpus: sprint regressions, legally cleared public/procedural/manual parts, rotated/re-exported variants, symmetry/ambiguity, analytic/freeform surfaces, and increasing face counts. SimJEB or any other named dataset is included only after license and relevance approval.
- Record ground truth, licensing, baseline precision/recall, silent errors, latency, memory, and stability.

**Out of scope.**
- New B-rep graph, features, agent, or classifier.

**Architecture and contracts.**
- Benchmark separates deterministic, LIVE, and replay modes and binds data to exact model versions.

**Prohibited shortcuts.**
- No fixture-only claims, test-set tuning in production, unlicensed data, or rewrite before baseline.

**Tests.**
- Corpus integrity/license metadata, deterministic ordering, baseline at representative 100/1,000/10,000-face tiers where available, rotations/re-exports, and existing fixture regressions.

**Definition of Done.**
- A reviewed benchmark and measured current baseline exist; the extraction kernel/isolation/tolerance decision is approved; Geometry V2 migration sequence is evidence-based.

**Required evidence.**
- Corpus manifest, licenses, benchmark results, profiler reports, kernel capability spike/ADR, isolation decision, failure taxonomy, and independent geometry review.

**Documentation updates.**
- Geometry audit, benchmark/evaluation plan, corpus provenance, risk updates, and `PROGRESS_V2.md`.

## Task 39 — Immutable complete B-rep topology graph

**Objective.** Represent bodies, shells, faces, loops/wires, edges, and vertices with validated incidence and model-version binding.

**Why it exists.** A flat face inventory cannot support complex patches, feature relationships, stable retrieval, or learned graph evidence.

**Dependencies.** Task 38; approved B-rep schema and migration/compatibility adapter.

**In scope.**
- Body/shell ownership, outer/inner wires, oriented coedges, face-edge and edge-vertex incidence, face orientation, shared topological adjacency, deterministic serialization, graph validation, and compatibility projection to current `FaceInventory`.

**Out of scope.**
- Rich descriptors, feature classification, retrieval, or learned inference.

**Architecture and contracts.**
- B-rep graph is immutable, versioned by source/parser/kernel schema, and stored once per ModelVersion.
- One contained extraction boundary owns the kernel lifecycle, resource limits, cleanup, and atomic cache publication.
- Current consumers use an explicit projection; no parallel parser truth.

**Prohibited shortcuts.**
- No tag-only identity, missing coedges/loops/vertices, mutable graph, direct request-handler kernel lifecycle, partial cache publication, duplicate parser path, or fixture-specific repair.

**Tests.**
- Round trip, incidence invariants, orientation/adjacency symmetry, invalid/non-manifold graph rejection, concurrent extraction, timeout/crash cleanup, atomic cache behavior, transformed/re-exported models, corpus coverage, scale/performance, and V1 projection parity.

**Definition of Done.**
- Every supported benchmark STEP model produces a consistent immutable graph or typed unsupported/invalid outcome.

**Required evidence.**
- Schemas, invariants report, corpus results, performance profile, compatibility comparison, and independent review.

**Documentation updates.**
- Geometry V2 graph design, serialization/versioning, compatibility projection, and `PROGRESS_V2.md`.

## Task 40 — Rich deterministic descriptors and intrinsic fingerprints

**Objective.** Compute scalable face/edge/topology/spatial evidence and transform-stable fingerprints.

**Why it exists.** Progressive retrieval and feature recognition need richer intrinsic evidence than type, area, centroid, and bounding boxes.

**Dependencies.** Tasks 38 and 39.

**In scope.**
- Face analytic/freeform type, area/perimeter, local frame, principal directions, normal/curvature statistics, loops, cylinder/cone parameters, dimensions, body-relative position.
- Edge curve type/length, dihedral, convexity/concavity, continuity, and boundary role.
- Geometry and neighborhood fingerprints plus implementation/version metadata.

**Out of scope.**
- Semantic feature labels, patch growth, retrieval, or ML.

**Architecture and contracts.**
- Descriptor records reference immutable graph entities and declare computation version/tolerance.
- Intrinsic fingerprints exclude unstable raw tags and rigid transforms. A fingerprint is matching evidence, never a global entity ID or approval.

**Prohibited shortcuts.**
- No bbox-derived analytic radius, hidden tolerance tuning per fixture, tag-order fingerprint, or descriptor recomputation in frontend/agent.

**Tests.**
- Rigid-transform invariance, face-tag permutation, numerical tolerance, analytic ground truth, invalid/degenerate geometry, corpus scale, cache/version invalidation, and V1 cylinder traps.

**Definition of Done.**
- Descriptors/fingerprints are deterministic, versioned, transform-stable within documented tolerances, and benchmarked at scale.

**Required evidence.**
- Descriptor schema, invariance report, tolerance rationale, benchmark results, and independent review.

**Documentation updates.**
- Descriptor/fingerprint contract, tolerances, limitations, and `PROGRESS_V2.md`.

## Task 41 — Semantic patch and deterministic region-growth engine

**Objective.** Select meaningful multi-face regions through explicit continuity and boundary rules.

**Why it exists.** Complex pads, bores, and blended surfaces often span multiple CAD faces.

**Dependencies.** Tasks 39 and 40.

**In scope.**
- Grow across tangent/coplanar faces, connected analytic/internal surfaces, configured continuity; stop at sharp/contradictory boundaries; manual add/remove correction; evidence and termination reasons.

**Out of scope.**
- Named engineering features, patterns, agent behavior, or learned segmentation.

**Architecture and contracts.**
- Patch is an immutable query result over graph entity IDs with operation, thresholds, evidence, model/version, and algorithm version.

**Prohibited shortcuts.**
- No flood-fill without termination evidence, silent crossing of discontinuities, frontend-only patch membership, or auto-confirmation.

**Tests.**
- Split pads, blended surfaces, internal components, sharp/tangent thresholds, manual correction, rotations, ambiguous boundaries, determinism, corpus precision/recall, and viewer display.

**Definition of Done.**
- Supported patch operations produce deterministic auditable regions and explicit uncertainty/termination.

**Required evidence.**
- Operation schemas, benchmark metrics, visual examples, failure cases, and independent review.

**Documentation updates.**
- Patch/region-growth contract, thresholds, correction flow, and `PROGRESS_V2.md`.

## Task 42 — Conservative analytic feature candidates

**Objective.** Generate verified or explicitly uncertain analytic engineering candidates before compound recognition or ML.

**Why it exists.** Holes, bores, pads, bosses, fillets, and chamfers can often be identified deterministically and safely.

**Dependencies.** Tasks 39–41 and benchmark ground truth from Task 38.

**In scope.**
- Through/blind holes, bores, planar pads, and simple cylindrical bosses where deterministic evidence supports them.
- Candidate entities, dimensions, local frame, verification checks, limitations, and `verified|partially_verified|unknown`.

**Out of scope.**
- Counterbore/countersink compounds, pockets/slots, ribs, patterns, or learned labels.

**Architecture and contracts.**
- Candidate service consumes graph/descriptors/patches and never mutates them; unknown is first-class. Existing `geom/cylinders.py` and label helpers are evolved or adapted into this owner rather than retained as competing calculations.

**Prohibited shortcuts.**
- No conflation of fillet/partial cylinder/boss/hole, fixture-specific thresholds, high-confidence guess, or candidate confirmation.

**Tests.**
- Sprint traps plus unseen corpus, transforms/re-exports, negative examples, unknown/OOD geometry, precision/recall, silent-error metric, and performance.

**Definition of Done.**
- Conservative analytic candidates meet approved precision/silent-error thresholds and uncertain cases remain unknown.

**Required evidence.**
- Per-class metrics, negative/failure examples, threshold rationale, and independent review.

**Documentation updates.**
- Candidate schemas, verification rules, benchmark report, and `PROGRESS_V2.md`.

## Task 43 — Compound features, equal-geometry groups, and pattern recognition

**Objective.** Build compound and relational candidates on top of proven analytic entities.

**Why it exists.** Counterbores, countersinks, slots, pockets, ribs, and circular/linear/symmetric groups require relationships and patterns, not one-face rules.

**Dependencies.** Tasks 39–42; approved per-family benchmark labels.

**In scope.**
- Counterbores/countersinks, conservative slots/pockets, fillet/chamfer chains, ribs where verifiable, equal-geometry groups, and linear/circular/symmetric patterns.
- Split implementation and metrics by feature family; allow unsupported families to remain unknown.

**Out of scope.**
- Learned inference, assemblies, arbitrary machining-history reconstruction, or agent reasoning.

**Architecture and contracts.**
- Compound candidates reference component candidates/patches and retain relationship proof and limitations.

**Prohibited shortcuts.**
- No monolithic classifier, partial relation treated as verified compound, family-specific production ID, or hidden fallback.

**Tests.**
- Per-family positives/negatives, near-miss compounds, incomplete patterns, symmetry ambiguity, transforms, unseen corpus metrics, performance, and unknown behavior.

**Definition of Done.**
- Each implemented family has separate evidence, thresholds, and safe unknown behavior; no family is claimed from aggregate metrics alone.

**Required evidence.**
- Per-family reports, relationship traces, visualizations, failures, and independent review.

**Documentation updates.**
- Compound/pattern contracts, family capability matrix, benchmark report, and `PROGRESS_V2.md`.

## Task 44 — Hierarchical geometry index and bounded summaries

**Objective.** Build the searchable hierarchy that exposes bodies, features, patches, and entities without dumping every face.

**Why it exists.** Large models require bounded coarse-to-fine inspection for both users and the later agent.

**Dependencies.** Tasks 39–43.

**In scope.**
- Model→body→feature group→patch→face/edge hierarchy; concise summaries; deterministic filters/pagination; relationship queries; model-version cache; capability/limitation metadata.

**Out of scope.**
- Natural-language parsing, agent tool loop, vector database, graph database, or learned embeddings.

**Architecture and contracts.**
- Every summary resolves to exact immutable entities and evidence; pagination/order are deterministic; cache keys include graph/index versions.

**Prohibited shortcuts.**
- No raw full-face prompt dump, lossy unresolvable summary, mutable index truth, premature vector/graph database, or LLM-generated summary facts.

**Tests.**
- Bounded output at scale, deterministic pagination, drill-down resolution, relationship queries, cache invalidation, permissions/model binding, performance, and corpus coverage.

**Definition of Done.**
- Complex benchmark models can be inspected progressively with bounded resolvable summaries and documented latency/memory.

**Required evidence.**
- Index schema, scale report, resolution traces, cache tests, and independent review.

**Documentation updates.**
- Hierarchy/index contract, pagination and cache policy, limitations, and `PROGRESS_V2.md`.

## Task 45 — Deterministic Geometry V2 benchmark gate

**Objective.** Decide whether the graph, descriptors, patches, features, patterns, and hierarchy are ready for progressive retrieval and freeze the exact semantic-region reference consumed downstream.

**Why it exists.** Retrieval must not hide weak or silently wrong geometry primitives.

**Dependencies.** Tasks 38–44; approved geometry metrics and thresholds.

**In scope.**
- Run full benchmark, failure taxonomy, regression comparison, scale/performance, transform/re-export stability, unknown behavior, and user-correction inspection.
- Define a versioned SemanticRegionRef containing ModelVersion/content hash, graph hash, exact entity kind/IDs, derivation and recognizer versions, evidence references, and verification status; validate it on every use and reject stale/cross-model references.

**Out of scope.**
- Requirement parsing, agent, solver mapping, or classifier.

**Architecture and contracts.**
- Gate metrics are per capability and include silent wrong selection, not only aggregate accuracy.
- SemanticRegionRef proves exact CAD ownership and derivation evidence. It does not prove engineer setup approval, CAD-to-mesh mapping, or solver suitability.

**Prohibited shortcuts.**
- No threshold change after viewing test labels without recorded plan/re-split, aggregate score masking weak class, or unsupported model exclusion after failure.

**Tests.**
- Full frozen benchmark, clean environment, repeatability, adverse/ambiguous set, performance tiers, V1 regression, SemanticRegionRef round trip, and forged/stale/cross-model rejection.

**Definition of Done.**
- Approved deterministic capabilities pass; exact semantic-region references are versioned and stale-safe; failures/unknowns are explicit; otherwise retrieval work is blocked or scoped to passing capabilities.

**Required evidence.**
- Signed benchmark report, frozen manifest/hash, SemanticRegionRef schema/examples, stale/cross-model tests, taxonomy, performance data, and independent review.

**Documentation updates.**
- Geometry V2 gate report, semantic-region identity contract, capability matrix, known limitations, and `PROGRESS_V2.md`.

## Task 46 — Instruction-requirement schema and complete clause parser

**Objective.** Convert every material engineering clause into explicit typed requirements before geometry retrieval.

**Why it exists.** Schema-valid query operations can ignore count, size, orientation, spatial, material, coordinate, or exclusion clauses and still appear confident.

**Dependencies.** Tasks 24, 28, 31, 32, 44, and 45; existing interpreter compatibility plan.

**In scope.**
- Requirements for concept, count, dimensions/comparison, orientation, position, parent body/patch, relations, patterns, inclusion/exclusion, coordinate system, material/condition clauses, and unsupported markers.
- Retain exact wording and source span; forbid entity IDs; extend/facade `llm/interpreter.py` rather than create a second interpreter.

**Out of scope.**
- Retrieval execution, agent replanning, approval, or entity selection.

**Architecture and contracts.**
- Versioned requirement schema is the only model-output contract before retrieval; current simple typed-op fast path remains behind an adapter.
- Existing `ground.semantics` remains the sole unit and direction-normalization owner; requirements preserve its typed outputs and never duplicate conversion rules.

**Prohibited shortcuts.**
- No dropped clause, entity ID, unit/count conflation, parallel parser, or unsupported clause converted to generic geometry.

**Tests.**
- Complex multi-clause benchmark, counts versus quantities, exclusions, coordinates/materials, unsupported phrases, direct-ID guard, mocked/live separation, migration, and simple-case parity.

**Definition of Done.**
- Every benchmark clause is represented, contradicted, or explicitly unsupported; no clause disappears silently.

**Required evidence.**
- Schema, coverage corpus, parser metrics/failures, compatibility report, and independent review.

**Documentation updates.**
- Requirement schema, model/deterministic boundary, versioning, and `PROGRESS_V2.md`.

## Task 47 — Typed progressive-retrieval facade over existing deterministic modules

**Objective.** Expose bounded coarse-to-fine tools by extending current query, geometry, grounding, semantics, and validation owners.

**Why it exists.** The later agent needs stable tools, but a second query/semantics implementation would create divergent truth.

**Dependencies.** Tasks 44–46; approved facade/tool versioning.

**In scope.**
- Typed tools for inspect model, list/describe bodies, search features/patches, query faces, patterns, spatial relationships, grow/subtract regions, compare candidates, and verify cardinality/relations/membership.
- Model/setup version binding, tool version, typed error, pagination, evidence, limitations, timing, trace, and server-issued candidate handles.
- Adapters into existing `ground/queries.py`, `ground/engine.py`, `ground/semantics.py`, and validator.

**Out of scope.**
- Agent loop, arbitrary Python/shell, network tools, approval, export, or solver action.

**Architecture and contracts.**
- Tools are deterministic pure/service calls with no LLM/network; one query/semantics owner remains.
- Candidate handles are trace-scoped, ModelVersion-bound, tamper-resistant references that resolve server-side to Task 45 SemanticRegionRefs and evidence. Model-facing boundaries accept handles, never invented raw entity IDs.

**Prohibited shortcuts.**
- No duplicate query engine, raw face dump, fixture branch, hidden state mutation, direct IDs from model output, client-authored candidate handle, or unbounded result.

**Tests.**
- Typed schema/error/pagination, evidence resolution, tool determinism, forged/stale/cross-model handle rejection, large-model bounds, existing simple query parity, permission/model binding, and no-network scans.

**Definition of Done.**
- Supported workflows can inspect and narrow geometry entirely through bounded deterministic tools and submit only server-resolvable candidate handles with exact evidence.

**Required evidence.**
- Tool catalog/schema, parity report, traces, performance limits, source-ownership scan, and independent review.

**Documentation updates.**
- Tool facade/API, ownership map, error/evidence contract, and `PROGRESS_V2.md`.

## Task 48 — Requirement-coverage, ambiguity, and clarification verifier

**Objective.** Verify every requirement independently and produce targeted clarification for unresolved critical clauses.

**Why it exists.** Candidate score margin is not proof that the entire instruction was satisfied.

**Dependencies.** Tasks 28, 30, 46, and 47.

**In scope.**
- `verified|contradicted|unresolved|unsupported` status, evidence, criticality, and exact clarification target per requirement.
- Separate interpretation, geometry, mapping, and approval confidence.
- Integrate durable clarification IDs/revisions from Task 28.

**Out of scope.**
- Agent replanning, human approval, export, or solver mapping.

**Architecture and contracts.**
- Any unresolved critical requirement blocks proposal.
- Clarification is a persisted typed terminal result, not a transient UI prompt.
- An answer is fully validated against clarification ID, candidate handle, ModelVersion, and base SetupRevision before the record is consumed; an invalid answer leaves it available for retry.
- Clause coverage is pre-proposal evidence and remains distinct from the existing authoritative `ir.validate` setup-validity gate.

**Prohibited shortcuts.**
- No aggregate score as coverage, generic clarification when a clause is known, candidate auto-pick, or verifier self-approval.

**Tests.**
- Over-specific instructions, six-small-holes-around-bore, contradictory clauses, missing exclusions, low/high margins, invalid-then-valid answer retention, durable clarification retry, direct click, stale revision/model, and unsupported requirements.

**Definition of Done.**
- Proposals include complete requirement coverage; unresolved critical clauses always clarify or fail safely.

**Required evidence.**
- Coverage matrices, targeted clarification traces, negative tests, metrics, and independent review.

**Documentation updates.**
- Coverage/verifier contract, confidence taxonomy, clarification integration, and `PROGRESS_V2.md`.

## Task 49 — Progressive retrieval integration and V2.3 release gate

**Objective.** Integrate requirement parsing, deterministic retrieval, coverage verification, correction continuation, and the V2 UI without an agent.

**Why it exists.** Progressive deterministic behavior must be proven before adding model-driven tool choice.

**Dependencies.** Tasks 30, 34, 37, and 45–48.

**In scope.**
- Coarse-to-fine retrieval execution plan for supported requirements, candidate comparison, targeted clarification, direct-click continuation, explanations, setup proposal, UI evidence trace, and benchmark release gate.

**Out of scope.**
- Tool-choosing agent, critic, solver mapping/execution, or classifier.

**Architecture and contracts.**
- Extend the existing `app/orchestration.py` strategy boundary. The deterministic controller invokes the same typed tools and verifier later exposed to the agent; setup writes remain versioned commands, and no second orchestration authority is created.

**Prohibited shortcuts.**
- No one-shot raw face prompt, hidden fallback to V1 expected outputs, unpersisted clarification, or geometry truth in React.

**Tests.**
- Full complex deterministic benchmark, simple V1 cases, corrections without prompt rewrite, requirement coverage, two-tab state, restart/multi-worker, latency/memory, and browser evidence trace.

**Definition of Done.**
- V2.3 meets approved complex deterministic selection/coverage/silent-error thresholds or remains blocked with scoped capabilities.

**Required evidence.**
- Frozen results, trace examples, correction-time metrics, UI/browser evidence, performance/cost report, and release approval.

**Documentation updates.**
- Progressive retrieval architecture, V2.3 capability/release notes, benchmark report, and `PROGRESS_V2.md`.

---

# PHASE 4 — V2.4 one bounded orchestration agent

## Task 50 — Bounded tool-using orchestration agent

**Objective.** Add one controlled agent that chooses deterministic tools and terminates through typed outcomes.

**Why it exists.** Complex references may require inspect–narrow–compare–verify replanning, but autonomy must remain bounded by deterministic evidence and human approval.

**Dependencies.** Tasks 46–49; approved budgets, model, prompt/version, and terminal-action schema.

**In scope.**
- Parse goals, select typed tools, inspect results, narrow/compare/replan, request clarification, and submit a server-issued candidate handle for a proposed setup command.
- Tool-call/replan/token/time limits, persisted trace, idempotent resume, and terminal actions: propose, clarify, unsupported, invalid model, insufficient evidence, failed safely.

**Out of scope.**
- Entity-ID invention, arbitrary shell/Python/network, confirmation, assumption/material approval, validation ownership, export, solver execution, or multi-agent swarm.

**Architecture and contracts.**
- Add the agent as one bounded strategy behind the existing `app/orchestration.py` authority. The agent sees only typed tool results and requirements; deterministic verifier and setup aggregate decide admissibility.
- Before materialization, the backend revalidates candidate handle, evidence, complete clause coverage, ModelVersion, and expected SetupRevision.

**Prohibited shortcuts.**
- No raw database/geometry access, raw entity IDs in terminal output, generated code execution, direct state mutation, self-validation, hidden retries beyond budget, or secondary autonomous agents.

**Tests.**
- Mocked deterministic tool traces, forged/stale/cross-model handles, budgets, empty/contradictory/broad results, resume/retry, no-ID guard, terminal actions, prompt injection, simple fast path, and LIVE/REPLAY separation.

**Definition of Done.**
- Agent behavior is bounded, fully traced, and unable to approve/export/execute; unsafe cases terminate safely.

**Required evidence.**
- Permission matrix, trace corpus, budget report, adversarial tests, model/prompt version, and independent safety review.

**Documentation updates.**
- Agent architecture, permissions, budgets, terminal schema, threat model, and `PROGRESS_V2.md`.

## Task 51 — Agent correction and evidence-explanation continuation

**Objective.** Continue retrieval after user correction and expose evidence without granting the agent or another model mutation authority.

**Why it exists.** Engineers need “why this region?”, “exclude these”, and “use the other candidate” without repeating the complete instruction.

**Dependencies.** Tasks 28, 30, 34, 48, 49, and 50.

**In scope.**
- Continue agent trace after durable click/clarification/correction; evidence explanations; typed exclude/other-candidate commands.
- Deterministic evidence and verifier concerns rendered as read-only explanations.

**Out of scope.**
- A second critic/model/agent, multi-agent debate, autonomous approval, or solver actions.

**Architecture and contracts.**
- Continuation references immutable prior trace and current setup revision.
- Explanations cite persisted deterministic evidence and verifier output. Only the single Task 50 agent may orchestrate retrieval, and it cannot call privileged write tools.

**Prohibited shortcuts.**
- No replaying hidden conversation as truth, second-agent review, history rewrite, unbounded continuation, or bypassing coverage.

**Tests.**
- Exclude/use-other/direct-click continuation, stale revision, explanation evidence resolution, single-agent permission denial, budgets, restart, and browser correction flow.

**Definition of Done.**
- Corrections continue safely with complete lineage; explanations resolve to evidence; no second agent or model is introduced.

**Required evidence.**
- Continuation traces, permission tests, correction-time results, UI examples, and independent review.

**Documentation updates.**
- Correction/explanation protocol, single-agent boundary, trace continuation, and `PROGRESS_V2.md`.

## Task 52 — Bounded-agent evaluation and V2.4 release gate

**Objective.** Prove the agent improves complex handling without increasing silent errors.

**Why it exists.** Tool use and plausible explanations are not evidence of correct engineering grounding.

**Dependencies.** Tasks 45, 49, 50, and 51; approved agent release thresholds.

**In scope.**
- Full-instruction coverage, region precision/recall, silent-wrong rate, clarification utility, correction time, tool calls, latency, cost, budget failures, unsupported behavior, and human review study.

**Out of scope.**
- Solver execution, classifier, or threshold changes during final scoring.

**Architecture and contracts.**
- Deterministic, REPLAY, and LIVE results remain separate; frozen benchmark and prompt/tool versions identify each run.

**Prohibited shortcuts.**
- No replay labeled LIVE, case-specific prompt branch, hidden retry, post-hoc test exclusion, or aggregate score masking critical failures.

**Tests.**
- Frozen benchmark in clean environment, adversarial prompt/tool failures, repeatability, cost/budget, regression fast path, and independent result verification.

**Definition of Done.**
- V2.4 ships only if silent-wrong and safety thresholds pass; otherwise the agent remains limited/disabled with deterministic V2.3 intact.

**Required evidence.**
- Signed evaluation report, frozen hashes, traces, taxonomy, cost/performance, human study, and release approval.

**Documentation updates.**
- Agent evaluation report, V2.4 capabilities/limitations, incident rollback, and `PROGRESS_V2.md`.

---

# PHASE 5 — V2.5 verified automatic CalculiX pipeline

## Task 53 — Controlled meshing and CAD-region propagation design/prototype

**Objective.** Select and prove the method for carrying confirmed semantic CAD regions into solver-native named sets.

**Why it exists.** Verified semantic identity must precede solver artifacts; positional face matching is blocked by Task 33.

**Dependencies.** Tasks 23–26, 33, 35, and 45; approved meshing scope and quality budgets. Instruction-derived regions additionally require Task 49 or Task 52 according to whether deterministic retrieval or the agent produced them; manually confirmed Task 45 semantic regions do not.

**In scope.**
- Design/spike: STEP→controlled Gmsh/OCC mesh→source-aware boundary facets→stable named node/element/surface sets.
- Meshing parameters/provenance, mapping verification, ambiguity failures, quality report, deterministic settings, benchmark spike.

**Out of scope.**
- Production meshing service, complete solver deck, execution, or automatic repair.

**Architecture and contracts.**
- Prototype consumes an exact approved semantic-region reference and emits MappingEvidence plus a named-set package without changing the setup. Task 45 identity is necessary but not itself proof of CAD-to-mesh or mesh-to-solver mapping.

**Prohibited shortcuts.**
- No runtime positional mapping, unchecked nearest face, manual set editing, quality warning treated as verified, or broad auto-repair.

**Tests.**
- Selected benchmark parts, permuted order, split faces, curved/tangent regions, changed mesh parameters, set completeness, resultant area, determinism, and fail-closed cases.

**Definition of Done.**
- One reviewed approach demonstrates verified mapping on the approved spike corpus with explicit unsupported cases; production design is approved.

**Required evidence.**
- Design ADR, spike packages, mapping/quality reports, failure cases, performance, and independent geometry/solver review.

**Documentation updates.**
- Controlled-meshing design, MappingEvidence extension, quality/unsupported policy, and `PROGRESS_V2.md`.

## Task 54 — Production controlled meshing and named-set mapping service

**Objective.** Implement the approved controlled-meshing path as a versioned deterministic service.

**Why it exists.** Solver adapters need verified named sets, not a prototype or face-order assumption.

**Dependencies.** Tasks 23–27, 33, 35, and 53.

**In scope.**
- Isolated bounded meshing execution, parameter profiles, source-region propagation, deterministic names, MappingEvidence, quality/capability report, artifact/object-store lineage, cancellation/timeout hook, and cache/versioning.
- Require every setup-referenced semantic region to map exactly once with verified orientation and ownership; missing, duplicate, or ambiguous propagation fails the complete mesh artifact.

**Out of scope.**
- Solver deck semantics, solver execution, adaptive repair, or result processing.

**Architecture and contracts.**
- Service consumes immutable model version plus approved setup regions and emits immutable mesh/set artifact plus evidence.
- “Execution” here is a bounded subprocess/container call owned by the meshing service. It does not introduce the durable JobService, a generic background-job table, or product job UI before Task 59.

**Prohibited shortcuts.**
- No mutable mesh cache, manual set patch, unsupported region drop, unbounded web-process Gmsh, or mapping without evidence.

**Tests.**
- Full approved mesh benchmark, mapping completeness, permuted order, cache/version invalidation, process failure/timeout, restart/multi-worker, deterministic names/hashes, quality thresholds, and security limits.

**Definition of Done.**
- Supported regions map to verified named sets reproducibly; unsupported/poor-quality cases fail before solver generation.

**Required evidence.**
- Mesh/mapping benchmark, artifact hashes/manifests, isolation/concurrency results, quality reports, and independent review.

**Documentation updates.**
- Meshing service/API, profiles, mapping evidence, operations limits, and `PROGRESS_V2.md`.

## Task 55 — Solver capability, preflight, and artifact-generation contract

**Objective.** Define one stable generation boundary for CalculiX and Abaqus without mixing execution or normalization.

**Why it exists.** Current adapters have different implicit capabilities, and the old plan combined generate, execute, poll, collect, and normalize before those domains existed.

**Dependencies.** Tasks 24, 31–35, and 54.

**In scope.**
- `probe_metadata`, `capabilities`, `preflight`, `generate`, and `verify_artifact`.
- Adapter/version metadata, source/model/entity/load/BC/material/analysis support, mesh requirements, solver version range, complete unsupported report, and artifact manifest integration.
- Wrap/refactor current adapters rather than duplicate them.

**Out of scope.**
- Execute, poll, cancel, collect, normalize, runner, or job state.

**Architecture and contracts.**
- Preflight is complete and machine-readable; generation cannot silently omit setup content.
- Adapter is pure with respect to approved inputs and produces immutable artifacts.

**Prohibited shortcuts.**
- No second exporter, partial unsupported warning followed by generation, arbitrary code, hidden solver defaults, or mapping bypass.

**Tests.**
- Capability matrix, complete preflight, every supported/unsupported condition, deterministic generation, manifest/hash, source/mapping mismatch, current golden parity, and source scan.

**Definition of Done.**
- CalculiX and the current Abaqus facade implement the same generation/preflight boundary; unsupported content blocks deterministically. Abaqus generation remains capability-blocked until Task 67A supplies licensed verification.

**Required evidence.**
- Interface/schema, capability matrices, golden artifacts, parity report, and independent solver review.

**Documentation updates.**
- Adapter generation contract, capability/preflight schema, migration from current exporters, and `PROGRESS_V2.md`.

## Task 56 — Complete executable CalculiX artifact

**Objective.** Generate a complete CalculiX model from an approved setup and verified named sets.

**Why it exists.** The current CalculiX output is an appendable fragment, not the complete automatic execution package required by V2.5.

**Dependencies.** Tasks 31–35, 54, and 55; approved supported CalculiX versions.

**In scope.**
- Nodes/elements/sets, materials/sections, step/output requests, supported BCs/loads, deterministic names, complete manifest, parser tests, and real controlled `ccx` verification smoke in a fixed isolated test harness.

**Out of scope.**
- Durable jobs, managed execution service, result normalization, or connected runner.

**Architecture and contracts.**
- Complete deck is generated only from approved revision, verified mesh/sets, and adapter capability report.
- The smoke harness proves the artifact and is not a product worker, JobService path, API execution feature, or substitute for Task 60’s pre-execution security gate.

**Prohibited shortcuts.**
- No handwritten deck completion, skipped unsupported condition, optional-only solver evidence, or manual result modification.

**Tests.**
- Parse and execute reference cases; resultant/pressure/gravity/displacement/reaction sanity; deterministic bytes; invalid/singular/missing set; supported solver versions; clean container.

**Definition of Done.**
- Multiple supported reference cases execute from generated complete decks without manual file edits; failures are explicit.

**Required evidence.**
- Decks/manifests, solver versions/commands/logs/status, sanity results, deterministic hashes, and independent review.

**Documentation updates.**
- Complete CalculiX generation, supported subset/version matrix, execution evidence, and `PROGRESS_V2.md`.

## Task 57 — Solver-neutral result schema

**Objective.** Define normalized result and lineage contracts before postprocessors, jobs, runners, or result UI depend on them.

**Why it exists.** Frontend and job packages must not independently understand native CalculiX/Abaqus formats.

**Dependencies.** Tasks 24, 26, 27, 32, 35, and 56; approved requested output scope.

**In scope.**
- ResultBundle with solver/version, model/setup/mapping/artifact lineage, completion/convergence metadata, warnings/errors, explicit units/coordinates, displacement/stress/strain/reaction summaries, normalized field-file references, raw logs/native results, hashes, postprocessor version, and an opaque versioned `SolverExecutionRef` value contract.

**Out of scope.**
- Native extraction code, durable Job ownership/state, job execution, contours, or automatic engineering validation.

**Architecture and contracts.**
- ResultBundle is immutable and execution-bound through `SolverExecutionRef`; raw and normalized artifacts remain distinct and hashed. Task 59 later creates the durable Job owner and binds its ID to this already-defined value contract.
- Initial conformance uses CalculiX examples. Abaqus schema conformance is added with Task 67B and does not block V2.5.

**Prohibited shortcuts.**
- No frontend-native parser, implicit units/coordinates, convergence claim from exit code alone, or summary without raw lineage.

**Tests.**
- Schema/golden bundles for CalculiX/Abaqus examples, units/coordinate validation, missing/partial output, failure result, version migration, and lineage mismatch.

**Definition of Done.**
- Both solver families can target one versioned result contract with explicit lineage and failure semantics.

**Required evidence.**
- Schema/golden examples, compatibility review, migration tests, and independent solver/frontend review.

**Documentation updates.**
- ResultBundle schema, units/coordinate conventions, failure semantics, and `PROGRESS_V2.md`.

## Task 58 — Trusted CalculiX postprocessor and sanity-check contract

**Objective.** Extract normalized CalculiX results through versioned trusted code and deterministic sanity checks.

**Why it exists.** Automatic execution is incomplete until requested results can be safely parsed, normalized, and checked.

**Dependencies.** Tasks 56 and 57; approved reference solutions/tolerances.

**In scope.**
- Trusted parser/postprocessor, requested field verification, finite values, completion/fatal error, singularity indicators, reaction balance where applicable, deformed-size plausibility, normalized field files, logs, and ResultBundle.

**Out of scope.**
- Abaqus ODB extraction, job scheduling, UI contours, autonomous convergence diagnosis, or changing the approved setup.

**Architecture and contracts.**
- Postprocessor version is whitelisted and packaged separately from user input; it only reads job outputs and emits immutable results.

**Prohibited shortcuts.**
- No arbitrary result script, silent missing field, tolerance chosen per case, changed input, or success from file existence.

**Tests.**
- Known reference solutions, failed/singular/incomplete outputs, NaN/infinity, missing requests, reaction balance, unit conversion, deterministic normalized files, and malicious/corrupt output.

**Definition of Done.**
- Controlled CalculiX outputs produce validated ResultBundles within approved tolerances; failures remain explicit.

**Required evidence.**
- Reference comparisons, postprocessor hashes, sanity reports, negative cases, and independent solver review.

**Documentation updates.**
- CalculiX postprocessor, sanity checks/tolerances, trusted-code policy, and `PROGRESS_V2.md`.

## Task 59 — One durable job-state service

**Objective.** Create the single durable owner for long-running product work and solver-job transitions.

**Why it exists.** Separate generic and solver job systems would diverge; automatic work must survive restart, cancellation, timeout, and worker loss.

**Dependencies.** Tasks 23, 26–29, 35, 55, 57, and 58; approved queue/worker technology and state model.

**In scope.**
- Job, JobEvent, durable outbox, lease/heartbeat, idempotent stage, retry/cancel/timeout/dead-letter, progress, immutable events, and typed job kind.
- Solver states: draft, approval wait, ready, queued, leased, preflight, running, postprocessing, completed, and explicit failures.
- Exact immutable ModelVersion, SetupRevision, MappingEvidence, ArtifactManifest, ResultBundle contract version, execution profile, and engineer approval references; optimistic concurrency and idempotency on commands.
- Initial solver-job implementation; future geometry/agent work may use the same service by adding typed stages, not another state store.

**Out of scope.**
- Signed packages, actual solver execution, runner enrollment, or result UI.

**Architecture and contracts.**
- Persisted events reconstruct status; one JobService owns transitions; workers request typed transitions under lease/version checks.
- Engineer approval binds the exact revision/mapping/artifact/execution-profile tuple. Any input change requires a new job and approval; retries may retain approval only for a byte-identical signed package under an explicitly approved policy.

**Prohibited shortcuts.**
- No process-memory queue, second solver state table, status inferred from logs, unsafe retry, orphaned lease, or client transition authority.

**Tests.**
- Every valid/invalid transition, approval invalidation, stale expected revision, duplicate command, restart/multi-worker, lease race/loss, heartbeat timeout, cancellation, idempotent retry, non-idempotent no-retry, dead letter, event reconstruction, authorization, outbox replay, and stale transition.

**Definition of Done.**
- Job status is durable and reconstructable; one service owns all transitions; worker failure cannot silently orphan work.

**Required evidence.**
- State diagram, database migrations, failure-injection/concurrency traces, queue metrics, and independent review.

**Documentation updates.**
- Job architecture/API/state machine, retry/cancel policy, operations runbook, and `PROGRESS_V2.md`.

## Task 60 — Signed job-package security design and pre-execution gate

**Objective.** Define and verify the only immutable package any managed worker or customer runner may execute.

**Why it exists.** Solver automation handles untrusted files and licensed local executables; security must gate execution rather than follow it.

**Dependencies.** Tasks 17, 23, 29, 35, 55, 57, 58, and 59; approved cryptography/key custody and execution threat model.

**In scope.**
- Job manifest, exact model/mesh, approved setup revision, adapter artifact, trusted postprocessor, expected outputs, hashes/signature, limits, target solver/version.
- Canonical serialization/archive rules; signature algorithm; key custody/KMS; rotation, expiry, replay prevention, and revocation; isolated directory/identity; command allowlist; network/resource limits; verification API; and pre-execution security review.

**Out of scope.**
- Solver execution, runner enrollment, scheduler adapters, or arbitrary user code.

**Architecture and contracts.**
- Workers execute only packages signed by the authorized service and referencing whitelisted adapter/postprocessor versions.

**Prohibited shortcuts.**
- No arbitrary command string, LLM/user Python, unsigned development bypass in production, shared privileged identity, unbounded resources, or post-download mutation.

**Tests.**
- Tamper every signed field/file; wrong job/workspace/profile; wrong/expired/revoked key; replay; changed artifact; canonicalization; path traversal, symlink, archive bomb, and command injection; version downgrade; resource/network limits; and signing-key rotation rehearsal.

**Definition of Done.**
- Critical threat findings are closed; tampered/unauthorized packages fail before any executable starts.

**Required evidence.**
- Threat model, key/rotation ADR, verification tests, sandbox review, penetration findings, and security sign-off.

**Documentation updates.**
- Job-package schema, signing/key policy, execution sandbox, incident/revocation runbook, and `PROGRESS_V2.md`.

## Task 61 — Managed isolated CalculiX execution

**Objective.** Deliver the first automatic solver execution path on managed isolated workers.

**Why it exists.** Managed CalculiX proves the end-to-end job/package/result contracts before adding customer runner complexity.

**Dependencies.** Tasks 56, 58, 59, and 60; approved managed infrastructure and solver license/version policy.

**In scope.**
- Create job from approved setup, package/sign, dispatch, preflight, execute `ccx`, stream/persist status/logs, cancel/timeout, postprocess, upload ResultBundle, and clean workspace.

**Out of scope.**
- Customer runner, Abaqus, HPC, or full result contours.

**Architecture and contracts.**
- Worker accepts only leased verified package; JobService owns status; ResultBundle/object store owns outputs.

**Prohibited shortcuts.**
- No manual file move, shell string, mutable job directory reuse, direct status write outside JobService, skipped postprocessor, or hidden retry.

**Tests.**
- Successful references, missing solver, preflight failure, singular/solver failure, timeout/cancel, worker loss/restart, duplicate lease, corrupt output, cleanup, resource isolation, and reproducible CI path.

**Definition of Done.**
- A user-authorized managed CalculiX job completes automatically to a normalized ResultBundle without manual file handling.

**Required evidence.**
- Job/event trace, signed package hash, solver logs/version, result/sanity report, failure-injection results, and security/solver review.

**Documentation updates.**
- Managed execution architecture, operations/deployment, supported versions, failure handling, and `PROGRESS_V2.md`.

## Task 62 — Managed CalculiX UI and V2.5 release gate

**Objective.** Expose approved managed CalculiX job creation, status, logs, cancellation, and normalized summary in the product.

**Why it exists.** Solver/job cards become real only after backend job and result contracts exist.

**Dependencies.** Tasks 19–21B, 30, 52, 57, 59, and 61; approved V2.5 reliability thresholds.

**In scope.**
- Solver profile, job, status/log, cancel, and result-summary cards backed by real APIs; setup-revision/job lineage; notification/recovery; V2.5 release evaluation.

**Out of scope.**
- Customer runner, Abaqus, HPC, full contour workspace, or autonomous result approval.

**Architecture and contracts.**
- Cards project JobService/ResultBundle; no client job state machine; polling/stream events cause authoritative refetch.

**Prohibited shortcuts.**
- No fake progress, inferred success, production fixture job, client status mutation, or manual artifact/result upload.

**Tests.**
- Browser create/run/cancel/fail/recover, reconnect/missed event, stale setup, two users/jobs, accessibility, long logs, managed execution suite, load/reliability, and full regression.

**Definition of Done.**
- V2.5 provides verified automatic managed CalculiX from approved setup to normalized summary without manual file movement.

**Required evidence.**
- End-to-end traces/video, reliability metrics, lineage links, accessibility/performance, incident rehearsal, and release approval.

**Documentation updates.**
- V2.5 release notes, managed solver UX, capability/limitations, support runbook, and `PROGRESS_V2.md`.

---

# PHASE 6 — V2.6 customer runner, connected Abaqus, and results workspace

## Task 63 — Runner enrollment and capability discovery

**Objective.** Securely register customer-side runners and expose trusted solver capabilities.

**Why it exists.** A runner must be authenticated, workspace-bound, revocable, and capability-probed before it may receive work.

**Dependencies.** Tasks 29, 59–61; approved device/enrollment flow, certificate policy, and runner distribution plan.

**In scope.**
- One-time registration/device flow, outbound authenticated channel, runner identity/certificate, workspace binding, heartbeat, OS/resources, solver/version/license probes, revocation, and capability record.

**Out of scope.**
- Job leasing/execution, package download, result upload, updater, or HPC.

**Architecture and contracts.**
- Runner has least-privilege identity and advertises signed/verified capabilities; server never trusts client-declared workspace.
- A probe result is an observation checked against the server adapter/capability matrix, never authorization to lease or execute by itself.

**Prohibited shortcuts.**
- No inbound public port, static shared secret, cross-workspace runner, unverified version string, or execution during enrollment.

**Tests.**
- Enrollment, expired/replayed code, certificate rotation/revocation, wrong workspace, heartbeat loss, solver/version/license probes, spoofed capability, and tenant denial.

**Definition of Done.**
- An authorized user enrolls/revokes a runner and sees trustworthy capabilities; runner cannot obtain a job.

**Required evidence.**
- Enrollment/certificate traces, capability matrix, revocation test, threat review, and independent security review.

**Documentation updates.**
- Enrollment/capability protocol, certificate lifecycle, installation prerequisites, and `PROGRESS_V2.md`.

## Task 64 — Restricted runner leasing and local execution sandbox

**Objective.** Allow an enrolled runner to lease, verify, and execute approved packages under strict restrictions.

**Why it exists.** Execution security and job-state correctness must be proven before result delivery or connected solver claims.

**Dependencies.** Tasks 59, 60, and 63; approved runner sandbox per supported OS.

**In scope.**
- Atomic capability-aware outbound lease with epoch/TTL/renew/reclaim, package download, signature/version verification, isolated job directory/OS identity, command allowlist, resource/network limits, status/log events, cancel/timeout, cleanup, and lost-runner handling.

**Out of scope.**
- Result upload, automatic updater, Abaqus postprocessing, HPC, or arbitrary local commands.

**Architecture and contracts.**
- JobService grants one expiring lease; runner transition requests are authenticated/versioned; execution uses package allowlist only.

**Prohibited shortcuts.**
- No shell command from server/user/LLM, shared working directory, inbound listener, unlimited resources, execution before verify, stale-epoch result acceptance, or runner-owned final status.

**Tests.**
- Two-runner lease races, renew/reclaim, stale epoch/replay, tampering, cancellation, timeout, process tree cleanup, lost network/runner, restart, resource/network limits, command injection, duplicate delivery, and cross-workspace denial.

**Definition of Done.**
- Restricted test packages execute safely under durable leases; failures reconstruct correctly; no arbitrary code path exists.

**Required evidence.**
- Sandbox/lease traces, security tests, process/resource logs, lost-runner recovery, and independent review.

**Documentation updates.**
- Lease/execution protocol, OS sandbox profiles, command allowlist, incident runbook, and `PROGRESS_V2.md`.

## Task 65 — Runner result delivery, upgrade, and compatibility lifecycle

**Objective.** Complete reliable result upload and runner software lifecycle before connected solver release.

**Why it exists.** A runner that can execute but cannot securely deliver outputs or upgrade safely is not supportable.

**Dependencies.** Tasks 57, 59, 60, 63, and 64.

**In scope.**
- Authorized resumable artifact/log/ResultBundle upload, checksum/lineage verification, retry/idempotency, local retention/cleanup, runner compatibility policy, signed upgrade metadata/package, rollback, and minimum-version enforcement.

**Out of scope.**
- Solver-specific connected execution, HPC, or UI contours.

**Architecture and contracts.**
- Uploads bind to leased job and expected manifest; updater uses a separate signed trusted channel and cannot alter active jobs.

**Prohibited shortcuts.**
- No arbitrary file upload, unverified update, delete-before-acknowledge, cross-job attachment, silent forced upgrade, or raw result without lineage.

**Tests.**
- Interrupted/resumed upload, checksum mismatch, duplicate retry, wrong job/workspace, storage failure, cleanup, upgrade/rollback/signature/revocation, active-job compatibility, and offline recovery.

**Definition of Done.**
- Verified results arrive durably and runner versions can upgrade/rollback securely without corrupting jobs.

**Required evidence.**
- Delivery/upgrade traces, hash/lineage tests, failure recovery, compatibility matrix, and independent review.

**Documentation updates.**
- Delivery protocol, retention, upgrade/compatibility policy, support runbook, and `PROGRESS_V2.md`.

## Task 66 — Connected CalculiX execution

**Objective.** Run approved CalculiX jobs automatically on enrolled customer runners.

**Why it exists.** Some customers need local compute/data control after the managed path is proven.

**Dependencies.** Tasks 56, 58, 59–61, and 63–65.

**In scope.**
- Runner CalculiX profile, workspace-admin-configured canonical executable path, version probe/compatibility, package dispatch, fixed arguments, restricted execution, logs/status, cancellation, trusted postprocessing, result upload, and managed-vs-connected capability selection.

**Out of scope.**
- Abaqus, HPC, or different result schema.

**Architecture and contracts.**
- Same adapter, package, JobService, and ResultBundle contracts as managed CalculiX; only execution profile changes.

**Prohibited shortcuts.**
- No separate connected job state, PATH-first arbitrary binary, user-supplied arguments, local manual deck edit, unverified executable, different postprocessor truth, or manual result upload.

**Tests.**
- Controlled local versions, missing/incompatible executable, offline/reconnect, cancel/timeout, runner loss, upload retry, parity with managed results, tenant isolation, and end-to-end UI.

**Definition of Done.**
- User selects a connected CalculiX profile and receives normalized results without moving files manually.

**Required evidence.**
- Managed/connected parity report, job traces, solver logs/versions, failure recovery, and independent review.

**Documentation updates.**
- Connected CalculiX setup, capability matrix, troubleshooting, and `PROGRESS_V2.md`.

## Task 67A — Verified Abaqus artifact generation

**Objective.** Generate a complete Abaqus artifact through verified named-set identity on an authorized licensed environment.

**Why it exists.** Connected Abaqus cannot depend on the blocked positional CAD-face path, and artifact generation must be proven independently of runner execution and native-result extraction.

**Dependencies.** Tasks 33, 35, 54, and 55; authorized Abaqus environment and approved artifact/version/license range.

**In scope.**
- Trusted existing-INP-set and controlled-meshing-set routes; complete preflight/generation; deterministic naming and manifest; authorized import/parse verification; named-set load/BC correspondence; explicit version/license/capability report.

**Out of scope.**
- Job dispatch, runner execution, ODB extraction, general CAD-native matcher, assemblies, or manual Abaqus CAE editing.

**Architecture and contracts.**
- Evolve the existing Abaqus adapter behind Task 55. Generated artifacts bind exact ModelVersion, SetupRevision, MappingEvidence, named sets, adapter version, and hashes and do not depend on a runner entity.
- CAD-native mapping remains blocked unless a separately proven matcher is later approved.

**Prohibited shortcuts.**
- No `faces[tag-1]`, face-count proof, arbitrary `cae noGUI` script, manual set correction, skipped unsupported setup content, or claim based only on successful import.

**Tests.**
- Authorized version import/parse, named-set load/BC verification, intentionally permuted Abaqus face order, equal-count wrong-order case, license/version mismatch, unsupported content, mapping mismatch, and deterministic artifact/manifest.

**Definition of Done.**
- Complete artifacts verify unchanged with exact named-set evidence; unsafe CAD-native capability remains explicitly blocked; no runner or postprocessor dependency was introduced.

**Required evidence.**
- Licensed-environment import commands/logs, artifact/manifest hashes, permuted-order mapping proof, capability failures, and independent Abaqus review.

**Documentation updates.**
- Abaqus generation contract, supported routes/version matrix, mapping limitations, license policy, and `PROGRESS_V2.md`.

## Task 67B — Trusted Abaqus ODB postprocessor

**Objective.** Normalize authorized Abaqus outputs through allowlisted, version-compatible trusted code.

**Why it exists.** Connected Abaqus cannot safely return ResultBundle data through an arbitrary job-supplied Python or `cae noGUI` script.

**Dependencies.** Tasks 57, 60, and 67A; authorized ODB reference cases, approved solver versions, and reference tolerances.

**In scope.**
- Preinstalled or vendor-reviewed extractor identified by version and digest; ODB/version compatibility; requested displacement, stress, strain, and reaction extraction; completion/convergence and balance/plausibility checks; normalized field artifacts; immutable ResultBundle.

**Out of scope.**
- Runner dispatch, solver execution, artifact generation, arbitrary user/LLM scripts, autonomous convergence diagnosis, or result UI.

**Architecture and contracts.**
- Job packages identify a trusted postprocessor ID/digest but never carry executable script text. Raw ODB and normalized artifacts remain distinct, hashed, and lineage-bound.

**Prohibited shortcuts.**
- No arbitrary `cae noGUI`/Python payload, browser ODB parsing, missing-field success, per-case tolerance changes, inferred units/frames, or unversioned extractor.

**Tests.**
- Authorized reference ODBs and tolerances, missing/corrupt/incompatible/incomplete ODB, non-finite values, absent requested field, unit/frame validation, output/resource bounds, deterministic normalized artifacts, and postprocessor digest mismatch.

**Definition of Done.**
- Supported ODBs deterministically produce trusted ResultBundles; incompatible or incomplete outputs fail explicitly before delivery.

**Required evidence.**
- Extractor source/version/digest, licensed reference comparisons, raw/normalized hashes, negative/resource tests, and independent Abaqus/security review.

**Documentation updates.**
- Abaqus postprocessor/output contract, trusted-code/version policy, reference tolerances, and `PROGRESS_V2.md`.

## Task 68 — Connected Abaqus runner execution

**Objective.** Dispatch approved Abaqus jobs through the secure customer runner and return normalized results automatically.

**Why it exists.** Licensed Abaqus normally runs in customer-controlled infrastructure and must use the same secure job and lineage model.

**Dependencies.** Tasks 59–60, 63–66, 67A, and 67B.

**In scope.**
- Abaqus runner profile, license probe/reporting, package dispatch, fixed input-deck command, status/logs, cancel, Task 67B ODB postprocessing, result delivery, and UI profile/job states. If vendor automation requires `cae noGUI`, it may invoke only a preinstalled versioned/digested trusted component selected by ID.

**Out of scope.**
- HPC scheduler, arbitrary local scripts, general unsupported Abaqus features, or manual file movement.

**Architecture and contracts.**
- Same JobService/package/delivery/ResultBundle owners; Abaqus adapter supplies only whitelisted solver-specific operations.
- The job package never carries Python or script text.

**Prohibited shortcuts.**
- No separate Abaqus job store, inbound port, arbitrary command, hidden license retry, manual ODB upload, or positional mapping.

**Tests.**
- Controlled end-to-end case, license unavailable/recovery, incompatible version, runner loss, cancellation, ODB failure, upload retry, tenant isolation, and UI recovery.

**Definition of Done.**
- A controlled case is dispatched, executed, postprocessed, and returned without manual file movement; license/version failures are explicit.

**Required evidence.**
- End-to-end job trace, signed package, solver/license logs, normalized results, failure recovery, and independent security/Abaqus review.

**Documentation updates.**
- Connected Abaqus installation, profile/capabilities, license troubleshooting, and `PROGRESS_V2.md`.

## Task 69 — HPC execution profiles

**Objective.** Add scheduler-backed execution only after local runner execution is stable.

**Why it exists.** Enterprise users may require Slurm, PBS, or LSF while scheduler credentials and data remain local.

**Dependencies.** Tasks 59–60 and 63–65, plus at least one stable connected solver from Task 66 or Task 68; approved customer scheduler priority. Task 69 is optional and does not gate V2.6 unless a launch customer explicitly selects HPC scope.

**In scope.**
- One scheduler profile at a time: submit, poll, cancel, collect; shared-filesystem or staged artifact mode; local credential ownership; scheduler job ID lineage.

**Out of scope.**
- Multiple schedulers in one task, cloud marketplace deployment, or new solver semantics.

**Architecture and contracts.**
- Scheduler adapter sits behind the same runner lease and JobService state; credentials never leave runner environment.

**Prohibited shortcuts.**
- No server-held scheduler credential, second job state, shell interpolation, unbounded queue wait, or scheduler success without result collection.

**Tests.**
- Scheduler simulator plus authorized integration, submit/poll/cancel/fail/timeout, credential isolation, lost scheduler job, staged/shared modes, and result lineage.

**Definition of Done.**
- If HPC is selected, one scheduler profile uses the shared job/package/result contracts and is supportable; later schedulers require separate reviewed tasks. Otherwise Task 69 remains deferred without blocking V2.6.

**Required evidence.**
- Adapter contract, integration traces, credential review, failure recovery, and independent HPC review.

**Documentation updates.**
- Scheduler profile setup/operations, credential policy, supported modes, and `PROGRESS_V2.md`.

## Task 70 — Normalized results workspace and 3D field visualization

**Objective.** Display job progress and trustworthy normalized engineering results in the chat-first product.

**Why it exists.** V2.6 requires one UI over ResultBundle rather than solver-native formats.

**Dependencies.** Tasks 21B, 30, 57–59, 61, 66, and 68; approved visualization/performance/accessibility budgets.

**In scope.**
- Real solver/job/result cards; queue/run/postprocess/fail states; logs/cancel; deformed/undeformed toggle; displacement and von Mises contours; range/units; reactions; warnings/sanity panel; artifact/raw downloads; lineage links.
- Viewer result layers tied to exact job/model version and cleared on model/job change.

**Out of scope.**
- Native FRD/ODB parsing in browser, autonomous result approval, convergence diagnosis, or comparison across assemblies.

**Architecture and contracts.**
- UI consumes ResultBundle and normalized field files only; ViewerController owns GPU field resources; JobService remains status authority.
- Every query, event, and field asset binds workspace, job, ModelVersion, SetupRevision, and result hash; model/job changes cancel stale requests and dispose prior GPU resources.

**Prohibited shortcuts.**
- No client-native solver parser, inferred units, stale field on another model, fake progress/result fixture in production, or hiding failed sanity checks.

**Tests.**
- CalculiX/Abaqus bundles, job transitions/reconnect, contours/ranges/units, stale model/job response cancellation, model/job switch cleanup, two-tab/two-model/two-job isolation, large field performance, WebGL fallback, accessibility, download authorization, production fixture scan, and visual regression.

**Definition of Done.**
- Users inspect normalized managed/connected results with exact lineage; fields and cards never cross job/model boundaries.

**Required evidence.**
- Browser traces/video, reference contour comparisons, performance/memory, accessibility, lineage/download tests, and independent solver/frontend review.

**Documentation updates.**
- Results UI, visualization conventions, performance limits, warnings/sanity interpretation, and `PROGRESS_V2.md`.

## Task 71 — V2.6 reliability, security, operations, and release gate

**Objective.** Harden connected execution and result delivery before external production release.

**Why it exists.** A functioning runner is not production-ready without security closure, observability, quotas, recovery, compatibility, and support operations.

**Dependencies.** Tasks 29, 60–68, and 70; Task 69 only when HPC is selected for the release; approved alpha SLOs, retention, support, and incident ownership.

**In scope.**
- Runner compatibility/update policy, secrets/certificate rotation, quotas, sandbox review, malware/file scanning, logs/metrics/traces/alerts, retention/deletion, backup/DR, incident/support runbooks, load/failure tests, tenant/artifact isolation, enabled-profile review, and V2.6 gate.

**Out of scope.**
- Classifier, assemblies, advanced physics, or unsupported solver expansion.

**Architecture and contracts.**
- Operations observe the same JobService/events and immutable lineage; security controls apply across managed and connected modes.

**Prohibited shortcuts.**
- No release with critical findings, untested restore, missing revocation/upgrade, unbounded job/resource use, or unsupported capability marketed as available.

**Tests.**
- End-to-end managed/connected/Abaqus, load/soak, worker/runner loss, backup/restore, certificate/key rotation, incident simulations, cross-tenant artifact denial, malware strategy checks, and rollback.

**Definition of Done.**
- Critical findings are closed, SLOs and recovery pass, operations can diagnose jobs, and humans approve V2.6; otherwise release remains blocked.

**Required evidence.**
- Security assessment, SLO/load reports, restore/rotation/incident rehearsals, operations dashboards/runbooks, and release approval.

**Documentation updates.**
- V2.6 release notes, operations/security/support/DR runbooks, compatibility matrix, and `PROGRESS_V2.md`.

---

# PHASE 7 — Learned geometry assistance, only after deterministic readiness

## Task 72 — Annotation schema and governed training-data pipeline

**Objective.** Create trustworthy labeled examples for semantics and feature instances only after deterministic failures are measured.

**Why it exists.** Learned assistance needs stable graph/descriptors/candidates, licensed data, unknown labels, and failure-driven objectives.

**Dependencies.** Tasks 38–45, 52, and 71; approved evidence that deterministic rules are a bottleneck; data/licensing/privacy approval.

**In scope.**
- Annotation schema/version, procedural/public/manual sources, deterministic failure sampling, explicit permission for product corrections, provenance/license, family-safe splits, unknown/OOD labels, review workflow, and dataset manifests.

**Out of scope.**
- Model training, online learning, unreviewed product telemetry, or solver truth.

**Architecture and contracts.**
- Labels bind to immutable model versions/B-rep entities and annotation version; corrections enter a reviewed queue only.

**Prohibited shortcuts.**
- No template/family leakage, unlabeled unknown treated negative, silent telemetry use, weak license metadata, or face-tag-only labels.

**Tests.**
- Schema/integrity, split leakage, provenance/license, annotation agreement, transform/re-export identity, unknown/OOD coverage, and access/retention controls.

**Definition of Done.**
- Reproducible governed train/validation/test datasets exist with explicit unknown/OOD and deterministic failure examples.

**Required evidence.**
- Dataset manifests/hashes/licenses, split analysis, agreement metrics, privacy review, and independent data review.

**Documentation updates.**
- Annotation/data pipeline, governance/licensing/privacy, split policy, and `PROGRESS_V2.md`.

## Task 73 — Interpretable baseline classifier

**Objective.** Establish a reproducible simple classifier over deterministic candidate descriptors.

**Why it exists.** A deep graph model is unjustified until a transparent baseline and calibrated unknown behavior are measured.

**Dependencies.** Tasks 40, 42–45, and 72; approved metrics and integration gate.

**In scope.**
- Gradient-boosted-tree or comparable baseline for approved feature classes plus unknown; calibration, OOD, per-class precision/recall, silent high-confidence error, latency/memory, and reproducible training.

**Out of scope.**
- Direct entity selection/confirmation, graph neural network, online learning, or production integration.

**Architecture and contracts.**
- Model consumes versioned deterministic candidate features and emits advisory label/ranking/uncertainty only.

**Prohibited shortcuts.**
- No raw tag/fixture/model-name feature, aggregate accuracy-only claim, test leakage, missing unknown class, or auto-confirmation.

**Tests.**
- Frozen splits, reproducibility, calibration/OOD, family-held-out/re-export variants, per-class metrics, adversarial correlations, latency/memory, and baseline comparison.

**Definition of Done.**
- Baseline beats the approved deterministic ranking metric without worsening silent high-confidence errors; otherwise ML work stops or is re-scoped.

**Required evidence.**
- Training config/code/data/model hashes, per-class/OOD/calibration report, error analysis, and independent ML/geometry review.

**Documentation updates.**
- Baseline model card, training/evaluation procedure, limitations, and `PROGRESS_V2.md`.

## Task 74 — B-rep graph model, conditional entry

**Objective.** Train a graph model only if Task 73 and data show a product-relevant gap it can plausibly close.

**Why it exists.** B-rep relationships may improve segmentation/instance affinity, but complexity is accepted only with measured benefit.

**Dependencies.** Tasks 39, 40, 44, 72, and 73; explicit human approval of the entry gate and compute budget.

**In scope.**
- Face semantics, same-instance affinity, feature classification, optional parameter regression, calibrated uncertainty/OOD, held-out family/re-export evaluation, and inference budget.

**Out of scope.**
- Geometry truth, direct confirmation, solver ID generation, online learning, or production integration.

**Architecture and contracts.**
- Graph model consumes immutable B-rep/descriptors and emits advisory evidence with model/data/version lineage.

**Prohibited shortcuts.**
- No phase start without entry approval, leakage, missing baseline comparison, high-confidence silent-error regression, or hidden cloud dependency.

**Tests.**
- Reproducible training, held-out families, re-exports/transforms, calibration/OOD, ablation, latency/memory, baseline comparison, and failure analysis.

**Definition of Done.**
- Model materially beats the baseline on approved product metrics without worsening silent high-confidence errors and fits runtime budgets.

**Required evidence.**
- Entry approval, model/data/config hashes, comparative report, ablations, model card, and independent review.

**Documentation updates.**
- Graph-model design/model card, entry decision, evaluation/limitations, and `PROGRESS_V2.md`.

## Task 75 — Advisory classifier integration and active-learning queue

**Objective.** Use learned predictions as bounded evidence while deterministic verification and human confirmation remain authoritative.

**Why it exists.** Even a strong classifier must not become silent geometry truth.

**Dependencies.** Tasks 45–52, 72–73, and Task 74 only if it passed; approved advisory threshold/rollback.

**In scope.**
- Deterministic candidates→learned ranking/label→deterministic verification→retrieval→human confirmation.
- Unknown/OOD fallback, model/version trace, feature flag, shadow evaluation, reviewed correction queue, rollback, and monitoring.

**Out of scope.**
- Direct setup mutation, online training, automatic approval, solver mapping, or replacement of deterministic tools.

**Architecture and contracts.**
- Classifier output is one evidence source; verifier may reject it; low-confidence/OOD becomes unknown/clarification.

**Prohibited shortcuts.**
- No direct region confirmation, ID generation, silent model update, unreviewed product data, deterministic bypass, or unavailable-model fallback that changes semantics.

**Tests.**
- Advisory precedence, verifier contradiction, unknown/OOD, disabled/unavailable model, shadow parity, rollback, monitoring, correction consent/queue, and end-to-end silent-error regression.

**Definition of Done.**
- Learned evidence improves approved metrics without gaining mutation authority; rollback and unknown behavior are proven.

**Required evidence.**
- Shadow/production-evaluation report, traces, monitoring/rollback rehearsal, data-governance review, and independent safety review.

**Documentation updates.**
- Advisory integration, model governance/monitoring, active-learning queue, rollback, and `PROGRESS_V2.md`.

## Task 76 — Learned-assistance release gate

**Objective.** Decide whether learned assistance may be enabled for approved users and capabilities.

**Why it exists.** Model quality, calibration, operations, privacy, and deterministic safety must be jointly reviewed.

**Dependencies.** Tasks 71–73 and 75; Task 74 only if its graph model was selected and accepted; approved ML release thresholds and operations owner.

**In scope.**
- Frozen evaluation, silent-error/calibration/OOD, shadow results, latency/cost, drift monitoring, data/privacy, rollback, user disclosure, and capability-specific enablement.

**Out of scope.**
- Assemblies, advanced physics, or classifier authority expansion.

**Architecture and contracts.**
- Feature flags are capability/workspace scoped; deterministic-only operation remains available.

**Prohibited shortcuts.**
- No global enablement from average accuracy, missing unknown/OOD gate, unreviewed data, irreversible rollout, or hidden learned decision.

**Tests.**
- Frozen benchmark, shadow/canary, rollback, drift alert, model outage, deterministic fallback equivalence, privacy/access, and user-facing disclosure.

**Definition of Done.**
- Humans approve a bounded advisory release or the feature remains disabled without affecting deterministic V2.

**Required evidence.**
- Signed ML gate, model card, shadow/canary metrics, rollback/drift rehearsal, privacy/security review, and release decision.

**Documentation updates.**
- Learned-assistance release notes, enabled capabilities, monitoring/rollback, and `PROGRESS_V2.md`.

---

# PHASE 8 — Later expansion

## Task group 77+ — Assemblies, contact, and advanced simulation

**Objective.** Plan each advanced domain only after the single-part vertical slice, solver pipeline, results, and operations are reliable.

**Why it exists.** Assemblies, contact, multiple materials/sections, and advanced physics introduce new identity, validation, solver, and approval problems that must not leak into earlier tasks.

**Dependencies.** Task 71; relevant benchmark, solver, customer, and safety evidence; a separately reviewed master plan for each domain.

**In scope.**
- Potential future work: assembly hierarchy/instances, instance-stable regions, contacts, fasteners/connectors, multiple materials/sections, nonlinear/multi-step/thermal/dynamic analyses, additional solvers, convergence assistance, and model-change remapping with reconfirmation.

**Out of scope.**
- Opportunistic implementation inside Tasks 16–76.

**Architecture and contracts.**
- Every new domain requires explicit identities, versioned schema/migration, deterministic validation, capability/preflight, approval, benchmark, and solver/result support.

**Prohibited shortcuts.**
- No feature smuggling, inherited single-part IDs, silent unsupported solver option, or automatic approval.

**Tests.**
- Defined by the separately approved domain plan, including migration, benchmark, solver, safety, and end-to-end evidence.

**Definition of Done.**
- No Task 77+ starts without an approved dependency-complete plan and entry gate.

**Required evidence.**
- Domain proposal, risk/architecture review, benchmark and solver availability, resource approval, and release criteria.

**Documentation updates.**
- New domain plan/ADRs, this plan’s approved extension, and `PROGRESS_V2.md`.

---

## 7. Dependency and release invariants

Before any task is approved, reviewers must verify:

1. Every dependency names an already completed task or pre-existing approved evidence.
2. No task consumes an ID, schema, service, or result type created later.
3. Tasks 19–22 remain a preview; real workflow begins only at Task 36A.
4. `/` remains legacy until Task 37 explicitly approves cutover.
5. Material, coordinate, mapping, persistence, authorization, clarification, scoped events, versioned reads, and correction commands precede Task 36A; artifact lineage precedes safe export UI in Task 36C.
6. STEP-to-Abaqus remains blocked until verified named-set mapping and Task 67A evidence; connected execution additionally requires Task 67B.
7. Geometry benchmark and baseline Task 38 precede graph/descriptors/features.
8. Deterministic hierarchy/tools/requirements/coverage precede the agent.
9. Result schema/postprocessor precede job packages and automatic execution.
10. One JobService owns managed, connected, Abaqus, and HPC state.
11. Security Task 60 precedes every solver execution task.
12. Classifier entry follows deterministic graph, descriptors, candidates, benchmark, unknown/OOD labels, and measured failures.

Phase gates must review implemented scope, remaining temporary paths, risks, evidence versus claims, deterministic/probabilistic boundaries, uncertainty behavior, lineage, rollback, and whether the next phase requires a plan amendment.

---

## 8. Human approvals still required

The plan is execution-ready only after the responsible humans approve:

- protected branch/tag and PR/CI administration;
- V2.1/V2.2/V2.3/V2.4/V2.5/V2.6 release thresholds;
- whether V2.1 is internal-only and who may enable the non-production V1 compatibility viewer;
- Node/package manager and Python lock/container tooling;
- OpenAPI generator, checked-in generated-client policy, API versioning, and deprecation window;
- production/LIVE/REPLAY/test mode policy and release-time mode verification;
- frontend visual/accessibility direction;
- database/ORM/migration and object-store technology;
- single-writer legacy-to-persistent cutover and rollback ownership;
- scoped invalidation transport choice and reconnect/retention policy;
- authentication provider, roles, retention, and deletion policy;
- upload resource limits and parser sandbox;
- geometry kernel and serialized-versus-process-isolated Gmsh policy;
- dataset licenses, benchmark corpus, and silent-error thresholds;
- OpenAI model/prompt budgets and LIVE evaluation spend;
- material/coordinate/mapping policy;
- supported Gmsh, CalculiX, and Abaqus versions;
- Abaqus license/environment access;
- job queue/worker platform;
- package-signing algorithm, KMS/key custody, rotation, and runner certificates;
- managed-worker isolation and customer-runner OS support;
- scheduler priority for HPC;
- security, privacy, operations, SLO, support, and incident ownership;
- every default-route, solver-execution, runner, and classifier release gate.

An unavailable approval is a blocker, not permission for an implementation agent to choose silently.

---

## 9. Old-to-new task crosswalk

| Old task/title | New task/title | Reason |
|---|---|---|
| 16 — Freeze demo baseline | 16 — Adopt governance and freeze V1 | Adds exact baseline, governance transition, CI/protection evidence. |
| 17 — Product/architecture/risk documents | 17 — Phase-1 decision-complete architecture | Narrowed; detailed geometry/runner design moved to audited phases. |
| 18 — Frontend/API audit | 18 — Completed planning evidence | Marked complete; not rescheduled. |
| 19 — Frontend foundation/API client | 19 — Reproducible foundation/typed transport | Bounded to toolchain, serving, generated types, errors, and CI. |
| 20 — Chat shell | 20 — Honest visual shell | Production fixtures forbidden; unsupported cards remain test-only. |
| 21 — Viewer controller | 21A + 21B | Split parity lifecycle from new layers/enhancements. |
| 22 — Real conversation/review workflow | 28, 30, 36A | Split durable conversation/clarification, coherent read/events, and final safe review integration. |
| 23 — Editable setup inspector | 30, 34, 36B | Split read projection, typed correction commands, and a bounded inspector integration. |
| 24 — UI parity/cutover | 22 + 37 | Preview gate separated from safety-complete default-route cutover. |
| 25 — Material ownership | 31 | Moved before real workflow/export. |
| 26 — Coordinate systems | 32 | Moved before real workflow/export. |
| 27 — Block unsafe Abaqus mapping | 33 | Moved before any V2 export integration. |
| 28 — Artifact manifest | 35 + 36C | Manifest follows real identities; safe UI exposure is a separate gated task. |
| 29 — Solver smoke | 56, 61, 67A + 67B | Split CalculiX artifact verification, managed execution, Abaqus artifact verification, and trusted ODB normalization. |
| 30 — Persistent domain/database | 26–29 | Split identity/storage, setup revisions, conversations, and authorization; future job/result tables deferred. |
| 31 — Object storage/model versions | 26 | Adds separate content hash versus model-version identity. |
| 32 — Persistent conversation/setup revisions | 27–28 | Split setup aggregate from append-only conversation/clarification. |
| 33 — Auth/tenant isolation | 29 | Moved before real persistent workflow. |
| 34 — Background jobs | 59 | Merged into one shared JobService; no separate generic/solver states. |
| 35 — Geometry audit/benchmark selection | 38 | Audit, corpus, harness, and measured baseline combined before rewrite. |
| 36 — Full B-rep graph | 39 | Retained after benchmark baseline. |
| 37 — Rich descriptors/fingerprints | 40 | Retained after graph. |
| 38 — Semantic patches | 41 | Retained after descriptors. |
| 39 — Features/patterns | 42 + 43 | Split conservative analytic candidates from compounds/patterns. |
| 40 — Hierarchical index | 44 | Retained after feature evidence. |
| 41 — Geometry benchmark | 38 + 45 | Harness/baseline moved before rewrite; final deterministic gate retained after. |
| 42 — Requirement schema/parser | 46 | Explicitly extends current interpreter and covers every clause. |
| 43 — Retrieval tools | 47 | Explicit facade over current deterministic owners. |
| 44 — Coverage/ambiguity verifier | 48 | Adds durable clarification dependency and confidence separation. |
| 45 — Bounded agent | 50 | Moved after deterministic retrieval integration/release. |
| 46 — Correction/critic | 49 + 51 | Deterministic correction precedes agent; correction/explanation continuation remains single-agent and the optional critic is deferred. |
| 47 — Agent evaluation | 52 | Retained as V2.4 release gate. |
| 48 — Meshing/mapping design | 53 + 54 | Split design/prototype from production service. |
| 49 — Adapter capability/preflight | 55 | Generation separated from execution/normalization. |
| 50 — Complete CalculiX deck | 56 | Retained before result/job execution. |
| 51 — Verified Abaqus artifact | 67A | Generation is verified independently of runner execution and ODB extraction. |
| 52 — Solver-job API | 59 | Merged into the one durable JobService. |
| 53 — Signed job package | 60 | Adds key/security design and pre-execution gate. |
| 54 — Runner core | 63–65 | Split enrollment/capabilities, restricted leasing/execution, and delivery/upgrade. |
| 55 — Managed/connected CalculiX | 61, 62, 66 | Managed backend/UI delivered before connected runner execution. |
| 56 — Connected Abaqus | 67A + 67B + 68 | Split verified artifact generation, trusted ODB postprocessing, and connected dispatch. |
| 57 — HPC profiles | 69 | Retained as optional, one scheduler at a time, and not a V2.6 blocker unless selected. |
| 58 — Result schema | 57 | Moved before postprocessing, jobs, packages, and execution. |
| 59 — Postprocessors | 58 + 67B | CalculiX and Abaqus trusted postprocessors remain solver-specific and precede their execution paths. |
| 60 — Results workspace | 62 + 70 | Basic managed job summary at V2.5; full normalized 3D workspace at V2.6. |
| 61 — Reliability/security/operations | 60 + 71 | Pre-execution security moved before workers; final operations gate retained. |
| 62 — Annotation/data | 72 | Retained after deterministic/agent evidence. |
| 63 — Baseline classifier | 73 | Retained with explicit unknown/OOD gate. |
| 64 — B-rep graph model | 74 | Conditional on baseline/data evidence. |
| 65 — Advisory integration | 75 + 76 | Split bounded integration from release gate. |
| 66+ — Assemblies/advanced simulation | 77+ | Deferred to separately approved plans after single-part reliability. |

---

## 10. Immediate next action

1. Approve this restructured plan.
2. Execute Task 16.
3. Execute Task 17 using the completed Task 18 audit evidence.
4. Execute Tasks 19–22 to deliver the first visible preview while `/` remains legacy.
5. Do not connect the real V2 review workflow until Tasks 23–34 are complete; Task 35 may proceed in parallel, and Task 36C safe export waits for both Task 35 and Task 36B.
6. Do not change the default route before Task 37.
7. Do not rewrite geometry before Task 38 establishes the benchmark and baseline.
8. Do not build the bounded agent before Tasks 46–49.
9. Do not execute a solver before Tasks 53–60.
10. Do not begin classifier work before Task 72’s entry gate.
