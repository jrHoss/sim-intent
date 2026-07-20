"""Task 12: deterministic grounding, ambiguity, and click fusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, file_sha256
from geom.parser import parse_step
from ground.engine import ClickEvidence, GroundingEngine
from ground.queries import QueryResult
from ir.schema import Region
from llm.interpreter import Interpretation


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def bracket():
    path = FIXTURES / "bracket.step"
    inventory = FaceInventory(path.name, file_sha256(path), parse_step(path))
    return inventory, analyze_cylinders(path)


def interpretation(*intents):
    return Interpretation.model_validate({"intents": list(intents)}, strict=True)


def fixed_intent(ops, target):
    return {
        "op_list": ops,
        "bc": {"type": "fixed_displacement", "components": ["x", "y", "z"]},
        "load": None,
        "target_description": target,
    }


def pressure_intent(ops, target="the selected hole"):
    return {
        "op_list": ops,
        "bc": None,
        "load": {"type": "pressure", "magnitude": "2 MPa"},
        "target_description": target,
    }


def result_for(engine, instruction, intent, **kwargs):
    parsed = interpretation(intent)
    return engine.ground_intent(instruction, parsed.intents[0], **kwargs)


def test_bolt_hole_grounding_produces_proposed_region(bracket):
    inventory, cylinders = bracket
    source = "Fix the two bolt holes."
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        source,
        fixed_intent(
            [
                {"op": "holes"},
                {"op": "hole_groups", "min_size": 2, "max_size": 2},
            ],
            "the two bolt holes",
        ),
    )

    assert isinstance(grounded.region, Region)
    assert grounded.clarification is None
    assert set(grounded.region.entity_ids) == {11, 12}
    assert 3 not in grounded.region.entity_ids
    assert 10 not in grounded.region.entity_ids
    assert grounded.region.status == "proposed"
    assert grounded.region.selection_method == "semantic_geometry_query"
    assert grounded.region.source_instruction == source
    assert 0.0 <= grounded.region.confidence <= 1.0
    assert grounded.bc.type == "fixed_displacement"
    assert grounded.query_evidence.entity_ids == [11, 12]
    # Raw Task 6 entity-score margin is retained even though the exact pair is
    # one candidate set for the grounding decision.
    assert grounded.query_evidence.score_margin == pytest.approx(0.0)
    assert grounded.query_evidence.candidate_set_margin == pytest.approx(1.0)


def test_ambiguous_singular_hole_returns_three_highlightable_alternatives(bracket):
    inventory, cylinders = bracket
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Fix the hole.",
        fixed_intent([{"op": "holes"}], "the hole"),
    )

    assert grounded.region is None
    request = grounded.clarification
    assert request is not None
    assert request.reason == "low_score_margin"
    assert len(request.candidate_sets) == 3
    assert [candidate.entity_ids for candidate in request.candidate_sets] == [[10], [11], [12]]
    assert {entity_id for candidate in request.candidate_sets for entity_id in candidate.entity_ids} == {
        10,
        11,
        12,
    }
    assert all(3 not in candidate.entity_ids for candidate in request.candidate_sets)
    assert all("hole" in candidate.label for candidate in request.candidate_sets)
    assert request.source_instruction == "Fix the hole."
    assert request.target_description == "the hole"
    assert request.model_dump(mode="json")


def test_click_assisted_hole_uses_only_clicked_face(bracket):
    inventory, cylinders = bracket
    source = "Fix this hole."
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        source,
        fixed_intent([{"op": "holes"}], "this hole"),
        click_evidence=ClickEvidence.for_inventory(inventory, [10]),
    )

    assert grounded.clarification is None
    assert grounded.region is not None
    assert grounded.region.entity_ids == [10]
    assert grounded.region.selection_method == "user_click"
    assert grounded.region.status == "proposed"
    assert grounded.region.source_instruction == source
    assert grounded.click_evidence.entity_ids == [10]


def test_click_query_conflict_retains_both_evidence_sources(bracket):
    inventory, cylinders = bracket
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Fix this bolt hole.",
        fixed_intent(
            [
                {"op": "holes"},
                {"op": "filter_radius", "radius": 5.5, "rtol": 0.05},
            ],
            "this bolt hole",
        ),
        click_evidence=ClickEvidence.for_inventory(inventory, [10]),
    )

    assert grounded.region is None
    request = grounded.clarification
    assert request is not None
    assert request.reason == "click_query_conflict"
    assert set(request.query_evidence.entity_ids) == {11, 12}
    assert request.click_evidence.entity_ids == [10]
    assert request.candidate_sets[-1].source == "user_click"
    assert request.candidate_sets[-1].entity_ids == [10]


def test_click_from_another_inventory_never_crosses_model_boundary(bracket):
    inventory, cylinders = bracket
    foreign = ClickEvidence(inventory_sha256="foreign-inventory", entity_ids=[10])
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Fix this hole.",
        fixed_intent([{"op": "holes"}], "this hole"),
        click_evidence=foreign,
    )
    assert grounded.region is None
    assert grounded.clarification.reason == "click_inventory_mismatch"
    assert grounded.clarification.click_evidence == foreign


def test_requested_count_mismatch_never_selects_first_candidates(bracket):
    inventory, cylinders = bracket
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Fix the two holes.",
        fixed_intent([{"op": "holes"}], "the two holes"),
    )
    assert grounded.region is None
    assert grounded.clarification.reason == "count_mismatch"
    assert grounded.clarification.requested_count == 2
    assert set(grounded.clarification.query_evidence.entity_ids) == {10, 11, 12}


def test_quantity_numeral_is_not_misread_as_entity_count(bracket):
    inventory, cylinders = bracket
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Apply 2 MPa pressure to the top face.",
        pressure_intent([{"op": "labeled", "name": "top_face"}], "the top face"),
    )
    assert grounded.clarification is None
    assert grounded.region is not None
    assert grounded.load.magnitude == "2 MPa"


class StubQueryEngine:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return self.result


def test_low_candidate_score_margin_requests_clarification(bracket):
    inventory, cylinders = bracket
    stub = StubQueryEngine(QueryResult([10, 11], {10: 0.60, 11: 0.55}))
    grounded = result_for(
        GroundingEngine(inventory, cylinders, ambiguity_threshold=0.10, query_engine=stub),
        "Fix a suitable hole.",
        fixed_intent([{"op": "holes"}], "a suitable hole"),
    )
    assert grounded.region is None
    assert grounded.clarification.reason == "low_score_margin"
    assert grounded.query_evidence.score_margin == pytest.approx(0.05)
    assert grounded.query_evidence.candidate_set_margin == pytest.approx(0.05)
    assert stub.queries[0].to_dict() == {"ops": [{"op": "holes"}]}


def test_empty_query_result_is_targeted_clarification(bracket):
    inventory, cylinders = bracket
    stub = StubQueryEngine(QueryResult([], {}))
    grounded = result_for(
        GroundingEngine(inventory, cylinders, query_engine=stub),
        "Fix the missing hole.",
        fixed_intent([{"op": "holes"}], "the missing hole"),
    )
    assert grounded.region is None
    assert grounded.clarification.reason == "no_usable_candidate"
    assert grounded.clarification.candidate_sets == []
    assert "could not find" in grounded.clarification.question.lower()


@pytest.mark.parametrize(
    "intent,instruction",
    [
        (
            fixed_intent(
                [{"op": "holes"}, {"op": "hole_groups", "min_size": 2, "max_size": 2}],
                "the two bolt holes",
            ),
            "Fix the two bolt holes.",
        ),
        (fixed_intent([{"op": "holes"}], "the hole"), "Fix the hole."),
    ],
)
def test_grounding_is_byte_for_byte_deterministic(bracket, intent, instruction):
    inventory, cylinders = bracket
    engine = GroundingEngine(inventory, cylinders)
    parsed = interpretation(intent).intents[0]
    first = engine.ground_intent(instruction, parsed).model_dump_json()
    second = engine.ground_intent(instruction, parsed).model_dump_json()
    assert first == second


def test_every_successful_region_has_full_provenance(bracket):
    inventory, cylinders = bracket
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Fix the top face verbatim.",
        fixed_intent([{"op": "labeled", "name": "top_face"}], "the top face"),
    )
    dumped = grounded.region.model_dump(mode="json")
    assert dumped["entity_ids"]
    assert dumped["selection_method"] == "semantic_geometry_query"
    assert 0.0 <= dumped["confidence"] <= 1.0
    assert dumped["source_instruction"] == "Fix the top face verbatim."
    assert dumped["status"] == "proposed"


def test_multiple_intents_preserve_order_and_payload_association(bracket):
    inventory, cylinders = bracket
    parsed = interpretation(
        fixed_intent(
            [{"op": "holes"}, {"op": "hole_groups", "min_size": 2, "max_size": 2}],
            "the two bolt holes",
        ),
        pressure_intent(
            [
                {"op": "holes"},
                {"op": "filter_radius", "radius": 8.0, "rtol": 0.05},
            ],
            "one larger wall hole",
        ),
    )
    batch = GroundingEngine(inventory, cylinders).ground_interpretation(
        "Fix the two bolt holes, then apply 2 MPa to one larger wall hole.", parsed
    )
    assert [result.intent_index for result in batch.results] == [0, 1]
    assert batch.results[0].bc.type == "fixed_displacement"
    assert batch.results[0].load is None
    assert batch.results[1].bc is None
    assert batch.results[1].load.type == "pressure"
    assert batch.results[0].region.entity_ids == [11, 12]
    assert batch.results[1].region.entity_ids == [10]


def test_grounding_never_crosses_the_live_openai_boundary(bracket, monkeypatch):
    inventory, cylinders = bracket

    def fail_live_request(*args, **kwargs):
        raise AssertionError("grounding must never make a live OpenAI request")

    monkeypatch.setattr("llm.interpreter.OpenAIStructuredOutputTransport.complete", fail_live_request)
    grounded = result_for(
        GroundingEngine(inventory, cylinders),
        "Fix the top face.",
        fixed_intent([{"op": "labeled", "name": "top_face"}], "the top face"),
    )
    assert grounded.region is not None


def test_production_grounding_has_no_fixture_specific_face_ids():
    source = (Path(__file__).resolve().parents[1] / "ground" / "engine.py").read_text(
        encoding="utf-8"
    )
    for fixture_literal in ("face 10", "face 11", "face 12", "{10", "{11", "{12"):
        assert fixture_literal not in source.lower()
