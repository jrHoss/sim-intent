# ADR-004: API, payload schemas, generated client, and errors

**Status:** Accepted by human Task 17 decision on 2026-07-24; independent
read-only review required before commit
**Technical Review Owner:** Ahmed Yassin

## Context

Persistent state and a new frontend cannot safely depend on unversioned
payloads, handwritten duplicate TypeScript domain schemas, or endpoint-specific
error bodies.

## Options considered

1. Versioned `/api/v1`, backend OpenAPI authority, integer payload versions,
   checked-in `openapi-typescript`/`openapi-fetch` output, and RFC 9457 errors.
2. Keep unversioned product routes and infer payload shape.
3. Handwrite frontend schemas and migrations.
4. Generate an opaque client without checked-in drift evidence.
5. Preserve custom error bodies per endpoint.

Option 1 creates one inspectable contract and deterministic drift checks.
Options 2–5 permit silent schema divergence or inconsistent failure handling.

## Decision

- New product APIs use `/api/v1`.
- Existing V1 paths remain explicit compatibility contracts and are not
  silently repurposed.
- Backend OpenAPI is authoritative for HTTP shapes.
- Versioned product and persisted JSON payloads declare a positive integer
  `schema_version`.
- Backend sequential migration registries accept supported historical
  versions, emit the current version, and reject unsupported future versions.
- Use `openapi-typescript` and `openapi-fetch` for the React boundary.
- Check generated output into the repository and fail a drift check when
  regeneration changes it.
- The frontend does not handwrite canonical domain schemas or migrations.
- Errors use RFC 9457 `application/problem+json` with:
  - HTTP status;
  - stable application `code`;
  - human-safe `title` and `detail`;
  - correlation/trace ID;
  - explicit retryability;
  - safe typed details such as current revision, blockers, or capability
    state.
- Problem details never expose credentials, prompt content, solver/source
  bytes, or absolute host paths.

## Consequences

- Task 19 must define schema and migration drift tests before persistent
  product records.
- Client changes are reviewable repository diffs.
- Checked-in generated output adds mechanical changes but enables clean
  frontend builds without code-generation ambiguity.
- Stable application codes become compatibility contracts and require a
  deprecation policy.
- Detailed diagnostics remain server-side and are correlated by trace ID.

## Downstream gate

This decision blocks Tasks 19, 20, 24, and all later product API/UI tasks if
versioning, client drift, or normalized error behavior is missing.
