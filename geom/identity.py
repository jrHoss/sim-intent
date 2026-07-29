"""Deterministic, versioned face identity within one model version.

The core deliberately separates four numerical concepts:

* semantic tolerances answer engineering comparison questions;
* representation-noise guards withhold uniqueness at unresolved float
  boundaries;
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
from fractions import Fraction
from typing import Any, Mapping, Sequence

from geom.analytic import AnalyticSurfaceEvidence
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

    def __post_init__(self) -> None:
        numeric_fields = (
            "semantic_linear_mm",
            "semantic_area_mm2",
            "semantic_angle_rad",
            "ambiguity_linear_mm",
            "ambiguity_area_mm2",
            "ambiguity_angle_rad",
            "position_quantum_mm",
            "length_quantum_mm",
            "area_quantum_mm2",
            "angle_quantum_rad",
            "direction_quantum",
            "scalar_quantum",
        )
        for name in numeric_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GeometryIdentityError(
                    "geometry.tolerance_policy_invalid",
                    "all tolerance values must be finite positive numbers",
                )
            try:
                normalized = float(value)
            except (OverflowError, TypeError, ValueError) as exc:
                raise GeometryIdentityError(
                    "geometry.tolerance_policy_invalid",
                    "all tolerance values must be finite positive numbers",
                ) from exc
            if not math.isfinite(normalized) or normalized <= 0:
                raise GeometryIdentityError(
                    "geometry.tolerance_policy_invalid",
                    "all tolerance values must be finite positive numbers",
                )
            if isinstance(value, int) and int(normalized) != value:
                raise GeometryIdentityError(
                    "geometry.tolerance_policy_invalid",
                    "all tolerance values must be finite positive numbers",
                )
            object.__setattr__(self, name, normalized)

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


def _artifact_schema_error(message: str) -> GeometryIdentityError:
    return GeometryIdentityError("geometry.artifact_schema_invalid", message)


def _exact_keys(value: Any, expected: set[str], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise _artifact_schema_error(
            f"{field_name} must contain exactly the versioned fields"
        )
    if any(not isinstance(name, str) for name in value):
        raise _artifact_schema_error(f"{field_name} field names must be strings")
    return value


def _artifact_integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _artifact_schema_error(
            f"{field_name} must be an integer greater than or equal to {minimum}"
        )
    return value


def _artifact_hash(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9a-f]{64}", value)
    ):
        raise _artifact_schema_error(f"{field_name} must be a lowercase SHA-256 value")
    return value


def _artifact_prefixed_hash(value: Any, prefix: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise _artifact_schema_error(f"{field_name} has an invalid discriminator")
    _artifact_hash(value[len(prefix) :], field_name)
    return value


def _artifact_source_ref(value: Any, field_name: str) -> int | str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _artifact_schema_error(
            f"{field_name} must be an integer or non-empty string"
        )
    if isinstance(value, str) and not value:
        raise _artifact_schema_error(
            f"{field_name} must be an integer or non-empty string"
        )
    return value


def _artifact_int_vector(value: Any, field_name: str) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise _artifact_schema_error(
            f"{field_name} must contain exactly three canonical integers"
        )
    return value


def _validate_canonical_geometry(
    value: Any, surface_type: str
) -> Mapping[str, Any]:
    required = {
        "surface_type",
        "area_q",
        "centroid_q",
        "boundary_loop_count",
        "surface",
    }
    if surface_type == "plane":
        required.add("normal_q")
    geometry = _exact_keys(value, required, "canonical_geometry")
    if geometry["surface_type"] != surface_type:
        raise _artifact_schema_error(
            "canonical_geometry surface type must match the face"
        )
    _artifact_integer(geometry["area_q"], "canonical_geometry.area_q", minimum=1)
    _artifact_int_vector(
        geometry["centroid_q"], "canonical_geometry.centroid_q"
    )
    _artifact_integer(
        geometry["boundary_loop_count"],
        "canonical_geometry.boundary_loop_count",
    )
    if surface_type == "plane":
        _artifact_int_vector(
            geometry["normal_q"], "canonical_geometry.normal_q"
        )
    shape = geometry["surface"]
    exact_shape_fields: dict[str, set[str]] = {
        "plane": set(),
        "cylinder": {
            "axis_q",
            "axis_point_q",
            "radius_q",
            "length_q",
            "angular_extent_q",
            "classification",
            "full_circle",
        },
        "sphere": {"center_q", "radius_q"},
        "torus": {"center_q", "axis_q", "major_radius_q", "minor_radius_q"},
    }
    if surface_type == "cone":
        if not isinstance(shape, Mapping) or set(shape) not in (
            {"axis_q", "apex_q", "semi_angle_q"},
            {"axis_q", "apex_q", "semi_angle_q", "reference_radius_q"},
        ):
            raise _artifact_schema_error(
                "canonical_geometry.surface must contain the cone descriptor fields"
            )
    elif surface_type in _FALLBACK_SURFACES:
        if (
            not isinstance(shape, Mapping)
            or not {"bbox_min_q", "bbox_max_q"} <= set(shape)
            or not set(shape)
            <= {"bbox_min_q", "bbox_max_q", "degree_u", "degree_v", "rational"}
        ):
            raise _artifact_schema_error(
                "canonical_geometry.surface must contain the fallback descriptor fields"
            )
    else:
        shape = _exact_keys(
            shape,
            exact_shape_fields[surface_type],
            "canonical_geometry.surface",
        )
    vector_fields = {
        "axis_q",
        "axis_point_q",
        "apex_q",
        "center_q",
        "bbox_min_q",
        "bbox_max_q",
    }
    scalar_fields = {
        "radius_q",
        "length_q",
        "angular_extent_q",
        "semi_angle_q",
        "reference_radius_q",
        "major_radius_q",
        "minor_radius_q",
    }
    for name, item in shape.items():
        if name in vector_fields:
            _artifact_int_vector(item, f"canonical_geometry.surface.{name}")
        elif name in scalar_fields:
            _artifact_integer(
                item, f"canonical_geometry.surface.{name}", minimum=1
            )
        elif name in {"degree_u", "degree_v"}:
            _artifact_integer(
                item, f"canonical_geometry.surface.{name}", minimum=1
            )
        elif name == "rational":
            if not isinstance(item, bool):
                raise _artifact_schema_error(
                    "canonical_geometry.surface.rational must be boolean"
                )
        elif name == "classification":
            if item not in {"hole", "boss", "fillet_partial"}:
                raise _artifact_schema_error(
                    "canonical_geometry.surface.classification is invalid"
                )
        elif name == "full_circle" and not isinstance(item, bool):
            raise _artifact_schema_error(
                "canonical_geometry.surface.full_circle must be boolean"
            )
    if surface_type == "cylinder":
        full_circle = shape["full_circle"]
        classification = shape["classification"]
        if (classification in {"hole", "boss"}) != full_circle:
            raise _artifact_schema_error(
                "canonical cylinder classification is inconsistent"
            )
    if surface_type == "torus" and shape["major_radius_q"] <= shape["minor_radius_q"]:
        raise _artifact_schema_error(
            "canonical torus radii are inconsistent"
        )
    return geometry


def _validate_tolerance_policy(value: Any) -> TolerancePolicy:
    policy = _exact_keys(
        value,
        {
            "policy_id",
            "semantic_tolerances",
            "ambiguity_tolerances",
            "canonical_quanta",
        },
        "tolerance_policy",
    )
    if not isinstance(policy["policy_id"], str) or not policy["policy_id"]:
        raise _artifact_schema_error("tolerance_policy.policy_id must be non-empty")
    semantic = _exact_keys(
        policy["semantic_tolerances"],
        {"linear_mm", "area_mm2", "angle_rad"},
        "tolerance_policy.semantic_tolerances",
    )
    ambiguity = _exact_keys(
        policy["ambiguity_tolerances"],
        {"linear_mm", "area_mm2", "angle_rad"},
        "tolerance_policy.ambiguity_tolerances",
    )
    quanta = _exact_keys(
        policy["canonical_quanta"],
        {
            "position_mm",
            "length_mm",
            "area_mm2",
            "angle_rad",
            "direction",
            "dimensionless_scalar",
        },
        "tolerance_policy.canonical_quanta",
    )
    try:
        result = TolerancePolicy(
            policy_id=policy["policy_id"],
            semantic_linear_mm=semantic["linear_mm"],
            semantic_area_mm2=semantic["area_mm2"],
            semantic_angle_rad=semantic["angle_rad"],
            ambiguity_linear_mm=ambiguity["linear_mm"],
            ambiguity_area_mm2=ambiguity["area_mm2"],
            ambiguity_angle_rad=ambiguity["angle_rad"],
            position_quantum_mm=quanta["position_mm"],
            length_quantum_mm=quanta["length_mm"],
            area_quantum_mm2=quanta["area_mm2"],
            angle_quantum_rad=quanta["angle_rad"],
            direction_quantum=quanta["direction"],
            scalar_quantum=quanta["dimensionless_scalar"],
        )
        _validate_policy(result)
    except GeometryIdentityError as exc:
        raise _artifact_schema_error("tolerance_policy is invalid") from exc
    if result.to_dict() != value:
        raise _artifact_schema_error("tolerance_policy is not canonical")
    return result


def deserialize_geometry_identity(
    canonical_bytes: bytes,
) -> GeometryIdentityArtifact:
    """Strictly deserialize one byte-canonical, semantically complete artifact."""

    try:
        raw = bytes(canonical_bytes)
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeometryIdentityError(
            "geometry.artifact_integrity_failed",
            "geometry identity bytes are not canonical JSON",
        ) from exc
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != raw:
        raise GeometryIdentityError(
            "geometry.artifact_integrity_failed",
            "geometry identity bytes are not canonical JSON",
        )
    document = _exact_keys(
        payload,
        {
            "artifact_type",
            "schema_version",
            "hash_domain",
            "model_binding",
            "tolerance_policy",
            "faces",
            "collision_groups",
        },
        "artifact",
    )
    if (
        document["artifact_type"] != "geometry_identity"
        or document["schema_version"] != GEOMETRY_IDENTITY_SCHEMA_VERSION
        or document["hash_domain"] != HASH_DOMAIN
    ):
        raise GeometryIdentityError(
            "geometry.artifact_version_unsupported",
            "geometry identity schema or hash-domain version is unsupported",
        )
    binding = _exact_keys(
        document["model_binding"],
        {"model_version_id", "source_sha256"},
        "model_binding",
    )
    if (
        not isinstance(binding["model_version_id"], str)
        or not binding["model_version_id"].strip()
        or not isinstance(binding["source_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", binding["source_sha256"])
    ):
        raise GeometryIdentityError(
            "geometry.artifact_binding_invalid",
            "geometry identity binding metadata is invalid",
        )
    tolerance_policy = _validate_tolerance_policy(document["tolerance_policy"])
    raw_faces = document["faces"]
    if not isinstance(raw_faces, list) or not raw_faces:
        raise _artifact_schema_error("faces must be a non-empty array")
    face_fields = {
        "source_ref",
        "surface_type",
        "canonical_geometry",
        "topology",
        "local_semantic_signature",
        "connected_topology_signature",
        "identity_candidate",
        "stable_identity",
        "collision_group_id",
        "ambiguous",
        "identity_quality",
        "evidence",
    }
    identities: list[FaceIdentity] = []
    by_key: dict[tuple[str, str], FaceIdentity] = {}
    for raw_face in raw_faces:
        face = _exact_keys(raw_face, face_fields, "face")
        source_ref = _artifact_source_ref(face["source_ref"], "face.source_ref")
        source_key = _source_key(source_ref)
        if source_key in by_key:
            raise _artifact_schema_error("face source references must be unique")
        surface_type = face["surface_type"]
        if surface_type not in SUPPORTED_SURFACES:
            raise _artifact_schema_error("face.surface_type is unsupported")
        canonical_geometry = _validate_canonical_geometry(
            face["canonical_geometry"], surface_type
        )
        topology = _exact_keys(
            face["topology"],
            {
                "boundary_loop_count",
                "adjacency_degree",
                "neighbor_surface_type_multiset",
                "repeated_feature_count",
                "adjacent_source_refs",
                "neighbor_semantic_signatures",
            },
            "face.topology",
        )
        loop_count = _artifact_integer(
            topology["boundary_loop_count"], "face.topology.boundary_loop_count"
        )
        if loop_count != canonical_geometry["boundary_loop_count"]:
            raise _artifact_schema_error(
                "face topology and canonical boundary-loop counts must agree"
            )
        adjacency_degree = _artifact_integer(
            topology["adjacency_degree"], "face.topology.adjacency_degree"
        )
        repeated_count = _artifact_integer(
            topology["repeated_feature_count"],
            "face.topology.repeated_feature_count",
        )
        adjacent_refs = topology["adjacent_source_refs"]
        if not isinstance(adjacent_refs, list):
            raise _artifact_schema_error(
                "face.topology.adjacent_source_refs must be an array"
            )
        adjacent_keys = [
            _source_key(
                _artifact_source_ref(
                    item, "face.topology.adjacent_source_refs"
                )
            )
            for item in adjacent_refs
        ]
        if (
            len(adjacent_keys) != adjacency_degree
            or len(set(adjacent_keys)) != len(adjacent_keys)
            or source_key in adjacent_keys
            or adjacent_keys != sorted(adjacent_keys)
        ):
            raise _artifact_schema_error(
                "face.topology adjacency mapping is malformed"
            )
        neighbor_signatures = topology["neighbor_semantic_signatures"]
        if (
            not isinstance(neighbor_signatures, list)
            or len(neighbor_signatures) != adjacency_degree
        ):
            raise _artifact_schema_error(
                "face.topology neighbor signatures are malformed"
            )
        for signature in neighbor_signatures:
            _artifact_hash(signature, "face.topology.neighbor_semantic_signatures")
        if neighbor_signatures != sorted(neighbor_signatures):
            raise _artifact_schema_error(
                "face.topology neighbor signatures are not canonical"
            )
        multiset = topology["neighbor_surface_type_multiset"]
        if not isinstance(multiset, list):
            raise _artifact_schema_error(
                "face.topology neighbor surface multiset must be an array"
            )
        multiset_total = 0
        previous_value: str | None = None
        for raw_item in multiset:
            item = _exact_keys(
                raw_item,
                {"value", "count"},
                "face.topology.neighbor_surface_type_multiset item",
            )
            if (
                item["value"] not in SUPPORTED_SURFACES
                or previous_value is not None
                and item["value"] <= previous_value
            ):
                raise _artifact_schema_error(
                    "face.topology neighbor surface multiset is malformed"
                )
            previous_value = item["value"]
            multiset_total += _artifact_integer(
                item["count"],
                "face.topology.neighbor_surface_type_multiset count",
                minimum=1,
            )
        if multiset_total != adjacency_degree:
            raise _artifact_schema_error(
                "face.topology neighbor surface multiset is inconsistent"
            )
        local_signature = _artifact_hash(
            face["local_semantic_signature"], "face.local_semantic_signature"
        )
        connected_signature = _artifact_hash(
            face["connected_topology_signature"],
            "face.connected_topology_signature",
        )
        identity_candidate = _artifact_prefixed_hash(
            face["identity_candidate"], "gic1:", "face.identity_candidate"
        )
        stable_identity = face["stable_identity"]
        if stable_identity is not None:
            _artifact_prefixed_hash(
                stable_identity, "gfi1:", "face.stable_identity"
            )
        collision_group_id = face["collision_group_id"]
        if collision_group_id is not None:
            _artifact_hash(collision_group_id, "face.collision_group_id")
        ambiguous = face["ambiguous"]
        if not isinstance(ambiguous, bool):
            raise _artifact_schema_error("face.ambiguous must be boolean")
        if ambiguous:
            if stable_identity is not None or collision_group_id is None:
                raise _artifact_schema_error(
                    "ambiguous faces require a collision group and no stable identity"
                )
        elif (
            collision_group_id is not None
            or stable_identity
            != f"gfi1:{identity_candidate.removeprefix('gic1:')}"
        ):
            raise _artifact_schema_error(
                "stable face identity state is inconsistent"
            )
        identity_quality = face["identity_quality"]
        if identity_quality not in {
            "analytic",
            "bounded_fallback",
            "bounded_representation_guard",
        }:
            raise _artifact_schema_error("face.identity_quality is invalid")
        if (
            identity_quality == "analytic"
            and surface_type not in _ANALYTIC_SURFACES
        ) or (
            identity_quality == "bounded_fallback"
            and surface_type not in _FALLBACK_SURFACES
        ):
            raise _artifact_schema_error(
                "face identity quality and surface type are inconsistent"
            )
        if identity_quality != "analytic" and not ambiguous:
            raise _artifact_schema_error(
                "bounded identity quality requires an ambiguity group"
            )
        evidence_fields = {
            "source_local_only",
            "repeated_feature_signature",
            "repeated_feature_group_size",
        }
        if identity_quality == "bounded_representation_guard":
            evidence_fields.add("representation_noise_guard")
        evidence = _exact_keys(face["evidence"], evidence_fields, "face.evidence")
        if evidence["source_local_only"] is not True:
            raise _artifact_schema_error(
                "face.evidence.source_local_only must be true"
            )
        repeated_signature = evidence["repeated_feature_signature"]
        if repeated_signature is not None:
            _artifact_hash(
                repeated_signature, "face.evidence.repeated_feature_signature"
            )
        evidence_repeated_count = _artifact_integer(
            evidence["repeated_feature_group_size"],
            "face.evidence.repeated_feature_group_size",
        )
        if (
            evidence_repeated_count != repeated_count
            or (repeated_signature is None) != (repeated_count == 0)
        ):
            raise _artifact_schema_error(
                "face repeated-feature evidence is inconsistent"
            )
        if identity_quality == "bounded_representation_guard":
            guard = _exact_keys(
                evidence["representation_noise_guard"],
                {"policy", "stable_identity_withheld"},
                "face.evidence.representation_noise_guard",
            )
            if guard != {
                "policy": "two_input_ulps_at_half_quantum",
                "stable_identity_withheld": True,
            }:
                raise _artifact_schema_error(
                    "face representation-noise guard is invalid"
                )
        identity = FaceIdentity(
            source_ref=source_ref,
            surface_type=surface_type,
            canonical_geometry=canonical_geometry,
            topology=topology,
            local_semantic_signature=local_signature,
            connected_topology_signature=connected_signature,
            identity_candidate=identity_candidate,
            stable_identity=stable_identity,
            collision_group_id=collision_group_id,
            ambiguous=ambiguous,
            identity_quality=identity_quality,
            evidence=evidence,
        )
        identities.append(identity)
        by_key[source_key] = identity
    if [_source_key(face.source_ref) for face in identities] != sorted(by_key):
        raise _artifact_schema_error("faces must use canonical source-reference order")
    recomputed_repeated = {
        key: _repeated_feature_signature(face.canonical_geometry)
        for key, face in by_key.items()
    }
    recomputed_repeated_members: dict[
        str, list[tuple[str, str]]
    ] = defaultdict(list)
    for key, signature in recomputed_repeated.items():
        if signature is not None:
            recomputed_repeated_members[signature].append(key)
    for key, face in by_key.items():
        signature = recomputed_repeated[key]
        group_size = (
            len(recomputed_repeated_members[signature])
            if signature is not None
            else 0
        )
        if (
            face.evidence["repeated_feature_signature"] != signature
            or face.evidence["repeated_feature_group_size"] != group_size
            or face.topology["repeated_feature_count"] != group_size
        ):
            raise _artifact_schema_error(
                "face repeated-feature partition is inconsistent with "
                "canonical cylinder geometry"
            )
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for key, face in by_key.items():
        neighbors = {_source_key(item) for item in face.topology["adjacent_source_refs"]}
        if any(neighbor not in by_key for neighbor in neighbors):
            raise _artifact_schema_error(
                "face adjacency references an unavailable source-local face"
            )
        adjacency[key] = neighbors
    for key, neighbors in adjacency.items():
        if any(key not in adjacency[neighbor] for neighbor in neighbors):
            raise _artifact_schema_error("face adjacency must be symmetric")
        face = by_key[key]
        if face.topology["neighbor_semantic_signatures"] != sorted(
            by_key[neighbor].local_semantic_signature for neighbor in neighbors
        ):
            raise _artifact_schema_error(
                "face neighbor semantic signatures are inconsistent"
            )
        expected_multiset = _multiset(
            sorted(by_key[neighbor].surface_type for neighbor in neighbors)
        )
        if face.topology["neighbor_surface_type_multiset"] != expected_multiset:
            raise _artifact_schema_error(
                "face neighbor surface multiset is inconsistent"
            )
    if len(_components(adjacency)) != 1:
        raise _artifact_schema_error("face topology must be connected")
    canonical_profile = {
        "policy_id": tolerance_policy.policy_id,
        "position_quantum_mm": tolerance_policy.position_quantum_mm,
        "length_quantum_mm": tolerance_policy.length_quantum_mm,
        "area_quantum_mm2": tolerance_policy.area_quantum_mm2,
        "angle_quantum_rad": tolerance_policy.angle_quantum_rad,
        "direction_quantum": tolerance_policy.direction_quantum,
    }
    base_payloads = {
        key: {
            "canonical_profile": canonical_profile,
            "geometry": by_key[key].canonical_geometry,
            "topology": {
                name: by_key[key].topology[name]
                for name in (
                    "boundary_loop_count",
                    "adjacency_degree",
                    "neighbor_surface_type_multiset",
                    "repeated_feature_count",
                )
            },
        }
        for key in sorted(by_key)
    }
    colors = {
        key: stable_hash(
            base_payloads[key], domain=f"{HASH_DOMAIN}/base-face"
        )
        for key in sorted(by_key)
    }
    for _round in range(len(by_key)):
        colors = {
            key: stable_hash(
                {
                    "base": base_payloads[key],
                    "neighbor_colors": sorted(
                        colors[neighbor] for neighbor in adjacency[key]
                    ),
                },
                domain=f"{HASH_DOMAIN}/topology-round",
            )
            for key in sorted(by_key)
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
        for key in sorted(by_key)
    }
    for key, face in by_key.items():
        if (
            face.local_semantic_signature != colors[key]
            or face.connected_topology_signature != component_signature
            or face.identity_candidate != f"gic1:{candidates[key]}"
        ):
            raise _artifact_schema_error(
                "face semantic or connected-topology identity is inconsistent"
            )
    raw_groups = document["collision_groups"]
    if not isinstance(raw_groups, list):
        raise _artifact_schema_error("collision_groups must be an array")
    groups: list[Mapping[str, Any]] = []
    groups_by_id: dict[str, Mapping[str, Any]] = {}
    reasons = {
        "representation_noise_guard_requires_confirmation",
        "bounded_fallback_requires_confirmation",
        "geometrically_and_topologically_indistinguishable",
        "within_declared_ambiguity_tolerance",
    }
    for raw_group in raw_groups:
        group = _exact_keys(
            raw_group,
            {
                "collision_group_id",
                "identity_candidates",
                "member_source_refs",
                "reason",
            },
            "collision_group",
        )
        group_id = _artifact_hash(
            group["collision_group_id"], "collision_group.collision_group_id"
        )
        if group_id in groups_by_id:
            raise _artifact_schema_error("collision group identifiers must be unique")
        group_candidates = group["identity_candidates"]
        members = group["member_source_refs"]
        if (
            not isinstance(group_candidates, list)
            or not isinstance(members, list)
            or not members
            or group_candidates != sorted(group_candidates)
            or len(group_candidates) != len(members)
        ):
            raise _artifact_schema_error("collision group mappings are malformed")
        for candidate in group_candidates:
            _artifact_prefixed_hash(
                candidate, "gic1:", "collision_group.identity_candidates"
            )
        member_keys = [
            _source_key(
                _artifact_source_ref(
                    member, "collision_group.member_source_refs"
                )
            )
            for member in members
        ]
        if (
            member_keys != sorted(member_keys)
            or len(member_keys) != len(set(member_keys))
            or any(member not in by_key for member in member_keys)
            or group["reason"] not in reasons
        ):
            raise _artifact_schema_error("collision group mappings are malformed")
        expected_faces = [by_key[member] for member in member_keys]
        if (
            any(
                not face.ambiguous or face.collision_group_id != group_id
                for face in expected_faces
            )
            or group_candidates
            != sorted(face.identity_candidate for face in expected_faces)
        ):
            raise _artifact_schema_error(
                "collision group and face identity states are inconsistent"
            )
        groups.append(group)
        groups_by_id[group_id] = group
    if [group["collision_group_id"] for group in groups] != sorted(groups_by_id):
        raise _artifact_schema_error("collision groups must use canonical order")
    for face in identities:
        if face.ambiguous:
            group = groups_by_id.get(face.collision_group_id)
            if (
                group is None
                or face.source_ref not in group["member_source_refs"]
            ):
                raise _artifact_schema_error(
                    "ambiguous face does not belong to its collision group"
                )
    ambiguity_labels = _ambiguity_partition_labels(
        keys=sorted(by_key),
        canonical_geometry={
            key: by_key[key].canonical_geometry for key in by_key
        },
        base_payloads=base_payloads,
        adjacency=adjacency,
        policy=tolerance_policy,
    )
    members_by_ambiguity: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, label in ambiguity_labels.items():
        members_by_ambiguity[label].append(key)
    expected_groups: list[dict[str, Any]] = []
    expected_group_ids: dict[tuple[str, str], str] = {}
    for label, members in sorted(members_by_ambiguity.items()):
        bounded = any(
            by_key[key].identity_quality != "analytic" for key in members
        )
        if len(members) <= 1 and not bounded:
            continue
        group_id = stable_hash(
            {
                "ambiguity_signature": label,
                "member_identity_candidates": sorted(
                    candidates[key] for key in members
                ),
            },
            domain=f"{HASH_DOMAIN}/collision-group",
        )
        for key in members:
            expected_group_ids[key] = group_id
        expected_groups.append(
            {
                "collision_group_id": group_id,
                "identity_candidates": sorted(
                    f"gic1:{candidates[key]}" for key in members
                ),
                "member_source_refs": [
                    by_key[key].source_ref for key in sorted(members)
                ],
                "reason": (
                    (
                        "representation_noise_guard_requires_confirmation"
                        if by_key[members[0]].identity_quality
                        == "bounded_representation_guard"
                        else "bounded_fallback_requires_confirmation"
                    )
                    if len(members) == 1
                    else (
                        "geometrically_and_topologically_indistinguishable"
                        if len({candidates[key] for key in members}) == 1
                        else "within_declared_ambiguity_tolerance"
                    )
                ),
            }
        )
    expected_groups.sort(key=lambda group: group["collision_group_id"])
    if groups != expected_groups or any(
        face.collision_group_id != expected_group_ids.get(key)
        or face.ambiguous != (key in expected_group_ids)
        for key, face in by_key.items()
    ):
        raise _artifact_schema_error(
            "collision groups do not match the authoritative ambiguity partition"
        )
    artifact = GeometryIdentityArtifact(
        model_version_id=binding["model_version_id"],
        source_sha256=binding["source_sha256"],
        faces=tuple(identities),
        collision_groups=tuple(groups),
        tolerance_policy=tolerance_policy,
    )
    if artifact.canonical_bytes() != raw:
        raise GeometryIdentityError(
            "geometry.artifact_integrity_failed",
            "geometry identity canonical reserialization changed stored bytes",
        )
    return artifact


def stable_hash(value: Any, *, domain: str) -> str:
    payload = domain.encode("ascii") + b"\0" + canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _repeated_feature_signature(
    canonical_geometry: Mapping[str, Any],
) -> str | None:
    """Return the authoritative signature for one canonical repeated feature."""

    if canonical_geometry.get("surface_type") != "cylinder":
        return None
    shape = canonical_geometry.get("surface")
    if not isinstance(shape, Mapping) or shape.get("classification") != "hole":
        return None
    repeated_shape = {
        name: value for name, value in shape.items() if name != "axis_point_q"
    }
    return stable_hash(
        repeated_shape, domain=f"{HASH_DOMAIN}/repeated-hole"
    )


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
    return abs(Fraction.from_float(a) - Fraction.from_float(b)) <= (
        Fraction.from_float(tolerance)
    )


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
        bounded = any(quality[key] != "analytic" for key in members)
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
                        (
                            "representation_noise_guard_requires_confirmation"
                            if quality[members[0]] == "bounded_representation_guard"
                            else "bounded_fallback_requires_confirmation"
                        )
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
        evidence = {
            "source_local_only": True,
            "repeated_feature_signature": repeated_key,
            "repeated_feature_group_size": repeated_group_size,
        }
        if quality[key] == "bounded_representation_guard":
            evidence["representation_noise_guard"] = {
                "policy": "two_input_ulps_at_half_quantum",
                "stable_identity_withheld": True,
            }
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
                evidence=evidence,
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
    analytic_surfaces: Mapping[int, AnalyticSurfaceEvidence] | None = None,
) -> list[GeometryFaceInput]:
    """Adapt the existing STEP inventory authority to the R4a core.

    Cylinder analysis is required because radius and axis must never be
    approximated from a bounding box.
    """

    cylinder_records = cylinders or {}
    surface_records = analytic_surfaces
    if surface_records is not None:
        expected = {
            face.tag
            for face in inventory.faces
            if _normalize_surface_type(face.surface_type) in _ANALYTIC_SURFACES
        }
        if set(surface_records) != expected:
            raise GeometryIdentityError(
                "geometry.descriptor_missing",
                "analytic descriptor evidence must match every source-local analytic face",
            )
    graph = adjacency_graph(inventory.faces)
    result: list[GeometryFaceInput] = []
    for face in inventory.faces:
        surface_type = _normalize_surface_type(face.surface_type)
        descriptors: dict[str, Any] = {}
        if surface_records is not None and surface_type in _ANALYTIC_SURFACES:
            evidence = surface_records.get(face.tag)
            if (
                evidence is None
                or evidence.tag != face.tag
                or evidence.surface_type != surface_type
                or not isinstance(evidence.descriptors, Mapping)
            ):
                raise GeometryIdentityError(
                    "geometry.descriptor_invalid",
                    "analytic descriptor evidence does not match its source-local face",
                )
            descriptors = dict(evidence.descriptors)
        elif surface_type == "cylinder":
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
    for section in (
        "semantic_tolerances",
        "ambiguity_tolerances",
        "canonical_quanta",
    ):
        for value in policy.to_dict()[section].values():
            if (
                isinstance(value, bool)
                or not isinstance(value, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise GeometryIdentityError(
                    "geometry.tolerance_policy_invalid",
                    "all tolerance values must be finite positive numbers",
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


class _QuantizedInteger(int):
    """Canonical integer carrying non-serialized boundary-guard evidence."""

    representation_guarded: bool

    def __new__(cls, value: int, *, representation_guarded: bool = False):
        instance = super().__new__(cls, value)
        instance.representation_guarded = representation_guarded
        return instance


def _round_half_even(value: Fraction) -> int:
    lower, remainder = divmod(value.numerator, value.denominator)
    comparison = 2 * remainder - value.denominator
    if comparison < 0:
        return lower
    if comparison > 0:
        return lower + 1
    return lower if lower % 2 == 0 else lower + 1


def _contains_representation_guard(value: Any) -> bool:
    if isinstance(value, _QuantizedInteger):
        return value.representation_guarded
    if isinstance(value, Mapping):
        return any(_contains_representation_guard(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_representation_guard(item) for item in value)
    return False


def _plain_canonical_numbers(value: Any) -> Any:
    if isinstance(value, _QuantizedInteger):
        return int(value)
    if isinstance(value, Mapping):
        return {
            name: _plain_canonical_numbers(item) for name, item in value.items()
        }
    if isinstance(value, list):
        return [_plain_canonical_numbers(item) for item in value]
    return value


def _normalized_vector(
    values: Sequence[float], *, field_name: str, unoriented: bool
) -> list[float]:
    scale = max(abs(component) for component in values)
    if scale == 0:
        raise GeometryIdentityError(
            "geometry.vector_zero", f"{field_name} cannot be a zero vector"
        )
    scaled = [component / scale for component in values]
    scaled_norm = math.hypot(*scaled)
    if not math.isfinite(scaled_norm) or scaled_norm == 0:
        raise GeometryIdentityError(
            "geometry.numeric_non_finite",
            f"{field_name} normalization must remain finite",
        )
    normalized = [component / scaled_norm for component in scaled]
    if not all(math.isfinite(component) for component in normalized):
        raise GeometryIdentityError(
            "geometry.numeric_non_finite",
            f"{field_name} normalization must remain finite",
        )
    if unoriented:
        dominant = max(range(3), key=lambda index: abs(normalized[index]))
        if normalized[dominant] < 0:
            normalized = [-component for component in normalized]
    return normalized


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryIdentityError(
            "geometry.descriptor_invalid", f"{field_name} must be numeric"
        )
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise GeometryIdentityError(
            "geometry.numeric_invalid",
            f"{field_name} must be representable as a finite number",
        ) from exc
    if not math.isfinite(result):
        raise GeometryIdentityError(
            "geometry.numeric_non_finite", f"{field_name} must be finite"
        )
    return result


def _quantize(value: Any, quantum: float, field_name: str) -> int:
    """Quantize exactly, guarding one-input-ULP noise at half-quantum boundaries.

    Comparison tolerances remain independent of this representation policy.
    Inputs within two ULPs of a rounding boundary use that boundary's
    half-even bucket and carry bounded-instability evidence, preventing a
    unique stable identity. Values outside the guard use exact rational
    half-even rounding and remain distinguishable.
    """

    number = _finite_number(value, field_name)
    exact_number = Fraction.from_float(number)
    exact_quantum = Fraction.from_float(quantum)
    scaled = exact_number / exact_quantum
    lower = scaled.numerator // scaled.denominator
    boundary = exact_quantum * Fraction(2 * lower + 1, 2)
    guard_radius = 2 * Fraction.from_float(math.ulp(number))
    guarded = abs(exact_number - boundary) <= guard_radius
    if guarded:
        quantized = lower if lower % 2 == 0 else lower + 1
    else:
        quantized = _round_half_even(scaled)
    return _QuantizedInteger(quantized, representation_guarded=guarded)


def _vector(
    value: Any,
    *,
    field_name: str,
    quantum: float,
    unit: bool = False,
    unoriented: bool = False,
) -> list[int]:
    if unit:
        _, quantized = _canonical_direction(
            value,
            field_name=field_name,
            quantum=quantum,
            unoriented=unoriented,
        )
        return quantized
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
    return [_quantize(component, quantum, field_name) for component in values]


def _canonical_direction(
    value: Any,
    *,
    field_name: str,
    quantum: float,
    unoriented: bool,
) -> tuple[list[float], list[int]]:
    """Validate, normalize, and safely quantize one orientation descriptor."""

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
    normalized = _normalized_vector(
        values, field_name=field_name, unoriented=unoriented
    )
    quantized = [
        _quantize(component, quantum, field_name) for component in normalized
    ]
    if all(component == 0 for component in quantized):
        raise GeometryIdentityError(
            "geometry.vector_quantized_zero",
            f"{field_name} cannot become zero during canonicalization",
        )
    return normalized, quantized


def _validate_analytic_boundary_topology(
    surface_type: str,
    boundary_loop_count: int,
    *,
    full_cylinder: bool | None,
) -> None:
    if surface_type == "plane" and boundary_loop_count == 0:
        raise GeometryIdentityError(
            "geometry.surface_topology_inconsistent",
            "a finite positive-area plane requires at least one boundary loop",
        )
    if surface_type == "cone" and boundary_loop_count == 0:
        raise GeometryIdentityError(
            "geometry.surface_topology_inconsistent",
            "a finite conical patch requires at least one boundary loop",
        )
    if surface_type != "cylinder":
        return
    if full_cylinder is True and boundary_loop_count != 2:
        raise GeometryIdentityError(
            "geometry.surface_topology_inconsistent",
            "a full finite cylindrical lateral surface requires two boundary loops",
        )
    if full_cylinder is False and boundary_loop_count == 0:
        raise GeometryIdentityError(
            "geometry.surface_topology_inconsistent",
            "a finite non-full cylindrical patch requires at least one boundary loop",
        )


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
    full_cylinder: bool | None = None
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
        axis_values, axis_q = _canonical_direction(
            descriptors["axis"],
            field_name="axis",
            quantum=policy.direction_quantum,
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
        try:
            axial_offset = math.fsum(
                component * direction
                for component, direction in zip(axis_point_values, axis_values)
            )
            canonical_axis_point = [
                component - axial_offset * direction
                for component, direction in zip(axis_point_values, axis_values)
            ]
        except OverflowError as exc:
            raise GeometryIdentityError(
                "geometry.numeric_non_finite",
                "axis-point canonicalization must remain finite",
            ) from exc
        if not math.isfinite(axial_offset) or not all(
            math.isfinite(component) for component in canonical_axis_point
        ):
            raise GeometryIdentityError(
                "geometry.numeric_non_finite",
                "axis-point canonicalization must remain finite",
            )
        shape = {
            "axis_q": axis_q,
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
        full_cylinder = full_circle
        shape["classification"] = classification
        shape["full_circle"] = full_circle
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
    _validate_analytic_boundary_topology(
        surface_type,
        face.boundary_loop_count,
        full_cylinder=full_cylinder,
    )
    common["surface"] = shape
    analytic = surface_type in _ANALYTIC_SURFACES
    guarded = _contains_representation_guard(common)
    common = _plain_canonical_numbers(common)
    repeated_key = _repeated_feature_signature(common)
    return (
        common,
        surface_type,
        (
            "bounded_representation_guard"
            if guarded
            else ("analytic" if analytic else "bounded_fallback")
        ),
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
    if not _quantized_scalar_close(
        left["area_q"],
        right["area_q"],
        quantum=policy.area_quantum_mm2,
        tolerance=policy.ambiguity_area_mm2,
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
            if not _quantized_scalar_close(
                left_value,
                right_value,
                quantum=policy.angle_quantum_rad,
                tolerance=policy.ambiguity_angle_rad,
            ):
                return False
        elif name.endswith("radius_q") or name == "length_q":
            if not _quantized_scalar_close(
                left_value,
                right_value,
                quantum=policy.length_quantum_mm,
                tolerance=policy.ambiguity_linear_mm,
            ):
                return False
        elif left_value != right_value:
            return False
    return True


def _quantized_scalar_close(
    left: int,
    right: int,
    *,
    quantum: float,
    tolerance: float,
) -> bool:
    difference = abs(left - right)
    return (
        difference * Fraction.from_float(quantum)
        <= Fraction.from_float(tolerance)
    )


def _quantized_vector_close(
    left: Sequence[int],
    right: Sequence[int],
    *,
    quantum: float,
    tolerance: float,
) -> bool:
    squared_distance = sum((a - b) ** 2 for a, b in zip(left, right))
    exact_quantum = Fraction.from_float(quantum)
    exact_tolerance = Fraction.from_float(tolerance)
    return (
        squared_distance * exact_quantum * exact_quantum
        <= exact_tolerance * exact_tolerance
    )


def _quantized_direction_close(
    left: Sequence[int],
    right: Sequence[int],
    *,
    policy: TolerancePolicy,
    unoriented: bool,
) -> bool:
    left_norm_squared = sum(value * value for value in left)
    right_norm_squared = sum(value * value for value in right)
    if left_norm_squared == 0 or right_norm_squared == 0:
        raise GeometryIdentityError(
            "geometry.vector_quantized_zero",
            "canonical direction cannot be zero during ambiguity comparison",
        )
    dot_product = sum(a * b for a, b in zip(left, right))
    if unoriented:
        dot_product = abs(dot_product)
        maximum_angle = math.pi / 2.0
    else:
        maximum_angle = math.pi
    if policy.ambiguity_angle_rad >= maximum_angle:
        return True

    cosine_threshold = Fraction.from_float(
        math.cos(policy.ambiguity_angle_rad)
    )
    if cosine_threshold == 0:
        return dot_product >= 0
    if cosine_threshold > 0 and dot_product <= 0:
        return False
    if cosine_threshold < 0 and dot_product >= 0:
        return True

    left_side = (
        dot_product
        * dot_product
        * cosine_threshold.denominator
        * cosine_threshold.denominator
    )
    right_side = (
        left_norm_squared
        * right_norm_squared
        * cosine_threshold.numerator
        * cosine_threshold.numerator
    )
    if cosine_threshold > 0:
        return left_side >= right_side
    return left_side <= right_side


def _multiset(values: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(Counter(values).items())
    ]
