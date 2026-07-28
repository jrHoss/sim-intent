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

from ir.schema import (
    BC_REGION_COMPATIBILITY,
    LOAD_REGION_COMPATIBILITY,
    EngineeringConsistencyError,
    ExportBlockedError,
    RegionTargetRule,
    SimulationIntent,
    StrictModel,
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
    export_eligible: bool
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
    entity_ids = getattr(region, "entity_ids", None)
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
        entity_ids = getattr(region, "entity_ids", None)
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
    for index, bc in enumerate(bcs):
        object_id = f"bc[{index}]"
        bc_type = getattr(bc, "type", None)
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

    if not loads:
        _issue(
            issues, "load.missing", "error",
            "At least one supported load is required.",
            blocks_export=True, object_type="load",
        )
    for index, load in enumerate(loads):
        object_id = f"load[{index}]"
        load_type = getattr(load, "type", None)
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
        issue.severity == "error" and issue.code != "source.stale"
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
    return ValidationReport(
        validation_status=validation_status,
        readiness_status=readiness_status,
        export_eligible=export_eligible,
        issues=issues,
    )
