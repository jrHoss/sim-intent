"""Strict, canonical R5.1 mesh topology and quality artifact contracts.

All identifiers in topology records are mesh-local.  In particular, exterior
triangles carry only their owning tetrahedron identifier; CAD and solver
identity domains deliberately do not appear in these schemas.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from itertools import combinations
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

MESH_TOPOLOGY_ARTIFACT_TYPE = "sim-intent.mesh-topology.v1"
MESH_QUALITY_ARTIFACT_TYPE = "sim-intent.mesh-quality.v1"
MESH_ARTIFACT_SCHEMA_VERSION = 1
MESH_MEDIA_TYPE = "application/vnd.sim-intent.mesh+json"
QUALITY_POLICY_ID = "sim-intent.tetra-quality"
QUALITY_POLICY_VERSION = 1
MEAN_RATIO_DEFINITION = "12 × (3V)^(2/3) / Σ(edge_length²)"
ASPECT_RATIO_DEFINITION = (
    "longest-edge/minimum-altitude, normalized so an ideal tetrahedron is 1"
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CanonicalUUID = Annotated[
    StrictStr,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]

PAIR_BINDING_FIELDS = (
    "mesh_revision_id",
    "project_id",
    "model_id",
    "model_version_id",
    "setup_id",
    "setup_revision_id",
)


class MeshArtifactError(ValueError):
    """Typed, sanitized artifact validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    # JSON has arrays rather than tuples, so container normalization is allowed;
    # field shapes, literals, bounds, finiteness, and unknown members remain strict.
    model_config = ConfigDict(extra="forbid", frozen=True)


def _finite(value: float | None, name: str) -> None:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


class MeshProvenance(StrictModel):
    producer: NonEmpty
    created_at: NonEmpty


class MeshNode(StrictModel):
    node_id: PositiveStrictInt
    coordinates: tuple[float, float, float]

    @model_validator(mode="after")
    def finite_coordinates(self) -> "MeshNode":
        if not all(math.isfinite(value) for value in self.coordinates):
            raise ValueError("node coordinates must be finite")
        return self


class Tetrahedron(StrictModel):
    element_id: PositiveStrictInt
    node_ids: tuple[
        PositiveStrictInt,
        PositiveStrictInt,
        PositiveStrictInt,
        PositiveStrictInt,
    ]

    @model_validator(mode="after")
    def distinct_nodes(self) -> "Tetrahedron":
        if any(item <= 0 for item in self.node_ids) or len(set(self.node_ids)) != 4:
            raise ValueError("tetrahedron nodes must be distinct positive IDs")
        return self


class ExteriorTriangle(StrictModel):
    triangle_id: PositiveStrictInt
    node_ids: tuple[PositiveStrictInt, PositiveStrictInt, PositiveStrictInt]
    owner_tetrahedron_id: PositiveStrictInt

    @model_validator(mode="after")
    def distinct_nodes(self) -> "ExteriorTriangle":
        if any(item <= 0 for item in self.node_ids) or len(set(self.node_ids)) != 3:
            raise ValueError("triangle nodes must be distinct positive IDs")
        return self


class MeshTopologyArtifact(StrictModel):
    artifact_type: Literal["sim-intent.mesh-topology.v1"]
    schema_version: StrictInt
    mesh_revision_id: CanonicalUUID
    project_id: CanonicalUUID
    model_id: CanonicalUUID
    model_version_id: CanonicalUUID
    setup_id: CanonicalUUID
    setup_revision_id: CanonicalUUID
    source_model_sha256: Sha256
    mesh_settings_hash: Sha256
    mesher_profile_id: NonEmpty
    mesher_profile_version: NonEmpty
    length_unit: Literal["mm"]
    nodes: tuple[MeshNode, ...] = Field(min_length=4)
    tetrahedra: tuple[Tetrahedron, ...] = Field(min_length=1)
    exterior_triangles: tuple[ExteriorTriangle, ...] = Field(min_length=1)
    provenance: MeshProvenance

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: int) -> int:
        if value != MESH_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported topology schema version")
        return value

    @model_validator(mode="after")
    def valid_topology(self) -> "MeshTopologyArtifact":
        node_ids = [item.node_id for item in self.nodes]
        element_ids = [item.element_id for item in self.tetrahedra]
        triangle_ids = [item.triangle_id for item in self.exterior_triangles]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate node ID")
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("duplicate element ID")
        if len(triangle_ids) != len(set(triangle_ids)):
            raise ValueError("duplicate boundary-triangle ID")
        known_nodes = set(node_ids)
        known_elements = set(element_ids)
        connectivities: set[tuple[int, ...]] = set()
        face_incidence: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for tetrahedron in self.tetrahedra:
            if not set(tetrahedron.node_ids) <= known_nodes:
                raise ValueError("tetrahedron references a missing node")
            canonical = tuple(sorted(tetrahedron.node_ids))
            if canonical in connectivities:
                raise ValueError("duplicate tetrahedron connectivity")
            connectivities.add(canonical)
            for face in combinations(tetrahedron.node_ids, 3):
                face_incidence[tuple(sorted(face))].append(
                    tetrahedron.element_id
                )
        boundary_connectivities: set[tuple[int, int, int]] = set()
        for triangle in self.exterior_triangles:
            if not set(triangle.node_ids) <= known_nodes:
                raise ValueError("triangle references a missing node")
            if triangle.owner_tetrahedron_id not in known_elements:
                raise ValueError("triangle owner tetrahedron does not exist")
            canonical = tuple(sorted(triangle.node_ids))
            if canonical in boundary_connectivities:
                raise ValueError("duplicate boundary-triangle connectivity")
            boundary_connectivities.add(canonical)
            incident_elements = face_incidence.get(canonical, [])
            if not incident_elements:
                raise ValueError("triangle is not a tetrahedral face")
            if len(incident_elements) != 1:
                raise ValueError("exterior triangle is not singly incident")
            if incident_elements[0] != triangle.owner_tetrahedron_id:
                raise ValueError("exterior triangle has the wrong owner")
        return self


class SignedVolumeSummary(StrictModel):
    metric: Literal["signed_tetrahedral_volume"]
    minimum: float
    non_positive_count: NonNegativeStrictInt
    degeneracy_tolerance: float = Field(ge=0)
    tolerance_unit: Literal["mm^3"]
    definition_version: StrictInt

    @field_validator("definition_version")
    @classmethod
    def supported_definition_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported signed-volume definition version")
        return value

    @model_validator(mode="after")
    def finite_values(self) -> "SignedVolumeSummary":
        _finite(self.minimum, "minimum")
        _finite(self.degeneracy_tolerance, "degeneracy_tolerance")
        return self


class MeanRatioSummary(StrictModel):
    metric: Literal["mean_ratio_tetrahedral_quality"]
    definition: Literal["12 × (3V)^(2/3) / Σ(edge_length²)"]
    minimum: float
    p01: float
    p05: float
    p50: float

    @model_validator(mode="after")
    def ordered(self) -> "MeanRatioSummary":
        values = (self.minimum, self.p01, self.p05, self.p50)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("mean-ratio values must be finite")
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("mean-ratio values must be within [0, 1]")
        if not self.minimum <= self.p01 <= self.p05 <= self.p50:
            raise ValueError("mean-ratio percentiles are inconsistent")
        return self


class AspectRatioSummary(StrictModel):
    metric: Literal["normalized_longest_edge_minimum_altitude"]
    definition: Literal[
        "longest-edge/minimum-altitude, normalized so an ideal tetrahedron is 1"
    ]
    p50: float
    p95: float
    p99: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self) -> "AspectRatioSummary":
        values = (self.p50, self.p95, self.p99, self.maximum)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("aspect-ratio values must be finite")
        if not all(value >= 1.0 for value in values):
            raise ValueError("aspect-ratio values must be at least 1")
        if not self.p50 <= self.p95 <= self.p99 <= self.maximum:
            raise ValueError("aspect-ratio percentiles are inconsistent")
        return self


class MeshQualityArtifact(StrictModel):
    artifact_type: Literal["sim-intent.mesh-quality.v1"]
    schema_version: StrictInt
    mesh_revision_id: CanonicalUUID
    project_id: CanonicalUUID
    model_id: CanonicalUUID
    model_version_id: CanonicalUUID
    setup_id: CanonicalUUID
    setup_revision_id: CanonicalUUID
    source_model_sha256: Sha256
    mesh_settings_hash: Sha256
    mesher_profile_id: NonEmpty
    mesher_profile_version: NonEmpty
    topology_artifact_sha256: Sha256
    quality_policy_id: Literal["sim-intent.tetra-quality"]
    quality_policy_version: StrictInt
    element_count: PositiveStrictInt
    status: Literal["accepted", "rejected"]
    rejection_codes: tuple[NonEmpty, ...]
    warnings: tuple[NonEmpty, ...]
    signed_volume: SignedVolumeSummary
    mean_ratio: MeanRatioSummary
    aspect_ratio: AspectRatioSummary
    provenance: MeshProvenance

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: int) -> int:
        if value != MESH_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported quality schema version")
        return value

    @field_validator("quality_policy_version")
    @classmethod
    def supported_policy_version(cls, value: int) -> int:
        if value != QUALITY_POLICY_VERSION:
            raise ValueError("unsupported quality policy version")
        return value

    @model_validator(mode="after")
    def consistent_status(self) -> "MeshQualityArtifact":
        if self.signed_volume.non_positive_count > self.element_count:
            raise ValueError("invalid non-positive element count")
        if self.signed_volume.non_positive_count == 0:
            if self.signed_volume.minimum <= 0.0:
                raise ValueError("positive elements require positive minimum volume")
        elif self.signed_volume.minimum > 0.0:
            raise ValueError("non-positive elements require non-positive minimum volume")
        if self.status == "accepted":
            if self.rejection_codes:
                raise ValueError("accepted quality cannot contain rejection codes")
            if self.signed_volume.non_positive_count != 0:
                raise ValueError("accepted quality cannot contain non-positive elements")
            if self.signed_volume.minimum <= 0.0:
                raise ValueError("accepted quality requires positive minimum volume")
            if self.mean_ratio.minimum <= 0.0:
                raise ValueError("accepted quality requires positive minimum mean ratio")
        elif not self.rejection_codes:
            raise ValueError("rejected quality requires a rejection code")
        return self


def _normalize_floats(value: Any) -> Any:
    """Recursively give every finite floating zero one canonical spelling."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return 0.0 if value == 0.0 else value
    if isinstance(value, dict):
        return {key: _normalize_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_floats(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_floats(item) for item in value]
    return value


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize validated JSON-compatible data with canonical finite floats."""

    normalized = _normalize_floats(payload)
    return (
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_document(artifact: StrictModel) -> bytes:
    payload = artifact.model_dump(mode="json")
    if "nodes" in payload:
        payload["nodes"].sort(key=lambda item: item["node_id"])
        payload["tetrahedra"].sort(key=lambda item: item["element_id"])
        payload["exterior_triangles"].sort(key=lambda item: item["triangle_id"])
    if "rejection_codes" in payload:
        payload["rejection_codes"] = sorted(payload["rejection_codes"])
        payload["warnings"] = sorted(payload["warnings"])
    return canonical_json_bytes(payload)


def canonical_topology_bytes(
    artifact: MeshTopologyArtifact | dict[str, Any],
) -> bytes:
    try:
        validated = (
            artifact
            if isinstance(artifact, MeshTopologyArtifact)
            else MeshTopologyArtifact.model_validate(artifact)
        )
        return _canonical_document(validated)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        raise MeshArtifactError("malformed_mesh_artifact") from exc


def canonical_quality_bytes(
    artifact: MeshQualityArtifact | dict[str, Any],
) -> bytes:
    try:
        validated = (
            artifact
            if isinstance(artifact, MeshQualityArtifact)
            else MeshQualityArtifact.model_validate(artifact)
        )
        return _canonical_document(validated)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        raise MeshArtifactError("malformed_mesh_artifact") from exc


def _load(raw: bytes, model: type[StrictModel], expected_type: str) -> StrictModel:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise MeshArtifactError("malformed_mesh_artifact") from exc
    if not isinstance(payload, dict):
        raise MeshArtifactError("malformed_mesh_artifact")
    if payload.get("artifact_type") != expected_type or payload.get("schema_version") != 1:
        raise MeshArtifactError("unsupported_artifact_version")
    try:
        artifact = model.model_validate(payload)
        canonical = _canonical_document(artifact)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        raise MeshArtifactError("malformed_mesh_artifact") from exc
    if canonical != raw:
        raise MeshArtifactError("mesh_artifact_integrity_failure")
    return artifact


def load_topology_artifact(raw: bytes) -> MeshTopologyArtifact:
    return _load(
        raw, MeshTopologyArtifact, MESH_TOPOLOGY_ARTIFACT_TYPE
    )  # type: ignore[return-value]


def load_quality_artifact(raw: bytes) -> MeshQualityArtifact:
    return _load(
        raw, MeshQualityArtifact, MESH_QUALITY_ARTIFACT_TYPE
    )  # type: ignore[return-value]


def validate_mesh_artifact_pair(
    topology: MeshTopologyArtifact,
    quality: MeshQualityArtifact,
    *,
    topology_sha256: str,
    expected_binding: Mapping[str, str] | None = None,
    expected_source_model_sha256: str | None = None,
    expected_mesh_settings_hash: str | None = None,
    expected_mesher_profile_id: str | None = None,
    expected_mesher_profile_version: str | None = None,
) -> None:
    """Authoritative cross-artifact and durable-record pair validation."""

    for field in PAIR_BINDING_FIELDS:
        topology_value = getattr(topology, field)
        quality_value = getattr(quality, field)
        if topology_value != quality_value:
            raise MeshArtifactError("mesh_ownership_mismatch")
        if expected_binding is not None and topology_value != expected_binding[field]:
            raise MeshArtifactError("mesh_ownership_mismatch")
    if topology.source_model_sha256 != quality.source_model_sha256:
        raise MeshArtifactError("source_hash_mismatch")
    if (
        expected_source_model_sha256 is not None
        and topology.source_model_sha256 != expected_source_model_sha256
    ):
        raise MeshArtifactError("source_hash_mismatch")
    if topology.mesh_settings_hash != quality.mesh_settings_hash:
        raise MeshArtifactError("settings_hash_mismatch")
    if (
        expected_mesh_settings_hash is not None
        and topology.mesh_settings_hash != expected_mesh_settings_hash
    ):
        raise MeshArtifactError("settings_hash_mismatch")
    if topology.mesher_profile_id != quality.mesher_profile_id:
        raise MeshArtifactError("mesh_artifact_integrity_failure")
    if topology.mesher_profile_version != quality.mesher_profile_version:
        raise MeshArtifactError("mesh_artifact_integrity_failure")
    if expected_mesher_profile_id is not None and (
        topology.mesher_profile_id != expected_mesher_profile_id
        or quality.mesher_profile_id != expected_mesher_profile_id
    ):
        raise MeshArtifactError("mesh_artifact_integrity_failure")
    if expected_mesher_profile_version is not None and (
        topology.mesher_profile_version != expected_mesher_profile_version
        or quality.mesher_profile_version != expected_mesher_profile_version
    ):
        raise MeshArtifactError("mesh_artifact_integrity_failure")
    if quality.topology_artifact_sha256 != topology_sha256:
        raise MeshArtifactError("mesh_artifact_integrity_failure")
    if (
        len(topology.nodes) < 4
        or not topology.tetrahedra
        or not topology.exterior_triangles
    ):
        raise MeshArtifactError("malformed_mesh_artifact")
    if quality.element_count != len(topology.tetrahedra):
        raise MeshArtifactError("malformed_mesh_artifact")


def artifact_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
