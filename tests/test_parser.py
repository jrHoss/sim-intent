"""Task 2 DoD tests: STEP parser + face inventory + cache."""

import json
import math
from pathlib import Path

import pytest

from geom.inventory import FaceInventory, file_sha256, get_inventory
from geom.parser import parse_step

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRACKET = FIXTURES / "bracket.step"
PLATE_HOLE = FIXTURES / "plate_hole.step"
EXPECTED = json.loads((FIXTURES / "bracket_expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bracket_faces():
    return parse_step(BRACKET)


@pytest.fixture(scope="module")
def plate_faces():
    return parse_step(PLATE_HOLE)


def test_bracket_has_exactly_12_faces(bracket_faces):
    assert len(bracket_faces) == EXPECTED["total_faces"] == 12


def test_bracket_has_4_cylinder_faces(bracket_faces):
    cylinders = {f.tag for f in bracket_faces if f.surface_type == "Cylinder"}
    assert len(cylinders) == 4
    gt = EXPECTED["ground_truth"]
    expected_cylinders = set(
        gt["bolt_holes"]["face_ids"] + gt["wall_hole"]["face_ids"] + gt["fillet"]["face_ids"]
    )
    assert cylinders == expected_cylinders  # {3, 10, 11, 12}


def test_bracket_planar_face_count(bracket_faces):
    planes = [f for f in bracket_faces if f.surface_type == "Plane"]
    assert len(planes) == EXPECTED["ground_truth"]["planar_faces"] == 8


def test_plate_hole_has_7_faces(plate_faces):
    assert len(plate_faces) == 7
    assert sum(1 for f in plate_faces if f.surface_type == "Cylinder") == 1


@pytest.mark.parametrize("fixture_name", ["bracket_faces", "plate_faces"])
def test_face_records_are_sane(fixture_name, request):
    faces = request.getfixturevalue(fixture_name)
    for f in faces:
        assert f.area > 0
        assert len(f.centroid) == 3
        assert len(f.normal) == 3
        assert math.isclose(math.hypot(*f.normal), 1.0, rel_tol=1e-6)
        assert all(lo <= hi for lo, hi in zip(f.bbox_min, f.bbox_max))
        assert f.edge_tags == sorted(set(f.edge_tags))
        assert f.edge_tags


def test_inventory_json_round_trip(bracket_faces):
    inventory = FaceInventory(
        source_name="bracket.step",
        file_sha256=file_sha256(BRACKET),
        faces=bracket_faces,
    )
    reloaded = FaceInventory.from_json(inventory.to_json())
    assert reloaded == inventory
    assert reloaded.face(3).surface_type == "Cylinder"


def test_cache_hit_on_second_load(tmp_path):
    cache_dir = tmp_path / "inv_cache"

    first, hit_first = get_inventory(BRACKET, cache_dir=cache_dir)
    assert hit_first is False

    sha = file_sha256(BRACKET)
    assert first.file_sha256 == sha
    assert (cache_dir / f"{sha}.json").is_file()

    second, hit_second = get_inventory(BRACKET, cache_dir=cache_dir)
    assert hit_second is True
    assert second == first


def test_cache_is_per_file_content(tmp_path):
    cache_dir = tmp_path / "inv_cache"
    get_inventory(BRACKET, cache_dir=cache_dir)
    plate, hit = get_inventory(PLATE_HOLE, cache_dir=cache_dir)
    assert hit is False  # different file hash, no false cache hit
    assert len(plate.faces) == 7
