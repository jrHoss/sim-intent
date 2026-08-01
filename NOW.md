# NOW — R4b.3: truthful CAD selection hydration

**Branch:** `r4b3-truthful-cad-selection-hydration` (from `b8a6215`)

## Goal

Reconcile the durable browser and the durable API's selection projection with
the R4b.2 SimulationIntent v3 stable-CAD-region contract, so an engineer can
click a STEP face, see truthful evidence of what was selected and whether it is
authoritative, confirm it, and reopen it unchanged after restart — with the
backend remaining the sole stable-identity authority.

## In scope

- Additive `cad_selection_evidence` projection on `SetupRevisionResponse`.
- Route-layer resolution of unresolved CAD claims on durable create and mutate.
- Browser: schema version 3, unresolved `cad_face_target` on STEP clicks, no CAD
  `entity_ids`, canonical source-tag comparison, v3-safe rendering, specific
  problem-code messages, live selection evidence.
- Executable browser harness and non-skippable frontend CI coverage.

## Out of scope

- R5 meshing and R6 CAD-to-mesh mapping.
- Any change to the geometry fingerprint algorithm or identity authority.
- Any migration or DDL change. Alembic head stays `0005`, SimulationIntent stays
  schema version 3, `API_CONTRACT_VERSION` stays 1.

## Done when

A viewer-created CAD region is resolved by the backend, confirmable, and
byte-identical after restart and reopen; source replacement reports an invalid
viewer binding without rebinding; and no browser path emits CAD `entity_ids`.
