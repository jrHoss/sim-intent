"""Authoritative immutable deterministic Gmsh profile for R5.2.

Byte identity is limited to the required Gmsh version on the supported
platform/runtime. Different operating systems, CPU architectures, and
floating-point implementations are explicit non-guarantees.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any


class ProfileManifestError(RuntimeError):
    """A material profile changed without a corresponding version bump."""


@dataclass(frozen=True)
class GmshProfileManifest:
    """Every input that can materially affect deterministic mesh artifacts."""

    logical_profile_name: str
    profile_version: int
    required_gmsh_version: str
    fixed_options: tuple[tuple[str, float], ...]
    request_options: tuple[tuple[str, str], ...]
    option_ordering_canonicalization_rule: str
    global_size_application_rule: str
    output_extraction_expectations: tuple[str, ...]
    element_family_order_restrictions: tuple[str, ...]
    deterministic_ordering_policy: tuple[str, ...]
    quality_degeneracy_policy: str
    artifact_provenance_rule: str
    worker_protocol_version: int


@dataclass(frozen=True)
class ProfileIdentityContract:
    logical_selector: str
    manifest_version: int
    required_gmsh_version: str
    worker_protocol_version: int
    worker_response_schema_id: str
    worker_response_schema_version: int
    worker_success_response_fields: tuple[str, ...]
    worker_rejection_response_fields: tuple[str, ...]
    worker_mesh_payload_fields: tuple[str, ...]


@dataclass(frozen=True)
class GmshExecutionContract:
    fixed_options: tuple[tuple[str, float], ...]
    request_options: tuple[tuple[str, str], ...]
    option_application_order_rule: str
    configuration_file_rule: str
    global_target_size_application_rule: str
    thread_policy: str
    randomization_policy: str
    geometry_import_expectations: tuple[str, ...]
    element_family_restrictions: tuple[str, ...]
    first_order_only_restriction: str


@dataclass(frozen=True)
class TopologyOutputContract:
    artifact_type: str
    artifact_schema_version: int
    length_unit: str
    binding_fields: tuple[str, ...]
    node_coordinate_normalization_rule: str
    negative_zero_rule: str
    duplicate_coordinate_rule: str
    node_ordering_rule: str
    tetrahedron_orientation_rule: str
    tetrahedron_ordering_and_renumbering_rule: str
    exterior_face_incidence_rule: str
    exterior_triangle_canonicalization_rule: str
    non_manifold_rejection_rule: str
    empty_or_unsupported_element_rejection_rule: str


@dataclass(frozen=True)
class QualityOutputContract:
    artifact_type: str
    artifact_schema_version: int
    quality_policy_id: str
    quality_policy_version: int
    signed_volume_formula_id: str
    signed_volume_formula: str
    mean_ratio_formula_id: str
    mean_ratio_formula: str
    normalized_aspect_ratio_formula_id: str
    normalized_aspect_ratio_formula: str
    relative_degeneracy_tolerance: float
    degenerate_inverted_classification_rule: str
    numeric_range_policy: str
    percentile_set: tuple[tuple[str, tuple[str, ...]], ...]
    percentile_interpolation_convention: str
    poor_but_valid_acceptance_rule: str


@dataclass(frozen=True)
class CanonicalSerializationContract:
    policy_id: str
    policy_version: int
    key_ordering_rule: str
    sequence_ordering_rule: str
    float_representation_policy: str
    text_encoding: str
    line_ending_rule: str
    hash_algorithm: str
    topology_to_quality_binding_rule: str


@dataclass(frozen=True)
class ProvenanceContract:
    timestamp_source: str
    utc_normalization_rule: str
    timestamp_precision_rule: str
    semantic_role: str


@dataclass(frozen=True)
class ProvenanceProducerContract:
    provenance_object_field_name: str
    producer_field_name: str
    producer_prefix: str
    construction_rule: str
    resolved_identity_format: str
    artifact_application_rule: str
    character_encoding: str
    normalization_rule: str
    runtime_content_rule: str


@dataclass(frozen=True)
class PhysicalToleranceSummaryContract:
    summary_object_field_name: str
    value_field_name: str
    units_field_name: str
    units: str
    unit_semantics: str
    source: str
    formula_id: str
    formula_definition: str
    normalized_threshold_relationship: str
    aggregation_rule: str
    empty_element_set_behavior: str
    finite_value_policy: str
    canonical_float_serialization_policy: str
    acceptance_role: str


@dataclass(frozen=True)
class GeneratedOutputFieldContract:
    artifact: str
    field_path: str
    classification: str
    declaration: str


@dataclass(frozen=True)
class GmshProfileManifestV2:
    """Structured material output contract for current mesh publication."""

    profile_identity: ProfileIdentityContract
    gmsh_execution_contract: GmshExecutionContract
    topology_output_contract: TopologyOutputContract
    quality_output_contract: QualityOutputContract
    canonical_serialization_contract: CanonicalSerializationContract
    provenance_contract: ProvenanceContract


@dataclass(frozen=True)
class GmshProfileManifestV3:
    """Complete generated-output contract for current mesh publication."""

    profile_identity: ProfileIdentityContract
    gmsh_execution_contract: GmshExecutionContract
    topology_output_contract: TopologyOutputContract
    quality_output_contract: QualityOutputContract
    canonical_serialization_contract: CanonicalSerializationContract
    provenance_contract: ProvenanceContract
    provenance_producer_contract: ProvenanceProducerContract
    physical_tolerance_summary_contract: PhysicalToleranceSummaryContract
    generated_output_field_contracts: tuple[GeneratedOutputFieldContract, ...]


ProfileManifest = (
    GmshProfileManifest | GmshProfileManifestV2 | GmshProfileManifestV3
)


def profile_manifest_document(
    manifest: ProfileManifest | Mapping[str, Any],
) -> dict[str, Any]:
    """Return the canonical JSON-domain representation of one manifest."""

    if isinstance(manifest, GmshProfileManifest):
        return {
            "artifact_provenance_rule": manifest.artifact_provenance_rule,
            "deterministic_ordering_policy": list(
                manifest.deterministic_ordering_policy
            ),
            "element_family_order_restrictions": list(
                manifest.element_family_order_restrictions
            ),
            "fixed_options": [
                {"name": name, "value": value}
                for name, value in manifest.fixed_options
            ],
            "global_size_application_rule": (
                manifest.global_size_application_rule
            ),
            "logical_profile_name": manifest.logical_profile_name,
            "option_ordering_canonicalization_rule": (
                manifest.option_ordering_canonicalization_rule
            ),
            "output_extraction_expectations": list(
                manifest.output_extraction_expectations
            ),
            "profile_version": manifest.profile_version,
            "quality_degeneracy_policy": (
                manifest.quality_degeneracy_policy
            ),
            "request_options": [
                {"name": name, "value_source": value_source}
                for name, value_source in manifest.request_options
            ],
            "required_gmsh_version": manifest.required_gmsh_version,
            "worker_protocol_version": manifest.worker_protocol_version,
        }
    if isinstance(manifest, GmshProfileManifestV2):
        identity = manifest.profile_identity
        execution = manifest.gmsh_execution_contract
        topology = manifest.topology_output_contract
        quality = manifest.quality_output_contract
        serialization = manifest.canonical_serialization_contract
        provenance = manifest.provenance_contract
        return {
            "canonical_serialization_contract": {
                "float_representation_policy": serialization.float_representation_policy,
                "hash_algorithm": serialization.hash_algorithm,
                "key_ordering_rule": serialization.key_ordering_rule,
                "line_ending_rule": serialization.line_ending_rule,
                "policy_id": serialization.policy_id,
                "policy_version": serialization.policy_version,
                "sequence_ordering_rule": serialization.sequence_ordering_rule,
                "text_encoding": serialization.text_encoding,
                "topology_to_quality_binding_rule": (
                    serialization.topology_to_quality_binding_rule
                ),
            },
            "gmsh_execution_contract": {
                "configuration_file_rule": execution.configuration_file_rule,
                "element_family_restrictions": list(
                    execution.element_family_restrictions
                ),
                "first_order_only_restriction": execution.first_order_only_restriction,
                "fixed_options": [
                    {"name": name, "value": value}
                    for name, value in execution.fixed_options
                ],
                "geometry_import_expectations": list(
                    execution.geometry_import_expectations
                ),
                "global_target_size_application_rule": (
                    execution.global_target_size_application_rule
                ),
                "option_application_order_rule": execution.option_application_order_rule,
                "randomization_policy": execution.randomization_policy,
                "request_options": [
                    {"name": name, "value_source": source}
                    for name, source in execution.request_options
                ],
                "thread_policy": execution.thread_policy,
            },
            "profile_identity": {
                "logical_selector": identity.logical_selector,
                "manifest_version": identity.manifest_version,
                "required_gmsh_version": identity.required_gmsh_version,
                "worker_protocol_version": identity.worker_protocol_version,
                "worker_response_schema_id": identity.worker_response_schema_id,
                "worker_response_schema_version": identity.worker_response_schema_version,
                "worker_success_response_fields": list(
                    identity.worker_success_response_fields
                ),
                "worker_rejection_response_fields": list(
                    identity.worker_rejection_response_fields
                ),
                "worker_mesh_payload_fields": list(
                    identity.worker_mesh_payload_fields
                ),
            },
            "provenance_contract": {
                "semantic_role": provenance.semantic_role,
                "timestamp_precision_rule": provenance.timestamp_precision_rule,
                "timestamp_source": provenance.timestamp_source,
                "utc_normalization_rule": provenance.utc_normalization_rule,
            },
            "quality_output_contract": {
                "artifact_schema_version": quality.artifact_schema_version,
                "artifact_type": quality.artifact_type,
                "degenerate_inverted_classification_rule": (
                    quality.degenerate_inverted_classification_rule
                ),
                "mean_ratio_formula": quality.mean_ratio_formula,
                "mean_ratio_formula_id": quality.mean_ratio_formula_id,
                "normalized_aspect_ratio_formula": quality.normalized_aspect_ratio_formula,
                "normalized_aspect_ratio_formula_id": (
                    quality.normalized_aspect_ratio_formula_id
                ),
                "numeric_range_policy": quality.numeric_range_policy,
                "percentile_interpolation_convention": (
                    quality.percentile_interpolation_convention
                ),
                "percentile_set": {
                    metric: list(values) for metric, values in quality.percentile_set
                },
                "poor_but_valid_acceptance_rule": quality.poor_but_valid_acceptance_rule,
                "quality_policy_id": quality.quality_policy_id,
                "quality_policy_version": quality.quality_policy_version,
                "relative_degeneracy_tolerance": quality.relative_degeneracy_tolerance,
                "signed_volume_formula": quality.signed_volume_formula,
                "signed_volume_formula_id": quality.signed_volume_formula_id,
            },
            "topology_output_contract": {
                "artifact_schema_version": topology.artifact_schema_version,
                "artifact_type": topology.artifact_type,
                "binding_fields": list(topology.binding_fields),
                "duplicate_coordinate_rule": topology.duplicate_coordinate_rule,
                "empty_or_unsupported_element_rejection_rule": (
                    topology.empty_or_unsupported_element_rejection_rule
                ),
                "exterior_face_incidence_rule": topology.exterior_face_incidence_rule,
                "exterior_triangle_canonicalization_rule": (
                    topology.exterior_triangle_canonicalization_rule
                ),
                "length_unit": topology.length_unit,
                "negative_zero_rule": topology.negative_zero_rule,
                "node_coordinate_normalization_rule": (
                    topology.node_coordinate_normalization_rule
                ),
                "node_ordering_rule": topology.node_ordering_rule,
                "non_manifold_rejection_rule": topology.non_manifold_rejection_rule,
                "tetrahedron_ordering_and_renumbering_rule": (
                    topology.tetrahedron_ordering_and_renumbering_rule
                ),
                "tetrahedron_orientation_rule": topology.tetrahedron_orientation_rule,
            },
        }
    if isinstance(manifest, GmshProfileManifestV3):
        document = profile_manifest_document(GmshProfileManifestV2(
            profile_identity=manifest.profile_identity,
            gmsh_execution_contract=manifest.gmsh_execution_contract,
            topology_output_contract=manifest.topology_output_contract,
            quality_output_contract=manifest.quality_output_contract,
            canonical_serialization_contract=manifest.canonical_serialization_contract,
            provenance_contract=manifest.provenance_contract,
        ))
        producer = manifest.provenance_producer_contract
        tolerance = manifest.physical_tolerance_summary_contract
        document["provenance_producer_contract"] = {
            "artifact_application_rule": producer.artifact_application_rule,
            "character_encoding": producer.character_encoding,
            "construction_rule": producer.construction_rule,
            "normalization_rule": producer.normalization_rule,
            "producer_field_name": producer.producer_field_name,
            "producer_prefix": producer.producer_prefix,
            "provenance_object_field_name": producer.provenance_object_field_name,
            "resolved_identity_format": producer.resolved_identity_format,
            "runtime_content_rule": producer.runtime_content_rule,
        }
        document["physical_tolerance_summary_contract"] = {
            "acceptance_role": tolerance.acceptance_role,
            "aggregation_rule": tolerance.aggregation_rule,
            "canonical_float_serialization_policy": (
                tolerance.canonical_float_serialization_policy
            ),
            "empty_element_set_behavior": tolerance.empty_element_set_behavior,
            "finite_value_policy": tolerance.finite_value_policy,
            "formula_definition": tolerance.formula_definition,
            "formula_id": tolerance.formula_id,
            "normalized_threshold_relationship": (
                tolerance.normalized_threshold_relationship
            ),
            "source": tolerance.source,
            "summary_object_field_name": tolerance.summary_object_field_name,
            "units": tolerance.units,
            "unit_semantics": tolerance.unit_semantics,
            "units_field_name": tolerance.units_field_name,
            "value_field_name": tolerance.value_field_name,
        }
        document["generated_output_field_contracts"] = [
            {
                "artifact": field.artifact,
                "classification": field.classification,
                "declaration": field.declaration,
                "field_path": field.field_path,
            }
            for field in manifest.generated_output_field_contracts
        ]
        return document
    if isinstance(manifest, Mapping):
        return dict(manifest)
    raise TypeError("profile manifest must be a manifest or mapping")


def canonical_profile_manifest_bytes(
    manifest: ProfileManifest | Mapping[str, Any],
) -> bytes:
    """Serialize a profile manifest with one versioned canonical rule."""

    return (
        json.dumps(
            profile_manifest_document(manifest),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def profile_manifest_sha256(
    manifest: ProfileManifest | Mapping[str, Any],
) -> str:
    return hashlib.sha256(canonical_profile_manifest_bytes(manifest)).hexdigest()


# Mesh size min/max are request-bound below. Equal bounds, with point,
# curvature, and boundary-extension sizing disabled, provide the one global
# R5.2 control. RandomFactor cannot be zero for Gmsh Delaunay predicates, so
# the seed and perturbations are explicit rather than inherited defaults.
GMSH_TET_V1_MANIFEST = GmshProfileManifest(
    logical_profile_name="gmsh_tet_v1",
    profile_version=1,
    required_gmsh_version="4.15.2",
    fixed_options=(
        ("General.Terminal", 0.0),
        ("General.NumThreads", 1.0),
        ("Geometry.OCCScaling", 1.0),
        ("Geometry.OCCFixDegenerated", 0.0),
        ("Geometry.OCCFixSmallEdges", 0.0),
        ("Geometry.OCCFixSmallFaces", 0.0),
        ("Geometry.OCCSewFaces", 0.0),
        ("Geometry.OCCMakeSolids", 0.0),
        ("Mesh.MaxNumThreads1D", 1.0),
        ("Mesh.MaxNumThreads2D", 1.0),
        ("Mesh.MaxNumThreads3D", 1.0),
        ("Mesh.Algorithm", 6.0),
        ("Mesh.Algorithm3D", 1.0),
        ("Mesh.ElementOrder", 1.0),
        ("Mesh.SecondOrderLinear", 0.0),
        ("Mesh.MeshSizeFactor", 1.0),
        ("Mesh.MeshSizeFromPoints", 0.0),
        ("Mesh.MeshSizeFromCurvature", 0.0),
        ("Mesh.MeshSizeExtendFromBoundary", 0.0),
        ("Mesh.Smoothing", 0.0),
        ("Mesh.Optimize", 0.0),
        ("Mesh.OptimizeNetgen", 0.0),
        ("Mesh.HighOrderOptimize", 0.0),
        ("Mesh.RecombineAll", 0.0),
        ("Mesh.RandomSeed", 1.0),
        ("Mesh.RandomFactor", 1e-9),
        ("Mesh.RandomFactor3D", 1e-12),
        ("Mesh.Binary", 0.0),
        ("Mesh.SaveAll", 1.0),
        ("Mesh.MshFileVersion", 4.1),
    ),
    request_options=(
        ("Mesh.MeshSizeMin", "exact global_element_size_mm"),
        ("Mesh.MeshSizeMax", "exact global_element_size_mm"),
    ),
    option_ordering_canonicalization_rule=(
        "apply fixed_options in manifest order, then request_options in "
        "manifest order; canonical JSON sorts object keys and preserves both "
        "option arrays"
    ),
    global_size_application_rule=(
        "set Mesh.MeshSizeMin and Mesh.MeshSizeMax to the same exact positive "
        "finite global_element_size_mm after disabling point, curvature, and "
        "boundary-extension sizing"
    ),
    output_extraction_expectations=(
        "import exactly one OCC volume and synchronize before meshing",
        "extract the complete finite node table and all volume elements",
        "accept only Gmsh type 4 named Tetrahedron 4 with four primary nodes",
        "derive singly incident mesh-local exterior triangles in application code",
        "emit protocol JSON only after suppressing native OCC stdout and stderr",
    ),
    element_family_order_restrictions=(
        "three-dimensional first-order four-node tetrahedra only",
        "no higher-order, mixed-family, recombined, or non-volume elements",
    ),
    deterministic_ordering_policy=(
        "reject distinct source node tags with identical canonical coordinates",
        "sort distinct nodes lexicographically by canonical coordinates",
        "preserve orientation using the lexicographically least even permutation",
        "sort tetrahedral connectivity then assign contiguous element identifiers",
        "sort exterior face connectivity then assign contiguous triangle identifiers",
    ),
    quality_degeneracy_policy=(
        "scale normalized edge vectors by each tetrahedron's maximum absolute "
        "finite edge component; reject absolute normalized determinant <= 1e-12; "
        "version 1 mean-ratio and normalized longest-edge/minimum-altitude metrics"
    ),
    artifact_provenance_rule=(
        "use the selected immutable SetupRevision created_at timestamp as the "
        "deterministic input-lineage provenance epoch"
    ),
    worker_protocol_version=1,
)


# Frozen from the complete immutable version-1 manifest above.
FROZEN_GMSH_TET_V1_MANIFEST_SHA256 = (
    "95fbbaf870e16c7381e24b9c9fd78bffec2ee3a42be7315ec625e042b0d59b7c"
)


GMSH_TET_V2_MANIFEST = GmshProfileManifestV2(
    profile_identity=ProfileIdentityContract(
        logical_selector="gmsh_tet_v1",
        manifest_version=2,
        required_gmsh_version="4.15.2",
        worker_protocol_version=1,
        worker_response_schema_id="sim-intent.gmsh-tet-worker-response",
        worker_response_schema_version=1,
        worker_success_response_fields=(
            "protocol_version", "operation", "status", "mesh",
        ),
        worker_rejection_response_fields=(
            "protocol_version", "operation", "status", "code",
        ),
        worker_mesh_payload_fields=(
            "gmsh_version",
            "profile_id",
            "profile_version",
            "target_size_mm",
            "nodes",
            "tetrahedra",
        ),
    ),
    gmsh_execution_contract=GmshExecutionContract(
        fixed_options=GMSH_TET_V1_MANIFEST.fixed_options,
        request_options=GMSH_TET_V1_MANIFEST.request_options,
        option_application_order_rule=(
            "apply fixed_options in manifest array order, then request_options "
            "in manifest array order; set each option and require exact numeric "
            "read-back equality"
        ),
        configuration_file_rule=(
            "initialize Gmsh with readConfigFiles=false so user and system "
            "gmshrc files cannot modify execution"
        ),
        global_target_size_application_rule=(
            "require one exact positive finite global_element_size_mm; set both "
            "Mesh.MeshSizeMin and Mesh.MeshSizeMax to its float value after "
            "point, curvature, and boundary-extension sizing are disabled"
        ),
        thread_policy=(
            "General.NumThreads and Mesh.MaxNumThreads1D/2D/3D are all fixed "
            "to 1; one application coordinator admits one Gmsh operation"
        ),
        randomization_policy=(
            "Mesh.RandomSeed=1, Mesh.RandomFactor=1e-9, and "
            "Mesh.RandomFactor3D=1e-12; no inherited random defaults"
        ),
        geometry_import_expectations=(
            "import the exact STEP bytes through OCC importShapes",
            "synchronize OCC before entity inspection and meshing",
            "require exactly one three-dimensional OCC volume",
            "generate a three-dimensional mesh and extract the complete finite node table",
        ),
        element_family_restrictions=(
            "accept only Gmsh element type 4 named Tetrahedron 4",
            "require dimension 3, four nodes, and four primary nodes",
            "reject mixed, recombined, non-volume, empty, or unsupported elements",
        ),
        first_order_only_restriction=(
            "Mesh.ElementOrder=1 and extracted element order must equal 1; "
            "higher-order output is rejected"
        ),
    ),
    topology_output_contract=TopologyOutputContract(
        artifact_type="sim-intent.mesh-topology.v1",
        artifact_schema_version=1,
        length_unit="mm",
        binding_fields=(
            "mesh_revision_id",
            "project_id",
            "model_id",
            "model_version_id",
            "setup_id",
            "setup_revision_id",
            "source_model_sha256",
            "mesh_settings_hash",
            "mesher_profile_id",
            "mesher_profile_version",
        ),
        node_coordinate_normalization_rule=(
            "accept only non-boolean JSON integers or floats, convert each to a "
            "finite Python float without rounding, and preserve the exact finite value"
        ),
        negative_zero_rule=(
            "normalize every floating value equal to zero, including -0.0, to +0.0 "
            "before ordering and canonical serialization"
        ),
        duplicate_coordinate_rule=(
            "reject distinct positive source node tags with identical canonical "
            "coordinate triples as duplicate_node_coordinates"
        ),
        node_ordering_rule=(
            "sort distinct nodes lexicographically by canonical (x,y,z), then assign "
            "contiguous one-based node_id values; source tags are not tie-breakers"
        ),
        tetrahedron_orientation_rule=(
            "map source tags to canonical node IDs and choose the lexicographically "
            "least even permutation of each four-node connectivity, preserving orientation"
        ),
        tetrahedron_ordering_and_renumbering_rule=(
            "reject duplicate unordered connectivity, lexicographically sort oriented "
            "connectivities, then assign contiguous one-based element_id values"
        ),
        exterior_face_incidence_rule=(
            "enumerate every three-node tetrahedron combination, sort each face's node "
            "IDs, classify incidence 1 as exterior and incidence 2 as interior"
        ),
        exterior_triangle_canonicalization_rule=(
            "sort exterior (canonical face, owner element_id) pairs lexicographically "
            "and assign contiguous one-based triangle_id values"
        ),
        non_manifold_rejection_rule=(
            "reject any canonical face with incidence greater than 2 and reject an "
            "output with no singly incident exterior face as invalid_exterior_topology"
        ),
        empty_or_unsupported_element_rejection_rule=(
            "reject an empty node or tetrahedron collection; reject non-four-node, "
            "missing-node, repeated-node, higher-order, mixed-family, or unsupported elements"
        ),
    ),
    quality_output_contract=QualityOutputContract(
        artifact_type="sim-intent.mesh-quality.v1",
        artifact_schema_version=1,
        quality_policy_id="sim-intent.tetra-quality",
        quality_policy_version=1,
        signed_volume_formula_id="sim-intent.signed-tetrahedral-volume.det-over-6.v1",
        signed_volume_formula=(
            "V=det(b-a,c-a,d-a)/6; compute on edge vectors divided by their "
            "maximum absolute component and rescale by that component cubed with frexp/ldexp"
        ),
        mean_ratio_formula_id="sim-intent.tetra-mean-ratio.v1",
        mean_ratio_formula="12 × (3V)^(2/3) / Σ(edge_length²)",
        normalized_aspect_ratio_formula_id=(
            "sim-intent.normalized-longest-edge-minimum-altitude.v1"
        ),
        normalized_aspect_ratio_formula=(
            "longest-edge/minimum-altitude × sqrt(2/3), clamped to a minimum of 1"
        ),
        relative_degeneracy_tolerance=1e-12,
        degenerate_inverted_classification_rule=(
            "after common edge-component scaling, zero scale or "
            "abs(determinant)<=1e-12 is degenerate; determinant<-1e-12 is inverted; "
            "otherwise require a finite positive physical volume"
        ),
        numeric_range_policy=(
            "every coordinate, subtraction, normalized component, determinant term, "
            "rescaled volume, squared distance, area, altitude, quality ratio, and "
            "percentile result must be finite; overflow, invalid underflow, or "
            "arithmetic failure rejects as mesh_numeric_range_failure"
        ),
        percentile_set=(
            ("mean_ratio", ("minimum", "p01=0.01", "p05=0.05", "p50=0.50")),
            ("normalized_aspect_ratio", ("p50=0.50", "p95=0.95", "p99=0.99", "maximum")),
        ),
        percentile_interpolation_convention=(
            "sort ascending; position=(n-1)*fraction; if integral select that item, "
            "otherwise linearly interpolate between floor(position) and ceil(position)"
        ),
        poor_but_valid_acceptance_rule=(
            "accept every nonempty mesh whose elements are finite, positive-volume, "
            "nondegenerate, and noninverted; no marginal mean-ratio or aspect-ratio "
            "threshold rejects or warns in quality policy version 1"
        ),
    ),
    canonical_serialization_contract=CanonicalSerializationContract(
        policy_id="sim-intent.mesh-artifact-canonical-json",
        policy_version=1,
        key_ordering_rule=(
            "serialize every JSON object with Python json sort_keys=true and compact "
            "separators ',' and ':'; disallow NaN and infinity"
        ),
        sequence_ordering_rule=(
            "topology nodes, tetrahedra, and exterior triangles sort by their one-based "
            "IDs; quality rejection_codes and warnings sort lexicographically; other arrays "
            "retain declared contract order"
        ),
        float_representation_policy=(
            "recursively normalize every finite floating zero to +0.0, then use the "
            "supported Python runtime's deterministic JSON finite-float representation"
        ),
        text_encoding="UTF-8 with ensure_ascii=false",
        line_ending_rule="append exactly one LF byte after the JSON document",
        hash_algorithm="SHA-256 over the exact canonical artifact bytes",
        topology_to_quality_binding_rule=(
            "quality.topology_artifact_sha256 must equal SHA-256 of the exact canonical "
            "topology bytes, and both artifacts must match every ownership, source, "
            "settings, and mesher-profile binding field"
        ),
    ),
    provenance_contract=ProvenanceContract(
        timestamp_source="exact immutable SetupRevision.created_at",
        utc_normalization_rule=(
            "treat a naive stored datetime as UTC, otherwise convert its instant to UTC, "
            "then replace the +00:00 suffix with Z"
        ),
        timestamp_precision_rule=(
            "use datetime.isoformat timespec='auto': omit fractional seconds only when "
            "microseconds are zero, otherwise emit exactly six fractional digits"
        ),
        semantic_role=(
            "setup-lineage provenance epoch shared by topology and quality; never worker "
            "wall-clock execution time"
        ),
    ),
)


# Frozen independently from the complete immutable version-2 manifest above.
FROZEN_GMSH_TET_V2_MANIFEST_SHA256 = (
    "c2614c3f75ffcd62bcea35005f1e41dd338695e0d68dbd5c29f702ab789f1357"
)


PROVENANCE_OBJECT_FIELD_NAME = "provenance"
PROVENANCE_PRODUCER_FIELD_NAME = "producer"
PROVENANCE_PRODUCER_PREFIX = "sim-intent."
PHYSICAL_TOLERANCE_VALUE_FIELD_NAME = "degeneracy_tolerance"
PHYSICAL_TOLERANCE_UNITS_FIELD_NAME = "tolerance_unit"
PHYSICAL_TOLERANCE_UNITS = "mm^3"
PROVENANCE_RESOLVED_IDENTITY_FORMAT = (
    "<logical-selector>@<profile-version>:<manifest-sha256>"
)
PHYSICAL_TOLERANCE_FORMULA_ID = (
    "sim-intent.physical-signed-volume-degeneracy-threshold.v1"
)


def build_provenance_producer(resolved_profile_identity: str) -> str:
    """Construct the one producer value shared by both generated artifacts."""

    if not isinstance(resolved_profile_identity, str) or not resolved_profile_identity:
        raise ValueError("invalid_resolved_profile_identity")
    return PROVENANCE_PRODUCER_PREFIX + resolved_profile_identity


def _output_field(
    artifact: str,
    field_path: str,
    classification: str,
    declaration: str,
) -> GeneratedOutputFieldContract:
    return GeneratedOutputFieldContract(
        artifact=artifact,
        field_path=field_path,
        classification=classification,
        declaration=declaration,
    )


_GENERATED_OUTPUT_FIELD_CONTRACTS = tuple(
    _output_field(*entry)
    for entry in (
        ("topology", "artifact_type", "declared_constant", "topology_output_contract.artifact_type"),
        ("topology", "schema_version", "declared_constant", "topology_output_contract.artifact_schema_version"),
        ("topology", "mesh_revision_id", "direct_immutable_input_binding", "exact requested MeshRevision identifier"),
        ("topology", "project_id", "direct_immutable_input_binding", "exact selected Project identifier"),
        ("topology", "model_id", "direct_immutable_input_binding", "exact selected Model identifier"),
        ("topology", "model_version_id", "direct_immutable_input_binding", "exact selected ModelVersion identifier"),
        ("topology", "setup_id", "direct_immutable_input_binding", "exact selected Setup identifier"),
        ("topology", "setup_revision_id", "direct_immutable_input_binding", "exact selected SetupRevision identifier"),
        ("topology", "source_model_sha256", "direct_immutable_input_binding", "exact selected ModelVersion source SHA-256"),
        ("topology", "mesh_settings_hash", "derived_by_declared_formula", "SHA-256 of compact sorted-key JSON for exact MeshSettings"),
        ("topology", "mesher_profile_id", "declared_constant", "profile_identity.logical_selector"),
        ("topology", "mesher_profile_version", "derived_by_declared_formula", "<profile-version>:<manifest-sha256> for the current profile"),
        ("topology", "length_unit", "declared_constant", "topology_output_contract.length_unit"),
        ("topology", "nodes", "canonical_ordering_output", "topology_output_contract node normalization, duplicate rejection, and ordering rules"),
        ("topology", "nodes[].node_id", "canonical_ordering_output", "contiguous one-based identifier assigned after canonical node ordering"),
        ("topology", "nodes[].coordinates", "canonical_ordering_output", "exact finite canonical coordinate triple in millimetres"),
        ("topology", "tetrahedra", "canonical_ordering_output", "topology_output_contract tetrahedron orientation, ordering, and rejection rules"),
        ("topology", "tetrahedra[].element_id", "canonical_ordering_output", "contiguous one-based identifier assigned after canonical tetrahedron ordering"),
        ("topology", "tetrahedra[].node_ids", "canonical_ordering_output", "lexicographically least orientation-preserving connectivity"),
        ("topology", "exterior_triangles", "canonical_ordering_output", "topology_output_contract exterior incidence and canonicalization rules"),
        ("topology", "exterior_triangles[].triangle_id", "canonical_ordering_output", "contiguous one-based identifier assigned after canonical exterior ordering"),
        ("topology", "exterior_triangles[].node_ids", "canonical_ordering_output", "ascending mesh-local node IDs for a singly incident face"),
        ("topology", "exterior_triangles[].owner_tetrahedron_id", "derived_by_declared_formula", "element_id of the face's sole incident tetrahedron"),
        ("topology", "provenance", "provenance_field", "provenance_contract and provenance_producer_contract"),
        ("topology", "provenance.producer", "provenance_field", "provenance_producer_contract"),
        ("topology", "provenance.created_at", "provenance_field", "provenance_contract timestamp source and normalization rules"),
        ("quality", "artifact_type", "declared_constant", "quality_output_contract.artifact_type"),
        ("quality", "schema_version", "declared_constant", "quality_output_contract.artifact_schema_version"),
        ("quality", "mesh_revision_id", "direct_immutable_input_binding", "exact requested MeshRevision identifier"),
        ("quality", "project_id", "direct_immutable_input_binding", "exact selected Project identifier"),
        ("quality", "model_id", "direct_immutable_input_binding", "exact selected Model identifier"),
        ("quality", "model_version_id", "direct_immutable_input_binding", "exact selected ModelVersion identifier"),
        ("quality", "setup_id", "direct_immutable_input_binding", "exact selected Setup identifier"),
        ("quality", "setup_revision_id", "direct_immutable_input_binding", "exact selected SetupRevision identifier"),
        ("quality", "source_model_sha256", "direct_immutable_input_binding", "exact selected ModelVersion source SHA-256"),
        ("quality", "mesh_settings_hash", "derived_by_declared_formula", "SHA-256 of compact sorted-key JSON for exact MeshSettings"),
        ("quality", "mesher_profile_id", "declared_constant", "profile_identity.logical_selector"),
        ("quality", "mesher_profile_version", "derived_by_declared_formula", "<profile-version>:<manifest-sha256> for the current profile"),
        ("quality", "topology_artifact_sha256", "derived_by_declared_formula", "canonical_serialization_contract.topology_to_quality_binding_rule"),
        ("quality", "quality_policy_id", "declared_constant", "quality_output_contract.quality_policy_id"),
        ("quality", "quality_policy_version", "declared_constant", "quality_output_contract.quality_policy_version"),
        ("quality", "element_count", "derived_by_declared_formula", "number of accepted canonical tetrahedra"),
        ("quality", "status", "declared_constant", "accepted after all declared generation and quality checks succeed"),
        ("quality", "rejection_codes", "canonical_ordering_output", "empty for generated accepted artifacts; otherwise lexicographically sorted"),
        ("quality", "warnings", "canonical_ordering_output", "empty because poor-but-valid elements are accepted without warnings"),
        ("quality", "signed_volume", "derived_by_declared_formula", "quality_output_contract signed-volume rules and physical_tolerance_summary_contract"),
        ("quality", "signed_volume.metric", "declared_constant", "signed_tetrahedral_volume"),
        ("quality", "signed_volume.minimum", "derived_by_declared_formula", "minimum finite positive physical signed volume over accepted tetrahedra"),
        ("quality", "signed_volume.non_positive_count", "declared_constant", "zero because non-positive tetrahedra are rejected before artifact creation"),
        ("quality", "signed_volume.degeneracy_tolerance", "derived_by_declared_formula", "physical_tolerance_summary_contract"),
        ("quality", "signed_volume.tolerance_unit", "declared_constant", "physical_tolerance_summary_contract.units"),
        ("quality", "signed_volume.definition_version", "declared_constant", "signed-volume artifact definition version 1"),
        ("quality", "mean_ratio", "derived_by_declared_formula", "quality_output_contract mean-ratio formula and percentile rules"),
        ("quality", "mean_ratio.metric", "declared_constant", "mean_ratio_tetrahedral_quality"),
        ("quality", "mean_ratio.definition", "declared_constant", "quality_output_contract.mean_ratio_formula"),
        ("quality", "mean_ratio.minimum", "derived_by_declared_formula", "minimum finite mean ratio over accepted tetrahedra"),
        ("quality", "mean_ratio.p01", "derived_by_declared_formula", "declared linear percentile at fraction 0.01"),
        ("quality", "mean_ratio.p05", "derived_by_declared_formula", "declared linear percentile at fraction 0.05"),
        ("quality", "mean_ratio.p50", "derived_by_declared_formula", "declared linear percentile at fraction 0.50"),
        ("quality", "aspect_ratio", "derived_by_declared_formula", "quality_output_contract normalized-aspect-ratio formula and percentile rules"),
        ("quality", "aspect_ratio.metric", "declared_constant", "normalized_longest_edge_minimum_altitude"),
        ("quality", "aspect_ratio.definition", "declared_constant", "quality_output_contract.normalized_aspect_ratio_formula"),
        ("quality", "aspect_ratio.p50", "derived_by_declared_formula", "declared linear percentile at fraction 0.50"),
        ("quality", "aspect_ratio.p95", "derived_by_declared_formula", "declared linear percentile at fraction 0.95"),
        ("quality", "aspect_ratio.p99", "derived_by_declared_formula", "declared linear percentile at fraction 0.99"),
        ("quality", "aspect_ratio.maximum", "derived_by_declared_formula", "maximum finite normalized aspect ratio over accepted tetrahedra"),
        ("quality", "provenance", "provenance_field", "provenance_contract and provenance_producer_contract"),
        ("quality", "provenance.producer", "provenance_field", "provenance_producer_contract"),
        ("quality", "provenance.created_at", "provenance_field", "provenance_contract timestamp source and normalization rules"),
    )
)


GMSH_TET_V3_MANIFEST = GmshProfileManifestV3(
    profile_identity=replace(
        GMSH_TET_V2_MANIFEST.profile_identity,
        manifest_version=3,
    ),
    gmsh_execution_contract=GMSH_TET_V2_MANIFEST.gmsh_execution_contract,
    topology_output_contract=GMSH_TET_V2_MANIFEST.topology_output_contract,
    quality_output_contract=GMSH_TET_V2_MANIFEST.quality_output_contract,
    canonical_serialization_contract=(
        GMSH_TET_V2_MANIFEST.canonical_serialization_contract
    ),
    provenance_contract=GMSH_TET_V2_MANIFEST.provenance_contract,
    provenance_producer_contract=ProvenanceProducerContract(
        provenance_object_field_name=PROVENANCE_OBJECT_FIELD_NAME,
        producer_field_name=PROVENANCE_PRODUCER_FIELD_NAME,
        producer_prefix=PROVENANCE_PRODUCER_PREFIX,
        construction_rule=(
            "concatenate the exact producer_prefix and the complete resolved "
            "profile identity with no inserted, removed, or replaced character"
        ),
        resolved_identity_format=PROVENANCE_RESOLVED_IDENTITY_FORMAT,
        artifact_application_rule=(
            "topology.provenance.producer and quality.provenance.producer must "
            "both use the one identical constructed producer value"
        ),
        character_encoding="Unicode string serialized as UTF-8 by the canonical JSON policy",
        normalization_rule=(
            "preserve prefix and resolved identity code points exactly; apply no "
            "case folding, whitespace change, or Unicode normalization"
        ),
        runtime_content_rule=(
            "append no worker wall-clock, hostname, process, temporary-path, "
            "environment, or other host-derived content"
        ),
    ),
    physical_tolerance_summary_contract=PhysicalToleranceSummaryContract(
        summary_object_field_name="signed_volume",
        value_field_name=PHYSICAL_TOLERANCE_VALUE_FIELD_NAME,
        units_field_name=PHYSICAL_TOLERANCE_UNITS_FIELD_NAME,
        units=PHYSICAL_TOLERANCE_UNITS,
        unit_semantics="cubic millimetres",
        source=(
            "each accepted tetrahedron's physical signed-volume degeneracy threshold"
        ),
        formula_id=PHYSICAL_TOLERANCE_FORMULA_ID,
        formula_definition=(
            "local_scale_mm=max(abs(component)) over the three finite edge vectors "
            "b-a, c-a, and d-a; threshold_mm3=(relative_degeneracy_tolerance/6) "
            "* local_scale_mm^3, evaluated by the same frexp/ldexp cube-rescaling "
            "policy with underflow-to-positive-zero permitted"
        ),
        normalized_threshold_relationship=(
            "relative_degeneracy_tolerance is the dimensionless absolute normalized "
            "determinant rejection threshold; division by 6 converts determinant "
            "to signed volume before rescaling by local_scale_mm cubed"
        ),
        aggregation_rule=(
            "emit the maximum threshold_mm3 over all accepted tetrahedra"
        ),
        empty_element_set_behavior=(
            "the summary helper returns 0.0 for an empty set, although generation "
            "rejects empty meshes before artifact construction"
        ),
        finite_value_policy=(
            "every per-element value and the aggregate must be finite and nonnegative; "
            "otherwise reject as mesh_numeric_range_failure"
        ),
        canonical_float_serialization_policy=(
            "normalize zero to +0.0 and serialize by canonical_serialization_contract.float_representation_policy"
        ),
        acceptance_role=(
            "informational summary only; it does not independently accept, reject, "
            "warn, or reclassify any tetrahedron or mesh"
        ),
    ),
    generated_output_field_contracts=_GENERATED_OUTPUT_FIELD_CONTRACTS,
)


# Filled from the canonical version-3 manifest and then guarded below.
FROZEN_GMSH_TET_V3_MANIFEST_SHA256 = (
    "80a8bd69b12ac4f132c4231fe7a38dec2dc67d1e6b7f26c8bc5e09b14322a1d5"
)


def verify_profile_manifest(
    manifest: ProfileManifest | Mapping[str, Any],
) -> str:
    """Fail if frozen material changes without a profile-version bump."""

    document = profile_manifest_document(manifest)
    digest = profile_manifest_sha256(document)
    if "profile_identity" in document:
        identity = document["profile_identity"]
        selector = identity.get("logical_selector")
        version = identity.get("manifest_version")
    else:
        selector = document.get("logical_profile_name")
        version = document.get("profile_version")
    expected = {
        ("gmsh_tet_v1", 1): FROZEN_GMSH_TET_V1_MANIFEST_SHA256,
        ("gmsh_tet_v1", 2): FROZEN_GMSH_TET_V2_MANIFEST_SHA256,
        ("gmsh_tet_v1", 3): FROZEN_GMSH_TET_V3_MANIFEST_SHA256,
    }.get((selector, version))
    if expected is not None and digest != expected:
        raise ProfileManifestError(
            "gmsh_tet_v1 manifest changed without a profile-version bump"
        )
    return digest


@dataclass(frozen=True)
class GmshTetProfile:
    manifest: ProfileManifest
    manifest_sha256: str

    @property
    def profile_id(self) -> str:
        if isinstance(self.manifest, (GmshProfileManifestV2, GmshProfileManifestV3)):
            return self.manifest.profile_identity.logical_selector
        return self.manifest.logical_profile_name

    @property
    def manifest_version(self) -> int:
        if isinstance(self.manifest, (GmshProfileManifestV2, GmshProfileManifestV3)):
            return self.manifest.profile_identity.manifest_version
        return self.manifest.profile_version

    @property
    def profile_version(self) -> str:
        """Durable R5.1 binding: logical version plus exact manifest hash."""

        return f"{self.manifest_version}:{self.manifest_sha256}"

    @property
    def resolved_identity(self) -> str:
        return f"{self.profile_id}@{self.profile_version}"

    @property
    def gmsh_version(self) -> str:
        if isinstance(self.manifest, (GmshProfileManifestV2, GmshProfileManifestV3)):
            return self.manifest.profile_identity.required_gmsh_version
        return self.manifest.required_gmsh_version

    @property
    def fixed_options(self) -> tuple[tuple[str, float], ...]:
        if isinstance(self.manifest, (GmshProfileManifestV2, GmshProfileManifestV3)):
            return self.manifest.gmsh_execution_contract.fixed_options
        return self.manifest.fixed_options

    @property
    def request_options(self) -> tuple[tuple[str, str], ...]:
        if isinstance(self.manifest, (GmshProfileManifestV2, GmshProfileManifestV3)):
            return self.manifest.gmsh_execution_contract.request_options
        return self.manifest.request_options

    @property
    def worker_protocol_version(self) -> int:
        if isinstance(self.manifest, (GmshProfileManifestV2, GmshProfileManifestV3)):
            return self.manifest.profile_identity.worker_protocol_version
        return self.manifest.worker_protocol_version

    def options(self, target_size_mm: float) -> tuple[tuple[str, float], ...]:
        if isinstance(target_size_mm, bool) or not isinstance(
            target_size_mm, (int, float)
        ):
            raise ValueError("invalid_mesh_settings")
        try:
            target = float(target_size_mm)
        except (OverflowError, ArithmeticError) as exc:
            raise ValueError("invalid_mesh_settings") from exc
        if not math.isfinite(target) or target <= 0.0:
            raise ValueError("invalid_mesh_settings")
        return self.fixed_options + tuple(
            (name, target) for name, _ in self.request_options
        )


_V1_MANIFEST_DIGEST = verify_profile_manifest(GMSH_TET_V1_MANIFEST)
_V2_MANIFEST_DIGEST = verify_profile_manifest(GMSH_TET_V2_MANIFEST)
_V3_MANIFEST_DIGEST = verify_profile_manifest(GMSH_TET_V3_MANIFEST)

# The logical selector retains its established name; version 3 is the sole
# current production resolution used for all new R5.2 generation/publication.
GMSH_TET_V1 = GmshTetProfile(
    manifest=GMSH_TET_V3_MANIFEST,
    manifest_sha256=_V3_MANIFEST_DIGEST,
)


def apply_profile(gmsh_module, target_size_mm: float) -> None:
    """Apply and read back every option; never pretend one succeeded."""

    if getattr(gmsh_module, "__version__", None) != GMSH_TET_V1.gmsh_version:
        raise ValueError("gmsh_version_unsupported")
    for name, value in GMSH_TET_V1.options(target_size_mm):
        try:
            gmsh_module.option.setNumber(name, value)
            observed = float(gmsh_module.option.getNumber(name))
        except Exception as exc:
            raise ValueError("gmsh_profile_option_unsupported") from exc
        if not math.isclose(observed, value, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("gmsh_profile_option_unsupported")
