# Geometry identity and durable CAD selection

This document describes what a CAD-face selection means in this system, what is
authoritative about it, and what is deliberately not.

## The one authority

**The backend is the sole stable-identity authority.** A stable face identity
(`gfi1:…`) is computed by `geom/identity.py` from geometric and topological
face properties, written once into a persisted geometry-identity artifact bound
to exactly one ModelVersion, and never recomputed at read time.

No client mints an identity. The browser submits an *unresolved* claim — the
face tag it observed and the ModelVersion it observed it on — and the backend
resolves it against that one exact persisted artifact.

## The two things a CAD region carries

A `cad_face` region has no `entity_ids`. Its only evidence is
`cad_face_target`, a discriminated union on `resolution`:

| `resolution` | Meaning | Confirmable |
| --- | --- | --- |
| `resolved` | One unique stable identity per claimed face | yes |
| `ambiguous` | Faces are geometrically indistinguishable; a collision group is recorded instead | no |
| `unresolved` | No stable claim yet — what a viewer click submits | no |
| `legacy_local_only` | Valid pre-v3 numeric evidence with no stable authority | no |
| `invalid_legacy_evidence` | Historical pre-v3 evidence that violates v3 constraints, preserved read-only | no |

Within a resolved or ambiguous target:

- `stable_identities` / `collision_group_ids` are **backend-authoritative**.
- `model_version_id` and `artifact_sha256` scope that authority to one exact
  ModelVersion and one exact artifact. They are never transferable.
- `source_face_tags` are **ModelVersion-local viewer evidence only**. They
  address geometry in the viewer for the bound version and nothing else. They
  are never a solver, mesh, node, element, set, or surface identifier, and a
  source face tag is never converted into a CAD `entity_ids` value.

Non-CAD regions (`mesh_face`, `node_set`, `element_set`, `cad_edge`) keep
`entity_ids` as their native membership field. That is legitimate and
unaffected by anything above.

## Reading the selection projection

`SetupRevisionResponse.cad_selection_evidence` publishes, per CAD region, what
is true right now. Two of its fields are deliberately separate:

- `stable_identity_authoritative` — the backend resolved this selection to a
  unique stable identity against the setup's exact persisted artifact.
- `viewer_binding_valid` — the bound ModelVersion is still the setup's live
  source, so `source_face_tags` and `viewer_node_names` still address the
  geometry on screen.

**A resolved identity can remain authoritative while its viewer binding is
invalid.** After the source is replaced, the stable identity stays truthful
historical evidence about the version it was bound to, but the viewer addressing
no longer describes anything displayed. Conflating the two would let a
superseded selection read as a live confirmed boundary.

`selected_entities` and `highlight_state` are viewer addressing for the bound
ModelVersion. They are not authoritative CAD membership. Check
`viewer_binding_valid` before drawing a CAD highlight as a live confirmed
boundary.

## Source replacement

Uploading a new version of a model marks existing setups stale
(`stale_reason: source_replaced`). A stale setup:

- stays bound to its original ModelVersion and artifact digest,
- keeps its regions, statuses, identities, and history unchanged,
- **is never rebound** to the successor version,
- reports `viewer_binding_valid: false`,
- rejects further mutation with `setup_source_superseded`,
- is blocked from export by `source.stale`.

The old artifact digest is unusable against the successor, and a target naming a
different ModelVersion is refused with `cad_region_model_version_mismatch`.

## Failure behaviour

Every failure is closed, sanitized (RFC 9457, `trace_id`, no host paths or
source bytes), and leaves nothing partially persisted:

| Condition | Code |
| --- | --- |
| CAD region carries `entity_ids` | `cad_region_entity_ids_forbidden` |
| CAD region against a native mesh source | `cad_region_not_applicable` |
| Target names another ModelVersion | `cad_region_model_version_mismatch` |
| Target names another artifact digest | `cad_region_artifact_mismatch` |
| Stored artifact missing / corrupt | `cad_region_artifact_missing`, `cad_region_artifact_integrity_failed` |
| Claimed identity or group absent from the artifact | `cad_region_identity_unknown`, `cad_region_collision_group_unknown` |
| Claimed evidence disagrees with the artifact | `cad_region_identity_evidence_inconsistent`, `cad_region_collision_evidence_inconsistent` |
| Confirming an unresolved or ambiguous target | `cad_region_unresolved` (409) |
| Legacy evidence from a current client | `cad_region_legacy_client_forbidden` |

A corrupt stored artifact is never repaired on read.

## Meshing boundary

R5.2 implements an internal deterministic STEP tetrahedral meshing service,
constructed during application startup, with immutable mesh revisions, exact
setup and source lineage, and mesh-local exterior triangles. It does not expose
a public mesh HTTP API or a frontend mesh-generation workflow.

Stable CAD-face-to-mesh-boundary mapping remains deferred. Exterior triangles
therefore do not correspond to stable CAD faces, and mapping-dependent STEP or
boundary-target export remains blocked by `artifact.mapping_not_verified`.

## Ambiguity coverage

The frozen evaluation fixtures (`bracket.step`, `plate_hole.step`) resolve every
face uniquely and produce no collision groups. Ambiguity is therefore covered
**synthetically**, by substituting an artifact containing two identical faces.
No end-to-end ambiguous real-CAD demonstration is claimed.
