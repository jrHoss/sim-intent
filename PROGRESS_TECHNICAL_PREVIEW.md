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
