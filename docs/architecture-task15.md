# Task 15 architecture: evaluated intent orchestration

Task 15 adds a narrow orchestration layer around Tasks 11–14. It does not add a second query engine, unit converter, validator, session store, or export implementation.

## End-to-end boundary

```text
instruction + optional viewer clicks
  -> Task 11 Interpreter.interpret
  -> typed query operations (no entity IDs)
  -> Task 12 GroundingEngine
  -> proposed regions or one ClarificationRequest
  -> Task 7 semantics and Task 1 solver-neutral IR
  -> Task 10 SelectionSessionStore
  -> viewer highlights and Task 13 audit/validation
  -> engineer confirmation and assumption decisions
  -> Task 14 export gate and adapter
```

`app/orchestration.py` is the shared bridge used by both the FastAPI application and `eval/harness.py`. Replay replaces only the Task 11 network transport; it continues through the same interpreter validation, grounding engine, semantics, IR, validation, state transitions, and export adapter as live mode.

## Model-generated

The configured OpenAI model may generate only:

- supported Task 6 query operations;
- a semantic target description;
- a supported boundary-condition or load type;
- component names, direction words, and value-plus-unit strings.

The structured schema and recursive guard reject entity-ID fields, face/node/element IDs in prose, and NSET/ELSET names. The browser never calls OpenAI directly.

Material definitions were not included in the Task 11 wire contract or the
Task 15 evaluation scope. An instruction containing explicit material
properties is therefore rejected before any provider call with structured code
`unsupported_material_input`; it is never retried as a generic schema failure
and no session intent is created. The supported Task 15 path is deliberately
narrow: a gravity proposal receives the reviewed demonstration-steel density
7850 kg/m^3, stored as 7.85e-9 tonne/mm^3, plus a pending unit-critical
assumption. The engineer must explicitly accept that assumption. A non-gravity
proposal does not require or invent density.

## Deterministic

Python owns:

- STEP parsing, model hashes, face inventories, labels, adjacency, and hole classification;
- query execution, candidate scores, score margins, entity IDs, and the bracket fillet exclusion;
- click/inventory binding and text/click conflict detection;
- N/kN/MN, Pa/kPa/MPa, and displacement conversion through `ground/semantics.py`;
- direction vectors and standard-gravity conversion;
- assumptions emitted by semantics rules;
- solver-neutral IR construction and provenance;
- Task 13 validation and export eligibility;
- confirmation and assumption state transitions;
- Task 14 Abaqus Python generation;
- gravity-density validation and deterministic material-density emission.

Session accumulation also has one deterministic IR-level guard: before a new
grounded condition is appended, its canonical signature is compared with the
stored conditions. The signature contains the grounded entity-ID set (or the
whole-model target for gravity), condition/load type, normalized constrained
components, normalized vector or magnitude, relevant typed parameters, and the
canonical internal unit. Exact matches do not add a condition or an otherwise
unused region. Source wording, fixture names, region IDs, and fuzzy text
similarity are deliberately excluded. Distinct conditions on the same entities
remain distinct, and duplicate detection never confirms an existing region.
The API reports the omitted instruction in `notices`, which the instruction
panel renders visibly.

Whole-model gravity still executes and records the real deterministic query, but it does not promote an arbitrary face into the IR because the existing gravity schema permits `region_ref=null`.
Validation blocks every gravity intent whose assigned material lacks positive,
finite density. The Abaqus adapter emits one `material.Density(...)` for its
single whole-solid material assignment; the CalculiX adapter emits `*DENSITY`.

Qualitative fixed-displacement components use the same Task 7 model convention
as directional loads: vertical motion maps to Y unless the instruction names an
explicit axis, and that choice is a reviewable critical assumption. Vague
lateral targets such as left/right/front/back “side” are not promoted to exact
extreme-face labels. They remain broad planar queries so the existing
candidate-margin logic can return a real clarification; explicit phrases such
as “left face” may still use the deterministic label.

## Human-controlled

The engineer owns:

- viewer clicks;
- selection of one returned clarification candidate;
- confirmation or rejection of every proposed region;
- acceptance or rejection of assumptions;
- the final export decision.

A clarification choice creates a proposed region with click provenance. It never confirms that region automatically. Only one clarification is permitted per instruction.

## Evaluation and fallback separation

- `python eval/run.py` is LIVE-only. It requires server-side OpenAI configuration and never substitutes replay. If configuration is absent, `eval/results.md` records LIVE/UNAVAILABLE with no score.
- `python eval/run.py --replay` uses checked-in, sanitized typed operations. Reports and UI are labeled REPLAY.
- `eval/fallback/*.json` includes the typed response, grounding, clarification evidence, proposed IR, provenance, and model hash. Every fallback region remains proposed.
- Expected entity IDs exist only in `eval/cases/*.json` and result assertions. They are never placed in a prompt, replay response, production query, or model-generated field.

The first scored LIVE result is retained as `eval/results-live-initial.*`
(13/15, with one axis-semantic and one ambiguity-unflagged failure). The final
LIVE result is `eval/results.*` (15/15, zero failures), while
`eval/results-replay.*` remains a separately labeled deterministic 15/15
regression. All use the unchanged frozen manifest
`47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`.

## Known limitations

- No automatic meshing or mesh repair.
- No solver execution, convergence analysis, result reading, or numerical result validation.
- No contact, nonlinear material, large-deformation, thermal, or dynamic analysis.
- Click evidence is supported; arbitrary screenshot, drawing, or computer-vision understanding is not.
- The Abaqus adapter assumes OCC source face tag `n` maps to imported `part.faces[n-1]` for the exact content-hashed STEP file.
- The Abaqus artifact has not been executed in a real Abaqus environment.
- The optional CalculiX test requires an installed `ccx` executable.
- Live LLM evaluation cannot be scored without server-side OpenAI configuration.

This prototype generates explicit simulation intent and inspectable solver artifacts. It does not perform FEA.
