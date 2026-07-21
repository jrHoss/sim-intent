"""Minimal Task 15 bridge from typed interpretation to proposed IR.

This module composes the existing Task 11 interpreter, Task 12 grounding,
Task 7 semantics, and Task 1 IR.  It deliberately owns no geometry-query,
unit-conversion, validation, confirmation, or export implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from geom.cylinders import CylinderRecord
from geom.inventory import FaceInventory
from ground.engine import ClickEvidence, GroundingBatch, GroundingEngine
from ground.semantics import (
    interpret_load,
    normalize_fixed_displacement_components,
    parse_quantity,
)
from ir.schema import (
    Analysis,
    Assumption,
    FixedDisplacementBC,
    GravityLoad,
    Material,
    PrescribedDisplacementBC,
    PressureLoad,
    ResultantSurfaceForceLoad,
    SimulationIntent,
    SurfaceTractionLoad,
    ConcentratedForceLoad,
    Units,
)
from llm.interpreter import Interpretation, Interpreter, summarize_face_inventory


class OrchestrationError(RuntimeError):
    """Raised when validated upstream pieces cannot form supported IR."""


DEMO_MATERIAL = Material(
    name="steel",
    model="linear_elastic_isotropic",
    E_MPa=210_000.0,
    nu=0.3,
)
DEMO_DENSITY_TONNE_PER_MM3 = 7.85e-9
DEMO_MATERIAL_ASSUMPTION = Assumption(
    text="The prototype demonstration material was set to steel (E=210000 MPa, nu=0.3).",
    criticality="noncritical",
    status="pending",
)
DEMO_GRAVITY_MATERIAL_ASSUMPTION = Assumption(
    text=(
        "For gravity, the prototype demonstration material was set to steel "
        "(E=210000 MPa, nu=0.3, density=7850 kg/m^3 = "
        "7.85e-9 tonne/mm^3 internal)."
    ),
    criticality="unit_critical",
    status="pending",
)


@dataclass(frozen=True)
class ProposalResult:
    interpretation: Interpretation
    grounding: GroundingBatch
    intent: SimulationIntent | None

    @property
    def clarifications(self):
        return [result.clarification for result in self.grounding.results if result.clarification]


@dataclass(frozen=True)
class MergeSessionResult:
    intent: SimulationIntent
    duplicate_notices: tuple[str, ...]

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicate_notices)


def interpret_and_propose(
    *,
    instruction: str,
    inventory: FaceInventory,
    cylinders: dict[int, CylinderRecord],
    interpreter: Interpreter,
    click_evidence_by_intent: Mapping[int, ClickEvidence] | None = None,
) -> ProposalResult:
    """Call the production interpreter and continue through production grounding."""

    interpretation = interpreter.interpret(
        instruction, summarize_face_inventory(inventory, cylinders)
    )
    return propose_from_interpretation(
        instruction=instruction,
        interpretation=interpretation,
        inventory=inventory,
        cylinders=cylinders,
        click_evidence_by_intent=click_evidence_by_intent,
    )


def propose_from_interpretation(
    *,
    instruction: str,
    interpretation: Interpretation,
    inventory: FaceInventory,
    cylinders: dict[int, CylinderRecord],
    click_evidence_by_intent: Mapping[int, ClickEvidence] | None = None,
) -> ProposalResult:
    """Ground validated typed operations and build IR when fully resolved."""

    grounding = GroundingEngine(inventory, cylinders).ground_interpretation(
        instruction,
        interpretation,
        click_evidence_by_intent=dict(click_evidence_by_intent or {}),
    )
    if any(result.clarification is not None for result in grounding.results):
        return ProposalResult(interpretation, grounding, None)
    return ProposalResult(
        interpretation,
        grounding,
        _build_intent(instruction=instruction, grounding=grounding),
    )


def _build_intent(*, instruction: str, grounding: GroundingBatch) -> SimulationIntent:
    regions = []
    bcs = []
    loads = []
    assumptions = []

    for result in grounding.results:
        region = result.region
        if region is not None:
            regions.append(region.model_copy(deep=True))
        region_ref = None if region is None else region.id

        if result.bc is not None:
            if region_ref is None:
                raise OrchestrationError("a boundary condition requires a grounded region")
            if result.bc.type == "fixed_displacement":
                components, component_assumption = normalize_fixed_displacement_components(
                    result.target_description,
                    result.bc.components,
                )
                if component_assumption is None and len(grounding.results) == 1:
                    components, component_assumption = normalize_fixed_displacement_components(
                        instruction,
                        result.bc.components,
                    )
                if component_assumption is not None:
                    assumptions.append(component_assumption)
                bcs.append(
                    FixedDisplacementBC(
                        type="fixed_displacement",
                        region_ref=region_ref,
                        components=components,
                    )
                )
            else:
                converted: dict[str, float] = {}
                for axis, quantity_text in result.bc.components.items():
                    quantity = parse_quantity(quantity_text, expected_kind="length")
                    converted[axis] = quantity.value
                    assumptions.append(
                        Assumption(
                            text=(
                                f"Prescribed {axis.upper()} displacement '{quantity_text}' "
                                f"was normalized to {quantity.value:g} mm."
                            ),
                            criticality="unit_critical",
                            status="pending",
                        )
                    )
                bcs.append(
                    PrescribedDisplacementBC(
                        type="prescribed_displacement",
                        region_ref=region_ref,
                        components=converted,
                    )
                )
            continue

        assert result.load is not None
        payload = result.load
        if payload.type == "gravity":
            semantic = interpret_load(f"gravity {payload.direction}")
        elif payload.type == "pressure":
            semantic = interpret_load(f"{payload.magnitude} pressure")
        elif payload.type == "surface_traction":
            semantic = interpret_load(
                f"{payload.magnitude} traction {payload.direction}"
            )
        elif payload.type == "concentrated_force":
            semantic = interpret_load(
                f"concentrated {payload.magnitude} {payload.direction}"
            )
        else:
            semantic = interpret_load(f"{payload.magnitude} {payload.direction}")
        if semantic.type != payload.type:
            raise OrchestrationError(
                f"typed load '{payload.type}' disagrees with central semantics '{semantic.type}'"
            )
        assumptions.extend(item.model_copy(deep=True) for item in semantic.assumptions)

        if payload.type == "gravity":
            assert semantic.vector is not None
            loads.append(GravityLoad(type="gravity", region_ref=None, vector=list(semantic.vector)))
        elif region_ref is None:
            raise OrchestrationError("a non-gravity load requires a grounded region")
        elif payload.type == "pressure":
            loads.append(PressureLoad(type="pressure", region_ref=region_ref, magnitude=semantic.value))
        elif payload.type == "resultant_surface_force":
            assert semantic.vector is not None
            loads.append(ResultantSurfaceForceLoad(type=payload.type, region_ref=region_ref, vector=list(semantic.vector)))
        elif payload.type == "surface_traction":
            assert semantic.vector is not None
            loads.append(SurfaceTractionLoad(type=payload.type, region_ref=region_ref, vector=list(semantic.vector)))
        else:
            assert semantic.vector is not None
            loads.append(ConcentratedForceLoad(type=payload.type, region_ref=region_ref, vector=list(semantic.vector)))

    has_gravity = any(load.type == "gravity" for load in loads)
    if has_gravity:
        material = DEMO_MATERIAL.model_copy(
            update={"density_tonne_per_mm3": DEMO_DENSITY_TONNE_PER_MM3},
            deep=True,
        )
        assumptions.insert(0, DEMO_GRAVITY_MATERIAL_ASSUMPTION.model_copy(deep=True))
    else:
        material = DEMO_MATERIAL.model_copy(deep=True)
        assumptions.insert(0, DEMO_MATERIAL_ASSUMPTION.model_copy(deep=True))
    unique_assumptions = {assumption.id: assumption for assumption in assumptions}
    return SimulationIntent(
        analysis=Analysis(type="static_structural", units=Units(length="mm", force="N", stress="MPa")),
        materials=[material],
        regions=regions,
        bcs=bcs,
        loads=loads,
        assumptions=list(unique_assumptions.values()),
        validation_status="unvalidated",
    )


def merge_session_intents(
    current: SimulationIntent | None,
    proposal: SimulationIntent,
    *,
    source_instruction: str | None = None,
) -> MergeSessionResult:
    """Append only semantically distinct normalized IR conditions.

    Equivalence is computed from the grounded target and normalized condition
    payload. Source wording, fixture identity, confidence, and region IDs are
    not inputs to the decision.
    """

    existing_regions = [] if current is None else current.regions
    current_bcs = [] if current is None else current.bcs
    current_loads = [] if current is None else current.loads
    current_region_map = {region.id: region for region in existing_regions}
    proposal_region_map = {region.id: region for region in proposal.regions}
    signatures = {
        _condition_signature(item, current_region_map)
        for item in [*current_bcs, *current_loads]
    }
    kept_bcs = []
    kept_loads = []
    duplicate_notices: list[str] = []
    for incoming, retained in (
        (proposal.bcs, kept_bcs),
        (proposal.loads, kept_loads),
    ):
        for item in incoming:
            signature = _condition_signature(item, proposal_region_map)
            if signature in signatures:
                source = _condition_source(
                    item,
                    proposal_region_map,
                    proposal,
                    source_instruction=source_instruction,
                )
                duplicate_notices.append(
                    "Equivalent condition already exists; duplicate was not added. "
                    f"New source instruction: {source}"
                )
                continue
            signatures.add(signature)
            retained.append(item)

    needed_region_ids = {
        item.region_ref
        for item in [*kept_bcs, *kept_loads]
        if item.region_ref is not None
    }
    used = {region.id for region in existing_regions}
    remap: dict[str, str] = {}
    next_number = 1
    for region in proposal.regions:
        if region.id not in needed_region_ids:
            continue
        candidate = region.id
        while candidate in used:
            while f"region_{next_number}" in used:
                next_number += 1
            candidate = f"region_{next_number}"
            next_number += 1
        remap[region.id] = candidate
        used.add(candidate)
    new_regions = [
        region.model_copy(update={"id": remap[region.id]}, deep=True)
        for region in proposal.regions
        if region.id in needed_region_ids
    ]
    new_bcs = [
        item.model_copy(update={"region_ref": remap[item.region_ref]}, deep=True)
        for item in kept_bcs
    ]
    new_loads = [
        item.model_copy(
            update={
                "region_ref": (
                    None if item.region_ref is None else remap[item.region_ref]
                )
            },
            deep=True,
        )
        for item in kept_loads
    ]
    current_materials = [] if current is None else current.materials
    current_assumptions = [] if current is None else current.assumptions
    materials = {item.name: item.model_copy(deep=True) for item in current_materials}
    default_density_applied = current is None
    for item in proposal.materials:
        existing = materials.get(item.name)
        if existing is None:
            materials[item.name] = item.model_copy(deep=True)
            default_density_applied = True
        elif (
            (kept_bcs or kept_loads)
            and existing.model == item.model
            and existing.E_MPa == item.E_MPa
            and existing.nu == item.nu
            and existing.density_tonne_per_mm3 is None
            and item.density_tonne_per_mm3 is not None
        ):
            materials[item.name] = existing.model_copy(
                update={"density_tonne_per_mm3": item.density_tonne_per_mm3},
                deep=True,
            )
            default_density_applied = True
    assumptions = {item.id: item.model_copy(deep=True) for item in current_assumptions}
    # A duplicate-only submission must not create new blocking assumptions.
    if kept_bcs or kept_loads:
        for item in proposal.assumptions:
            if (
                item.id == DEMO_GRAVITY_MATERIAL_ASSUMPTION.id
                and not default_density_applied
            ):
                continue
            assumptions.setdefault(item.id, item.model_copy(deep=True))
    merged = SimulationIntent(
        analysis=(current.analysis if current is not None else proposal.analysis).model_copy(deep=True),
        materials=list(materials.values()),
        regions=[
            *(item.model_copy(deep=True) for item in existing_regions),
            *new_regions,
        ],
        bcs=[*(item.model_copy(deep=True) for item in current_bcs), *new_bcs],
        loads=[*(item.model_copy(deep=True) for item in current_loads), *new_loads],
        assumptions=list(assumptions.values()),
        validation_status="unvalidated",
    )
    return MergeSessionResult(merged, tuple(duplicate_notices))


def _condition_signature(item: Any, regions: Mapping[str, Any]) -> str:
    payload = item.model_dump(mode="json")
    region_ref = payload.pop("region_ref", None)
    if item.type == "gravity" and region_ref is None:
        target: dict[str, Any] = {"scope": "whole_model"}
    else:
        region = regions[region_ref]
        entity_ids = sorted(
            ({"kind": type(value).__name__, "value": value} for value in region.entity_ids),
            key=lambda value: (value["kind"], str(value["value"])),
        )
        target = {
            "scope": "region",
            "entity_type": region.entity_type,
            "entity_ids": entity_ids,
        }
    if item.type == "fixed_displacement":
        payload["components"] = sorted(payload["components"])
    internal_unit = {
        "fixed_displacement": "none",
        "prescribed_displacement": "mm",
        "resultant_surface_force": "N",
        "concentrated_force": "N",
        "surface_traction": "MPa",
        "pressure": "MPa",
        "gravity": "mm/s^2",
    }[item.type]
    return json.dumps(
        {"target": target, "condition": payload, "internal_unit": internal_unit},
        sort_keys=True,
        separators=(",", ":"),
    )


def _condition_source(
    item: Any,
    regions: Mapping[str, Any],
    proposal: SimulationIntent,
    *,
    source_instruction: str | None,
) -> str:
    if source_instruction:
        return source_instruction
    if item.region_ref is not None:
        return regions[item.region_ref].source_instruction
    region_sources = [region.source_instruction for region in proposal.regions]
    return region_sources[0] if region_sources else "whole-model body-load instruction"
