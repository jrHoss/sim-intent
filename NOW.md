# NOW — R4 / R5.2 integration remediation

**Active branch:** `integration-r4-r5-2`

**R4 completed baseline:**
`6b8abf2c24629b8161a38db824ca7de652053866`

**R5.2 completed baseline:**
`7e4b7c2dfbec87108c3ec4c4bb6c572aeb181ecb`

## Status

The first independent review returned `REQUEST CHANGES`. It identified one
HIGH migration downgrade-atomicity finding and one LOW stale-documentation
finding. Both findings have been remediated locally.

Downgrade preflight now refuses to leave the integrated R4/R5 state before any
destructive R5 schema operation when immutable schema-v3 setup revisions or
mesh revisions exist. Regression coverage protects both the merged
`0006_merge_r4_r5_heads` state and a database stamped with both independent
`0005` heads, without depending on Alembic predecessor traversal order. Safe
empty-database downgrade to `0004_geometry_identity_artifacts` remains
verified from both starting states.

`docs/geometry-identity.md` now distinguishes the internal deterministic R5.2
STEP meshing service and immutable mesh-local artifacts from the still-deferred
public mesh API, frontend workflow, stable CAD-face-to-mesh-boundary mapping,
and mapping-dependent export behavior.

The complete staged integration and remediation are ready for a fresh second
independent review. This is not approval. The merge remains in progress and
uncommitted. No push, remote publication, or protected-branch update is
authorized.

## Remediation verification

- Six focused atomicity regressions pass for merged-head and two-head refusal,
  mesh-only history, schema-v3-only data, complete data/schema/CAS preservation,
  post-failure exact reads and enforcement, and empty-database safe downgrade.
- The complete migration-focused selection collected and passed 162 tests with
  no failures or skips in 36.36 seconds; its one warning is the existing
  Starlette `httpx` deprecation warning.
- The semantic R4/R5 integration selection collected and passed 170 tests with
  no failures or skips in 82.67 seconds. It reported the existing Starlette
  deprecation warning and two expected SQLAlchemy identity-conflict warnings in
  CAS rollback tests.
- Manual post-suite reruns from both `0006_merge_r4_r5_heads` and the two-`0005`
  state raised the sanitized preflight error and preserved exact Alembic rows,
  mesh row and schema objects, setup bytes and schema version, geometry identity,
  source/ownership rows, CAS bytes, exact reads, and mutation enforcement.
- Alembic structural checks report the sole final head
  `0006_merge_r4_r5_heads`, the unchanged two-parent merge topology, and no new
  or renamed revision.
- All five browser JavaScript syntax checks passed. The R3.2b DOM harness passed
  with 17 mutation calls; the R4b.3 hydration harness passed at schema version 3
  with two CAD regions and seven mutations.
- Checked-in OpenAPI/JSON Schema artifacts match the backend, all 35 versioned
  payloads are current, and `uv lock --check` resolves the unchanged 41-package
  lock successfully.
- The full Python suite collected 1,891 tests: 1,890 passed, one expected
  optional CalculiX parse-run skipped because no `ccx` executable is installed,
  zero failed, and zero errored in 245.52 seconds. All 49 required baseline-
  evidence cases executed; Node-backed tests ran. The three warnings are the
  existing Starlette deprecation warning and two expected SQLAlchemy warnings
  from CAS rollback tests.

## Integration boundary

R4 stable CAD truth remains authoritative. R5 mesh identity and lineage remain
exact and immutable. Mesh exterior triangles retain mesh-local identity only;
CAD-to-mesh boundary mapping remains intentionally deferred.

No dependency, lockfile, generated-contract, public mesh API, frontend mesh
control, CAD-to-mesh mapping, solver/deck integration, or unrelated production
behavior was added by this remediation.

No commit, push, publication, tag, pull request, or merge request is authorized
before the second independent review.
