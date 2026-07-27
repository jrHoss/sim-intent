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

### Task 18 post-merge closure (append-only reconciliation, 2026-07-24)

This entry is appended, not a rewrite. Every earlier Task 18 entry above is
retained verbatim as historical evidence.

- Task 18 implementation commit `6e93165` was merged through pull request #4.
- `main` advanced to merge commit
  `6f92b5349d72fd7ef563293cd883c8b61fa3bbb5`; `origin/main` matches.
- The D4 decision is **ratified**: CalculiX `ccx 2.23` is built from the
  official hash-verified source archives (dhondt.de `ccx_2.23.src.tar.bz2`,
  netlib SPOOLES 2.2) in a dedicated builder stage on the unchanged
  digest-pinned Debian-trixie base, with no cross-release package mixing and no
  third-party prebuilt solver binary. The recorded trixie packaging blocker
  stands as the evidence that motivated it.
- The Task 18 status wording *"IMPLEMENTATION COMPLETE — UNCOMMITTED, AWAITING
  INDEPENDENT READ-ONLY REVIEW"* and the accompanying statements that no
  commit, push, or CI run existed are **historical** and are superseded by this
  closure entry. Task 18 is COMPLETE and MERGED.
- `demo-v1` remains peeled to
  `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d` and was not touched.

---

## Task 19 — Version API, IR, fixtures, and migration contracts

**Status:** IMPLEMENTATION COMPLETE — UNCOMMITTED, AWAITING INDEPENDENT
READ-ONLY REVIEW. Nothing is staged, committed, or pushed.

### Scope and takeover

- Date: 2026-07-24, Europe/Berlin.
- Branch: `task-19-schema-versioning`.
- Starting `HEAD`: `6f92b5349d72fd7ef563293cd883c8b61fa3bbb5` (merge of pull
  request #4). `main` and `origin/main` matched exactly.
- Initial worktree and index: clean (`git status --porcelain=v1 -uall` empty,
  `git diff --cached --stat` empty).
- `demo-v1` verified peeled to `154fe6ad0ac1336600d6ca5ec908d1b6c6e7401d`;
  not touched.
- Authority read before editing: `CLAUDE.md`, `release-goal.md`,
  `TECHNICAL_PREVIEW_PLAN.md` Task 19, this ledger, the Task 17 architecture
  set, and the approved versioning ADR
  [`ADR-004`](docs/architecture/technical-preview/adrs/ADR-004-api-schema-client-and-errors.md)
  with [`migration-rules.md`](docs/architecture/technical-preview/migration-rules.md).
- Task 20 was not started. No database, persistence, `Project`/`ModelVersion`/
  `SetupRevision`, material or coordinate semantics, React/frontend
  application, parser containment, geometry, meshing, solver, or result schema
  was implemented.

### Human decisions applied (2026-07-24)

D-1 approved (replay bodies unchanged, versioned sidecar manifest);
D-2 approved narrowly (single route-scoped legacy exception);
D-3 approved (frozen manifest hash preserved, separate version-aware hash);
D-4 approved (typed failures at the loader boundary; legacy envelopes
unchanged); D-5 approved with location change (`tools/openapi-types/`, no root
manifest); D-6 approved (caches unversioned); D-7 approved (geometry fixtures
unstamped); **D-8 rejected** (`EvaluationReport` and the five frozen
`eval/results*` records untouched); **D-9 rejected for Task 19** (no new runtime
endpoint); D-10 approved as append-only governance reconciliation (entry above).

All nine binding corrections were applied, including: no alias-route OpenAPI
retyping, no `export/versioning.py`, no fake production `1 → 2` migration,
synthetic test registries for mechanics, stamping restricted to setup-bearing
payloads, loaders rejecting missing versions despite the model default, a full
audit of direct `SimulationIntent.model_validate` call sites, and LF-normalised
deterministic generated files with `.gitattributes` coverage.

### Version taxonomy

Single central table: `ir/schema_version.py` (constants and family metadata
only; no models, registries, or I/O). It lives in `ir` because `ir` is the
lowest-level package and has no internal dependencies.

| Family | Current | Minimum | Registry | Declared in |
|---|---|---|---|---|
| `simulation_intent` | 1 | 1 | `ir.versioning.SIMULATION_INTENT_MIGRATIONS` | `SimulationIntent.schema_version` |
| `evaluation_case` | 1 | 1 | `eval.versioning.EVALUATION_CASE_MIGRATIONS` | `EvaluationCase.schema_version` |
| `fallback_record` | 1 | 1 | `app.record_versions.FALLBACK_RECORD_MIGRATIONS` | fallback envelope key |
| `replay_record` | 1 | 1 | `eval.versioning.REPLAY_RECORD_MIGRATIONS` | `eval/replay/manifest.json` |
| API contract | 1 | — | none (not a payload) | `schema/openapi.json` `info.version` |
| Artifact metadata | 1 | — | **reserved only** | not yet emitted |

All four payload registries are **legitimately empty**: the pre-Task-19 shape
*is* version 1 and no earlier shape has existed. `validate()` runs at import for
every production registry and proves the emptiness is intentional. Registry
mechanics are proven with synthetic test-owned registries.

### Migration behaviour

- `register(from_version)` only; the target is always `from_version + 1`, so a
  skipping edge is unrepresentable.
- The registry owns the version field end to end: it strips `schema_version`
  before calling a migration and sets it afterwards. A migration that sets the
  field is a registry defect (`MigrationPathError`).
- Loader order, never reordered: structural gate → explicit declaration →
  bounds (future/obsolete, **no body parsing**) → sequential migration → strict
  model validation → post-assertion of the current version.
- Writes emit only the current version (`dump_simulation_intent` overwrites a
  stale declared version).
- Idempotent at the current version: zero migration functions run.
- `SAFETY_CRITICAL_PATHS` (23 paths) may never be synthesised from absence;
  `approval_upgrades()` detects any transition toward `confirmed`, `accepted`,
  or `valid`. Both are asserted.

### Typed failure model

`payload_structure_invalid` (422), `schema_version_missing` (422),
`schema_version_malformed` (422), `schema_version_unsupported_future` (422),
`schema_version_obsolete` (422), `schema_migration_path_missing` (**500**,
server registry defect), and import-time `MigrationRegistryError`.
`problem_details()` emits RFC 9457 members at the loader boundary only;
existing legacy route envelopes are unchanged (D-4). An absolute host path
accidentally passed as `source` is reduced to a short relative label.

### Legacy compatibility (D-2)

`PUT /session/{session_id}/intent` is the sole exception. `app/schema_compat.py`
normalises an **absent** `schema_version` to `1`. It never inspects payload
shape, never rewrites a declared version, does not apply to files or any other
family or route, and is documented as temporary until the legacy route is
retired (Task 45 owns route cutover). The route keeps its typed
`SimulationIntent` request body, so the published request contract and
FastAPI's existing 422 envelope are unchanged; the cached raw body is read only
to inspect version presence.

### Stamped payloads (35 targets)

| Path | Count | Declares |
|---|---|---|
| `examples/*.json` | 3 | `simulation_intent` |
| `docs/task13-bracket-demo.json` | 1 | `simulation_intent` |
| `eval/cases/*.json` | 15 | `evaluation_case` |
| `eval/fallback/*.json` | 15 | `fallback_record` + nested `simulation_intent` |
| `eval/replay/manifest.json` | 1 | `replay_record` (new sidecar) |

Deliberately not stamped: `eval/replay/*.json` bodies (strict `Interpretation`
wire contract), `eval/results*`, `fixtures/bracket_expected.json`,
`tests/fixtures/*`, `tests/golden/bracket_abaqus.py`, `.sim_intent_cache/**`.

`scripts/stamp_schema_versions.py` uses textual insertion for hand-formatted
documents (one added line each) and canonical re-emission for the
machine-generated fallback records (two added lines each), so the diff is
minimal and formatting survives. Every write is refused unless the stamped
document, with declared versions removed again, parses equal to the original.
That equality is re-asserted against the `6f92b53` Git blobs by 34
parametrised tests.

### Files created (19)

`ir/schema_version.py`, `ir/versioning.py`, `eval/versioning.py`,
`app/record_versions.py`, `app/schema_compat.py`, `scripts/export_schema.py`,
`scripts/stamp_schema_versions.py`, `schema/openapi.json`,
`schema/simulation-intent.schema.json`,
`schema/generated/typescript/api-types.ts`, `schema/README.md`,
`eval/replay/manifest.json`, `tools/openapi-types/package.json`,
`tools/openapi-types/package-lock.json`, `docs/schema-versioning.md`,
`tests/test_schema_versioning.py`, `tests/test_schema_versioned_payloads.py`,
`tests/test_schema_version_routes.py`, `tests/test_openapi_contract.py`.

`app/record_versions.py` is named that way, rather than `fallback_records.py`,
so the Task 18 production-exclusion check (zero `*fallback*`/`*replay*`
filesystem entries in the runtime image) keeps working **unweakened**. The
module carries no fixture or replay data.

### Files modified (45)

`ir/schema.py` (adds `schema_version`), `eval/schema.py` (record version,
authoritative case loader, D-3 hash split), `eval/harness.py` (replay manifest
verification, versioned fallback writer), `app/server.py` (fallback loader,
legacy compatibility adapter, OpenAPI `info.version`),
`.github/workflows/ci.yml`, `.gitattributes`, `.gitignore`, `.dockerignore`,
`CLAUDE.md`, `docs/environment.md`, this ledger, plus the 34 stamped payload
files. (The count was reported as 44 in the first pass; the correct figure is
**45** — this ledger itself was appended after that count was taken. Corrected
per the independent review. The B-1/B-2 correction round adds `conftest.py`,
bringing the Task 19 total to 46 modified files; see the correction addendum.)

`app/session.py` needed no change: `schema_version` survives every existing
`model_copy` / `model_validate` round trip, and a regression test proves it.
`pyproject.toml`, `uv.lock`, `requirements.txt`, and `.python-version` are
**untouched** — Task 19 adds no Python dependency.

### Generated contracts

| Artifact | SHA-256 (LF-normalised) |
|---|---|
| `schema/openapi.json` | `80218e2448f61ff0fefbc9cacdd1f358aaa5ff3b40a557717af8e560fcf9c879` |
| `schema/simulation-intent.schema.json` | `494b660ef7fdf99979b2b887504db4d91006270ab1e553f53ce9390f4061b1c9` |
| `schema/generated/typescript/api-types.ts` | `cdda9e443163138ac230cacd0b03826840b228a55c459888ce606636bf3175e2` |
| `schema/README.md` | `12081079badec8c940d7188370b0de0a766cbe90cc52e1ba5b8b698ff3a56996` |
| `eval/replay/manifest.json` | `90ff12bffd5ae19b823b1af147916da0e86b1727595983e22d2ab04d3177560d` |
| `tools/openapi-types/package.json` | `e5ad3dbe749df5408e30d28c0fe6a0a1715c2c1730fbf2eae0053c730940ba58` |
| `tools/openapi-types/package-lock.json` | `8f048963226ab638866a28799e01372239d4f6687302d8baad01ceeb594cbce7` |
| `ir/schema_version.py` | `3e321c464d442f82d7fc4bd7ef37e08e8f068c258140d06fe40262c52ab372b7` |
| `ir/versioning.py` | `6cea8c7e0f814a1c1a880b01388650fb818bf231bac48f4af3775b98bf63dc51` |
| `eval/versioning.py` | `94986f4e1df5e82f7a1591bbc5b612803554f7b18950d71425bc32f6b1b47185` |
| `app/record_versions.py` | `5efdc01ec9fa6f0a1e9de34b1245121d5642e881483c9a7e3327fe3e35e371d3` |
| `app/schema_compat.py` | `367ec4aff7a2baf5e50dc05d02bc7e7c1936b53737228602fee0955afe6cf335` |
| `scripts/export_schema.py` | `a416c5d2ffc6f771bcdc2ece327a027b3d0dedabfd98ec30bac5c58169b9c077` |
| `scripts/stamp_schema_versions.py` | `eab7d706514283b32925b90e8dff2de29c1d67376279ef841087c4d3eb027ea6` |
| `docs/schema-versioning.md` | `a773aeeb89520776a9ee11bd3499001403b0a63c716f0dcb78e90185c9687a21` |

Generator: `openapi-typescript` **7.13.0**, exact-pinned in
`tools/openapi-types/package.json` with a committed `package-lock.json`
(lockfileVersion 3, 33 packages). No React, Vite, `openapi-fetch`, or
Playwright. No manifest at the repository root. `node_modules/` is git-ignored
and excluded from the container build context.

`schema/openapi.json` is always generated in `production` runtime mode; a test
asserts mode independence so the replay-only fallback routes can never leak
into the published contract. `info.version` is `"1"`, the string form of
`API_CONTRACT_VERSION`.

### Frozen-evidence results

- **Frozen 15-case manifest hash unchanged:**
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`
  (host and in-container REPLAY both reproduced it).
- New version-aware corpus hash:
  `adb5201a93f4d4619a84f6b56f3e68ec12f975a345cc78e47178b0d7a719ff53`.
- **All ten `eval/results*` files byte-identical** — `git status` reports no
  change after the REPLAY runs; the run-rewritten replay reports were restored
  with `git checkout --`.
- **Solver artifact bytes unchanged:** regenerated Abaqus artifact SHA-256
  `7ed6c5dc5d9e19ed6c9c6e70065f162e08f1c4418afee362d14a9a825f56e3ed`,
  byte-identical to `tests/golden/bracket_abaqus.py` and to the hash recorded
  by Task 16. `schema_version` does not appear in any generated artifact text:
  the export adapters build their output line by line and only dump individual
  BC/Load sub-models as a sort tiebreaker, so the top-level aggregate field
  never reaches artifact bytes.
- `fixtures/bracket_expected.json`, `tests/fixtures/*`, and
  `eval/replay/*.json` bodies are unmodified.

### Validation commands and results

Windows development environment (repository `.venv`, CPython 3.13):

- Focused Task 19 suites (4 files) → **169 passed**.
- Full suite → **524 passed, 1 skipped** (525 collected; baseline 356 collected
  → +169). The single skip remains the optional `ccx` smoke.
- `scripts/check_env.py` → `CCX UNAVAILABLE (optional)` then `ENV OK`.
- `python eval/run.py --replay` → REPLAY 15/15 (13 PASS,
  2 PASS_AFTER_CLARIFICATION, 0 FAIL), frozen manifest reproduced; tracked
  reports restored byte-exact.
- `python scripts/export_schema.py --check` → matches.
- `python scripts/stamp_schema_versions.py --check` → all 35 stamped and
  current; running the stamper twice produces no diff.
- `npm --prefix tools/openapi-types ci` then `run generate`, then
  `git diff --exit-code -- schema/generated` → **no drift**.
- `node --check app/static/app.js` and `audit.js` → passed (Node 22.14.0).
- `git diff --check` → clean (exit 0).

Supported Linux container (Docker 29.1.3, linux/amd64):

- `docker build --target ci` → image
  `sha256:9cf06b6127ea4b4f8a012f466f7be0b18aff711991b12c125bb71921d08781c8`.
- `docker build --target runtime` → image
  `sha256:2a4903d7b67c62fa3a1b5da338341abe348dfa49059bc3113382c17c6aa0ee38`.
- In-container full suite → **489 passed, 35 skipped, 1 deselected**. The 35
  skips are exactly the Git-baseline stamping-evidence tests (34 parametrised
  payloads plus the replay-body baseline check); the images intentionally
  exclude `.git`, so they skip rather than requiring a new CI deselect flag.
  The deselection is the pre-existing Task 16/18 git-metadata self-test.
- In-container REPLAY → 15/15, frozen manifest reproduced.
- In-container `scripts/export_schema.py --check` and
  `scripts/stamp_schema_versions.py --check` → both clean.
- In-container environment gate → `CCX AVAILABLE: This is Version 2.23`,
  `ENV OK`.
- Production image checks re-passed: `eval/`, `tests/`, `fixtures/`,
  `examples/`, `docs/`, `.github/`, `schema/`, `tools/` all absent; **zero**
  `*fallback*`/`*replay*` filesystem entries; import OK; startup OK; legacy `/`
  serves HTML; `/healthz` returns `{"status": "ok", "mode": "production"}`;
  both fallback endpoints return 404; served `/openapi.json` reports
  `info.version == "1"` and contains no fallback path.

### Checks that could not be executed here

- `uv lock --check` and `python scripts/export_requirements.py --check` could
  not be run: **`uv` is not installed in this environment** (Task 18 recorded
  uv 0.11.32; it is absent now). Both checks are unaffected by Task 19 by
  construction — `pyproject.toml`, `uv.lock`, `requirements.txt`, and
  `.python-version` are byte-unchanged, confirmed by `git status`. CI's
  `lock-and-drift` job still runs them.
- No GitHub Actions run exists: nothing has been pushed. The new
  `schema-drift` job and the extended `frontend-smoke` job were executed
  locally command-for-command with the results above.

### CI changes

- New bounded `schema-drift` job (15 min): frozen install, byte-exact OpenAPI
  and IR-schema export check, payload stamping check, and the four Task 19
  suites.
- `frontend-smoke` extended: `npm ci` from the committed lockfile,
  regeneration, and `git diff --exit-code -- schema/generated`.
- All external references remain pinned; LIVE evaluation is still never run.

### Hygiene scans

- Secret scan (private keys, API-key/secret assignments, `sk-` tokens, bearer
  headers) over all 63 changed and new files → no findings.
- Absolute host-path scan → no findings.
- Attribution/tool-marker scan → no findings.
- Scope scan: no database, SQLAlchemy, Alembic, migration table, `Project`,
  `ModelVersion`, `SetupRevision`, React/Vite/frontend application, parser
  worker, JobService, geometry, meshing, or solver-execution artifact was
  created. No new runtime endpoint exists; `/api/` is absent from the published
  contract (asserted by test).
- The test-generated `.sim_intent_cache/` was verified inside the repository
  and removed after evidence capture; no generated cache remains.

### Risks and rollback

- The four production migration registries are empty by design. The first real
  `n → n+1` migration must be added with golden evidence and must pass the
  safety-critical conformance test.
- The D-2 legacy exception is a real, if narrow, compatibility surface. It is
  route-scoped, tested, and must be removed when Task 45 retires the route.
- The generated-TypeScript drift gate depends on npm registry reachability in
  CI. It is a separate job, so a registry outage cannot block the backend
  suite.
- 34 checked-in payloads were rewritten. The evidence is the version-stripped
  equality against the `6f92b53` blobs, re-asserted on every run where Git
  metadata is available.
- Rollback: revert the Task 19 commit(s). No database, persistent record, tag,
  fixture, dependency, or lockfile changed, so no environment rebuild is
  needed; `tools/openapi-types/node_modules/` is git-ignored and removable.

### Completion state

- Task 19 implementation is complete and validated on Windows and in the
  supported Linux container.
- Nothing is staged, committed, or pushed; the worktree diff is the reviewable
  Task 19 change set.
- Awaiting independent read-only review.
- Task 20 was not started.

### Correction addendum (2026-07-24, independent review B-1 and B-2)

Two blocking findings from the independent read-only Task 19 review are fixed
here. Where this addendum differs from figures above, the addendum is current.
No frozen report, historical Task 18 evidence, stamped payload, replay body,
fixture, solver artifact, OpenAPI/TypeScript contract, or dependency was
changed.

#### B-1 — baseline evidence is now a non-skippable CI gate

**The defect.** The 35 baseline comparisons (34 parametrised stamped payloads
plus one replay-body byte-identity check) resolve real blobs at
`6f92b5349d72fd7ef563293cd883c8b61fa3bbb5`. `actions/checkout` defaults to
`fetch-depth: 1`, so that commit was absent in **every** hosted CI job, the old
helper skipped, and the stamping migration-evidence gate never actually ran in
CI. The first-pass report recorded the container skips as intentional but did
not notice the hosted jobs were skipping too.

**The fix.**

- New `tests/baseline_evidence.py` owns the policy and separates the decision
  (`resolve_baseline_evidence`, side-effect free and directly testable) from
  its effect (`require_baseline_object`).
- `SIM_INTENT_REQUIRE_BASELINE_EVIDENCE=1` makes an unavailable baseline object
  a **hard failure**. Without the variable it may still skip, so the container
  images, which intentionally exclude `.git`, keep running every other test.
- Both required hosted jobs — `backend-suite` and `schema-drift` — now check out
  with `fetch-depth: 0`, set the variable, and run an explicit preflight
  `git cat-file -e 6f92b5349d72fd7ef563293cd883c8b61fa3bbb5^{commit}` that
  fails the job immediately when the baseline is unavailable.
- Both jobs run pytest with `-rs`, and `conftest.py` adds a
  `pytest_terminal_summary` hook printing
  `Task 19 baseline evidence: executed=… skipped=… failed=… required=… available=…`,
  so a silent skip is visible in any job log. Policy probes pass explicit
  `env`/`root` arguments and are excluded from that accounting.
- No baseline blob was copied into the repository as fixture data; the
  comparison still uses real Git history.

**Measured behaviour.**

| Scenario | Result |
|---|---|
| Full history + `=1` (hosted simulation) | `executed=49 skipped=0 failed=0`; 86 passed |
| Baseline absent + `=1` (shallow simulation) | **35 failed**, `executed=0 skipped=0 failed=35`; preflight `git cat-file` exits 128 |
| Baseline absent, variable unset (container case) | 35 explicit skips, 50 passed, safety harness still 31 passed |

The 49 executed comparisons are the 34 stamped payloads plus the 15 replay
bodies iterated inside the single replay-body test.

The shallow case was simulated by copying the worktree without `.git` into a
scratch directory and running `git init` there, so `.git` exists but `6f92b53`
does not.

#### B-2 — complete all-registry migration safety conformance

**The defect.** The previous conformance test inspected only
`SIMULATION_INTENT_MIGRATIONS`, detected only synthesis from absence, never
called `approval_upgrades()`, and was vacuous while the registries were empty.

**The fix.** `tests/migration_safety.py` is a reusable family-aware harness
covering all four production registries with a complete representative payload
per family:

| Family | Representative | Protected paths |
|---|---|---|
| `simulation_intent` | `examples/bracket_sprint_goal.json` | `SAFETY_CRITICAL_PATHS` |
| `evaluation_case` | `eval/cases/01_bracket_bottom_fixed.json` | 18 ground-truth paths |
| `fallback_record` | `eval/fallback/bracket_bottom_fixed.json` | 6 envelope provenance paths + nested `proposed_ir` intent paths |
| `replay_record` | `eval/replay/manifest.json` | `records` |

It classifies `synthesis`, `deletion`, `mutation`, and `approval_upgrade`.
Approval detection calls the production `ir.versioning.approval_upgrades()` for
`SimulationIntent`-shaped scopes, including the nested `proposed_ir`, and a
family-specific map elsewhere (`artifact_export_eligible → true` and
`clarification_required → false` are approval strengthening for an evaluation
case). Evaluation cases and replay manifests are audited with their own
protected paths, never as if they were `SimulationIntent` payloads.

Every edge is discovered from `registry.registered_edges`, so a future
migration is audited automatically; a test proves auto-pickup with a synthetic
two-edge registry. A protected-path change is a violation unless that exact
`scope:path` appears in `APPROVED_SAFETY_CHANGES`, the migration-evidence hook,
which is empty while the registries are empty; a test proves the hook works in
both directions.

The gate is non-vacuous today: 31 tests in `tests/test_migration_safety.py`
prove synthetic unsafe migrations are rejected for deletion of
`regions[].entity_ids`, deletion and mutation of `bcs[].components`, mutation of
canonical units, synthesis of `materials[].density_tonne_per_mm3`,
`proposed → confirmed`, `pending → accepted`, `unvalidated → valid`, evaluation
export-eligibility upgrade, dropping a required clarification, unsafe nested
`proposed_ir` mutation and approval upgrade inside a fallback envelope, and
replay-manifest record tampering or deletion — while a safe metadata-only
migration is accepted for every family. Production registries remain empty; no
fake `1 → 2` migration was created.

The superseded single-registry test and its helpers were removed from
`tests/test_schema_versioning.py`.

#### Additional factual documentation correction found during this validation

The regeneration command published in the first pass,
`npm --prefix tools/openapi-types run generate`, **does not work**: `--prefix`
does not put the local `node_modules/.bin` on `PATH`, and the npm script
resolves `../../schema/...` relative to the tool directory. Because the failure
was piped through `grep`, a follow-up `git diff --exit-code` passed trivially
without regenerating anything. `schema/README.md` and `docs/environment.md` now
document the working `cd tools/openapi-types && npm run generate` form, which is
what `.github/workflows/ci.yml` already used via `working-directory:`. The
TypeScript drift gate was then re-run for real after `npm ci` from the committed
lockfile: `api-types.ts` regenerated byte-identically, SHA-256
`cdda9e443163138ac230cacd0b03826840b228a55c459888ce606636bf3175e2`,
`git diff --exit-code -- schema/generated` clean.

An empty `tools/openapi-types/schema/` directory tree, created by a stray
`mkdir` during the first pass and invisible to Git because it contained no
files, was removed.

#### Files changed in this correction round

Created: `tests/baseline_evidence.py`, `tests/migration_safety.py`,
`tests/test_migration_safety.py`.

Modified: `.github/workflows/ci.yml`, `conftest.py`,
`tests/test_schema_versioned_payloads.py`, `tests/test_schema_versioning.py`,
`schema/README.md`, `docs/environment.md`, `docs/schema-versioning.md`, and
this ledger.

Task 19 totals after this round: **46 modified, 22 untracked**. `conftest.py` is
the only newly modified tracked file; the other three created files are new
untracked test modules.

#### Revalidation after both corrections

- Focused Task 19 suites (now five files) with the required flag →
  **213 passed**, `executed=49 skipped=0 failed=0`.
- Full host suite with the required flag → **568 passed, 1 skipped** (the skip
  is the optional `ccx` smoke). Baseline accounting `executed=49 skipped=0`.
- `scripts/export_schema.py --check` and `scripts/stamp_schema_versions.py
  --check` → clean, host and in-container.
- `npm ci` from the committed lockfile then `npm run generate` → no drift.
- `python eval/run.py --replay` → 15/15, manifest
  `47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b`;
  tracked reports restored byte-exact.
- Frozen evidence re-verified unchanged: the ten `eval/results*` files, the 15
  replay bodies, `fixtures/`, `tests/fixtures/`, and the Abaqus artifact
  `7ed6c5dc5d9e19ed6c9c6e70065f162e08f1c4418afee362d14a9a825f56e3ed`.
- Supported Linux container rebuilt: **531 passed, 37 skipped, 1 deselected**;
  REPLAY, drift checks, and the production startup/exclusion checks all
  re-passed. The 37 skips are the 35 Git-history comparisons plus the two
  baseline-*policy* meta-tests that need Git metadata to assert against
  (`test_available_baseline_with_required_flag_executes_every_comparison` and
  `test_baseline_commit_availability_probe_matches_git`); the deselection is
  the pre-existing Task 16/18 git-metadata self-test. Every runtime, migration,
  and safety-conformance test executes there.
- `git diff --check` clean; secret, host-path, and attribution scans clean.
- `uv lock --check` and `scripts/export_requirements.py --check` still could not
  be executed: `uv` remains absent from this environment. `pyproject.toml`,
  `uv.lock`, `requirements.txt`, and `.python-version` are byte-unchanged, and
  CI's `lock-and-drift` job runs both.
- Nothing staged, committed, or pushed. Task 20 not started.

## R1.2 — Durable setup revisions (2026-07-26)

- Added durable simulation setups tied to one project, model, and model-version
  lineage, with immutable full-snapshot `SimulationIntent` revisions.
- Region confirmation/rejection and assumption acceptance/rejection are stored
  as new authoritative revisions and survive application restart/reopen.
- Optimistic `expected_revision` checks provide controlled stale-write
  conflicts; project-scoped request IDs and canonical fingerprints provide
  sequential and concurrent idempotency for setup creation and mutations.
- Lineage checks isolate setup decisions between model versions. Legacy
  `/session/...` routes remain explicitly volatile and are not dual-written.
- Alembic migration `0002_setup_revisions` enforces lineage, sequential parent
  chains, immutable revisions, valid current pointers, safe cascades, and
  populated downgrade/re-upgrade behavior.
- Focused R1.2 and migration tests: **14 passed**.
- Full regression suite: **606 passed, 1 skipped** (documented optional skip).
- OpenAPI, generated TypeScript, migration, cascade, restart, concurrency,
  idempotency, and `git diff --check` evidence passed.
- Independent review verdict: **READY TO COMMIT**.
## R2.1 safe ingestion (working tree evidence, 2026-07-26)

- Branch: `r2a-safe-ingestion`; no commit created.
- Added bounded raw/multipart quarantine ingestion, incremental source SHA-256,
  isolated version-1 parser worker responses, bounded output and timeout
  termination, file-based CAS publication, and bounded startup cleanup.
- Focused ingestion/persistence/upload/viewer/migration run: 98 passed.
- Full suite: 615 passed, 1 skipped (optional environment capability).
- OpenAPI/schema stamping, TypeScript generation drift, requirements/lock file
  byte drift, compileall, and `git diff --check`: clean.
- `uv lock --check` could not run because uv is not installed/on PATH in this
  Windows environment; neither `uv.lock` nor dependency declarations changed.
- Remaining boundary: the worker provides crash/timeout isolation, not OS CPU
  or memory quotas; durable STEP viewer tessellation remains existing trusted
  post-validation behavior pending the later meshing isolation slice.
## R2.1 independent-review remediation (working tree, 2026-07-26)

- Preserved validated `.step`/`.stp`/`.inp` suffixes on unique quarantine
  files; real bracket STEP upload, inventory, glTF, shutdown, and fresh-app
  reopen pass without parser mocking.
- Replaced multipart envelope decoding with an incremental boundary parser
  that writes and hashes only the selected file part, bounds source bytes and
  envelope overhead, and cleans partial files on malformed/ambiguous input.
- Windows parser processes now run in a kill-on-close Job Object; a real fake
  descendant could not create its delayed side-effect after timeout. Unix
  process-group termination remains in place.
- Structured stdout remains strict and bounded; stderr is drained with a
  bounded retained prefix and truncation marker without invalidating success.
  Non-object JSON is rejected as `parser_crash`; internal worker diagnostics
  are correlated in private logs and sanitized from problem responses.
- Added portable filename allowlisting, encoded download disposition, long
  comment-led INP coverage, finite configuration validation, reserved-root
  protection, and generated HTTP 413 OpenAPI/TypeScript contracts.
- Focused safe-ingestion set: 36 passed. Full suite: 642 passed, 1 skipped.
## R2.1 final correlation/length remediation (working tree, 2026-07-26)

- Every HTTP request now resolves one validated supplied or generated
  correlation ID before route handling. Durable upload, inventory, and glTF
  parser calls, private diagnostics, and RFC 9457 responses share that ID.
- Narrow Content-Length parsing no longer catches the `ApiProblem` 413;
  declared raw oversize is rejected without reading the request stream while
  streamed limits remain authoritative for malformed, missing, or deceptive
  lengths.
- Portable filename validation additionally rejects Windows reserved device
  basenames case-insensitively.
- Focused safe-ingestion suite: 46 passed; affected persistence/viewer suite:
  100 passed; full suite: 652 passed, 1 skipped.

## R2.2 source invalidation and storage capacity (working tree, 2026-07-27)

- Branch: `r2b-source-invalidation-storage-cap`; no commit created.
- Added authoritative current-version pointers, explicit version supersession,
  and mutable setup staleness without changing immutable setup revision
  payloads. Stale reads remain available, export eligibility is forced false,
  and setup mutations return `setup_source_superseded`.
- Added a configurable 1 GiB default source-CAS capacity under the existing
  publication coordination lock. Unique blobs are quota checked; deduplicated
  blobs consume no additional capacity; malformed, unrelated, and symlink
  entries are ignored; failures return RFC 9457 HTTP 507
  `source_storage_limit_exceeded`.
- Migration `0003_source_supersession` deterministically selects the greatest
  existing version as current, backfills supersession/staleness, preserves
  payload immutability and lineage triggers, and passes populated downgrade
  and re-upgrade.
- Focused R2.2 suite: **10 passed**. Migration plus R2.2 suite: **13 passed**.
  Affected R1/R2.1 persistence, setup, ingestion, and migration regressions:
  **90 passed**.
- Full suite with required baseline evidence: **662 passed, 1 skipped**;
  baseline comparisons executed=49, skipped=0, failed=0. The skip remains the
  optional environment capability.
- OpenAPI export/check, TypeScript regeneration, schema stamping, compileall,
  and `git diff --check` passed.

### R2.2 independent-review remediation (2026-07-27)

- Added a database INSERT backstop for active setups plus shared source-lock
  coordination, irreversible setup staleness, authoritative pointer
  insert/update/delete protection, and pointer-derived API `is_current`.
- Idempotent mutation replay now precedes stale-source rejection for intent,
  region, and assumption operations.
- The 0002 backfill now records complete supersession timestamps and successor
  pointers. Its populated fixture covers multiple projects, three source
  versions, old/current setups, multiple immutable revisions, confirmed
  regions, accepted assumptions, downgrade/re-upgrade, and behavioral trigger
  checks.
- Unique-publication quota checks first perform bounded coordinated orphan
  cleanup; referenced and malformed/unrelated entries remain protected.
- Focused R2.2 suite: **14 passed**. Affected R1/R2.1 and migration set:
  **96 passed**. Full suite: **666 passed, 1 skipped** with all 49 required
  baseline comparisons executed.
- OpenAPI/schema checks, TypeScript regeneration, schema stamping, compileall,
  and `git diff --check` passed. No commit was created.

### R2.2 final projection/documentation remediation (2026-07-27)

- Setup `model_version_is_current` now compares the setup version directly
  with `Model.current_version_id`, matching every other currentness field.
  A trigger-preserving inconsistent-state regression proves that two
  non-superseded flags cannot produce two current API projections and that the
  non-pointed setup remains stale and export-blocked.
- `docs/environment.md` now records the 100-candidate orphan-reclamation bound,
  the need for later attempts or maintenance beyond that bound, and the
  prohibition on automatic eviction of referenced historical sources.
- Focused R2.2 plus setup projection tests: **26 passed**. Full suite:
  **667 passed, 1 skipped**, with all 49 required baseline comparisons
  executed. Contract drift, compile, and whitespace checks passed.
