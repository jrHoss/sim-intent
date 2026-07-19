"""STEP parsing via gmsh OCC (Task 2).

Loads a STEP file and produces one FaceRecord per CAD face. Face tags are
stable across reimports of the same file (CLAUDE.md quirk), but NOT across
regeneration of the geometry — which is why inventories are cached keyed by
file hash (geom/inventory.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gmsh


@dataclass
class FaceRecord:
    tag: int
    surface_type: str  # gmsh OCC type name: "Plane", "Cylinder", ...
    area: float
    centroid: list[float]  # [x, y, z], mm
    bbox_min: list[float]
    bbox_max: list[float]
    normal: list[float]  # outward normal sampled at the parametric midpoint
    edge_tags: list[int]  # unique perimeter edge tags, sorted

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "surface_type": self.surface_type,
            "area": self.area,
            "centroid": self.centroid,
            "bbox_min": self.bbox_min,
            "bbox_max": self.bbox_max,
            "normal": self.normal,
            "edge_tags": self.edge_tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FaceRecord":
        return cls(**data)


def parse_step(path: str | Path) -> list[FaceRecord]:
    """Parse a STEP file into per-face records.

    Runs a private gmsh session (initialize/finalize per call) so parses are
    isolated from each other and from any caller gmsh state.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if gmsh.isInitialized():
        raise RuntimeError("parse_step requires exclusive use of gmsh")

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(f"parse_{path.stem}")
        gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()

        records = []
        for dim, tag in gmsh.model.getEntities(2):
            surface_type = gmsh.model.getType(dim, tag)
            area = gmsh.model.occ.getMass(dim, tag)
            centroid = list(gmsh.model.occ.getCenterOfMass(dim, tag))
            bbox = gmsh.model.getBoundingBox(dim, tag)
            lo, hi = gmsh.model.getParametrizationBounds(dim, tag)
            mid = [(lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0]
            # For OCC solids gmsh returns the outward-oriented normal here
            # (verified against both fixtures; see PROGRESS.md Task 2).
            normal = list(gmsh.model.getNormal(tag, mid))
            edges = gmsh.model.getBoundary(
                [(dim, tag)], combined=False, oriented=False, recursive=False
            )
            # A cylinder's seam edge shows up twice; keep unique tags.
            edge_tags = sorted({abs(etag) for _, etag in edges})
            records.append(
                FaceRecord(
                    tag=tag,
                    surface_type=surface_type,
                    area=area,
                    centroid=centroid,
                    bbox_min=list(bbox[:3]),
                    bbox_max=list(bbox[3:]),
                    normal=normal,
                    edge_tags=edge_tags,
                )
            )
        return records
    finally:
        gmsh.finalize()
