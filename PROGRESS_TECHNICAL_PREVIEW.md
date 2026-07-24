# Technical-preview progress

## Task 16 — Adopt technical-preview governance and freeze V1

**Status:** IN PROGRESS — implementation and validation complete; pending
independent review and the user-authorized final commit.

### Scope and authority

- `release-goal.md` is preserved without semantic change as the authoritative release
  definition.
- `TECHNICAL_PREVIEW_PLAN.md` is the active execution authority for Tasks
  16–45.
- `docs/roadmap/PRODUCT_V2_ROADMAP.md` is preserved unchanged as non-blocking
  future direction.
- `sprint-goal.md`, `EXECUTION_PLAN.md`, and `PROGRESS.md` remain frozen V1
  historical records.
- Task 16 contains governance and evidence changes only. Task 17 and all product
  implementation remain unstarted.

### Takeover

- Date: 2026-07-24, Europe/Berlin.
- Starting branch: `main`.
- Task branch: `task-16-technical-preview-governance`.
- Starting `HEAD`: `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.
- Baseline commit subject: `Task 15: complete evaluated intent workflow`.
- Remote: `origin` at `https://github.com/jrHoss/sim-intent.git`.
- Initial untracked entries were exactly:
  `TECHNICAL_PREVIEW_PLAN.md`, `docs/roadmap/`, and `release-goal.md`.
- Tracked application, test, fixture, example, and historical-evidence files had
  no worktree diff.
- The pre-Task-16 `CLAUDE.md` conflict was explicit: it still made
  `EXECUTION_PLAN.md` authoritative and prohibited meshing/solver work.
  Task 16 replaces future-work governance while preserving those rules as the
  frozen V1 boundary.

### Baseline tag

- Local annotated tag: `demo-v1`.
- Tag target:
  `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.
- Tag type: annotated Git tag object.
- Annotation: `V1 demo baseline: completed Tasks 1-15`.
- The tag was created locally only. No remote tag or branch was created or
  pushed.

### Runtime versions

- Operating system: Microsoft Windows NT 10.0.26200.0.
- PowerShell: 5.1.26100.8875.
- Git: 2.50.0.windows.2.
- Python: 3.13.2.
- Node.js: 22.14.0.
- gmsh: 4.15.2.
- meshio: 5.3.5.
- FastAPI: 0.139.2.
- Uvicorn: 0.51.0.
- Pydantic: 2.13.4.
- OpenAI Python SDK: 2.46.0.
- NumPy: 2.5.1.
- SciPy: 1.18.0.
- pytest: 9.1.1.
- httpx: 0.28.1.
- CalculiX `ccx`: not installed; the existing optional solver smoke remains an
  expected skip and no solver is executed by Task 16.

### Frozen hashes

All values are SHA-256 over exact file bytes.

| Artifact | SHA-256 |
|---|---|
| `release-goal.md` | `370c7148e304ccc2cccd2fa839506ca4fc4867906489703a42b3ac6e155592c5` |
| `docs/roadmap/PRODUCT_V2_ROADMAP.md` | `ac89eae90cea70aa758eadb2c678ad336d27c66d3c78e497f8aab08fe51e1d1a` |
| `PROGRESS.md` | `abcfeea0b9e81d7cd9df0ecf97672e422243697828e48840d041ec0065a6d8f7` |
| `EXECUTION_PLAN.md` | `0de6023078119fd148ab8bf23e48ad892aed5cbc7ccf4f66d62378ddbb02f27a` |
| `fixtures/bracket.step` and `tests/fixtures/bracket.step` | `d81d158aa3b0a5464407496bd1782eba375f853e870fba6edd8cf485825f3c90` |
| Checkout `fixtures/bracket_expected.json` and `tests/fixtures/bracket_expected.json` (700 bytes, LF) | `e8fb94e02a878626350df51439f718235448871733faf7644e1156d0b8f29971` |
| `git archive` versions of both `bracket_expected.json` copies (733 bytes, CRLF) | `e2fc8506ef80ea311ebbd359d4c7e61d814526578c97df615774ebff88633982` |
| `fixtures/plate_hole.step` and `tests/fixtures/plate_hole.step` | `446cf12fed1139d2bfae5e483c1c34905b1444a8d05154a6bd972f1eaa214712` |
| `tests/golden/bracket_abaqus.py` | `7ed6c5dc5d9e19ed6c9c6e70065f162e08f1c4418afee362d14a9a825f56e3ed` |

### Frozen evaluation evidence

- Canonical `eval.schema.manifest_hash` over 15 cases:
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`.
- Sorted exact-file aggregate for `eval/cases/` (15 files):
  `f6fb2dab0a4ee15d5a21e329b1ab067e75c7c294fb0f63ceeec165a4fe1d3e3f`.
- Sorted exact-file aggregate for `eval/replay/` (15 files):
  `e2d21ed8d02fda9e02e735ffbceaeccda5b10d72452a3887669e669f51b108fc`.
- Sorted exact-file aggregate for `eval/fallback/` (15 files):
  `13935335d88c678c127f83d92a0ffac8d91c5418956c41636c61379f2abf5033`.
- Aggregate algorithm: sort files by filename, encode one UTF-8 line per file
  as `<repo-relative-path>  <lowercase-file-sha256>`, terminate every line with
  LF, then SHA-256 the complete manifest bytes.
- Final LIVE report remains separately recorded as 15/15 in
  `eval/results.{md,json}`.
- Deterministic REPLAY remains separately recorded as 15/15 in
  `eval/results-replay.{md,json}`.
- REPLAY, fallback, and fixtures are not accepted as LIVE evidence.

### Validation commands and results

- `.\.venv\Scripts\python.exe scripts\check_env.py` → `ENV OK`.
- `.\.venv\Scripts\python.exe -m pytest tests --collect-only -q` →
  318 tests collected.
- `.\.venv\Scripts\python.exe -m pytest tests -x -q` → 317 passed,
  1 optional `ccx` test skipped, 1 known pytest-cache permission warning, in
  10.09 seconds. The malformed-STEP negative test emitted its expected parser
  diagnostic.
- `.\.venv\Scripts\python.exe -m pytest tests\test_eval.py -q` → 59 passed,
  1 known pytest-cache permission warning, in 6.04 seconds.
- `.\.venv\Scripts\python.exe eval\run.py --replay` → REPLAY 15/15,
  13 PASS, 2 PASS_AFTER_CLARIFICATION, 0 FAIL. The command’s revision-dependent
  report rewrite was discarded; the frozen tracked report bytes were restored
  exactly.
- `node --check app\static\app.js` and
  `node --check app\static\audit.js` → both passed.
- `git archive --format=tar 154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`
  produced a 125-file clean archive with SHA-256
  `5b3e222474337edb860fd06749bff26cbb9ce896b7ae35d95a382edb55b2b7cd`.
- Two independently generated baseline archives had that same SHA-256
  byte-for-byte.
- In the extracted archive, using the repository virtual-environment
  interpreter, `python scripts\check_env.py` → `ENV OK`.
- The raw archive intentionally has no `.git` directory. Its full suite reached
  55 passes before the single test
  `test_raw_fixture_hashes_match_git_archive_and_reject_different_bytes`
  failed because that test invokes `git archive HEAD`. The same test passed in
  the normal 317-pass repository run. Running the archive suite with only that
  Git-metadata-dependent self-test deselected produced 316 passed, 1 optional
  `ccx` skip, and 1 deselection in 7.97 seconds.
- In the extracted archive with `OPENAI_API_KEY` explicitly absent,
  `eval\run.py` returned expected exit code 2 and wrote LIVE/UNAVAILABLE with
  no score and the explicit statement that no replay score was substituted.
- In the extracted archive, `eval\run.py --replay` returned REPLAY 15/15,
  separately labeled.
- Clean-archive raw STEP and golden Abaqus artifact hashes matched the recorded
  checkout hashes. The JSON ground-truth fixture has CRLF bytes in `git archive`
  and LF bytes in this Windows checkout because it is not marked `-text`;
  both exact hashes are recorded above. Its parsed content is unchanged.
- `git diff --check` → passed after whitespace-only normalization removed the
  plan's trailing Markdown hard-break spaces and the release goal's extra blank
  line at EOF while preserving its normal final newline.
- Application, test, dependency, frozen fixture/evaluation, and Task 17 artifact
  diff scans → no changes.
- Private-key/token, credential-assignment, and absolute-host-path scans over
  all Task 16 documents → no findings.
- Final authority-document hash recheck matched the recorded
  `release-goal.md` and product-roadmap hashes exactly.
- Final local-tag check returned annotated object type `tag` and exact peeled
  target `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.
- Temporary archive/cache validation outputs were removed after evidence was
  captured.

### Governance and CI expectations established

- `release-goal.md` → active release definition;
  `TECHNICAL_PREVIEW_PLAN.md` → execution authority;
  `PROGRESS_TECHNICAL_PREVIEW.md` → evidence ledger;
  product roadmap → non-blocking future direction.
- Dedicated task branches, strict task/dependency order, single-task scope,
  independent review, and user-authorized protected-branch/tag operations are
  required.
- Focused, affected, full-regression, JavaScript/browser where applicable,
  integrity, dependency, secret, scope, and `git diff --check` evidence must be
  recorded.
- Task 18 owns pinned environments and bounded CI timeouts. Task 16 does not
  install dependencies or implement CI/product behavior.

### Known frozen-baseline limitations

- Project/setup state is process-memory only and does not survive backend
  restart.
- The frontend is vanilla JavaScript with global DOM/Three.js lifecycle state;
  there is no React frontend or browser execution/visual-regression suite.
- Uploads/parsers lack product-grade size, process, memory, time, and Gmsh
  concurrency containment.
- Python dependencies are not locked and there is no supported Linux
  container/package contract.
- Natural-language material definitions are unsupported; the V1 demonstration
  steel path is an explicitly reviewed prototype assumption.
- STEP-to-Abaqus uses unsafe positional face mapping and must remain blocked in
  the technical-preview path until verified mapping exists.
- The CalculiX adapter emits an appendable INP fragment, not a complete managed
  execution deck.
- `ccx` is not installed in the captured environment.
- No production meshing, solver worker, result parser, numerical validation,
  persistent project, reproducibility bundle, connected runner, or SaaS
  capability exists at the V1 baseline.

### Review state

- No final Task 16 commit has been created.
- Independent review and user inspection of the complete diff remain required.
- A clean post-commit worktree cannot be claimed before that review/commit.
- Task 17 has not started.
