"""Task 7 DoD: deterministic load and unit semantics."""

import pytest

from ground.semantics import convert_value, interpret_load, parse_direction, parse_quantity


@pytest.mark.parametrize(
    ("text", "kind", "value", "unit"),
    [
        ("1 N", "force", 1, "N"), ("5 kN", "force", 5000, "N"),
        ("2 MN", "force", 2_000_000, "N"), ("1 Pa", "stress", 1e-6, "MPa"),
        ("4 kPa", "stress", .004, "MPa"), ("2 MPa", "stress", 2, "MPa"),
        ("210 GPa", "stress", 210_000, "MPa"), ("12 mm", "length", 12, "mm"),
        ("0.5 m", "length", 500, "mm"),
    ],
)
def test_unit_table(text, kind, value, unit):
    result = parse_quantity(text)
    assert (result.kind, result.value, result.unit) == (kind, value, unit)


@pytest.mark.parametrize(
    ("phrase", "load_type", "value", "vector"),
    [
        ("Apply 5 kN downward across the top flange", "resultant_surface_force", 5000, (0, -5000, 0)),
        ("Apply 20 N on the face in negative Z", "resultant_surface_force", 20, (0, 0, -20)),
        ("Apply 2 MPa pressure to the inner cylinder", "pressure", 2, None),
        ("Apply 500 kPa on the face", "pressure", .5, None),
        ("Apply 3 MPa traction in positive X", "surface_traction", 3, (3, 0, 0)),
        ("Apply a 40 N concentrated load in negative X", "concentrated_force", 40, (-40, 0, 0)),
        ("Apply gravity in the negative Z direction", "gravity", 9810, (0, 0, -9810)),
        ("Apply 2 kN downward", "resultant_surface_force", 2000, (0, -2000, 0)),
        ("Apply 4 N in positive Y", "resultant_surface_force", 4, (0, 4, 0)),
        ("Apply 1 MN downward across the face", "resultant_surface_force", 1_000_000, (0, -1_000_000, 0)),
    ],
)
def test_phrase_table(phrase, load_type, value, vector):
    result = interpret_load(phrase)
    assert (result.type, result.value, result.vector) == (load_type, value, vector)
    assert result.assumptions and all(a.status == "pending" for a in result.assumptions)


def test_per_node_force_is_totalled_twentieth_case():
    result = interpret_load("Apply 50 N downward per node", node_count=8)
    assert result.value == 400
    assert result.vector == (0, -400, 0)
    assert any("8 nodes" in assumption.text for assumption in result.assumptions)


def test_per_node_requires_count():
    with pytest.raises(ValueError, match="node_count"):
        interpret_load("Apply 50 N downward per node")


def test_downward_model_convention_is_audited():
    vector, assumption = parse_direction("downward", downward_axis="z")
    assert vector == (0, 0, -1)
    assert "model convention" in assumption.text


def test_invalid_and_mismatched_units_rejected():
    with pytest.raises(ValueError):
        convert_value(1, "kg")
    with pytest.raises(ValueError):
        parse_quantity("5 kN", expected_kind="stress")
    with pytest.raises(ValueError):
        interpret_load("move 2 mm downward")
