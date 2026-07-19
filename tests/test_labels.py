"""Task 4 DoD tests: planar labels + adjacency + spatial predicates."""

from pathlib import Path

import pytest

from geom.labels import (
    adjacency_graph,
    area_rank,
    component_of,
    connected_components,
    extreme_face_labels,
    face_labels,
    height_rank,
    is_above,
    is_below,
    largest_face,
)
from geom.parser import FaceRecord, parse_step

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def bracket():
    return parse_step(FIXTURES / "bracket.step")


@pytest.fixture(scope="module")
def plate():
    return parse_step(FIXTURES / "plate_hole.step")


def _face(faces, tag):
    return next(f for f in faces if f.tag == tag)


# --- extreme-face labels -------------------------------------------------


def test_bracket_top_is_top_of_wall(bracket):
    labels = extreme_face_labels(bracket)
    assert labels["top"] == 4
    # It is the wall's top (z = 70 = model bbox max), chosen over the base's
    # top face 5 (z = 10) even though both normals are +Z.
    assert _face(bracket, 4).centroid[2] == pytest.approx(70.0)
    assert _face(bracket, 5).normal[2] == pytest.approx(1.0)


def test_bracket_bottom_is_base_bottom(bracket):
    assert extreme_face_labels(bracket)["bottom"] == 8
    assert _face(bracket, 8).centroid[2] == pytest.approx(0.0)


def test_bracket_all_six_labels(bracket):
    assert extreme_face_labels(bracket) == {
        "top": 4,
        "bottom": 8,
        "left": 1,
        "right": 9,
        "front": 7,
        "back": 6,
    }


def test_front_label_is_extreme_not_first_aligned(bracket):
    # Face 2 (wall front, y = 70) also has normal -Y; the label must go to
    # face 7 at the bbox extreme y = 0, not to whichever aligned face comes
    # first in tag order.
    assert _face(bracket, 2).normal[1] == pytest.approx(-1.0)
    assert extreme_face_labels(bracket)["front"] == 7


def test_cylinders_never_labeled(bracket):
    # The wall hole (face 10) samples a +Z midpoint normal on its seam; only
    # the surface-type filter keeps it out of the label pool.
    assert _face(bracket, 10).normal[2] == pytest.approx(1.0)
    labeled_tags = set(extreme_face_labels(bracket).values())
    assert labeled_tags.isdisjoint({3, 10, 11, 12})


def test_plate_yields_the_known_6_planar_labels(plate):
    labels = extreme_face_labels(plate)
    assert labels == {
        "left": 1,
        "front": 2,
        "top": 3,
        "back": 4,
        "bottom": 5,
        "right": 6,
    }
    assert 7 not in labels.values()  # the hole face


def test_face_labels_inverse_view(plate):
    inverse = face_labels(plate)
    assert inverse[3] == ["top"]
    assert 7 not in inverse
    assert sorted(label for labels in inverse.values() for label in labels) == sorted(
        extreme_face_labels(plate)
    )


# --- largest face --------------------------------------------------------


def test_bracket_largest_face_is_base_bottom(bracket):
    assert largest_face(bracket) == 8


def test_plate_largest_face_tie_breaks_to_lowest_tag(plate):
    # Top (3) and bottom (5) have bitwise-equal areas; deterministic
    # tie-break picks the lower tag.
    assert _face(plate, 3).area == _face(plate, 5).area
    assert largest_face(plate) == 3


# --- adjacency + components ---------------------------------------------


def test_bracket_adjacency_connected_with_12_nodes(bracket):
    graph = adjacency_graph(bracket)
    assert len(graph) == 12
    components = connected_components(bracket)
    assert components == [sorted(graph)]  # one component holding all 12 faces


def test_adjacency_is_symmetric_without_self_loops(bracket):
    graph = adjacency_graph(bracket)
    for tag, neighbors in graph.items():
        assert tag not in neighbors
        for other in neighbors:
            assert tag in graph[other]


def test_hole_neighborhoods_match_geometry(bracket):
    graph = adjacency_graph(bracket)
    # Wall hole pierces the wall: front face 2 and back face 6.
    assert graph[10] == {2, 6}
    # Bolt holes pierce the base: top face 5 and bottom face 8.
    assert graph[11] == {5, 8}
    assert graph[12] == {5, 8}


def test_plate_single_component(plate):
    assert connected_components(plate) == [[1, 2, 3, 4, 5, 6, 7]]
    assert component_of(plate, 7) == [1, 2, 3, 4, 5, 6, 7]


def test_component_of_unknown_tag_raises(plate):
    with pytest.raises(KeyError):
        component_of(plate, 99)


# --- spatial predicates --------------------------------------------------


def test_above_below(bracket):
    wall_top = _face(bracket, 4)
    base_bottom = _face(bracket, 8)
    assert is_above(wall_top, base_bottom)
    assert is_below(base_bottom, wall_top)
    assert not is_above(base_bottom, wall_top)
    assert not is_above(wall_top, wall_top)


def test_bracket_height_rank(bracket):
    # Exact centroid heights verified against the fixture; ties (1/9 at the
    # same z, 7/11/12 at z=5) resolve in tag order.
    assert height_rank(bracket) == [4, 10, 2, 6, 1, 9, 3, 5, 7, 11, 12, 8]


def test_bracket_area_rank_extremes(bracket):
    ranked = area_rank(bracket)
    assert len(ranked) == 12
    assert ranked[0] == 8  # base bottom is the largest face
    assert set(ranked[-2:]) == {11, 12}  # bolt holes are the smallest faces


def test_rank_tie_break_is_deterministic():
    def rec(tag):
        return FaceRecord(
            tag=tag,
            surface_type="Plane",
            area=10.0,
            centroid=[0.0, 0.0, 5.0],
            bbox_min=[0.0, 0.0, 5.0],
            bbox_max=[1.0, 1.0, 5.0],
            normal=[0.0, 0.0, 1.0],
            edge_tags=[],
        )

    faces = [rec(7), rec(2), rec(5)]
    assert height_rank(faces) == [2, 5, 7]
    assert area_rank(faces) == [2, 5, 7]
    assert largest_face(faces) == 2
