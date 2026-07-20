"""Task 11: mocked natural-language to typed-operation interpretation."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.lib._parsing._responses import type_to_response_format_param

from ground.queries import LLM_SAFE_QUERY_OPERATION_NAMES
from llm.interpreter import (
    FaceInventorySummary,
    Interpreter,
    InterpreterError,
    Interpretation,
    OpenAIStructuredOutputTransport,
    OpenAIWireInterpretation,
)


SUMMARY = FaceInventorySummary.model_validate(
    {
        "source_name": "bracket.step",
        "face_count": 12,
        "surface_type_counts": {"Cylinder": 4, "Plane": 8},
        "available_labels": [
            "back_face",
            "bottom_face",
            "front_face",
            "left_face",
            "right_face",
            "top_face",
        ],
        "hole_groups": [
            {"count": 2, "radius_mm": 5.5, "axis_direction": [0.0, 0.0, 1.0]},
            {"count": 1, "radius_mm": 8.0, "axis_direction": [0.0, 1.0, 0.0]},
        ],
        "face_areas": {
            "minimum_mm2": 345.6,
            "maximum_mm2": 6000.0,
            "largest_first_mm2": [6000.0, 4550.0, 3200.0],
        },
    },
    strict=True,
)


class SequenceTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def fixed_holes():
    return {
        "intents": [
            {
                "op_list": [
                    {"op": "holes"},
                    {"op": "hole_groups", "min_size": 2, "max_size": 2},
                    {"op": "filter_radius", "radius": 5.5, "rtol": 0.05},
                ],
                "bc": {"type": "fixed_displacement", "components": ["x", "y", "z"]},
                "load": None,
                "target_description": "the pair of similar-radius bolt holes",
            }
        ]
    }


def downward_force():
    return {
        "intents": [
            {
                "op_list": [
                    {"op": "find_faces", "surface_type": "Plane"},
                    {"op": "rank_by", "position": "upper", "n": 1},
                ],
                "bc": None,
                "load": {
                    "type": "resultant_surface_force",
                    "magnitude": "5 kN",
                    "direction": "downward",
                },
                "target_description": "the upper mounting face",
            }
        ]
    }


def pressure():
    return {
        "intents": [
            {
                "op_list": [
                    {"op": "find_faces", "surface_type": "Cylinder"},
                    {"op": "holes"},
                ],
                "bc": None,
                "load": {"type": "pressure", "magnitude": "2 MPa"},
                "target_description": "the inner cylindrical surface",
            }
        ]
    }


def all_operation_names(result):
    return {op.op for intent in result.intents for op in intent.op_list}


def test_fix_two_bolt_holes_uses_only_typed_ops_and_fixed_bc():
    transport = SequenceTransport(fixed_holes())
    result = Interpreter(transport=transport).interpret("Fix the two bolt holes", SUMMARY)
    assert result.intents[0].bc.type == "fixed_displacement"
    assert all_operation_names(result) <= LLM_SAFE_QUERY_OPERATION_NAMES
    assert "entity_ids" not in result.model_dump_json()
    assert json.loads(result.model_dump_json())["intents"][0]["op_list"][0] == {"op": "holes"}


def test_total_downward_force_preserves_units_direction_and_spatial_ops():
    result = Interpreter(transport=SequenceTransport(downward_force())).interpret(
        "Apply a total downward force of 5 kN to the upper mounting face", SUMMARY
    )
    intent = result.intents[0]
    assert intent.load.type == "resultant_surface_force"
    assert intent.load.magnitude == "5 kN"
    assert intent.load.direction == "downward"
    assert [op.op for op in intent.op_list] == ["find_faces", "rank_by"]


def test_pressure_preserves_semantics_and_uses_cylindrical_hole_ops():
    result = Interpreter(transport=SequenceTransport(pressure())).interpret(
        "Apply 2 MPa pressure to the inner cylindrical surface", SUMMARY
    )
    intent = result.intents[0]
    assert intent.load.type == "pressure"
    assert intent.load.magnitude == "2 MPa"
    assert {op.op for op in intent.op_list} == {"find_faces", "holes"}


def test_entity_ids_field_is_rejected_and_reasked():
    unsafe = fixed_holes()
    unsafe["intents"][0]["entity_ids"] = [11, 12]
    transport = SequenceTransport(unsafe, fixed_holes())
    result = Interpreter(transport=transport).interpret("Fix the two bolt holes", SUMMARY)
    assert result.attempts == 2
    assert "forbidden direct entity selection" in transport.requests[1].messages[-1]["content"]


@pytest.mark.parametrize(
    "field",
    [
        "face_id",
        "face_ids",
        "edge_id",
        "edge_ids",
        "node_id",
        "node_ids",
        "element_id",
        "element_ids",
        "NSET",
        "ELSET",
    ],
)
def test_alternative_direct_id_fields_are_rejected(field):
    unsafe = fixed_holes()
    unsafe["intents"][0][field] = [11]
    with pytest.raises(InterpreterError, match="forbidden direct entity selection"):
        Interpreter(transport=SequenceTransport(unsafe), max_retries=0).interpret(
            "Fix the two bolt holes", SUMMARY
        )


def test_direct_entity_id_hidden_in_text_is_rejected():
    unsafe = fixed_holes()
    unsafe["intents"][0]["target_description"] = "faces 11 and 12"
    with pytest.raises(InterpreterError, match="forbidden direct entity selection"):
        Interpreter(transport=SequenceTransport(unsafe), max_retries=0).interpret(
            "Fix the holes", SUMMARY
        )


def test_unknown_query_operation_is_rejected():
    invalid = fixed_holes()
    invalid["intents"][0]["op_list"] = [{"op": "invented_magic"}]
    with pytest.raises(InterpreterError, match="unknown query operation"):
        Interpreter(transport=SequenceTransport(invalid), max_retries=0).interpret(
            "Fix the holes", SUMMARY
        )


@pytest.mark.parametrize("invalid", ["not json", {"intents": [{}]}])
def test_malformed_or_incomplete_output_triggers_clean_reask(invalid):
    transport = SequenceTransport(invalid, fixed_holes())
    result = Interpreter(transport=transport, max_retries=1).interpret("Fix the holes", SUMMARY)
    assert result.retry_count == 1
    assert len(transport.requests) == 2
    assert "previous response was rejected" in transport.requests[1].messages[-1]["content"].lower()


def test_valid_after_invalid_records_retry_deterministically():
    transport = SequenceTransport(json.dumps({"intents": []}), fixed_holes())
    result = Interpreter(transport=transport, max_retries=2).interpret("Fix the holes", SUMMARY)
    assert result.attempts == 2
    assert result.retry_count == 1
    assert len(transport.requests[0].messages) == 1
    assert len(transport.requests[1].messages) == 2


def test_repeated_invalid_responses_stop_at_retry_limit():
    transport = SequenceTransport("bad", "still bad", {"intents": []})
    with pytest.raises(InterpreterError, match="failed after 3 attempts") as caught:
        Interpreter(transport=transport, max_retries=2).interpret("Fix the holes", SUMMARY)
    assert caught.value.attempts == 3
    assert len(transport.requests) == 3


def test_multiple_intents_preserve_separation_and_order():
    combined = {
        "intents": [fixed_holes()["intents"][0], downward_force()["intents"][0]]
    }
    result = Interpreter(transport=SequenceTransport(combined)).interpret(
        "Fix the two bolt holes, then apply 5 kN downward to the upper face", SUMMARY
    )
    assert [intent.bc.type if intent.bc else intent.load.type for intent in result.intents] == [
        "fixed_displacement",
        "resultant_surface_force",
    ]


def test_mocked_tests_never_construct_openai_client(monkeypatch):
    def fail_live_client(*args, **kwargs):
        raise AssertionError("live OpenAI client must not be constructed")

    monkeypatch.setattr("llm.interpreter.OpenAI", fail_live_client)
    result = Interpreter(transport=SequenceTransport(pressure())).interpret("Apply pressure", SUMMARY)
    assert result.intents[0].load.type == "pressure"


def test_openai_boundary_uses_mocked_responses_structured_parse():
    class FakeResponses:
        def __init__(self):
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                output_parsed=OpenAIWireInterpretation.model_validate(
                    fixed_holes(), strict=True
                )
            )

    fake_client = SimpleNamespace(responses=FakeResponses())
    result = Interpreter(client=fake_client).interpret("Fix the holes", SUMMARY)
    assert result.intents[0].bc.type == "fixed_displacement"
    assert fake_client.responses.kwargs["text_format"] is OpenAIWireInterpretation
    assert fake_client.responses.kwargs["instructions"]
    assert fake_client.responses.kwargs["input"][0]["role"] == "user"


def _keyword_paths(value, keyword, path="$"):
    paths = []
    if isinstance(value, dict):
        if keyword in value:
            paths.append(f"{path}.{keyword}")
        for key, item in value.items():
            paths.extend(_keyword_paths(item, keyword, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_keyword_paths(item, keyword, f"{path}[{index}]"))
    return paths


def _object_schemas(value):
    objects = []
    if isinstance(value, dict):
        if value.get("type") == "object":
            objects.append(value)
        for item in value.values():
            objects.extend(_object_schemas(item))
    elif isinstance(value, list):
        for item in value:
            objects.extend(_object_schemas(item))
    return objects


def test_exact_openai_responses_schema_uses_supported_union_shape():
    response_format = type_to_response_format_param(OpenAIWireInterpretation)
    schema = response_format["json_schema"]["schema"]
    assert _keyword_paths(schema, "oneOf") == []
    assert _keyword_paths(schema, "discriminator") == []
    assert _keyword_paths(schema, "propertyNames") == []
    assert _keyword_paths(schema, "anyOf")
    assert _object_schemas(schema)
    assert all(obj.get("additionalProperties") is False for obj in _object_schemas(schema))
    assert "entity_ids" not in json.dumps(schema)


def test_openai_wire_payload_still_requires_internal_unit_validation():
    valid_wire = OpenAIWireInterpretation.model_validate(pressure(), strict=True)
    valid = Interpretation.model_validate(valid_wire.to_internal_payload(), strict=True)
    assert valid.intents[0].load.type == "pressure"
    invalid_pressure = pressure()
    invalid_pressure["intents"][0]["load"]["magnitude"] = "5 kN"
    invalid_wire = OpenAIWireInterpretation.model_validate(invalid_pressure, strict=True)
    with pytest.raises(ValueError, match="expected stress quantity"):
        Interpretation.model_validate(invalid_wire.to_internal_payload(), strict=True)


def test_openai_model_is_environment_configurable(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "test-openai-model")
    assert OpenAIStructuredOutputTransport(client=SimpleNamespace()).model == "test-openai-model"


def test_only_openai_provider_dependency_and_runtime_references_remain():
    repository = Path(__file__).resolve().parents[1]
    retired_provider = "".join(("anth", "ropic"))
    requirements = (repository / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "openai" in requirements
    assert retired_provider not in requirements
    for relative_path in ("llm/interpreter.py", "scripts/smoke_llm.py"):
        assert retired_provider not in (repository / relative_path).read_text(encoding="utf-8").lower()


def test_output_json_schema_forbids_unexpected_and_id_fields():
    schema_text = json.dumps(Interpretation.model_json_schema())
    assert '"oneOf"' in schema_text  # Internal discriminated models remain strongly typed.
    assert '"additionalProperties": false' in schema_text
    assert "entity_ids" not in schema_text


def test_smoke_script_without_api_key_fails_cleanly(monkeypatch, capsys):
    from scripts.smoke_llm import main

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke_llm.py"])
    assert main() == 2
    assert "no live request was made" in capsys.readouterr().err


def test_summary_with_direct_selection_data_is_rejected_before_request():
    unsafe_summary = deepcopy(SUMMARY.model_dump(mode="json"))
    unsafe_summary["face_ids"] = [1, 2]
    transport = SequenceTransport(fixed_holes())
    with pytest.raises(ValueError, match="forbidden direct entity selection"):
        Interpreter(transport=transport).interpret("Fix the holes", unsafe_summary)
    assert transport.requests == []
