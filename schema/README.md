# Authoritative schema artifacts (Task 19)

Everything in this directory is **generated and checked in**. Do not hand-edit
any file here; regenerate it and commit the result.

| Artifact | Generator | Authority |
|---|---|---|
| `openapi.json` | `python scripts/export_schema.py` | Backend OpenAPI document; the API contract authority (ADR-004) |
| `simulation-intent.schema.json` | `python scripts/export_schema.py` | JSON Schema of the `SimulationIntent` payload contract |
| `generated/typescript/api-types.ts` | `npm run generate` in `tools/openapi-types` | TypeScript types for the React boundary (Task 24 consumes these) |

## Regeneration

```bash
python scripts/export_schema.py
cd tools/openapi-types && npm ci && npm run generate && cd ../..
```

The generator must run **from `tools/openapi-types`**: the npm script resolves
`../../schema/...` relative to that directory, and `npm --prefix` does not put
the local `node_modules/.bin` on `PATH`. CI uses `working-directory:` for the
same reason.

## Drift checks

```bash
python scripts/export_schema.py --check          # backend artifacts
cd tools/openapi-types && npm run generate && cd ../..
git diff --exit-code -- schema/generated         # generated TypeScript
```

Both run in CI. A drift failure means the checked-in artifact no longer matches
the backend; regenerate rather than editing the artifact.

## Determinism rules

- JSON is emitted with sorted keys, a two-space indent, and exactly one
  trailing newline.
- All files here are LF-normalised through `.gitattributes`, so the byte
  comparison is platform-independent.
- `openapi.json` is always generated in `production` runtime mode. The REPLAY
  fallback routes register only in `replay`/`test` mode, so generating in any
  other mode would make the published contract depend on `SIM_INTENT_MODE`.

## Versions

`info.version` in `openapi.json` is the string form of
`ir.schema_version.API_CONTRACT_VERSION`, the single authority for the API
contract version. Task 19 publishes no runtime version endpoint (decision D-9);
the constant, this snapshot, the generated TypeScript, and the drift tests are
the contract.

Payload schema versions are separate integers declared per family in
`ir/schema_version.py`.
