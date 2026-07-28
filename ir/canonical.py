"""Canonical semantic identities for engineering conditions and durable intent.

These helpers deliberately exclude original-input provenance from duplicate
identity while retaining normalized engineering meaning.  Provenance remains
in the immutable revision; it simply cannot hide a semantic duplicate.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ir.schema import SimulationIntent
from ir.versioning import dump_simulation_intent


def _stable_float(value: Any) -> str:
    """Return an exact, platform-stable identity for a normalized float."""

    return float(value).hex()


def canonical_bc_semantics(bc: Any) -> dict[str, Any]:
    """Return normalized BC meaning, excluding original-unit provenance."""

    if bc.type == "fixed_displacement":
        condition: dict[str, Any] = {
            "type": bc.type,
            "region_ref": bc.region_ref,
            "components": sorted(set(bc.components)),
        }
    else:
        condition = {
            "type": bc.type,
            "region_ref": bc.region_ref,
            "components": {
                axis: _stable_float(value)
                for axis, value in sorted(bc.components.items())
            },
        }
    return condition


def canonical_load_semantics(load: Any) -> dict[str, Any]:
    """Return normalized load meaning, excluding identifiers and provenance."""

    condition: dict[str, Any] = {
        "type": load.type,
        "region_ref": load.region_ref,
    }
    if load.type == "pressure":
        condition["magnitude"] = _stable_float(load.magnitude)
    else:
        condition["vector"] = [_stable_float(value) for value in load.vector]
    distribution = getattr(load, "distribution", None)
    if distribution is not None:
        condition["distribution"] = distribution
    return condition


def canonical_semantic_key(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def canonical_intent_document(intent: SimulationIntent) -> dict[str, Any]:
    """Return the durable document with semantically unordered loads sorted.

    Load authoring order is not engineering meaning.  Sorting the serialized
    load documents by normalized semantics and then by their full provenance
    keeps every submitted field while making the durable hash invariant to a
    pure load-list permutation.
    """

    payload = dump_simulation_intent(intent)
    typed_by_document = zip(payload["loads"], intent.loads, strict=True)
    payload["loads"] = [
        document
        for document, _load in sorted(
            typed_by_document,
            key=lambda pair: (
                canonical_semantic_key(canonical_load_semantics(pair[1])),
                canonical_semantic_key(pair[0]),
            ),
        )
    ]
    return payload
