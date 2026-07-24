# Capability and unsupported-state matrix

## Authoritative capability states

Only `supported` permits the requested capability to proceed.

| State | Definition | Required behavior |
|---|---|---|
| `supported` | The request is inside the approved envelope and every environment, input, approval, validation, mapping, and revision prerequisite is satisfied. | Permit only the exact supported operation and record its lineage. |
| `unsupported` | The request is outside the approved technical-preview envelope. | Return the specific unsupported capability and do not approximate it. |
| `unavailable` | The capability is inside the envelope, but an environment or licensed dependency is absent. | Report the missing dependency without changing project/setup state. |
| `blocked` | The capability is inside the envelope, but a required input, engineer approval, or deterministic validation is missing or failed. | Identify each blocker and require a successor decision/input before retry. |
| `insufficient_evidence` | Deterministic grounding or source-to-target mapping cannot be proven. | Return candidates/evidence where safe and require clarification or new mapping evidence. |
| `stale` | A referenced identity, revision, approval, mesh, mapping, artifact, or event no longer matches the current lineage. | Reject the operation and identify the current identity/revision required for a new command. |

Every non-`supported` state fails closed. It emits no solver artifact, creates
no executable job, and cannot be downgraded to a warning by the frontend.
Multiple blockers may be returned, but the top-level state follows this
precedence for deterministic handling:

```text
unsupported
→ stale
→ insufficient_evidence
→ blocked
→ unavailable
→ supported
```

`unsupported` takes precedence because the product must not suggest that an
environment installation could enable excluded physics. `unavailable` is
evaluated only after the request and engineering inputs are otherwise eligible.

## Active release capability matrix

| Capability | Release classification | `supported` prerequisites | Non-supported examples | Owning tasks |
|---|---|---|---|---|
| Single-solid 3D STEP import | Included | Bounded valid parse; one supported solid; immutable ModelVersion | Assembly or invalid topology → `unsupported`/typed invalid result; parser absent → `unavailable` | 20–21 |
| Existing first-order-solid INP import | Included | Supported elements/sections; declared node/element sets; immutable ModelVersion | Shell/beam/unsupported element → `unsupported`; missing required set → `blocked` | 20–21, 28 |
| INP remeshing | Excluded | None | Always `unsupported` | 17, 34–35 |
| Geometry inspection | Included | Current ModelVersion and valid inventory/artifact | Old model binding → `stale`; parser evidence missing → `blocked` | 21, 25–26, 30–32, 39 |
| Natural-language setup proposal | Included | Approved provider configuration or deterministic path; typed requirements; bounded evidence | Provider absent → `unavailable`; ambiguous grounding → `insufficient_evidence` | 23, 28–33 |
| Direct face selection | Included | Verified viewer interaction bound to exact ModelVersion/revision | Wrong model/revision → `stale`; unresolvable pick → `insufficient_evidence` | 23, 25–26, 33 |
| Screenshot markup grounding | Optional only | Recorded camera projection plus deterministic ray casting and approved implementation | Missing projection/evidence → `insufficient_evidence`; arbitrary image reconstruction → `unsupported` | 17, 33 |
| Linear-elastic isotropic material | Included | Explicit properties/units/assignment and engineer confirmation | Nonlinear material → `unsupported`; missing approval/property → `blocked` | 28–29 |
| Static small-displacement structural physics | Included | Explicit fixed supported physics and settings | Buckling, dynamics, thermal, large deformation → `unsupported` | 28–29 |
| Fixed translational constraint | Included | Confirmed region and components; current mapping | Unconfirmed region → `blocked`; stale mapping → `stale` | 28–29, 35–36 |
| Prescribed translational displacement | Included | Confirmed region, components, units, coordinate reference, mapping | Rotation DOF/unsupported constraint → `unsupported`; ambiguous direction → `blocked` | 28–29, 35–36 |
| Resultant surface force | Included | Confirmed mapped surface, resultant semantics, magnitude/unit/vector | Incomplete mapping → `insufficient_evidence`; missing direction → `blocked` | 28–29, 35–36 |
| Surface traction | Included | Confirmed mapped surface, traction units/vector | Unsupported distribution → `unsupported` | 28–29, 35–36 |
| Pressure | Included | Confirmed mapped surface, pressure units and sign convention | Unproven surface orientation → `insufficient_evidence` | 28–29, 35–36 |
| Concentrated nodal force on STEP | Included after meshing | Explicit mesh node or deterministic point-to-node mapping bound to one MeshRevision | Before mesh → `blocked`; after remesh → `stale` | 34–36 |
| Concentrated nodal force on INP | Included | Explicit supported node or node set | Missing/unknown node set → `blocked`/`stale` | 28–29, 35–36 |
| Gravity | Included | Explicit vector/coordinate system, confirmed material density | Missing density/direction approval → `blocked` | 28–29, 36 |
| STEP tetrahedral meshing | Included | Supported STEP, confirmed setup regions, valid global size, Gmsh available | Gmsh absent → `unavailable`; invalid elements → `blocked` | 34 |
| Remeshing STEP | Included | New MeshRevision; full invalidation of mesh-bound derivatives | Reuse of prior mesh-bound node/surface → `stale` | 34–35 |
| CAD-to-mesh mapping | Included | Non-empty mapping, residual/area evidence, accepted required regions | Ambiguous/partial mapping → `insufficient_evidence` | 35 |
| Complete CalculiX deck | Included | Exact supported SetupRevision, MeshRevision/mapping, adapter version, successful preflight | Unsupported content → `unsupported`; missing approval → `blocked`; stale input → `stale` | 36 |
| Local CalculiX execution | Included | Installed supported solver; immutable approved package; JobService eligibility | `ccx` absent → `unavailable`; failed preflight → `blocked` | 38 |
| Result parsing and numerical checks | Included | Exact solver outputs, matching mesh/cardinality, versioned parser | Missing/corrupt output → `blocked`; mismatch → `stale` | 37–39 |
| Rerun and bounded comparison | Included | Immutable predecessor/successor setup and run identities | Mutable overwrite request → `unsupported`; unrelated field transfer → `unsupported` | 39 |
| Reproducibility bundle | Included | Exact complete lineage and hashes | Missing artifact/result → `blocked` | 36–39 |
| Abaqus Python export | Included as export-only | Verified named/set mapping; supported setup; deterministic script; documented Abaqus version validation | Positional STEP face mapping → `insufficient_evidence`; licensed environment absent for validation → `unavailable` | 35–36, 42 |
| Product-submitted Abaqus execution | Excluded | None | Always `unsupported` | 17, 42 |
| Assemblies, contact, shells, beams, connectors, composites | Excluded | None | Always `unsupported` | 17 |
| Nonlinear/advanced physics and certification | Excluded | None | Always `unsupported` | 17 |
| Customer/remote runners, HPC, SaaS, collaboration | Excluded | None | Always `unsupported` | 17 |
| Learned classifier as release dependency | Excluded | None | Always `unsupported` for the active release | 17, 31–33 |

## Artifact and job gate

Before artifact generation or job creation, the backend evaluates:

1. capability is not outside the envelope;
2. all referenced domain identities exist and are current;
3. every material, coordinate, region, assumption, and setup decision is
   accepted for the exact SetupRevision;
4. deterministic validation passes;
5. every required region has accepted non-empty MappingEvidence;
6. mesh, mapping, adapter, and solver versions match the requested package;
7. the required local environment is available.

Any failed item returns its typed capability state and RFC 9457 problem
details. The command has no partial artifact/job side effect.
