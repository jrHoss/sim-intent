"""Cylindrical face analysis + hole semantics (Task 3).

For each cylindrical face of a STEP file: true radius and axis (principal
curvatures + least-squares circle fit of sampled surface points — never the
bbox span), angular extent (full circle vs partial), length along the axis.

Classification (CLAUDE.md rule 11): "cylindrical face" != "hole".
- hole:           full circle AND the outward face normal points toward the
                  axis (material lies outside the cylinder).
- boss:           full circle, normal points away from the axis.
- fillet_partial: angular extent < full circle (e.g. the bracket fillet,
                  which is surface-type Cylinder but only a quarter arc).

Grouping: holes cluster by radius (relative tolerance) then by axis
direction; coaxial detection compares axis lines, not just directions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gmsh
import numpy as np

TWO_PI = 2.0 * np.pi

# Sampling density over the face's (u, v) parametric rectangle.
_N_U = 48
_N_V = 8


@dataclass
class CylinderRecord:
    tag: int
    radius: float
    axis_dir: list[float]  # unit vector, sign-canonicalized
    axis_point: list[float]  # a point on the axis (mid-length)
    angular_extent: float  # radians; TWO_PI when full
    full_circle: bool
    length: float  # extent along the axis
    normal_points_inward: bool  # outward face normal points toward the axis
    classification: str  # "hole" | "boss" | "fillet_partial"

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "radius": self.radius,
            "axis_dir": self.axis_dir,
            "axis_point": self.axis_point,
            "angular_extent": self.angular_extent,
            "full_circle": self.full_circle,
            "length": self.length,
            "normal_points_inward": self.normal_points_inward,
            "classification": self.classification,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CylinderRecord":
        return cls(**data)


@dataclass
class HoleGroup:
    face_tags: list[int]  # sorted
    radius: float  # mean radius of the group
    axis_dir: list[float]

    def to_dict(self) -> dict:
        return {
            "face_tags": self.face_tags,
            "radius": self.radius,
            "axis_dir": self.axis_dir,
        }


def _canonical_sign(v: np.ndarray) -> np.ndarray:
    """Flip an axis direction so its dominant component is positive."""
    i = int(np.argmax(np.abs(v)))
    return -v if v[i] < 0 else v


def _fit_circle_2d(xy: np.ndarray) -> tuple[np.ndarray, float]:
    """Kasa least-squares circle fit: returns (center, radius)."""
    A = np.column_stack([2.0 * xy, np.ones(len(xy))])
    b = (xy**2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    center = sol[:2]
    radius = float(np.sqrt(sol[2] + center @ center))
    return center, radius


def _analyze_face(tag: int) -> CylinderRecord:
    """Analyze one cylindrical face inside an active gmsh session."""
    lo, hi = gmsh.model.getParametrizationBounds(2, tag)
    us = np.linspace(lo[0], hi[0], _N_U)
    vs = np.linspace(lo[1], hi[1], _N_V)
    uu, vv = np.meshgrid(us, vs)
    params = np.column_stack([uu.ravel(), vv.ravel()]).ravel()

    points = np.asarray(gmsh.model.getValue(2, tag, params)).reshape(-1, 3)

    # Axis direction = principal direction of (near-)zero curvature; radius
    # from the nonzero principal curvature. Averaged over all samples.
    curv_max, curv_min, dir_max, dir_min = gmsh.model.getPrincipalCurvatures(tag, params)
    curv_max = np.asarray(curv_max)
    curv_min = np.asarray(curv_min)
    dir_max = np.asarray(dir_max).reshape(-1, 3)
    dir_min = np.asarray(dir_min).reshape(-1, 3)

    flat_is_min = np.abs(curv_min) < np.abs(curv_max)
    axis_samples = np.where(flat_is_min[:, None], dir_min, dir_max)
    bending = np.where(flat_is_min, curv_max, curv_min)
    radius_curv = float(np.mean(1.0 / np.abs(bending)))

    # Align sample signs before averaging (principal directions are ±).
    ref = axis_samples[0]
    axis_samples = np.where((axis_samples @ ref)[:, None] < 0, -axis_samples, axis_samples)
    axis = axis_samples.mean(axis=0)
    axis = _canonical_sign(axis / np.linalg.norm(axis))

    # Project points onto a plane perpendicular to the axis, fit the circle.
    p0 = points.mean(axis=0)
    e1 = np.cross(axis, [1.0, 0.0, 0.0])
    if np.linalg.norm(e1) < 1e-6:
        e1 = np.cross(axis, [0.0, 1.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)

    rel = points - p0
    t = rel @ axis
    xy = np.column_stack([rel @ e1, rel @ e2])
    center_2d, radius_fit = _fit_circle_2d(xy)

    # Cross-check the two independent radius estimates.
    if not np.isclose(radius_fit, radius_curv, rtol=0.01):
        raise ValueError(
            f"face {tag}: circle-fit radius {radius_fit:.4f} disagrees with "
            f"curvature radius {radius_curv:.4f}"
        )

    t_mid = 0.5 * (t.min() + t.max())
    axis_point = p0 + center_2d[0] * e1 + center_2d[1] * e2 + t_mid * axis
    length = float(t.max() - t.min())

    # Angular extent from coverage of point angles about the fitted center:
    # the largest gap between consecutive sorted angles is the uncovered arc.
    ang = np.sort(np.arctan2(xy[:, 1] - center_2d[1], xy[:, 0] - center_2d[0]))
    gaps = np.diff(ang)
    wrap_gap = TWO_PI - (ang[-1] - ang[0])
    max_gap = float(max(gaps.max(), wrap_gap))
    full_circle = max_gap <= 2.5 * (TWO_PI / _N_U)
    angular_extent = TWO_PI if full_circle else float(TWO_PI - max_gap)

    # Outward normal vs radial direction at the parametric midpoint decides
    # which side the material is on.
    mid = [(lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0]
    p_mid = np.asarray(gmsh.model.getValue(2, tag, mid))
    normal = np.asarray(gmsh.model.getNormal(tag, mid))
    radial = p_mid - axis_point
    radial -= (radial @ axis) * axis
    inward = bool(normal @ radial < 0)

    if not full_circle:
        classification = "fillet_partial"
    elif inward:
        classification = "hole"
    else:
        classification = "boss"

    return CylinderRecord(
        tag=tag,
        radius=radius_fit,
        axis_dir=[float(x) for x in axis],
        axis_point=[float(x) for x in axis_point],
        angular_extent=angular_extent,
        full_circle=full_circle,
        length=length,
        normal_points_inward=inward,
        classification=classification,
    )


def analyze_cylinders(path: str | Path) -> dict[int, CylinderRecord]:
    """Analyze every cylindrical face of a STEP file, keyed by face tag.

    Runs a private gmsh session, like geom.parser.parse_step.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if gmsh.isInitialized():
        raise RuntimeError("analyze_cylinders requires exclusive use of gmsh")

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(f"cyl_{path.stem}")
        gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()

        records = {}
        for dim, tag in gmsh.model.getEntities(2):
            if gmsh.model.getType(dim, tag) == "Cylinder":
                records[tag] = _analyze_face(tag)
        return records
    finally:
        gmsh.finalize()


def holes(records: dict[int, CylinderRecord]) -> set[int]:
    return {tag for tag, r in records.items() if r.classification == "hole"}


def _same_axis_dir(a: CylinderRecord, b: CylinderRecord, tol_deg: float) -> bool:
    dot = abs(np.dot(a.axis_dir, b.axis_dir))
    return dot >= np.cos(np.radians(tol_deg))


def are_coaxial(
    a: CylinderRecord,
    b: CylinderRecord,
    axis_tol_deg: float = 2.0,
    dist_tol: float = 0.1,
) -> bool:
    """True when two cylinders share the same axis line (not just direction)."""
    if not _same_axis_dir(a, b, axis_tol_deg):
        return False
    offset = np.asarray(b.axis_point) - np.asarray(a.axis_point)
    perp = offset - (offset @ np.asarray(a.axis_dir)) * np.asarray(a.axis_dir)
    return bool(np.linalg.norm(perp) <= dist_tol)


def group_holes(
    records: dict[int, CylinderRecord],
    radius_rtol: float = 0.05,
    axis_tol_deg: float = 2.0,
) -> list[HoleGroup]:
    """Group hole faces by radius cluster, then by axis direction.

    Groups are sorted by (radius, tags) for deterministic output.
    """
    hole_records = sorted(
        (records[tag] for tag in holes(records)), key=lambda r: (r.radius, r.tag)
    )

    radius_clusters: list[list[CylinderRecord]] = []
    for rec in hole_records:
        if radius_clusters and np.isclose(
            rec.radius, radius_clusters[-1][0].radius, rtol=radius_rtol
        ):
            radius_clusters[-1].append(rec)
        else:
            radius_clusters.append([rec])

    groups = []
    for cluster in radius_clusters:
        axis_bins: list[list[CylinderRecord]] = []
        for rec in cluster:
            for b in axis_bins:
                if _same_axis_dir(rec, b[0], axis_tol_deg):
                    b.append(rec)
                    break
            else:
                axis_bins.append([rec])
        for b in axis_bins:
            groups.append(
                HoleGroup(
                    face_tags=sorted(r.tag for r in b),
                    radius=float(np.mean([r.radius for r in b])),
                    axis_dir=b[0].axis_dir,
                )
            )
    groups.sort(key=lambda g: (g.radius, g.face_tags))
    return groups


def coaxial_groups(records: dict[int, CylinderRecord]) -> list[list[int]]:
    """Partition all cylindrical faces into coaxial sets (singletons included)."""
    remaining = sorted(records)
    result: list[list[int]] = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        for tag in remaining[:]:
            if are_coaxial(records[seed], records[tag]):
                group.append(tag)
                remaining.remove(tag)
        result.append(sorted(group))
    return result
