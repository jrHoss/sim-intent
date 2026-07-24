# Technical-preview progress

## Task 16 — Adopt technical-preview governance and freeze V1

**Status:** COMPLETE — MERGED through pull request #1 on 2026-07-24.

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

### Completion and merge evidence

- Task 16 implementation commit:
  `0bfd6921f69c1cb93c9a7ac91b46287250aff9ce`.
- Task 16 merge commit:
  `a8092dcfd688a47a882ab4246fa96331cd790475`.
- Pull request: #1.
- Independent review: APPROVE, no remaining findings.
- Task 16 was merged with a clean worktree.
- Task 17 was not started.

### Baseline tag

- Annotated tag: `demo-v1`.
- Tag target:
  `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.
- Tag type: annotated Git tag object.
- Annotation: `V1 demo baseline: completed Tasks 1-15`.
- The tag remains on the exact V1 baseline and was not moved by the Task 16
  implementation or merge.

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
  expected skip. Its absence is a future Task 18 environment dependency, not a
  Task 16 failure, and no solver was executed by Task 16.

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
- Task 17's accepted repository-audit dependency is
  [`docs/audits/product-v2-repository-audit-2026-07-23.md`](docs/audits/product-v2-repository-audit-2026-07-23.md).
  It consolidates the already accepted repository evidence and is not a new
  audit.

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

### Completion state

- Task 16 is complete and merged.
- The accepted implementation and merge commits are recorded above.
- The V1 baseline remains fixed at the annotated `demo-v1` tag target.
- Task 17 and Task 18 have not started.

---

## Task 17 — Approve release architecture and decision-complete ADRs

**Status:** COMPLETE — APPROVED FOR COMMIT.

### Scope and takeover

- Date: 2026-07-24, Europe/Berlin.
- Branch: `task-17-architecture-adrs`.
- Starting and current pre-review `HEAD`:
  `61d659551da9da2629a33f88e8aa9e2a68d6a175`.
- `HEAD` is merge pull request #2,
  `Task 16: reconcile completion evidence`, with parents
  `a8092dcfd688a47a882ab4246fa96331cd790475` and
  `38ac82d142daf04318abb886d3233fd5812d4809`.
- Initial worktree: clean.
- Task 16 status was verified as `COMPLETE — MERGED`.
- The accepted audit dependency exists at
  [`docs/audits/product-v2-repository-audit-2026-07-23.md`](docs/audits/product-v2-repository-audit-2026-07-23.md)
  and identifies itself as accepted evidence consolidation.
- The annotated `demo-v1` tag remains peeled to
  `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`.
- `CLAUDE.md`, `release-goal.md`, `TECHNICAL_PREVIEW_PLAN.md`, this progress
  ledger, the preserved Product V2 roadmap, the accepted audit, and the
  complete tracked repository structure were read before editing.
- Task 17 changes architecture and evidence documents only. No frontend,
  persistence, geometry, meshing, solver, product behavior, dependency,
  lockfile, OCI, or CI implementation was started.

### Human-approved decisions

The human Task 17 decision on 2026-07-24 approved:

- the active supported envelope unchanged, with formal change-control for any
  expansion;
- legacy `/` through Task 44, `/legacy` rollback, `/app-v2` preview, and
  Task-45-only final cutover of `/` after all gates and human approval;
- backend versioned aggregates as engineering truth and React limited to
  drafts, layout, viewer lifecycle, and read-only server cache;
- a local modular monolith with SQLite, local SHA-256 content-addressed
  artifacts, and isolated parser/solver child processes;
- SQLAlchemy 2 repositories, Alembic migrations, enabled SQLite foreign keys,
  and explicit transaction ownership;
- UUIDv4 domain IDs, immutable revisions/hashes, and content hashes kept
  separate from domain identity;
- `/api/v1`, integer schema versions, backend OpenAPI authority,
  `openapi-typescript`/`openapi-fetch`, checked-in generation, and drift checks;
- `uv`/`uv.lock`, npm/`package-lock.json`, and a versioned Debian-stable OCI
  baseline, with actual versions and installation deferred to Task 18;
- startup-fixed production, LIVE evaluation, REPLAY, and test modes, with
  physical production exclusion of replay/fallback/fixture behavior;
- fresh bounded no-network parser/Gmsh subprocesses, safe temporary
  directories, deterministic cleanup, and one shared Gmsh slot initially;
- one durable application-owned JobService and an isolated local no-network
  CalculiX subprocess with immutable packages, argument vectors, limits,
  cancellation, process-group cleanup, default concurrency one, and restart
  reconciliation;
- RFC 9457 `application/problem+json` with a stable application code,
  correlation ID, retryability, and safe typed details;
- capability states `supported`, `unsupported`, `unavailable`, `blocked`,
  `insufficient_evidence`, and `stale`, with every non-`supported` state
  failing closed and emitting no solver artifact or job;
- per-aggregate single-writer migration, no dual writes, no volatile-session
  migration, documented compatibility reads only, forward migrations with
  pre-upgrade backup, and rollback by restoring the previous application and
  database backup;
- Release Owner Maein, subject to his acceptance; Technical Review Owner Ahmed
  Yassin; a separate read-only reviewer before each task commit; and a named
  Security Review Owner as a Task 18 approval entry gate.

### Architecture evidence created

The authoritative Task 17 index is
[`docs/architecture/technical-preview/README.md`](docs/architecture/technical-preview/README.md).
It links:

- the release architecture;
- state-writer and ownership matrix;
- route matrix and deployment decision;
- capability matrix;
- migration and rollback rules;
- Task 18–45 dependency traceability;
- threat model;
- risk register;
- release requirement/evidence ownership;
- nine accepted ADRs covering every Task 17 decision topic.

The architecture set contains 19 Markdown files and 82,051 exact bytes. Its
sorted manifest SHA-256 is
`eaabc59d89f4f93dd732ea02854dbc99b9232b2ebae5dd05f0bfa599af3012ca`.
The manifest algorithm sorts repository-relative paths, emits
`<path><two spaces><lowercase file SHA-256>` with LF after every entry, and
SHA-256 hashes the UTF-8 manifest without a byte-order mark.

### Dependency and ownership validation

- Tasks 18–45 were walked in order against their exact declared dependencies.
- Every task consumes only earlier active tasks or approved pre-existing
  evidence.
- No active task depends on remote/customer runners, connected Abaqus
  execution, HPC, classifiers, assemblies/contact, advanced physics,
  multi-user collaboration, SaaS, or another post-preview capability.
- The state matrix records 36 unique state rows. Every persistent state has one
  authoritative persistence writer; transient React, viewer, and query-cache
  state is explicitly non-authoritative.
- The route matrix has one consistent timeline: Tasks 18–44 preserve legacy
  `/`; Task 45 alone may approve V2 `/`; `/legacy` and `/app-v2` retain their
  rollback/compatibility roles.
- The migration walkthrough verifies relational, payload, generated-client,
  aggregate-cutover, artifact-format, and route-cutover ownership with no dual
  write or frontend migration.
- The capability walkthrough verifies all six approved states, their recovery
  boundary, and the rule that only `supported` may emit an artifact or job.
- Release demonstration, supported-envelope, product-invariant, workstream,
  and quantitative-gate matrices assign every active release requirement to
  evidence-producing tasks and a final gate.

### Manual review checklist

- [x] Read all nine created ADRs completely after drafting.
- [x] Read all ten created architecture, matrix, threat, risk, and index
  documents completely after drafting.
- [x] Confirmed every human decision records options, the selected
  recommendation, consequences, and downstream blocking impact.
- [x] Scanned the complete Task 17 architecture set for `TBD`, `TODO`, `FIXME`,
  `XXX`, “to be decided,” “decision pending,” and “unresolved decision”; no
  findings.
- [x] Walked the development and final-gate route matrices end to end and
  confirmed Task 44 cannot activate cutover.
- [x] Walked all 36 state-owner rows and corrected artifact-output and
  AuditEvent wording so temporary workers/aggregates do not appear to be
  second persistence writers.
- [x] Walked the migration sequence from legacy compatibility through
  single-writer persistent ownership, payload migration, database backup,
  route cutover, and rollback.
- [x] Walked the capability matrix from input/import through setup, mapping,
  artifact, local execution, results, Abaqus export-only validation, and all
  explicit exclusions.
- [x] Walked Tasks 18–45 end to end and confirmed no forward or post-preview
  dependency.
- [x] Verified every active release demonstration, envelope requirement,
  product invariant, workstream, and quantitative gate has an owner.
- [x] Validated every Markdown link and local file path in the architecture
  set; no broken link.
- [x] Verified all active task rows 18–45, all ADR-001–ADR-009 references, all
  six capability-state references, route assertions, and risk/threat
  cross-references.
- [x] Reviewed the threat model against upload, parser/Gmsh, stale write,
  mapping, artifact, solver, mode, egress, error, migration, and scope threats.
- [x] Confirmed post-preview mentions are exclusions or risk controls, never
  dependencies.
- [x] Separate independent read-only review completed on 2026-07-24 with an
  APPROVE verdict; see the independent-review record below.

### Validation commands and results

- `.\.venv\Scripts\python.exe scripts\check_env.py` → `ENV OK`.
- `.\.venv\Scripts\python.exe -m pytest tests -x -q` → 317 passed, one optional
  `ccx` test skipped, and the known pytest-cache permission warning, in 11.94
  seconds.
- `node --check app\static\app.js` and
  `node --check app\static\audit.js` → passed.
- Markdown-link validation over all 19 Task 17 files →
  `MARKDOWN_LINKS_OK`.
- Active dependency-row validation → `ACTIVE_TASK_ROWS_18_45_OK`.
- Capability vocabulary validation → `CAPABILITY_STATES_OK`.
- Route-gate validation → `ROUTE_GATE_ASSERTIONS_OK`.
- State-row uniqueness validation → `STATE_ROWS_UNIQUE_OK count=36`.
- Unresolved-marker scan → no findings.
- Route and post-preview reference scans were manually reviewed; all matches
  agree with the approved route timeline and exclusion boundary.
- `git diff --check` → passed after Markdown hard-break and trailing-EOF
  whitespace normalization.
- Combined architecture/progress Markdown-link validation →
  `ALL_DOCUMENT_LINKS_OK`.
- Refined private-key/token, credential-assignment, and absolute-host-path scan
  over all Task 17 documents → `SECRET_AND_HOST_PATH_SCAN_OK`.
- Diff scope scan → `TASK17_SCOPE_SCAN_OK`; changed paths are only the Task 17
  architecture directory and this progress ledger.
- Task 18 artifact scan confirmed that `uv.lock`, `package.json`,
  `package-lock.json`, `frontend/`, `Dockerfile`, and `.github/workflows/` were
  not created.
- The full regression created `.sim_intent_cache/`; its resolved path was
  verified inside the repository and the test-generated directory was removed.
  No generated cache remains.

### Independent read-only review

- Review date: 2026-07-24.
- Reviewer: separate read-only Codex reviewer, independent of the Task 17
  author.
- Verdict: APPROVE.
- Findings: no blocking, major, or minor findings.
- All nine ADRs were confirmed decision-complete and mutually consistent.
- All Tasks 18–45 were confirmed to have valid dependency coverage.
- All 36 state rows were confirmed to have exactly one authoritative
  persistence writer.
- The six capability states were confirmed exact, non-overlapping, and
  fail-closed.
- The changed scope was confirmed limited to this progress ledger and the 19
  architecture Markdown files.
- Task 18 was confirmed not started.

### Risks, blockers, and review state

- `R-REV-01`: CLOSED. The required separate independent read-only Task 17
  review completed on 2026-07-24 with an APPROVE verdict and no blocking,
  major, or minor findings. It no longer blocks the Task 17 commit.
- `R-SEC-01`: a named Security Review Owner is not yet assigned. This does not
  block Task 17 drafting or independent technical review, but it blocks Task
  18 approval.
- Maein’s Release Owner assignment remains subject to his acceptance.
- Architecture controls are decisions, not implementation evidence. Each
  consuming task must prove its controls before claiming support.
- No Task 17 commit or push has been created.
- Task 18 has not started and is not authorized by these documents.

---

## Task 18 — Reproducible backend/frontend environments and bounded CI

**Status:** IMPLEMENTATION COMPLETE — UNCOMMITTED, AWAITING INDEPENDENT
READ-ONLY REVIEW. The two human-directed corrections of 2026-07-24 (runtime-
mode closure authority; reproducible CalculiX source build) are implemented
and recorded in the correction addendum at the end of this section.

### Scope and takeover

- Date: 2026-07-24, Europe/Berlin.
- Branch: `task-18-reproducible-environments`.
- Starting `HEAD`: `2ee6cb2` (merge of pull request #3,
  `task-17-architecture-adrs`).
- Initial worktree: clean. Repository root verified with
  `git rev-parse --show-toplevel`; all work performed inside
  `sim_intent_starter` (the empty outer `.git` directory artifact was not
  used).
- `demo-v1` remains peeled to
  `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d` and was not touched.
- Four stale local `uvicorn` development servers (ports 8766–8769) were
  holding the old `.venv` open and were stopped before the approved
  environment rebuild. They held only process-memory V1 state.
- No commit, stage, push, branch, tag, or Git-configuration change was made.
- Task 19 and Task 24 were not started. No persistence, API/schema
  versioning, React/frontend code, parser worker, solver worker, JobService,
  geometry, or meshing behavior was implemented.

### Human decisions applied (2026-07-24)

- **D0**: Security Review Owner: **Ahmed Yassin**. Independent Task 18
  security evidence must still be reviewed by a separate read-only reviewer
  before commit; the role assignment does not skip that review. `R-SEC-01`
  is thereby resolved as an entry gate; the independent review remains open.
- **D1**: `SIM_INTENT_MODE` unset/empty defaults to `production`; unknown
  values fail startup with a configuration error naming the accepted
  vocabulary; production never exposes REPLAY routes, fixtures,
  substitution, or fallback; the demo documentation now sets
  `SIM_INTENT_MODE=replay` explicitly.
- **D2**: Official Python slim Debian image pinned by immutable digest;
  `requires-python = ">=3.13,<3.14"`; exact patch **3.13.14** selected after
  the clean locked environment reproduced the full-suite baseline;
  `.python-version` contains `3.13.14`; all apt packages install from the
  frozen Debian snapshot `20260720T000000Z` with exact pinned versions.
- **D3**: `scipy`, `typer`, and `rich` removed from the declared dependency
  set after verifying no repository import/script/test/eval usage
  (`git grep` over `*.py`: zero first-party imports). Proven by clean frozen
  install, environment gate, full regression, and REPLAY 15/15. `rich`
  remains in the locked closure only as a transitive dependency of `meshio`.
- **D4**: The Debian `calculix-ccx` package could not be obtained
  reproducibly from trixie (blocker record below, retained as evidence).
  Under the follow-up correction directive, CalculiX **ccx 2.23 is now built
  from the official hash-verified source archives** inside a dedicated
  container builder stage on the unchanged trixie base — no cross-release
  package mixing, no third-party binary. Details in the correction addendum.
- **D5**: `pytest-timeout` default 120 seconds per test
  (`pyproject.toml [tool.pytest.ini_options] timeout = 120`); every CI job
  has `timeout-minutes` as the outer bound. No test needed an exception.
- **D6**: CI runs `node --check` on the two legacy JavaScript files with
  exact Node 22.14.0. No `package.json`, `package-lock.json`, `frontend/`,
  React, Vite, or npm dependency was created.

### Recorded D4 blocker — CalculiX not installable from Debian trixie (superseded by the source-build correction; retained as evidence)

Evidence gathered inside the pinned base image against the frozen snapshot
`20260720T000000Z`:

- `calculix-ccx` is absent from trixie `main`, `trixie-updates`,
  `trixie-security`, and `trixie-backports` (only the documentation package
  `calculix-ccx-test` remains).
- sid has `calculix-ccx 2.21-1`
  (`SHA256 eb34d52c77e37d5401e036678d63ad3476052b1e0ca68e65dfdd000bb0ae43cb`),
  but it depends on `libspooles2.2t64 2.2-16+b1`
  (`SHA256 e82c4173706d831a490439bf5b31dae5194dca07482ac8632bc219514372ac3c`),
  which requires `libopenmpi40 (>= 5.0.10)`; trixie provides `5.0.7-1`, so
  installation would cascade into importing the sid OpenMPI stack.
- bookworm has `calculix-ccx 2.20-1`
  (`SHA256 a448239f3caf5a324f9589a636e595e3e676153489f75843e509decb6809f346`)
  with a bookworm-era library chain (`libarpack2`, `libspooles2.2`), i.e.
  equivalent cross-release mixing in the other direction.

Options that were reported for the human decision: (1) accept `unavailable`;
(2) hash-pinned cross-release artifact chain; (3) build from a hash-pinned
source tarball in a dedicated build stage; (4) re-approve a bookworm base.
The follow-up human correction directive selected option 3 (with cross-
release mixing explicitly prohibited); its implementation and evidence are in
the correction addendum at the end of this section.

### Exact selected versions

- CPython **3.13.14** (`.python-version`, container image, uv-managed
  Windows interpreter). `requires-python = ">=3.13,<3.14"`.
- uv **0.11.32** (local tools environment, container — installed with
  recorded wheel SHA-256 hashes and `--require-hashes` — and CI).
- Direct runtime pins: `fastapi==0.139.2`, `gmsh==4.15.2`, `meshio==5.3.5`,
  `numpy==2.5.1`, `openai==2.46.0`, `pydantic==2.13.4`, `uvicorn==0.51.0`.
- Dev group (PEP 735 `dev`): `pytest==9.1.1`, `httpx==0.28.1`,
  `pytest-timeout==2.4.0`.
- Locked transitive closure: 35 packages in `uv.lock` (authoritative).
- Base image: `python:3.13.14-slim-trixie@sha256:6771159cd4fa5d9bba1258caf0`
  `b82e6b73458c694d178ad97c5e925c2d0e1a91` (single digest, wrapped here for
  line length).
- Debian snapshot: `20260720T000000Z` (deb + deb-security), exact native
  pins: `libgl1=1.7.0-1+b2`, `libglu1-mesa=9.0.2-1.1+b3`,
  `libx11-6=2:1.8.12-1`, `libxcursor1=1:1.2.3-1`, `libxext6=2:1.3.4-1+b3`,
  `libxfixes3=1:6.0.0-2+b4`, `libxft2=2.3.6-1+b4`,
  `libxinerama1=2:1.1.4-3+b4`, `libxrender1=1:0.9.12-1`,
  `libfontconfig1=2.15.0-2.3`, `libgomp1=14.2.0-19`. The list is the
  measured `ldd` closure of the locked gmsh wheel, not a generic list.
- CI tools: Node `22.14.0`; gitleaks `8.30.1`
  (`551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`);
  syft `1.49.0` (linux
  `7aa2f03ee92739cf643279ba3990548b9925d4e22cae13f46831ee62821147fe`,
  windows verified locally
  `6edff6c6e06ddd43ae3b779099653f499a856009786b5375a7cf23aed6b67b1a`).
- GitHub Actions pinned by full commit SHA: `actions/checkout`
  `3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1), `actions/setup-node`
  `820762786026740c76f36085b0efc47a31fe5020` (v7.0.0), `astral-sh/setup-uv`
  `c771a70e6277c0a99b617c7a806ffedaca235ff9` (v9.0.0),
  `actions/upload-artifact` `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`
  (v7.0.1).

### Files created

- `pyproject.toml` — project pins, `dev` group, non-packaged flat uv
  configuration, pytest timeout configuration.
- `uv.lock` — authoritative lock (uv 0.11.32).
- `.python-version` — `3.13.14`.
- `scripts/export_requirements.py` — deterministic hashed
  `requirements.txt` export and byte-exact `--check`.
- `app/runtime_mode.py` — `RuntimeMode`, `resolve_runtime_mode(env)`,
  `RuntimeModeError`.
- `tests/test_runtime_mode.py` — 35 focused tests (resolution vocabulary,
  route matrix, unregistered-404, immutability, multi-app coexistence,
  `/healthz`, LIVE-never-substitutes-REPLAY).
- `Containerfile` — frozen-snapshot base, hash-pinned uv, `runtime` and
  `ci` targets; runtime physically excludes `eval/`, `tests/`, `fixtures/`,
  `examples/`, and development documentation.
- `.dockerignore` — minimal context; deliberately keeps `eval/`, `tests/`,
  `fixtures/` available to the `ci` target.
- `.github/workflows/ci.yml` — six bounded jobs (lock/drift, backend suite,
  REPLAY evaluation, container + SBOM, frontend smoke, hygiene), all
  external references pinned immutably, LIVE never run.
- `docs/environment.md` — supported environment contract, mode matrix,
  clean install, update procedure, CI/SBOM policy, rollback.

### Files modified

- `app/server.py` — `create_app(storage_dir, *, mode=None)` resolves the
  immutable mode once per construction into a closure value that all mode
  consumers read (`app.state.runtime_mode` is a diagnostic copy only),
  registers the two fallback routes only in `replay`/`test`, adds
  `GET /healthz` (`{"status": "ok", "mode": ...}`), and makes the 503
  provider-failure `fallback_available` hint mode-accurate. The module-level
  `app = create_app()` still resolves the environment exactly once at
  process startup. Fallback fixture paths are referenced only inside the
  gated registration, so production import and startup succeed with the
  eval tree physically absent (proven in-container).
- `conftest.py` — forces `SIM_INTENT_MODE=test` before application imports
  so the frozen fallback contract tests keep their routes; the new
  mode-matrix tests construct applications explicitly instead of mutating a
  running application.
- `requirements.txt` — replaced by the generated pinned, hashed export with
  an authority banner (uv.lock is authoritative).
- `scripts/check_env.py` — additive CalculiX presence/version report;
  `ENV OK` remains the final success line and `ccx` absence never fails the
  gate.
- `tests/test_interpreter.py` — one assertion in
  `test_only_openai_provider_dependency_and_runtime_references_remain`
  updated from the exact line `openai` to `openai==<version>` because the
  approved export format now pins versions. This is a consequence of the
  approved format change, not a rebaseline: the assertion's intent
  (only the OpenAI provider dependency remains; the retired provider is
  absent) is unchanged and still enforced.
- `docs/demo.md` — the demonstration now sets `SIM_INTENT_MODE=replay`
  explicitly before starting the server and documents why.
- `CLAUDE.md` — baseline environment facts updated to the locked-environment
  contract (factual update only).
- `PROGRESS_TECHNICAL_PREVIEW.md` — this section.

### Runtime-mode implementation and route matrix

| Mode | Fallback routes | Verified by |
|---|---|---|
| `production` (default when unset/empty) | absent — unregistered, plain 404 | route-table tests, in-container startup checks |
| `live_evaluation` | absent — unregistered, plain 404 | route-table tests |
| `replay` | present, responses always labeled REPLAY | route-table tests, frozen fallback contract tests |
| `test` | present | full suite under `conftest.py` |

- Unknown `SIM_INTENT_MODE` raises `RuntimeModeError` naming the accepted
  vocabulary (startup configuration failure; tested).
- The mode is resolved once per constructed application; registration
  happens only during construction; mutating the stored mode on a built
  application cannot change its routes (tested).
- LIVE provider failure returns a typed 503 with mode-accurate
  `fallback_available` and never substitutes REPLAY in any mode (tested in
  `production` and `replay`).
- `/healthz` returns exactly `{"status": "ok", "mode": <mode>}` (tested for
  all four modes; leak test asserts no paths/keys).

### Dependency and lock evidence

All SHA-256 over exact file bytes:

| Artifact | SHA-256 |
|---|---|
| `uv.lock` | `aa24bf884d0abb01da73e2c1533c6e51a1ec816b8addbac4ad85bf65d12a738e` |
| `requirements.txt` (generated export) | `9de5aaeff26da7d3fd53a921fc91e86e09d8cf3badf23b01d87db3074381ad12` |
| `pyproject.toml` | `f46c6fc7b00521f392157f675f1db09820476cb2442a8587618b3edb12679f96` |
| `.python-version` | `f8faecf2505680716c6279bf2cdec3d5a5ba2ba852f0d7df45d51ac1ce8d9ade` |
| `Containerfile` | `32b21126788b24a5fa0c0aa338c9d2f606044dec1d5345ee0f8b59527ae5e52b` |
| `.github/workflows/ci.yml` | `794d79f128d7d4b76793931652202c483b226eb36414d85f86e1fd01c4a8a2ff` |

Reproducibility comparison (two fully independent
`pip install uv==0.11.32` + `uv sync --frozen --no-dev` runs from the pinned
base image on the same Linux platform):

- `uv pip freeze` SHA-256, both runs:
  `7744d6818b163eec34b6bae19cff8f404e96bbf73cf723c52893bb547828739f`.
- Concatenated sorted dist-info `RECORD` manifest SHA-256, both runs:
  `7e0f4caeb9c43d85253c7a6c0cb81e02b28dba2458b3a45d3f71bb094b3cafee`.
- Byte-identical installations confirmed. (Windows and Linux legitimately
  differ by platform-marker artifacts such as `colorama`; cross-OS byte
  identity is not claimed.)

### Container and native-package evidence

- Runtime image ID:
  `sha256:3d1d11d0fe35219476b69cac6cc4e6f6e0c8be511e97a14eab9b6b63d252772f`.
- CI image ID:
  `sha256:a32107dfd158522a0e958834a82c12ec6fc9f3d090c0d4eff07b73a4ead5bfd8`.
- (Local `docker build` image IDs; registry digests will exist once CI
  pushes are approved — none were made.)
- Installed-package manifest `/opt/native-packages.txt`: 140 packages,
  SHA-256
  `8fd7531bca9a366d1812991d80e5422dee350f4a1028bb38ce30bcc4297bd3b6`.
- Production-image checks (all passed): `eval/`, `tests/`, `fixtures/`,
  `examples/`, `docs/`, `.github/` absent; zero `*fallback*`/`*replay*`
  filesystem entries; `import app.server` succeeds; module-level app mode is
  `production`; server starts with the eval tree absent; `/` serves the
  legacy application; `/healthz` returns
  `{"status": "ok", "mode": "production"}`; both fallback endpoints return
  plain 404.
- Environment gate inside both images: gmsh meshes the check box
  (`ENV OK`); `CCX UNAVAILABLE` reported as an optional capability.

### CI and SBOM implementation

- `.github/workflows/ci.yml`: `lock-and-drift` (15 min), `backend-suite`
  (30 min), `replay-eval` (15 min), `container` (45 min, includes
  production-exclusion/startup checks, in-container suite with the
  git-metadata self-test deselected, in-container REPLAY, digests, SBOM
  artifact), `frontend-smoke` (10 min), `hygiene` (15 min). Every job has
  `timeout-minutes`; per-test timeout is 120 s. LIVE evaluation is never
  run in CI. `ubuntu-24.04` is only the hosted runner label; release
  evidence comes from the pinned container.
- SBOM policy: generated from the final runtime image with pinned syft as a
  CI artifact; not checked in. Local evidence run: CycloneDX SBOM of the
  runtime image, 762 components (174 libraries, 1 operating system),
  SHA-256
  `9d9938a189989f5a53a9565f2b3a85e1034e8de5569ad820c460dd87c3a5258c`.

### Validation commands and results

Windows development environment (uv-managed CPython 3.13.14, frozen lock):

- `uv sync --frozen` → environment rebuilt from `uv.lock` (35 packages).
- `.\.venv\Scripts\python.exe scripts\check_env.py` →
  `CCX UNAVAILABLE (optional; solver capability reports unavailable)` then
  `ENV OK`.
- Pre-change baseline reproduction on the locked environment:
  `pytest tests -q` → **317 passed, 1 skipped** (identical to the recorded
  Task 16/17 baseline).
- Post-change full suite: `pytest tests -q` → **352 passed, 1 skipped**
  (353 collected: baseline 318 plus 35 new Task 18 tests; the single skip
  remains the optional `ccx` smoke).
- Focused Task 18 tests: `pytest tests/test_runtime_mode.py -q` →
  **35 passed**.
- `python eval/run.py --replay` → REPLAY 15/15 (13 PASS,
  2 PASS_AFTER_CLARIFICATION, 0 FAIL), manifest
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`
  (matches the frozen Task 16 value); the revision-dependent report rewrite
  was discarded and the frozen tracked report bytes restored exactly.
- `python scripts/export_requirements.py --check` →
  `requirements.txt matches uv.lock export`.
- `uv lock --check` → lock is current with `pyproject.toml`.
- `node --check app/static/app.js` and `node --check app/static/audit.js`
  → both passed (Node 22.14.0).
- `git diff --check` → no whitespace errors.
- Documentation link validation over changed documents → all referenced
  repository paths exist.
- Secret scan over the diff (key/token/credential patterns) → no findings.
- Attribution scan over the diff (AI/tool author markers) → no findings.
- Scope scan: changed paths are exactly the Task 18 files listed above; no
  `package.json`, `package-lock.json`, `frontend/`, SQLAlchemy/Alembic,
  migration, OpenAPI-client, parser-worker, JobService, solver-execution,
  geometry, or meshing artifact was created.
- Test-generated `.sim_intent_cache/` was verified inside the repository
  and removed after evidence capture; no generated cache remains.

Supported Linux container (Docker Desktop 29.1.3, linux/amd64):

- `docker build --target runtime` and `--target ci` → both built from the
  digest-pinned base and frozen snapshot.
- In-container full suite (ci image):
  **351 passed, 1 skipped, 1 deselected** — the deselected test is
  `test_raw_fixture_hashes_match_git_archive_and_reject_different_bytes`,
  which requires `.git` metadata that images intentionally exclude; the same
  handling as the recorded Task 16 clean-archive evidence. The same test
  passes in the host-checkout suite.
- In-container `python eval/run.py --replay` → REPLAY 15/15, manifest
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`
  (report rewrite confined to the ephemeral container filesystem).
- In-container environment gate → `ENV OK` with `CCX UNAVAILABLE`.
- Production exclusion, import, startup, route, and `/healthz` checks → all
  passed (details above).

### Test evidence by environment

| Environment | Collected | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|---|
| Windows dev, locked env, pre-change baseline | 318 | 317 | 0 | 1 (`ccx`) | 0 |
| Windows dev, locked env, post-change | 353 | 352 | 0 | 1 (`ccx`) | 0 |
| Linux ci container, post-change | 353 | 351 | 0 | 1 (`ccx`) | 1 (git-metadata self-test) |

### Deviations, unavailable evidence, and deferred items

- **D4 deviation (requires ratification):** CalculiX is not installed; the
  recorded blocker and four options are above. The in-container solver
  smoke therefore still skips.
- **CI run links:** no commit or push exists, so no GitHub Actions run has
  executed. The workflow is implemented and its exact commands were executed
  locally (host and container) with the results above. CI links become
  available after the reviewed commit is pushed.
- **Registry image digests:** images were built and validated locally; no
  registry push was authorized, so only local image IDs are recorded.
- **`uv.lock` regeneration determinism** was verified through
  `uv lock --check` and byte-identical duplicate installs; a full
  independent re-resolution comparison on a second machine is left to CI.

### Risks and rollback

- Runtime-mode gating changes the default local server surface: a bare
  `uvicorn app.server:app` now runs in `production` and hides fallback
  routes; the demo requires `SIM_INTENT_MODE=replay` (documented). This is
  the approved D1 behavior.
- snapshot.debian.org availability is a build-time dependency; the frozen
  timestamp keeps resolution deterministic but the service must be reachable
  to rebuild images.
- The trixie `calculix-ccx` removal is upstream reality; every option that
  restores `ccx` requires a new human decision (recorded above).
- Rollback: revert the Task 18 commit(s); no persistent data, schema, tag,
  or fixture changed. `SIM_INTENT_MODE=test` restores the pre-Task-18 route
  surface on a running system without a revert. Local images are removed
  with `docker rmi`; the local `.venv` can be recreated from either
  `uv.lock` (current) or the reverted `requirements.txt`.

### Completion state

- Task 18 implementation is complete and validated locally on Windows and
  in the supported Linux container.
- Nothing is staged, committed, or pushed; the worktree diff is the
  reviewable Task 18 change set.
- Awaiting: independent read-only review (including security evidence per
  D0), and the human D4 CalculiX decision.
- Task 19 and Task 24 were not started.

### Correction addendum (2026-07-24, human-directed)

Two corrections were directed after the initial implementation report and are
now implemented. Where this addendum differs from figures above, the addendum
is current.

#### Correction 1 — runtime-mode closure authority

The construction-time `resolved_mode` closure in `create_app` was already the
value read by route registration, `/healthz`, and the provider-error
`fallback_available` hint; the code comment and this ledger now state
explicitly that `app.state.runtime_mode` is a **diagnostic copy only** and
never a handler's source of truth. New tests prove that after a hostile
post-construction mutation of `app.state.runtime_mode`:

- the route table is unchanged
  (`test_constructed_application_mode_cannot_change_routes`);
- `/healthz` still reports the construction mode
  (`test_mutating_state_copy_does_not_change_healthz`);
- `fallback_available` still reflects the construction mode
  (`test_mutating_state_copy_does_not_change_fallback_available`);
- production cannot become replay-capable
  (`test_production_cannot_become_replay_capable_by_state_mutation`).

`tests/test_runtime_mode.py` now contains 38 tests (three net-new plus one
rename to `test_app_state_carries_a_diagnostic_mode_copy`).

#### Correction 2 — reproducible CalculiX source build

The trixie packaging blocker above stands, but CalculiX is now provided by a
**source build from official immutable archives** on the unchanged trixie
base (option 3; no cross-release package mixing; no third-party binary):

- Provenance and licenses: `ccx_2.23.src.tar.bz2` from https://www.dhondt.de
  (GPL — the license notice is embedded in the source headers), SHA-256
  `9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7`;
  `spooles.2.2.tgz` from https://netlib.org/linalg/spooles (public domain
  per its documentation and Debian copyright records), SHA-256
  `a84559a0e987a1e423055ef4fdf3035d55b65bbe4bf915efaa1a35bef7f8c5dd`.
  Both hashes are enforced with `sha256sum -c` in the build.
- Build dependencies (frozen snapshot, exact pins, builder stage only —
  compilers never reach runtime/ci images): `gcc=4:14.2.0-1`,
  `gfortran=4:14.2.0-1`, `make=4.4.1-2`, `libarpack2-dev=3.9.1-6`,
  `bzip2=1.0.8-6`, `wget=1.25.0-2`, `ca-certificates=20250419`.
- Runtime dependencies (measured `ldd` closure of the built executable,
  added to the base stage): `libarpack2t64=3.9.1-6`, `liblapack3=3.12.1-6`,
  `libblas3=3.12.1-6`, `libgfortran5=14.2.0-19`.
- Build adjustments are compiler-conformance flags only, no source patches:
  SPOOLES compiled as `gnu89`; ccx C compiled with GCC 14's newly promoted
  errors downgraded back to warnings (`-Wno-error=return-mismatch,
  implicit-function-declaration, implicit-int, int-conversion,
  incompatible-pointer-types`); Fortran with `-fallow-argument-mismatch`;
  the static `ARPACK/libarpack_INTEL.a` reference replaced by
  `-larpack -llapack -lblas`.
- Feasibility iterations recorded honestly: missing `bzip2` in the slim
  base; GCC 14 `-Wreturn-mismatch` promotion failing `readnewmesh.c`; and a
  final-link `dlaev2_` undefined reference requiring explicit
  `-llapack -lblas`. Each was diagnosed from full build logs and fixed by
  the flags above.
- Result: `ccx -v` → `This is Version 2.23`. Stripped executable SHA-256 in
  the final images:
  `2725b5e976f3d505cfbc792431f01708350ab04df0014cbd1719af972b7ec669`.
  The executable embeds its build date (upstream `date.pl`), so the binary
  hash is recorded per build rather than claimed bit-reproducible; all build
  inputs are hash-pinned.
- `scripts/check_env.py` in both images now reports
  `CCX AVAILABLE: This is Version 2.23` before `ENV OK`.

#### Revalidation evidence (all rerun after both corrections)

- Clean `uv sync --frozen` → 34 packages checked; `uv lock --check` →
  current; `scripts/export_requirements.py --check` → byte-exact.
- Focused Task 18 tests: **38 passed**.
- Full Windows suite: **355 passed, 1 skipped** (356 collected; the skip is
  the ccx smoke, correct for Windows without ccx).
- Windows REPLAY evaluation: 15/15, manifest
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`;
  frozen tracked reports restored byte-exact.
- Container images rebuilt: runtime
  `sha256:5e1140824af79ba15692baceeba66814fecd7a010a5315e416ac044ba6fde38f`,
  ci
  `sha256:08cc6db51ee540133ed3296265939d96444169b9455233092731f623858d0a14`.
- Native manifest `/opt/native-packages.txt`: 144 packages, SHA-256
  `541dc68000c1435df2cf430b83bd9e7cb29a97a03fbbfd450aa4d0dcd3e7a2df`.
- In-container full suite: **355 passed, 1 deselected, 0 skipped** — the
  optional solver smoke `tests/test_export.py::test_optional_ccx_parse_run`
  now **executes and passes** (also verified individually: `1 passed`),
  meaning the built ccx actually solved the minimal test deck.
- In-container REPLAY evaluation: 15/15, frozen manifest.
- Production-image checks re-passed: exclusions absent, zero
  fallback/replay filesystem entries, import OK, startup without `eval/`,
  legacy `/`, `/healthz` = production, fallback endpoints 404.
- SBOM regenerated from the final runtime image with
  `SYFT_FILE_METADATA_SELECTION=all` so the source-built executable is
  included: 9,413 components; `/usr/local/bin/ccx` appears as a file
  component with its exact SHA-256; SBOM SHA-256
  `90b105db176abe311ff6be1a990498713533ae68d1e6ad63a2ec1251bb17ea31`.
  `.github/workflows/ci.yml` generates the SBOM the same way.
- Updated tracked-file hashes after the corrections: `Containerfile`
  and `.github/workflows/ci.yml` changed; final hashes are part of the
  reviewable diff (the pre-correction values above are historical evidence).
- `git diff --check`, documentation-link validation, secret scan,
  attribution scan, and scope scan re-run clean after the corrections.
- Nothing was staged, committed, or pushed; Task 19 and Task 24 remain
  unstarted.
