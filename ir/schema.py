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

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Annotated, ClassVar, Final, Literal, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ir.schema_version import SIMULATION_INTENT_SCHEMA_VERSION


class ExportBlockedError(RuntimeError):
    """Raised when an IR with non-confirmed regions is asked to export."""


class EngineeringConsistencyError(ValueError):
    """A contradictory engineering quantity, carrying a stable machine code.

    Pydantic wraps this into its ``ValidationError``; the ``code`` is rendered
    into the message so an API client and a test both see the same stable token
    that ``ir.validate`` reports as ``ValidationIssue.code``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Engineering quantity provenance
# --------------------------------------------------------------------------
#
# ``ground.semantics`` owns the supported-unit table and every conversion.
# The ``Literal`` vocabularies below are this module's wire mirror of
# ``ground.semantics.SUPPORTED_UNITS_BY_KIND``; a conformance test asserts the
# two are identical so a unit can never be added in only one place.  Importing
# ``ground.semantics`` at module scope would invert the ``ground -> ir`` layer
# direction, so the arithmetic is reached through the lazy helpers below.

ForceUnit = Literal["N", "kN", "MN"]
StressUnit = Literal["Pa", "kPa", "MPa", "GPa"]
LengthUnit = Literal["mm", "m"]
DensityUnit = Literal["kg/m^3", "kg/m3", "t/mm^3", "tonne/mm^3"]
AccelerationUnit = Literal["mm/s^2", "m/s^2"]


def _normalize(value: float, unit: str, kind: str) -> float:
    from ground.semantics import normalize_quantity

    return normalize_quantity(value, unit, kind=kind).value  # type: ignore[arg-type]


def _agrees(expected: float, actual: float, *, scale: float | None = None) -> bool:
    from ground.semantics import normalized_matches

    return normalized_matches(expected, actual, scale=scale)


def _direction_norm(direction: Sequence[float]) -> float:
    from ground.semantics import direction_norm

    return direction_norm(direction)


def _is_unit_direction(direction: Sequence[float]) -> bool:
    from ground.semantics import is_unit_direction

    return is_unit_direction(direction)


#: The single server-owned code published for *every* unsupported engineering
#: unit, on every quantity, at every nesting depth.  It is a fixed constant that
#: no request can influence, so a client can neither choose it nor learn a raw
#: validator message through it.
UNSUPPORTED_UNIT_CODE: Final[str] = "quantity.unsupported_unit"


class OriginalQuantity(StrictModel):
    """Auditable user-entered scalar before normalization.

    Subclasses narrow ``unit`` to one supported vocabulary.  This base declares
    no ``unit`` field and is never used directly as a field type, so an
    arbitrary nonempty unit string is not representable anywhere in the IR.
    """

    #: The ``ground.semantics`` quantity kind whose vocabulary ``unit`` narrows
    #: to.  Every subclass states one; the conformance test in
    #: ``tests/test_engineering_setup.py`` asserts the ``Literal`` and the
    #: central table agree, so this can never drift into a second vocabulary.
    QUANTITY_KIND: ClassVar[str]

    value: float

    @field_validator("unit", mode="before", check_fields=False)
    @classmethod
    def _supported_unit(cls, value: object) -> object:
        """Trusted pre-validation boundary for the unit vocabulary.

        This runs *before* the ``Literal`` core schema, so an unsupported unit
        is reported with one stable engineering code instead of a generic
        ``literal_error`` whose text is a pydantic implementation detail.  The
        declared field type is untouched, so the enum still reaches the
        generated JSON Schema, OpenAPI and TypeScript contracts.

        The message is a fixed sentence plus the server's own supported
        vocabulary; the rejected value is never echoed back.
        """

        from ground.semantics import supported_units

        vocabulary = supported_units(cls.QUANTITY_KIND)  # type: ignore[arg-type]
        if isinstance(value, str) and value in vocabulary:
            return value
        raise EngineeringConsistencyError(
            UNSUPPORTED_UNIT_CODE,
            f"the supported {cls.QUANTITY_KIND} units are "
            f"{', '.join(vocabulary)}",
        )

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise EngineeringConsistencyError(
                "quantity.nonfinite", "an original quantity must be finite"
            )
        return value


class ForceQuantity(OriginalQuantity):
    QUANTITY_KIND: ClassVar[str] = "force"
    unit: ForceUnit


class StressQuantity(OriginalQuantity):
    QUANTITY_KIND: ClassVar[str] = "stress"
    unit: StressUnit


class LengthQuantity(OriginalQuantity):
    QUANTITY_KIND: ClassVar[str] = "length"
    unit: LengthUnit


class DensityQuantity(OriginalQuantity):
    QUANTITY_KIND: ClassVar[str] = "density"
    unit: DensityUnit


class AccelerationQuantity(OriginalQuantity):
    QUANTITY_KIND: ClassVar[str] = "acceleration"
    unit: AccelerationUnit


# --------------------------------------------------------------------------
# Analysis / units
# --------------------------------------------------------------------------

class Units(StrictModel):
    """Internal convention is mm-N-MPa; the IR only ever stores these."""

    length: Literal["mm"]
    force: Literal["N"]
    stress: Literal["MPa"] = "MPa"


class Analysis(StrictModel):
    """Analysis configuration.

    ``dimensionality``, ``solver_target`` and ``coordinate_system`` were
    introduced by schema version 2 and default to ``None`` — *explicitly
    missing*, not silently approved.  A schema-version-1 payload migrated
    forward keeps them ``None`` and is therefore structurally incomplete until
    an engineer states them; a current-version submission must supply them.
    """

    type: Literal["static_structural"]
    units: Units
    dimensionality: Literal["3d_solid"] | None = None
    solver_target: Literal["calculix"] | None = None
    coordinate_system: Literal["global_cartesian"] | None = None


# --------------------------------------------------------------------------
# Material
# --------------------------------------------------------------------------

class Material(StrictModel):
    name: str
    model: Literal["linear_elastic_isotropic"]
    E_MPa: float = Field(gt=0)
    nu: float = Field(gt=-1.0, lt=0.5)
    # Optional for non-body-load analyses. The canonical mm-N-MPa mass unit
    # is tonne, so density is stored in tonne/mm^3.
    density_tonne_per_mm3: float | None = Field(default=None, gt=0)
    # Provenance is optional, but a declared original must agree with the
    # normalized value it claims to explain.
    youngs_modulus_original: StressQuantity | None = None
    density_original: DensityQuantity | None = None

    @field_validator("E_MPa", "nu", "density_tonne_per_mm3")
    @classmethod
    def _finite_properties(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise EngineeringConsistencyError(
                "material.nonfinite", "material properties must be finite"
            )
        return value

    def check_consistency(self) -> None:
        """Raise :class:`EngineeringConsistencyError` on a contradiction.

        Exposed so ``ir.validate`` can report the same stable code as a
        :class:`~ir.validate.ValidationIssue` for objects that were built below
        the schema with ``model_construct``.
        """

        if self.youngs_modulus_original is not None:
            expected = _normalize(
                self.youngs_modulus_original.value,
                self.youngs_modulus_original.unit,
                "stress",
            )
            if not _agrees(expected, self.E_MPa):
                raise EngineeringConsistencyError(
                    "material.youngs_modulus_normalization_mismatch",
                    f"{self.youngs_modulus_original.value} "
                    f"{self.youngs_modulus_original.unit} normalizes to "
                    f"{expected} MPa, not {self.E_MPa} MPa",
                )
        if self.density_original is not None:
            if self.density_tonne_per_mm3 is None:
                raise EngineeringConsistencyError(
                    "material.density_normalization_missing",
                    "density provenance was supplied without a normalized density",
                )
            expected = _normalize(
                self.density_original.value, self.density_original.unit, "density"
            )
            if not _agrees(expected, self.density_tonne_per_mm3):
                raise EngineeringConsistencyError(
                    "material.density_normalization_mismatch",
                    f"{self.density_original.value} {self.density_original.unit} "
                    f"normalizes to {expected} tonne/mm^3, not "
                    f"{self.density_tonne_per_mm3} tonne/mm^3",
                )

    @model_validator(mode="after")
    def _consistent_provenance(self) -> "Material":
        self.check_consistency()
        return self


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
    """Zero-only prescribed displacement.

    The R3.1 preview envelope has no verified nonzero prescribed-displacement
    interpretation or export path, so every component must be an exact zero.
    Signed zero is accepted because ``-0.0 == 0.0``; any other value is a
    deterministic rejection rather than a silently exported approximation.
    """

    type: Literal["prescribed_displacement"]
    region_ref: str
    components: dict[Axis, float] = Field(min_length=1)  # displacement in mm
    components_original: dict[Axis, LengthQuantity] | None = None

    def check_consistency(self) -> None:
        for axis, value in sorted(self.components.items()):
            if not math.isfinite(value):
                raise EngineeringConsistencyError(
                    "bc.prescribed_displacement_nonfinite",
                    f"prescribed displacement component '{axis}' must be finite",
                )
            if value != 0.0:
                raise EngineeringConsistencyError(
                    "bc.prescribed_displacement_nonzero",
                    "the supported envelope permits zero prescribed displacement "
                    f"only; component '{axis}' is {value}",
                )
        if self.components_original is not None:
            if set(self.components_original) != set(self.components):
                raise EngineeringConsistencyError(
                    "bc.displacement_provenance_mismatch",
                    "prescribed-displacement provenance must cover exactly the "
                    "normalized components",
                )
            for axis, original in sorted(self.components_original.items()):
                expected = _normalize(original.value, original.unit, "length")
                if not _agrees(expected, self.components[axis]):
                    raise EngineeringConsistencyError(
                        "bc.displacement_normalization_mismatch",
                        f"{original.value} {original.unit} normalizes to "
                        f"{expected} mm, not {self.components[axis]} mm on "
                        f"component '{axis}'",
                    )

    @model_validator(mode="after")
    def _zero_only_components(self) -> "PrescribedDisplacementBC":
        self.check_consistency()
        return self


BC = Annotated[
    Union[FixedDisplacementBC, PrescribedDisplacementBC],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# Loads
# --------------------------------------------------------------------------

Vector3 = Annotated[list[float], Field(min_length=3, max_length=3)]


def _check_canonical_vector(load_type: str, vector: Sequence[float]) -> None:
    """A canonical load vector must be finite and carry a nonzero magnitude."""

    if not all(math.isfinite(float(value)) for value in vector):
        raise EngineeringConsistencyError(
            "load.vector_nonfinite", f"{load_type} vector components must be finite"
        )
    if not any(float(value) != 0.0 for value in vector):
        raise EngineeringConsistencyError(
            "load.vector_zero", f"{load_type} vector magnitude must be nonzero"
        )


def _check_directed_provenance(
    *,
    code_prefix: str,
    kind: str,
    canonical_unit: str,
    vector: Sequence[float],
    original: OriginalQuantity | None,
    magnitude: float | None,
    direction: Sequence[float] | None,
) -> None:
    """Enforce ``vector == magnitude * direction`` against the original quantity.

    Provenance is optional, but it is all-or-nothing: an original quantity, its
    normalized magnitude, and a normalized nonzero direction are one indivisible
    statement.  Vector/magnitude and vector/direction disagreement are reported
    as two distinct stable codes so a client can tell them apart.
    """

    supplied = [original is not None, magnitude is not None, direction is not None]
    if not any(supplied):
        return
    if not all(supplied):
        raise EngineeringConsistencyError(
            f"{code_prefix}.provenance_incomplete",
            "an original quantity, a normalized magnitude, and a normalized "
            "direction must be supplied together",
        )
    assert original is not None and magnitude is not None and direction is not None

    expected_magnitude = _normalize(original.value, original.unit, kind)
    if expected_magnitude <= 0.0:
        raise EngineeringConsistencyError(
            f"{code_prefix}.magnitude_nonpositive",
            f"the original quantity normalizes to {expected_magnitude} "
            f"{canonical_unit}, which cannot define a load",
        )
    if not _agrees(expected_magnitude, magnitude):
        raise EngineeringConsistencyError(
            f"{code_prefix}.magnitude_normalization_mismatch",
            f"{original.value} {original.unit} normalizes to "
            f"{expected_magnitude} {canonical_unit}, not {magnitude}",
        )

    norm = _direction_norm(direction)
    if not math.isfinite(norm):
        raise EngineeringConsistencyError(
            f"{code_prefix}.direction_nonfinite",
            "the normalized direction must have three finite components",
        )
    if norm == 0.0:
        raise EngineeringConsistencyError(
            f"{code_prefix}.direction_zero",
            "the normalized direction must be nonzero",
        )
    if not _is_unit_direction(direction):
        raise EngineeringConsistencyError(
            f"{code_prefix}.direction_not_normalized",
            f"the direction has length {norm}, not 1",
        )

    vector_norm = _direction_norm(vector)
    if not _agrees(magnitude, vector_norm):
        raise EngineeringConsistencyError(
            f"{code_prefix}.vector_magnitude_mismatch",
            f"the vector magnitude {vector_norm} does not match the normalized "
            f"magnitude {magnitude}",
        )
    for index, component in enumerate(direction):
        expected = magnitude * float(component)
        if not _agrees(expected, float(vector[index]), scale=magnitude):
            raise EngineeringConsistencyError(
                f"{code_prefix}.vector_direction_mismatch",
                f"component {index} should be {expected} for magnitude "
                f"{magnitude} along {list(direction)}, not {vector[index]}",
            )


class ResultantSurfaceForceLoad(StrictModel):
    """A total force on a surface region, with unambiguous force provenance."""

    type: Literal["resultant_surface_force"]
    region_ref: str
    vector: Vector3  # total force, N
    original_force: ForceQuantity | None = None
    magnitude_N: float | None = None
    direction: Vector3 | None = None
    distribution: Literal["uniform"] = "uniform"

    def check_consistency(self) -> None:
        _check_canonical_vector(self.type, self.vector)
        _check_directed_provenance(
            code_prefix="load.force",
            kind="force",
            canonical_unit="N",
            vector=self.vector,
            original=self.original_force,
            magnitude=self.magnitude_N,
            direction=self.direction,
        )

    @model_validator(mode="after")
    def _consistent(self) -> "ResultantSurfaceForceLoad":
        self.check_consistency()
        return self


class SurfaceTractionLoad(StrictModel):
    """The already-supported traction interpretation: a uniform MPa vector.

    ``original_traction`` is the traction magnitude the engineer entered and
    ``direction`` is the unit vector it acts along; the canonical ``vector`` is
    their product in MPa.  No second traction mode exists.
    """

    type: Literal["surface_traction"]
    region_ref: str
    vector: Vector3  # traction, MPa
    original_traction: StressQuantity | None = None
    magnitude_MPa: float | None = None
    direction: Vector3 | None = None
    distribution: Literal["uniform"] = "uniform"

    def check_consistency(self) -> None:
        _check_canonical_vector(self.type, self.vector)
        _check_directed_provenance(
            code_prefix="load.traction",
            kind="stress",
            canonical_unit="MPa",
            vector=self.vector,
            original=self.original_traction,
            magnitude=self.magnitude_MPa,
            direction=self.direction,
        )

    @model_validator(mode="after")
    def _consistent(self) -> "SurfaceTractionLoad":
        self.check_consistency()
        return self


class PressureLoad(StrictModel):
    """A nonnegative scalar pressure on the inward surface normal.

    There is deliberately no ``direction`` field: the sign convention is the
    surface normal, so a client-controlled direction is not representable and
    ``extra="forbid"`` rejects one.
    """

    type: Literal["pressure"]
    region_ref: str
    magnitude: float = Field(ge=0)  # MPa, positive = into the surface
    original_pressure: StressQuantity | None = None
    distribution: Literal["uniform"] = "uniform"

    def check_consistency(self) -> None:
        if not math.isfinite(self.magnitude):
            raise EngineeringConsistencyError(
                "load.pressure_nonfinite", "pressure magnitude must be finite"
            )
        if self.original_pressure is not None:
            expected = _normalize(
                self.original_pressure.value, self.original_pressure.unit, "stress"
            )
            if expected < 0.0:
                raise EngineeringConsistencyError(
                    "load.pressure_negative",
                    "pressure is a nonnegative scalar on the inward surface normal",
                )
            if not _agrees(expected, self.magnitude):
                raise EngineeringConsistencyError(
                    "load.pressure_normalization_mismatch",
                    f"{self.original_pressure.value} "
                    f"{self.original_pressure.unit} normalizes to {expected} MPa, "
                    f"not {self.magnitude} MPa",
                )

    @model_validator(mode="after")
    def _consistent(self) -> "PressureLoad":
        self.check_consistency()
        return self


class GravityLoad(StrictModel):
    """Model-wide gravity acceleration in mm/s^2.

    ``region_ref`` stays ``None`` for the whole model; the only supported
    non-null target is an element-set material domain (see
    ``LOAD_REGION_COMPATIBILITY``).  A surface target is never valid.
    """

    type: Literal["gravity"]
    region_ref: Union[str, None] = None
    vector: Vector3  # acceleration, mm/s^2
    original_acceleration: AccelerationQuantity | None = None
    magnitude_mm_per_s2: float | None = None
    direction: Vector3 | None = None
    distribution: Literal["uniform"] = "uniform"

    def check_consistency(self) -> None:
        _check_canonical_vector(self.type, self.vector)
        _check_directed_provenance(
            code_prefix="load.gravity",
            kind="acceleration",
            canonical_unit="mm/s^2",
            vector=self.vector,
            original=self.original_acceleration,
            magnitude=self.magnitude_mm_per_s2,
            direction=self.direction,
        )

    @model_validator(mode="after")
    def _consistent(self) -> "GravityLoad":
        self.check_consistency()
        return self


class ConcentratedForceLoad(StrictModel):
    type: Literal["concentrated_force"]
    region_ref: str
    vector: Vector3  # force, N
    original_force: ForceQuantity | None = None
    magnitude_N: float | None = None
    direction: Vector3 | None = None
    distribution: Literal["uniform"] = "uniform"

    def check_consistency(self) -> None:
        _check_canonical_vector(self.type, self.vector)
        _check_directed_provenance(
            code_prefix="load.force",
            kind="force",
            canonical_unit="N",
            vector=self.vector,
            original=self.original_force,
            magnitude=self.magnitude_N,
            direction=self.direction,
        )

    @model_validator(mode="after")
    def _consistent(self) -> "ConcentratedForceLoad":
        self.check_consistency()
        return self


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
# Load / boundary-condition to region compatibility
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionTargetRule:
    """Whether a condition needs a region, and which entity types are valid.

    ``entity_types`` is drawn only from the existing :data:`EntityType`
    vocabulary and only from what the current export and interpretation
    envelope already resolves.  No mesh-domain entity type is invented here.
    """

    target: Literal["required", "optional", "prohibited"]
    entity_types: frozenset[str]


#: Region entity types that denote a loadable surface today: CAD faces for the
#: Abaqus adapter and Task 5 boundary facet groups for the CalculiX adapter.
SURFACE_REGION_ENTITY_TYPES: frozenset[str] = frozenset({"cad_face", "mesh_face"})

#: Entity types a displacement constraint resolves to: CAD faces (Abaqus) plus
#: native node sets and facet groups (CalculiX).
CONSTRAINT_REGION_ENTITY_TYPES: frozenset[str] = frozenset(
    {"cad_face", "mesh_face", "node_set"}
)

#: The single authoritative load -> region compatibility table.
LOAD_REGION_COMPATIBILITY: dict[str, RegionTargetRule] = {
    # A resultant force is applied to a surface; CalculiX additionally
    # distributes it exactly over the nodes of a native node set.
    "resultant_surface_force": RegionTargetRule(
        "required", SURFACE_REGION_ENTITY_TYPES | {"node_set"}
    ),
    "surface_traction": RegionTargetRule("required", SURFACE_REGION_ENTITY_TYPES),
    "pressure": RegionTargetRule("required", SURFACE_REGION_ENTITY_TYPES),
    # Gravity is model-wide.  The only supported non-null target is an
    # element-set material domain; every surface target is rejected.
    "gravity": RegionTargetRule("optional", frozenset({"element_set"})),
    "concentrated_force": RegionTargetRule("required", frozenset({"node_set"})),
}

#: The same table for displacement constraints.
BC_REGION_COMPATIBILITY: dict[str, RegionTargetRule] = {
    "fixed_displacement": RegionTargetRule("required", CONSTRAINT_REGION_ENTITY_TYPES),
    "prescribed_displacement": RegionTargetRule(
        "required", CONSTRAINT_REGION_ENTITY_TYPES
    ),
}


# --------------------------------------------------------------------------
# Mesh and solver configuration
# --------------------------------------------------------------------------


class MeshSettings(StrictModel):
    """The deterministic preview meshing profile.

    Every field is required: schema version 2 makes the meshing decision an
    explicit client statement rather than a default a legacy payload could
    silently acquire.
    """

    global_element_size_mm: float = Field(gt=0)
    element_type: Literal["tetrahedral"]
    element_order: Literal["first_order"]
    mesher: Literal["gmsh"]
    mesher_preset: Literal["gmsh_tet_v1"]
    target_size_original: LengthQuantity | None = None

    @field_validator("global_element_size_mm")
    @classmethod
    def _finite_size(cls, value: float) -> float:
        if not math.isfinite(value):
            raise EngineeringConsistencyError(
                "mesh.element_size_nonfinite",
                "the global target element size must be finite",
            )
        return value

    def check_consistency(self) -> None:
        if self.target_size_original is not None:
            expected = _normalize(
                self.target_size_original.value,
                self.target_size_original.unit,
                "length",
            )
            if not _agrees(expected, self.global_element_size_mm):
                raise EngineeringConsistencyError(
                    "mesh.target_size_normalization_mismatch",
                    f"{self.target_size_original.value} "
                    f"{self.target_size_original.unit} normalizes to {expected} mm, "
                    f"not {self.global_element_size_mm} mm",
                )

    @model_validator(mode="after")
    def _consistent(self) -> "MeshSettings":
        self.check_consistency()
        return self


ResultField = Literal["displacement", "stress", "reaction_force"]


class SolverSettings(StrictModel):
    """The deterministic preview solver profile; every field is required."""

    target: Literal["calculix"]
    analysis_profile: Literal["linear_static_v1"]
    requested_results: list[ResultField] = Field(min_length=1)

    @field_validator("requested_results")
    @classmethod
    def _unique_results(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise EngineeringConsistencyError(
                "solver.requested_results_duplicated",
                "requested results must be unique",
            )
        return value


# --------------------------------------------------------------------------
# Assumptions / top-level intent
# --------------------------------------------------------------------------

AssumptionCriticality = Literal["unit_critical", "noncritical"]


class Assumption(StrictModel):
    """Auditable inference with a stable identity and explicit criticality.

    Task 1 assumptions predate explicit identifiers and criticality.  Defaults
    keep those payloads valid, while the identifier is deterministically
    derived from immutable assumption content instead of an array index.
    """

    id: str = Field(default="", min_length=1)
    text: str = Field(min_length=1)
    criticality: AssumptionCriticality = "noncritical"
    status: Literal["pending", "accepted", "rejected"]

    @model_validator(mode="before")
    @classmethod
    def _assign_stable_id(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        text = value.get("text")
        criticality = value.get("criticality", "noncritical")
        if not isinstance(text, str) or not isinstance(criticality, str):
            return value
        digest = hashlib.sha256(
            f"{criticality}\0{text}".encode("utf-8")
        ).hexdigest()[:16]
        expected = f"assumption_{digest}"
        supplied = value.get("id")
        if supplied not in (None, "", expected):
            raise ValueError("assumption id does not match its immutable content")
        return {**value, "id": expected}


ValidationStatus = Literal["unvalidated", "valid", "invalid"]


class SimulationIntent(StrictModel):
    # Task 19 (ADR-004): explicit positive-integer payload version.  The
    # default keeps in-process construction ergonomic; it must never be relied
    # on for untrusted input.  ``ir.versioning.load_simulation_intent`` is the
    # authoritative external ingestion path and requires an explicit
    # declaration.
    schema_version: int = Field(
        default=SIMULATION_INTENT_SCHEMA_VERSION, ge=1
    )
    analysis: Analysis
    materials: list[Material]
    regions: list[Region]
    bcs: list[BC]
    loads: list[Load]
    assumptions: list[Assumption]
    # Schema version 2 additions.  ``None`` means *explicitly missing*: a
    # migrated legacy payload never acquires a mesh or solver profile, and a
    # current-version submission must state one before the setup can be ready.
    mesh_settings: MeshSettings | None = None
    solver_settings: SolverSettings | None = None
    validation_status: ValidationStatus = "unvalidated"

    @model_validator(mode="after")
    def _check_region_refs(self) -> "SimulationIntent":
        region_ids = [r.id for r in self.regions]
        if len(set(region_ids)) != len(region_ids):
            raise ValueError("duplicate region ids")
        assumption_ids = [assumption.id for assumption in self.assumptions]
        if len(set(assumption_ids)) != len(assumption_ids):
            raise ValueError("duplicate assumption ids")
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
