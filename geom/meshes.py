"""Mesh-format ingestion: Abaqus INP + STL via meshio (Task 5).

meshio does the structural reading (points, cells, NSET/ELSET names). Native
Abaqus ids are recovered by a light keyword scan of the file, because meshio
re-indexes nodes/elements 0-based in file order and drops the deck's own
numbering — regions must carry native ids to stay meaningful next to the
source deck. STL has no native ids, so facet ids are the 1-based file order
of the triangles (deterministic per file content).

Boundary facets (INP: tet faces used by exactly one tet, outward-oriented via
the owning tet; STL: the triangles themselves, file winding) are grouped by
normal-continuity region growing into FacetGroups carrying area, centroid and
normal — the same vocabulary FaceInventory gives the grounding layer for CAD.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import meshio
import numpy as np

from geom.inventory import file_sha256

# Facets whose normals differ by more than this dihedral angle start a new
# group (coarse meshes split curved faces into several groups; that is fine,
# grounding ranks groups, it does not need groups == CAD faces).
FEATURE_ANGLE_DEG = 30.0

_VOLUME_CELL_TYPES = {"tetra"}
_SURFACE_CELL_TYPES = {"triangle"}


@dataclass
class MeshRegion:
    """A named node/element set from the source deck, in native ids."""

    name: str
    kind: str  # "node_set" | "element_set"
    ids: list[int]  # native ids, sorted unique

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "ids": self.ids}

    @classmethod
    def from_dict(cls, data: dict) -> "MeshRegion":
        return cls(**data)


@dataclass
class BoundaryFacet:
    id: int  # stable facet id (see module docstring)
    node_ids: list[int]  # native node ids, outward winding order
    area: float
    centroid: list[float]
    normal: list[float]  # unit outward normal ([0,0,0] if degenerate)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_ids": self.node_ids,
            "area": self.area,
            "centroid": self.centroid,
            "normal": self.normal,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BoundaryFacet":
        return cls(**data)


@dataclass
class FacetGroup:
    """Contiguous boundary facets with continuous normals: the mesh analog of
    a CAD face record (area, centroid, normal) for grounding."""

    id: int
    facet_ids: list[int]  # sorted
    area: float
    centroid: list[float]  # area-weighted
    normal: list[float]  # normalized area-weighted mean ([0,0,0] if it cancels)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "facet_ids": self.facet_ids,
            "area": self.area,
            "centroid": self.centroid,
            "normal": self.normal,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FacetGroup":
        return cls(**data)


@dataclass
class MeshInventory:
    source_name: str
    file_sha256: str
    format: str  # "abaqus" | "stl"
    n_nodes: int
    n_elements: int
    regions: list[MeshRegion] = field(default_factory=list)
    facets: list[BoundaryFacet] = field(default_factory=list)
    facet_groups: list[FacetGroup] = field(default_factory=list)

    def region(self, name: str) -> MeshRegion:
        for r in self.regions:
            if r.name == name:
                return r
        raise KeyError(f"no region named {name!r}")

    def facet_group(self, group_id: int) -> FacetGroup:
        for g in self.facet_groups:
            if g.id == group_id:
                return g
        raise KeyError(f"no facet group with id {group_id}")

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "file_sha256": self.file_sha256,
            "format": self.format,
            "n_nodes": self.n_nodes,
            "n_elements": self.n_elements,
            "regions": [r.to_dict() for r in self.regions],
            "facets": [f.to_dict() for f in self.facets],
            "facet_groups": [g.to_dict() for g in self.facet_groups],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MeshInventory":
        return cls(
            source_name=data["source_name"],
            file_sha256=data["file_sha256"],
            format=data["format"],
            n_nodes=data["n_nodes"],
            n_elements=data["n_elements"],
            regions=[MeshRegion.from_dict(r) for r in data["regions"]],
            facets=[BoundaryFacet.from_dict(f) for f in data["facets"]],
            facet_groups=[FacetGroup.from_dict(g) for g in data["facet_groups"]],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "MeshInventory":
        return cls.from_dict(json.loads(text))


def load_mesh(path: str | Path, feature_angle_deg: float = FEATURE_ANGLE_DEG) -> MeshInventory:
    """Dispatch on file suffix: .inp -> Abaqus, .stl -> STL."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".inp":
        return parse_inp(path, feature_angle_deg)
    if suffix == ".stl":
        return parse_stl(path, feature_angle_deg)
    raise ValueError(f"unsupported mesh format: {path.name} (expected .inp or .stl)")


# --------------------------------------------------------------------------
# Abaqus INP


def _scan_inp_native_ids(path: Path) -> tuple[list[int], list[list[int]]]:
    """First column of *NODE / *ELEMENT data lines, in file order.

    Returns (node_ids, element_id_blocks); blocks align 1:1 with the cell
    blocks meshio produces (one per *ELEMENT keyword, in file order). An
    element data line ending in ',' continues on the next line.
    """
    node_ids: list[int] = []
    element_blocks: list[list[int]] = []
    mode: str | None = None
    continuing = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            keyword = line.split(",")[0].strip().upper()
            if keyword == "*NODE":
                mode = "node"
            elif keyword == "*ELEMENT":
                mode = "element"
                element_blocks.append([])
            else:
                mode = None
            continuing = False
            continue
        if mode == "node":
            node_ids.append(int(line.split(",")[0]))
        elif mode == "element":
            if not continuing:
                element_blocks[-1].append(int(line.split(",")[0]))
            continuing = line.endswith(",")
    return node_ids, element_blocks


def parse_inp(path: str | Path, feature_angle_deg: float = FEATURE_ANGLE_DEG) -> MeshInventory:
    path = Path(path)
    mesh = meshio.read(path, file_format="abaqus")
    node_ids, element_blocks = _scan_inp_native_ids(path)

    if len(node_ids) != len(mesh.points):
        raise ValueError(
            f"native-id scan found {len(node_ids)} nodes, meshio read {len(mesh.points)}"
        )
    if [len(b) for b in element_blocks] != [len(cb.data) for cb in mesh.cells]:
        raise ValueError("native-id scan element blocks disagree with meshio cell blocks")

    regions = [
        MeshRegion(
            name=name,
            kind="node_set",
            ids=sorted({node_ids[i] for i in np.asarray(indices, dtype=int)}),
        )
        for name, indices in mesh.point_sets.items()
    ]
    for name, per_block in mesh.cell_sets.items():
        ids: set[int] = set()
        for native, indices in zip(element_blocks, per_block):
            if indices is not None:
                ids.update(native[i] for i in np.asarray(indices, dtype=int))
        regions.append(MeshRegion(name=name, kind="element_set", ids=sorted(ids)))

    points = np.asarray(mesh.points, dtype=float)
    tets = [cb.data for cb in mesh.cells if cb.type in _VOLUME_CELL_TYPES]
    if tets:
        triangles = _boundary_triangles(np.vstack(tets), points)
    else:
        surface = [cb.data for cb in mesh.cells if cb.type in _SURFACE_CELL_TYPES]
        if not surface:
            raise ValueError(f"{path.name}: no tetra or triangle cells to build a boundary from")
        triangles = np.vstack(surface)

    # Derived facets have no native element id; sorting by the (sorted) native
    # node-id triple makes the numbering a pure function of the file content.
    order = sorted(
        range(len(triangles)),
        key=lambda i: tuple(sorted(node_ids[j] for j in triangles[i])),
    )
    facets = _build_facets(triangles[order], points, node_ids)
    groups = _group_facets(facets, feature_angle_deg)
    return MeshInventory(
        source_name=path.name,
        file_sha256=file_sha256(path),
        format="abaqus",
        n_nodes=len(node_ids),
        n_elements=sum(len(b) for b in element_blocks),
        regions=regions,
        facets=facets,
        facet_groups=groups,
    )


def _boundary_triangles(tets: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Outward-oriented tet faces used by exactly one tet (0-based indices).

    Orientation: the face winding is flipped so its normal points away from
    the owning tet's opposite vertex, i.e. out of the material.
    """
    seen: dict[tuple[int, int, int], tuple[int, int, int, int] | None] = {}
    for a, b, c, d in tets.astype(int):
        for tri, opposite in (((b, c, d), a), ((a, c, d), b), ((a, b, d), c), ((a, b, c), d)):
            key = tuple(sorted(tri))
            if key in seen:
                seen[key] = None  # interior face, shared by two tets
            else:
                seen[key] = (*tri, opposite)
    boundary = []
    for entry in seen.values():
        if entry is None:
            continue
        i, j, k, opposite = entry
        normal = np.cross(points[j] - points[i], points[k] - points[i])
        inward = float(np.dot(normal, points[opposite] - points[i])) > 0.0
        boundary.append((i, k, j) if inward else (i, j, k))
    return np.asarray(boundary, dtype=int)


def _build_facets(
    triangles: np.ndarray, points: np.ndarray, node_ids: list[int] | None
) -> list[BoundaryFacet]:
    """BoundaryFacet records for oriented triangles; ids are 1..N in the given
    order. node_ids maps 0-based point index -> native id (None: use index+1)."""
    facets = []
    for n, tri in enumerate(triangles, start=1):
        p0, p1, p2 = points[tri[0]], points[tri[1]], points[tri[2]]
        cross = np.cross(p1 - p0, p2 - p0)
        doubled = float(np.linalg.norm(cross))
        area = 0.5 * doubled
        normal = (cross / doubled).tolist() if doubled > 1e-12 else [0.0, 0.0, 0.0]
        native = [node_ids[i] if node_ids else i + 1 for i in tri.tolist()]
        facets.append(
            BoundaryFacet(
                id=n,
                node_ids=native,
                area=area,
                centroid=((p0 + p1 + p2) / 3.0).tolist(),
                normal=normal,
            )
        )
    return facets


def _group_facets(facets: list[BoundaryFacet], feature_angle_deg: float) -> list[FacetGroup]:
    """Region-grow facets across shared edges while normals stay within the
    feature angle; deterministic: seeds and traversal in ascending facet id."""
    cos_threshold = math.cos(math.radians(feature_angle_deg))
    by_id = {f.id: f for f in facets}

    facets_on_edge: dict[frozenset[int], list[int]] = {}
    for f in facets:
        n = f.node_ids
        for edge in (frozenset((n[0], n[1])), frozenset((n[1], n[2])), frozenset((n[2], n[0]))):
            facets_on_edge.setdefault(edge, []).append(f.id)
    neighbors: dict[int, set[int]] = {f.id: set() for f in facets}
    for ids in facets_on_edge.values():
        for fid in ids:
            neighbors[fid].update(i for i in ids if i != fid)

    assigned: set[int] = set()
    groups: list[FacetGroup] = []
    for seed in sorted(by_id):
        if seed in assigned:
            continue
        member_ids: list[int] = []
        stack = [seed]
        while stack:
            fid = stack.pop()
            if fid in assigned:
                continue
            assigned.add(fid)
            member_ids.append(fid)
            f = by_id[fid]
            for other in sorted(neighbors[fid] - assigned):
                if _cos_between(f.normal, by_id[other].normal) >= cos_threshold:
                    stack.append(other)
        members = [by_id[i] for i in sorted(member_ids)]
        area = sum(m.area for m in members)
        centroid = [
            sum(m.area * m.centroid[axis] for m in members) / area if area > 0 else 0.0
            for axis in range(3)
        ]
        mean = [sum(m.area * m.normal[axis] for m in members) for axis in range(3)]
        norm = math.sqrt(sum(x * x for x in mean))
        normal = [x / norm for x in mean] if norm > 1e-12 else [0.0, 0.0, 0.0]
        groups.append(
            FacetGroup(
                id=len(groups) + 1,
                facet_ids=[m.id for m in members],
                area=area,
                centroid=centroid,
                normal=normal,
            )
        )
    return groups


def _cos_between(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


# --------------------------------------------------------------------------
# STL


def parse_stl(path: str | Path, feature_angle_deg: float = FEATURE_ANGLE_DEG) -> MeshInventory:
    path = Path(path)
    mesh = meshio.read(path, file_format="stl")
    surface = [cb.data for cb in mesh.cells if cb.type in _SURFACE_CELL_TYPES]
    if not surface:
        raise ValueError(f"{path.name}: no triangles in STL")
    triangles = np.vstack(surface)
    # Facet ids are 1..N in file order; node ids are 1-based indices into the
    # deduplicated point list — both pure functions of the file content.
    facets = _build_facets(triangles, np.asarray(mesh.points, dtype=float), None)
    groups = _group_facets(facets, feature_angle_deg)
    return MeshInventory(
        source_name=path.name,
        file_sha256=file_sha256(path),
        format="stl",
        n_nodes=len(mesh.points),
        n_elements=len(triangles),
        regions=[],
        facets=facets,
        facet_groups=groups,
    )
