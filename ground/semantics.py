"""Load and unit semantics for the mm-N-MPa internal convention (Task 7).

This is the sole module that turns user-facing quantities into internal
values.  It deliberately returns assumptions alongside every interpreted
load so conversions and semantic choices remain visible in the audit trail.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Sequence, cast, get_args

from ir.schema import Assumption, DensityQuantity, OriginalQuantity, StressQuantity


QuantityKind = Literal["force", "stress", "length", "density", "acceleration"]
CanonicalUnit = Literal["N", "MPa", "mm", "tonne/mm^3", "mm/s^2"]
ConstraintAxis = Literal["x", "y", "z"]
LoadType = Literal[
    "resultant_surface_force", "surface_traction", "pressure", "gravity", "concentrated_force"
]

# The single supported-unit table for every engineering quantity in the IR.
# Keys are case-folded spellings; values are (kind, factor to canonical, canonical).
# ``force``/``stress``/``length`` are the unchanged Task 7 text-phrase vocabulary.
# ``density``/``acceleration`` are structured-only kinds: they are never parsed
# out of free text, so ``_QUANTITY_RE`` deliberately does not mention them.
_UNIT_TABLE: dict[str, tuple[QuantityKind, float, CanonicalUnit]] = {
    "n": ("force", 1.0, "N"),
    "kn": ("force", 1_000.0, "N"),
    "mn": ("force", 1_000_000.0, "N"),
    "pa": ("stress", 1e-6, "MPa"),
    "kpa": ("stress", 1e-3, "MPa"),
    "mpa": ("stress", 1.0, "MPa"),
    "gpa": ("stress", 1_000.0, "MPa"),
    "mm": ("length", 1.0, "mm"),
    "m": ("length", 1_000.0, "mm"),
    "kg/m^3": ("density", 1e-12, "tonne/mm^3"),
    "kg/m3": ("density", 1e-12, "tonne/mm^3"),
    "t/mm^3": ("density", 1.0, "tonne/mm^3"),
    "tonne/mm^3": ("density", 1.0, "tonne/mm^3"),
    "mm/s^2": ("acceleration", 1.0, "mm/s^2"),
    "m/s^2": ("acceleration", 1_000.0, "mm/s^2"),
}
_QUANTITY_RE = re.compile(
    r"(?<![\w.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"(GPa|MPa|kPa|Pa|MN|kN|N|mm|m)\b",
    re.IGNORECASE,
)
SUPPORTED_QUANTITY_UNITS = ("N", "kN", "MN", "Pa", "kPa", "MPa", "GPa", "mm", "m")

#: Canonical wire spellings per quantity kind.  ``ir.schema`` mirrors these as
#: ``Literal`` unit vocabularies; ``tests/test_engineering_setup.py`` asserts the
#: two stay identical, so a unit may never be added in only one place.
SUPPORTED_UNITS_BY_KIND: dict[QuantityKind, tuple[str, ...]] = {
    "force": ("N", "kN", "MN"),
    "stress": ("Pa", "kPa", "MPa", "GPa"),
    "length": ("mm", "m"),
    "density": ("kg/m^3", "kg/m3", "t/mm^3", "tonne/mm^3"),
    "acceleration": ("mm/s^2", "m/s^2"),
}

#: Deterministic agreement tolerance between a client-submitted normalized value
#: and the value this module derives from the submitted original quantity.
#: Agreement is purely *relative* to the larger compared magnitude (or to an
#: explicit vector scale), so an exact zero must remain an exact zero and a
#: denormal density is not compared against a meaningless absolute floor.
NORMALIZATION_RELATIVE_TOLERANCE: float = 1e-9

#: Tolerance on ``|direction| == 1`` for a normalized direction vector.
DIRECTION_NORM_TOLERANCE: float = 1e-9
_VERTICAL_MOTION_RE = re.compile(
    r"\bvertical\s+(?:motion|movement|displacement|translation)\b",
    re.IGNORECASE,
)
_EXPLICIT_CONSTRAINT_AXIS_RE = re.compile(
    r"(?:\b[xyz]\s*(?:-|\s)?(?:axis|direction|motion|movement|displacement|translation)\b|"
    r"\b(?:positive|negative|plus|minus)\s*[xyz]\b|"
    r"\b(?:in|along)\s+(?:the\s+)?[xyz]\b)",
    re.IGNORECASE,
)


def _critical_assumption(text: str) -> Assumption:
    """Mark Task 7 unit/load/direction semantics as export-critical."""

    return Assumption(text=text, criticality="unit_critical", status="pending")


def semantics_vocabulary() -> dict[str, list[str]]:
    """Return the Task 7 load/unit vocabulary in deterministic prompt form."""
    return {
        "load_types": list(get_args(LoadType)),
        "quantity_units": list(SUPPORTED_QUANTITY_UNITS),
        "direction_terms": [
            "downward",
            "upward",
            "negative X",
            "positive X",
            "negative Y",
            "positive Y",
            "negative Z",
            "positive Z",
        ],
    }


@dataclass(frozen=True)
class ConvertedQuantity:
    value: float
    unit: CanonicalUnit
    kind: QuantityKind


@dataclass(frozen=True)
class SemanticLoad:
    type: LoadType
    value: float
    unit: str
    vector: tuple[float, float, float] | None
    assumptions: tuple[Assumption, ...]

    @property
    def magnitude(self) -> float:
        return self.value


def convert_value(value: float, unit: str) -> ConvertedQuantity:
    """Convert a numeric value into its canonical mm-N-MPa unit."""
    try:
        kind, factor, internal_unit = _UNIT_TABLE[unit.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported unit: {unit!r}") from exc
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("quantity must be finite")
    unit_name = cast(CanonicalUnit, internal_unit)
    return ConvertedQuantity(numeric * factor, unit_name, kind)


def parse_quantity(text: str, expected_kind: QuantityKind | None = None) -> ConvertedQuantity:
    """Parse the first supported value+unit token in *text*."""
    match = _QUANTITY_RE.search(text)
    if match is None:
        raise ValueError("no supported value+unit quantity found")
    quantity = convert_value(float(match.group(1)), match.group(2))
    if expected_kind is not None and quantity.kind != expected_kind:
        raise ValueError(f"expected {expected_kind} quantity, got {quantity.kind}")
    return quantity


# Descriptive alias used by callers that already split value and unit.
convert_to_internal = convert_value


def supported_units(kind: QuantityKind | None = None) -> tuple[str, ...]:
    """Return the supported original-unit vocabulary, optionally for one kind."""

    if kind is None:
        return tuple(
            unit for group in SUPPORTED_UNITS_BY_KIND.values() for unit in group
        )
    try:
        return SUPPORTED_UNITS_BY_KIND[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported quantity kind: {kind!r}") from exc


def unit_kind(unit: str) -> QuantityKind:
    """Return the quantity kind of a supported unit, or raise ``ValueError``."""

    try:
        return _UNIT_TABLE[str(unit).strip().lower()][0]
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"unsupported unit: {unit!r}") from exc


def normalize_quantity(
    value: float, unit: str, *, kind: QuantityKind | None = None
) -> ConvertedQuantity:
    """The single trusted original -> canonical conversion for the IR.

    Every engineering quantity that stores both an original and a normalized
    form is normalized here, so the supported unit vocabulary and the conversion
    factors can never disagree between call sites.
    """

    converted = convert_value(value, unit)
    if kind is not None and converted.kind != kind:
        raise ValueError(
            f"expected a {kind} quantity, got {converted.kind} from unit {unit!r}"
        )
    return converted


def normalized_matches(
    expected: float, actual: float, *, scale: float | None = None
) -> bool:
    """Deterministic agreement test between a derived and a submitted value.

    The comparison is relative to the larger of the two magnitudes, or to an
    explicit ``scale`` when one component of a vector is compared against the
    vector's own magnitude.  There is no absolute floor: without a scale an
    exact zero must stay an exact zero.
    """

    try:
        left, right = float(expected), float(actual)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(left) and math.isfinite(right)):
        return False
    reference = max(abs(left), abs(right))
    if scale is not None and math.isfinite(float(scale)):
        reference = max(reference, abs(float(scale)))
    if reference == 0.0:
        return left == right
    return abs(left - right) <= NORMALIZATION_RELATIVE_TOLERANCE * reference


def direction_norm(direction: Sequence[float]) -> float:
    """Euclidean norm of a candidate direction, or ``nan`` when unusable."""

    try:
        components = [float(value) for value in direction]
    except (TypeError, ValueError):
        return math.nan
    if len(components) != 3 or not all(math.isfinite(value) for value in components):
        return math.nan
    return math.sqrt(sum(value * value for value in components))


def is_unit_direction(direction: Sequence[float]) -> bool:
    """True only for a finite three-component vector of unit length."""

    norm = direction_norm(direction)
    if not math.isfinite(norm):
        return False
    return abs(norm - 1.0) <= DIRECTION_NORM_TOLERANCE


def normalize_youngs_modulus(value: float, unit: str) -> tuple[float, OriginalQuantity]:
    """Normalize a user-entered elastic modulus to MPa with audit provenance."""

    converted = normalize_quantity(value, unit, kind="stress")
    if converted.value <= 0:
        raise ValueError("Young's modulus must be a positive stress quantity")
    return converted.value, StressQuantity(value=float(value), unit=unit)


def normalize_density(value: float, unit: str) -> tuple[float, OriginalQuantity]:
    """Normalize supported density units to the canonical tonne/mm^3 unit."""

    converted = normalize_quantity(value, unit, kind="density")
    if converted.value <= 0:
        raise ValueError("density must be finite and greater than zero")
    return converted.value, DensityQuantity(value=float(value), unit=unit)


def parse_direction(
    text: str,
    *,
    downward_axis: Literal["x", "y", "z"] = "y",
) -> tuple[tuple[float, float, float], Assumption]:
    """Resolve an axis direction and return the required audit assumption."""
    lower = text.lower().replace("−", "-")
    explicit = re.search(r"(?:negative|minus|-)\s*([xyz])\b", lower)
    if explicit:
        axis, sign = explicit.group(1), -1.0
        reason = f"Direction was interpreted as the explicit negative {axis.upper()} axis."
    else:
        explicit = re.search(r"(?:positive|plus|\+)\s*([xyz])\b", lower)
        if explicit:
            axis, sign = explicit.group(1), 1.0
            reason = f"Direction was interpreted as the explicit positive {axis.upper()} axis."
        elif "downward" in lower or "downwards" in lower or re.search(r"\bdown\b", lower):
            axis, sign = downward_axis, -1.0
            reason = f"Downward was interpreted as the negative {axis.upper()} axis per model convention."
        elif "upward" in lower or "upwards" in lower or re.search(r"\bup\b", lower):
            axis, sign = downward_axis, 1.0
            reason = f"Upward was interpreted as the positive {axis.upper()} axis per model convention."
        else:
            raise ValueError("no supported direction found")
    vector = [0.0, 0.0, 0.0]
    vector["xyz".index(axis)] = sign
    return (vector[0], vector[1], vector[2]), _critical_assumption(reason)


def normalize_fixed_displacement_components(
    text: str,
    components: Sequence[ConstraintAxis],
    *,
    vertical_axis: ConstraintAxis = "y",
) -> tuple[list[ConstraintAxis], Assumption | None]:
    """Apply the Task 7 model-axis convention to qualitative constraints.

    An explicit axis always wins. Otherwise, ``vertical motion`` and its
    supported synonyms constrain only the configured vertical axis.
    """

    normalized = list(components)
    if not _VERTICAL_MOTION_RE.search(text):
        return normalized, None
    if _EXPLICIT_CONSTRAINT_AXIS_RE.search(text):
        return normalized, None
    return [vertical_axis], _critical_assumption(
        f"Vertical motion was interpreted as the {vertical_axis.upper()} displacement component per model convention."
    )


def interpret_load(
    phrase: str,
    *,
    region_count: int | None = None,
    node_count: int | None = None,
    downward_axis: Literal["x", "y", "z"] = "y",
) -> SemanticLoad:
    """Interpret one supported load phrase into internal units.

    Force units on/across a face mean a resultant.  Stress units mean pressure
    unless ``traction`` is explicit.  ``per node`` force values are multiplied
    by ``node_count`` (``region_count`` is accepted as a compatibility alias).
    """
    lower = phrase.lower()
    assumptions: list[Assumption] = []

    if "gravity" in lower:
        direction, direction_assumption = parse_direction(phrase, downward_axis=downward_axis)
        match = _QUANTITY_RE.search(phrase)
        if match:
            raise ValueError("gravity acceleration units are not part of the supported unit vocabulary")
        value = 9_810.0
        vector = tuple(value * component for component in direction)
        assumptions.extend([
            direction_assumption,
            _critical_assumption("Standard gravity was interpreted as 9810 mm/s^2."),
        ])
        return SemanticLoad("gravity", value, "mm/s^2", vector, tuple(assumptions))

    quantity = parse_quantity(phrase)
    if quantity.kind == "length":
        raise ValueError("a length quantity cannot define a load")

    if quantity.kind == "stress":
        if "traction" in lower:
            load_type: LoadType = "surface_traction"
            direction, direction_assumption = parse_direction(phrase, downward_axis=downward_axis)
            vector = tuple(quantity.value * component for component in direction)
            assumptions.append(direction_assumption)
            semantic = "surface traction because traction was explicit"
        else:
            load_type, vector = "pressure", None
            semantic = "pressure because stress units were supplied"
            assumptions.append(_critical_assumption(
                "Positive pressure was interpreted as acting into the surface."
            ))
        assumptions.append(_critical_assumption(
            f"The {quantity.unit} value was interpreted as {semantic}."
        ))
        return SemanticLoad(load_type, quantity.value, quantity.unit, vector, tuple(assumptions))

    count = node_count if node_count is not None else region_count
    value = quantity.value
    if "per node" in lower or "each node" in lower:
        if count is None or count <= 0:
            raise ValueError("a positive node_count is required for a per-node force")
        value *= count
        assumptions.append(_critical_assumption(
            f"The per-node force was converted to a total over {count} nodes."
        ))

    concentrated = any(token in lower for token in ("concentrated", "point load", "at node", "on node"))
    load_type = "concentrated_force" if concentrated else "resultant_surface_force"
    direction, direction_assumption = parse_direction(phrase, downward_axis=downward_axis)
    assumptions.append(direction_assumption)
    assumptions.append(_critical_assumption(
        "The force value was interpreted as a concentrated force."
        if concentrated
        else "The force value was interpreted as a total resultant, not pressure."
    ))
    vector = tuple(value * component for component in direction)
    return SemanticLoad(load_type, value, quantity.unit, vector, tuple(assumptions))


def interpret_load_phrase(*args: object, **kwargs: object) -> SemanticLoad:
    """Backward-friendly descriptive alias for :func:`interpret_load`."""
    return interpret_load(*args, **kwargs)  # type: ignore[arg-type]
