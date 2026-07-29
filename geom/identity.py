"""Deterministic, versioned face identity within one model version.

The core deliberately separates three numerical concepts:

* semantic tolerances answer engineering comparison questions;
* canonical quanta turn validated numbers into platform-stable integers;
* ambiguity tolerances conservatively decide whether evidence can distinguish
  two candidates.

Canonical identity never contains raw floating-point text, parser order,
source-local face references, edge identifiers, or provenance such as a source
file name.  Source references remain in the artifact solely as local evidence.
The topology refinement is a deterministic Weisfeiler-Lehman-style coloring;
faces that remain indistinguishable share a collision-group identity instead
of receiving parser-order tie-breakers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Mapping, Sequence

from geom.cylinders import CylinderRecord
from geom.inventory import FaceInventory
from geom.labels import adjacency_graph

GEOMETRY_IDENTITY_SCHEMA_VERSION = 1
HASH_DOMAIN = "sim-intent.geometry-identity/v1"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ANALYTIC_SURFACES = frozenset({"plane", "cylinder", "cone", "sphere", "torus"})
_FALLBACK_SURFACES = frozenset({"bspline_surface", "bezier_surface"})
SUPPORTED_SURFACES = _ANALYTIC_SURFACES | _FALLBACK_SURFACES


class GeometryIdentityError(ValueError):
    """A stable, sanitized geometry-identity validation failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class TolerancePolicy:
    """Millimetre-based R4a comparison and canonicalization policy.

    Each ambiguity tolerance must be at least its semantic counterpart so
    semantically equivalent evidence can never receive false stable uniqueness.
    """

    policy_id: str = "sim-intent-mm-geometry/v1"
    semantic_linear_mm: float = 1.0e-6
    semantic_area_mm2: float = 1.0e-6
    semantic_angle_rad: float = 1.0e-8
    ambiguity_linear_mm: float = 2.0e-6
    ambiguity_area_mm2: float = 2.0e-6
    ambiguity_angle_rad: float = 2.0e-8
    position_quantum_mm: float = 1.0e-6
    length_quantum_mm: float = 1.0e-6
    area_quantum_mm2: float = 1.0e-6
    angle_quantum_rad: float = 1.0e-8
    direction_quantum: float = 1.0e-9
    scalar_quantum: float = 1.0e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "semantic_tolerances": {
                "linear_mm": self.semantic_linear_mm,
                "area_mm2": self.semantic_area_mm2,
                "angle_rad": self.semantic_angle_rad,
            },
            "ambiguity_tolerances": {
                "linear_mm": self.ambiguity_linear_mm,
                "area_mm2": self.ambiguity_area_mm2,
                "angle_rad": self.ambiguity_angle_rad,
            },
            "canonical_quanta": {
                "position_mm": self.position_quantum_mm,
                "length_mm": self.length_quantum_mm,
                "area_mm2": self.area_quantum_mm2,
                "angle_rad": self.angle_quantum_rad,
                "direction": self.direction_quantum,
                "dimensionless_scalar": self.scalar_quantum,
            },
        }


DEFAULT_TOLERANCE_POLICY = TolerancePolicy()


@dataclass(frozen=True)
class GeometryFaceInput:
    """Validated input vocabulary for one source-local CAD face."""

    source_ref: int | str
    surface_type: str
    area: float
    centroid: Sequence[float]
    boundary_loop_count: int
    adjacent_refs: Sequence[int | str] = ()
    normal: Sequence[float] | None = None
    descriptors: Mapping[str, Any] = field(default_factory=dict)
    bbox_min: Sequence[float] | None = None
    bbox_max: Sequence[float] | None = None


@dataclass(frozen=True)
class FaceIdentity:
    source_ref: int | str
    surface_type: str
    canonical_geometry: Mapping[str, Any]
    topology: Mapping[str, Any]
    local_semantic_signature: str
    connected_topology_signature: str
    identity_candidate: str
    stable_identity: str | None
    collision_group_id: str | None
    ambiguous: bool
    identity_quality: str
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "surface_type": self.surface_type,
            "canonical_geometry": dict(self.canonical_geometry),
            "topology": dict(self.topology),
            "local_semantic_signature": self.local_semantic_signature,
            "connected_topology_signature": self.connected_topology_signature,
            "identity_candidate": self.identity_candidate,
            "stable_identity": self.stable_identity,
            "collision_group_id": self.collision_group_id,
            "ambiguous": self.ambiguous,
            "identity_quality": self.identity_quality,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class GeometryIdentityArtifact:
    model_version_id: str
    source_sha256: str
    faces: tuple[FaceIdentity, ...]
    collision_groups: tuple[Mapping[str, Any], ...]
    tolerance_policy: TolerancePolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "geometry_identity",
            "schema_version": GEOMETRY_IDENTITY_SCHEMA_VERSION,
            "hash_domain": HASH_DOMAIN,
            "model_binding": {
                "model_version_id": self.model_version_id,
                "source_sha256": self.source_sha256,
            },
            "tolerance_policy": self.tolerance_policy.to_dict(),
            "faces": [face.to_dict() for face in self.faces],
            "collision_groups": [dict(group) for group in self.collision_groups],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def artifact_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize already-canonical data with one byte-stable JSON profile."""

    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def stable_hash(value: Any, *, domain: str) -> str:
    payload = domain.encode("ascii") + b"\0" + canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def semantically_equivalent(
    left: float,
    right: float,
    *,
    quantity: str = "linear",
    policy: TolerancePolicy = DEFAULT_TOLERANCE_POLICY,
    ambiguity: bool = False,
) -> bool:
    """Compare finite values without using canonical hash buckets."""

    a = _finite_number(left, "comparison value")
    b = _finite_number(right, "comparison value")
    tolerances = {
        ("linear", False): policy.semantic_linear_mm,
        ("area", False): policy.semantic_area_mm2,
        ("angle", False): policy.semantic_angle_rad,
        ("linear", True): policy.ambiguity_linear_mm,
        ("area", True): policy.ambiguity_area_mm2,
        ("angle", True): policy.ambiguity_angle_rad,
    }
    try:
        tolerance = tolerances[(quantity, ambiguity)]
    except KeyError as exc:
        raise GeometryIdentityError(
            "geometry.tolerance_quantity_invalid",
            "quantity must be linear, area, or angle",
        ) from exc
    return abs(a - b) <= tolerance


def build_geometry_identity(
    *,
    model_version_id: str,
    source_sha256: str,
    faces: Sequence[GeometryFaceInput],
    tolerance_policy: TolerancePolicy = DEFAULT_TOLERANCE_POLICY,
) -> GeometryIdentityArtifact:
    """Build a deterministic identity artifact for one single-solid version."""

    _validate_binding(model_version_id, source_sha256)
    if not faces:
        raise GeometryIdentityError(
            "geometry.faces_missing", "at least one face is required"
        )
    _validate_policy(tolerance_policy)

    by_ref: dict[tuple[str, str], GeometryFaceInput] = {}
    original_by_key: dict[tuple[str, str], int | str] = {}
    for face in faces:
        key = _source_key(face.source_ref)
        if key in by_ref:
            raise GeometryIdentityError(
                "geometry.source_ref_duplicate",
                "source-local face references must be unique",
            )
        by_ref[key] = face
        original_by_key[key] = face.source_ref

    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for key, face in by_ref.items():
        if (
            not isinstance(face.adjacent_refs, Sequence)
            or isinstance(face.adjacent_refs, (str, bytes))
        ):
            raise GeometryIdentityError(
                "geometry.adjacency_invalid",
                "adjacent_refs must be a sequence of source-local references",
            )
        neighbor_keys: list[tuple[str, str]] = []
        for neighbor in face.adjacent_refs:
            try:
                neighbor_key = _source_key(neighbor)
            except GeometryIdentityError as exc:
                raise GeometryIdentityError(
                    "geometry.adjacency_invalid",
                    "adjacent_refs must contain only source-local references",
                ) from exc
            if neighbor_key not in by_ref:
                raise GeometryIdentityError(
                    "geometry.adjacency_reference_invalid",
                    "adjacency references must identify an existing face",
                )
            neighbor_keys.append(neighbor_key)
        if len(neighbor_keys) != len(set(neighbor_keys)):
            raise GeometryIdentityError(
                "geometry.adjacency_duplicate",
                "a face adjacency list must not contain duplicates",
            )
        if key in neighbor_keys:
            raise GeometryIdentityError(
                "geometry.adjacency_self_loop",
                "a face cannot be adjacent to itself",
            )
        adjacency[key] = set(neighbor_keys)
    for key in sorted(adjacency):
        for neighbor in sorted(adjacency[key]):
            if key not in adjacency[neighbor]:
                raise GeometryIdentityError(
                    "geometry.adjacency_asymmetric",
                    "face adjacency must be symmetric",
                )
    components = _components(adjacency)
    if len(components) != 1:
        raise GeometryIdentityError(
            "geometry.topology_disconnected",
            "a supported single-solid face graph must be connected",
        )

    canonical_geometry: dict[tuple[str, str], dict[str, Any]] = {}
    surface_types: dict[tuple[str, str], str] = {}
    quality: dict[tuple[str, str], str] = {}
    repeated_keys: dict[tuple[str, str], str | None] = {}
    for key in sorted(by_ref):
        geometry, normalized_type, identity_quality, repeated_key = (
            _canonicalize_face(by_ref[key], tolerance_policy)
        )
        canonical_geometry[key] = geometry
        surface_types[key] = normalized_type
        quality[key] = identity_quality
        repeated_keys[key] = repeated_key

    repeated_members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, repeated_key in repeated_keys.items():
        if repeated_key is not None:
            repeated_members[repeated_key].append(key)

    base_payloads: dict[tuple[str, str], dict[str, Any]] = {}
    colors: dict[tuple[str, str], str] = {}
    for key in sorted(by_ref):
        neighbor_types = sorted(surface_types[n] for n in adjacency[key])
        repeated_key = repeated_keys[key]
        repeated_count = (
            len(repeated_members[repeated_key]) if repeated_key is not None else 0
        )
        base_payload = {
            "canonical_profile": {
                "policy_id": tolerance_policy.policy_id,
                "position_quantum_mm": tolerance_policy.position_quantum_mm,
                "length_quantum_mm": tolerance_policy.length_quantum_mm,
                "area_quantum_mm2": tolerance_policy.area_quantum_mm2,
                "angle_quantum_rad": tolerance_policy.angle_quantum_rad,
                "direction_quantum": tolerance_policy.direction_quantum,
            },
            "geometry": canonical_geometry[key],
            "topology": {
                "boundary_loop_count": by_ref[key].boundary_loop_count,
                "adjacency_degree": len(adjacency[key]),
                "neighbor_surface_type_multiset": _multiset(neighbor_types),
                "repeated_feature_count": repeated_count,
            },
        }
        base_payloads[key] = base_payload
        colors[key] = stable_hash(base_payload, domain=f"{HASH_DOMAIN}/base-face")

    # A fixed face-count refinement is deterministic even when color hashes
    # change at every round; it avoids order-sensitive "first stable" logic.
    for _round in range(len(by_ref)):
        colors = {
            key: stable_hash(
                {
                    "base": base_payloads[key],
                    "neighbor_colors": sorted(colors[n] for n in adjacency[key]),
                },
                domain=f"{HASH_DOMAIN}/topology-round",
            )
            for key in sorted(by_ref)
        }

    component_signature = stable_hash(
        {
            "face_colors": sorted(colors.values()),
            "edges": sorted(
                sorted((colors[left], colors[right]))
                for left in sorted(adjacency)
                for right in sorted(adjacency[left])
                if left < right
            ),
        },
        domain=f"{HASH_DOMAIN}/connected-component",
    )
    candidates = {
        key: stable_hash(
            {"local": colors[key], "connected": component_signature},
            domain=f"{HASH_DOMAIN}/identity-candidate",
        )
        for key in sorted(by_ref)
    }
    members_by_candidate: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, candidate in candidates.items():
        members_by_candidate[candidate].append(key)

    ambiguity_labels = _ambiguity_partition_labels(
        keys=sorted(by_ref),
        canonical_geometry=canonical_geometry,
        base_payloads=base_payloads,
        adjacency=adjacency,
        policy=tolerance_policy,
    )
    members_by_ambiguity: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, label in ambiguity_labels.items():
        members_by_ambiguity[label].append(key)

    face_identities: list[FaceIdentity] = []
    collision_groups: list[dict[str, Any]] = []
    group_ids: dict[tuple[str, str], str] = {}
    for ambiguity_label, members in sorted(members_by_ambiguity.items()):
        bounded = any(quality[key] == "bounded_fallback" for key in members)
        if len(members) > 1 or bounded:
            group_id = stable_hash(
                {
                    "ambiguity_signature": ambiguity_label,
                    "member_identity_candidates": sorted(
                        candidates[key] for key in members
                    ),
                },
                domain=f"{HASH_DOMAIN}/collision-group",
            )
            for key in members:
                group_ids[key] = group_id
            collision_groups.append(
                {
                    "collision_group_id": group_id,
                    "identity_candidates": sorted(
                        f"gic1:{candidates[key]}" for key in members
                    ),
                    "member_source_refs": [
                        original_by_key[key] for key in sorted(members)
                    ],
                    "reason": (
                        "bounded_fallback_requires_confirmation"
                        if len(members) == 1
                        else (
                            "geometrically_and_topologically_indistinguishable"
                            if len({candidates[key] for key in members}) == 1
                            else "within_declared_ambiguity_tolerance"
                        )
                    ),
                }
            )

    for key in sorted(by_ref):
        candidate = candidates[key]
        repeated_key = repeated_keys[key]
        repeated_group_size = (
            len(repeated_members[repeated_key]) if repeated_key is not None else 0
        )
        topology = base_payloads[key]["topology"] | {
            "adjacent_source_refs": [
                original_by_key[n] for n in sorted(adjacency[key])
            ],
            "neighbor_semantic_signatures": sorted(colors[n] for n in adjacency[key]),
        }
        ambiguous = key in group_ids
        face_identities.append(
            FaceIdentity(
                source_ref=original_by_key[key],
                surface_type=surface_types[key],
                canonical_geometry=canonical_geometry[key],
                topology=topology,
                local_semantic_signature=colors[key],
                connected_topology_signature=component_signature,
                identity_candidate=f"gic1:{candidate}",
                stable_identity=None if ambiguous else f"gfi1:{candidate}",
                collision_group_id=group_ids.get(key),
                ambiguous=ambiguous,
                identity_quality=quality[key],
                evidence={
                    "source_local_only": True,
                    "repeated_feature_signature": repeated_key,
                    "repeated_feature_group_size": repeated_group_size,
                },
            )
        )

    face_identities.sort(key=lambda item: _source_key(item.source_ref))
    collision_groups.sort(key=lambda group: group["collision_group_id"])
    return GeometryIdentityArtifact(
        model_version_id=model_version_id,
        source_sha256=source_sha256.lower(),
        faces=tuple(face_identities),
        collision_groups=tuple(collision_groups),
        tolerance_policy=tolerance_policy,
    )


def faces_from_inventory(
    inventory: FaceInventory,
    cylinders: Mapping[int, CylinderRecord] | None = None,
) -> list[GeometryFaceInput]:
    """Adapt the existing STEP inventory authority to the R4a core.

    Cylinder analysis is required because radius and axis must never be
    approximated from a bounding box.
    """

    cylinder_records = cylinders or {}
    graph = adjacency_graph(inventory.faces)
    result: list[GeometryFaceInput] = []
    for face in inventory.faces:
        surface_type = _normalize_surface_type(face.surface_type)
        descriptors: dict[str, Any] = {}
        if surface_type == "cylinder":
            cylinder = cylinder_records.get(face.tag)
            if cylinder is None:
                raise GeometryIdentityError(
                    "geometry.descriptor_missing",
                    "cylinder identity requires analyzed radius and axis descriptors",
                )
            descriptors = {
                "axis": cylinder.axis_dir,
                "axis_point": cylinder.axis_point,
                "radius": cylinder.radius,
                "length": cylinder.length,
                "angular_extent": cylinder.angular_extent,
                "classification": cylinder.classification,
                "full_circle": cylinder.full_circle,
            }
        result.append(
            GeometryFaceInput(
                source_ref=face.tag,
                surface_type=surface_type,
                area=face.area,
                centroid=face.centroid,
                normal=face.normal,
                bbox_min=face.bbox_min,
                bbox_max=face.bbox_max,
                boundary_loop_count=face.boundary_loop_count,
                adjacent_refs=sorted(graph[face.tag]),
                descriptors=descriptors,
            )
        )
    return result


def _validate_binding(model_version_id: str, source_sha256: str) -> None:
    if not isinstance(model_version_id, str) or not model_version_id.strip():
        raise GeometryIdentityError(
            "geometry.model_version_binding_invalid",
            "model_version_id must be a non-empty string",
        )
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise GeometryIdentityError(
            "geometry.source_binding_invalid",
            "source_sha256 must be a 64-character hexadecimal digest",
        )


def _validate_policy(policy: TolerancePolicy) -> None:
    for value in policy.to_dict()["semantic_tolerances"].values():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise GeometryIdentityError(
                "geometry.tolerance_policy_invalid",
                "all tolerance values must be finite and positive",
            )
    for section in ("ambiguity_tolerances", "canonical_quanta"):
        for value in policy.to_dict()[section].values():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise GeometryIdentityError(
                    "geometry.tolerance_policy_invalid",
                    "all tolerance values must be finite and positive",
                )
    if (
        policy.ambiguity_linear_mm < policy.semantic_linear_mm
        or policy.ambiguity_area_mm2 < policy.semantic_area_mm2
        or policy.ambiguity_angle_rad < policy.semantic_angle_rad
    ):
        raise GeometryIdentityError(
            "geometry.tolerance_policy_invalid",
            "ambiguity tolerances must be greater than or equal to semantic tolerances",
        )


def _source_key(value: int | str) -> tuple[str, str]:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GeometryIdentityError(
            "geometry.source_ref_invalid",
            "source-local references must be integers or non-empty strings",
        )
    if isinstance(value, str) and not value:
        raise GeometryIdentityError(
            "geometry.source_ref_invalid",
            "source-local references must be integers or non-empty strings",
        )
    return ("integer", str(value)) if isinstance(value, int) else ("string", value)


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryIdentityError(
            "geometry.descriptor_invalid", f"{field_name} must be numeric"
        )
    result = float(value)
    if not math.isfinite(result):
        raise GeometryIdentityError(
            "geometry.numeric_non_finite", f"{field_name} must be finite"
        )
    return result


def _quantize(value: Any, quantum: float, field_name: str) -> int:
    number = _finite_number(value, field_name)
    return int(
        (Decimal(str(number)) / Decimal(str(quantum))).to_integral_value(
            rounding=ROUND_HALF_EVEN
        )
    )


def _vector(
    value: Any,
    *,
    field_name: str,
    quantum: float,
    unit: bool = False,
    unoriented: bool = False,
) -> list[int]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise GeometryIdentityError(
            "geometry.descriptor_invalid",
            f"{field_name} must contain exactly three numeric components",
        )
    values = [_finite_number(component, field_name) for component in value]
    if unit:
        norm = math.sqrt(math.fsum(component * component for component in values))
        if norm == 0:
            raise GeometryIdentityError(
                "geometry.vector_zero", f"{field_name} cannot be a zero vector"
            )
        values = [component / norm for component in values]
        if unoriented:
            dominant = max(range(3), key=lambda index: abs(values[index]))
            if values[dominant] < 0:
                values = [-component for component in values]
    return [_quantize(component, quantum, field_name) for component in values]


def _unit_values(value: Any, *, field_name: str, unoriented: bool) -> list[float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise GeometryIdentityError(
            "geometry.descriptor_invalid",
            f"{field_name} must contain exactly three numeric components",
        )
    values = [_finite_number(component, field_name) for component in value]
    norm = math.sqrt(math.fsum(component * component for component in values))
    if norm == 0:
        raise GeometryIdentityError(
            "geometry.vector_zero", f"{field_name} cannot be a zero vector"
        )
    values = [component / norm for component in values]
    if unoriented:
        dominant = max(range(3), key=lambda index: abs(values[index]))
        if values[dominant] < 0:
            values = [-component for component in values]
    return values


def _positive(value: Any, field_name: str) -> float:
    number = _finite_number(value, field_name)
    if number <= 0:
        raise GeometryIdentityError(
            "geometry.descriptor_invalid", f"{field_name} must be positive"
        )
    return number


def _reject_unknown_descriptors(
    descriptors: Mapping[str, Any], allowed: set[str]
) -> None:
    if any(not isinstance(name, str) or name not in allowed for name in descriptors):
        raise GeometryIdentityError(
            "geometry.descriptor_unknown",
            "surface descriptors contain a field outside the versioned vocabulary",
        )


def _normalize_surface_type(surface_type: Any) -> str:
    if not isinstance(surface_type, str) or not surface_type.strip():
        raise GeometryIdentityError(
            "geometry.surface_type_invalid", "surface_type must be a non-empty string"
        )
    normalized = surface_type.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "bspline": "bspline_surface",
        "b_spline_surface": "bspline_surface",
        "bezier": "bezier_surface",
    }
    return aliases.get(normalized, normalized)


def _required_descriptor(
    descriptors: Mapping[str, Any], name: str, surface_type: str
) -> Any:
    if name not in descriptors:
        raise GeometryIdentityError(
            "geometry.descriptor_missing",
            f"{surface_type} identity requires descriptor {name}",
        )
    return descriptors[name]


def _canonicalize_face(
    face: GeometryFaceInput, policy: TolerancePolicy
) -> tuple[dict[str, Any], str, str, str | None]:
    surface_type = _normalize_surface_type(face.surface_type)
    if surface_type not in SUPPORTED_SURFACES:
        raise GeometryIdentityError(
            "geometry.surface_type_unsupported",
            "surface type is outside the supported R4a identity vocabulary",
        )
    if isinstance(face.boundary_loop_count, bool) or not isinstance(
        face.boundary_loop_count, int
    ) or face.boundary_loop_count < 0:
        raise GeometryIdentityError(
            "geometry.boundary_loop_count_invalid",
            "boundary_loop_count must be a non-negative integer",
        )
    descriptors = face.descriptors
    if not isinstance(descriptors, Mapping):
        raise GeometryIdentityError(
            "geometry.descriptor_invalid", "descriptors must be a mapping"
        )
    common: dict[str, Any] = {
        "surface_type": surface_type,
        "area_q": _quantize(
            _positive(face.area, "area"), policy.area_quantum_mm2, "area"
        ),
        "centroid_q": _vector(
            face.centroid,
            field_name="centroid",
            quantum=policy.position_quantum_mm,
        ),
        "boundary_loop_count": face.boundary_loop_count,
    }
    if surface_type == "plane" and face.normal is not None:
        common["normal_q"] = _vector(
            face.normal,
            field_name="normal",
            quantum=policy.direction_quantum,
            unit=True,
        )
    elif surface_type == "plane":
        raise GeometryIdentityError(
            "geometry.descriptor_missing", "plane identity requires a normal"
        )

    shape: dict[str, Any]
    repeated_key: str | None = None
    if surface_type == "plane":
        _reject_unknown_descriptors(descriptors, set())
        shape = {}
    elif surface_type == "cylinder":
        _reject_unknown_descriptors(
            descriptors,
            {
                "axis",
                "axis_point",
                "radius",
                "length",
                "angular_extent",
                "classification",
                "full_circle",
            },
        )
        for required_name in (
            "axis",
            "axis_point",
            "radius",
            "length",
            "angular_extent",
            "classification",
            "full_circle",
        ):
            _required_descriptor(descriptors, required_name, surface_type)
        axis_values = _unit_values(
            descriptors["axis"],
            field_name="axis",
            unoriented=True,
        )
        axis_point_value = descriptors["axis_point"]
        if (
            not isinstance(axis_point_value, Sequence)
            or isinstance(axis_point_value, (str, bytes))
            or len(axis_point_value) != 3
        ):
            raise GeometryIdentityError(
                "geometry.descriptor_invalid",
                "axis_point must contain exactly three numeric components",
            )
        axis_point_values = [
            _finite_number(component, "axis_point") for component in axis_point_value
        ]
        axial_offset = math.fsum(
            component * direction
            for component, direction in zip(axis_point_values, axis_values)
        )
        canonical_axis_point = [
            component - axial_offset * direction
            for component, direction in zip(axis_point_values, axis_values)
        ]
        shape = {
            "axis_q": [
                _quantize(component, policy.direction_quantum, "axis")
                for component in axis_values
            ],
            "axis_point_q": _vector(
                canonical_axis_point,
                field_name="axis_point",
                quantum=policy.position_quantum_mm,
            ),
            "radius_q": _quantize(
                _positive(
                    descriptors["radius"],
                    "radius",
                ),
                policy.length_quantum_mm,
                "radius",
            ),
        }
        for name, quantum in (
            ("length", policy.length_quantum_mm),
            ("angular_extent", policy.angle_quantum_rad),
        ):
            value = _positive(descriptors[name], name)
            if name == "angular_extent" and value > 2.0 * math.pi + policy.semantic_angle_rad:
                raise GeometryIdentityError(
                    "geometry.descriptor_invalid",
                    "angular_extent must not exceed two pi radians",
                )
            shape[f"{name}_q"] = _quantize(value, quantum, name)
        classification = descriptors["classification"]
        full_circle = descriptors["full_circle"]
        if not isinstance(classification, str):
            raise GeometryIdentityError(
                "geometry.descriptor_invalid",
                "classification must be a string",
            )
        if classification not in {"hole", "boss", "fillet_partial"}:
            raise GeometryIdentityError(
                "geometry.descriptor_invalid",
                "classification is outside the supported cylinder vocabulary",
            )
        if not isinstance(full_circle, bool):
            raise GeometryIdentityError(
                "geometry.descriptor_invalid", "full_circle must be boolean"
            )
        angular_extent = _finite_number(
            descriptors["angular_extent"], "angular_extent"
        )
        extent_is_full = abs(angular_extent - 2.0 * math.pi) <= policy.semantic_angle_rad
        if full_circle != extent_is_full:
            raise GeometryIdentityError(
                "geometry.descriptor_inconsistent",
                "full_circle must agree with angular_extent",
            )
        if classification in {"hole", "boss"} and not full_circle:
            raise GeometryIdentityError(
                "geometry.descriptor_inconsistent",
                "hole and boss classifications require a full-circle patch",
            )
        if classification == "fillet_partial" and full_circle:
            raise GeometryIdentityError(
                "geometry.descriptor_inconsistent",
                "fillet_partial classification requires a partial patch",
            )
        shape["classification"] = classification
        shape["full_circle"] = full_circle
        if shape.get("classification") == "hole":
            repeated_shape = {
                name: value
                for name, value in shape.items()
                if name != "axis_point_q"
            }
            repeated_key = stable_hash(
                repeated_shape, domain=f"{HASH_DOMAIN}/repeated-hole"
            )
    elif surface_type == "cone":
        _reject_unknown_descriptors(
            descriptors, {"axis", "apex", "semi_angle", "reference_radius"}
        )
        semi_angle = _positive(
            _required_descriptor(descriptors, "semi_angle", surface_type),
            "semi_angle",
        )
        if semi_angle >= math.pi / 2.0:
            raise GeometryIdentityError(
                "geometry.descriptor_invalid",
                "semi_angle must be less than pi/2 radians",
            )
        shape = {
            "axis_q": _vector(
                _required_descriptor(descriptors, "axis", surface_type),
                field_name="axis",
                quantum=policy.direction_quantum,
                unit=True,
            ),
            "apex_q": _vector(
                _required_descriptor(descriptors, "apex", surface_type),
                field_name="apex",
                quantum=policy.position_quantum_mm,
            ),
            "semi_angle_q": _quantize(
                semi_angle,
                policy.angle_quantum_rad,
                "semi_angle",
            ),
        }
        if "reference_radius" in descriptors:
            shape["reference_radius_q"] = _quantize(
                _positive(descriptors["reference_radius"], "reference_radius"),
                policy.length_quantum_mm,
                "reference_radius",
            )
    elif surface_type == "sphere":
        _reject_unknown_descriptors(descriptors, {"center", "radius"})
        shape = {
            "center_q": _vector(
                _required_descriptor(descriptors, "center", surface_type),
                field_name="center",
                quantum=policy.position_quantum_mm,
            ),
            "radius_q": _quantize(
                _positive(
                    _required_descriptor(descriptors, "radius", surface_type),
                    "radius",
                ),
                policy.length_quantum_mm,
                "radius",
            ),
        }
    elif surface_type == "torus":
        _reject_unknown_descriptors(
            descriptors, {"center", "axis", "major_radius", "minor_radius"}
        )
        major_radius = _positive(
            _required_descriptor(descriptors, "major_radius", surface_type),
            "major_radius",
        )
        minor_radius = _positive(
            _required_descriptor(descriptors, "minor_radius", surface_type),
            "minor_radius",
        )
        if major_radius <= minor_radius:
            raise GeometryIdentityError(
                "geometry.descriptor_invalid",
                "major_radius must exceed minor_radius for a supported torus",
            )
        shape = {
            "center_q": _vector(
                _required_descriptor(descriptors, "center", surface_type),
                field_name="center",
                quantum=policy.position_quantum_mm,
            ),
            "axis_q": _vector(
                _required_descriptor(descriptors, "axis", surface_type),
                field_name="axis",
                quantum=policy.direction_quantum,
                unit=True,
                unoriented=True,
            ),
            "major_radius_q": _quantize(
                major_radius,
                policy.length_quantum_mm,
                "major_radius",
            ),
            "minor_radius_q": _quantize(
                minor_radius,
                policy.length_quantum_mm,
                "minor_radius",
            ),
        }
    else:
        _reject_unknown_descriptors(
            descriptors, {"degree_u", "degree_v", "rational"}
        )
        if face.bbox_min is None or face.bbox_max is None:
            raise GeometryIdentityError(
                "geometry.descriptor_missing",
                "bounded fallback identity requires bbox_min and bbox_max",
            )
        bbox_min = _vector(
            face.bbox_min,
            field_name="bbox_min",
            quantum=policy.position_quantum_mm,
        )
        bbox_max = _vector(
            face.bbox_max,
            field_name="bbox_max",
            quantum=policy.position_quantum_mm,
        )
        if any(low > high for low, high in zip(bbox_min, bbox_max)):
            raise GeometryIdentityError(
                "geometry.bounding_box_invalid",
                "bbox_min must not exceed bbox_max",
            )
        shape = {"bbox_min_q": bbox_min, "bbox_max_q": bbox_max}
        for name in ("degree_u", "degree_v"):
            if name in descriptors:
                value = descriptors[name]
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise GeometryIdentityError(
                        "geometry.descriptor_invalid",
                        f"{name} must be a positive integer",
                    )
                shape[name] = value
        if "rational" in descriptors:
            if not isinstance(descriptors["rational"], bool):
                raise GeometryIdentityError(
                    "geometry.descriptor_invalid", "rational must be boolean"
                )
            shape["rational"] = descriptors["rational"]
    common["surface"] = shape
    return (
        common,
        surface_type,
        "analytic" if surface_type in _ANALYTIC_SURFACES else "bounded_fallback",
        repeated_key,
    )


def _components(
    adjacency: Mapping[tuple[str, str], set[tuple[str, str]]],
) -> list[set[tuple[str, str]]]:
    remaining = set(adjacency)
    result: list[set[tuple[str, str]]] = []
    while remaining:
        start = min(remaining)
        component: set[tuple[str, str]] = set()
        stack = [start]
        while stack:
            key = stack.pop()
            if key in component:
                continue
            component.add(key)
            stack.extend(sorted(adjacency[key] - component, reverse=True))
        remaining -= component
        result.append(component)
    return result


def _ambiguity_partition_labels(
    *,
    keys: Sequence[tuple[str, str]],
    canonical_geometry: Mapping[tuple[str, str], Mapping[str, Any]],
    base_payloads: Mapping[tuple[str, str], Mapping[str, Any]],
    adjacency: Mapping[tuple[str, str], set[tuple[str, str]]],
    policy: TolerancePolicy,
) -> dict[tuple[str, str], str]:
    """Return conservative, order-independent ambiguity equivalence labels."""

    equivalence_graph = {key: set() for key in keys}
    for index, left in enumerate(keys):
        for right in keys[index + 1 :]:
            if (
                base_payloads[left]["topology"]
                == base_payloads[right]["topology"]
                and _geometry_within_ambiguity(
                    canonical_geometry[left], canonical_geometry[right], policy
                )
            ):
                equivalence_graph[left].add(right)
                equivalence_graph[right].add(left)
    initial_components = _components(equivalence_graph)
    labels: dict[tuple[str, str], str] = {}
    for component in initial_components:
        label = stable_hash(
            {
                "member_geometry": sorted(
                    canonical_json_bytes(canonical_geometry[key])
                    .decode("ascii")
                    for key in component
                ),
                "topology": sorted(
                    canonical_json_bytes(base_payloads[key]["topology"])
                    .decode("ascii")
                    for key in component
                ),
            },
            domain=f"{HASH_DOMAIN}/ambiguity-base",
        )
        for key in component:
            labels[key] = label

    # Partition refinement can split a geometrically ambiguous class when the
    # canonical multiset of its neighbors differs.  It can never merge classes.
    for _round in range(len(keys)):
        refinement_keys = {
            key: (
                labels[key],
                tuple(sorted(labels[neighbor] for neighbor in adjacency[key])),
            )
            for key in keys
        }
        unique = sorted(set(refinement_keys.values()))
        next_label_by_key = {
            refinement_key: stable_hash(
                {
                    "ambiguity_base": refinement_key[0],
                    "neighbor_ambiguity_labels": refinement_key[1],
                },
                domain=f"{HASH_DOMAIN}/ambiguity-topology",
            )
            for refinement_key in unique
        }
        next_labels = {
            key: next_label_by_key[refinement_keys[key]] for key in keys
        }
        old_partition = {
            frozenset(member for member in keys if labels[member] == label)
            for label in set(labels.values())
        }
        new_partition = {
            frozenset(member for member in keys if next_labels[member] == label)
            for label in set(next_labels.values())
        }
        labels = next_labels
        if old_partition == new_partition:
            break
    return labels


def _geometry_within_ambiguity(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    policy: TolerancePolicy,
) -> bool:
    if (
        left["surface_type"] != right["surface_type"]
        or left["boundary_loop_count"] != right["boundary_loop_count"]
    ):
        return False
    if (
        abs(left["area_q"] - right["area_q"]) * policy.area_quantum_mm2
        > policy.ambiguity_area_mm2
    ):
        return False
    if not _quantized_vector_close(
        left["centroid_q"],
        right["centroid_q"],
        quantum=policy.position_quantum_mm,
        tolerance=policy.ambiguity_linear_mm,
    ):
        return False
    if ("normal_q" in left) != ("normal_q" in right):
        return False
    if "normal_q" in left and not _quantized_direction_close(
        left["normal_q"],
        right["normal_q"],
        policy=policy,
        unoriented=False,
    ):
        return False
    left_shape = left["surface"]
    right_shape = right["surface"]
    if set(left_shape) != set(right_shape):
        return False
    for name in sorted(left_shape):
        left_value = left_shape[name]
        right_value = right_shape[name]
        if name in {"axis_q"}:
            if not _quantized_direction_close(
                left_value, right_value, policy=policy, unoriented=True
            ):
                return False
        elif name in {
            "axis_point_q",
            "apex_q",
            "center_q",
            "bbox_min_q",
            "bbox_max_q",
        }:
            if not _quantized_vector_close(
                left_value,
                right_value,
                quantum=policy.position_quantum_mm,
                tolerance=policy.ambiguity_linear_mm,
            ):
                return False
        elif name.endswith("angle_q") or name == "angular_extent_q":
            if (
                abs(left_value - right_value) * policy.angle_quantum_rad
                > policy.ambiguity_angle_rad
            ):
                return False
        elif name.endswith("radius_q") or name == "length_q":
            if (
                abs(left_value - right_value) * policy.length_quantum_mm
                > policy.ambiguity_linear_mm
            ):
                return False
        elif left_value != right_value:
            return False
    return True


def _quantized_vector_close(
    left: Sequence[int],
    right: Sequence[int],
    *,
    quantum: float,
    tolerance: float,
) -> bool:
    distance = math.sqrt(
        math.fsum(((a - b) * quantum) ** 2 for a, b in zip(left, right))
    )
    return distance <= tolerance


def _quantized_direction_close(
    left: Sequence[int],
    right: Sequence[int],
    *,
    policy: TolerancePolicy,
    unoriented: bool,
) -> bool:
    left_values = [value * policy.direction_quantum for value in left]
    right_values = [value * policy.direction_quantum for value in right]
    left_norm = math.sqrt(math.fsum(value * value for value in left_values))
    right_norm = math.sqrt(math.fsum(value * value for value in right_values))
    cosine = math.fsum(
        a * b for a, b in zip(left_values, right_values)
    ) / (left_norm * right_norm)
    if unoriented:
        cosine = abs(cosine)
    cosine = min(1.0, max(-1.0, cosine))
    return math.acos(cosine) <= policy.ambiguity_angle_rad


def _multiset(values: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(Counter(values).items())
    ]
