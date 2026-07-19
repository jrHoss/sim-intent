"""Task 6 DoD tests: deterministic, composable face query engine."""

from pathlib import Path

import pytest

from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, file_sha256
from geom.parser import parse_step
from ground.queries import (
    Query,
    QueryEngine,
    filter_axis,
    filter_radius,
    holes,
    execute,
    intersect,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _context(name):
    path = FIXTURES / name
    inventory = FaceInventory(path.name, file_sha256(path), parse_step(path))
    return inventory, analyze_cylinders(path)


@pytest.fixture(scope="module")
def bracket():
    return _context("bracket.step")


@pytest.fixture(scope="module")
def plate():
    return _context("plate_hole.step")


def test_dod_two_bolt_holes_by_radius_and_parallel_axis(bracket):
    inventory, cylinders = bracket
    result = execute(
        inventory,
        [
            {"op": "holes"},
            {"op": "filter_radius", "radius": 5.5, "rtol": 0.05},
            {"op": "filter_axis", "direction": [0, 0, 1]},
        ],
        cylinders,
    )
    assert set(result.entity_ids) == {11, 12}
    assert result.per_candidate_scores.keys() == {11, 12}
    assert result.score_margin == pytest.approx(0.0)


@pytest.mark.parametrize("fixture, expected", [("bracket", 4), ("plate", 3)])
def test_dod_labeled_top_face(request, fixture, expected):
    inventory, cylinders = request.getfixturevalue(fixture)
    assert execute(inventory, [{"op": "labeled", "name": "top_face"}], cylinders).entity_ids == [expected]


def test_holes_exclude_partial_cylinder(bracket):
    inventory, cylinders = bracket
    assert set(execute(inventory, [{"op": "holes"}], cylinders).entity_ids) == {10, 11, 12}
    assert set(execute(inventory, [{"op": "find_faces", "surface_type": "Cylinder"}], cylinders).entity_ids) == {3, 10, 11, 12}


def test_hole_group_uses_radius_plus_axis_not_coaxiality(bracket):
    inventory, cylinders = bracket
    result = execute(inventory, [{"op": "holes"}, {"op": "hole_groups", "min_size": 2}], cylinders)
    assert set(result.entity_ids) == {11, 12}


def test_filter_axis_is_sign_independent(bracket):
    inventory, cylinders = bracket
    result = execute(inventory, [{"op": "holes"}, {"op": "filter_axis", "dir": [0, 0, -1]}], cylinders)
    assert set(result.entity_ids) == {11, 12}


def test_area_rank_adjacency_and_component(bracket):
    inventory, cylinders = bracket
    engine = QueryEngine(inventory, cylinders)
    assert engine.execute([{"op": "area_max", "n": 1}]).entity_ids == [8]
    assert set(engine.execute([{"op": "adjacent_to", "ids": [11]}]).entity_ids) == {5, 8}
    assert set(engine.execute([{"op": "in_component", "id": 11}]).entity_ids) == set(range(1, 13))


def test_rank_by_position(bracket):
    inventory, cylinders = bracket
    engine = QueryEngine(inventory, cylinders)
    assert engine.execute([{"op": "rank_by", "predicate": "highest", "n": 2}]).entity_ids == [4, 10]
    assert engine.execute([{"op": "rank_by", "predicate": "lowest"}]).entity_ids == [8]


def test_combine_intersect_union_difference(bracket):
    inventory, cylinders = bracket
    engine = QueryEngine(inventory, cylinders)
    holes = {"ops": [{"op": "holes"}]}
    z_axis = {"ops": [{"op": "filter_axis", "direction": [0, 0, 1]}]}
    cylinders_query = {"ops": [{"op": "find_faces", "surface_type": "Cylinder"}]}
    assert set(engine.execute([{"op": "intersect", "queries": [holes, z_axis]}]).entity_ids) == {11, 12}
    assert set(engine.execute([{"op": "union", "queries": [holes, {"ops": [{"op": "labeled", "name": "top"}]}]}]).entity_ids) == {4, 10, 11, 12}
    assert engine.execute([{"op": "difference", "queries": [cylinders_query, holes]}]).entity_ids == [3]


def test_query_json_round_trip_and_result_serialization(bracket):
    inventory, cylinders = bracket
    query = Query([{"op": "holes"}, {"op": "filter_radius", "r": 8.0}])
    restored = Query.from_json(query.to_json())
    assert restored == query
    result = QueryEngine(inventory, cylinders).execute(restored)
    assert result.entity_ids == [10]
    assert result.to_dict()["score_margin"] == 1.0


def test_operation_factories_are_composable_and_json_native(bracket):
    inventory, cylinders = bracket
    query = Query([holes()]).then(filter_radius(5.5), filter_axis([0, 0, 1]))
    assert set(QueryEngine(inventory, cylinders).execute(query).entity_ids) == {11, 12}
    combined = Query([intersect(Query([holes()]), Query([filter_axis([0, 1, 0])]))])
    assert QueryEngine(inventory, cylinders).execute(combined).entity_ids == [10]


def test_invalid_ops_fail_cleanly(bracket):
    inventory, cylinders = bracket
    engine = QueryEngine(inventory, cylinders)
    with pytest.raises(ValueError, match="unknown query operation"):
        engine.execute([{"op": "coaxial_magic"}])
    with pytest.raises(ValueError, match="cannot be zero"):
        engine.execute([{"op": "filter_axis", "direction": [0, 0, 0]}])
