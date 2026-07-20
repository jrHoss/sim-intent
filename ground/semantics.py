"""Load and unit semantics for the mm-N-MPa internal convention (Task 7).

This is the sole module that turns user-facing quantities into internal
values.  It deliberately returns assumptions alongside every interpreted
load so conversions and semantic choices remain visible in the audit trail.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, cast, get_args

from ir.schema import Assumption


QuantityKind = Literal["force", "stress", "length"]
LoadType = Literal[
    "resultant_surface_force", "surface_traction", "pressure", "gravity", "concentrated_force"
]

_UNIT_TABLE: dict[str, tuple[QuantityKind, float, str]] = {
    "n": ("force", 1.0, "N"),
    "kn": ("force", 1_000.0, "N"),
    "mn": ("force", 1_000_000.0, "N"),
    "pa": ("stress", 1e-6, "MPa"),
    "kpa": ("stress", 1e-3, "MPa"),
    "mpa": ("stress", 1.0, "MPa"),
    "gpa": ("stress", 1_000.0, "MPa"),
    "mm": ("length", 1.0, "mm"),
    "m": ("length", 1_000.0, "mm"),
}
_QUANTITY_RE = re.compile(
    r"(?<![\w.])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"(GPa|MPa|kPa|Pa|MN|kN|N|mm|m)\b",
    re.IGNORECASE,
)
SUPPORTED_QUANTITY_UNITS = ("N", "kN", "MN", "Pa", "kPa", "MPa", "GPa", "mm", "m")


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
    unit: Literal["N", "MPa", "mm"]
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
    """Convert a numeric value into N, MPa, or mm."""
    try:
        kind, factor, internal_unit = _UNIT_TABLE[unit.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported unit: {unit!r}") from exc
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("quantity must be finite")
    unit_name = cast(Literal["N", "MPa", "mm"], internal_unit)
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
