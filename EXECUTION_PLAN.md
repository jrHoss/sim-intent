# EXECUTION_PLAN.md — sim-intent prototype

Implements sprint-goal.md as 15 sequential implementation tasks. Each task = one session, ends with its Definition of Done (DoD) command passing plus `pytest tests/ -x` green plus a line in PROGRESS.md. Tasks 1-7 are the headless core (no UI, no live LLM needed). Tasks 8-10 the viewer. Tasks 11-15 interpretation, export, evaluation. If working in a team, tasks 8-10 can run in parallel with 5-7 by a second person; the plan works solo too.

```
STEP/INP ─> [T2 parser] ─> [T3-4 geometry index] ─┐
                                                   ├─> [T6 query engine] ─> [T12 grounding] ─> [T1 IR] ─> [T13 validation] ─> [T14 export]
Text ─────────────────────> [T11 interpreter] ─────┘                                            │
Clicks ───> [T8-10 viewer] ────────────────────────┘                       [T13 audit/confirm] ─┘
```

Fixtures provided (tests/fixtures/, read-only):
- bracket.step — L-bracket, 12 faces: 2 bolt holes (ids 11,12, r5.5, Z-axis), 1 wall hole (id 10, r8, Y-axis), 1 FILLET that is surface-type Cylinder (id 3, r4). The fillet is a deliberate trap.
- bracket_expected.json — ground truth incl. traps.
- plate_hole.step — 7 faces, simple sanity part.

---

## Task 1 — Repo + env gate + IR schema
Scaffold repo per CLAUDE.md rule layout. `scripts/check_env.py`: import gmsh, mesh a box, assert tets; import meshio, fastapi. Then `ir/schema.py` (Pydantic v2): Analysis(type=static_structural, units), Material(linear_elastic_isotropic: E_MPa, nu; name), Region(id, entity_type: cad_face|cad_edge|mesh_face|node_set|element_set, entity_ids: list[int]|list[str], selection_method: semantic_geometry_query|multimodal_reference|user_click|user_confirmed, confidence: float, source_instruction: str, status: proposed|confirmed|rejected), BC(fixed_displacement|prescribed_displacement, region_ref, components), Load(resultant_surface_force|surface_traction|pressure|gravity|concentrated_force, region_ref, vector|magnitude), Assumption(text, status: pending|accepted|rejected), SimulationIntent(analysis, materials, regions, bcs, loads, assumptions, validation_status). JSON Schema export. Write 3 hand-authored example IRs incl. the sprint-goal.md example verbatim.
DoD: `python scripts/check_env.py` prints ENV OK; `pytest tests/test_ir.py` (round-trip; sprint-goal example validates; region without confidence rejected; export blocked serializer refuses unconfirmed regions).

## Task 2 — STEP parser + raw face inventory
`geom/parser.py`: load STEP via gmsh OCC, stable face tags, per-face record: surface type, area, centroid, bbox, sampled outward normal, perimeter edges. `geom/inventory.py`: FaceInventory dataclass, JSON serialization, cache keyed by file sha256.
DoD: `pytest tests/test_parser.py`: bracket.step -> exactly 12 faces, 4 of type Cylinder, plate_hole.step -> 7 faces; inventory JSON round-trips; cache hit on second load.

## Task 3 — Cylindrical analysis + hole semantics
`geom/cylinders.py`: for each cylindrical face compute true radius + axis (fit from surface points or OCC curvature, NOT bbox), angular extent (full 2*pi vs partial), length along axis. Classify: hole (full circle, material outside, i.e. face normal points inward), boss (normal outward), fillet/partial (angular extent < full OR length >> radius with convex adjacency). Group holes: by radius cluster (rtol 5%) then by axis direction; coaxial detection.
DoD: `pytest tests/test_cylinders.py` against bracket_expected.json: holes == {10,11,12}; fillet 3 classified fillet/partial, NOT hole; bolt-hole group == {11,12} (same radius+axis), wall hole separate. 100% pass, no tolerance fudging.

## Task 4 — Planar labels + adjacency + spatial predicates
`geom/labels.py`: extreme-face labels (top/bottom/left/right/front/back via centroid + normal against model bbox), largest_face, adjacency graph (shared edges), connected components, position predicates: above/below/relative height rank, area rank.
DoD: `pytest tests/test_labels.py`: bracket top-of-wall face and base bottom face labeled correctly; plate_hole yields the known 6 planar labels; adjacency graph of bracket is connected, node count 12.

## Task 5 — Mesh-format ingestion
`geom/meshes.py`: read Abaqus INP and STL via meshio; stable IDs (INP: native ids + preserved NSET/ELSET names as regions; STL: face ids from deterministic ordering). Extract boundary-face inventory analog (area, centroid, normal per boundary facet group) so grounding works on meshes too. Generate a small test INP fixture from bracket via gmsh in the test setup (allowed: fixture generation, not runtime meshing).
DoD: `pytest tests/test_meshes.py`: INP round-trip preserves set names; STL loads with stable ids.

## Task 6 — Deterministic query engine
`ground/queries.py`, pure functions over FaceInventory (zero LLM, zero network): find_faces(surface_type=), holes(), hole_groups(), filter_radius(r, rtol), filter_axis(dir), rank_by(pos_predicate), area_max/min(n), adjacent_to(ids), in_component(id), labeled(name), combine via intersect/union/difference. Composable: a Query is a JSON-serializable op list; `execute(ops) -> QueryResult(entity_ids, per_candidate_scores)`.
DoD: `pytest tests/test_queries.py`: op list [holes, filter_radius(5.5), filter_axis(Z)] -> {11,12} on bracket; [labeled(top_face)] correct on both parts; score margins exposed; 100% pass.

## Task 7 — Load/unit semantics
`ground/semantics.py`: single module owning unit parsing (N, kN, MN, Pa, kPa, MPa, GPa, mm, m) -> mm-N-MPa; force-vs-pressure-vs-traction disambiguation rules ("X kN across/on face" = resultant; "X MPa" = pressure; "per node" -> total with count from region); direction words ("downward" = -Y or -Z per model convention, recorded as assumption); gravity handling. Every inference emits an Assumption.
DoD: `pytest tests/test_semantics.py`: table of 20 phrase->(type, SI value, assumptions) cases incl. the sprint-goal eval unit traps; 100% pass.

## Task 8 — Viewer backend
`app/server.py` FastAPI: POST /models (upload STEP/INP), GET /models/{id}/inventory, GET /models/{id}/gltf. glTF built per CLAUDE.md quirk: per-face surface tessellation, one named node per face (face_{tag}), so picking is name-based. Contract frozen after this task.
DoD: `pytest tests/test_server.py` (httpx): upload bracket, inventory matches Task 2, glTF contains 12 nodes named face_1..face_12; manual check: file opens in a glTF viewer.

## Task 9 — Viewer frontend
`app/static/`: vanilla JS + three.js (CDN). Load glTF, orbit controls, raycaster click -> face tag -> POST /select, highlight API: styles for confirmed(green)/proposed(blue)/candidate(amber)/fixed-BC(hatch)/load(arrow glyph along vector). Axes triad. No build step.
DoD: `uvicorn app.server:app` -> browser: bracket renders, clicking a bolt hole logs face_11/face_12 server-side, POST /highlight from curl changes face colors live. (Manual DoD, record a short screen capture into docs/.)

## Task 10 — Selection session state
`app/session.py`: server-side session per model: current IR draft, selected entities, highlight state; endpoints: GET/PUT /session/{id}/intent, POST /session/{id}/confirm_region, POST /reject_region. Viewer polls or SSE for highlight updates.
DoD: `pytest tests/test_session.py`: full state machine proposed->confirmed->export-eligible; rejecting a region reopens it.

## Task 11 — LLM interpreter (text -> typed ops)
`llm/interpreter.py`: OpenAI Responses API structured output. Input: instruction + FaceInventory summary (labels, hole groups with radii/axes, areas; NOT raw coordinates dump) + semantics module vocabulary. Output: list of intents, each = {op_list (Task 6 vocabulary only), bc/load payload (value+unit strings), target_description}. Hard rule: schema forbids entity_ids in LLM output (validator rejects). Mocked tests in CI; `scripts/smoke_llm.py` for live.
DoD: `pytest tests/test_interpreter.py` (mocked): "fix the two bolt holes" -> ops [holes, group_coaxial/filter_radius...] not IDs; "5 kN downward on the upper mounting face" -> resultant_surface_force + rank_by(upper) ops; malformed LLM reply -> clean re-ask path.

## Task 12 — Grounding engine + ambiguity
`ground/engine.py`: execute interpreter ops -> candidates + scores; confidence = normalized margin; margin < threshold OR count mismatch ("two holes" but 3 candidates) -> ClarificationRequest {question, candidate_sets} instead of Region; else Region(proposed) with full provenance. Fusion rule: user_click evidence overrides query result, conflict -> clarification, never silent override.
DoD: `pytest tests/test_grounding.py`: "fix the two bolt holes" on bracket -> exactly {11,12} proposed (fillet excluded, wall hole excluded); "fix the hole" -> ClarificationRequest with 3 candidate sets; click on face 10 + "this hole" -> {10} with selection_method user_click.

## Task 13 — Validation + audit + confirmation gate
`ir/validate.py`: units complete, every load/bc region resolved, vector magnitudes nonzero, material fields positive, no unconfirmed assumption of type unit-critical; validation_status computed. `app/static/audit.js` + endpoints: audit panel listing per region source instruction, method, confidence, assumptions accept/reject buttons; export button disabled until all confirmed (server-enforced, Task 10 state machine).
DoD: `pytest tests/test_validate.py`; manual: audit panel renders for a grounded bracket session; export endpoint returns 409 while any region unconfirmed.

## Task 14 — Export adapters
`export/abaqus_py.py`: confirmed IR -> Abaqus Python script (part refs by face IDs, *materials, BCs, loads, static step) as inspectable text artifact. `export/ccx_inp.py`: for FE-mesh-input models, IR -> INP fragment (NSETs from entity ids, *BOUNDARY, *CLOAD resultant split, *DLOAD). Both regenerate purely from IR (no conversation state). Stretch flag --run: if model came as mesh, run ccx on assembled deck and return max displacement (only if ccx present; skip cleanly otherwise).
DoD: `pytest tests/test_export.py`: golden-file comparison of generated Abaqus script for the sprint-goal example IR; ccx fragment for the Task 5 INP fixture is accepted by ccx (parse-run) when available.

## Task 15 — Evaluation harness + demo hardening
`eval/cases/*.json`: 15 cases per sprint-goal list across bracket + plate_hole (fix bottom face; prevent vertical motion; fix both bolt holes; 2 MPa inner cylinder; 5 kN top flange; gravity -Z; ambiguous "fix the left side" expecting clarification; mixed-unit traps; click-assisted cases marked). Each: instruction, expected entity_ids, type, vector, units, clarification_required, expected IR. `eval/run.py`: headless run (mock clicks where marked), results table (pass/fail/after-clarification), failure taxonomy (grounding|unit|ambiguity-unflagged|llm-parse). `docs/demo.md`: 3-minute script + fallback: pre-grounded session cache for all 15 cases.
DoD: `python eval/run.py` >= 12/15 after at most one clarification each; results table + taxonomy written to eval/results.md; all sprint-goal success criteria 1-9 checked off in PROGRESS.md with evidence links.

---

## Session protocol
- One task per session; read CLAUDE.md first; never redefine a DoD, report blockers instead.
- Tasks 1-7 need no browser and no API key (mocked). First live-LLM moment is the Task 11 smoke script. First visual moment is Task 9. Plan demos accordingly.
- Team split option: person A tasks 5-7,11-12; person B tasks 8-10; merge at 13.
