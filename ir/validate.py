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

from ir.schema import ExportBlockedError, SimulationIntent, StrictModel


IssueSeverity = Literal["error", "warning"]


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


def _validate_reference(
    issues: list[ValidationIssue],
    *,
    item_type: str,
    item_id: str,
    region_ref: object,
    regions_by_id: dict[str, object],
    required: bool,
) -> None:
    if region_ref is None and not required:
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


def validate_intent(intent: SimulationIntent) -> ValidationReport:
    """Return a deterministic report without trusting or mutating the intent."""

    issues: list[ValidationIssue] = []

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
    for index, material in enumerate(materials):
        material_id = getattr(material, "name", None) or f"material[{index}]"
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
    for index, bc in enumerate(bcs):
        object_id = f"bc[{index}]"
        _validate_reference(
            issues,
            item_type="bc",
            item_id=object_id,
            region_ref=getattr(bc, "region_ref", None),
            regions_by_id=regions_by_id,
            required=True,
        )
        if getattr(bc, "type", None) == "prescribed_displacement":
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
            elif not all(_finite(value) for value in components.values()):
                _issue(
                    issues,
                    "bc.vector_nonfinite",
                    "error",
                    "Prescribed displacement components must be finite.",
                    blocks_export=True,
                    object_type="bc",
                    object_id=object_id,
                    field="components",
                )
            elif not any(float(value) != 0.0 for value in components.values()):
                _issue(
                    issues,
                    "bc.vector_zero",
                    "error",
                    "Prescribed displacement magnitude must be nonzero.",
                    blocks_export=True,
                    object_type="bc",
                    object_id=object_id,
                    field="components",
                )

    for index, load in enumerate(loads):
        object_id = f"load[{index}]"
        load_type = getattr(load, "type", None)
        _validate_reference(
            issues,
            item_type="load",
            item_id=object_id,
            region_ref=getattr(load, "region_ref", None),
            regions_by_id=regions_by_id,
            required=load_type != "gravity",
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
            elif float(magnitude) == 0.0:
                _issue(
                    issues,
                    "load.magnitude_zero",
                    "error",
                    "Load magnitude must be nonzero.",
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
    return ValidationReport(
        validation_status=validation_status,
        export_eligible=export_eligible,
        issues=issues,
    )
