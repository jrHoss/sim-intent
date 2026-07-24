# Route and deployment decision

## Route policy

The default-route policy has exactly two release phases.

### Development and migration through Task 44

| Route | Owner | Required behavior |
|---|---|---|
| `GET /` | Legacy application | Serve the existing legacy application. No task before Task 45 may replace, redirect, or shadow it. |
| `GET /legacy` and legacy deep links | Legacy compatibility adapter | Serve the explicit rollback application using the same legacy assets and behavior. |
| `GET /static/*` | Legacy static owner | Preserve the current compatibility assets. |
| `GET /app-v2` and `/app-v2/*` | React technical-preview application | Serve the independently built V2 application and support browser deep links without falling back to legacy content. |
| `/api/v1/*` | Versioned backend API | Serve new product contracts from backend-owned OpenAPI. |
| Existing V1 API paths | Legacy compatibility handlers | Remain additive-only compatibility routes until a task explicitly retires one; they do not become V2 engineering truth. |
| Existing fallback/replay paths | Runtime-mode router | Absent from production. Available only in explicitly approved REPLAY or test modes. |
| `GET /events` | Legacy compatibility event broker | Legacy-only transient behavior; V2 must not consume it as state truth. |

Missing V2 build assets under `/app-v2` return a typed failure and never serve
test fixtures, legacy HTML, or an optimistic shell.

### Final technical-preview release gate: Task 45 only

If and only if Task 45 passes every release gate and records human approval:

| Route | Required behavior |
|---|---|
| `GET /` | Serve the approved V2 application. |
| `GET /legacy` | Retain the legacy application for the documented rollback window. |
| `GET /app-v2` and `/app-v2/*` | Retain V2 as a compatibility route during the rollback window. |

Task 45 owns the cutover decision and evidence. Task 44 may package and rehearse
the configuration but may not activate the default-route change. A failed or
unapproved Task 45 leaves `/` on the legacy application.

## Route invariants

1. Frontend routing never selects the runtime mode.
2. Production route registration physically excludes REPLAY, fixture, and
   fallback handlers.
3. V2 API clients use `/api/v1`; legacy endpoints are not silently repurposed.
4. `/legacy` does not bypass backend safety checks for V2 records.
5. Deep-link fallback is confined to the owning frontend route family.
6. Route changes do not migrate, copy, or dual-write engineering state.
7. Rollback changes route configuration and restores the compatible database
   backup; it does not reinterpret newer state through older code.

## Deployment decision

The supported deployment is a versioned Debian-stable OCI application image or
equivalent reproducible local package, with a writable local data volume:

```text
Host browser
    │
    ▼
Optional local/on-prem reverse proxy
    │ same origin
    ▼
Debian-stable application image
    ├── API/application process
    ├── fresh bounded geometry subprocesses (shared Gmsh slot)
    ├── durable local JobService
    ├── isolated CalculiX subprocesses (default concurrency 1)
    └── mounted data volume
          ├── SQLite database
          ├── SHA-256 artifact store
          ├── migration backups
          └── bounded temporary/job directories
```

The application binds to loopback by default. On-premises network exposure is
an operator decision requiring a trusted reverse proxy and transport controls;
it does not introduce tenancy or multi-user authorization into the active
release.

## Active deployment exclusions

- No cloud database or remote object store.
- No customer-side or remote solver runner.
- No connected Abaqus execution.
- No HPC scheduler.
- No multi-tenant control plane.
- No runtime dependency on the preserved post-preview roadmap.
