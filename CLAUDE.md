# CLAUDE.md — sim-intent (sprint prototype)

Prototype per sprint-goal.md (Maien's scope): a grounding layer that turns natural-language + click/image engineering intent into a solver-neutral IR with explicit entity IDs, reviewed by the engineer, exported to one solver format. NOT an FEA automation system.

Work through EXECUTION_PLAN.md tasks strictly in order. Do not start a task until the previous task's DoD command passes.

## Scope guards (from sprint-goal.md, enforce against yourself)
IN: static structural intent only; linear elastic isotropic; STEP + INP/STL ingestion; face/edge/set selection; fixed/vector displacement BCs; concentrated force, surface traction, pressure, gravity; one export adapter; confirmation before export.
OUT (do not build, even if it seems easy): mesh generation/repair, solver execution infrastructure, convergence diagnosis, result validation, contact, nonlinearity, thermal, dynamics, point-cloud reconstruction. If a task seems to need one of these, stop and re-read the task.

## Standing rules
1. The LLM NEVER emits entity IDs. It composes typed queries from the deterministic query library; the query engine returns IDs. Any code path where model output contains a face/node/element ID directly is a bug.
2. Every Region in the IR carries: entity_ids, selection_method, confidence, source_instruction (verbatim), status(proposed|confirmed|rejected). Every IR carries: units block, assumptions[], validation_status. No exceptions, no optional-for-now.
3. Nothing exports until every region status == confirmed. The confirmation gate is architectural, not UI polish.
4. Ambiguity is a feature: when candidate score margin < threshold, return a clarification with candidate entity sets attached. Never auto-pick silently.
5. Units: mm-N-MPa internally. LLM emits value+unit strings; Python converts. "5 kN across the face" = resultant force; "2 MPa on" = pressure; per-node values are always converted to totals in the IR. This disambiguation lives in ONE module (semantics.py), tested exhaustively.
6. Deterministic layer gets 100% test pass, no flaky tolerance. LLM layer is tested with mocked responses in CI; live API only in smoke scripts.
7. Never modify tests/fixtures/. bracket_expected.json defines ground truth.
8. Frontend is served by FastAPI as static files; one interface contract with backend: GET /model/{id}/gltf, GET /model/{id}/inventory, POST /highlight {entity_ids, style}, click events POST /select {entity_id}. Freeze this contract at Task 8; additive changes only.
9. Dependencies: gmsh, meshio, fastapi, uvicorn, pydantic v2, openai, numpy, scipy, typer, rich, pytest, httpx. Frontend: three.js via CDN, vanilla JS, no build step, no npm. Ask before adding anything.
10. Every module created in a task gets its test file in the same task.
11. Known fixture traps (deliberate): the bracket's fillet (face 3) is surface-type Cylinder; "find cylindrical faces" must not equal "find holes". A hole = cylindrical face subtending a full circle with free interior (adjacency check) and bounded length. The wall hole (face 10) differs from bolt holes (11,12) in both radius and axis; radius clustering + axis grouping must separate them.

## Environment quirks (verified in sandbox)
- pip gmsh needs apt libglu1-mesa on headless Ubuntu.
- gmsh OCC: importShapes -> per-face getType/getMass/getBoundingBox/getNormal all work; cylinder radius: derive from curvature or fit, bbox span is an approximation only.
- Face tags are stable across reimports of the same STEP file; they are NOT stable across regeneration of the geometry. Cache inventories keyed by file hash.
- Tessellation for the viewer: mesh each CAD face's surface triangulation separately (gmsh 2D mesh per face) and export one glTF node per face named face_{tag}; then three.js raycaster picking returns the face tag from the node name for free. Do not export one merged mesh.
- meshio reads Abaqus INP meshes including ELSETs/NSETs; preserve set names as first-class regions for FE-mesh inputs.
