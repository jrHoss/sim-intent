"""Focused R5.1 mesh artifact contract tests; no mesher is imported."""

from __future__ import annotations

import copy
import hashlib

import pytest

from mesh.artifacts import (
    MeshArtifactError,
    canonical_quality_bytes,
    canonical_topology_bytes,
    load_quality_artifact,
    load_topology_artifact,
)


MESH_REVISION_ID = "123e4567-e89b-42d3-a456-426614174000"
PROJECT_ID = "223e4567-e89b-42d3-a456-426614174001"
MODEL_ID = "323e4567-e89b-42d3-a456-426614174002"
MODEL_VERSION_ID = "423e4567-e89b-42d3-a456-426614174003"
SETUP_ID = "523e4567-e89b-42d3-a456-426614174004"
SETUP_REVISION_ID = "623e4567-e89b-42d3-a456-426614174005"


def topology(**changes):
    value = {
        "artifact_type": "sim-intent.mesh-topology.v1",
        "schema_version": 1,
        "mesh_revision_id": MESH_REVISION_ID,
        "project_id": PROJECT_ID,
        "model_id": MODEL_ID,
        "model_version_id": MODEL_VERSION_ID,
        "setup_id": SETUP_ID,
        "setup_revision_id": SETUP_REVISION_ID,
        "source_model_sha256": "a" * 64,
        "mesh_settings_hash": "b" * 64,
        "mesher_profile_id": "hand-authored-test",
        "mesher_profile_version": "1",
        "length_unit": "mm",
        "nodes": [
            {"node_id": 4, "coordinates": [0.0, 0.0, 1.0]},
            {"node_id": 2, "coordinates": [1.0, 0.0, 0.0]},
            {"node_id": 1, "coordinates": [0.0, 0.0, 0.0]},
            {"node_id": 3, "coordinates": [0.0, 1.0, 0.0]},
        ],
        "tetrahedra": [{"element_id": 7, "node_ids": [1, 2, 3, 4]}],
        "exterior_triangles": [
            {"triangle_id": 9, "node_ids": [1, 2, 3], "owner_tetrahedron_id": 7}
        ],
        "provenance": {"producer": "test", "created_at": "2026-07-30T00:00:00Z"},
    }
    value.update(changes)
    return value


def quality(topology_sha256: str, **changes):
    value = {
        "artifact_type": "sim-intent.mesh-quality.v1",
        "schema_version": 1,
        "mesh_revision_id": MESH_REVISION_ID,
        "project_id": PROJECT_ID,
        "model_id": MODEL_ID,
        "model_version_id": MODEL_VERSION_ID,
        "setup_id": SETUP_ID,
        "setup_revision_id": SETUP_REVISION_ID,
        "source_model_sha256": "a" * 64,
        "mesh_settings_hash": "b" * 64,
        "mesher_profile_id": "hand-authored-test",
        "mesher_profile_version": "1",
        "topology_artifact_sha256": topology_sha256,
        "quality_policy_id": "sim-intent.tetra-quality",
        "quality_policy_version": 1,
        "element_count": 1,
        "status": "accepted",
        "rejection_codes": [],
        "warnings": ["z-warning", "a-warning"],
        "signed_volume": {"metric": "signed_tetrahedral_volume", "minimum": 1.0, "non_positive_count": 0, "degeneracy_tolerance": 1e-12, "tolerance_unit": "mm^3", "definition_version": 1},
        "mean_ratio": {"metric": "mean_ratio_tetrahedral_quality", "definition": "12 × (3V)^(2/3) / Σ(edge_length²)", "minimum": 0.5, "p01": 0.6, "p05": 0.7, "p50": 0.8},
        "aspect_ratio": {"metric": "normalized_longest_edge_minimum_altitude", "definition": "longest-edge/minimum-altitude, normalized so an ideal tetrahedron is 1", "p50": 1.1, "p95": 1.2, "p99": 1.3, "maximum": 1.4},
        "provenance": {"producer": "test", "created_at": "2026-07-30T00:00:00Z"},
    }
    value.update(changes)
    return value


def test_round_trip_and_order_independent_canonical_hashes():
    first = topology()
    second = copy.deepcopy(first)
    second["nodes"].reverse()
    first_bytes = canonical_topology_bytes(first)
    second_bytes = canonical_topology_bytes(second)
    assert first_bytes == second_bytes
    assert first_bytes.endswith(b"\n")
    assert hashlib.sha256(first_bytes).digest() == hashlib.sha256(second_bytes).digest()
    assert load_topology_artifact(first_bytes).nodes[0].node_id == 1
    quality_bytes = canonical_quality_bytes(quality(hashlib.sha256(first_bytes).hexdigest()))
    assert load_quality_artifact(quality_bytes).warnings == ("a-warning", "z-warning")


@pytest.mark.parametrize("mutation", [
    lambda value: value["nodes"].append(copy.deepcopy(value["nodes"][0])),
    lambda value: value["tetrahedra"].append(copy.deepcopy(value["tetrahedra"][0])),
    lambda value: value["exterior_triangles"].append(copy.deepcopy(value["exterior_triangles"][0])),
    lambda value: value["exterior_triangles"].append({
        **copy.deepcopy(value["exterior_triangles"][0]), "triangle_id": 10
    }),
    lambda value: value["tetrahedra"][0].update(node_ids=[1, 1, 3, 4]),
    lambda value: value["exterior_triangles"][0].update(node_ids=[1, 1, 3]),
    lambda value: value["tetrahedra"][0].update(node_ids=[1, 2, 3, 99]),
    lambda value: value["nodes"][0].update(coordinates=[0.0, 0.0, float("nan")]),
    lambda value: value.update(cad_face_id="forbidden"),
    lambda value: value.update(schema_version=2),
])
def test_invalid_topology_is_rejected(mutation):
    value = topology()
    mutation(value)
    with pytest.raises(MeshArtifactError):
        canonical_topology_bytes(value)


def test_malformed_noncanonical_and_inconsistent_quality_are_rejected():
    with pytest.raises(MeshArtifactError, match="malformed_mesh_artifact"):
        load_topology_artifact(b"{")
    raw = canonical_topology_bytes(topology())
    with pytest.raises(MeshArtifactError, match="mesh_artifact_integrity_failure"):
        load_topology_artifact(raw[:-1] + b" \n")
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(MeshArtifactError):
        canonical_quality_bytes(quality(digest, status="rejected", rejection_codes=[]))
    invalid = quality(digest)
    invalid["mean_ratio"]["p01"] = float("inf")
    with pytest.raises(MeshArtifactError):
        canonical_quality_bytes(invalid)
