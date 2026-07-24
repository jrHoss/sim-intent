# Accepted Product V2 repository audit evidence

**Status:** Accepted evidence consolidation; this is not a new audit.
**Audit date:** 2026-07-23.
**Audited repository SHA:** `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.
**Evidence use:** Repository-grounded dependency for technical-preview Task 17.

This document consolidates the accepted repository audit evidence already
summarized in
[`PRODUCT_V2_ROADMAP.md`](../roadmap/PRODUCT_V2_ROADMAP.md). It records the
facts that informed the dependency order in
[`TECHNICAL_PREVIEW_PLAN.md`](../../TECHNICAL_PREVIEW_PLAN.md); it does not
rerun the generic audit, approve Task 17 decisions, or authorize product
implementation.

The Task 16 merge changed only governance documents. The application, routes,
schemas, tests, fixtures, dependencies, and evaluation inputs remain the V1
implementation audited at the SHA above.

## Frontend structure and lifecycle

The current frontend is a static vanilla-JavaScript application:

- `app/static/index.html` owns the fixed legacy document structure.
- `app/static/styles.css` owns the legacy presentation.
- `app/static/app.js` owns upload, Three.js setup, model loading, picking,
  instruction/clarification calls, transient click evidence, SSE highlights,
  and top-level viewer state.
- `app/static/audit.js` owns audit rendering, confirm/reject actions, readiness
  requests, and artifact downloads.

Both JavaScript modules eagerly query fixed DOM nodes at module load.
`app.js` also creates the Three.js scene, cameras, two renderers,
`OrbitControls`, loader, raycaster, face and arrow maps, active-model state,
event listeners, a global `EventSource`, and a perpetual animation loop at
module scope. `disposeModel()` releases the current model's geometry and
materials, but there is no component-level owner that cancels the animation
frame, closes the event stream, disposes both renderers and controls, and
removes every page-level listener.

The accepted conclusion is to preserve the legacy application as a
compatibility surface and port its useful concepts behind an explicit viewer
lifecycle. `app.js` and `audit.js` are not suitable for direct import into a
React component tree.

### Reusable viewer concepts

The following concepts are repository-backed and reusable:

- one glTF node per selectable face or facet group, named `face_{id}`;
- named-ancestor lookup, so descendant meshes resolve to the owning face node;
- deterministic registration of all meshes under a named face;
- bounding-box centering, camera framing, and distance limits;
- `OrbitControls` orbit, pan, damping, and zoom behavior;
- ray picking against the loaded model;
- the current click-versus-orbit threshold: at most 5 pixels of travel and
  less than 500 milliseconds;
- the synchronized orientation-axes renderer;
- the base, proposed, confirmed, candidate, fixed-BC, and load-direction
  visual vocabulary;
- per-model geometry/material disposal on model replacement.

These are interaction and rendering concepts, not authoritative engineering
state.

## Backend and API route/reuse matrix

All current routes are defined in `app/server.py`.

| Current route | Current owner and behavior | Accepted reuse treatment |
|---|---|---|
| `GET /` and `/static/*` | Legacy HTML and static assets | Preserve as the legacy compatibility surface until an approved route gate changes it. |
| `POST /models` | `ModelStore` reads the complete request body, validates the filename/suffix, writes source bytes, and parses synchronously | Retain supported upload semantics, but place them behind bounded parsing and durable model-version identity. |
| `GET /models/{model_id}/inventory` | STEP `FaceInventory` or INP `MeshInventory` | Extend or wrap behind immutable model-version and schema contracts. |
| `GET /model/{model_id}/inventory` | Singular compatibility alias for the same handler | Preserve only as an explicit compatibility alias. |
| `GET /models/{model_id}/gltf` | STEP/INP tessellation to face-named glTF | Reuse the glTF naming and viewer contract behind bounded, versioned artifact generation. |
| `GET /model/{model_id}/gltf` | Singular compatibility alias for the same handler | Preserve only as an explicit compatibility alias. |
| `POST /select` | Logs one selected face and echoes `face_{id}`; it does not mutate setup state | Treat as telemetry/legacy compatibility, not engineering truth. |
| `POST /highlight` | Publishes a transient visual command | Retain only as legacy compatibility; future updates require model/revision scope. |
| `GET /events` | Global in-memory SSE fan-out | Replace for product use with scoped invalidation/progress transport; never use it as state truth. |
| `POST /session/{session_id}/interpret` | `app/orchestration.py`, `llm/interpreter.py`, deterministic grounding, and the session store | Reuse the existing interpreter/orchestration owners behind versioned model/setup and durable conversation contracts. |
| `POST /session/{session_id}/clarify` | Resolves one process-memory pending interpretation | Replace the volatile lifecycle with a durable, retry-safe clarification record. |
| `GET /session/{session_id}/fallback-cases` | Lists checked-in REPLAY cases for the uploaded model hash | Keep outside production through explicit runtime-mode separation. |
| `POST /session/{session_id}/fallback/{case_id}` | Loads a checked-in REPLAY proposal | Keep outside production and never substitute it for LIVE. |
| `GET /session/{session_id}/intent` | Returns a `SessionSnapshot` from `SelectionSessionStore` | Evolve into a coherent versioned setup read model. |
| `PUT /session/{session_id}/intent` | Replaces the current intent subject to server-managed status checks | Replace product use with narrow revisioned commands; do not make whole-document PUT the concurrency model. |
| `POST /session/{session_id}/confirm_region` | Server-authoritative proposed-to-confirmed transition | Preserve the deterministic gate within the future setup aggregate. |
| `POST /session/{session_id}/reject_region` | Server-authoritative proposed-to-rejected transition | Preserve the deterministic gate within the future setup aggregate. |
| `POST /session/{session_id}/assumptions/{assumption_id}/accept` | Server-authoritative pending-to-accepted transition | Preserve with exact revision and actor lineage. |
| `POST /session/{session_id}/assumptions/{assumption_id}/reject` | Server-authoritative pending-to-rejected transition | Preserve with exact revision and actor lineage. |
| `GET /session/{session_id}/audit` | Partial audit projection plus freshly computed validation | Reuse validation, but hydrate product UI from one coherent revisioned setup projection. |
| `POST /session/{session_id}/export-gate` | Recomputes deterministic export eligibility | Preserve as backend-owned readiness logic. |
| `POST /session/{session_id}/export` | Regenerates Abaqus Python or a CalculiX INP fragment without executing a solver | Reuse adapter owners behind explicit capability, mapping, lineage, and artifact contracts; block unsafe capabilities. |

The current `SimulationIntent` in `ir/schema.py`, validation in
`ir/validate.py`, session transitions in `app/session.py`, unit/load semantics
in `ground/semantics.py`, deterministic grounding modules, and export adapters
are existing owners to extend or wrap. A frontend or parallel service must not
become a second owner for those rules.

## Authoritative state and current risks

Current ownership is:

| State | Current owner | Current limitation |
|---|---|---|
| Uploaded source bytes and metadata | Filesystem-backed `ModelStore` | No project or immutable domain model version; the model ID hashes safe filename plus a NUL separator plus file bytes, rather than identifying content bytes alone. |
| STEP inventory | `geom/inventory.py`, cached by exact source-file SHA-256 | Cache is local filesystem state and not a durable product artifact record. |
| Existing INP mesh and declared sets | `geom/meshes.py` through `meshio` plus native-ID scanning | No persistent model-version lineage. |
| Setup intent and region/assumption transitions | Process-memory `SelectionSessionStore` | Lost on restart and unavailable across workers. |
| Validation and export eligibility | `ir/validate.py`, recomputed by backend paths | Audit and intent are separate projections with no shared setup revision token. |
| Pending clarification | `app.state.pending_interpretations` dictionary | Volatile, keyed only by session/model ID, and not retry-safe. |
| Viewer highlight events | Process-memory `ViewerEventBroker` | Global and unscoped by model, setup, revision, user, or workspace. |
| Browser click evidence | `selectedClicks` in `app.js` until submitted with an interpretation | Local, transient, and unversioned; `/select` is telemetry only. |
| Viewer camera and GPU objects | Module globals in `app.js` | No explicit component lifecycle or multi-viewer isolation. |

There is no current Project, Conversation, SetupRevision, MeshRevision,
SolverRun, ResultBundle, tenant, or durable audit-event model. The current
session ID is the uploaded model ID, so model identity and volatile setup
identity are conflated.

## Persistence, clarification, SSE, and concurrency findings

- Uploaded files and inventory caches use the local filesystem, but
  engineering setup, confirmations, assumptions, pending clarification, and
  viewer events are process-memory state.
- `SelectionSessionStore` uses an `RLock`, which provides atomic transitions
  only inside one Python process. It does not provide restart recovery,
  multi-worker consistency, ETags, idempotency keys, or optimistic
  concurrency.
- Orchestration reads the current snapshot, merges a proposal, and saves it in
  separate calls. The sequence is not one durable transaction.
- `PUT /session/{session_id}/intent` accepts a complete intent and has no base
  revision token; two tabs cannot receive a typed stale-write result.
- `POST /session/{session_id}/clarify` removes the pending item with `pop()`
  before validating the returned candidate set and before reproposal
  succeeds. An invalid answer can therefore destroy the pending
  clarification.
- A later interpretation for the same session can replace the single pending
  dictionary entry.
- `/events` creates one queue per connected subscriber and broadcasts every
  highlight to every subscriber. Events have no model/setup/revision scope,
  durable cursor, replay, or missed-event recovery contract.
- `GET /session/{session_id}/intent` and
  `GET /session/{session_id}/audit` cannot prove that two responses describe
  the same revision.

These findings support durable revision ownership, consume-on-success
clarification, scoped update transport, and explicit stale-write behavior
before a real product workflow is exposed.

## Unsafe STEP-to-Abaqus positional mapping

`export/abaqus_py.py` declares and implements
`source_step_face_order`: OCC face tag `n` is emitted as
`part.faces[n - 1]`. Preflight requires a contiguous source tag sequence, and
the generated script checks face count, but neither check proves that Abaqus
imported the faces in the same geometric order.

This is a cross-kernel positional assumption that can silently apply a load or
boundary condition to the wrong face. Accepted evidence requires the
technical-preview STEP-to-Abaqus path to remain blocked until verified
mapping or controlled named-set evidence exists. Abaqus remains export-only
for the active release.

## Material and coordinate-direction findings

- `SimulationIntent` has a material list but no material-to-body or
  material-to-region assignment field.
- Both current adapters require exactly one material; Abaqus assigns it to all
  solid cells and CalculiX assigns it to all elements.
- `app/orchestration.py` inserts demonstration steel
  (`E=210000 MPa`, `nu=0.3`) and adds density for gravity. The choice is
  recorded as a pending assumption, but it is still an implicit prototype
  default rather than engineer-entered material state.
- `llm/interpreter.py` rejects natural-language material-property input in the
  current workflow.
- `ground/semantics.py` defaults qualitative downward/upward and vertical
  language to the Y axis unless an explicit axis is present. It records an
  export-critical assumption, but there is no coordinate-system entity or
  condition-level coordinate reference in `SimulationIntent`.
- The current analysis schema fixes `static_structural` and mm-N-MPa internal
  units. It has no named analysis revision or versioned solver-settings
  aggregate.

The accepted conclusion is to make materials, assignments, original and
normalized units, coordinate systems, directional references, physics, and
supported solver settings explicit and revision-bound before solve.

## Test inventory and coverage gaps

The accepted Task 16 baseline evidence records:

- 318 tests collected;
- 317 passed;
- one optional CalculiX `ccx` smoke skipped;
- REPLAY evaluation 15/15, reported separately from LIVE.

The Python test inventory contains 15 modules:

- geometry and ingestion:
  `test_parser.py`, `test_cylinders.py`, `test_labels.py`,
  `test_meshes.py`, and `test_server.py`;
- query, semantics, and grounding:
  `test_queries.py`, `test_semantics.py`, and `test_grounding.py`;
- IR, validation, and session state:
  `test_ir.py`, `test_validate.py`, and `test_session.py`;
- interpretation and evaluation:
  `test_interpreter.py` and `test_eval.py`;
- export:
  `test_export.py`;
- legacy viewer HTTP/source contracts:
  `test_viewer.py`.

`tests/test_viewer.py` verifies served HTML/JavaScript strings, HTTP routes,
selection telemetry, highlight SSE, and glTF contracts. Other tests also
inspect JavaScript source strings. There is no JavaScript execution test
runner, browser automation, WebGL lifecycle test, two-tab test, or visual
regression suite. No frontend package manifest or Playwright configuration is
present.

## Dependencies, packaging, parser isolation, and Gmsh concurrency

- `requirements.txt` lists direct dependencies without a complete lock; only
  `pydantic>=2` has a version constraint, and it is not a reproducible
  transitive lock.
- There is no checked-in backend container contract, frontend package/lockfile,
  or CI workflow in the audited repository.
- Headless Linux Gmsh requires the documented GLU system dependency, but no
  supported Linux package/container contract currently installs it.
- `POST /models` calls `await request.body()` and parses multipart content
  after the entire request is resident in memory. No product upload-size,
  parser CPU, memory, time, disk, or no-network boundary is present.
- Model ingestion invokes STEP/INP parsers synchronously from the web-process
  request path.
- `geom/parser.py`, `geom/cylinders.py`, and STEP tessellation each initialize
  and finalize the process-global Gmsh API independently and reject calls when
  Gmsh is already initialized.
- There is no inter-request lock, queue, or isolated worker that serializes
  those Gmsh operations. The exclusivity check detects some overlap but does
  not provide a safe concurrency policy.
- CalculiX `ccx` was absent from the captured Task 16 environment. That is a
  future technical-preview Task 18 environment dependency, not a Task 16
  failure.

## Accepted reviewer conclusions and task-order impact

The accepted conclusions were:

1. Preserve V1 and port viewer concepts; do not import the eager legacy
   modules into the future React lifecycle.
2. Keep backend `SimulationIntent`, semantics, grounding, validation, session
   gates, and adapters as owners to extend or wrap; do not duplicate
   engineering truth in the frontend or a parallel service.
3. Establish architecture ownership and route decisions before
   implementation.
4. Establish reproducible environments and bounded CI before persistent or
   solver capabilities depend on them.
5. Version API and schema contracts before creating durable product records.
6. Contain uploads/parsers and resolve Gmsh concurrency before product model
   persistence and public upload workflows rely on them.
7. Create durable model, setup, and clarification ownership before exposing a
   real chat-first engineering workflow.
8. Replace implicit material and coordinate conventions with explicit,
   engineer-reviewed state.
9. Block positional STEP-to-Abaqus mapping until verified boundary evidence
   exists.
10. Add browser/WebGL lifecycle, multi-tab, and visual coverage with the new
    frontend rather than treating source-string checks as browser evidence.

This evidence directly informed the technical-preview ordering: Task 17 owns
the decision-complete architecture; Task 18 owns reproducible environments;
Task 19 owns schema versioning; Task 20 owns upload/parser and Gmsh
containment; Tasks 21-23 establish durable state; Tasks 24-26 establish the
additive frontend and coherent read model; Task 28 removes implicit
engineering defaults; and Tasks 34-38 establish meshing, verified mapping,
complete artifacts, results, and isolated local CalculiX execution in
dependency order.

Task 17 must consume this accepted evidence and make its required decisions.
This document does not make those decisions and does not indicate that Task 17
or Task 18 has started.

## Repository sources

The consolidation was checked against:

- `app/static/index.html`, `app/static/app.js`,
  `app/static/audit.js`, and `app/static/styles.css`;
- `app/server.py`, `app/session.py`, and `app/orchestration.py`;
- `ir/schema.py` and `ir/validate.py`;
- `ground/semantics.py`, `ground/queries.py`, and `ground/engine.py`;
- `geom/parser.py`, `geom/inventory.py`, `geom/meshes.py`, and
  `geom/cylinders.py`;
- `llm/interpreter.py`;
- `export/abaqus_py.py`, `export/ccx_inp.py`, and `export/common.py`;
- `tests/test_*.py` and `requirements.txt`;
- the accepted audit summary and completed-planning-evidence section in
  [`PRODUCT_V2_ROADMAP.md`](../roadmap/PRODUCT_V2_ROADMAP.md);
- the active dependency sequence in
  [`TECHNICAL_PREVIEW_PLAN.md`](../../TECHNICAL_PREVIEW_PLAN.md).
