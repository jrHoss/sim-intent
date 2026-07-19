"""Deterministic, composable face queries (Task 6).

The query language is deliberately small and JSON-native.  It selects only
from geometry records supplied by Python; it never accepts model-generated
entity ids as an answer.  Cylinder metadata is supplied alongside the raw
``FaceInventory`` because true radii and axes cannot be recovered from its
bounding boxes (CLAUDE.md rule 11).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from geom.cylinders import CylinderRecord, group_holes
from geom.inventory import FaceInventory
from geom.labels import adjacency_graph, area_rank, component_of, extreme_face_labels, height_rank


@dataclass(frozen=True)
class Query:
    """A JSON-serializable sequence of query operations."""

    ops: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"ops": self.ops}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Query":
        ops = value.get("ops")
        if not isinstance(ops, list) or not all(isinstance(op, dict) for op in ops):
            raise ValueError("Query.ops must be a list of operation objects")
        return cls(ops=ops)

    @classmethod
    def from_json(cls, value: str) -> "Query":
        return cls.from_dict(json.loads(value))

    def then(self, *ops: dict[str, Any]) -> "Query":
        """Return a new query with operations appended."""
        return Query([*self.ops, *ops])


@dataclass(frozen=True)
class QueryResult:
    entity_ids: list[int]
    per_candidate_scores: dict[int, float]

    @property
    def score_margin(self) -> float:
        """Top-minus-runner-up score (a lone candidate has margin 1)."""
        scores = sorted(self.per_candidate_scores.values(), reverse=True)
        if not scores:
            return 0.0
        if len(scores) == 1:
            return 1.0
        return scores[0] - scores[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_ids": self.entity_ids,
            "per_candidate_scores": self.per_candidate_scores,
            "score_margin": self.score_margin,
        }


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def _unit(vector: Sequence[float]) -> list[float]:
    if len(vector) != 3:
        raise ValueError("axis direction must have three components")
    norm = math.sqrt(_dot(vector, vector))
    if norm == 0:
        raise ValueError("axis direction cannot be zero")
    return [float(x) / norm for x in vector]


def _op_name(op: dict[str, Any]) -> str:
    name = op.get("op")
    if not isinstance(name, str):
        raise ValueError("each query operation requires a string 'op'")
    return name


@dataclass
class QueryEngine:
    inventory: FaceInventory
    cylinders: dict[int, CylinderRecord] = field(default_factory=dict)

    def execute(self, query: Query | list[dict[str, Any]]) -> QueryResult:
        ops = query.ops if isinstance(query, Query) else query
        if not isinstance(ops, list):
            raise ValueError("ops must be a list")
        ids = {face.tag for face in self.inventory.faces}
        scores = {tag: 1.0 for tag in ids}
        ids, scores = self._run(ops, ids, scores)
        ordered = sorted(ids, key=lambda tag: (-scores.get(tag, 0.0), tag))
        return QueryResult(ordered, {tag: scores.get(tag, 0.0) for tag in ordered})

    def _run(
        self,
        ops: list[dict[str, Any]],
        ids: set[int],
        scores: dict[int, float],
    ) -> tuple[set[int], dict[int, float]]:
        for op in ops:
            if not isinstance(op, dict):
                raise ValueError("each query operation must be an object")
            name = _op_name(op)

            if name == "find_faces":
                surface_type = op.get("surface_type")
                matches = {
                    face.tag
                    for face in self.inventory.faces
                    if surface_type is None or face.surface_type == surface_type
                }
                ids &= matches
            elif name == "holes":
                ids &= {
                    tag for tag, rec in self.cylinders.items() if rec.classification == "hole"
                }
            elif name == "hole_groups":
                groups = group_holes(self.cylinders)
                min_size = int(op.get("min_size", 1))
                max_size = int(op.get("max_size", 2**31 - 1))
                matches = {
                    tag
                    for group in groups
                    if min_size <= len(group.face_tags) <= max_size
                    for tag in group.face_tags
                }
                ids &= matches
                group_sizes = {
                    tag: len(group.face_tags) for group in groups for tag in group.face_tags
                }
                largest = max(group_sizes.values(), default=1)
                scores = {
                    tag: scores.get(tag, 1.0) * group_sizes[tag] / largest for tag in ids
                }
            elif name == "filter_radius":
                radius = float(op.get("radius", op.get("r")))
                rtol = float(op.get("rtol", 0.05))
                if radius <= 0 or rtol < 0:
                    raise ValueError("radius must be positive and rtol non-negative")
                matches: set[int] = set()
                next_scores: dict[int, float] = {}
                for tag in ids:
                    rec = self.cylinders.get(tag)
                    if rec is None:
                        continue
                    relative_error = abs(rec.radius - radius) / radius
                    if relative_error <= rtol:
                        matches.add(tag)
                        fit = 1.0 if rtol == 0 else 1.0 - relative_error / rtol
                        next_scores[tag] = scores.get(tag, 1.0) * fit
                ids, scores = matches, next_scores
            elif name == "filter_axis":
                requested = _unit(op.get("direction", op.get("dir")))
                tol_deg = float(op.get("tol_deg", 2.0))
                cutoff = math.cos(math.radians(tol_deg))
                matches = set()
                next_scores = {}
                for tag in ids:
                    rec = self.cylinders.get(tag)
                    if rec is None:
                        continue
                    alignment = abs(_dot(_unit(rec.axis_dir), requested))
                    if alignment >= cutoff:
                        matches.add(tag)
                        next_scores[tag] = scores.get(tag, 1.0) * alignment
                ids, scores = matches, next_scores
            elif name == "rank_by":
                predicate = op.get("position", op.get("predicate"))
                count = int(op.get("n", 1))
                if predicate in {"top", "upper", "highest", "above"}:
                    ranking = height_rank([f for f in self.inventory.faces if f.tag in ids])
                elif predicate in {"bottom", "lower", "lowest", "below"}:
                    ranking = [
                        face.tag
                        for face in sorted(
                            (f for f in self.inventory.faces if f.tag in ids),
                            key=lambda f: (f.centroid[2], f.tag),
                        )
                    ]
                elif predicate in {"largest", "area_max"}:
                    ranking = area_rank([f for f in self.inventory.faces if f.tag in ids])
                elif predicate in {"smallest", "area_min"}:
                    ranking = [
                        face.tag
                        for face in sorted(
                            (f for f in self.inventory.faces if f.tag in ids),
                            key=lambda f: (f.area, f.tag),
                        )
                    ]
                else:
                    raise ValueError(f"unknown position predicate: {predicate!r}")
                ids = set(ranking[:count])
                scores = {tag: 1.0 - i / max(len(ranking), 1) for i, tag in enumerate(ranking[:count])}
            elif name in {"area_max", "area_min"}:
                ranking = area_rank([f for f in self.inventory.faces if f.tag in ids])
                if name == "area_min":
                    ranking = [
                        face.tag
                        for face in sorted(
                            (f for f in self.inventory.faces if f.tag in ids),
                            key=lambda f: (f.area, f.tag),
                        )
                    ]
                count = int(op.get("n", 1))
                ids = set(ranking[:count])
                scores = {tag: 1.0 - i / max(len(ranking), 1) for i, tag in enumerate(ranking[:count])}
            elif name == "adjacent_to":
                targets = _ids_arg(op)
                graph = adjacency_graph(self.inventory.faces)
                matches = set().union(*(graph.get(tag, set()) for tag in targets)) if targets else set()
                ids &= matches
            elif name == "in_component":
                tag = int(op.get("id", op.get("entity_id")))
                ids &= set(component_of(self.inventory.faces, tag))
            elif name == "labeled":
                label = str(op.get("name", op.get("label", "")))
                if label.endswith("_face"):
                    label = label[:-5]
                tag = extreme_face_labels(self.inventory.faces).get(label)
                ids &= {tag} if tag is not None else set()
                scores = {candidate: 1.0 for candidate in ids}
            elif name in {"intersect", "union", "difference"}:
                branches = op.get("queries", op.get("operands"))
                if not isinstance(branches, list) or not branches:
                    raise ValueError(f"{name} requires non-empty queries")
                results = [self.execute(Query.from_dict(branch) if isinstance(branch, dict) and "ops" in branch else branch) for branch in branches]
                sets = [set(result.entity_ids) for result in results]
                if name == "intersect":
                    combined = set.intersection(*sets)
                elif name == "union":
                    combined = set.union(*sets)
                else:
                    combined = sets[0].difference(*sets[1:])
                ids &= combined
                scores = {
                    tag: max(
                        (result.per_candidate_scores.get(tag, 0.0) for result in results),
                        default=0.0,
                    )
                    for tag in ids
                }
            else:
                raise ValueError(f"unknown query operation: {name}")

            scores = {tag: scores.get(tag, 1.0) for tag in ids}
        return ids, scores


def _ids_arg(op: dict[str, Any]) -> set[int]:
    raw: Any = op.get("ids", op.get("entity_ids", []))
    if isinstance(raw, int):
        raw = [raw]
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise ValueError("ids must be an integer or list of integers")
    return {int(tag) for tag in raw}


def execute(
    inventory: FaceInventory,
    ops: Query | list[dict[str, Any]],
    cylinders: dict[int, CylinderRecord] | None = None,
) -> QueryResult:
    """Convenience functional API over :class:`QueryEngine`."""
    return QueryEngine(inventory, cylinders or {}).execute(ops)


# Operation factories keep interpreter output on a fixed, typed vocabulary
# while remaining plain dictionaries after JSON serialization.
def find_faces(surface_type: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "find_faces"}
    if surface_type is not None:
        op["surface_type"] = surface_type
    return op


def holes() -> dict[str, Any]:
    return {"op": "holes"}


def hole_groups(min_size: int = 1, max_size: int | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "hole_groups", "min_size": min_size}
    if max_size is not None:
        op["max_size"] = max_size
    return op


def filter_radius(radius: float, rtol: float = 0.05) -> dict[str, Any]:
    return {"op": "filter_radius", "radius": radius, "rtol": rtol}


def filter_axis(direction: Sequence[float], tol_deg: float = 2.0) -> dict[str, Any]:
    return {"op": "filter_axis", "direction": list(direction), "tol_deg": tol_deg}


def rank_by(position: str, n: int = 1) -> dict[str, Any]:
    return {"op": "rank_by", "position": position, "n": n}


def area_max(n: int = 1) -> dict[str, Any]:
    return {"op": "area_max", "n": n}


def area_min(n: int = 1) -> dict[str, Any]:
    return {"op": "area_min", "n": n}


def adjacent_to(ids: int | Iterable[int]) -> dict[str, Any]:
    values = [ids] if isinstance(ids, int) else list(ids)
    return {"op": "adjacent_to", "ids": values}


def in_component(entity_id: int) -> dict[str, Any]:
    return {"op": "in_component", "id": entity_id}


def labeled(name: str) -> dict[str, Any]:
    return {"op": "labeled", "name": name}


def _combine(name: str, *queries: Query | list[dict[str, Any]]) -> dict[str, Any]:
    operands = [query.to_dict() if isinstance(query, Query) else {"ops": query} for query in queries]
    return {"op": name, "queries": operands}


def intersect(*queries: Query | list[dict[str, Any]]) -> dict[str, Any]:
    return _combine("intersect", *queries)


def union(*queries: Query | list[dict[str, Any]]) -> dict[str, Any]:
    return _combine("union", *queries)


def difference(*queries: Query | list[dict[str, Any]]) -> dict[str, Any]:
    return _combine("difference", *queries)
