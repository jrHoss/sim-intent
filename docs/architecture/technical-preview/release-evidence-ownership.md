# Release requirement and evidence ownership

## Human accountability

| Responsibility | Owner | Boundary |
|---|---|---|
| Release Owner | Maein, subject to his acceptance of this responsibility | Owns release ledger completeness, gate coordination, risk escalation, and Task 45 release decision process |
| Technical Review Owner | Ahmed Yassin | Coordinates architecture and technical review; does not replace the separate independent reviewer |
| Independent technical evidence | Separate read-only reviewer for each task | Reviews the complete diff and evidence before commit; task author cannot self-approve |
| Security Review Owner | Accountable role awaiting assignment to a named person | Must be named before Task 18 may be approved |
| Task evidence preparation | Implementing task owner | Records commands, versions, hashes, results, risks, and limitations in `PROGRESS_TECHNICAL_PREVIEW.md` |

## Release demonstration ownership

| Demonstration requirement | Primary owner tasks | Gate/evidence owner |
|---|---|---|
| Create named project and upload supported STEP/INP | 20–21, 24–27 | Task 27 preview gate; Task 39 end-to-end trace |
| Inspect geometry, coordinates, dimensions, units, mesh/sets | 25–26, 28, 31–32, 39 | Task 39 browser evidence |
| Define/edit materials, constraints, and loads by language and click | 23, 28–29, 33, 39 | Task 39 workflow evidence |
| Resolve ambiguity and review IDs, units, assumptions, and source evidence | 23, 26, 29, 33, 39 | Task 39 clarification/audit trace |
| Generate, inspect, adjust, and regenerate STEP mesh | 34–35, 39 | Task 39 mesh/mapping report |
| Run bounded isolated local CalculiX | 36–38 | Task 38 execution evidence |
| View displacements, stress, reactions, extrema, and warnings | 37, 39 | Task 39 result UI evidence |
| Revise, rerun, and compare immutable analyses | 22, 29, 38–39 | Task 39 lineage/comparison report |
| Download reproducibility bundle | 36–39 | Task 39 checksum evidence |
| Close/reopen without losing state or audit trail | 21–23, 27, 38–39 | Tasks 27 and 39 restart traces |
| Three unseen golden parts including multi-hole bracket | 40 | Task 40 signed gate |

## Supported-envelope ownership

| Active requirement | Owning tasks |
|---|---|
| Single-solid STEP and supported first-order-solid INP inputs | 20–21, 30–35 |
| Linear-elastic isotropic materials and explicit conversion | 19, 28–29 |
| Static small-displacement analysis | 28–29, 36 |
| Fixed/prescribed translational constraints | 28–29, 35–36 |
| Surface resultant, traction, pressure, nodal force, gravity | 28–29, 34–36 |
| Deterministic STEP tetrahedral meshing and quality | 20, 34 |
| Existing INP mesh without remeshing | 20–21, 35–39 |
| Verified boundary mapping | 31–35 |
| Local CalculiX generation/execution/results | 36–40 |
| Abaqus export-only validation | 35–37, 42 |
| Text and direct viewer-click grounding | 23, 25–26, 31–33 |
| Single-user local persistence and multi-tab concurrency | 21–23, 26–27, 29, 38–39 |
| Explicit unsupported-capability response | 19, 28–29, 33, 35–39, 42 |
| All explicit exclusions and no silent approximation | Task 17 architecture; verified by Tasks 29, 33, 36, 40, 45 |

## Product-invariant ownership

| Product invariant | Owning tasks |
|---|---|
| Model never emits/chooses CAD or mesh IDs | 31–33 |
| Deterministic geometry/viewer interaction produces IDs | 25, 31–33 |
| No unconfirmed/rejected/stale region reaches mapping/export/solve | 22, 29, 35–38 |
| Unit-bearing value retains original, normalized, internal unit, conversion | 19, 28 |
| Solver artifacts bind exact source/setup/mesh/mapping/adapter/solver hashes | 21–22, 34–38 |
| Source change invalidates confirmations and derivatives | 21–22, 31, 34–35 |
| Runs immutable; edits create successors | 22, 38–39 |
| Execution, numerical checks, and engineer approval are separate | 29, 37–40 |
| Provider failure preserves state; REPLAY never labeled LIVE | 18, 23, 33, 41, 45 |
| Uploads/jobs bounded, sanitized, defensive, no shell interpolation | 20, 38, 43 |
| Local data and credential egress control | 18, 20, 38, 43–44 |
| Chat not engineering truth; one backend aggregate | 22–23, 26, 29, 39 |
| Uncertainty, invalidity, and insufficient constraints fail closed | 29, 33, 35–38 |
| No fixture/replay branch in production | 18, 33, 40–41, 45 |
| No active dependency on post-preview capabilities | Task 17 dependency review; Task 45 final scan |
| Existing deterministic owners extended/wrapped, not duplicated | 19, 28–33, 36–37 |

## Workstream ownership

| Release-goal workstream | Owning tasks |
|---|---|
| Durable project and revision model | 19, 21–23 |
| Engineering setup editor | 26, 28–29, 39 |
| Stable geometry and mesh lineage | 30–35 |
| Verified solver execution and results | 36–40 |
| Reliability evaluation | 40–43, 45 |
| Packaging, security, and operations | 18, 20, 38, 43–45 |

## Quantitative gate ownership

| Release gate | Evidence-producing tasks | Final verifier |
|---|---|---|
| At least 95/100 cases correct after at most one clarification | 41, 43 | Task 45 |
| Golden path 100% entity, condition, normalized quantity | 40–41, 43 | Task 45 |
| Zero false-ready outcomes | 29, 33, 35–41, 43 | Task 45 |
| LIVE and REPLAY reported separately | 18, 41, 43 | Task 45 |
| All required confirmed boundaries map non-empty | 35, 40–41, 43 | Task 45 |
| CalculiX decks execute without repair for at least 18/20 models | 36, 38, 40–41, 43 | Task 45 |
| Axial displacement/reaction within 2% | 37, 40, 43 | Task 45 |
| Constant stress within 3% | 37, 40, 43 | Task 45 |
| Applicable reaction imbalance below 1% or visibly flagged | 37, 39–40, 43 | Task 45 |
| One Abaqus golden model completes from export in documented version | 42 | Task 45 |
| Full bounded Linux test suites pass | 18–20, 24–26, 43–44 | Task 45 |
| Twenty consecutive golden runs without leaks/corruption | 38, 43 | Task 45 |
| Forced backend crash preserves committed revision and reports interruption | 22, 38, 43 | Task 45 |
| Median proposal latency ≤8 s and p95 ≤20 s on documented workstation | 33, 43 | Task 45 |
| No open critical/high security finding; no secrets | 18, 20, 38, 43–44 | Named Security Review Owner and Task 45 |
| Complete third-party license inventory | 18, 43–44 | Task 45 |
| Reproducible tagged package and three-part clean-install demonstration | 40, 44 | Task 45 |

## Evidence lifecycle

1. The task owner records exact commands, versions, hashes, results,
   limitations, and risks in
   [`PROGRESS_TECHNICAL_PREVIEW.md`](../../../PROGRESS_TECHNICAL_PREVIEW.md).
2. The task owner maps evidence to the task Definition of Done and the tables
   above.
3. A separate read-only reviewer examines the complete diff and evidence before
   commit.
4. Findings are resolved or recorded as explicit blockers; a task is not
   self-approved.
5. The Release Owner maintains gate-level completeness and stops later tasks
   when a dependency lacks approved evidence.
6. Security-sensitive tasks additionally require the named Security Review
   Owner once assigned.
7. Task 45 independently verifies the complete release matrix against the
   tagged candidate; development-worktree evidence alone cannot pass a release
   gate.
