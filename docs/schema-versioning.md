# Schema versioning, migration, and contract drift (Task 19)

Authority: [ADR-004](architecture/technical-preview/adrs/ADR-004-api-schema-client-and-errors.md)
and [migration-rules.md](architecture/technical-preview/migration-rules.md).

This document is the Task 19 implementation contract. It does not add or change
an architecture decision.

## 1. Version registry

Every versioned family declares a **positive integer** `schema_version`. The
single central table is [`ir/schema_version.py`](../ir/schema_version.py); it
lives in `ir` because `ir` is the lowest-level package and has no internal
dependencies, so every other package can import it without an import cycle.

| Family | Constant | Current | Minimum supported | Registry | Declared in |
|---|---|---|---|---|---|
| `simulation_intent` | `SIMULATION_INTENT_SCHEMA_VERSION` | 2 | 1 | `ir.versioning.SIMULATION_INTENT_MIGRATIONS` | `SimulationIntent.schema_version` |
| `evaluation_case` | `EVALUATION_CASE_SCHEMA_VERSION` | 1 | 1 | `eval.versioning.EVALUATION_CASE_MIGRATIONS` | `EvaluationCase.schema_version` |
| `fallback_record` | `FALLBACK_RECORD_SCHEMA_VERSION` | 1 | 1 | `app.record_versions.FALLBACK_RECORD_MIGRATIONS` | fallback envelope key |
| `replay_record` | `REPLAY_RECORD_SCHEMA_VERSION` | 1 | 1 | `eval.versioning.REPLAY_RECORD_MIGRATIONS` | `eval/replay/manifest.json` |
| API contract | `API_CONTRACT_VERSION` | 1 | — | none (not a payload) | `schema/openapi.json` `info.version` |
| Artifact metadata | `ARTIFACT_METADATA_SCHEMA_VERSION` | 1 | — | **reserved** | not yet emitted |

`ARTIFACT_METADATA_SCHEMA_VERSION` is a reserved constant only. No artifact
manifest file format exists yet, so Task 19 deliberately creates no artifact
migration loader. Task 36 owns that contract.

### Independent versus coupled

- API contract and `simulation_intent` are **independent**: a route change must
  not force an IR migration, and vice versa.
- `evaluation_case`, `replay_record`, and `simulation_intent` are
  **independent** integers.
- `fallback_record` is coupled to `simulation_intent` **by containment only**.
  An envelope migration never rewrites the nested `proposed_ir` body; it
  delegates to the intent registry after its own migration completes. That is
  the only permitted cross-family interaction.
- The API contract version, the checked-in OpenAPI snapshot, and the generated
  TypeScript move together by construction and are enforced by the drift check,
  not by three separate integers.

## 2. Migration matrix

| Family | Registered edges | Notes |
|---|---|---|
| `simulation_intent` | `1 → 2` | R3.1 engineering-setup decisions |
| `evaluation_case` | *(none)* | `minimum == current == 1` |
| `fallback_record` | *(none)* | `minimum == current == 1` |
| `replay_record` | *(none)* | `minimum == current == 1` |

The three empty registries are legitimately empty: the pre-Task-19 shape of
those families *is* version 1 and no earlier shape has ever existed.
`MigrationRegistry.validate()` runs at import for every production registry and
proves that emptiness is intentional rather than forgotten.

### `simulation_intent` 1 → 2 (R3.1)

Version 2 makes five engineering decisions explicit rather than implied:
`analysis.dimensionality`, `analysis.solver_target`,
`analysis.coordinate_system`, `mesh_settings` and `solver_settings`.

`_simulation_intent_one_to_two` writes an explicit `null` for each and changes
nothing else. That is deliberate and is the whole point of the edge: a
version-1 payload predates every one of those decisions, so inventing a value
would hand an old setup a 3D-solid approval, a global-coordinate approval, a
CalculiX target, a 1 mm Gmsh mesh, a solver profile and a set of requested
results that no engineer ever chose. Explicitly missing is the honest state, and
`ir.validate` reports the migrated payload as `structurally_incomplete` with
`export_eligible = false` until each decision is stated deliberately.

The corresponding model fields therefore carry `None` defaults rather than
usable values. There is **no export-enabling default anywhere in the schema**;
compatibility is achieved by staying incomplete, never by filling gaps.

`APPROVED_SAFETY_CHANGES` in `tests/migration_safety.py` remains **empty**: the
migration touches no path in `SAFETY_CRITICAL_PATHS`, so it needs no waiver.
`tests/test_engineering_setup.py` additionally pins that the migration adds
exactly those five keys, all `null`, and leaves materials, regions, BCs, loads,
assumptions, units and `validation_status` byte-identical.

Registry mechanics are proven with **synthetic test-owned registries** in
[`tests/test_schema_versioning.py`](../tests/test_schema_versioning.py):
sequential multi-step migration, mid-chain entry, missing edge, duplicate edge,
unrepresentable skipping edge, obsolete version, malformed and missing
declarations, and refusal to upgrade safety-critical semantics.

## 3. Migration contract

A migration is a **pure content transform**:

```python
@REGISTRY.register(1)
def _one_to_two(payload: Mapping[str, Any]) -> dict[str, Any]:
    ...
```

- `register` accepts only `from_version`; the target is always
  `from_version + 1`, so a skipping edge cannot be expressed.
- The registry strips `schema_version` before calling a migration and sets it
  afterwards. A migration that sets the field itself is a registry defect.
- `validate()` asserts the edge set exactly covers
  `minimum_supported_version .. current_version - 1`.
- Migration is idempotent at the current version: a current payload runs
  through zero migration functions.

### Loader order (never reorder)

1. structural gate — the payload must be a JSON object;
2. explicit version declaration — missing or malformed is a typed failure;
3. version bounds — future or obsolete is a typed failure, **with no body
   parsing**;
4. sequential `n → n+1` migration;
5. strict model validation, which rejects malformed or partial historical
   payloads without defaulting any field;
6. post-assertion that the result carries exactly the current version.

### Safety-critical fields

`ir.versioning.SAFETY_CRITICAL_PATHS` enumerates the paths a migration may
never synthesise from absence: analysis type and units, material model /
`E_MPa` / `nu` / density, every region provenance field and status, BC and load
references, components, vectors, magnitudes, assumption text / criticality /
status, and `validation_status`.

Where a future migration genuinely lacks one of these,
[migration-rules.md](architecture/technical-preview/migration-rules.md)
requires the explicit unapproved/blocked state — `proposed`, `pending`,
`unvalidated` — never `confirmed`, `accepted`, or `valid`.

#### Safety conformance gate

`tests/migration_safety.py` audits **all four** production registries, each
with a complete representative payload of its own family, and classifies four
failure modes:

| Check | Meaning |
|---|---|
| `synthesis` | a protected field that was absent is now present |
| `deletion` | a protected field that was present is now absent |
| `mutation` | a protected field changed value |
| `approval_upgrade` | the payload moved toward a more-approved state |

Approval detection calls the production `ir.versioning.approval_upgrades()` for
`SimulationIntent`-shaped scopes — including the nested `proposed_ir` inside a
fallback envelope — and a family-specific map elsewhere. For an evaluation
case, `artifact_export_eligible → true` and `clarification_required → false`
are treated as approval strengthening because both weaken a safety gate.

Protected paths are family-specific: `SAFETY_CRITICAL_PATHS` for
`SimulationIntent`, ground-truth fields for `EvaluationCase`, provenance fields
for the fallback envelope plus the full intent path set for its nested
`proposed_ir`, and the record map for the replay manifest. Evaluation cases and
replay manifests are never treated as if they were `SimulationIntent` payloads.

Every registered edge is discovered from `registry.registered_edges`, so a
future migration is audited without anyone adding a second test. A change to a
protected path is a violation unless that exact `scope:path` is listed in
`APPROVED_SAFETY_CHANGES`, the explicit migration-evidence hook, which is empty
while the registries are empty.

Because the production registries are empty today, the gate is proven
non-vacuous by synthetic deliberately unsafe migrations in
`tests/test_migration_safety.py`. They are rejected for: deletion of
`regions[].entity_ids`; deletion and mutation of `bcs[].components`; mutation of
canonical units; synthesis of `materials[].density_tonne_per_mm3`;
`proposed → confirmed`; `pending → accepted`; `unvalidated → valid`; evaluation
export-eligibility upgrade; dropping a required clarification; unsafe nested
`proposed_ir` mutation and approval upgrade inside a fallback envelope; and
replay-manifest record tampering or deletion. A safe metadata-only migration is
accepted for every family, so the harness is not merely rejecting everything.

## 4. Typed failure model

| Exception | `code` | HTTP | Retryable |
|---|---|---|---|
| `PayloadStructureError` | `payload_structure_invalid` | 422 | no |
| `MissingSchemaVersionError` | `schema_version_missing` | 422 | no |
| `MalformedSchemaVersionError` | `schema_version_malformed` | 422 | no |
| `UnsupportedFutureVersionError` | `schema_version_unsupported_future` | 422 | no |
| `ObsoleteSchemaVersionError` | `schema_version_obsolete` | 422 | no |
| `MigrationPathError` | `schema_migration_path_missing` | **500** | no |
| `MigrationRegistryError` | — (import-time) | fails startup | — |

`MigrationPathError` is deliberately 5xx: a gap in the server's own registry is
a server defect, not bad client input.

`SchemaVersionError.problem_details()` emits RFC 9457 members. Per decision
D-4 this applies at the versioned loader boundary and to any future versioned
API surface; **existing legacy route error envelopes are unchanged**. Details
never carry credentials, prompt content, solver or source bytes, or absolute
host paths — an accidental absolute path in `source` is reduced to a short
relative label.

## 5. Authoritative loaders

| Loader | Family | Used by |
|---|---|---|
| `ir.versioning.load_simulation_intent` | `simulation_intent` | checked-in intent documents, legacy PUT, nested fallback bodies |
| `ir.versioning.dump_simulation_intent` | `simulation_intent` | every write |
| `eval.schema.load_evaluation_case` | `evaluation_case` | `eval/cases/*.json` via `load_cases` |
| `app.record_versions.load_fallback_record` | `fallback_record` | the REPLAY fallback route and the harness |
| `eval.versioning.verify_replay_directory` | `replay_record` | `load_replay` before any body is trusted |

The current `SimulationIntent.schema_version` field is **required**, including
in generated JSON Schema, OpenAPI, and TypeScript contracts. Durable `/api/v1`
create and full-intent revision boundaries additionally reject missing,
malformed, legacy, and future declarations with the stable
`simulation_intent.schema_version_*` codes before persistence. Historical
records still pass through the authoritative loader and controlled migration.

## 6. Legacy compatibility exception (decision D-2)

`PUT /session/{session_id}/intent` is the **only** exception. CLAUDE.md
invariant 8 makes the frozen legacy viewer contracts additive-only, so Task 19
may not turn a previously absent field into a mandatory one on that route.
The route uses the typed `LegacySimulationIntent` compatibility schema; no
durable endpoint uses that model.

`app/schema_compat.py` normalises an **absent** `schema_version` to
`LEGACY_UNVERSIONED_INTENT_VERSION = 1`. The exception:

- never inspects or guesses the payload shape — only key presence;
- never rewrites a *declared* version: malformed, obsolete, and future
  declarations still fail through the normal typed path;
- does not apply to files, fallback records, evaluation cases, new API
  contracts, or any future route;
- is covered by regression tests in
  [`tests/test_schema_version_routes.py`](../tests/test_schema_version_routes.py);
- is **temporary** and is removed when the legacy route is retired. Route
  cutover is owned by Task 45.

The route keeps its typed `SimulationIntent` request body, so the published
OpenAPI request contract and FastAPI's existing 422 envelope are unchanged; the
cached raw body is read only to inspect version presence.

## 7. Versioned checked-in payloads

Stamped (setup-bearing):

| Path | Count | Declares |
|---|---|---|
| `examples/*.json` | 3 | `simulation_intent` |
| `docs/task13-bracket-demo.json` | 1 | `simulation_intent` |
| `eval/cases/*.json` | 15 | `evaluation_case` |
| `eval/fallback/*.json` | 15 | `fallback_record` + nested `simulation_intent` |
| `eval/replay/manifest.json` | 1 | `replay_record` (sidecar) |

Deliberately **not** stamped:

| Path | Reason |
|---|---|
| `eval/replay/*.json` bodies | strict `Interpretation` LLM wire contract (D-1); versioned by sidecar manifest |
| `eval/results*.{json,md}` | frozen evaluation evidence; must stay byte-identical (D-8) |
| `fixtures/bracket_expected.json`, `tests/fixtures/*` | geometry ground truth, not setup-bearing (D-7) |
| `tests/golden/bracket_abaqus.py` | golden solver artifact |
| `.sim_intent_cache/**` | disposable, regenerable runtime caches outside the persisted-contract boundary (D-6) |

The evaluation case records carry deliberately *partial* IR fragments
(`expected_ir_subset`, `expected_structured_ir_subset`). Those are never
treated as `SimulationIntent` payloads and are never stamped.

### Stamping and its migration evidence

`scripts/stamp_schema_versions.py` performs the one-shot stamping and supports
`--check`. Two strategies keep the diff reviewable:

- **insert** — hand-formatted documents get a single textual line after the
  opening brace, so formatting and key order survive untouched;
- **canonical** — machine-generated documents are re-emitted exactly as their
  producer emits them, so the stamped file is byte-identical to a regeneration.

Every write is verified before it happens: the stamped document, with its
declared versions removed again, must parse equal to the original. That
equality is the migration evidence required before rewriting a checked-in
payload, and it is re-asserted against the **actual committed `6f92b53` blobs**
by `tests/test_schema_versioned_payloads.py`. The baseline blobs are never
copied into the repository as fixture data; the comparison needs real Git
history.

A *declared* version is never taken as evidence that a document is valid.
Before a supported-version document is returned unchanged — and again after any
document is stamped — the stamper validates it through the family's own
authoritative loader: `ir.versioning.load_simulation_intent` for the intent
documents, `eval.schema.load_evaluation_case` for the case records, and
`app.record_versions.load_fallback_record` for the fallback envelopes including
their nested `proposed_ir`. At the current version the loader runs zero
migrations, so a current-version document is judged directly by the current
typed schema; a legacy document is judged by the same schema after the
registered migration path has carried it forward. Malformed nested objects,
invalid discriminators, unsupported units, missing required legacy structure and
invalid nested schema-version combinations therefore fail `--check` whether the
document declares version 1, version 2, or nothing at all.

Validation never rewrites. A valid schema-version-1 document stays
byte-identical at version 1, so valid v1 evidence is not silently upgraded to
v2. Refusal diagnostics carry only the repository-relative path and the
family's own stable error `code`; the underlying exception text and any host
filesystem detail are deliberately dropped.

#### Where the baseline evidence executes

There are 35 baseline comparisons: 34 parametrised stamped payloads plus one
replay-body byte-identity check. `tests/baseline_evidence.py` owns the policy.

| Environment | `SIM_INTENT_REQUIRE_BASELINE_EVIDENCE` | Baseline object | Behaviour |
|---|---|---|---|
| CI `backend-suite` (required) | `1` | present (`fetch-depth: 0`) | all 35 **execute**; a missing baseline **fails** the job |
| CI `schema-drift` (required) | `1` | present (`fetch-depth: 0`) | all 35 **execute**; a missing baseline **fails** the job |
| CI `container` job | unset | absent (`.git` excluded from the image) | those 35 **skip**, together with two baseline-*policy* meta-tests that need Git metadata to assert against; every runtime, migration, and safety-conformance test still executes |
| Local full-history checkout | optional | present | all 35 execute |

Both required hosted jobs additionally run an explicit preflight,
`git cat-file -e 6f92b5349d72fd7ef563293cd883c8b61fa3bbb5^{commit}`, so a
shallow checkout fails immediately with a clear message rather than producing a
green run with silent skips. Until this correction, `actions/checkout` defaulted
to `fetch-depth: 1`, so all 35 comparisons skipped in every hosted job and the
evidence gate never actually ran.

The container-only skips are intentional and are covered by the required hosted
non-skipping gate. Pytest prints a
`Task 19 baseline evidence: executed=… skipped=… failed=…` accounting line, so
the number that actually ran is visible in every job log.

### Frozen evaluation manifest (decision D-3)

The frozen 15-case manifest hash

```
47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b
```

is immutable baseline evidence recorded by Tasks 16 and 18. `manifest_hash()`
therefore excludes the record version declaration from its canonical bytes and
is unchanged. A separate `versioned_manifest_hash()` covers the declarations:

```
adb5201a93f4d4619a84f6b56f3e68ec12f975a345cc78e47178b0d7a719ff53
```

Both are asserted explicitly, including that a version change moves the
versioned hash and leaves the frozen hash alone.

### Replay sidecar manifest hashing

`eval/replay/manifest.json` records a SHA-256 per body over **LF-normalised**
bytes. The bodies are LF in the Git index but Git may hand a Windows working
tree CRLF, so hashing raw bytes would make the manifest platform-dependent and
fail in CI. Normalisation keeps the manifest identical on every supported
platform while staying sensitive to any real content change.

## 8. Generated contracts and drift checks

See [`schema/README.md`](../schema/README.md) for the artifact table and
regeneration commands.

| Artifact | Generator | Drift check |
|---|---|---|
| `schema/openapi.json` | `python scripts/export_schema.py` | `--check` (byte-exact) |
| `schema/simulation-intent.schema.json` | `python scripts/export_schema.py` | `--check` (byte-exact) |
| `schema/generated/typescript/api-types.ts` | `npm --prefix tools/openapi-types run generate` | regenerate then `git diff --exit-code` |

Determinism: sorted JSON keys, two-space indent, exactly one trailing newline,
and LF normalisation enforced by `.gitattributes` for `schema/**` and `*.ts`.

`openapi.json` is always generated in `production` runtime mode. The REPLAY
fallback routes register only in `replay`/`test` mode, so generating in another
mode would make the published contract depend on `SIM_INTENT_MODE`. A test
asserts mode independence.

Generator tooling lives at `tools/openapi-types/` and contains
`openapi-typescript` only — no React, Vite, `openapi-fetch`, or Playwright, and
no manifest at the repository root (decision D-5). `openapi-fetch` is a runtime
client library and belongs to Task 24's frontend dependencies.

The container images carry no Node toolchain, so the TypeScript drift gate runs
in the `frontend-smoke` CI job where Node 22.14.0 is pinned. The Python drift
checks run in the new `schema-drift` job and also inside the container.

## 9. What Task 19 did not do

No database, persistence, `Project`/`ModelVersion`/`SetupRevision`, material or
coordinate semantics, React UI, parser containment, geometry, meshing, solver,
result schema, or new runtime endpoint. No `/api/v1` route exists yet; the API
contract version is published statically (decision D-9).
