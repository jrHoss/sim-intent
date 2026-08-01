"""Pure deterministic tetrahedral extraction and quality computation."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from ir.schema import MeshSettings
from mesh.artifacts import (
    ASPECT_RATIO_DEFINITION,
    MEAN_RATIO_DEFINITION,
    MESH_ARTIFACT_SCHEMA_VERSION,
    MESH_QUALITY_ARTIFACT_TYPE,
    MESH_TOPOLOGY_ARTIFACT_TYPE,
    QUALITY_POLICY_ID,
    QUALITY_POLICY_VERSION,
    canonical_topology_bytes,
)
from mesh.profile import (
    GMSH_TET_V1,
    PHYSICAL_TOLERANCE_UNITS,
    PHYSICAL_TOLERANCE_UNITS_FIELD_NAME,
    PHYSICAL_TOLERANCE_VALUE_FIELD_NAME,
    PROVENANCE_OBJECT_FIELD_NAME,
    PROVENANCE_PRODUCER_FIELD_NAME,
    build_provenance_producer,
)

# A tetrahedron is degenerate when its determinant, after scaling all three
# edge vectors by their largest absolute component, is at or below this fixed
# dimensionless threshold. The global target mesh size never participates.
DEGENERACY_RELATIVE_TOLERANCE = 1e-12
NUMERIC_RANGE_FAILURE = "mesh_numeric_range_failure"


class MeshGenerationError(ValueError):
    """Stable sanitized mesh-domain failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def mesh_settings_hash(settings: MeshSettings) -> str:
    canonical = json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _finite_result(value: float) -> float:
    if not math.isfinite(value):
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE)
    return 0.0 if value == 0.0 else value


def _number(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeshGenerationError(code)
    try:
        result = float(value)
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    if not math.isfinite(result):
        raise MeshGenerationError(code)
    return 0.0 if result == 0.0 else result


def _positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MeshGenerationError(code)
    return value


def _object(value: Any, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise MeshGenerationError(code)
    return value


def _oriented_canonical(values: tuple[int, int, int, int]) -> tuple[int, ...]:
    even: list[tuple[int, ...]] = []
    for indexes in itertools.permutations(range(4)):
        inversions = sum(
            indexes[left] > indexes[right]
            for left in range(4)
            for right in range(left + 1, 4)
        )
        if inversions % 2 == 0:
            even.append(tuple(values[index] for index in indexes))
    return min(even)


def _subtract(left: float, right: float) -> float:
    try:
        return _finite_result(left - right)
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc


def _vector_subtract(
    left: Sequence[float], right: Sequence[float]
) -> tuple[float, float, float]:
    return tuple(_subtract(left[index], right[index]) for index in range(3))  # type: ignore[return-value]


def _determinant(
    first: Sequence[float],
    second: Sequence[float],
    third: Sequence[float],
) -> float:
    try:
        cross_0 = _finite_result(
            second[1] * third[2] - second[2] * third[1]
        )
        cross_1 = _finite_result(
            second[0] * third[2] - second[2] * third[0]
        )
        cross_2 = _finite_result(
            second[0] * third[1] - second[1] * third[0]
        )
        return _finite_result(
            first[0] * cross_0
            - first[1] * cross_1
            + first[2] * cross_2
        )
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc


def _cube_rescale(
    coefficient: float, scale: float, *, allow_underflow_zero: bool = False
) -> float:
    """Compute coefficient * scale**3 without intermediate overflow/underflow."""

    try:
        mantissa, exponent = math.frexp(scale)
        scaled_coefficient = _finite_result(
            coefficient * mantissa * mantissa * mantissa
        )
        result = math.ldexp(scaled_coefficient, exponent * 3)
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    if not math.isfinite(result) or result < 0.0:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE)
    if result == 0.0 and not allow_underflow_zero:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE)
    return 0.0 if result == 0.0 else result


def physical_signed_volume_degeneracy_threshold(local_scale_mm: float) -> float:
    """Return one element's physical signed-volume degeneracy threshold."""

    return _cube_rescale(
        DEGENERACY_RELATIVE_TOLERANCE / 6.0,
        local_scale_mm,
        allow_underflow_zero=True,
    )


def physical_degeneracy_tolerance_summary(
    element_thresholds_mm3: Sequence[float],
) -> float:
    """Return the finite maximum threshold, or +0.0 for an empty set."""

    if not element_thresholds_mm3:
        return 0.0
    if not all(
        math.isfinite(value) and value >= 0.0
        for value in element_thresholds_mm3
    ):
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE)
    return _finite_result(max(element_thresholds_mm3))


def _distance_squared(
    left: Sequence[float], right: Sequence[float]
) -> float:
    delta = _vector_subtract(left, right)
    try:
        squares = [_finite_result(value * value) for value in delta]
        return _finite_result(math.fsum(squares))
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc


def _triangle_area(
    a: Sequence[float], b: Sequence[float], c: Sequence[float]
) -> float:
    ab = _vector_subtract(b, a)
    ac = _vector_subtract(c, a)
    try:
        cross = (
            _finite_result(ab[1] * ac[2] - ab[2] * ac[1]),
            _finite_result(ab[2] * ac[0] - ab[0] * ac[2]),
            _finite_result(ab[0] * ac[1] - ab[1] * ac[0]),
        )
        squared = _finite_result(math.fsum(
            _finite_result(value * value) for value in cross
        ))
        return _finite_result(0.5 * math.sqrt(squared))
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc


def _tetrahedron_geometry(
    coords: Mapping[int, tuple[float, float, float]],
    node_ids: Sequence[int],
) -> tuple[
    float,
    float,
    dict[int, tuple[float, float, float]],
    float,
]:
    """Return physical volume, normalized volume/points, and local cutoff."""

    a, b, c, d = (coords[node_id] for node_id in node_ids)
    edges = (
        _vector_subtract(b, a),
        _vector_subtract(c, a),
        _vector_subtract(d, a),
    )
    local_scale = max(abs(component) for edge in edges for component in edge)
    if not math.isfinite(local_scale):
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE)
    if local_scale == 0.0:
        raise MeshGenerationError("degenerate_elements")
    try:
        normalized_edges = tuple(
            tuple(_finite_result(component / local_scale) for component in edge)
            for edge in edges
        )
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    determinant = _determinant(*normalized_edges)
    if abs(determinant) <= DEGENERACY_RELATIVE_TOLERANCE:
        raise MeshGenerationError("degenerate_elements")
    if determinant < 0.0:
        raise MeshGenerationError("inverted_elements")
    normalized_volume = _finite_result(determinant / 6.0)
    physical_volume = _cube_rescale(normalized_volume, local_scale)
    physical_cutoff = physical_signed_volume_degeneracy_threshold(
        local_scale
    )
    scaled_points = {
        node_ids[0]: (0.0, 0.0, 0.0),
        node_ids[1]: normalized_edges[0],
        node_ids[2]: normalized_edges[1],
        node_ids[3]: normalized_edges[2],
    }
    return (
        physical_volume,
        normalized_volume,
        scaled_points,
        physical_cutoff,
    )


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values or not all(math.isfinite(value) for value in values):
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE)
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    try:
        position = _finite_result((len(ordered) - 1) * fraction)
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = _finite_result(position - lower)
        result = (
            ordered[lower] * (1.0 - weight)
            + ordered[upper] * weight
        )
        return _finite_result(result)
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc


def _quality_metrics(
    scaled_coords: Mapping[int, tuple[float, float, float]],
    ids: Sequence[int],
    normalized_volume: float,
) -> tuple[float, float]:
    edges = [
        _distance_squared(scaled_coords[left], scaled_coords[right])
        for left, right in itertools.combinations(ids, 2)
    ]
    try:
        edge_sum = _finite_result(math.fsum(edges))
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    if edge_sum <= 0.0:
        raise MeshGenerationError("degenerate_elements")
    try:
        mean_ratio = _finite_result(
            12.0 * (3.0 * normalized_volume) ** (2.0 / 3.0)
            / edge_sum
        )
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    mean_ratio = min(1.0, max(0.0, mean_ratio))
    areas = [
        _triangle_area(*(scaled_coords[item] for item in face))
        for face in itertools.combinations(ids, 3)
    ]
    if min(areas) <= 0.0:
        raise MeshGenerationError("degenerate_elements")
    try:
        minimum_altitude = min(
            _finite_result(3.0 * normalized_volume / area)
            for area in areas
        )
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    if minimum_altitude <= 0.0:
        raise MeshGenerationError("degenerate_elements")
    try:
        aspect = _finite_result(
            math.sqrt(max(edges))
            / minimum_altitude
            * math.sqrt(2.0 / 3.0)
        )
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError(NUMERIC_RANGE_FAILURE) from exc
    return mean_ratio, max(1.0, aspect)


def _setup_provenance_timestamp(value: datetime) -> str:
    """Canonicalize the immutable SetupRevision creation instant to UTC."""

    if not isinstance(value, datetime):
        raise MeshGenerationError("invalid_setup_provenance")
    try:
        aware = value if value.tzinfo is not None else value.replace(
            tzinfo=timezone.utc
        )
        normalized = aware.astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")
    except (OverflowError, ArithmeticError) as exc:
        raise MeshGenerationError("invalid_setup_provenance") from exc


def build_mesh_artifacts(
    raw: Mapping[str, Any],
    *,
    mesh_revision_id: str,
    project_id: str,
    model_id: str,
    model_version_id: str,
    setup_id: str,
    setup_revision_id: str,
    setup_revision_created_at: datetime,
    source_model_sha256: str,
    settings: MeshSettings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate worker output and build the existing R5.1 artifact pair."""

    document = _object(
        raw,
        {
            "gmsh_version",
            "nodes",
            "profile_id",
            "profile_version",
            "target_size_mm",
            "tetrahedra",
        },
        "malformed_worker_response",
    )
    if (
        document["gmsh_version"] != GMSH_TET_V1.gmsh_version
        or document["profile_id"] != GMSH_TET_V1.profile_id
        or document["profile_version"] != GMSH_TET_V1.profile_version
    ):
        raise MeshGenerationError("gmsh_version_unsupported")
    target = _number(document["target_size_mm"], "malformed_worker_response")
    if target != float(settings.global_element_size_mm):
        raise MeshGenerationError("settings_binding_mismatch")
    raw_nodes, raw_tets = document["nodes"], document["tetrahedra"]
    if not isinstance(raw_nodes, list) or not isinstance(raw_tets, list):
        raise MeshGenerationError("malformed_worker_response")
    if not raw_nodes or not raw_tets:
        raise MeshGenerationError("empty_mesh")

    parsed_nodes: list[tuple[int, tuple[float, float, float]]] = []
    seen_tags: set[int] = set()
    coordinate_owners: dict[tuple[float, float, float], int] = {}
    for item in raw_nodes:
        node = _object(item, {"coordinates", "tag"}, "malformed_worker_response")
        tag = _positive_int(node["tag"], "malformed_worker_response")
        values = node["coordinates"]
        if not isinstance(values, list) or len(values) != 3 or tag in seen_tags:
            raise MeshGenerationError("malformed_worker_response")
        coordinates = tuple(
            _number(value, "malformed_worker_response") for value in values
        )
        previous_tag = coordinate_owners.get(coordinates)
        if previous_tag is not None and previous_tag != tag:
            raise MeshGenerationError("duplicate_node_coordinates")
        coordinate_owners[coordinates] = tag
        seen_tags.add(tag)
        parsed_nodes.append((tag, coordinates))  # type: ignore[arg-type]
    parsed_nodes.sort(key=lambda item: item[1])
    tag_to_id = {tag: index for index, (tag, _) in enumerate(parsed_nodes, 1)}
    coords = {tag_to_id[tag]: xyz for tag, xyz in parsed_nodes}
    nodes = [
        {"node_id": node_id, "coordinates": list(coords[node_id])}
        for node_id in sorted(coords)
    ]

    connectivities: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for item in raw_tets:
        if not isinstance(item, list) or len(item) != 4:
            raise MeshGenerationError("unsupported_element_type")
        tags = tuple(
            _positive_int(value, "malformed_worker_response") for value in item
        )
        if len(set(tags)) != 4 or any(tag not in tag_to_id for tag in tags):
            raise MeshGenerationError("malformed_worker_response")
        oriented = _oriented_canonical(tuple(tag_to_id[tag] for tag in tags))
        unordered = tuple(sorted(oriented))
        if unordered in seen:
            raise MeshGenerationError("invalid_exterior_topology")
        seen.add(unordered)
        connectivities.append(oriented)  # type: ignore[arg-type]
    connectivities.sort()

    volumes: list[float] = []
    local_cutoffs: list[float] = []
    mean_ratios: list[float] = []
    aspect_ratios: list[float] = []
    tetrahedra: list[dict[str, Any]] = []
    incidence: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for element_id, connectivity in enumerate(connectivities, 1):
        (
            volume,
            normalized_volume,
            scaled_coords,
            physical_cutoff,
        ) = _tetrahedron_geometry(coords, connectivity)
        mean_ratio, aspect = _quality_metrics(
            scaled_coords, connectivity, normalized_volume
        )
        volumes.append(volume)
        local_cutoffs.append(physical_cutoff)
        mean_ratios.append(mean_ratio)
        aspect_ratios.append(aspect)
        tetrahedra.append(
            {"element_id": element_id, "node_ids": list(connectivity)}
        )
        for face in itertools.combinations(connectivity, 3):
            incidence[tuple(sorted(face))].append(element_id)
    if any(len(owners) > 2 for owners in incidence.values()):
        raise MeshGenerationError("invalid_exterior_topology")
    exterior = sorted(
        (face, owners[0])
        for face, owners in incidence.items()
        if len(owners) == 1
    )
    if not exterior:
        raise MeshGenerationError("invalid_exterior_topology")
    triangles = [
        {
            "triangle_id": index,
            "node_ids": list(face),
            "owner_tetrahedron_id": owner,
        }
        for index, (face, owner) in enumerate(exterior, 1)
    ]

    binding = {
        "mesh_revision_id": mesh_revision_id,
        "project_id": project_id,
        "model_id": model_id,
        "model_version_id": model_version_id,
        "setup_id": setup_id,
        "setup_revision_id": setup_revision_id,
    }
    settings_digest = mesh_settings_hash(settings)
    provenance = {
        PROVENANCE_PRODUCER_FIELD_NAME: build_provenance_producer(
            GMSH_TET_V1.resolved_identity
        ),
        "created_at": _setup_provenance_timestamp(
            setup_revision_created_at
        ),
    }
    topology = {
        "artifact_type": MESH_TOPOLOGY_ARTIFACT_TYPE,
        "schema_version": MESH_ARTIFACT_SCHEMA_VERSION,
        **binding,
        "source_model_sha256": source_model_sha256,
        "mesh_settings_hash": settings_digest,
        "mesher_profile_id": GMSH_TET_V1.profile_id,
        "mesher_profile_version": GMSH_TET_V1.profile_version,
        "length_unit": "mm",
        "nodes": nodes,
        "tetrahedra": tetrahedra,
        "exterior_triangles": triangles,
        PROVENANCE_OBJECT_FIELD_NAME: provenance,
    }
    topology_digest = hashlib.sha256(
        canonical_topology_bytes(topology)
    ).hexdigest()
    quality = {
        "artifact_type": MESH_QUALITY_ARTIFACT_TYPE,
        "schema_version": MESH_ARTIFACT_SCHEMA_VERSION,
        **binding,
        "source_model_sha256": source_model_sha256,
        "mesh_settings_hash": settings_digest,
        "mesher_profile_id": GMSH_TET_V1.profile_id,
        "mesher_profile_version": GMSH_TET_V1.profile_version,
        "topology_artifact_sha256": topology_digest,
        "quality_policy_id": QUALITY_POLICY_ID,
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "element_count": len(tetrahedra),
        "status": "accepted",
        "rejection_codes": [],
        "warnings": [],
        "signed_volume": {
            "metric": "signed_tetrahedral_volume",
            "minimum": min(volumes),
            "non_positive_count": 0,
            # The v1 schema stores a physical summary. This is the maximum
            # element-local physical cutoff implied by the fixed dimensionless
            # determinant threshold, never a target-size-derived tolerance.
            PHYSICAL_TOLERANCE_VALUE_FIELD_NAME: physical_degeneracy_tolerance_summary(
                local_cutoffs
            ),
            PHYSICAL_TOLERANCE_UNITS_FIELD_NAME: PHYSICAL_TOLERANCE_UNITS,
            "definition_version": 1,
        },
        "mean_ratio": {
            "metric": "mean_ratio_tetrahedral_quality",
            "definition": MEAN_RATIO_DEFINITION,
            "minimum": min(mean_ratios),
            "p01": _percentile(mean_ratios, 0.01),
            "p05": _percentile(mean_ratios, 0.05),
            "p50": _percentile(mean_ratios, 0.50),
        },
        "aspect_ratio": {
            "metric": "normalized_longest_edge_minimum_altitude",
            "definition": ASPECT_RATIO_DEFINITION,
            "p50": _percentile(aspect_ratios, 0.50),
            "p95": _percentile(aspect_ratios, 0.95),
            "p99": _percentile(aspect_ratios, 0.99),
            "maximum": max(aspect_ratios),
        },
        PROVENANCE_OBJECT_FIELD_NAME: provenance,
    }
    return topology, quality
