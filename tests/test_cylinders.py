"""Task 3 DoD tests: cylindrical analysis + hole semantics vs bracket_expected.json."""

import json
import math
from pathlib import Path

import pytest

from geom.cylinders import (
    TWO_PI,
    CylinderRecord,
    analyze_cylinders,
    are_coaxial,
    coaxial_groups,
    group_holes,
    holes,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRACKET = FIXTURES / "bracket.step"
PLATE_HOLE = FIXTURES / "plate_hole.step"
EXPECTED = json.loads((FIXTURES / "bracket_expected.json").read_text(encoding="utf-8"))
GT = EXPECTED["ground_truth"]

AXIS_VECTORS = {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}


@pytest.fixture(scope="module")
def bracket():
    return analyze_cylinders(BRACKET)


@pytest.fixture(scope="module")
def plate():
    return analyze_cylinders(PLATE_HOLE)


def test_bracket_analyzes_all_4_cylinder_faces(bracket):
    expected_tags = set(
        GT["bolt_holes"]["face_ids"] + GT["wall_hole"]["face_ids"] + GT["fillet"]["face_ids"]
    )
    assert set(bracket) == expected_tags  # {3, 10, 11, 12}


def test_bracket_holes_are_exactly_ground_truth(bracket):
    expected_holes = set(GT["bolt_holes"]["face_ids"] + GT["wall_hole"]["face_ids"])
    assert holes(bracket) == expected_holes == {10, 11, 12}


def test_fillet_is_not_a_hole(bracket):
    fillet = bracket[GT["fillet"]["face_ids"][0]]
    assert fillet.classification == "fillet_partial"
    assert fillet.classification != "hole"
    assert not fillet.full_circle
    assert fillet.angular_extent < TWO_PI
    # The trap: the fillet's normal ALSO points toward its axis, so the
    # inward-normal test alone would call it a hole; only the full-circle
    # check excludes it.
    assert fillet.normal_points_inward


def test_true_radii_match_ground_truth(bracket):
    for tag in GT["bolt_holes"]["face_ids"]:
        assert math.isclose(bracket[tag].radius, GT["bolt_holes"]["radius_mm"], rel_tol=1e-6)
    for tag in GT["wall_hole"]["face_ids"]:
        assert math.isclose(bracket[tag].radius, GT["wall_hole"]["radius_mm"], rel_tol=1e-6)
    for tag in GT["fillet"]["face_ids"]:
        assert math.isclose(bracket[tag].radius, GT["fillet"]["radius_mm"], rel_tol=1e-6)


def _assert_axis(actual: list[float], label: str):
    dot = abs(sum(a * b for a, b in zip(actual, AXIS_VECTORS[label])))
    assert dot > 0.9999, f"axis {actual} is not along {label}"


def test_axes_match_ground_truth(bracket):
    for tag in GT["bolt_holes"]["face_ids"]:
        _assert_axis(bracket[tag].axis_dir, GT["bolt_holes"]["axis"])
    for tag in GT["wall_hole"]["face_ids"]:
        _assert_axis(bracket[tag].axis_dir, GT["wall_hole"]["axis"])


def test_hole_faces_are_full_circles_with_inward_normals(bracket):
    for tag in holes(bracket):
        rec = bracket[tag]
        assert rec.full_circle
        assert rec.angular_extent == TWO_PI
        assert rec.normal_points_inward
        assert rec.length > 0


def test_bolt_hole_group_and_wall_hole_separate(bracket):
    groups = group_holes(bracket)
    assert len(groups) == 2
    by_tags = {tuple(g.face_tags): g for g in groups}

    bolt = by_tags[tuple(sorted(GT["bolt_holes"]["face_ids"]))]  # {11, 12}
    assert math.isclose(bolt.radius, GT["bolt_holes"]["radius_mm"], rel_tol=1e-6)
    _assert_axis(bolt.axis_dir, GT["bolt_holes"]["axis"])

    wall = by_tags[tuple(GT["wall_hole"]["face_ids"])]  # {10}
    assert math.isclose(wall.radius, GT["wall_hole"]["radius_mm"], rel_tol=1e-6)
    _assert_axis(wall.axis_dir, GT["wall_hole"]["axis"])


def test_bolt_holes_are_parallel_but_not_coaxial(bracket):
    a, b = (bracket[tag] for tag in GT["bolt_holes"]["face_ids"])
    # Same direction (that is why they group), different axis lines.
    assert not are_coaxial(a, b)
    assert coaxial_groups(bracket) == [[3], [10], [11], [12]]


def test_radius_never_from_bbox(bracket):
    # The fillet face's bbox is a quarter-arc box (4 x 4 span in Y/Z), so a
    # bbox-derived "radius" would be off for it, and the analyzer already
    # cross-checks circle-fit vs curvature radius at 1% internally. Hitting
    # every ground-truth radius to 1e-6 proves no bbox approximation is used.
    expected_radius = {
        11: GT["bolt_holes"]["radius_mm"],
        12: GT["bolt_holes"]["radius_mm"],
        10: GT["wall_hole"]["radius_mm"],
        3: GT["fillet"]["radius_mm"],
    }
    for tag, rec in bracket.items():
        assert math.isclose(rec.radius, expected_radius[tag], rel_tol=1e-6)


def test_plate_hole_single_cylinder_is_a_hole(plate):
    assert len(plate) == 1
    (rec,) = plate.values()
    assert rec.classification == "hole"
    assert holes(plate) == {rec.tag}
    assert math.isclose(rec.radius, 15.0, rel_tol=1e-6)


def test_record_round_trip(bracket):
    for rec in bracket.values():
        assert CylinderRecord.from_dict(rec.to_dict()) == rec
