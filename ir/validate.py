"""Deterministic Task 13 validation for solver-neutral simulation intent.

Validation is deliberately side-effect free: it performs no conversion,
grounding, network access, geometry mutation, export generation, or solver
execution.  ``ground.semantics`` remains the sole owner of unit conversion and
load semantics.  The existing ``SimulationIntent.export_payload`` method
remains the final architectural confirmation gate.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from ir.canonical import (
    canonical_bc_semantics,
    canonical_load_semantics,
    canonical_semantic_key,
)
from ir.schema import (
    BC_REGION_COMPATIBILITY,
    LOAD_REGION_COMPATIBILITY,
    EngineeringConsistencyError,
    ExportBlockedError,
    Region,
    RegionTargetRule,
    SimulationIntent,
    StrictModel,
    enforce_cad_region_entity_ids_invariant,
    material_proposal_fingerprint,
    region_entity_membership,
)


IssueSeverity = Literal["error", "warning"]

#: Codes that mean "a required part of the setup has not been stated yet".
#: They take precedence over every other finding: an incomplete setup cannot be
#: meaningfully judged semantically valid, and a stale source must not hide the
#: fact that the setup was never finished.  A schema-version-1 payload migrated
#: forward lands here, never in ``ready``.
STRUCTURALLY_INCOMPLETE_CODES: frozenset[str] = frozenset(
    {
        "analysis.missing",
        "analysis.dimensionality_missing",
        "analysis.solver_target_missing",
        "analysis.coordinate_system_missing",
        "units.missing",
        "units.incomplete",
        "material.missing",
        "bc.missing",
        "load.missing",
        "mesh.missing",
        "solver.missing",
    }
)


class ValidationIssue(StrictModel):
    """One stable, JSON-serializable validation or readiness finding."""

    code: str = Field(min_length=1)
    severity: IssueSeverity
    message: str = Field(min_length=1)
    blocks_export: bool
    object_type: str | None = None
    object_id: str | None = None
    field: str | None = None


class UnresolvedLoadResultant(StrictModel):
    load_index: int = Field(ge=0)
    load_type: Literal["pressure", "surface_traction"]
    region_ref: str
    reason_code: Literal["geometry.surface_area_required"]


class LoadSummary(StrictModel):
    """Canonical load totals that require no geometry inference."""

    explicit_force_vector_sum_N: list[float] = Field(min_length=3, max_length=3)
    concentrated_force_total_N: list[float] = Field(min_length=3, max_length=3)
    resultant_surface_force_total_N: list[float] = Field(min_length=3, max_length=3)
    gravity_accelerations_mm_per_s2: list[list[float]]
    gravity_density_required: bool
    gravity_density_available: bool
    concentrated_force_count: int = Field(ge=0)
    resultant_surface_force_count: int = Field(ge=0)
    pressure_load_count: int = Field(ge=0)
    traction_load_count: int = Field(ge=0)
    gravity_load_count: int = Field(ge=0)
    distributed_load_count: int = Field(ge=0)
    distributed_load_types: dict[str, int]
    unresolved_resultants: list[UnresolvedLoadResultant]


class ValidationReport(StrictModel):
    """Computed validation state; client-supplied status is never consulted."""

    validation_status: Literal["valid", "invalid"]
    #: Declared in the deterministic precedence order applied below.
    readiness_status: Literal[
        "structurally_incomplete",
        "semantically_invalid",
        "stale_source",
        "awaiting_region_confirmation",
        "awaiting_assumption_acceptance",
        "ready",
    ]
    engineering_ready: bool
    export_eligible: bool
    load_summary: LoadSummary
    issues: list[ValidationIssue]


_SEVERITY_ORDER = {"error": 0, "warning": 1}


def _issue(
    issues: list[ValidationIssue],
    code: str,
    severity: IssueSeverity,
    message: str,
    *,
    blocks_export: bool,
    object_type: str | None = None,
    object_id: str | None = None,
    field: str | None = None,
) -> None:
    issues.append(
        ValidationIssue(
            code=code,
            severity=severity,
            message=message,
            blocks_export=blocks_export,
            object_type=object_type,
            object_id=object_id,
            field=field,
        )
    )


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _canonical_material_scalar(value: object) -> str:
    """Canonicalize one normalized material scalar for semantic comparison.

    Fifteen significant decimal digits preserve meaningful submitted
    engineering values while collapsing the one-ULP noise that can result when
    equivalent supported units are converted through different power-of-ten
    factors (for example kg/m^3 versus tonne/mm^3).  Signed zero has no
    material meaning.
    """

    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return f"invalid:{type(value).__name__}:{value!s}"
    if numeric == 0.0:
        numeric = 0.0
    return format(numeric, ".15g")


def _material_semantic_key(material: object) -> tuple[str, str, str, str, str | None]:
    """Return material engineering identity without authority/provenance."""

    density = getattr(material, "density_tonne_per_mm3", None)
    return (
        str(getattr(material, "name", "<missing-material-name>")),
        str(getattr(material, "model", "<missing-material-model>")),
        _canonical_material_scalar(getattr(material, "E_MPa", None)),
        _canonical_material_scalar(getattr(material, "nu", None)),
        None if density is None else _canonical_material_scalar(density),
    )


def summarize_loads(intent: SimulationIntent) -> LoadSummary:
    """Compute only resultants explicitly present in the durable intent."""

    concentrated_components: list[list[float]] = [[], [], []]
    surface_components: list[list[float]] = [[], [], []]
    gravity: list[list[float]] = []
    distributed: dict[str, int] = {}
    unresolved_inputs: list[tuple[str, str, str]] = []
    counts = {
        "concentrated_force": 0,
        "resultant_surface_force": 0,
        "pressure": 0,
        "surface_traction": 0,
        "gravity": 0,
    }
    for load in intent.loads:
        load_type = load.type
        counts[load_type] += 1
        if load_type == "concentrated_force":
            for axis in range(3):
                concentrated_components[axis].append(float(load.vector[axis]))
        elif load_type == "resultant_surface_force":
            for axis in range(3):
                surface_components[axis].append(float(load.vector[axis]))
        if load_type != "concentrated_force":
            distributed[load_type] = distributed.get(load_type, 0) + 1
        if load_type == "gravity":
            gravity.append([float(value) for value in load.vector])
        elif load_type in {"pressure", "surface_traction"}:
            unresolved_inputs.append(
                (
                    canonical_semantic_key(canonical_load_semantics(load)),
                    load_type,
                    load.region_ref,
                )
            )
    concentrated = [math.fsum(values) for values in concentrated_components]
    surface_resultant = [math.fsum(values) for values in surface_components]
    explicit = [
        math.fsum(
            [*concentrated_components[axis], *surface_components[axis]]
        )
        for axis in range(3)
    ]
    gravity.sort(key=lambda vector: tuple(float(value).hex() for value in vector))
    unresolved = [
        UnresolvedLoadResultant(
            load_index=index,
            load_type=load_type,
            region_ref=region_ref,
            reason_code="geometry.surface_area_required",
        )
        for index, (_key, load_type, region_ref) in enumerate(
            sorted(unresolved_inputs)
        )
    ]
    density_available = (
        len(intent.materials) == 1
        and intent.materials[0].density_tonne_per_mm3 is not None
    )
    return LoadSummary(
        explicit_force_vector_sum_N=explicit,
        concentrated_force_total_N=concentrated,
        resultant_surface_force_total_N=surface_resultant,
        gravity_accelerations_mm_per_s2=gravity,
        gravity_density_required=bool(gravity),
        gravity_density_available=density_available,
        concentrated_force_count=counts["concentrated_force"],
        resultant_surface_force_count=counts["resultant_surface_force"],
        pressure_load_count=counts["pressure"],
        traction_load_count=counts["surface_traction"],
        gravity_load_count=counts["gravity"],
        distributed_load_count=sum(distributed.values()),
        distributed_load_types=dict(sorted(distributed.items())),
        unresolved_resultants=unresolved,
    )


def _validate_vector(
    issues: list[ValidationIssue],
    vector: object,
    *,
    object_type: str,
    object_id: str,
    field: str,
) -> None:
    if not isinstance(vector, (list, tuple)) or len(vector) != 3:
        _issue(
            issues,
            f"{object_type}.vector_invalid",
            "error",
            "Vector must contain exactly three components.",
            blocks_export=True,
            object_type=object_type,
            object_id=object_id,
            field=field,
        )
        return
    if not all(_finite(component) for component in vector):
        _issue(
            issues,
            f"{object_type}.vector_nonfinite",
            "error",
            "Vector components must all be finite.",
            blocks_export=True,
            object_type=object_type,
            object_id=object_id,
            field=field,
        )
        return
    if not any(float(component) != 0.0 for component in vector):
        _issue(
            issues,
            f"{object_type}.vector_zero",
            "error",
            "Vector magnitude must be nonzero.",
            blocks_export=True,
            object_type=object_type,
            object_id=object_id,
            field=field,
        )


def _check_engineering_consistency(
    issues: list[ValidationIssue],
    item: object,
    *,
    object_type: str,
    object_id: str | None = None,
) -> None:
    """Mirror the schema's original/normalized checks as validation issues.

    ``ir.schema`` rejects a contradiction outright, so a payload that reached
    here through the normal model path is already consistent.  Objects built
    below the schema with ``model_construct`` are not, and this keeps the same
    stable code reaching the client either way.
    """

    checker = getattr(item, "check_consistency", None)
    if not callable(checker):
        return
    try:
        checker()
    except EngineeringConsistencyError as exc:
        _issue(
            issues,
            exc.code,
            "error",
            str(exc),
            blocks_export=True,
            object_type=object_type,
            object_id=object_id,
        )
    except Exception:  # a partially constructed object cannot be judged
        _issue(
            issues,
            f"{object_type}.consistency_uncheckable",
            "error",
            "Engineering consistency could not be evaluated for this object.",
            blocks_export=True,
            object_type=object_type,
            object_id=object_id,
        )


def _validate_reference(
    issues: list[ValidationIssue],
    *,
    item_type: str,
    item_id: str,
    region_ref: object,
    regions_by_id: dict[str, object],
    rule: RegionTargetRule,
) -> None:
    """Apply the authoritative region-target rule for one condition."""

    if rule.target == "prohibited" and region_ref is not None:
        _issue(
            issues,
            f"{item_type}.region_target_prohibited",
            "error",
            "This condition applies to the whole model and must not target a region.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )
        return
    if region_ref is None and rule.target != "required":
        return
    if not isinstance(region_ref, str) or not region_ref:
        _issue(
            issues,
            f"{item_type}.region_missing",
            "error",
            "A required region reference is missing.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )
        return
    region = regions_by_id.get(region_ref)
    if region is None:
        _issue(
            issues,
            f"{item_type}.region_unresolved",
            "error",
            f"Region reference '{region_ref}' does not resolve.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )
        return
    entity_type = getattr(region, "entity_type", None)
    if entity_type not in rule.entity_types:
        _issue(
            issues,
            f"{item_type}.region_entity_unsupported",
            "error",
            f"Region '{region_ref}' is a {entity_type!r} region; this condition "
            f"supports {', '.join(sorted(rule.entity_types))}.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )
    entity_ids = region_entity_membership(region)
    if not isinstance(entity_ids, list) or not entity_ids:
        _issue(
            issues,
            f"{item_type}.region_empty",
            "error",
            f"Referenced region '{region_ref}' has no entities.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )
    status = getattr(region, "status", None)
    if status == "rejected":
        _issue(
            issues,
            f"{item_type}.region_rejected",
            "error",
            f"Referenced region '{region_ref}' is rejected.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )
    elif status != "confirmed":
        _issue(
            issues,
            f"{item_type}.region_unconfirmed",
            "warning",
            f"Referenced region '{region_ref}' is not confirmed.",
            blocks_export=True,
            object_type=item_type,
            object_id=item_id,
            field="region_ref",
        )


def validate_intent(
    intent: SimulationIntent, *, source_is_stale: bool = False
) -> ValidationReport:
    """Return a deterministic report without trusting or mutating the intent."""

    issues: list[ValidationIssue] = []
    try:
        enforce_cad_region_entity_ids_invariant(intent)
    except EngineeringConsistencyError as exc:
        _issue(
            issues,
            exc.code,
            "error",
            "A CAD-face region contains forbidden duplicate numeric evidence.",
            blocks_export=True,
            object_type="region",
            field="entity_ids",
        )
    if source_is_stale:
        _issue(
            issues, "source.stale", "error",
            "The setup source version has been superseded.",
            blocks_export=True, object_type="source",
        )

    analysis = getattr(intent, "analysis", None)
    if analysis is None:
        _issue(
            issues,
            "analysis.missing",
            "error",
            "Analysis configuration is required.",
            blocks_export=True,
            object_type="analysis",
        )
    else:
        if getattr(analysis, "type", None) != "static_structural":
            _issue(
                issues,
                "analysis.unsupported_type",
                "error",
                "Only static_structural analysis is supported.",
                blocks_export=True,
                object_type="analysis",
                field="type",
            )
        units = getattr(analysis, "units", None)
        if units is None:
            _issue(
                issues,
                "units.missing",
                "error",
                "The canonical units block is required.",
                blocks_export=True,
                object_type="analysis",
                field="units",
            )
        else:
            expected_units = (("length", "mm"), ("force", "N"), ("stress", "MPa"))
            for field_name, expected in expected_units:
                actual = getattr(units, field_name, None)
                if actual is None:
                    _issue(
                        issues,
                        "units.incomplete",
                        "error",
                        f"Canonical unit '{field_name}' is missing.",
                        blocks_export=True,
                        object_type="analysis",
                        field=f"units.{field_name}",
                    )
                elif actual != expected:
                    _issue(
                        issues,
                        "units.unsupported",
                        "error",
                        f"Internal {field_name} unit must be {expected}, not {actual}.",
                        blocks_export=True,
                        object_type="analysis",
                        field=f"units.{field_name}",
                    )
        # Schema version 2 decisions.  ``None`` is *explicitly missing*: a
        # migrated legacy setup reports structural incompleteness here rather
        # than silently acquiring a 3D-solid, global-coordinate, CalculiX
        # approval it never received.
        for field_name, description in (
            ("dimensionality", "The analysis dimensionality"),
            ("solver_target", "The solver target"),
            ("coordinate_system", "The coordinate system"),
        ):
            if getattr(analysis, field_name, None) is None:
                _issue(
                    issues,
                    f"analysis.{field_name}_missing",
                    "error",
                    f"{description} must be stated explicitly.",
                    blocks_export=True,
                    object_type="analysis",
                    field=field_name,
                )

    materials = getattr(intent, "materials", None)
    loads = getattr(intent, "loads", None)
    if not isinstance(loads, list):
        loads = []
    has_gravity = any(getattr(load, "type", None) == "gravity" for load in loads)
    if not isinstance(materials, list) or not materials:
        _issue(
            issues,
            "material.missing",
            "error",
            "At least one linear-elastic isotropic material is required.",
            blocks_export=True,
            object_type="material",
        )
        materials = []
        if has_gravity:
            _issue(
                issues,
                "material.density_required_for_gravity",
                "error",
                "Gravity requires an assigned material with positive finite density in tonne/mm^3.",
                blocks_export=True,
                object_type="material",
                field="density_tonne_per_mm3",
            )
    if len(materials) > 1:
        material_names = sorted(
            {
                str(getattr(material, "name", "<missing-material-name>"))
                for material in materials
            }
        )
        canonical = {_material_semantic_key(material) for material in materials}
        duplicate = len(material_names) == 1 and len(canonical) == 1
        assignment_id = ", ".join(material_names)
        _issue(
            issues,
            (
                "material.assignment_duplicate"
                if duplicate
                else "material.assignment_conflict"
            ),
            "error",
            (
                f"Material assignment '{assignment_id}' is repeated with equivalent properties."
                if duplicate
                else f"Material assignments '{assignment_id}' conflict for the single solid."
            ),
            blocks_export=True,
            object_type="material",
            object_id=assignment_id,
        )
        _issue(
            issues, "material.count_unsupported", "error",
            "Exactly one material is supported for the single solid.",
            blocks_export=True, object_type="material",
        )
    for index, material in enumerate(materials):
        material_id = getattr(material, "name", None) or f"material[{index}]"
        _check_engineering_consistency(
            issues, material, object_type="material", object_id=str(material_id)
        )
        if getattr(material, "model", None) != "linear_elastic_isotropic":
            _issue(
                issues,
                "material.unsupported_model",
                "error",
                "Only linear_elastic_isotropic materials are supported.",
                blocks_export=True,
                object_type="material",
                object_id=str(material_id),
                field="model",
            )
        youngs_modulus = getattr(material, "E_MPa", None)
        if not _finite(youngs_modulus) or float(youngs_modulus) <= 0.0:
            _issue(
                issues,
                "material.youngs_modulus_invalid",
                "error",
                "Young's modulus must be finite and greater than zero.",
                blocks_export=True,
                object_type="material",
                object_id=str(material_id),
                field="E_MPa",
            )
        poisson_ratio = getattr(material, "nu", None)
        if (
            not _finite(poisson_ratio)
            or float(poisson_ratio) <= -1.0
            or float(poisson_ratio) >= 0.5
        ):
            _issue(
                issues,
                "material.poisson_ratio_invalid",
                "error",
                "Poisson's ratio must be finite and satisfy -1 < nu < 0.5.",
                blocks_export=True,
                object_type="material",
                object_id=str(material_id),
                field="nu",
            )
        density = getattr(material, "density_tonne_per_mm3", None)
        if density is not None and (
            not _finite(density) or float(density) <= 0.0
        ):
            _issue(
                issues,
                "material.density_invalid",
                "error",
                "Material density must be finite and greater than zero in tonne/mm^3.",
                blocks_export=True,
                object_type="material",
                object_id=str(material_id),
                field="density_tonne_per_mm3",
            )
        elif has_gravity and density is None:
            _issue(
                issues,
                "material.density_required_for_gravity",
                "error",
                "Gravity requires assigned material density in tonne/mm^3.",
                blocks_export=True,
                object_type="material",
                object_id=str(material_id),
                field="density_tonne_per_mm3",
            )
        if getattr(material, "authority", "engineer_entered") == "system_proposed":
            proposal_ref = getattr(material, "proposal_assumption_ref", None)
            proposal_decisions = [
                item
                for item in getattr(intent, "assumptions", [])
                if getattr(item, "id", None) == proposal_ref
            ]
            proposal_decision = (
                proposal_decisions[0] if len(proposal_decisions) == 1 else None
            )
            if proposal_decision is None:
                _issue(
                    issues,
                    "material.proposal_decision_missing",
                    "error",
                    "A system-proposed material must retain its linked decision.",
                    blocks_export=True,
                    object_type="material",
                    object_id=str(material_id),
                    field="proposal_assumption_ref",
                )
            elif getattr(proposal_decision, "status", None) == "accepted":
                accepted_fingerprint = getattr(
                    proposal_decision,
                    "material_proposal_fingerprint_sha256",
                    None,
                )
                expected_fingerprint = material_proposal_fingerprint(material)
                if accepted_fingerprint != expected_fingerprint:
                    _issue(
                        issues,
                        "material.proposal_decision_stale",
                        "error",
                        "The accepted material decision does not match the current proposal snapshot.",
                        blocks_export=True,
                        object_type="material",
                        object_id=str(material_id),
                        field="proposal_assumption_ref",
                    )

    regions = getattr(intent, "regions", None)
    if not isinstance(regions, list):
        regions = []
    regions_by_id: dict[str, object] = {}
    for index, region in enumerate(regions):
        region_id = getattr(region, "id", None) or f"region[{index}]"
        if region_id in regions_by_id:
            _issue(
                issues,
                "region.duplicate_id",
                "error",
                f"Region id '{region_id}' is duplicated.",
                blocks_export=True,
                object_type="region",
                object_id=str(region_id),
                field="id",
            )
        else:
            regions_by_id[str(region_id)] = region
        # Every other probe in this loop reads the region defensively so an
        # unvalidated shape is reported rather than raised on.  A region that
        # never became a ``Region`` -- for example a hostile mapping smuggled
        # in through ``model_construct`` -- has no trustworthy membership, and
        # the CAD invariant above has already recorded the blocking issue.
        entity_ids = (
            region_entity_membership(region)
            if isinstance(region, Region)
            else []
        )
        if not isinstance(entity_ids, list) or not entity_ids:
            _issue(
                issues,
                "region.entity_ids_empty",
                "error",
                "Region must contain at least one entity id.",
                blocks_export=True,
                object_type="region",
                object_id=str(region_id),
                field="entity_ids",
            )
        for field_name in ("selection_method", "source_instruction", "status"):
            if not getattr(region, field_name, None):
                _issue(
                    issues,
                    "region.provenance_missing",
                    "error",
                    f"Region provenance field '{field_name}' is required.",
                    blocks_export=True,
                    object_type="region",
                    object_id=str(region_id),
                    field=field_name,
                )
        confidence = getattr(region, "confidence", None)
        if (
            not _finite(confidence)
            or float(confidence) < 0.0
            or float(confidence) > 1.0
        ):
            _issue(
                issues,
                "region.confidence_invalid",
                "error",
                "Region confidence must be finite and between 0.0 and 1.0.",
                blocks_export=True,
                object_type="region",
                object_id=str(region_id),
                field="confidence",
            )
        status = getattr(region, "status", None)
        if getattr(region, "entity_type", None) == "cad_face":
            target = getattr(region, "cad_face_target", None)
            resolution = getattr(target, "resolution", None)
            if target is None:
                _issue(
                    issues,
                    "region.cad_stable_target_missing",
                    "error",
                    "CAD-face region has no authoritative stable target.",
                    blocks_export=True,
                    object_type="region",
                    object_id=str(region_id),
                    field="cad_face_target",
                )
            elif resolution != "resolved":
                _issue(
                    issues,
                    f"region.cad_{resolution or 'unresolved'}",
                    "error",
                    "CAD-face region is not uniquely resolved to stable geometry.",
                    blocks_export=True,
                    object_type="region",
                    object_id=str(region_id),
                    field="cad_face_target.resolution",
                )
        if status == "proposed":
            _issue(
                issues,
                "region.proposed",
                "warning",
                f"Region '{region_id}' requires engineer confirmation.",
                blocks_export=True,
                object_type="region",
                object_id=str(region_id),
                field="status",
            )
        elif status == "rejected":
            _issue(
                issues,
                "region.rejected",
                "error",
                f"Region '{region_id}' is rejected and cannot be exported.",
                blocks_export=True,
                object_type="region",
                object_id=str(region_id),
                field="status",
            )
        elif status != "confirmed":
            _issue(
                issues,
                "region.status_invalid",
                "error",
                f"Region '{region_id}' has an invalid status.",
                blocks_export=True,
                object_type="region",
                object_id=str(region_id),
                field="status",
            )

    bcs = getattr(intent, "bcs", None)
    if not isinstance(bcs, list):
        bcs = []
    if not bcs:
        _issue(
            issues, "bc.missing", "error",
            "At least one displacement constraint is required.",
            blocks_export=True, object_type="bc",
        )
    bc_signatures: dict[str, int] = {}
    restraint_axes: set[str] = set()
    fully_fixed_region = False
    fixed_components: dict[tuple[str, str], list[int]] = {}
    prescribed_components: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for index, bc in enumerate(bcs):
        object_id = f"bc[{index}]"
        bc_type = getattr(bc, "type", None)
        restraint_region = regions_by_id.get(str(getattr(bc, "region_ref", "")))
        counts_as_restraint = (
            restraint_region is not None
            and getattr(restraint_region, "status", None) != "rejected"
        )
        signature = canonical_semantic_key(canonical_bc_semantics(bc))
        if signature in bc_signatures:
            _issue(
                issues,
                "bc.duplicate",
                "error",
                "An exact duplicate boundary condition is present.",
                blocks_export=True,
                object_type="bc",
                object_id=object_id,
            )
        else:
            bc_signatures[signature] = index
        bc_rule = BC_REGION_COMPATIBILITY.get(str(bc_type))
        if bc_rule is None:
            _issue(
                issues,
                "bc.unsupported_type",
                "error",
                f"Constraint type {bc_type!r} is outside the supported envelope.",
                blocks_export=True,
                object_type="bc",
                object_id=object_id,
                field="type",
            )
        else:
            _validate_reference(
                issues,
                item_type="bc",
                item_id=object_id,
                region_ref=getattr(bc, "region_ref", None),
                regions_by_id=regions_by_id,
                rule=bc_rule,
            )
        if bc_type == "prescribed_displacement":
            components = getattr(bc, "components", None)
            if not isinstance(components, dict) or not components:
                _issue(
                    issues,
                    "bc.vector_invalid",
                    "error",
                    "Prescribed displacement requires at least one component.",
                    blocks_export=True,
                    object_type="bc",
                    object_id=object_id,
                    field="components",
                )
            else:
                _check_engineering_consistency(
                    issues, bc, object_type="bc", object_id=object_id
                )
                for axis, value in sorted(components.items()):
                    if counts_as_restraint:
                        restraint_axes.add(axis)
                    prescribed_components.setdefault(
                        (str(getattr(bc, "region_ref", "")), axis), []
                    ).append((index, float(value)))
        elif bc_type == "fixed_displacement":
            if counts_as_restraint and set(getattr(bc, "components", [])) == {
                "x", "y", "z"
            }:
                fully_fixed_region = True
            for axis in sorted(getattr(bc, "components", [])):
                if counts_as_restraint:
                    restraint_axes.add(axis)
                fixed_components.setdefault(
                    (str(getattr(bc, "region_ref", "")), axis), []
                ).append(index)

    for key, values in sorted(prescribed_components.items()):
        unique_values = {value for _, value in values}
        if len(unique_values) > 1:
            _issue(
                issues,
                "bc.prescribed_displacement_conflict",
                "error",
                f"Region '{key[0]}' has conflicting prescribed {key[1].upper()} displacements.",
                blocks_export=True,
                object_type="bc",
                object_id=key[0],
                field=f"components.{key[1]}",
            )
        if key in fixed_components and any(value != 0.0 for _, value in values):
            _issue(
                issues,
                "bc.fixed_prescribed_conflict",
                "error",
                f"Region '{key[0]}' is fixed and has a nonzero prescribed {key[1].upper()} displacement.",
                blocks_export=True,
                object_type="bc",
                object_id=key[0],
                field=f"components.{key[1]}",
            )
    for axis in ("x", "y", "z"):
        if axis not in restraint_axes:
            _issue(
                issues,
                f"constraint.rigid_body_translation_{axis}",
                "error",
                f"No confirmed {axis.upper()} translational restraint is present.",
                blocks_export=True,
                object_type="constraint",
                field=axis,
            )
    if restraint_axes == {"x", "y", "z"} and not fully_fixed_region:
        _issue(
            issues,
            "constraint.rotational_restraint_unverified",
            "warning",
            "Translational restraint is covered, but rotational restraint cannot be proven without geometry and stiffness-rank analysis.",
            blocks_export=False,
            object_type="constraint",
        )

    if not loads:
        _issue(
            issues, "load.missing", "error",
            "At least one supported load is required.",
            blocks_export=True, object_type="load",
        )
    load_signatures: dict[str, int] = {}
    for index, load in enumerate(loads):
        object_id = f"load[{index}]"
        load_type = getattr(load, "type", None)
        signature = canonical_semantic_key(canonical_load_semantics(load))
        if signature in load_signatures:
            _issue(
                issues,
                "load.duplicate",
                "error",
                "An exact duplicate load is present.",
                blocks_export=True,
                object_type="load",
                object_id=object_id,
            )
        else:
            load_signatures[signature] = index
        load_rule = LOAD_REGION_COMPATIBILITY.get(str(load_type))
        if load_rule is None:
            _issue(
                issues,
                "load.unsupported_type",
                "error",
                f"Load type {load_type!r} is outside the supported envelope.",
                blocks_export=True,
                object_type="load",
                object_id=object_id,
                field="type",
            )
        else:
            _validate_reference(
                issues,
                item_type="load",
                item_id=object_id,
                region_ref=getattr(load, "region_ref", None),
                regions_by_id=regions_by_id,
                rule=load_rule,
            )
        _check_engineering_consistency(
            issues, load, object_type="load", object_id=object_id
        )
        if load_type == "pressure":
            magnitude = getattr(load, "magnitude", None)
            if not _finite(magnitude):
                _issue(
                    issues,
                    "load.magnitude_nonfinite",
                    "error",
                    "Load magnitude must be finite.",
                    blocks_export=True,
                    object_type="load",
                    object_id=object_id,
                    field="magnitude",
                )
            elif float(magnitude) < 0.0:
                _issue(
                    issues,
                    "load.pressure_negative",
                    "error",
                    "Pressure is a nonnegative scalar; positive acts into the surface.",
                    blocks_export=True,
                    object_type="load",
                    object_id=object_id,
                    field="magnitude",
                )
            elif float(magnitude) == 0.0:
                _issue(
                    issues,
                    "load.magnitude_zero",
                    "error",
                    "Pressure magnitude must be greater than zero to define a load.",
                    blocks_export=True,
                    object_type="load",
                    object_id=object_id,
                    field="magnitude",
                )
        else:
            _validate_vector(
                issues,
                getattr(load, "vector", None),
                object_type="load",
                object_id=object_id,
                field="vector",
            )

    assumptions = getattr(intent, "assumptions", None)
    if not isinstance(assumptions, list):
        assumptions = []
    for index, assumption in enumerate(assumptions):
        assumption_id = getattr(assumption, "id", None) or f"assumption[{index}]"
        criticality = getattr(assumption, "criticality", "noncritical")
        status = getattr(assumption, "status", None)
        if criticality == "unit_critical" and status in {"pending", "rejected"}:
            _issue(
                issues,
                f"assumption.unit_critical_{status}",
                "error",
                f"Unit-critical assumption is {status} and blocks export.",
                blocks_export=True,
                object_type="assumption",
                object_id=str(assumption_id),
                field="status",
            )
        elif criticality == "noncritical" and status in {"pending", "rejected"}:
            _issue(
                issues,
                f"assumption.noncritical_{status}",
                "warning",
                f"Noncritical assumption is {status}; it is reported but does not block export.",
                blocks_export=False,
                object_type="assumption",
                object_id=str(assumption_id),
                field="status",
            )

    mesh = getattr(intent, "mesh_settings", None)
    if mesh is None:
        _issue(
            issues, "mesh.missing", "error",
            "An explicit meshing profile is required; it is never defaulted.",
            blocks_export=True, object_type="mesh",
        )
    else:
        _check_engineering_consistency(issues, mesh, object_type="mesh")
        size = getattr(mesh, "global_element_size_mm", None)
        if not _finite(size) or float(size) <= 0:
            _issue(
                issues, "mesh.element_size_invalid", "error",
                "Global target element size must be finite and greater than zero.",
                blocks_export=True, object_type="mesh",
                field="global_element_size_mm",
            )
        if (
            getattr(mesh, "element_type", None) != "tetrahedral"
            or getattr(mesh, "element_order", None) != "first_order"
            or getattr(mesh, "mesher", None) != "gmsh"
            or getattr(mesh, "mesher_preset", None) != "gmsh_tet_v1"
        ):
            _issue(
                issues, "mesh.profile_unsupported", "error",
                "Only the deterministic gmsh_tet_v1 first-order tetrahedral profile is supported.",
                blocks_export=True, object_type="mesh",
            )

    solver = getattr(intent, "solver_settings", None)
    if solver is None:
        _issue(
            issues, "solver.missing", "error",
            "An explicit solver profile is required; it is never defaulted.",
            blocks_export=True, object_type="solver",
        )
    else:
        if (
            getattr(solver, "target", None) != "calculix"
            or getattr(solver, "analysis_profile", None) != "linear_static_v1"
        ):
            _issue(
                issues, "solver.profile_unsupported", "error",
                "Only the CalculiX linear_static_v1 profile is supported.",
                blocks_export=True, object_type="solver",
            )
        requested = getattr(solver, "requested_results", None)
        if not isinstance(requested, list) or not requested:
            _issue(
                issues, "solver.requested_results_missing", "error",
                "At least one requested result field is required.",
                blocks_export=True, object_type="solver",
                field="requested_results",
            )
        elif len(set(requested)) != len(requested):
            _issue(
                issues, "solver.requested_results_duplicated", "error",
                "Requested result fields must be unique.",
                blocks_export=True, object_type="solver",
                field="requested_results",
            )
    try:
        intent.export_payload()
    except ExportBlockedError as exc:
        _issue(
            issues,
            "export.confirmation_gate_blocked",
            "warning",
            str(exc),
            blocks_export=True,
            object_type="intent",
            field="regions",
        )
    except EngineeringConsistencyError:
        # The shared invariant guard already emitted its stable error issue at
        # the start of this deterministic pass.
        pass

    issues.sort(
        key=lambda issue: (
            _SEVERITY_ORDER[issue.severity],
            issue.code,
            issue.object_type or "",
            issue.object_id or "",
            issue.field or "",
            issue.message,
        )
    )
    validation_status: Literal["valid", "invalid"] = (
        "invalid" if any(issue.severity == "error" for issue in issues) else "valid"
    )
    export_eligible = not any(issue.blocks_export for issue in issues)
    codes = {issue.code for issue in issues}
    # Deterministic precedence.  Structural incompleteness and semantic
    # invalidity outrank source staleness: a stale setup that was never
    # finished, or that carries a contradictory quantity, must report the
    # deeper problem rather than hiding it behind "the source moved on".  Every
    # finding stays in ``issues`` regardless of which status is selected.
    if codes & STRUCTURALLY_INCOMPLETE_CODES:
        readiness_status = "structurally_incomplete"
    elif any(
        issue.severity == "error"
        and issue.code != "source.stale"
        and not issue.code.endswith("_pending")
        for issue in issues
    ):
        readiness_status = "semantically_invalid"
    elif "source.stale" in codes:
        readiness_status = "stale_source"
    elif any(
        code in codes
        for code in ("region.proposed", "load.region_unconfirmed", "bc.region_unconfirmed")
    ):
        readiness_status = "awaiting_region_confirmation"
    elif any(code.endswith("_pending") for code in codes):
        readiness_status = "awaiting_assumption_acceptance"
    else:
        readiness_status = "ready"
    engineering_ready = readiness_status == "ready" and export_eligible
    return ValidationReport(
        validation_status=validation_status,
        readiness_status=readiness_status,
        engineering_ready=engineering_ready,
        export_eligible=export_eligible,
        load_summary=summarize_loads(intent),
        issues=issues,
    )
