"""Authoritative analytic-surface evidence for durable geometry identity.

The extractor runs while the imported OCC model is live.  It derives analytic
parameters from surface values and principal-curvature directions, never from
bounding-box spans, and keeps every descriptor bound to its source-local face
tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gmsh
import numpy as np

from geom.cylinders import analyze_cylinder_face


_ANALYTIC_TYPES = frozenset({"plane", "cylinder", "cone", "sphere", "torus"})
_SAMPLE_U = 16
_SAMPLE_V = 11


@dataclass(frozen=True)
class AnalyticSurfaceEvidence:
    tag: int
    surface_type: str
    descriptors: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "surface_type": self.surface_type,
            "descriptors": self.descriptors,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AnalyticSurfaceEvidence":
        if set(value) != {"tag", "surface_type", "descriptors"}:
            raise ValueError("invalid analytic-surface evidence")
        tag = value["tag"]
        surface_type = value["surface_type"]
        descriptors = value["descriptors"]
        if (
            isinstance(tag, bool)
            or not isinstance(tag, int)
            or tag <= 0
            or not isinstance(surface_type, str)
            or surface_type not in _ANALYTIC_TYPES
            or not isinstance(descriptors, dict)
        ):
            raise ValueError("invalid analytic-surface evidence")
        return cls(tag=tag, surface_type=surface_type, descriptors=descriptors)


def _canonical_sign(vector: np.ndarray) -> np.ndarray:
    dominant = int(np.argmax(np.abs(vector)))
    return -vector if vector[dominant] < 0 else vector


def _unit(vector: np.ndarray, field_name: str) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1.0e-12:
        raise ValueError(f"{field_name} is unavailable")
    result = vector / norm
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{field_name} is unavailable")
    return result


def _parameter_grid(tag: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lower, upper = gmsh.model.getParametrizationBounds(2, tag)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    if (
        lower.shape != (2,)
        or upper.shape != (2,)
        or not np.all(np.isfinite(lower))
        or not np.all(np.isfinite(upper))
        or np.any(upper <= lower)
    ):
        raise ValueError("analytic parametrization is unavailable")
    us = np.linspace(lower[0], upper[0], _SAMPLE_U, endpoint=False)
    us += 0.5 * (upper[0] - lower[0]) / _SAMPLE_U
    vs = np.linspace(lower[1], upper[1], _SAMPLE_V, endpoint=False)
    vs += 0.5 * (upper[1] - lower[1]) / _SAMPLE_V
    uu, vv = np.meshgrid(us, vs)
    params = np.column_stack((uu.ravel(), vv.ravel())).ravel()
    points = np.asarray(gmsh.model.getValue(2, tag, params), dtype=float).reshape(
        -1, 3
    )
    if not np.all(np.isfinite(points)):
        raise ValueError("analytic surface points are unavailable")
    return us, vs, points.reshape(_SAMPLE_V, _SAMPLE_U, 3)


def _principal_data(
    tag: int, us: np.ndarray, vs: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    uu, vv = np.meshgrid(us, vs)
    params = np.column_stack((uu.ravel(), vv.ravel())).ravel()
    maximum, minimum, maximum_direction, minimum_direction = (
        gmsh.model.getPrincipalCurvatures(tag, params)
    )
    arrays = (
        np.asarray(maximum, dtype=float),
        np.asarray(minimum, dtype=float),
        np.asarray(maximum_direction, dtype=float).reshape(-1, 3),
        np.asarray(minimum_direction, dtype=float).reshape(-1, 3),
    )
    if not all(np.all(np.isfinite(array)) for array in arrays):
        raise ValueError("analytic curvature evidence is unavailable")
    return arrays


def _axis_orthogonal_to(directions: np.ndarray) -> np.ndarray:
    covariance = directions.T @ directions
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    axis = _canonical_sign(_unit(eigenvectors[:, int(np.argmin(eigenvalues))], "axis"))
    residual = np.max(np.abs(directions @ axis))
    if residual > 2.0e-5:
        raise ValueError("analytic axis evidence is inconsistent")
    return axis


def _fit_circle_2d(points: np.ndarray) -> tuple[np.ndarray, float]:
    matrix = np.column_stack((2.0 * points, np.ones(len(points))))
    right = np.sum(points * points, axis=1)
    solution, *_ = np.linalg.lstsq(matrix, right, rcond=None)
    center = solution[:2]
    radius_squared = float(solution[2] + center @ center)
    if radius_squared <= 0 or not np.isfinite(radius_squared):
        raise ValueError("analytic circle evidence is unavailable")
    radius = float(np.sqrt(radius_squared))
    residual = np.max(np.abs(np.linalg.norm(points - center, axis=1) - radius))
    if residual > max(1.0e-7, radius * 2.0e-6):
        raise ValueError("analytic circle evidence is inconsistent")
    return center, radius


def _cone_descriptors(tag: int) -> dict[str, Any]:
    us, vs, grid = _parameter_grid(tag)
    maximum, minimum, maximum_direction, minimum_direction = _principal_data(
        tag, us, vs
    )
    maximum_is_flat = np.abs(maximum) <= np.abs(minimum)
    if not np.all(maximum_is_flat) and np.any(maximum_is_flat):
        raise ValueError("cone curvature classification is inconsistent")
    generators = maximum_direction if np.all(maximum_is_flat) else minimum_direction
    circumferential = minimum_direction if np.all(maximum_is_flat) else maximum_direction
    axis = _axis_orthogonal_to(circumferential)
    generator_units = np.asarray([_unit(item, "cone generator") for item in generators])
    projectors = np.asarray(
        [np.eye(3) - np.outer(direction, direction) for direction in generator_units]
    )
    matrix = np.sum(projectors, axis=0)
    right = np.sum(
        np.einsum("nij,nj->ni", projectors, grid.reshape(-1, 3)), axis=0
    )
    apex, *_ = np.linalg.lstsq(matrix, right, rcond=None)
    line_residual = np.max(
        np.linalg.norm(
            np.einsum(
                "nij,nj->ni",
                projectors,
                grid.reshape(-1, 3) - apex,
            ),
            axis=1,
        )
    )
    scale = max(1.0, float(np.max(np.linalg.norm(grid.reshape(-1, 3) - apex, axis=1))))
    if line_residual > scale * 2.0e-5:
        raise ValueError("cone apex evidence is inconsistent")
    if float(np.dot(np.mean(grid.reshape(-1, 3), axis=0) - apex, axis)) < 0:
        axis = -axis
    angles = np.arccos(
        np.clip(np.abs(generator_units @ axis), -1.0, 1.0)
    )
    semi_angle = float(np.median(angles))
    if (
        not np.isfinite(semi_angle)
        or semi_angle <= 0
        or semi_angle >= np.pi / 2.0
        or np.max(np.abs(angles - semi_angle)) > 2.0e-5
    ):
        raise ValueError("cone angle evidence is inconsistent")
    return {
        "axis": [float(value) for value in axis],
        "apex": [float(value) for value in apex],
        "semi_angle": semi_angle,
    }


def _sphere_descriptors(tag: int) -> dict[str, Any]:
    _us, _vs, grid = _parameter_grid(tag)
    points = grid.reshape(-1, 3)
    matrix = np.column_stack((2.0 * points, np.ones(len(points))))
    right = np.sum(points * points, axis=1)
    solution, *_ = np.linalg.lstsq(matrix, right, rcond=None)
    center = solution[:3]
    radius_squared = float(solution[3] + center @ center)
    if radius_squared <= 0 or not np.isfinite(radius_squared):
        raise ValueError("sphere radius evidence is unavailable")
    radius = float(np.sqrt(radius_squared))
    residual = np.max(np.abs(np.linalg.norm(points - center, axis=1) - radius))
    if residual > max(1.0e-7, radius * 2.0e-6):
        raise ValueError("sphere evidence is inconsistent")
    return {
        "center": [float(value) for value in center],
        "radius": radius,
    }


def _torus_descriptors(tag: int) -> dict[str, Any]:
    us, vs, grid = _parameter_grid(tag)
    maximum, minimum, maximum_direction, minimum_direction = _principal_data(
        tag, us, vs
    )
    curvature_candidates = []
    for curvature, direction, other_direction in (
        (maximum, maximum_direction, minimum_direction),
        (minimum, minimum_direction, maximum_direction),
    ):
        absolute = np.abs(curvature)
        median = float(np.median(absolute))
        spread = float(np.max(np.abs(absolute - median)))
        curvature_candidates.append((spread / max(median, 1.0e-15), median, direction, other_direction))
    _spread, minor_curvature, _minor_direction, azimuth_direction = min(
        curvature_candidates, key=lambda item: item[0]
    )
    if minor_curvature <= 0:
        raise ValueError("torus minor-radius evidence is unavailable")
    minor_radius = 1.0 / minor_curvature
    axis = _axis_orthogonal_to(azimuth_direction)
    basis_candidate = np.cross(axis, np.array([1.0, 0.0, 0.0]))
    if np.linalg.norm(basis_candidate) <= 1.0e-8:
        basis_candidate = np.cross(axis, np.array([0.0, 1.0, 0.0]))
    basis_first = _unit(basis_candidate, "torus basis")
    basis_second = np.cross(axis, basis_first)
    row_centers = []
    row_radii = []
    row_heights = []
    for row in grid:
        projected = np.column_stack((row @ basis_first, row @ basis_second))
        center_2d, radius = _fit_circle_2d(projected)
        row_centers.append(center_2d)
        row_radii.append(radius)
        row_heights.append(float(np.mean(row @ axis)))
    transverse_center = np.mean(np.asarray(row_centers), axis=0)
    if np.max(np.linalg.norm(np.asarray(row_centers) - transverse_center, axis=1)) > max(
        1.0e-7, minor_radius * 2.0e-5
    ):
        raise ValueError("torus center evidence is inconsistent")
    radii = np.asarray(row_radii)
    heights = np.asarray(row_heights)
    matrix = np.column_stack((2.0 * radii, 2.0 * heights, np.ones(len(radii))))
    right = radii * radii + heights * heights
    solution, *_ = np.linalg.lstsq(matrix, right, rcond=None)
    major_radius, axial_center, constant = (float(value) for value in solution)
    if (
        major_radius <= minor_radius
        or abs(
            constant
            + (major_radius * major_radius + axial_center * axial_center - minor_radius * minor_radius)
        )
        > max(1.0e-6, major_radius * major_radius * 5.0e-5)
    ):
        raise ValueError("torus radius evidence is inconsistent")
    center = (
        transverse_center[0] * basis_first
        + transverse_center[1] * basis_second
        + axial_center * axis
    )
    radial = np.sqrt(
        (grid.reshape(-1, 3) @ basis_first - transverse_center[0]) ** 2
        + (grid.reshape(-1, 3) @ basis_second - transverse_center[1]) ** 2
    )
    axial = grid.reshape(-1, 3) @ axis - axial_center
    residual = np.max(
        np.abs(np.sqrt((radial - major_radius) ** 2 + axial**2) - minor_radius)
    )
    if residual > max(1.0e-7, minor_radius * 2.0e-5):
        raise ValueError("torus evidence is inconsistent")
    return {
        "center": [float(value) for value in center],
        "axis": [float(value) for value in axis],
        "major_radius": major_radius,
        "minor_radius": minor_radius,
    }


def analyze_identity_surfaces(
    path: str | Path,
) -> dict[int, AnalyticSurfaceEvidence]:
    """Analyze every R4a analytic face in one isolated OCC session."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if gmsh.isInitialized():
        raise RuntimeError("analyze_identity_surfaces requires exclusive use of gmsh")
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(f"identity_{source.stem}")
        gmsh.model.occ.importShapes(str(source))
        gmsh.model.occ.synchronize()
        result: dict[int, AnalyticSurfaceEvidence] = {}
        for dim, tag in gmsh.model.getEntities(2):
            surface_type = gmsh.model.getType(dim, tag).strip().lower()
            if surface_type not in _ANALYTIC_TYPES:
                continue
            if surface_type == "plane":
                descriptors: dict[str, Any] = {}
            elif surface_type == "cylinder":
                cylinder = analyze_cylinder_face(tag)
                descriptors = {
                    "axis": cylinder.axis_dir,
                    "axis_point": cylinder.axis_point,
                    "radius": cylinder.radius,
                    "length": cylinder.length,
                    "angular_extent": cylinder.angular_extent,
                    "classification": cylinder.classification,
                    "full_circle": cylinder.full_circle,
                }
            elif surface_type == "cone":
                descriptors = _cone_descriptors(tag)
            elif surface_type == "sphere":
                descriptors = _sphere_descriptors(tag)
            else:
                descriptors = _torus_descriptors(tag)
            result[tag] = AnalyticSurfaceEvidence(
                tag=tag,
                surface_type=surface_type,
                descriptors=descriptors,
            )
        return result
    finally:
        gmsh.finalize()
