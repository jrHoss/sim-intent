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
    normalize_quantity,
    parse_quantity,
)
from ir.canonical import (
    canonical_bc_semantics,
    canonical_load_semantics,
    canonical_semantic_key,
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
from ir.schema_version import SIMULATION_INTENT_SCHEMA_VERSION
from llm.interpreter import (
    Interpretation,
    Interpreter,
    MaterialProposalPayload,
    summarize_face_inventory,
)


class OrchestrationError(RuntimeError):
    """Raised when validated upstream pieces cannot form supported IR."""


# Natural-language interpretation is not an engineering approval.
#
# This bridge proposes exactly what the instruction and the geometry support:
# regions, boundary conditions, loads, and the assumptions those conversions
# already make explicit.  It states no analysis dimensionality, coordinate
# system, solver target, meshing profile, solver profile, or requested result
# set, because the engineer never supplied any of them.  Schema version 2 gives
# those fields no model default, so each stays ``None`` -- *explicitly missing*
# -- and ``ir.validate`` reports the proposal as ``structurally_incomplete``
# and export-ineligible until a deliberate engineering-setup revision states
# them.  Confirming a region or accepting an assumption approves only that
# region or that assumption; it never synthesizes the missing configuration.
def proposal_analysis(units: Units | None = None) -> Analysis:
    """The analysis block a natural-language proposal can honestly state.

    Only the analysis type and the fixed internal mm-N-MPa unit convention are
    known here.  ``dimensionality``, ``solver_target`` and
    ``coordinate_system`` are deliberately left unset.
    """

    return Analysis(
        type="static_structural",
        units=(units or Units(length="mm", force="N", stress="MPa")).model_copy(
            deep=True
        ),
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
        _build_intent(
            instruction=instruction,
            grounding=grounding,
            material_proposal=interpretation.material_proposal,
        ),
    )


def _original_quantity(text: str) -> dict[str, float | str]:
    value, unit = text.rsplit(maxsplit=1)
    return {"value": float(value), "unit": unit}


def _build_intent(
    *,
    instruction: str,
    grounding: GroundingBatch,
    material_proposal: MaterialProposalPayload | None,
) -> SimulationIntent:
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
                originals: dict[str, dict[str, float | str]] = {}
                for axis, quantity_text in result.bc.components.items():
                    quantity = parse_quantity(quantity_text, expected_kind="length")
                    converted[axis] = quantity.value
                    originals[axis] = _original_quantity(quantity_text)
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
                        components_original=originals,
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

    materials: list[Material] = []
    if material_proposal is not None:
        proposal_text = (
            f"Proposed isotropic material '{material_proposal.name}' from the "
            f"numeric request: E={material_proposal.youngs_modulus}, "
            f"nu={material_proposal.poisson_ratio:g}"
            + (
                ""
                if material_proposal.density is None
                else f", density={material_proposal.density}"
            )
            + "."
        )
        decision = Assumption(
            text=proposal_text,
            criticality="unit_critical",
            status="pending",
        )
        youngs = parse_quantity(
            material_proposal.youngs_modulus, expected_kind="stress"
        )
        density = (
            None
            if material_proposal.density is None
            else normalize_quantity(
                float(material_proposal.density.rsplit(maxsplit=1)[0]),
                material_proposal.density.rsplit(maxsplit=1)[1],
                kind="density",
            )
        )
        materials.append(
            Material(
                name=material_proposal.name,
                model="linear_elastic_isotropic",
                authority="system_proposed",
                proposal_assumption_ref=decision.id,
                E_MPa=youngs.value,
                nu=material_proposal.poisson_ratio,
                density_tonne_per_mm3=None if density is None else density.value,
                youngs_modulus_original=_original_quantity(
                    material_proposal.youngs_modulus
                ),
                density_original=(
                    None
                    if material_proposal.density is None
                    else _original_quantity(material_proposal.density)
                ),
            )
        )
        assumptions.append(decision)
    unique_assumptions = {assumption.id: assumption for assumption in assumptions}
    return SimulationIntent(
        schema_version=SIMULATION_INTENT_SCHEMA_VERSION,
        analysis=proposal_analysis(),
        # Natural-language orchestration never assigns an unstated material.
        materials=materials,
        regions=regions,
        bcs=bcs,
        loads=loads,
        assumptions=list(unique_assumptions.values()),
        # Never server-generated: an engineering-setup revision must state them.
        mesh_settings=None,
        solver_settings=None,
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
    for item in proposal.materials:
        existing = materials.get(item.name)
        if existing is None:
            materials[item.name] = item.model_copy(deep=True)
    assumptions = {item.id: item.model_copy(deep=True) for item in current_assumptions}
    for item in proposal.assumptions:
        assumptions.setdefault(item.id, item.model_copy(deep=True))
    # The existing setup keeps authority over its own engineering configuration;
    # only a setup that has none yet inherits the proposal's.
    source = current if current is not None else proposal
    merged = SimulationIntent(
        schema_version=SIMULATION_INTENT_SCHEMA_VERSION,
        analysis=source.analysis.model_copy(deep=True),
        mesh_settings=(
            source.mesh_settings.model_copy(deep=True)
            if source.mesh_settings is not None
            else None
        ),
        solver_settings=(
            source.solver_settings.model_copy(deep=True)
            if source.solver_settings is not None
            else None
        ),
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
    payload = (
        canonical_bc_semantics(item)
        if item.type in {"fixed_displacement", "prescribed_displacement"}
        else canonical_load_semantics(item)
    )
    region_ref = payload.pop("region_ref", None)
    if item.type == "gravity" and region_ref is None:
        target: dict[str, Any] = {"scope": "whole_model"}
    else:
        region = regions[region_ref]
        from ir.schema import region_entity_membership

        entity_ids = sorted(
            (
                {"kind": type(value).__name__, "value": value}
                for value in region_entity_membership(region)
            ),
            key=lambda value: (value["kind"], str(value["value"])),
        )
        target = {
            "scope": "region",
            "entity_type": region.entity_type,
            "entity_ids": entity_ids,
        }
    internal_unit = {
        "fixed_displacement": "none",
        "prescribed_displacement": "mm",
        "resultant_surface_force": "N",
        "concentrated_force": "N",
        "surface_traction": "MPa",
        "pressure": "MPa",
        "gravity": "mm/s^2",
    }[item.type]
    return canonical_semantic_key(
        {"target": target, "condition": payload, "internal_unit": internal_unit},
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
