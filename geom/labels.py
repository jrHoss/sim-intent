"""Planar extreme-face labels, adjacency, spatial predicates (Task 4).

Extreme-face labels (top/bottom/left/right/front/back) name, per direction,
the PLANAR face whose outward normal aligns with that direction and whose
centroid lies farthest along it. Model-frame convention: +Z up, +X right,
+Y back (direction words in Task 7 record this choice as an Assumption).

Only planes are eligible for labels: a cylinder's sampled midpoint normal can
accidentally align with an axis (the bracket wall hole's seam normal is +Z),
so eligibility is by surface type, never by normal alone.
"""

from __future__ import annotations

import math

from geom.parser import FaceRecord

DIRECTION_LABELS: dict[str, tuple[float, float, float]] = {
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "right": (1.0, 0.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "front": (0.0, -1.0, 0.0),
}
UP = DIRECTION_LABELS["top"]

# cos(~8 deg): how well a face normal must align with a label direction.
_ALIGN_DOT = 0.99


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v) -> list[float]:
    n = math.sqrt(_dot(v, v))
    return [x / n for x in v]


def extreme_face_labels(faces: list[FaceRecord]) -> dict[str, int]:
    """Map label -> face tag; directions with no aligned planar face are absent.

    Winner per direction = aligned planar face with the farthest centroid
    projection along the direction; exact projection ties break to the lowest
    tag (deterministic; exact ties only occur for coplanar faces).
    """
    labels: dict[str, int] = {}
    for label, direction in DIRECTION_LABELS.items():
        candidates = [
            f
            for f in faces
            if f.surface_type == "Plane" and _dot(_unit(f.normal), direction) >= _ALIGN_DOT
        ]
        if candidates:
            best = min(candidates, key=lambda f: (-_dot(f.centroid, direction), f.tag))
            labels[label] = best.tag
    return labels


def face_labels(faces: list[FaceRecord]) -> dict[int, list[str]]:
    """Inverse view: face tag -> its labels (tags without labels are absent)."""
    inverse: dict[int, list[str]] = {}
    for label, tag in extreme_face_labels(faces).items():
        inverse.setdefault(tag, []).append(label)
    return inverse


def largest_face(faces: list[FaceRecord]) -> int:
    """Tag of the largest-area face; exact area ties break to the lowest tag."""
    if not faces:
        raise ValueError("no faces")
    return min(faces, key=lambda f: (-f.area, f.tag)).tag


def adjacency_graph(faces: list[FaceRecord]) -> dict[int, set[int]]:
    """Face tag -> tags of faces sharing at least one perimeter edge."""
    faces_on_edge: dict[int, set[int]] = {}
    for f in faces:
        for edge in f.edge_tags:
            faces_on_edge.setdefault(edge, set()).add(f.tag)
    graph: dict[int, set[int]] = {f.tag: set() for f in faces}
    for tags in faces_on_edge.values():
        for tag in tags:
            graph[tag] |= tags - {tag}
    return graph


def connected_components(faces: list[FaceRecord]) -> list[list[int]]:
    """Edge-connected face components, each sorted, ordered by smallest member."""
    graph = adjacency_graph(faces)
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in sorted(graph):
        if start in seen:
            continue
        stack, component = [start], set()
        while stack:
            tag = stack.pop()
            if tag in component:
                continue
            component.add(tag)
            stack.extend(graph[tag] - component)
        seen |= component
        components.append(sorted(component))
    return components


def component_of(faces: list[FaceRecord], tag: int) -> list[int]:
    """The connected component containing `tag` (sorted)."""
    for component in connected_components(faces):
        if tag in component:
            return component
    raise KeyError(f"no face with tag {tag}")


def height(face: FaceRecord, up=UP) -> float:
    """Centroid projection along the up direction."""
    return _dot(face.centroid, up)


def is_above(a: FaceRecord, b: FaceRecord, up=UP, min_gap: float = 0.0) -> bool:
    return height(a, up) > height(b, up) + min_gap


def is_below(a: FaceRecord, b: FaceRecord, up=UP, min_gap: float = 0.0) -> bool:
    return is_above(b, a, up, min_gap)


def height_rank(faces: list[FaceRecord], up=UP) -> list[int]:
    """All face tags, highest centroid first; exact ties in tag order."""
    return [f.tag for f in sorted(faces, key=lambda f: (-height(f, up), f.tag))]


def area_rank(faces: list[FaceRecord]) -> list[int]:
    """All face tags, largest area first; exact ties in tag order."""
    return [f.tag for f in sorted(faces, key=lambda f: (-f.area, f.tag))]
