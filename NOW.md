# Current release slice

## R5.2 — Deterministic STEP Meshing Service

- **R5.1 completed:** `cc6ed16d2e260fb5bb409152da6e14add4a52dd5` (`feat(mesh): add durable R5.1 mesh artifacts and persistence`).
- **Objective:** Convert one exact current single-solid STEP `ModelVersion` and one exact current eligible `SetupRevision`, including explicit valid `MeshSettings`, into deterministic validated topology and quality artifacts and publish one immutable `MeshRevision` through the R5.1 persistence boundary.
- **Included scope:** The fixed `gmsh_tet_v1` profile; global target size; fresh bounded isolated Gmsh execution; one application-owned Gmsh slot shared by parsing and meshing; first-order tetrahedra; canonical nodes, tetrahedra, and mesh-local exterior triangles; signed-volume, mean-ratio, and normalized-aspect-ratio summaries; typed sanitized failures; first mesh, replay, request conflict, and linear successor remesh behavior.
- **Explicit exclusions:** Migration `0006`; dependency changes; HTTP, OpenAPI, generated TypeScript, or frontend work; CAD-face/boundary mapping and every R6 metric; advanced/local refinement; solver-deck generation; solver execution; durable jobs or distributed queues.
- **Dependencies:** R5.1 mesh artifacts, CAS publication/cleanup, ownership, exact-read, idempotency, and predecessor lineage; R2 isolated parser boundary; R3 `MeshSettings`; R4 immutable STEP identity; accepted ADR-006 subprocess and shared-slot decision; supported Gmsh 4.15.2 environment.
- **Acceptance criteria:** Supported bracket and plate STEP sources mesh repeatably; application-controlled order is canonical; identical supported-environment inputs produce identical canonical artifact bytes and hashes; invalid/empty/unsupported/higher-order/inverted/degenerate/inconsistent meshes fail before publication; failures leave no mesh row, artifact, or temporary output; remesh preserves immutable readable predecessors.
- **Required evidence:** Focused profile/domain/worker/service tests; affected mesh, ingestion, persistence, migration, and concurrency suites; full Python suite; environment, schema/OpenAPI, lock, fixture, frozen-evaluation, migration-head, Git, secret, host-path, dependency, migration, API/frontend, R6-scope, and generated-file checks.
- **Stop conditions:** Stop rather than broaden scope if a migration or dependency is required, R5.1 cannot represent the output/lineage, safe execution requires durable jobs, R6 mapping is required, a frozen fixture must change, or an accepted architecture decision must be reversed.

**Status:** R5.2 implementation is complete locally. The fourth fresh
independent review returned `APPROVE`: no unresolved BLOCKER, HIGH, MEDIUM, or
LOW finding remains, and every historical R5.2 finding is closed. The current
production profile is version 3 with fingerprint
`80a8bd69b12ac4f132c4231fe7a38dec2dc67d1e6b7f26c8bc5e09b14322a1d5` and
resolved identity
`gmsh_tet_v1@3:80a8bd69b12ac4f132c4231fe7a38dec2dc67d1e6b7f26c8bc5e09b14322a1d5`.
The fourth independent full suite passed **1,773** tests with **2 expected
skips**, and R5.2 stayed within its approved scope. Nothing has been staged,
committed, pushed, merged, or remotely published. The branch is ready for a
separately authorized local commit, but commit remains unauthorized until the
user explicitly approves it; any later merge or publication requires a
separate decision.
