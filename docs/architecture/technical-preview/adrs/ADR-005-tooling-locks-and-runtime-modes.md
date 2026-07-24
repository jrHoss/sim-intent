# ADR-005: Package locks, OCI baseline, and runtime modes

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Release Owner:** Maein, subject to acceptance

## Context

The repository has unpinned Python dependencies, no frontend package, no
supported container baseline, and always-mounted replay/fallback behavior.
Task 18 needs approved tooling without Task 17 installing or selecting exact
versions.

## Options considered

1. `uv`/`uv.lock`, npm/`package-lock.json`, a versioned Debian-stable OCI
   baseline, and one immutable startup mode.
2. pip-tools plus pnpm.
3. Poetry plus Yarn.
4. Floating requirements and combinable runtime feature flags.

Options 1–3 can be reproducible, but option 1 provides one direct lock artifact
per existing language ecosystem with low operational complexity. Floating
requirements and combinable flags cannot prove environment or mode identity.

## Decision

- Use `uv` and commit `uv.lock` for Python direct and transitive dependencies.
- Use npm and commit `package-lock.json` for the React frontend.
- Use a versioned Debian-stable OCI baseline.
- Task 18 owns actual installation, supported version selection, lock
  generation, system-package selection, image implementation, and SBOM
  baseline.
- Exactly one runtime mode is selected and frozen at process startup:
  - `production`;
  - `live_evaluation`;
  - `replay`;
  - `test`.
- Production physically excludes replay routes, fallback behavior, evaluation
  fixtures, and test-only assets from route registration and product bundles.
- No runtime failure may fall back from LIVE to REPLAY.
- Mode identity appears in health/diagnostic evidence without exposing secrets.

## Consequences

- Dependency updates require explicit lock regeneration and review.
- The OCI baseline must include documented Gmsh/CalculiX system dependencies in
  Task 18.
- npm is selected over alternative frontend package managers; a future change
  requires an ADR update and lock migration.
- Separate process startup is required to change mode.
- Production image/bundle scans become a release check.

## Downstream gate

This decision blocks Task 18 until implemented and reviewed. Mode separation
also gates Tasks 23, 33, 41, 43, and 45.
