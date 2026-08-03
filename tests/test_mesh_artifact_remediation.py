"""Regression coverage for rejected R5.1 mesh artifact contracts."""

from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json

import pytest
from pydantic import ValidationError

from mesh.artifacts import (
    MeshArtifactError,
    MeshQualityArtifact,
    MeshTopologyArtifact,
    canonical_json_bytes,
    canonical_quality_bytes,
    canonical_topology_bytes,
)
from tests.test_mesh_artifacts import quality, topology


def _reject_topology(document: dict) -> None:
    with pytest.raises(MeshArtifactError, match="malformed_mesh_artifact"):
        canonical_topology_bytes(document)


def _reject_quality(document: dict) -> None:
    with pytest.raises(MeshArtifactError, match="malformed_mesh_artifact"):
        canonical_quality_bytes(document)


@pytest.mark.parametrize(
    ("name", "nodes", "tetrahedra", "triangles"),
    [
        ("no_nodes", [], None, None),
        ("fewer_than_four_nodes", None, None, None),
        ("no_tetrahedra", None, [], None),
        ("no_exterior_triangles", None, None, []),
    ],
)
def test_empty_or_incomplete_topology_is_rejected(
    name, nodes, tetrahedra, triangles
):
    document = topology()
    if name == "fewer_than_four_nodes":
        document["nodes"] = document["nodes"][:3]
    if nodes is not None:
        document["nodes"] = nodes
    if tetrahedra is not None:
        document["tetrahedra"] = tetrahedra
    if triangles is not None:
        document["exterior_triangles"] = triangles
    _reject_topology(document)


@pytest.mark.parametrize("status", ["accepted", "rejected"])
def test_zero_element_quality_is_rejected_for_every_status(status):
    top_bytes = canonical_topology_bytes(topology())
    document = quality(hashlib.sha256(top_bytes).hexdigest())
    document["element_count"] = 0
    document["status"] = status
    document["rejection_codes"] = [] if status == "accepted" else ["empty_mesh"]
    _reject_quality(document)


@pytest.mark.parametrize(
    ("name", "mutation"),
    [
        (
            "negative_mean_ratio",
            lambda value: value["mean_ratio"].update(minimum=-0.1),
        ),
        (
            "mean_ratio_above_one",
            lambda value: value["mean_ratio"].update(p50=1.01),
        ),
        (
            "aspect_ratio_below_one",
            lambda value: value["aspect_ratio"].update(p50=0.99),
        ),
        (
            "mean_ratio_percentile_order",
            lambda value: value["mean_ratio"].update(p01=0.4),
        ),
        (
            "aspect_ratio_percentile_order",
            lambda value: value["aspect_ratio"].update(p95=1.0),
        ),
        (
            "zero_count_with_zero_minimum_volume",
            lambda value: value["signed_volume"].update(minimum=0.0),
        ),
        (
            "non_positive_count_with_positive_minimum",
            lambda value: (
                value.update(status="rejected", rejection_codes=["inverted"]),
                value["signed_volume"].update(
                    minimum=1.0, non_positive_count=1
                ),
            ),
        ),
        (
            "accepted_inverted_mesh",
            lambda value: value["signed_volume"].update(
                minimum=-1.0, non_positive_count=1
            ),
        ),
        (
            "accepted_degenerate_mesh",
            lambda value: value["signed_volume"].update(
                minimum=0.0, non_positive_count=1
            ),
        ),
        (
            "accepted_with_rejection_codes",
            lambda value: value.update(rejection_codes=["quality_reason"]),
        ),
        (
            "rejected_without_code",
            lambda value: value.update(status="rejected"),
        ),
    ],
)
def test_mathematically_or_status_inconsistent_quality_is_rejected(
    name, mutation
):
    top_bytes = canonical_topology_bytes(topology())
    document = quality(hashlib.sha256(top_bytes).hexdigest())
    mutation(document)
    _reject_quality(document)


def test_rejected_non_volume_reason_can_have_consistent_positive_volume():
    top_bytes = canonical_topology_bytes(topology())
    document = quality(
        hashlib.sha256(top_bytes).hexdigest(),
        status="rejected",
        rejection_codes=["aspect_ratio_policy"],
    )
    assert canonical_quality_bytes(document)


def _strict_integer_topology() -> dict:
    document = topology()
    document["tetrahedra"][0]["element_id"] = 1
    document["exterior_triangles"][0]["triangle_id"] = 1
    document["exterior_triangles"][0]["owner_tetrahedron_id"] = 1
    return document


def _strict_integer_quality(topology_digest: str) -> dict:
    document = quality(
        topology_digest,
        status="rejected",
        rejection_codes=["inverted"],
    )
    document["signed_volume"]["minimum"] = 0.0
    document["signed_volume"]["non_positive_count"] = 1
    return document


def _set_path(document: dict, path: tuple[object, ...], value: object) -> None:
    target: object = document
    for component in path[:-1]:
        target = target[component]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]


STRICT_INTEGER_CASES = [
    ("topology_schema", "topology", ("schema_version",)),
    ("node_id", "topology", ("nodes", 2, "node_id")),
    ("element_id", "topology", ("tetrahedra", 0, "element_id")),
    ("element_connectivity", "topology", ("tetrahedra", 0, "node_ids", 0)),
    ("triangle_id", "topology", ("exterior_triangles", 0, "triangle_id")),
    (
        "triangle_connectivity",
        "topology",
        ("exterior_triangles", 0, "node_ids", 0),
    ),
    (
        "triangle_owner",
        "topology",
        ("exterior_triangles", 0, "owner_tetrahedron_id"),
    ),
    ("quality_schema", "quality", ("schema_version",)),
    ("quality_policy", "quality", ("quality_policy_version",)),
    ("element_count", "quality", ("element_count",)),
    (
        "non_positive_count",
        "quality",
        ("signed_volume", "non_positive_count"),
    ),
    (
        "signed_volume_definition",
        "quality",
        ("signed_volume", "definition_version"),
    ),
]


@pytest.mark.parametrize(
    "coercible_value",
    [True, False, 1.0, "1", Decimal("1")],
    ids=["true", "false", "float", "string", "decimal"],
)
@pytest.mark.parametrize(
    ("name", "artifact_kind", "path"),
    STRICT_INTEGER_CASES,
)
def test_every_integer_category_rejects_coercible_non_integers(
    name, artifact_kind, path, coercible_value
):
    top = _strict_integer_topology()
    top_bytes = canonical_topology_bytes(top)
    if artifact_kind == "topology":
        document = copy.deepcopy(top)
        _set_path(document, path, coercible_value)
        _reject_topology(document)
    else:
        document = _strict_integer_quality(
            hashlib.sha256(top_bytes).hexdigest()
        )
        _set_path(document, path, coercible_value)
        _reject_quality(document)


def _assert_same_bytes_and_hash(first: bytes, second: bytes) -> None:
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


@pytest.mark.parametrize("coordinate_index", [0, 1, 2])
def test_coordinate_signed_zero_has_one_canonical_form(coordinate_index):
    positive = topology()
    negative = copy.deepcopy(positive)
    positive["nodes"][2]["coordinates"][coordinate_index] = 0.0
    negative["nodes"][2]["coordinates"][coordinate_index] = -0.0
    _assert_same_bytes_and_hash(
        canonical_topology_bytes(positive),
        canonical_topology_bytes(negative),
    )


@pytest.mark.parametrize("field", ["minimum", "degeneracy_tolerance"])
def test_signed_volume_signed_zero_has_one_canonical_form(field):
    top_bytes = canonical_topology_bytes(topology())
    digest = hashlib.sha256(top_bytes).hexdigest()
    positive = quality(
        digest, status="rejected", rejection_codes=["volume_or_other"]
    )
    positive["signed_volume"].update(minimum=0.0, non_positive_count=1)
    negative = copy.deepcopy(positive)
    positive["signed_volume"][field] = 0.0
    negative["signed_volume"][field] = -0.0
    _assert_same_bytes_and_hash(
        canonical_quality_bytes(positive),
        canonical_quality_bytes(negative),
    )


@pytest.mark.parametrize("field", ["minimum", "p01", "p05", "p50"])
def test_mean_ratio_signed_zero_has_one_canonical_form(field):
    top_bytes = canonical_topology_bytes(topology())
    digest = hashlib.sha256(top_bytes).hexdigest()
    positive = quality(
        digest, status="rejected", rejection_codes=["non_volume_reason"]
    )
    positive["mean_ratio"].update(minimum=0.0, p01=0.0, p05=0.0, p50=0.0)
    negative = copy.deepcopy(positive)
    positive["mean_ratio"][field] = 0.0
    negative["mean_ratio"][field] = -0.0
    _assert_same_bytes_and_hash(
        canonical_quality_bytes(positive),
        canonical_quality_bytes(negative),
    )


@pytest.mark.parametrize("field", ["p50", "p95", "p99", "maximum"])
def test_shared_serializer_normalizes_signed_zero_in_aspect_ratio_category(field):
    # Aspect-ratio artifact values below one are correctly rejected. Exercise the
    # shared serializer directly to prove this and future categories cannot
    # reintroduce a signed-zero hash distinction.
    positive = {"aspect_ratio": {field: 0.0}}
    negative = {"aspect_ratio": {field: -0.0}}
    _assert_same_bytes_and_hash(
        canonical_json_bytes(positive),
        canonical_json_bytes(negative),
    )


UUID_FIELDS = (
    "mesh_revision_id",
    "project_id",
    "model_id",
    "model_version_id",
    "setup_id",
    "setup_revision_id",
)
CANONICAL_UUID = "123e4567-e89b-42d3-a456-426614174000"
NONCANONICAL_UUIDS = (
    pytest.param("project-1", id="arbitrary-text"),
    pytest.param(CANONICAL_UUID.upper(), id="uppercase"),
    pytest.param("{" + CANONICAL_UUID + "}", id="braced"),
    pytest.param(" " + CANONICAL_UUID + " ", id="whitespace"),
    pytest.param(CANONICAL_UUID.replace("-", ""), id="unhyphenated"),
    pytest.param(CANONICAL_UUID[:-1], id="truncated"),
    pytest.param(CANONICAL_UUID[:-1] + "g", id="invalid-hex"),
    pytest.param(123, id="integer"),
)


def _artifact_document(kind: str) -> tuple[type, dict]:
    top = topology()
    if kind == "topology":
        return MeshTopologyArtifact, top
    top_bytes = canonical_topology_bytes(top)
    return (
        MeshQualityArtifact,
        quality(hashlib.sha256(top_bytes).hexdigest()),
    )


def _construct(model: type, document: dict, mode: str):
    if mode == "python":
        return model(**document)
    return model.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("mode", ["python", "json"])
@pytest.mark.parametrize("kind", ["topology", "quality"])
@pytest.mark.parametrize("field", UUID_FIELDS)
@pytest.mark.parametrize("invalid_uuid", NONCANONICAL_UUIDS)
def test_every_domain_uuid_field_rejects_noncanonical_input(
    invalid_uuid, field, kind, mode
):
    model, document = _artifact_document(kind)
    document[field] = invalid_uuid
    with pytest.raises(ValidationError):
        _construct(model, document, mode)


@pytest.mark.parametrize("mode", ["python", "json"])
@pytest.mark.parametrize("kind", ["topology", "quality"])
@pytest.mark.parametrize("field", UUID_FIELDS)
def test_every_domain_uuid_field_accepts_canonical_round_trip(field, kind, mode):
    model, document = _artifact_document(kind)
    document[field] = CANONICAL_UUID
    artifact = _construct(model, document, mode)
    assert getattr(artifact, field) == CANONICAL_UUID
    round_tripped = model.model_validate_json(artifact.model_dump_json())
    assert getattr(round_tripped, field) == CANONICAL_UUID


def _two_tetrahedra_document() -> dict:
    document = topology()
    document["nodes"].append(
        {"node_id": 5, "coordinates": [0.0, 0.0, -1.0]}
    )
    document["tetrahedra"].append(
        {"element_id": 8, "node_ids": [1, 2, 3, 5]}
    )
    return document


def test_true_singly_incident_exterior_face_is_accepted():
    assert canonical_topology_bytes(topology())


def test_face_shared_by_two_tetrahedra_is_rejected_as_exterior():
    document = _two_tetrahedra_document()
    document["exterior_triangles"][0].update(
        node_ids=[1, 2, 3], owner_tetrahedron_id=7
    )
    _reject_topology(document)


def test_exterior_face_with_wrong_owning_tetrahedron_is_rejected():
    document = _two_tetrahedra_document()
    document["exterior_triangles"][0].update(
        node_ids=[1, 2, 4], owner_tetrahedron_id=8
    )
    _reject_topology(document)


def test_non_tetrahedral_triangle_is_rejected():
    document = _two_tetrahedra_document()
    document["exterior_triangles"][0].update(
        node_ids=[1, 4, 5], owner_tetrahedron_id=7
    )
    _reject_topology(document)


def test_duplicate_canonical_exterior_face_declarations_are_rejected():
    document = topology()
    duplicate = copy.deepcopy(document["exterior_triangles"][0])
    duplicate.update(triangle_id=10, node_ids=[3, 1, 2])
    document["exterior_triangles"].append(duplicate)
    _reject_topology(document)
