# Reproducible environments and runtime modes (Task 18)

This document is the supported environment contract established by Task 18
under ADR-005, ADR-006, and ADR-007. It covers exact tool versions, the
supported Linux container, runtime-mode separation, clean installation, the
dependency update procedure, CI, SBOM evidence, and rollback.

## Exact supported versions

| Component | Version | Pin location |
|---|---|---|
| CPython | 3.13.14 (`requires-python >=3.13,<3.14`) | `.python-version`, `pyproject.toml`, container image |
| uv | 0.11.32 | `docs/environment.md`, `Containerfile`, `.github/workflows/ci.yml` |
| Python direct dependencies | fastapi 0.139.2, gmsh 4.15.2, meshio 5.3.5, numpy 2.5.1, openai 2.46.0, pydantic 2.13.4, uvicorn 0.51.0 | `pyproject.toml` (exact `==`) |
| Dev dependency group | pytest 9.1.1, httpx 0.28.1, pytest-timeout 2.4.0 | `pyproject.toml` `[dependency-groups] dev` |
| Transitive closure | 35 locked packages | `uv.lock` (authoritative) |
| Base image | `python:3.13.14-slim-trixie@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91` | `Containerfile` |
| Debian apt source | `snapshot.debian.org` frozen at `20260720T000000Z` | `Containerfile` |
| Node (legacy JS syntax checks only) | 22.14.0 | `.github/workflows/ci.yml` |
| gitleaks | 8.30.1 (sha256-verified) | `.github/workflows/ci.yml` |
| syft | 1.49.0 (sha256-verified) | `.github/workflows/ci.yml` |

`uv.lock` is the authoritative direct and transitive lock. `requirements.txt`
is a generated, hashed compatibility export; regenerate or verify it only with
`python scripts/export_requirements.py [--check]`. Never edit either by hand.

`scipy`, `typer`, and `rich` were removed from the declared dependency set by
approved Task 18 decision D3: no repository code imports them. (`rich`
remains in the closure as a transitive dependency of `meshio`.)

## Native Linux packages (measured closure)

The locked gmsh 4.15.2 manylinux wheel requires these shared libraries,
measured with `ldd` inside the pinned base image (not assumed from generic
lists): `libGL.so.1`, `libGLU.so.1`, `libX11.so.6`, `libXcursor.so.1`,
`libXext.so.6`, `libXfixes.so.3`, `libXft.so.2`, `libXinerama.so.1`,
`libXrender.so.1`, `libfontconfig.so.1`, `libgomp.so.1`.

They are satisfied by exactly these Debian trixie packages, installed from the
frozen snapshot with pinned versions (see `Containerfile`):

```
libgl1=1.7.0-1+b2            libglu1-mesa=9.0.2-1.1+b3   libx11-6=2:1.8.12-1
libxcursor1=1:1.2.3-1        libxext6=2:1.3.4-1+b3       libxfixes3=1:6.0.0-2+b4
libxft2=2.3.6-1+b4           libxinerama1=2:1.1.4-3+b4   libxrender1=1:0.9.12-1
libfontconfig1=2.15.0-2.3    libgomp1=14.2.0-19
```

The measured runtime closure of the source-built ccx executable adds
(`ldd` inside the image): `libarpack2t64=3.9.1-6`, `liblapack3=3.12.1-6`,
`libblas3=3.12.1-6`, `libgfortran5=14.2.0-19`.

The complete installed-package manifest is written to
`/opt/native-packages.txt` inside every image at build time.

## CalculiX availability

The `calculix-ccx` package was removed from Debian trixie, and cross-release
package mixing is not permitted. The supported images therefore build
CalculiX **ccx 2.23 from the official hash-verified source archives** in a
dedicated container builder stage (`ccx-builder` in `Containerfile`):

- `ccx_2.23.src.tar.bz2` from the author's site (dhondt.de), GPL, SHA-256
  `9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7`;
- `spooles.2.2.tgz` from netlib.org, public domain per its documentation,
  SHA-256
  `a84559a0e987a1e423055ef4fdf3035d55b65bbe4bf915efaa1a35bef7f8c5dd`;
- build dependencies from the frozen snapshot with exact pins
  (`gcc=4:14.2.0-1`, `gfortran=4:14.2.0-1`, `make=4.4.1-2`,
  `libarpack2-dev=3.9.1-6`, `bzip2=1.0.8-6`, `wget=1.25.0-2`,
  `ca-certificates=20250419`); compilers never reach the runtime/ci images —
  only the stripped `/usr/local/bin/ccx` executable is copied in;
- runtime libraries from trixie (`libarpack2t64=3.9.1-6`,
  `liblapack3=3.12.1-6`, `libblas3=3.12.1-6`, `libgfortran5=14.2.0-19`);
- build adjustments are compiler-conformance flags only (SPOOLES compiled as
  `gnu89`; GCC 14's newly promoted C errors downgraded back to warnings;
  `-fallow-argument-mismatch` for legacy Fortran; shared
  arpack/lapack/blas replaces the static ARPACK path). No source patches.
- The ccx executable embeds its build date, so its byte hash is recorded per
  build rather than claimed bit-reproducible; all build inputs are
  hash-pinned.

Outside the supported container (for example Windows development), a missing
`ccx` remains an `unavailable` capability (ADR-007), never an
environment-gate failure: `scripts/check_env.py` reports
`CCX UNAVAILABLE (optional; solver capability reports unavailable)` and still
exits `ENV OK`. Inside the supported images `check_env.py` reports the ccx
version, and the optional solver smoke test in `tests/test_export.py`
executes instead of skipping.

No automatic solver execution or JobService exists; that is Task 38.

## Runtime modes

Exactly one mode is fixed when an application instance is constructed
(`SIM_INTENT_MODE`; `app/runtime_mode.py`); it can never change on a running
application. Unset or empty selects `production`. Unknown values fail startup
with a configuration error naming the accepted vocabulary.

| Mode | Fallback routes (`/session/{id}/fallback-cases`, `/session/{id}/fallback/{case}`) | Purpose |
|---|---|---|
| `production` | absent (unregistered; 404) | default deployment |
| `live_evaluation` | absent (unregistered; 404) | labeled LIVE evaluation |
| `replay` | present, always labeled REPLAY | deterministic checked-in replay |
| `test` | present | automated test suite (set by `conftest.py`) |

Production guarantees:

- fallback routes are never registered — 404 comes from an unregistered
  route, not a mounted handler;
- the runtime container image physically excludes `eval/`, `tests/`,
  `fixtures/`, and `examples/`, so REPLAY fixtures cannot be loaded
  regardless of configuration, and the application imports and starts with
  those trees absent;
- a LIVE provider failure returns a typed 503 with
  `fallback_available: false` and never substitutes REPLAY output (no mode
  substitutes REPLAY for LIVE — in replay/test modes REPLAY data is served
  only by the explicit, labeled fallback route).

`GET /healthz` reports exactly `{"status": "ok", "mode": "<mode>"}` — no
secrets, paths, or environment contents.

## Clean installation

### Local durable data

Project metadata and source blobs use one local data root. Set
`SIM_INTENT_DATA_ROOT` to an absolute path for an explicit deployment or test
override. Without it, the stable default is `sim-intent` beneath
`LOCALAPPDATA` on Windows or beneath `XDG_DATA_HOME` (falling back to
`~/.local/share`) on other platforms. The default is independent of the
process working directory. SQLite is stored as `sim-intent.sqlite3` and blobs
under `blobs/` within that root. Persistence is opened and migrated during
FastAPI lifespan startup and its SQLAlchemy engine is disposed at shutdown.
Exactly one application process may own a data root at a time. Before creating
or touching the target root, startup canonicalizes its absolute path, hashes
that identity with SHA-256, and takes an exclusive cross-platform
operating-system lock named `<hash>.lock` under the dedicated
`sim-intent-data-root-locks` directory in the platform temporary directory.
The lock is held for the complete application lifespan. A second process must
use a different absolute `SIM_INTENT_DATA_ROOT` or wait for the owner to exit.
The external lock file can remain after a crash; operating-system lock
ownership, rather than file existence, determines availability. Thread-level
blob publication and cleanup are additionally serialized by an in-process
lock, but that lock is not the cross-process ownership mechanism.

Durable STEP/INP uploads are streamed to `quarantine/` under the same data
root and published to `blobs/` only after isolated parsing succeeds.

| Environment variable | Default |
|---|---:|
| `SIM_INTENT_MAX_SOURCE_UPLOAD_BYTES` | 67108864 (64 MiB) |
| `SIM_INTENT_MAX_SOURCE_STORAGE_BYTES` | 1073741824 (1 GiB) |
| `SIM_INTENT_QUARANTINE_DIR` | `<data-root>/quarantine` |
| `SIM_INTENT_PARSER_TIMEOUT_SECONDS` | 30 |
| `SIM_INTENT_PARSER_OUTPUT_BYTES` | 262144 per output stream |
| `SIM_INTENT_STALE_QUARANTINE_AGE_SECONDS` | 3600 |
| `SIM_INTENT_STALE_QUARANTINE_CLEANUP_LIMIT` | 100 |

The source-storage limit counts unique regular blobs in the fixed-depth
`blobs/sha256` CAS only. Deduplicated content is counted once; SQLite,
quarantine, external locks, symlinks, and malformed/unrelated entries are
excluded. Capacity failures do not evict historical sources.

Before each unique publication attempt, coordinated orphan reclamation
processes at most 100 valid unreferenced CAS candidates. If more than 100
orphans exist, later publication attempts or explicit maintenance may be
needed to reclaim the remainder. Historical referenced blobs are never
evicted automatically.

Integer limits must be positive and the stale age non-negative; invalid values
must also be finite. Quarantine must be an absolute directory beneath the data
root, but outside the blob/CAS tree, SQLite path, and external lock tree;
invalid settings fail startup configuration. The parser uses a controlled argument vector, a
minimal environment, a deterministic working directory, and no shell. This
fresh-process boundary contains parser crashes and global library state, but
is not a hostile sandbox. OS-level CPU and memory quotas remain deferred
reliability/security work.

### Supported Linux container (release environment)

```bash
docker build --target runtime -t sim-intent:runtime -f Containerfile .
docker build --target ci      -t sim-intent:ci      -f Containerfile .
docker run --rm sim-intent:ci python scripts/check_env.py
docker run --rm sim-intent:ci python -m pytest tests -q \
  --deselect tests/test_eval.py::test_raw_fixture_hashes_match_git_archive_and_reject_different_bytes
docker run --rm sim-intent:ci python eval/run.py --replay
docker run --rm -p 127.0.0.1:8000:8000 sim-intent:runtime   # serves / and /healthz
```

The single deselected test requires `.git` metadata and is exercised by the
host-checkout suite instead (same handling as the Task 16 clean-archive
evidence).

### Windows / local development (not release evidence)

```powershell
# once: install uv 0.11.32 (e.g. pip install uv==0.11.32 in a tools env)
uv sync --frozen          # creates .venv with CPython 3.13.14 and all groups
uv run python scripts/check_env.py
uv run pytest tests -q
uv run python eval/run.py --replay
```

`uv sync` manages the repository `.venv`, so the existing documented commands
(`.\.venv\Scripts\python.exe -m pytest tests -q`, `python eval\run.py`)
continue to work unchanged, including the `eval/run.py` `.venv` re-exec.

## Dependency update procedure

1. Edit the exact pin in `pyproject.toml` (runtime) or the `dev` group.
2. `uv lock` — regenerates `uv.lock` (review the diff).
3. `uv sync --frozen` — update the local environment.
4. `python scripts/export_requirements.py` — regenerate the compatibility
   export.
5. Run the environment gate, the full suite, and REPLAY evaluation.
6. Record versions and evidence in the progress ledger with the change.

Native-package updates change the `DEBIAN_SNAPSHOT` argument and the exact
pinned versions in `Containerfile` together, followed by a rebuild and the
container checks above.

## CI

`.github/workflows/ci.yml` runs bounded jobs (every job has
`timeout-minutes`; every test has a 120-second `pytest-timeout` default set in
`pyproject.toml`, with the CI job timeout as the outer bound):

| Job | Content | Bound |
|---|---|---|
| lock-and-drift | `uv lock --check`, clean `uv sync --frozen`, byte-exact requirements drift | 15 min |
| backend-suite | environment gate + full suite on the runner | 30 min |
| replay-eval | deterministic REPLAY evaluation (never LIVE) | 15 min |
| container | runtime/ci builds, production exclusion + startup checks, in-container suite and REPLAY, digests, SBOM | 45 min |
| schema-drift | byte-exact OpenAPI / IR-schema export check, payload stamping check, schema-version and contract suites (Task 19) | 15 min |
| frontend-smoke | `node --check` on the two legacy files, plus `npm ci` + TypeScript regeneration + `git diff --exit-code` drift gate, Node 22.14.0 | 10 min |
| hygiene | whitespace check, sha256-verified gitleaks scan | 15 min |

All actions are pinned by full commit SHA; downloaded tools are exact
versions verified by SHA-256. LIVE evaluation is never run in CI. No test can
hang indefinitely.

## SBOM and license evidence

The SBOM is generated from the final runtime image (not only the Python
export) with pinned syft as a CI artifact (`sbom-runtime.cdx.json`,
CycloneDX), using `SYFT_FILE_METADATA_SELECTION=all` so the source-built
`/usr/local/bin/ccx` executable is included with its SHA-256. The SBOM's
SHA-256 is recorded in the progress ledger per run. The large generated file
is not checked into the repository.

## Frontend / Node policy

No frontend application exists. When Task 24 introduces `frontend/`, npm with a
committed `package-lock.json` and a pinned Node LTS version are the approved
tooling (ADR-005). Task 18 intentionally created no `package.json`,
`package-lock.json`, or frontend code.

Task 19 added exactly one Node surface: **generator tooling only**, at
`tools/openapi-types/`, with an exact `openapi-typescript` pin and a committed
`package-lock.json`. It contains no React, Vite, `openapi-fetch`, Playwright, or
other application dependency, and there is deliberately no manifest at the
repository root. It exists solely to turn `schema/openapi.json` into the
checked-in `schema/generated/typescript/api-types.ts`.

```bash
cd tools/openapi-types
npm ci                                       # lockfile-exact install
npm run generate
cd ../..
git diff --exit-code -- schema/generated     # drift gate
```

The generator must run from `tools/openapi-types`: its npm script resolves
`../../schema/...` relative to that directory, and `npm --prefix` does not put
the local `node_modules/.bin` on `PATH`. CI uses `working-directory:`.

`tools/openapi-types/node_modules/` is git-ignored and excluded from the
container build context. The supported container images carry **no** Node
toolchain, so the TypeScript drift gate runs only in the `frontend-smoke` CI
job; the Python schema drift checks (`scripts/export_schema.py --check`,
`scripts/stamp_schema_versions.py --check`) run on the runner and inside the
container. See `docs/schema-versioning.md`.

## Rollback

Task 18 changes no persistent data, schema, tag, or fixture. To roll back:

1. Revert the Task 18 commit(s) (restores floating `requirements.txt`,
   removes runtime-mode gating, container, and CI).
2. Remove `.venv` and recreate it from the reverted `requirements.txt` if
   desired (`python -m venv .venv && .venv/Scripts/pip install -r requirements.txt`).
3. Remove the built local images (`docker rmi sim-intent:runtime sim-intent:ci`).

To disable only the mode gating without a revert, run the application with
`SIM_INTENT_MODE=test`, which restores the pre-Task-18 route surface exactly.
