# Three-minute Task 15 demonstration

The demonstration shows intent interpretation, deterministic grounding, review, and artifact generation. It does not run a solver or perform FEA.

## Preparation

From the repository root in PowerShell:

```powershell
python eval/run.py
python eval/run.py --replay
.\.venv\Scripts\python.exe -m uvicorn app.server:app --host 127.0.0.1 --port 8765
```

The first command is the required live evaluation. It requires `OPENAI_API_KEY` in the server environment and fails clearly otherwise. Never paste or store the key in repository files. The second command is deterministic replay and is always labeled REPLAY.

Open `http://127.0.0.1:8765/`. Use:

- model: `tests/fixtures/bracket.step`;
- primary instruction: `Fix both bolt holes and apply a total downward force of 5 kN to the top flange.`;
- ambiguity instruction: `Fix the left side.`;
- fallback case: `bracket_combined_export`;
- generated artifact: `bracket_abaqus.py`.

## Timed live path

### 0:00–0:25 — Load and inspect

1. Click **Load model** and choose `tests/fixtures/bracket.step`.
2. Orbit the bracket and point out selectable per-face geometry and the axes.

Expected state: 12 selectable faces, an empty intent audit, and a LIVE badge in the instruction panel.

### 0:25–0:55 — Interpret natural language

1. Enter the primary instruction.
2. Leave **Use current face clicks as evidence** checked with no current clicks.
3. Click **Interpret**.

Expected state: typed model output contains query operations and engineering payloads but no entity IDs. Deterministic grounding proposes bolt-hole faces 11/12 and top face 4. Face 3, the cylindrical fillet trap, and wall-hole face 10 are excluded from the bolt-hole region. The force is `[0, -5000, 0]` N and remains a resultant surface force, not pressure.

### 0:55–1:25 — Inspect provenance and highlights

Show:

- proposed highlights;
- explicit entity IDs in the audit panel;
- verbatim source instruction;
- `semantic_geometry_query` selection method and confidence;
- pending unit/direction assumptions;
- invalid/blocked export state before review.

The entity IDs come from deterministic query execution, not from the model.

### 1:25–2:05 — Review and export

1. Confirm both proposed regions.
2. Accept each critical assumption.
3. Verify validation becomes valid and export eligibility becomes eligible.
4. Select **Abaqus Python (STEP)** and click **Export artifact**.
5. Open `bracket_abaqus.py` as text.

Expected state: the artifact contains fixed displacement on faces 11/12 and a 5000 N total force on face 4. It states that no job is created or submitted. No generated JSON or Python is manually edited.

### 2:05–2:35 — Show ambiguity and correction

1. Enter `Fix the left side.` and click **Interpret**.
2. Show the actual amber candidate sets returned by Task 12.
3. Select one candidate, or click the intended face and repeat using current click evidence.

Expected state: the system does not silently choose. One clarification resolves the region, but its status remains proposed until the engineer confirms it. This demonstrates correction without rewriting the prompt.

### 2:35–3:00 — Explain the boundary

State explicitly:

- The model creates typed unresolved operations, not entity IDs.
- Geometry, units, validation, and export are deterministic.
- The engineer controls clarification, confirmation, assumptions, and export.
- The system generates intent and an inspectable artifact; it does not mesh, execute Abaqus, or validate FEA results.

## Click-assisted path

1. Click face 5.
2. Enter `Prevent vertical motion on this face.`
3. Click **Interpret**.

Expected state: face 5 remains the selected evidence, the proposed region uses `user_click` provenance, and only component `y` is constrained. A text/click conflict must return clarification rather than silently replacing either source.

## Additive-session duplicate retest

Use a fresh server process and load `tests/fixtures/bracket.step`.

1. Enter `Fix both bolt holes.` and click **Interpret**.
2. Verify one proposed region contains faces 11 and 12 and has one fixed-displacement BC.
3. Without confirming that region, enter `Fix both bolt holes, apply a total downward force of 5 kN to the top flange, and apply gravity in negative Z.` and click **Interpret**.
4. Verify the instruction panel visibly reports `Equivalent condition already exists; duplicate was not added.` and includes the new instruction as provenance.
5. Verify the faces 11/12 region still appears once, its review state is still proposed, and it still has one fixed-displacement BC. Verify one new top-flange region and one resultant force were added.
6. Submit `Apply gravity in negative Z.` once more. Verify another duplicate notice appears and no region is created for gravity.
7. Confirm the two proposed face regions, accept every critical assumption, select **Abaqus Python (STEP)**, and export.
8. Open the artifact as text. Verify it contains exactly one `model.Gravity(` call and that call targets `instance.sets['ALL_SOLID_CELLS']`.

The session intent must contain exactly one gravity load with `region_ref=null`.
No duplicate submission should confirm any region automatically.

## Material and gravity safety retest

Use a fresh server session and load `tests/fixtures/bracket.step`.

1. Submit `Use steel with Young's modulus 210 GPa, Poisson's ratio 0.3, and density 7850 kg/m³. Fix both bolt holes, apply a total downward force of 5 kN to the top flange, and apply gravity in negative Z.`
2. Verify the request returns HTTP 422 with code `unsupported_material_input` and a visible explanation that natural-language material definitions are outside Task 15.
3. Verify the response has no retry count, no provider retry occurred, no intent was created, and audit remains unvalidated.
4. In the same clean session, submit `Fix both bolt holes, apply a total downward force of 5 kN to the top flange, and apply gravity in negative Z.`
5. Verify the proposed IR contains one steel material with `density_tonne_per_mm3=7.85e-9`, gravity has `region_ref=null`, and the audit shows a pending unit-critical assumption stating `density=7850 kg/m^3 = 7.85e-9 tonne/mm^3 internal`.
6. Confirm the two proposed face regions but do not accept the density assumption. Verify export remains blocked.
7. Accept the density assumption and every other critical assumption, then export Abaqus Python.
8. Verify the artifact contains exactly one `material.Density(table=((7.85e-09,),))`, exactly one `model.Gravity(`, and gravity targets `instance.sets['ALL_SOLID_CELLS']`.

As a non-gravity control, start a fresh session and submit `Fix both bolt holes.`
No density is required for that intent; normal region confirmation remains the
only material-independent review gate.

## LIVE axis and ambiguity regression retest

Use a fresh server session with `OPENAI_API_KEY` configured and load
`tests/fixtures/bracket.step`.

### Vertical click

1. Click face 5 and verify the viewer shows `face_5` as the current selection.
2. Enter `Prevent vertical motion on this face.` and leave **Use current face clicks as evidence** enabled.
3. Click **Interpret**.
4. Verify the proposal contains only face 5, uses `user_click` provenance, remains `proposed`, and has `fixed_displacement.components=["y"]`.
5. Verify the audit includes the pending model-axis assumption that vertical motion means the Y displacement component. No X or Z component may be constrained.

### Directional side ambiguity

1. Start another fresh server session, reload `bracket.step`, enter `Fix the left side.`, and click **Interpret**.
2. Verify the UI shows multiple actual candidate buttons and amber candidate highlights. It must not create a region or silently choose `left_face` before the engineer responds.
3. Choose the candidate containing face 1.
4. Verify the resulting region contains face 1, preserves the original source instruction and clarification evidence, and remains `proposed`.
5. Verify export remains blocked until the engineer explicitly confirms that region.

Recorded final manual evidence (user-observed): both regressions passed. The
vertical-click path retained face 5 with `user_click` provenance, constrained
only Y, displayed the pending Y-axis assumption, remained proposed, and kept
export blocked. The left-side path displayed multiple candidates, created no
automatic region, recorded the face-1 clarification through `user_click`,
preserved the original instruction, remained proposed, and kept export blocked
until confirmation.

## Final evaluation evidence

- Initial scored LIVE report, retained in `eval/results-live-initial.*`: 13/15;
  12 PASS, 1 PASS_AFTER_CLARIFICATION, 2 FAIL.
- Initial failures: `bracket_vertical_click` (`unit`) and
  `bracket_left_side_clarify` (`ambiguity-unflagged`).
- Final LIVE report in `eval/results.*`: 15/15; 13 PASS,
  2 PASS_AFTER_CLARIFICATION, 0 FAIL.
- Deterministic REPLAY report in `eval/results-replay.*`: 15/15 and always
  labeled REPLAY.
- Frozen manifest SHA-256:
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`.

No solver job was created or executed during evaluation or manual verification.

## Fallback/replay path

If OpenAI is unavailable:

1. Use the **Replay fallback** selector.
2. Choose `bracket_combined_export` and click **Load REPLAY case**.
3. Verify the badge says REPLAY.
4. Continue through the same audit, confirmation, assumption, validation, and export controls.

Fallback data is model-hash-bound and validated server-side. It does not confirm regions or accept assumptions automatically and must never be described as live model performance.

For the ambiguity fallback, load `bracket_left_side_clarify`. For the second model, upload `tests/fixtures/plate_hole.step` and load `plate_hole_pressure_pa`; face 7 must carry 2 MPa pressure.

## Recovery

### OpenAI is not configured

The live endpoint returns HTTP 503 with code `provider_not_configured`. The instruction panel displays the safe error, clears its busy state, preserves current clicks, and leaves the session unchanged. Use a REPLAY case or restart the server after setting `OPENAI_API_KEY` locally.

### Provider is temporarily unavailable

The endpoint returns a structured HTTP 503 with code `provider_unavailable`. Check server-side configuration and provider availability, then retry. Do not put credentials in the browser.

### Browser loses the event stream

Hard-refresh the page, re-upload the same model bytes, and reopen the audit. The content-addressed model ID reconnects to the existing in-memory session while that server process remains running. If the server restarted, load the fallback case again and repeat review transitions.

### A stale highlight remains

Reload the page and model to reconstruct highlights from server audit/session state. Backend validation and export gates remain authoritative regardless of client display state.

## What not to claim

Do not claim that the system:

- performed FEA or ran Abaqus/CalculiX;
- generated or repaired a mesh;
- verified stresses, displacements, convergence, or physical correctness;
- supports contact, nonlinear, thermal, or dynamic analysis;
- understands arbitrary marked-up screenshots;
- has demonstrated live LLM accuracy when only REPLAY results are available.
