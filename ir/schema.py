"""Solver-neutral simulation-intent IR (Task 1).

Pydantic v2 models per EXECUTION_PLAN Task 1 and CLAUDE.md standing rules:

- Every Region carries entity_ids, selection_method, confidence,
  source_instruction (verbatim user text) and status. None are optional.
- Every SimulationIntent carries a units block, assumptions[] and
  validation_status.
- Internal units are fixed to mm-N-MPa; unit conversion happens upstream
  (ground/semantics.py, Task 7), never here.
- Nothing exports until every region status == "confirmed":
  SimulationIntent.export_payload() raises ExportBlockedError otherwise.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExportBlockedError(RuntimeError):
    """Raised when an IR with non-confirmed regions is asked to export."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Analysis / units
# --------------------------------------------------------------------------

class Units(StrictModel):
    """Internal convention is mm-N-MPa; the IR only ever stores these."""

    length: Literal["mm"]
    force: Literal["N"]
    stress: Literal["MPa"] = "MPa"


class Analysis(StrictModel):
    type: Literal["static_structural"]
    units: Units


# --------------------------------------------------------------------------
# Material
# --------------------------------------------------------------------------

class Material(StrictModel):
    name: str
    model: Literal["linear_elastic_isotropic"]
    E_MPa: float = Field(gt=0)
    nu: float = Field(gt=-1.0, lt=0.5)


# --------------------------------------------------------------------------
# Region
# --------------------------------------------------------------------------

EntityType = Literal["cad_face", "cad_edge", "mesh_face", "node_set", "element_set"]
SelectionMethod = Literal[
    "semantic_geometry_query", "multimodal_reference", "user_click", "user_confirmed"
]
RegionStatus = Literal["proposed", "confirmed", "rejected"]


class Region(StrictModel):
    id: str
    entity_type: EntityType
    entity_ids: Union[list[int], list[str]] = Field(min_length=1)
    selection_method: SelectionMethod
    confidence: float = Field(ge=0.0, le=1.0)
    source_instruction: str
    status: RegionStatus


# --------------------------------------------------------------------------
# Boundary conditions
# --------------------------------------------------------------------------

Axis = Literal["x", "y", "z"]


class FixedDisplacementBC(StrictModel):
    type: Literal["fixed_displacement"]
    region_ref: str
    components: list[Axis] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_components(self) -> "FixedDisplacementBC":
        if len(set(self.components)) != len(self.components):
            raise ValueError("duplicate axis in components")
        return self


class PrescribedDisplacementBC(StrictModel):
    type: Literal["prescribed_displacement"]
    region_ref: str
    components: dict[Axis, float] = Field(min_length=1)  # displacement in mm


BC = Annotated[
    Union[FixedDisplacementBC, PrescribedDisplacementBC],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# Loads
# --------------------------------------------------------------------------

Vector3 = Annotated[list[float], Field(min_length=3, max_length=3)]


class ResultantSurfaceForceLoad(StrictModel):
    type: Literal["resultant_surface_force"]
    region_ref: str
    vector: Vector3  # total force, N


class SurfaceTractionLoad(StrictModel):
    type: Literal["surface_traction"]
    region_ref: str
    vector: Vector3  # traction, MPa


class PressureLoad(StrictModel):
    type: Literal["pressure"]
    region_ref: str
    magnitude: float  # MPa, positive = into the surface


class GravityLoad(StrictModel):
    type: Literal["gravity"]
    # None means the entire model; otherwise an element_set region.
    region_ref: Union[str, None] = None
    vector: Vector3  # acceleration, mm/s^2


class ConcentratedForceLoad(StrictModel):
    type: Literal["concentrated_force"]
    region_ref: str
    vector: Vector3  # force, N


Load = Annotated[
    Union[
        ResultantSurfaceForceLoad,
        SurfaceTractionLoad,
        PressureLoad,
        GravityLoad,
        ConcentratedForceLoad,
    ],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# Assumptions / top-level intent
# --------------------------------------------------------------------------

class Assumption(StrictModel):
    text: str
    status: Literal["pending", "accepted", "rejected"]


ValidationStatus = Literal["unvalidated", "valid", "invalid"]


class SimulationIntent(StrictModel):
    analysis: Analysis
    materials: list[Material]
    regions: list[Region]
    bcs: list[BC]
    loads: list[Load]
    assumptions: list[Assumption]
    validation_status: ValidationStatus = "unvalidated"

    @model_validator(mode="after")
    def _check_region_refs(self) -> "SimulationIntent":
        region_ids = [r.id for r in self.regions]
        if len(set(region_ids)) != len(region_ids):
            raise ValueError("duplicate region ids")
        known = set(region_ids)
        for item in [*self.bcs, *self.loads]:
            ref = item.region_ref
            if ref is not None and ref not in known:
                raise ValueError(
                    f"{item.type} references unknown region '{ref}'"
                )
        return self

    def export_payload(self) -> dict:
        """Serialize for export adapters.

        Architectural confirmation gate (CLAUDE.md rule 3): refuses unless
        every region status == "confirmed".
        """
        blocked = [r.id for r in self.regions if r.status != "confirmed"]
        if blocked:
            raise ExportBlockedError(
                "export blocked: regions not confirmed: " + ", ".join(blocked)
            )
        return self.model_dump(mode="json")


def export_json_schema() -> dict:
    """JSON Schema for the full IR."""
    return SimulationIntent.model_json_schema()


if __name__ == "__main__":
    print(json.dumps(export_json_schema(), indent=2))
