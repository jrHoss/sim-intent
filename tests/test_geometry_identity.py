"""R4a deterministic geometry-identity core regression matrix."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from geom.cylinders import analyze_cylinders
from geom.identity import (
    DEFAULT_TOLERANCE_POLICY,
    GEOMETRY_IDENTITY_SCHEMA_VERSION,
    HASH_DOMAIN,
    GeometryFaceInput,
    GeometryIdentityError,
    TolerancePolicy,
    build_geometry_identity,
    canonical_json_bytes,
    faces_from_inventory,
    semantically_equivalent,
    stable_hash,
)
from geom.inventory import FaceInventory, file_sha256
from geom.parser import parse_step

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_HASH = "a" * 64
MODEL_VERSION = "model-version-r4a"
POLICY_NUMERIC_FIELDS = (
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
SEMANTIC_TO_AMBIGUITY_FIELD = {
    "semantic_linear_mm": "ambiguity_linear_mm",
    "semantic_area_mm2": "ambiguity_area_mm2",
    "semantic_angle_rad": "ambiguity_angle_rad",
}


def plane(
    ref,
    *,
    centroid=(0.0, 0.0, 0.0),
    normal=(0.0, 0.0, 1.0),
    area=10.0,
    adjacent=(),
    loops=1,
):
    return GeometryFaceInput(
        source_ref=ref,
        surface_type="Plane",
        area=area,
        centroid=centroid,
        normal=normal,
        boundary_loop_count=loops,
        adjacent_refs=adjacent,
    )


def cylinder(
    ref,
    *,
    centroid=(0.0, 0.0, 0.0),
    axis_point=(0.0, 0.0, 0.0),
    radius=2.0,
    adjacent=(),
    classification="hole",
):
    return GeometryFaceInput(
        source_ref=ref,
        surface_type="Cylinder",
        area=20.0,
        centroid=centroid,
        normal=(1.0, 0.0, 0.0),  # sampled normal is intentionally non-semantic
        boundary_loop_count=2,
        adjacent_refs=adjacent,
        descriptors={
            "axis": [0.0, 0.0, 1.0],
            "axis_point": axis_point,
            "radius": radius,
            "length": 5.0,
            "angular_extent": 2.0 * math.pi,
            "classification": classification,
            "full_circle": True,
        },
    )


def partial_cylinder(ref, *, loops=1, axis=(0.0, 0.0, 1.0)):
    face = cylinder(ref)
    descriptors = dict(face.descriptors)
    descriptors.update(
        {
            "axis": axis,
            "angular_extent": math.pi,
            "classification": "fillet_partial",
            "full_circle": False,
        }
    )
    return replace(
        face,
        boundary_loop_count=loops,
        descriptors=descriptors,
    )


def cone(ref, *, loops=1, axis=(0.0, 0.0, 1.0)):
    return GeometryFaceInput(
        source_ref=ref,
        surface_type="Cone",
        area=12.0,
        centroid=(0.0, 0.0, 0.0),
        boundary_loop_count=loops,
        descriptors={
            "axis": axis,
            "apex": (0.0, 0.0, 0.0),
            "semi_angle": 0.25,
        },
    )


def torus(ref, *, loops=0, axis=(0.0, 0.0, 1.0)):
    return GeometryFaceInput(
        source_ref=ref,
        surface_type="Torus",
        area=12.0,
        centroid=(0.0, 0.0, 0.0),
        boundary_loop_count=loops,
        descriptors={
            "center": (0.0, 0.0, 0.0),
            "axis": axis,
            "major_radius": 6.0,
            "minor_radius": 2.0,
        },
    )


def policy_with_numeric_field(field_name, value):
    values = {field_name: value}
    ambiguity_field = SEMANTIC_TO_AMBIGUITY_FIELD.get(field_name)
    if ambiguity_field is not None:
        values[ambiguity_field] = value
    return TolerancePolicy(**values)


def artifact(faces, **overrides):
    return build_geometry_identity(
        model_version_id=overrides.get("model_version_id", MODEL_VERSION),
        source_sha256=overrides.get("source_sha256", SOURCE_HASH),
        faces=faces,
    )


def by_ref(result):
    return {face.source_ref: face for face in result.faces}


def test_repeated_identical_processing_is_byte_and_hash_identical():
    faces = [
        plane(3, centroid=(0, 0, 0), adjacent=(9,)),
        plane(9, centroid=(0, 0, 1), normal=(0, 0, -1), adjacent=(3,)),
    ]
    first = artifact(faces)
    second = artifact(faces)

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.to_dict()["schema_version"] == GEOMETRY_IDENTITY_SCHEMA_VERSION
    assert first.to_dict()["hash_domain"] == HASH_DOMAIN


def test_face_and_adjacency_order_permutations_are_canonical():
    ordered = [
        plane("left", centroid=(-1, 0, 0), adjacent=("middle",)),
        plane(
            "middle",
            centroid=(0, 0, 0),
            adjacent=("left", "right"),
            normal=(0, 1, 0),
        ),
        plane("right", centroid=(1, 0, 0), adjacent=("middle",)),
    ]
    permuted = [
        replace(ordered[2], adjacent_refs=("middle",)),
        replace(ordered[1], adjacent_refs=("right", "left")),
        replace(ordered[0], adjacent_refs=("middle",)),
    ]

    assert artifact(ordered).canonical_bytes() == artifact(permuted).canonical_bytes()


def test_equivalent_descriptor_dictionary_order_does_not_change_identity():
    original = cylinder(1)
    reversed_descriptors = dict(reversed(list(original.descriptors.items())))
    reordered = replace(original, descriptors=reversed_descriptors)

    assert artifact([original]).canonical_bytes() == artifact([reordered]).canonical_bytes()


def test_source_local_references_and_model_provenance_do_not_change_semantic_identity():
    first = artifact([plane(1)])
    renumbered = artifact(
        [plane(999)],
        model_version_id="another-model-version",
        source_sha256="b" * 64,
    )

    assert first.faces[0].identity_candidate == renumbered.faces[0].identity_candidate
    assert first.faces[0].stable_identity == renumbered.faces[0].stable_identity
    assert first.artifact_sha256 != renumbered.artifact_sha256


@pytest.mark.parametrize(
    ("surface_type", "normal", "descriptors", "bbox"),
    [
        ("Plane", (0, 0, 1), {}, None),
        (
            "Cylinder",
            None,
            {
                "axis": [0, 0, -1],
                "axis_point": [0, 0, 8],
                "radius": 3,
                "length": 5,
                "angular_extent": 2.0 * math.pi,
                "classification": "hole",
                "full_circle": True,
            },
            None,
        ),
        (
            "Cone",
            None,
            {"axis": [0, 0, 1], "apex": [0, 0, 0], "semi_angle": 0.25},
            None,
        ),
        ("Sphere", None, {"center": [0, 0, 0], "radius": 4}, None),
        (
            "Torus",
            None,
            {
                "center": [0, 0, 0],
                "axis": [0, 1, 0],
                "major_radius": 6,
                "minor_radius": 2,
            },
            None,
        ),
        (
            "BSpline surface",
            None,
            {"degree_u": 3, "degree_v": 2, "rational": False},
            ((-1, -2, -3), (1, 2, 3)),
        ),
        (
            "Bezier surface",
            None,
            {"degree_u": 3, "degree_v": 3},
            ((0, 0, 0), (2, 2, 1)),
        ),
    ],
)
def test_every_supported_surface_has_a_versioned_canonical_descriptor(
    surface_type, normal, descriptors, bbox
):
    face = GeometryFaceInput(
        source_ref=surface_type,
        surface_type=surface_type,
        area=12.0,
        centroid=[0.0, 0.0, 0.0],
        normal=normal,
        boundary_loop_count=(
            0
            if surface_type in {"Sphere", "Torus"}
            else (2 if surface_type == "Cylinder" else 1)
        ),
        descriptors=descriptors,
        bbox_min=None if bbox is None else bbox[0],
        bbox_max=None if bbox is None else bbox[1],
    )

    result = artifact([face]).faces[0]
    assert result.canonical_geometry["surface_type"]
    assert all(
        not isinstance(value, float)
        for value in result.canonical_geometry.values()
    )
    if "surface" in surface_type.lower() and surface_type != "Plane":
        assert result.identity_quality == "bounded_fallback"
        assert result.ambiguous
        assert result.stable_identity is None
    else:
        assert result.identity_quality == "analytic"


def test_cylinder_axis_sign_and_point_along_axis_are_canonical():
    first = cylinder(1, axis_point=(4, 5, 6))
    descriptors = dict(first.descriptors)
    descriptors["axis"] = [0, 0, -1]
    descriptors["axis_point"] = [4, 5, 100]
    equivalent = replace(first, descriptors=descriptors)

    assert (
        artifact([first]).faces[0].identity_candidate
        == artifact([equivalent]).faces[0].identity_candidate
    )


def test_sampled_cylinder_normal_does_not_leak_parametric_traversal_order():
    first = cylinder(1)
    other_midpoint = replace(first, normal=(-1.0, 0.0, 0.0))

    assert artifact([first]).canonical_bytes() == artifact([other_midpoint]).canonical_bytes()


def test_geometrically_identical_faces_with_different_neighborhoods_are_distinct():
    faces = [
        plane("a", adjacent=("plane-neighbor",)),
        plane("b", adjacent=("cylinder-neighbor",)),
        plane(
            "plane-neighbor",
            centroid=(0, 2, 0),
            normal=(0, 1, 0),
            adjacent=("a", "cylinder-neighbor"),
        ),
        cylinder(
            "cylinder-neighbor",
            centroid=(0, 3, 0),
            adjacent=("b", "plane-neighbor"),
            classification="boss",
        ),
    ]
    result = by_ref(artifact(faces))

    assert result["a"].canonical_geometry == result["b"].canonical_geometry
    assert result["a"].local_semantic_signature != result["b"].local_semantic_signature
    assert result["a"].stable_identity != result["b"].stable_identity


def test_genuinely_symmetric_faces_share_truthful_collision_group():
    faces = [
        plane("left", adjacent=("center",)),
        plane("right", adjacent=("center",)),
        plane(
            "center",
            centroid=(0, 1, 0),
            normal=(0, 1, 0),
            adjacent=("left", "right"),
        ),
    ]
    result = artifact(faces)
    identities = by_ref(result)

    assert identities["left"].identity_candidate == identities["right"].identity_candidate
    assert identities["left"].stable_identity is None
    assert identities["right"].stable_identity is None
    assert identities["left"].collision_group_id == identities["right"].collision_group_id
    assert identities["left"].ambiguous and identities["right"].ambiguous
    assert len(result.collision_groups) == 1
    assert result.collision_groups[0]["member_source_refs"] == ["left", "right"]


def test_near_symmetric_faces_inside_ambiguity_tolerance_refuse_false_uniqueness():
    faces = [
        plane("left", centroid=(0, 0, 0), adjacent=("center",)),
        plane("right", centroid=(1.4e-6, 0, 0), adjacent=("center",)),
        plane(
            "center",
            centroid=(0, 1, 0),
            normal=(0, 1, 0),
            adjacent=("left", "right"),
        ),
    ]
    result = artifact(faces)
    identities = by_ref(result)

    assert identities["left"].identity_candidate != identities["right"].identity_candidate
    assert identities["left"].stable_identity is None
    assert identities["right"].stable_identity is None
    assert identities["left"].collision_group_id == identities["right"].collision_group_id
    assert result.collision_groups[0]["reason"] == "within_declared_ambiguity_tolerance"


def test_mirrored_planar_orientation_is_not_false_equivalence():
    upward = artifact([plane(1, normal=(0, 0, 1))]).faces[0]
    downward = artifact([plane(1, normal=(0, 0, -1))]).faces[0]

    assert upward.identity_candidate != downward.identity_candidate


def test_repeated_bolt_holes_publish_group_evidence_without_parser_tie_breaks():
    faces = [
        cylinder(
            11,
            centroid=(-10, 0, 5),
            axis_point=(-10, 0, 0),
            adjacent=(5, 8),
        ),
        cylinder(
            12,
            centroid=(10, 0, 5),
            axis_point=(10, 0, 0),
            adjacent=(8, 5),
        ),
        plane(5, centroid=(0, 0, 10), adjacent=(11, 12), loops=3),
        plane(
            8,
            centroid=(0, 0, 0),
            normal=(0, 0, -1),
            adjacent=(12, 11),
            loops=3,
        ),
    ]
    result = by_ref(artifact(faces))

    assert result[11].evidence["repeated_feature_group_size"] == 2
    assert (
        result[11].evidence["repeated_feature_signature"]
        == result[12].evidence["repeated_feature_signature"]
    )
    assert result[11].stable_identity != result[12].stable_identity


def test_semantic_and_ambiguity_tolerances_are_explicit_and_inclusive():
    policy = DEFAULT_TOLERANCE_POLICY
    assert semantically_equivalent(0.0, policy.semantic_linear_mm)
    assert not semantically_equivalent(
        0.0, math.nextafter(policy.semantic_linear_mm, math.inf)
    )
    assert semantically_equivalent(
        0.0, policy.ambiguity_linear_mm, ambiguity=True
    )
    assert not semantically_equivalent(
        0.0,
        math.nextafter(policy.ambiguity_linear_mm, math.inf),
        ambiguity=True,
    )


def test_inverted_ambiguity_policy_is_rejected_before_false_uniqueness():
    inverted = replace(
        DEFAULT_TOLERANCE_POLICY,
        ambiguity_linear_mm=DEFAULT_TOLERANCE_POLICY.semantic_linear_mm / 2,
    )
    with pytest.raises(GeometryIdentityError) as caught:
        build_geometry_identity(
            model_version_id=MODEL_VERSION,
            source_sha256=SOURCE_HASH,
            faces=[plane(1)],
            tolerance_policy=inverted,
        )
    assert caught.value.code == "geometry.tolerance_policy_invalid"
    boundary_policy = TolerancePolicy(
        ambiguity_linear_mm=DEFAULT_TOLERANCE_POLICY.semantic_linear_mm
    )
    faces = [
        plane("left", centroid=(0, 0, 0), adjacent=("center",)),
        plane("right", centroid=(1.0e-6, 0, 0), adjacent=("center",)),
        plane(
            "center",
            centroid=(0, 1, 0),
            normal=(0, 1, 0),
            adjacent=("left", "right"),
        ),
    ]
    result = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=SOURCE_HASH,
        faces=faces,
        tolerance_policy=boundary_policy,
    )
    identities = by_ref(result)
    assert identities["left"].stable_identity is None
    assert identities["right"].stable_identity is None
    assert identities["left"].collision_group_id == identities["right"].collision_group_id


def test_values_safely_inside_quantum_match_and_outside_semantic_tolerance_do_not():
    base = artifact([plane(1, centroid=(10.0, 0, 0))]).faces[0]
    inside = artifact([plane(1, centroid=(10.0 + 0.4e-6, 0, 0))]).faces[0]
    outside = artifact([plane(1, centroid=(10.0 + 1.1e-6, 0, 0))]).faces[0]

    assert base.identity_candidate == inside.identity_candidate
    assert base.identity_candidate != outside.identity_candidate


@pytest.mark.parametrize("boundary", [0.5e-6, -0.5e-6])
def test_r4a_001_nextafter_quantum_boundary_noise_has_bounded_identity(boundary):
    immediately_below = artifact(
        [
            plane(
                1,
                centroid=(math.nextafter(boundary, -math.inf), 0.0, 0.0),
            )
        ]
    )
    immediately_above = artifact(
        [
            plane(
                1,
                centroid=(math.nextafter(boundary, math.inf), 0.0, 0.0),
            )
        ]
    )

    below_face = immediately_below.faces[0]
    above_face = immediately_above.faces[0]
    assert below_face.canonical_geometry == above_face.canonical_geometry
    assert below_face.identity_candidate == above_face.identity_candidate
    assert below_face.stable_identity is None
    assert above_face.stable_identity is None
    assert below_face.identity_quality == "bounded_representation_guard"
    assert above_face.identity_quality == "bounded_representation_guard"
    assert below_face.evidence["representation_noise_guard"] == {
        "policy": "two_input_ulps_at_half_quantum",
        "stable_identity_withheld": True,
    }
    assert (
        below_face.evidence["representation_noise_guard"]
        == above_face.evidence["representation_noise_guard"]
    )
    assert (
        immediately_below.collision_groups[0]["reason"]
        == immediately_above.collision_groups[0]["reason"]
        == "representation_noise_guard_requires_confirmation"
    )


@pytest.mark.parametrize("boundary", [0.5e-6, -0.5e-6])
def test_r4a_001_values_outside_representation_guard_remain_distinguishable(boundary):
    lower = boundary
    upper = boundary
    for _ in range(4):
        lower = math.nextafter(lower, -math.inf)
        upper = math.nextafter(upper, math.inf)

    lower_face = artifact([plane(1, centroid=(lower, 0.0, 0.0))]).faces[0]
    upper_face = artifact([plane(1, centroid=(upper, 0.0, 0.0))]).faces[0]

    assert lower_face.identity_candidate != upper_face.identity_candidate
    assert lower_face.stable_identity is not None
    assert upper_face.stable_identity is not None
    assert "representation_noise_guard" not in lower_face.evidence
    assert "representation_noise_guard" not in upper_face.evidence


def test_r4a_002_oversized_integer_has_sanitized_numeric_failure():
    failures = []
    for _ in range(2):
        with pytest.raises(GeometryIdentityError) as caught:
            artifact([plane(1, area=10**400)])
        failures.append((caught.value.code, str(caught.value)))

    assert failures == [
        (
            "geometry.numeric_invalid",
            "geometry.numeric_invalid: area must be representable as a finite number",
        ),
    ] * 2


@pytest.mark.parametrize(
    ("normal", "expected"),
    [
        ((1.0e308, 1.0e308, 0.0), [707106781, 707106781, 0]),
        ((5.0e-324, 0.0, 0.0), [1000000000, 0, 0]),
    ],
)
def test_r4a_002_extreme_finite_nonzero_vectors_normalize_safely(normal, expected):
    result = artifact([plane(1, normal=normal)]).faces[0]

    assert result.canonical_geometry["normal_q"] == expected
    assert any(result.canonical_geometry["normal_q"])


@pytest.mark.parametrize(
    ("normal", "code", "message"),
    [
        (
            (float("inf"), 0.0, 0.0),
            "geometry.numeric_non_finite",
            "normal must be finite",
        ),
        (
            (float("nan"), 0.0, 0.0),
            "geometry.numeric_non_finite",
            "normal must be finite",
        ),
        (
            (0.0, 0.0, 0.0),
            "geometry.vector_zero",
            "normal cannot be a zero vector",
        ),
    ],
)
def test_r4a_002_invalid_directions_have_stable_sanitized_failures(
    normal, code, message
):
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([plane(1, normal=normal)])

    assert caught.value.code == code
    assert str(caught.value) == f"{code}: {message}"
    assert "0x" not in str(caught.value)
    assert "C:\\" not in str(caught.value)


@pytest.mark.parametrize(
    ("surface_type", "field_name"),
    [
        ("plane", "normal"),
        ("cylinder", "axis"),
        ("cone", "axis"),
        ("torus", "axis"),
    ],
)
def test_r4a_002_every_directional_surface_rejects_quantized_zero(
    surface_type, field_name
):
    direction = (1.0, 1.0, 1.0)
    if surface_type == "plane":
        face = plane(1, normal=direction)
    elif surface_type == "cylinder":
        face = cylinder(1)
        face = replace(
            face,
            descriptors={**face.descriptors, "axis": direction},
        )
    elif surface_type == "cone":
        face = cone(1, axis=direction)
    else:
        face = torus(1, axis=direction)

    policy = TolerancePolicy(direction_quantum=3.0)
    failures = []
    for _ in range(2):
        with pytest.raises(GeometryIdentityError) as caught:
            build_geometry_identity(
                model_version_id=MODEL_VERSION,
                source_sha256=SOURCE_HASH,
                faces=[face],
                tolerance_policy=policy,
            )
        failures.append((caught.value.code, str(caught.value)))

    expected = (
        "geometry.vector_quantized_zero",
        "geometry.vector_quantized_zero: "
        f"{field_name} cannot become zero during canonicalization",
    )
    assert failures == [expected, expected]
    assert "0x" not in failures[0][1]
    assert "C:\\" not in failures[0][1]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (1.7e308, -1.7e308),
        (-1.7e308, 1.7e308),
    ],
)
def test_r4a_002_extreme_finite_scalar_comparisons_are_not_equal(left, right):
    assert not semantically_equivalent(left, right)


def test_r4a_002_extreme_area_ambiguity_comparison_is_overflow_safe():
    faces = [
        plane("larger", area=1.7e308, adjacent=("smaller",)),
        plane("smaller", area=1.6e308, adjacent=("larger",)),
    ]

    first = artifact(faces)
    reordered = artifact(list(reversed(faces)))
    identities = by_ref(first)

    assert first.canonical_bytes() == reordered.canonical_bytes()
    assert (
        identities["larger"].identity_candidate
        != identities["smaller"].identity_candidate
    )
    assert (
        identities["larger"].collision_group_id
        != identities["smaller"].collision_group_id
    )


def test_r4a_002_extreme_centroid_ambiguity_is_reordering_deterministic():
    positive = plane(
        "positive",
        centroid=(1.7e308, 0.0, 0.0),
        adjacent=("center",),
    )
    negative = plane(
        "negative",
        centroid=(-1.7e308, 0.0, 0.0),
        adjacent=("center",),
    )
    center = plane(
        "center",
        centroid=(0.0, 1.0, 0.0),
        normal=(0.0, 1.0, 0.0),
        adjacent=("positive", "negative"),
    )

    first = artifact([positive, center, negative])
    reordered = artifact(
        [
            negative,
            replace(center, adjacent_refs=("negative", "positive")),
            positive,
        ]
    )
    identities = by_ref(first)

    assert first.canonical_bytes() == reordered.canonical_bytes()
    assert (
        identities["positive"].identity_candidate
        != identities["negative"].identity_candidate
    )
    assert (
        identities["positive"].collision_group_id
        != identities["negative"].collision_group_id
    )


def test_r4a_002_finite_axis_point_overflow_has_sanitized_failure():
    face = cylinder(1)
    face = replace(
        face,
        descriptors={
            **face.descriptors,
            "axis": (1.0, 1.0, 0.0),
            "axis_point": (1.7e308, 1.7e308, 0.0),
        },
    )

    with pytest.raises(GeometryIdentityError) as caught:
        artifact([face])

    assert caught.value.code == "geometry.numeric_non_finite"
    assert str(caught.value) == (
        "geometry.numeric_non_finite: "
        "axis-point canonicalization must remain finite"
    )
    assert "1.7e+308" not in str(caught.value)
    assert "C:\\" not in str(caught.value)


def test_r4a_003_zero_loop_finite_plane_is_rejected():
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([plane(1, loops=0)])

    assert caught.value.code == "geometry.surface_topology_inconsistent"
    assert str(caught.value) == (
        "geometry.surface_topology_inconsistent: "
        "a finite positive-area plane requires at least one boundary loop"
    )


def test_r4a_003_zero_loop_partial_cylinder_is_rejected():
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([partial_cylinder(1, loops=0)])

    assert caught.value.code == "geometry.surface_topology_inconsistent"
    assert str(caught.value) == (
        "geometry.surface_topology_inconsistent: "
        "a finite non-full cylindrical patch requires at least one boundary loop"
    )


@pytest.mark.parametrize("loops", [1, 3])
def test_r4a_003_partial_cylinder_with_boundary_loops_is_supported(loops):
    result = artifact([partial_cylinder(1, loops=loops)]).faces[0]

    assert result.canonical_geometry["boundary_loop_count"] == loops
    assert result.stable_identity is not None


def test_r4a_003_zero_loop_finite_cone_is_rejected():
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([cone(1, loops=0)])

    assert caught.value.code == "geometry.surface_topology_inconsistent"
    assert str(caught.value) == (
        "geometry.surface_topology_inconsistent: "
        "a finite conical patch requires at least one boundary loop"
    )


@pytest.mark.parametrize("loops", [1, 2])
def test_r4a_003_finite_cone_with_boundary_loops_is_supported(loops):
    result = artifact([cone(1, loops=loops)]).faces[0]

    assert result.canonical_geometry["boundary_loop_count"] == loops
    assert result.stable_identity is not None


@pytest.mark.parametrize("loops", [0, 1, 3])
def test_r4a_003_full_finite_cylinder_requires_two_loops(loops):
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([replace(cylinder(1), boundary_loop_count=loops)])

    assert caught.value.code == "geometry.surface_topology_inconsistent"
    assert str(caught.value) == (
        "geometry.surface_topology_inconsistent: "
        "a full finite cylindrical lateral surface requires two boundary loops"
    )


def test_r4a_003_valid_plane_and_full_finite_cylinder_remain_supported():
    assert artifact([plane(1, loops=1)]).faces[0].stable_identity is not None
    result = artifact([cylinder(1)]).faces[0]

    assert result.canonical_geometry["boundary_loop_count"] == 2
    assert result.stable_identity is not None


@pytest.mark.parametrize(
    ("surface_type", "descriptors"),
    [
        ("Sphere", {"center": [0, 0, 0], "radius": 4}),
        (
            "Torus",
            {
                "center": [0, 0, 0],
                "axis": [0, 0, 1],
                "major_radius": 6,
                "minor_radius": 2,
            },
        ),
    ],
)
def test_r4a_003_valid_closed_surfaces_allow_zero_boundary_loops(
    surface_type, descriptors
):
    face = GeometryFaceInput(
        source_ref=1,
        surface_type=surface_type,
        area=10.0,
        centroid=(0.0, 0.0, 0.0),
        boundary_loop_count=0,
        descriptors=descriptors,
    )

    result = artifact([face]).faces[0]
    assert result.identity_quality == "analytic"
    assert result.stable_identity is not None


def test_r4a_004_equivalent_policy_number_spellings_are_byte_and_hash_identical():
    integer = TolerancePolicy(position_quantum_mm=1)
    floating = TolerancePolicy(position_quantum_mm=1.0)
    scientific = TolerancePolicy(length_quantum_mm=1e-6)
    decimal = TolerancePolicy(length_quantum_mm=0.000001)

    assert canonical_json_bytes(integer.to_dict()) == canonical_json_bytes(
        floating.to_dict()
    )
    assert canonical_json_bytes(scientific.to_dict()) == canonical_json_bytes(
        decimal.to_dict()
    )

    integer_artifact = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=SOURCE_HASH,
        faces=[plane(1)],
        tolerance_policy=integer,
    )
    floating_artifact = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=SOURCE_HASH,
        faces=[plane(1)],
        tolerance_policy=floating,
    )
    assert integer_artifact.canonical_bytes() == floating_artifact.canonical_bytes()
    assert integer_artifact.artifact_sha256 == floating_artifact.artifact_sha256


@pytest.mark.parametrize("field_name", POLICY_NUMERIC_FIELDS)
@pytest.mark.parametrize(
    "value",
    [1, 1.0, 2**53, 2**53 + 2],
    ids=["integer", "float", "large-exact", "large-exact-above-boundary"],
)
def test_r4a_004_lossless_numbers_are_accepted_for_every_policy_field(
    field_name, value
):
    policy = policy_with_numeric_field(field_name, value)

    assert getattr(policy, field_name) == float(value)
    assert isinstance(getattr(policy, field_name), float)


@pytest.mark.parametrize("field_name", POLICY_NUMERIC_FIELDS)
@pytest.mark.parametrize(
    "value",
    [2**53 + 1, True, False, float("inf"), float("-inf"), float("nan")],
    ids=["lossy-integer", "true", "false", "positive-inf", "negative-inf", "nan"],
)
def test_r4a_004_lossy_boolean_and_nonfinite_values_are_rejected_for_every_field(
    field_name, value
):
    with pytest.raises(GeometryIdentityError) as caught:
        policy_with_numeric_field(field_name, value)

    assert caught.value.code == "geometry.tolerance_policy_invalid"
    assert str(caught.value) == (
        "geometry.tolerance_policy_invalid: "
        "all tolerance values must be finite positive numbers"
    )
    assert "0x" not in str(caught.value)
    assert "C:\\" not in str(caught.value)


@pytest.mark.parametrize("value", [0, -1, 10**400, "1e-6", None])
def test_r4a_004_other_invalid_policy_values_have_one_sanitized_failure(value):
    with pytest.raises(GeometryIdentityError) as caught:
        TolerancePolicy(position_quantum_mm=value)

    assert caught.value.code == "geometry.tolerance_policy_invalid"
    assert str(caught.value) == (
        "geometry.tolerance_policy_invalid: "
        "all tolerance values must be finite positive numbers"
    )
    assert "0x" not in str(caught.value)
    assert "C:\\" not in str(caught.value)


def test_r4a_004_lossy_integer_is_rejected_before_artifact_hashing(monkeypatch):
    def forbidden_hash(*_args, **_kwargs):
        raise AssertionError("artifact hashing must not run")

    monkeypatch.setattr("geom.identity.hashlib.sha256", forbidden_hash)

    with pytest.raises(GeometryIdentityError) as caught:
        build_geometry_identity(
            model_version_id=MODEL_VERSION,
            source_sha256=SOURCE_HASH,
            faces=[plane(1)],
            tolerance_policy=TolerancePolicy(position_quantum_mm=2**53 + 1),
        )

    assert caught.value.code == "geometry.tolerance_policy_invalid"
    assert str(caught.value) == (
        "geometry.tolerance_policy_invalid: "
        "all tolerance values must be finite positive numbers"
    )
    assert "0x" not in str(caught.value)
    assert "C:\\" not in str(caught.value)


def test_r4a_004_distinct_exact_policies_remain_byte_and_hash_distinguishable():
    lower = TolerancePolicy(position_quantum_mm=2**53)
    upper = TolerancePolicy(position_quantum_mm=2**53 + 2)

    assert canonical_json_bytes(lower.to_dict()) != canonical_json_bytes(
        upper.to_dict()
    )

    lower_artifact = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=SOURCE_HASH,
        faces=[plane(1)],
        tolerance_policy=lower,
    )
    upper_artifact = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=SOURCE_HASH,
        faces=[plane(1)],
        tolerance_policy=upper,
    )
    assert lower_artifact.canonical_bytes() != upper_artifact.canonical_bytes()
    assert lower_artifact.artifact_sha256 != upper_artifact.artifact_sha256


def test_stable_hash_has_explicit_domain_separation():
    payload = {"b": [2, 1], "a": 3}
    assert stable_hash(payload, domain="one") == stable_hash(payload, domain="one")
    assert stable_hash(payload, domain="one") != stable_hash(payload, domain="two")
    assert len(stable_hash(payload, domain="one")) == 64
    assert canonical_json_bytes(payload) == b'{"a":3,"b":[2,1]}'


@pytest.mark.parametrize(
    ("faces", "code"),
    [
        ([plane(1), plane(1)], "geometry.source_ref_duplicate"),
        ([plane(1, adjacent=(2,))], "geometry.adjacency_reference_invalid"),
        (
            [plane(1, adjacent=(2,)), plane(2)],
            "geometry.adjacency_asymmetric",
        ),
        ([plane(1, adjacent=(1,))], "geometry.adjacency_self_loop"),
        (
            [plane(1, adjacent=(2, 2)), plane(2, adjacent=(1,))],
            "geometry.adjacency_duplicate",
        ),
        ([plane(1), plane(2)], "geometry.topology_disconnected"),
        ([plane(1, area=float("nan"))], "geometry.numeric_non_finite"),
        (
            [
                GeometryFaceInput(
                    source_ref=1,
                    surface_type="OffsetSurface",
                    area=1,
                    centroid=(0, 0, 0),
                    boundary_loop_count=1,
                )
            ],
            "geometry.surface_type_unsupported",
        ),
        (
            [
                GeometryFaceInput(
                    source_ref=1,
                    surface_type="Cylinder",
                    area=1,
                    centroid=(0, 0, 0),
                    boundary_loop_count=2,
                    descriptors={"radius": 1},
                )
            ],
            "geometry.descriptor_missing",
        ),
        (
            [
                GeometryFaceInput(
                    source_ref=1,
                    surface_type="BSpline surface",
                    area=1,
                    centroid=(0, 0, 0),
                    boundary_loop_count=1,
                    bbox_min=(1, 0, 0),
                    bbox_max=(0, 1, 1),
                )
            ],
            "geometry.bounding_box_invalid",
        ),
    ],
)
def test_malformed_geometry_fails_with_stable_sanitized_codes(faces, code):
    with pytest.raises(GeometryIdentityError) as caught:
        artifact(faces)

    assert caught.value.code == code
    assert caught.value.args == (f"{code}: {caught.value.message}",)
    assert "0x" not in str(caught.value)
    assert "C:\\" not in str(caught.value)


@pytest.mark.parametrize(
    "adjacent_refs",
    [None, 7, "face-2", b"face-2", {"face": 2}, [object()]],
)
def test_malformed_adjacency_containers_have_one_stable_failure(adjacent_refs):
    malformed = replace(plane(1), adjacent_refs=adjacent_refs)
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([malformed])
    assert caught.value.code == "geometry.adjacency_invalid"
    assert "0x" not in str(caught.value)


@pytest.mark.parametrize(
    "missing",
    ["length", "angular_extent", "classification", "full_circle"],
)
def test_cylinder_requires_complete_analyzed_patch_vocabulary(missing):
    face = cylinder(1)
    descriptors = dict(face.descriptors)
    descriptors.pop(missing)
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([replace(face, descriptors=descriptors)])
    assert caught.value.code == "geometry.descriptor_missing"


@pytest.mark.parametrize(
    "updates",
    [
        {"classification": "hole", "full_circle": False, "angular_extent": math.pi},
        {"classification": "boss", "full_circle": False, "angular_extent": math.pi},
        {
            "classification": "fillet_partial",
            "full_circle": True,
            "angular_extent": 2.0 * math.pi,
        },
        {"classification": "hole", "full_circle": True, "angular_extent": math.pi},
    ],
)
def test_cylinder_rejects_contradictory_patch_state(updates):
    face = cylinder(1)
    descriptors = dict(face.descriptors)
    descriptors.update(updates)
    with pytest.raises(GeometryIdentityError) as caught:
        artifact([replace(face, descriptors=descriptors)])
    assert caught.value.code == "geometry.descriptor_inconsistent"


def test_invalid_model_binding_fails_before_identity_generation():
    with pytest.raises(GeometryIdentityError) as caught:
        build_geometry_identity(
            model_version_id="",
            source_sha256="not-a-hash",
            faces=[plane(1)],
        )
    assert caught.value.code == "geometry.model_version_binding_invalid"


def test_existing_step_inventory_adapter_is_deterministic_and_captures_loops():
    path = FIXTURES / "bracket.step"
    parsed_a = parse_step(path)
    parsed_b = list(reversed(parse_step(path)))
    cylinders = analyze_cylinders(path)
    inventory_a = FaceInventory(path.name, file_sha256(path), parsed_a)
    inventory_b = FaceInventory(path.name, file_sha256(path), parsed_b)

    first = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=inventory_a.file_sha256,
        faces=faces_from_inventory(inventory_a, cylinders),
    )
    second = build_geometry_identity(
        model_version_id=MODEL_VERSION,
        source_sha256=inventory_b.file_sha256,
        faces=faces_from_inventory(inventory_b, cylinders),
    )

    assert first.canonical_bytes() == second.canonical_bytes()
    records = {face.tag: face for face in parsed_a}
    assert records[5].boundary_loop_count == 3
    assert records[10].boundary_loop_count == 2
    assert records[11].boundary_loop_count == 2
    assert records[12].boundary_loop_count == 2
    identities = by_ref(first)
    assert identities[11].evidence["repeated_feature_group_size"] == 2
    assert identities[12].evidence["repeated_feature_group_size"] == 2


def test_inventory_adapter_refuses_missing_true_cylinder_descriptors():
    path = FIXTURES / "plate_hole.step"
    faces = parse_step(path)
    inventory = FaceInventory(path.name, file_sha256(path), faces)

    with pytest.raises(GeometryIdentityError) as caught:
        faces_from_inventory(inventory)
    assert caught.value.code == "geometry.descriptor_missing"
