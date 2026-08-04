"""Focused R5.2 profile, extraction, quality, and worker tests."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import itertools
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mesh.generation as mesh_generation

from app.config import LocalDataConfig
from app.gmsh_coordinator import GmshCoordinationError, GmshExecutionCoordinator
from app.meshing import MeshingService, MeshingServiceError
from ir.schema import MeshSettings
from mesh.artifacts import canonical_quality_bytes, canonical_topology_bytes
from mesh.generation import (
    DEGENERACY_RELATIVE_TOLERANCE,
    MeshGenerationError,
    build_mesh_artifacts,
    physical_degeneracy_tolerance_summary,
    physical_signed_volume_degeneracy_threshold,
)
from mesh.profile import (
    FROZEN_GMSH_TET_V1_MANIFEST_SHA256,
    FROZEN_GMSH_TET_V2_MANIFEST_SHA256,
    FROZEN_GMSH_TET_V3_MANIFEST_SHA256,
    GMSH_TET_V1,
    GMSH_TET_V1_MANIFEST,
    GMSH_TET_V2_MANIFEST,
    GMSH_TET_V3_MANIFEST,
    PHYSICAL_TOLERANCE_FORMULA_ID,
    PROVENANCE_PRODUCER_PREFIX,
    build_provenance_producer,
    ProfileManifestError,
    canonical_profile_manifest_bytes,
    profile_manifest_document,
    profile_manifest_sha256,
    verify_profile_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures"
IDS = [
    "123e4567-e89b-42d3-a456-426614174000",
    "223e4567-e89b-42d3-a456-426614174001",
    "323e4567-e89b-42d3-a456-426614174002",
    "423e4567-e89b-42d3-a456-426614174003",
    "523e4567-e89b-42d3-a456-426614174004",
    "623e4567-e89b-42d3-a456-426614174005",
]
SETUP_CREATED_AT = datetime(2026, 7, 31, 12, 34, 56, tzinfo=timezone.utc)


def settings(size=1.0):
    return MeshSettings.model_validate({
        "global_element_size_mm": size,
        "element_type": "tetrahedral", "element_order": "first_order",
        "mesher": "gmsh", "mesher_preset": "gmsh_tet_v1",
        "target_size_original": {"value": size, "unit": "mm"},
    })


def raw_tetra():
    return {
        "gmsh_version": "4.15.2", "profile_id": "gmsh_tet_v1",
        "profile_version": GMSH_TET_V1.profile_version, "target_size_mm": 1.0,
        "nodes": [
            {"tag": 1, "coordinates": [0.0, 0.0, 0.0]},
            {"tag": 2, "coordinates": [1.0, 0.0, 0.0]},
            {"tag": 3, "coordinates": [0.0, 1.0, 0.0]},
            {"tag": 4, "coordinates": [0.0, 0.0, 1.0]},
        ],
        "tetrahedra": [[1, 2, 3, 4]],
    }


def artifacts(raw=None, *, mesh_settings=None, created_at=SETUP_CREATED_AT):
    return build_mesh_artifacts(
        raw or raw_tetra(), mesh_revision_id=IDS[0], project_id=IDS[1],
        model_id=IDS[2], model_version_id=IDS[3], setup_id=IDS[4],
        setup_revision_id=IDS[5],
        setup_revision_created_at=created_at,
        source_model_sha256="a" * 64,
        settings=mesh_settings or settings(),
    )


def test_profile_is_versioned_explicit_and_target_size_is_bound():
    names = [name for name, _ in GMSH_TET_V1.fixed_options]
    assert GMSH_TET_V1.profile_id == "gmsh_tet_v1"
    assert GMSH_TET_V1.manifest_version == 3
    assert GMSH_TET_V1.profile_version.startswith("3:")
    assert GMSH_TET_V1.resolved_identity.startswith("gmsh_tet_v1@3:")
    assert GMSH_TET_V1.gmsh_version == "4.15.2"
    assert len(names) == len(set(names))
    for required in {
        "Mesh.Algorithm", "Mesh.Algorithm3D", "Mesh.ElementOrder",
        "Mesh.MeshSizeFromPoints", "Mesh.MeshSizeFromCurvature",
        "Mesh.MeshSizeExtendFromBoundary", "Mesh.Optimize",
        "Mesh.RandomSeed", "Mesh.RandomFactor", "Mesh.RandomFactor3D",
        "General.NumThreads", "Mesh.MaxNumThreads3D",
    }:
        assert required in names
    options = dict(GMSH_TET_V1.options(2.5))
    assert options["Mesh.MeshSizeMin"] == options["Mesh.MeshSizeMax"] == 2.5


def test_extraction_incidence_and_existing_quality_formulas():
    topology, quality = artifacts()
    assert len(topology["nodes"]) == 4
    assert len(topology["tetrahedra"]) == 1
    assert len(topology["exterior_triangles"]) == 4
    assert {face["owner_tetrahedron_id"] for face in topology["exterior_triangles"]} == {1}
    assert quality["signed_volume"]["minimum"] == pytest.approx(1 / 6)
    expected_mean_ratio = 12 * (3 / 6) ** (2 / 3) / 9
    assert quality["mean_ratio"]["minimum"] == pytest.approx(expected_mean_ratio)
    assert quality["aspect_ratio"]["maximum"] >= 1.0
    assert quality["status"] == "accepted"


@pytest.mark.parametrize("kind,code", [
    ("inverted", "inverted_elements"),
    ("degenerate", "degenerate_elements"),
    ("unsupported", "unsupported_element_type"),
    ("empty", "empty_mesh"),
])
def test_invalid_element_families_and_volumes_are_rejected(kind, code):
    raw = raw_tetra()
    if kind == "inverted":
        raw["tetrahedra"] = [[2, 1, 3, 4]]
    elif kind == "degenerate":
        raw["nodes"][3]["coordinates"] = [1.0, 1.0, 0.0]
    elif kind == "unsupported":
        raw["tetrahedra"] = [[1, 2, 3, 4, 1]]
    else:
        raw["tetrahedra"] = []
    with pytest.raises(MeshGenerationError, match=code):
        artifacts(raw)


@pytest.mark.parametrize("fixture", ["bracket.step", "plate_hole.step"])
def test_supported_step_workers_are_byte_deterministic(fixture):
    command = [sys.executable, "-m", "app.mesh_worker",
               str(FIXTURES / fixture), "10.0"]
    first = subprocess.run(command, capture_output=True, check=True).stdout
    second = subprocess.run(command, capture_output=True, check=True).stdout
    assert first == second
    response = json.loads(first)
    assert response["status"] == "ok"
    topology, quality = build_mesh_artifacts(
        response["mesh"], mesh_revision_id=IDS[0], project_id=IDS[1],
        model_id=IDS[2], model_version_id=IDS[3], setup_id=IDS[4],
        setup_revision_id=IDS[5], source_model_sha256="a" * 64,
        setup_revision_created_at=SETUP_CREATED_AT,
        settings=settings(10.0),
    )
    assert hashlib.sha256(canonical_topology_bytes(topology)).hexdigest()
    assert hashlib.sha256(canonical_quality_bytes(quality)).hexdigest()
    assert quality["element_count"] == len(topology["tetrahedra"])
    assert quality["status"] == "accepted"
    assert quality["mean_ratio"]["minimum"] > 0.0
    incidence = {}
    for element in topology["tetrahedra"]:
        for face in itertools.combinations(element["node_ids"], 3):
            key = tuple(sorted(face))
            incidence[key] = incidence.get(key, 0) + 1
    assert all(incidence[tuple(face["node_ids"])] == 1
               for face in topology["exterior_triangles"])


def test_shared_parse_mesh_slot_has_bounded_saturation():
    async def scenario():
        coordinator = GmshExecutionCoordinator(
            wait_timeout_seconds=1.0, max_pending=2
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with coordinator.acquire("parse"):
                entered.set()
                await release.wait()

        async def waiter():
            async with coordinator.acquire("mesh"):
                pass

        first = asyncio.create_task(holder())
        await entered.wait()
        second = asyncio.create_task(waiter())
        while coordinator.pending < 2:
            await asyncio.sleep(0)
        with pytest.raises(GmshCoordinationError, match="gmsh_slot_saturated"):
            async with coordinator.acquire("mesh"):
                pass
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())


def test_shared_slot_wait_is_bounded():
    async def scenario():
        coordinator = GmshExecutionCoordinator(
            wait_timeout_seconds=0.01, max_pending=2
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with coordinator.acquire("parse"):
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await entered.wait()
        with pytest.raises(GmshCoordinationError, match="gmsh_slot_timeout"):
            async with coordinator.acquire("mesh"):
                pass
        release.set()
        await task

    asyncio.run(scenario())


@pytest.mark.parametrize("mode,code", [
    ("sleep", "mesh_worker_timeout"),
    ("crash", "mesh_worker_crash"),
    ("malformed", "mesh_worker_response_malformed"),
    ("large", "mesh_worker_response_too_large"),
    ("unavailable", "gmsh_unavailable"),
])
def test_worker_failures_are_typed_and_temporary_directories_clean(tmp_path, mode, code):
    config = LocalDataConfig(
        tmp_path / "data", mesher_timeout_seconds=0.2,
        mesher_output_bytes=1024,
    )
    service = MeshingService(None, GmshExecutionCoordinator(), config)
    service._worker_command_prefix = [
        sys.executable, str(Path(__file__).parent / "fake_mesh_worker.py"), mode,
    ]
    with pytest.raises(MeshingServiceError, match=code):
        asyncio.run(service._run_worker(b"STEP", ".step", 1.0))
    assert list(config.worker_root.iterdir()) == []


def test_malformed_step_has_sanitized_diagnostic_and_cleanup(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    service = MeshingService(None, GmshExecutionCoordinator(), config)
    with pytest.raises(MeshingServiceError) as failure:
        asyncio.run(service._run_worker(
            b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n",
            ".step", 1.0,
        ))
    assert failure.value.code in {
        "invalid_cad", "unsupported_solid_count", "mesh_generation_failed"
    }
    assert str(tmp_path) not in str(failure.value)
    assert list(config.worker_root.iterdir()) == []

def _scaled_tetra(
    scale: float,
    *,
    height_factor: float = 1.0,
    target_size: float = 1.0,
    inverted: bool = False,
):
    raw = raw_tetra()
    raw["target_size_mm"] = target_size
    raw["nodes"] = [
        {"tag": 1, "coordinates": [0.0, 0.0, 0.0]},
        {"tag": 2, "coordinates": [scale, 0.0, 0.0]},
        {"tag": 3, "coordinates": [0.0, scale, 0.0]},
        {"tag": 4, "coordinates": [0.0, 0.0, scale * height_factor]},
    ]
    raw["tetrahedra"] = [
        [2, 1, 3, 4] if inverted else [1, 2, 3, 4]
    ]
    return raw


def test_frozen_profile_versions_and_current_production_resolution():
    manifests = (
        (GMSH_TET_V1_MANIFEST, FROZEN_GMSH_TET_V1_MANIFEST_SHA256),
        (GMSH_TET_V2_MANIFEST, FROZEN_GMSH_TET_V2_MANIFEST_SHA256),
        (GMSH_TET_V3_MANIFEST, FROZEN_GMSH_TET_V3_MANIFEST_SHA256),
    )
    for manifest, frozen_digest in manifests:
        assert profile_manifest_sha256(manifest) == frozen_digest
        assert verify_profile_manifest(manifest) == frozen_digest
    assert len({digest for _, digest in manifests}) == 3
    assert GMSH_TET_V1.manifest is GMSH_TET_V3_MANIFEST
    assert GMSH_TET_V1.manifest_version == 3
    assert GMSH_TET_V1.manifest_sha256 == (
        FROZEN_GMSH_TET_V3_MANIFEST_SHA256
    )
    assert GMSH_TET_V1.profile_version == (
        f"3:{FROZEN_GMSH_TET_V3_MANIFEST_SHA256}"
    )
    assert GMSH_TET_V1.resolved_identity == (
        f"gmsh_tet_v1@3:{FROZEN_GMSH_TET_V3_MANIFEST_SHA256}"
    )


def test_version_2_manifest_exhaustively_declares_material_output_contracts():
    document = profile_manifest_document(GMSH_TET_V2_MANIFEST)
    assert set(document) == {
        "profile_identity",
        "gmsh_execution_contract",
        "topology_output_contract",
        "quality_output_contract",
        "canonical_serialization_contract",
        "provenance_contract",
    }
    identity = document["profile_identity"]
    assert set(identity) == {
        "logical_selector",
        "manifest_version",
        "required_gmsh_version",
        "worker_protocol_version",
        "worker_response_schema_id",
        "worker_response_schema_version",
        "worker_success_response_fields",
        "worker_rejection_response_fields",
        "worker_mesh_payload_fields",
    }
    assert identity == {
        "logical_selector": "gmsh_tet_v1",
        "manifest_version": 2,
        "required_gmsh_version": "4.15.2",
        "worker_protocol_version": 1,
        "worker_response_schema_id": "sim-intent.gmsh-tet-worker-response",
        "worker_response_schema_version": 1,
        "worker_success_response_fields": [
            "protocol_version", "operation", "status", "mesh",
        ],
        "worker_rejection_response_fields": [
            "protocol_version", "operation", "status", "code",
        ],
        "worker_mesh_payload_fields": [
            "gmsh_version",
            "profile_id",
            "profile_version",
            "target_size_mm",
            "nodes",
            "tetrahedra",
        ],
    }
    execution = document["gmsh_execution_contract"]
    assert set(execution) == {
        "configuration_file_rule",
        "element_family_restrictions",
        "first_order_only_restriction",
        "fixed_options",
        "geometry_import_expectations",
        "global_target_size_application_rule",
        "option_application_order_rule",
        "randomization_policy",
        "request_options",
        "thread_policy",
    }
    represented_options = {
        item["name"] for item in execution["fixed_options"]
    } | {item["name"] for item in execution["request_options"]}
    assert represented_options == {
        name for name, _ in GMSH_TET_V1.options(2.5)
    }
    topology = document["topology_output_contract"]
    assert set(topology) == {
        "artifact_schema_version",
        "artifact_type",
        "binding_fields",
        "duplicate_coordinate_rule",
        "empty_or_unsupported_element_rejection_rule",
        "exterior_face_incidence_rule",
        "exterior_triangle_canonicalization_rule",
        "length_unit",
        "negative_zero_rule",
        "node_coordinate_normalization_rule",
        "node_ordering_rule",
        "non_manifold_rejection_rule",
        "tetrahedron_ordering_and_renumbering_rule",
        "tetrahedron_orientation_rule",
    }
    assert topology["artifact_type"] == "sim-intent.mesh-topology.v1"
    assert topology["artifact_schema_version"] == 1
    quality = document["quality_output_contract"]
    assert set(quality) == {
        "artifact_schema_version",
        "artifact_type",
        "degenerate_inverted_classification_rule",
        "mean_ratio_formula",
        "mean_ratio_formula_id",
        "normalized_aspect_ratio_formula",
        "normalized_aspect_ratio_formula_id",
        "numeric_range_policy",
        "percentile_interpolation_convention",
        "percentile_set",
        "poor_but_valid_acceptance_rule",
        "quality_policy_id",
        "quality_policy_version",
        "relative_degeneracy_tolerance",
        "signed_volume_formula",
        "signed_volume_formula_id",
    }
    assert quality["artifact_type"] == "sim-intent.mesh-quality.v1"
    assert quality["artifact_schema_version"] == 1
    assert quality["quality_policy_id"] == "sim-intent.tetra-quality"
    assert quality["quality_policy_version"] == 1
    assert quality["relative_degeneracy_tolerance"] == (
        DEGENERACY_RELATIVE_TOLERANCE
    )
    assert quality["percentile_set"] == {
        "mean_ratio": ["minimum", "p01=0.01", "p05=0.05", "p50=0.50"],
        "normalized_aspect_ratio": [
            "p50=0.50", "p95=0.95", "p99=0.99", "maximum",
        ],
    }
    for formula in (
        "signed_volume_formula_id",
        "mean_ratio_formula_id",
        "normalized_aspect_ratio_formula_id",
    ):
        assert quality[formula].startswith("sim-intent.")
    serialization = document["canonical_serialization_contract"]
    assert set(serialization) == {
        "float_representation_policy",
        "hash_algorithm",
        "key_ordering_rule",
        "line_ending_rule",
        "policy_id",
        "policy_version",
        "sequence_ordering_rule",
        "text_encoding",
        "topology_to_quality_binding_rule",
    }
    assert serialization["policy_id"] == (
        "sim-intent.mesh-artifact-canonical-json"
    )
    assert serialization["hash_algorithm"].startswith("SHA-256")
    provenance = document["provenance_contract"]
    assert set(provenance) == {
        "semantic_role",
        "timestamp_precision_rule",
        "timestamp_source",
        "utc_normalization_rule",
    }
    assert provenance["timestamp_source"] == (
        "exact immutable SetupRevision.created_at"
    )
    assert "UTC" in provenance["utc_normalization_rule"]
    assert "microseconds" in provenance["timestamp_precision_rule"]
    assert "never worker wall-clock" in provenance["semantic_role"]


def _generated_field_paths(document):
    paths = set()

    def visit(value, prefix=""):
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            if isinstance(child, dict):
                visit(child, path)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        visit(item, f"{path}[]")

    visit(document)
    return paths


def test_version_3_generated_output_contract_inventory_is_exhaustive():
    document = profile_manifest_document(GMSH_TET_V3_MANIFEST)
    assert set(document) == {
        "profile_identity",
        "gmsh_execution_contract",
        "topology_output_contract",
        "quality_output_contract",
        "canonical_serialization_contract",
        "provenance_contract",
        "provenance_producer_contract",
        "physical_tolerance_summary_contract",
        "generated_output_field_contracts",
    }
    inventory = document["generated_output_field_contracts"]
    keys = [(item["artifact"], item["field_path"]) for item in inventory]
    assert len(keys) == len(set(keys))
    assert {item["classification"] for item in inventory} == {
        "declared_constant",
        "direct_immutable_input_binding",
        "derived_by_declared_formula",
        "canonical_ordering_output",
        "provenance_field",
    }
    assert all(item["declaration"] for item in inventory)

    topology, quality = artifacts()
    expected_topology = {
        "artifact_type", "schema_version", "mesh_revision_id", "project_id",
        "model_id", "model_version_id", "setup_id", "setup_revision_id",
        "source_model_sha256", "mesh_settings_hash", "mesher_profile_id",
        "mesher_profile_version", "length_unit", "nodes",
        "nodes[].node_id", "nodes[].coordinates", "tetrahedra",
        "tetrahedra[].element_id", "tetrahedra[].node_ids",
        "exterior_triangles", "exterior_triangles[].triangle_id",
        "exterior_triangles[].node_ids",
        "exterior_triangles[].owner_tetrahedron_id", "provenance",
        "provenance.producer", "provenance.created_at",
    }
    expected_quality = {
        "artifact_type", "schema_version", "mesh_revision_id", "project_id",
        "model_id", "model_version_id", "setup_id", "setup_revision_id",
        "source_model_sha256", "mesh_settings_hash", "mesher_profile_id",
        "mesher_profile_version", "topology_artifact_sha256",
        "quality_policy_id", "quality_policy_version", "element_count",
        "status", "rejection_codes", "warnings", "signed_volume",
        "signed_volume.metric", "signed_volume.minimum",
        "signed_volume.non_positive_count",
        "signed_volume.degeneracy_tolerance",
        "signed_volume.tolerance_unit", "signed_volume.definition_version",
        "mean_ratio", "mean_ratio.metric", "mean_ratio.definition",
        "mean_ratio.minimum", "mean_ratio.p01", "mean_ratio.p05",
        "mean_ratio.p50", "aspect_ratio", "aspect_ratio.metric",
        "aspect_ratio.definition", "aspect_ratio.p50", "aspect_ratio.p95",
        "aspect_ratio.p99", "aspect_ratio.maximum", "provenance",
        "provenance.producer", "provenance.created_at",
    }
    inventory_by_artifact = {
        artifact: {
            item["field_path"]
            for item in inventory
            if item["artifact"] == artifact
        }
        for artifact in ("topology", "quality")
    }
    assert inventory_by_artifact == {
        "topology": expected_topology,
        "quality": expected_quality,
    }
    assert _generated_field_paths(topology) == expected_topology
    assert _generated_field_paths(quality) == expected_quality


def test_version_3_provenance_producer_contract_matches_generation():
    contract = profile_manifest_document(GMSH_TET_V3_MANIFEST)[
        "provenance_producer_contract"
    ]
    assert contract == {
        "artifact_application_rule": (
            "topology.provenance.producer and quality.provenance.producer must "
            "both use the one identical constructed producer value"
        ),
        "character_encoding": (
            "Unicode string serialized as UTF-8 by the canonical JSON policy"
        ),
        "construction_rule": (
            "concatenate the exact producer_prefix and the complete resolved "
            "profile identity with no inserted, removed, or replaced character"
        ),
        "normalization_rule": (
            "preserve prefix and resolved identity code points exactly; apply no "
            "case folding, whitespace change, or Unicode normalization"
        ),
        "producer_field_name": "producer",
        "producer_prefix": PROVENANCE_PRODUCER_PREFIX,
        "provenance_object_field_name": "provenance",
        "resolved_identity_format": (
            "<logical-selector>@<profile-version>:<manifest-sha256>"
        ),
        "runtime_content_rule": (
            "append no worker wall-clock, hostname, process, temporary-path, "
            "environment, or other host-derived content"
        ),
    }
    topology, quality = artifacts()
    expected = build_provenance_producer(GMSH_TET_V1.resolved_identity)
    assert expected == PROVENANCE_PRODUCER_PREFIX + GMSH_TET_V1.resolved_identity
    assert topology["provenance"]["producer"] == expected
    assert quality["provenance"]["producer"] == expected
    changed_identity = "gmsh_tet_v1@3:" + "f" * 64
    assert build_provenance_producer(changed_identity) == (
        PROVENANCE_PRODUCER_PREFIX + changed_identity
    )


def test_version_3_physical_tolerance_contract_matches_generation():
    contract = profile_manifest_document(GMSH_TET_V3_MANIFEST)[
        "physical_tolerance_summary_contract"
    ]
    assert contract["summary_object_field_name"] == "signed_volume"
    assert contract["value_field_name"] == "degeneracy_tolerance"
    assert contract["units_field_name"] == "tolerance_unit"
    assert contract["units"] == "mm^3"
    assert contract["unit_semantics"] == "cubic millimetres"
    assert contract["formula_id"] == PHYSICAL_TOLERANCE_FORMULA_ID
    assert "relative_degeneracy_tolerance/6" in contract["formula_definition"]
    assert "maximum" in contract["aggregation_rule"]
    assert "returns 0.0" in contract["empty_element_set_behavior"]
    assert "finite and nonnegative" in contract["finite_value_policy"]
    assert "informational summary only" in contract["acceptance_role"]

    one_threshold = physical_signed_volume_degeneracy_threshold(1.0)
    two_threshold = physical_signed_volume_degeneracy_threshold(2.0)
    assert one_threshold == pytest.approx(DEGENERACY_RELATIVE_TOLERANCE / 6.0)
    assert two_threshold == pytest.approx(
        DEGENERACY_RELATIVE_TOLERANCE / 6.0 * 2.0**3
    )
    topology, quality = artifacts()
    assert topology["tetrahedra"]
    assert quality["signed_volume"]["degeneracy_tolerance"] == pytest.approx(
        one_threshold
    )
    assert quality["signed_volume"]["tolerance_unit"] == "mm^3"
    assert physical_degeneracy_tolerance_summary([]) == 0.0
    assert physical_degeneracy_tolerance_summary(
        [one_threshold, two_threshold]
    ) == two_threshold
    assert math.isfinite(
        quality["signed_volume"]["degeneracy_tolerance"]
    )
    with pytest.raises(MeshGenerationError, match="mesh_numeric_range_failure"):
        physical_degeneracy_tolerance_summary([float("inf")])


def test_physical_tolerance_summary_is_maximum_and_informational(monkeypatch):
    raw = raw_tetra()
    raw["nodes"].extend([
        {"tag": 5, "coordinates": [10.0, 0.0, 0.0]},
        {"tag": 6, "coordinates": [12.0, 0.0, 0.0]},
        {"tag": 7, "coordinates": [10.0, 2.0, 0.0]},
        {"tag": 8, "coordinates": [10.0, 0.0, 2.0]},
    ])
    raw["tetrahedra"].append([5, 6, 7, 8])
    _, quality = artifacts(raw)
    assert quality["signed_volume"]["degeneracy_tolerance"] == pytest.approx(
        physical_signed_volume_degeneracy_threshold(2.0)
    )

    monkeypatch.setattr(
        mesh_generation,
        "physical_signed_volume_degeneracy_threshold",
        lambda _local_scale: 123.0,
    )
    _, informational = artifacts()
    assert informational["status"] == "accepted"
    assert informational["rejection_codes"] == []
    assert informational["signed_volume"]["degeneracy_tolerance"] == 123.0


def _set_nested(document, path, value):
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("gmsh_execution_contract", "fixed_options", 0, "value"), 1.0),
        (("topology_output_contract", "artifact_schema_version"), 2),
        (("quality_output_contract", "artifact_schema_version"), 2),
        (("quality_output_contract", "quality_policy_version"), 2),
        (("quality_output_contract", "signed_volume_formula_id"), "changed-formula"),
        (("quality_output_contract", "mean_ratio_formula_id"), "changed-formula"),
        (("quality_output_contract", "normalized_aspect_ratio_formula_id"), "changed-formula"),
        (("quality_output_contract", "percentile_set", "mean_ratio"), ["p50=0.50"]),
        (("quality_output_contract", "percentile_interpolation_convention"), "nearest"),
        (("quality_output_contract", "relative_degeneracy_tolerance"), 1e-9),
        (("provenance_contract", "timestamp_source"), "worker wall clock"),
        (("provenance_contract", "timestamp_precision_rule"), "milliseconds"),
        (("profile_identity", "worker_response_schema_version"), 2),
        (("canonical_serialization_contract", "policy_id"), "changed-json"),
        (("topology_output_contract", "node_ordering_rule"), "source tag order"),
        (("topology_output_contract", "exterior_face_incidence_rule"), "all faces"),
        (("canonical_serialization_contract", "topology_to_quality_binding_rule"), "unbound"),
    ],
    ids=[
        "gmsh-option",
        "topology-schema",
        "quality-schema",
        "quality-policy",
        "signed-volume-formula",
        "mean-ratio-formula",
        "aspect-ratio-formula",
        "percentiles",
        "interpolation",
        "degeneracy",
        "timestamp-source",
        "timestamp-precision",
        "worker-response-schema",
        "canonical-serialization",
        "node-ordering",
        "exterior-extraction",
        "topology-quality-binding",
    ],
)
def test_every_material_version_2_mutation_changes_digest_and_trips_guard(
    path, replacement
):
    changed = profile_manifest_document(GMSH_TET_V2_MANIFEST)
    _set_nested(changed, path, replacement)
    assert profile_manifest_sha256(changed) != (
        FROZEN_GMSH_TET_V2_MANIFEST_SHA256
    )
    with pytest.raises(ProfileManifestError, match="version bump"):
        verify_profile_manifest(changed)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("provenance_producer_contract", "producer_prefix"), "changed."),
        (("provenance_producer_contract", "construction_rule"), "insert slash"),
        (("provenance_producer_contract", "resolved_identity_format"), "selector:hash"),
        (("provenance_producer_contract", "artifact_application_rule"), "topology only"),
        (("physical_tolerance_summary_contract", "value_field_name"), "other_tolerance"),
        (("physical_tolerance_summary_contract", "units"), "m^3"),
        (("physical_tolerance_summary_contract", "formula_id"), "changed-formula"),
        (("physical_tolerance_summary_contract", "formula_definition"), "target_size_mm cubed"),
        (("physical_tolerance_summary_contract", "aggregation_rule"), "minimum"),
        (("physical_tolerance_summary_contract", "empty_element_set_behavior"), "error"),
        (("physical_tolerance_summary_contract", "finite_value_policy"), "allow infinity"),
        (("physical_tolerance_summary_contract", "acceptance_role"), "reject above threshold"),
    ],
    ids=[
        "producer-prefix",
        "producer-concatenation",
        "resolved-identity-format",
        "producer-artifact-application",
        "physical-field-name",
        "physical-units",
        "physical-formula-id",
        "physical-formula-definition",
        "physical-aggregation",
        "physical-empty-set",
        "physical-finite-policy",
        "physical-acceptance-role",
    ],
)
def test_every_new_version_3_mutation_changes_digest_and_trips_guard(
    path, replacement
):
    changed = profile_manifest_document(GMSH_TET_V3_MANIFEST)
    _set_nested(changed, path, replacement)
    assert profile_manifest_sha256(changed) != (
        FROZEN_GMSH_TET_V3_MANIFEST_SHA256
    )
    with pytest.raises(ProfileManifestError, match="version bump"):
        verify_profile_manifest(changed)


def _reverse_object_insertion_order(value):
    if isinstance(value, dict):
        return {
            key: _reverse_object_insertion_order(value[key])
            for key in reversed(tuple(value))
        }
    if isinstance(value, list):
        return [_reverse_object_insertion_order(item) for item in value]
    return value


def test_manifest_fingerprint_ignores_dictionary_insertion_order():
    document = profile_manifest_document(GMSH_TET_V3_MANIFEST)
    reordered = _reverse_object_insertion_order(document)
    assert reordered == document
    assert canonical_profile_manifest_bytes(reordered) == (
        canonical_profile_manifest_bytes(document)
    )
    assert profile_manifest_sha256(reordered) == (
        FROZEN_GMSH_TET_V3_MANIFEST_SHA256
    )


def test_version_1_manifest_remains_guarded_as_frozen_history():
    changed = profile_manifest_document(GMSH_TET_V1_MANIFEST)
    changed["quality_degeneracy_policy"] += " changed"
    assert profile_manifest_sha256(changed) != (
        FROZEN_GMSH_TET_V1_MANIFEST_SHA256
    )
    with pytest.raises(ProfileManifestError, match="version bump"):
        verify_profile_manifest(changed)


@pytest.mark.parametrize(
    "scale",
    [1e-100, 1.0, 1e100, 1e-107, 5e102],
    ids=[
        "small-valid",
        "unit-valid",
        "large-valid",
        "underflow-prone",
        "overflow-prone",
    ],
)
def test_valid_tetrahedron_classification_is_affine_scale_invariant(scale):
    topology, quality = artifacts(_scaled_tetra(scale))
    assert topology["tetrahedra"]
    assert quality["signed_volume"]["minimum"] > 0.0
    assert quality["mean_ratio"]["minimum"] == pytest.approx(
        artifacts(_scaled_tetra(1.0))[1]["mean_ratio"]["minimum"]
    )


@pytest.mark.parametrize("scale", [1e-100, 1.0, 1e100])
def test_near_degenerate_classification_is_affine_scale_invariant(scale):
    raw = _scaled_tetra(
        scale,
        height_factor=DEGENERACY_RELATIVE_TOLERANCE / 2.0,
    )
    with pytest.raises(MeshGenerationError) as failure:
        artifacts(raw)
    assert failure.value.code == "degenerate_elements"


def test_exactly_degenerate_and_inverted_tetrahedra_are_distinctly_typed():
    degenerate = _scaled_tetra(1e100, height_factor=0.0)
    degenerate["nodes"][3]["coordinates"] = [1e100, 1e100, 0.0]
    with pytest.raises(MeshGenerationError) as failure:
        artifacts(degenerate)
    assert failure.value.code == "degenerate_elements"

    inverted = _scaled_tetra(1e-100, inverted=True)
    with pytest.raises(MeshGenerationError) as failure:
        artifacts(inverted)
    assert failure.value.code == "inverted_elements"


def test_target_size_does_not_control_degeneracy_classification():
    for target_size in (1e-100, 1e100):
        valid = _scaled_tetra(1e-6, target_size=target_size)
        assert artifacts(
            valid, mesh_settings=settings(target_size)
        )[1]["status"] == "accepted"
        near_degenerate = _scaled_tetra(
            1e-6,
            height_factor=DEGENERACY_RELATIVE_TOLERANCE / 2.0,
            target_size=target_size,
        )
        with pytest.raises(MeshGenerationError) as failure:
            artifacts(
                near_degenerate,
                mesh_settings=settings(target_size),
            )
        assert failure.value.code == "degenerate_elements"


def test_unrepresentable_physical_volume_is_typed_numeric_range_failure():
    with pytest.raises(MeshGenerationError) as failure:
        artifacts(_scaled_tetra(2e103))
    assert failure.value.code == "mesh_numeric_range_failure"


def test_extreme_finite_coordinate_subtraction_is_typed_numeric_failure():
    raw = raw_tetra()
    raw["nodes"] = [
        {"tag": 1, "coordinates": [-1e308, 0.0, 0.0]},
        {"tag": 2, "coordinates": [1e308, 0.0, 0.0]},
        {"tag": 3, "coordinates": [-1e308, 1.0, 0.0]},
        {"tag": 4, "coordinates": [-1e308, 0.0, 1.0]},
    ]
    with pytest.raises(MeshGenerationError) as failure:
        artifacts(raw)
    assert failure.value.code == "mesh_numeric_range_failure"


def test_duplicate_coordinates_are_tag_independent_and_stably_rejected():
    first = raw_tetra()
    first["nodes"][3]["coordinates"] = [0.0, 1.0, -0.0]
    second = copy.deepcopy(first)
    second["nodes"][2]["tag"], second["nodes"][3]["tag"] = (
        second["nodes"][3]["tag"],
        second["nodes"][2]["tag"],
    )
    codes = []
    for raw in (first, second):
        with pytest.raises(MeshGenerationError) as failure:
            artifacts(raw)
        codes.append(failure.value.code)
    assert codes == ["duplicate_node_coordinates"] * 2


def test_distinct_coordinate_mesh_is_deterministic_across_source_tag_changes():
    first = raw_tetra()
    second = copy.deepcopy(first)
    tag_map = {1: 40, 2: 30, 3: 20, 4: 10}
    for node in second["nodes"]:
        node["tag"] = tag_map[node["tag"]]
    second["nodes"].reverse()
    second["tetrahedra"] = [
        [tag_map[tag] for tag in first["tetrahedra"][0]]
    ]
    first_topology, first_quality = artifacts(first)
    second_topology, second_quality = artifacts(second)
    assert canonical_topology_bytes(first_topology) == canonical_topology_bytes(
        second_topology
    )
    assert canonical_quality_bytes(first_quality) == canonical_quality_bytes(
        second_quality
    )


def test_provenance_is_the_exact_immutable_setup_revision_timestamp():
    first_topology, first_quality = artifacts()
    assert first_topology["provenance"]["created_at"] == (
        "2026-07-31T12:34:56Z"
    )
    assert first_quality["provenance"] == first_topology["provenance"]
    assert first_topology["mesher_profile_version"] == (
        GMSH_TET_V1.profile_version
    )

    second_timestamp = datetime(
        2026, 8, 1, 9, 8, 7, 123456, tzinfo=timezone.utc
    )
    second_topology, _ = artifacts(created_at=second_timestamp)
    assert second_topology["provenance"]["created_at"] == (
        "2026-08-01T09:08:07.123456Z"
    )
    assert canonical_topology_bytes(first_topology) != canonical_topology_bytes(
        second_topology
    )
