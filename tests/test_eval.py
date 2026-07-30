"""Task 15 frozen evaluation, orchestration, fallback, and UI integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import LocalDataConfig
from app.orchestration import interpret_and_propose
from app.record_versions import load_fallback_record
from app.runtime_mode import RuntimeMode
from app.session import SelectionSessionStore
from app.server import create_app
from eval.harness import (
    HarnessPaths,
    ReplayTransport,
    _clicks,
    _contains_forbidden_id_key,
    evaluate_case,
    load_replay,
    persist_evaluation_setup_to_incomplete,
    render_markdown,
    run_evaluation,
    submit_evaluation_engineering_revision,
    write_report,
    write_live_unavailable_report,
)
from eval.schema import EvaluationCase, load_cases, manifest_hash
from export.abaqus_py import export_abaqus_py
from export.common import CadModelMetadata, ExportNotReadyError
from geom.cylinders import analyze_cylinders
from geom.inventory import get_inventory
from ground.engine import GroundingEngine
from llm.interpreter import (
    CombinedMaterialInputError,
    Interpreter,
    InterpreterProviderError,
    OpenAIStructuredOutputTransport,
    summarize_face_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
PATHS = HarnessPaths.from_root(ROOT)


@pytest.fixture(scope="module")
def cases():
    return load_cases(PATHS.case_dir, fixture_dir=PATHS.fixture_dir)


@pytest.fixture(scope="module")
def replay_report():
    return run_evaluation(root=ROOT, mode="REPLAY")


def by_id(cases, case_id: str) -> EvaluationCase:
    return next(case for case in cases if case.case_id == case_id)


async def _request(app, method: str, path: str, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs):
    return asyncio.run(_request(app, method, path, **kwargs))


def upload(app, fixture: str = "bracket.step") -> str:
    path = PATHS.fixture_dir / fixture
    response = request(app, "POST", "/models", files={"file": (fixture, path.read_bytes(), "application/step")})
    assert response.status_code == 201
    return response.json()["id"]


def replay_intent(case: EvaluationCase):
    fixture = PATHS.fixture_dir / case.model_fixture
    inventory, _ = get_inventory(fixture)
    proposal = interpret_and_propose(
        instruction=case.instruction,
        inventory=inventory,
        cylinders=analyze_cylinders(fixture),
        interpreter=Interpreter(
            transport=ReplayTransport(load_replay(case, PATHS.replay_dir))
        ),
        click_evidence_by_intent=_clicks(case, inventory),
    )
    assert proposal.intent is not None
    assert not proposal.clarifications
    return proposal.intent


def test_exactly_15_case_files_exist(cases):
    assert len(list(PATHS.case_dir.glob("*.json"))) == len(cases) == 15


def test_case_ids_are_unique(cases):
    assert len({case.case_id for case in cases}) == 15


def test_both_reference_models_are_represented(cases):
    assert {case.model_fixture for case in cases} == {"bracket.step", "plate_hole.step"}


def test_every_case_validates_against_typed_schema(cases):
    for case in cases:
        assert EvaluationCase.model_validate(case.model_dump(mode="python"), strict=True) == case


def test_every_expected_entity_exists(cases):
    for case in cases:
        inventory, _ = get_inventory(PATHS.fixture_dir / case.model_fixture)
        known = {face.tag for face in inventory.faces}
        assert all(set(condition.entity_ids) <= known for condition in case.expected_conditions)


def test_evaluation_ordering_is_deterministic(cases):
    assert [case.case_id for case in cases] == [case.case_id for case in load_cases(PATHS.case_dir)]


def test_ground_truth_is_not_in_interpreter_request(cases):
    case = by_id(cases, "bracket_bolt_holes_fixed")
    payload = load_replay(case, PATHS.replay_dir)
    transport = ReplayTransport(payload)
    inventory, _ = get_inventory(PATHS.fixture_dir / case.model_fixture)
    from llm.interpreter import summarize_face_inventory
    Interpreter(transport=transport).interpret(case.instruction, summarize_face_inventory(inventory, analyze_cylinders(PATHS.fixture_dir / case.model_fixture)))
    prompt = transport.payload
    assert not _contains_forbidden_id_key(prompt)
    assert "expected_conditions" not in json.dumps(prompt)


def test_replay_outputs_contain_no_entity_ids(cases):
    assert all(not _contains_forbidden_id_key(load_replay(case, PATHS.replay_dir)) for case in cases)


def test_runner_calls_production_interpreter_interface(cases, monkeypatch):
    calls = []
    original = Interpreter.interpret
    monkeypatch.setattr(Interpreter, "interpret", lambda self, *args, **kwargs: (calls.append(args[0]), original(self, *args, **kwargs))[1])
    result = evaluate_case(by_id(cases, "bracket_bottom_fixed"), paths=PATHS, mode="REPLAY")
    assert result.status == "PASS" and calls == ["Fix the bottom face."]


def test_runner_calls_production_grounding_engine(cases, monkeypatch):
    calls = []
    original = GroundingEngine.ground_interpretation
    monkeypatch.setattr(GroundingEngine, "ground_interpretation", lambda self, *args, **kwargs: (calls.append(args[0]), original(self, *args, **kwargs))[1])
    result = evaluate_case(by_id(cases, "bracket_bottom_fixed"), paths=PATHS, mode="REPLAY")
    assert result.status == "PASS" and calls


def test_evaluation_engineering_update_is_one_durable_idempotent_revision(
    tmp_path, cases, monkeypatch
):
    case = by_id(cases, "bracket_bottom_fixed")
    intent = replay_intent(case)
    app = create_app(
        tmp_path / "legacy",
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(tmp_path / "durable-data"),
    )

    with TestClient(app) as client:
        setup_id, incomplete = persist_evaluation_setup_to_incomplete(
            client,
            case=case,
            intent=intent,
            fixture=PATHS.fixture_dir / case.model_fixture,
        )
        assert incomplete["validation"]["readiness_status"] == (
            "structurally_incomplete"
        )
        assert incomplete["intent"]["mesh_settings"] is None
        assert incomplete["intent"]["solver_settings"] is None
        before = client.get(f"/api/v1/setups/{setup_id}/revisions").json()

        def reject_new_session_store(*args, **kwargs):
            raise AssertionError(
                "engineering revision created a SelectionSessionStore"
            )

        monkeypatch.setattr(
            SelectionSessionStore,
            "__init__",
            reject_new_session_store,
        )
        configured, request_body = submit_evaluation_engineering_revision(
            client,
            case_id=case.case_id,
            setup_id=setup_id,
            incomplete_revision=incomplete,
        )

        after = client.get(f"/api/v1/setups/{setup_id}/revisions").json()
        assert len(after) == len(before) + 1
        assert configured["revision"] == incomplete["revision"] + 1
        assert configured["parent_revision_id"] == incomplete["id"]
        assert configured["request_id"] == (
            f"evaluation-{case.case_id}-engineering-v1"
        )

        prior = client.get(
            f"/api/v1/setups/{setup_id}/revisions/{incomplete['revision']}"
        )
        assert prior.status_code == 200
        assert prior.json()["id"] == incomplete["id"]
        assert prior.json()["intent_sha256"] == incomplete["intent_sha256"]
        assert prior.json()["intent"]["mesh_settings"] is None

        replay = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json=request_body,
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == configured["id"]

        changed = json.loads(json.dumps(request_body))
        changed["intent"]["mesh_settings"]["global_element_size_mm"] = 2.0
        changed["intent"]["mesh_settings"]["target_size_original"]["value"] = 2.0
        conflict = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json=changed,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "setup_request_id_conflict"

        stale = json.loads(json.dumps(request_body))
        stale["request_id"] = f"evaluation-{case.case_id}-stale-v1"
        stale_response = client.post(
            f"/api/v1/setups/{setup_id}/revisions",
            json=stale,
        )
        assert stale_response.status_code == 409
        assert stale_response.json()["code"] == "setup_revision_conflict"

        final = client.get(f"/api/v1/setups/{setup_id}/revisions").json()
        assert [item["id"] for item in final] == [item["id"] for item in after]


def test_click_evidence_only_for_marked_cases(cases):
    for case in cases:
        inventory, _ = get_inventory(PATHS.fixture_dir / case.model_fixture)
        assert bool(_clicks(case, inventory)) == bool(case.click_evidence)


def test_required_clarification_is_recorded(cases):
    result = evaluate_case(by_id(cases, "bracket_inner_pressure_clarify"), paths=PATHS, mode="REPLAY")
    assert result.status == "PASS_AFTER_CLARIFICATION"
    assert result.clarification_observed and result.clarifications_used == 1


def test_more_than_one_clarification_fails(cases, monkeypatch):
    import eval.harness as harness
    case = by_id(cases, "bracket_left_side_clarify")
    fixture = PATHS.fixture_dir / case.model_fixture
    inventory, _ = get_inventory(fixture)
    interpretation = Interpreter(transport=ReplayTransport(load_replay(case, PATHS.replay_dir))).interpret(
        case.instruction,
        __import__("llm.interpreter", fromlist=["summarize_face_inventory"]).summarize_face_inventory(inventory, analyze_cylinders(fixture)),
    )
    unresolved = harness.propose_from_interpretation(
        instruction=case.instruction, interpretation=interpretation, inventory=inventory,
        cylinders=analyze_cylinders(fixture), click_evidence_by_intent={},
    )
    monkeypatch.setattr(harness, "propose_from_interpretation", lambda **kwargs: unresolved)
    result = evaluate_case(case, paths=PATHS, mode="REPLAY")
    assert result.status == "FAIL" and "second clarification" in (result.harness_error or "")


def test_ambiguous_auto_selection_is_ambiguity_unflagged(cases):
    case = by_id(cases, "bracket_left_side_clarify")

    class AlreadyInterpretedAutoSelection:
        def interpret(self, instruction, inventory_summary):
            from llm.interpreter import Interpretation

            return Interpretation.model_validate(
                {
                    "intents": [
                        {
                            "op_list": [{"op": "labeled", "name": "left_face"}],
                            "bc": {
                                "type": "fixed_displacement",
                                "components": ["x", "y", "z"],
                            },
                            "load": None,
                            "target_description": "the left face",
                        }
                    ]
                },
                strict=True,
            )

    result = evaluate_case(
        case,
        paths=PATHS,
        mode="LIVE",
        live_interpreter=AlreadyInterpretedAutoSelection(),
    )
    assert result.failure_category == "ambiguity-unflagged"


def test_incorrect_entities_are_grounding(cases):
    case = by_id(cases, "bracket_bottom_fixed").model_copy(deep=True)
    case.expected_conditions[0].entity_ids = [7]
    result = evaluate_case(case, paths=PATHS, mode="REPLAY")
    assert result.failure_category == "grounding"


def test_incorrect_normalized_value_is_unit(cases):
    case = by_id(cases, "bracket_top_force_5kn").model_copy(deep=True)
    case.expected_conditions[0].vector = [0.0, -5.0, 0.0]
    result = evaluate_case(case, paths=PATHS, mode="REPLAY")
    assert result.failure_category == "unit"


def test_malformed_model_output_is_llm_parse(cases):
    interpreter = Interpreter(transport=ReplayTransport({"wrong": []}), max_retries=0)
    result = evaluate_case(by_id(cases, "bracket_bottom_fixed"), paths=PATHS, mode="LIVE", live_interpreter=interpreter)
    assert result.failure_category == "llm-parse"


def test_exact_entity_set_comparison(cases):
    case = by_id(cases, "bracket_bolt_holes_fixed").model_copy(deep=True)
    case.expected_conditions[0].entity_ids = [11]
    assert evaluate_case(case, paths=PATHS, mode="REPLAY").status == "FAIL"


def test_condition_type_comparison(cases):
    case = by_id(cases, "bracket_top_force_5kn").model_copy(deep=True)
    case.expected_conditions[0].condition_type = "surface_traction"
    assert evaluate_case(case, paths=PATHS, mode="REPLAY").failure_category == "unit"


def test_vector_and_component_comparison(cases):
    force = by_id(cases, "bracket_top_force_5kn").model_copy(deep=True)
    force.expected_conditions[0].vector = [0.0, 5000.0, 0.0]
    vertical = by_id(cases, "bracket_vertical_click").model_copy(deep=True)
    vertical.expected_conditions[0].components = ["z"]
    assert evaluate_case(force, paths=PATHS, mode="REPLAY").status == "FAIL"
    assert evaluate_case(vertical, paths=PATHS, mode="REPLAY").status == "FAIL"


def test_expected_ir_comparison(cases):
    case = by_id(cases, "bracket_bottom_fixed").model_copy(deep=True)
    case.expected_conditions[0].expected_ir_subset["region_ref"] = "wrong"
    assert evaluate_case(case, paths=PATHS, mode="REPLAY").status == "FAIL"


def test_pass_after_clarification_counts_toward_score(replay_report):
    assert replay_report.pass_after_clarification_count == 2
    assert replay_report.score == replay_report.pass_count + 2


def test_failures_remain_in_rendered_results(replay_report):
    failed = replay_report.model_copy(update={"cases": [replay_report.cases[0].model_copy(update={"status":"FAIL", "failure_category":"grounding"})], "fail_count":1})
    assert "FAIL" in render_markdown(failed) and "grounding" in render_markdown(failed)


def test_threshold_calculation(replay_report):
    assert replay_report.total == 15 and replay_report.score == 15 and replay_report.threshold_achieved


def test_cli_below_threshold_returns_failure(monkeypatch, replay_report):
    import eval.run as run
    low = replay_report.model_copy(update={"score": 11, "threshold_achieved": False})
    monkeypatch.setattr(run, "run_evaluation", lambda **kwargs: low)
    monkeypatch.setattr(run, "write_report", lambda *args, **kwargs: (ROOT / "eval" / "x.md", ROOT / "eval" / "x.json"))
    assert run.main(["--replay"]) == 1


def test_cli_threshold_returns_success(monkeypatch, replay_report):
    import eval.run as run
    monkeypatch.setattr(run, "run_evaluation", lambda **kwargs: replay_report)
    monkeypatch.setattr(run, "write_report", lambda *args, **kwargs: (ROOT / "eval" / "x.md", ROOT / "eval" / "x.json"))
    assert run.main(["--replay"]) == 0


def test_replay_markdown_is_deterministic(replay_report):
    assert render_markdown(replay_report) == render_markdown(replay_report)


def test_live_report_preserves_first_scored_run_separately(tmp_path, replay_report):
    (tmp_path / "eval").mkdir()
    initial = replay_report.model_copy(
        update={
            "mode": "LIVE",
            "pass_count": 12,
            "pass_after_clarification_count": 1,
            "fail_count": 2,
            "score": 13,
            "threshold_achieved": True,
        },
        deep=True,
    )
    final = initial.model_copy(
        update={"pass_count": 13, "fail_count": 1, "score": 14}, deep=True
    )
    write_report(initial, root=tmp_path)
    write_report(final, root=tmp_path)
    preserved = json.loads(
        (tmp_path / "eval" / "results-live-initial.json").read_text(encoding="utf-8")
    )
    current = json.loads((tmp_path / "eval" / "results.json").read_text(encoding="utf-8"))
    assert preserved["score"] == 13 and preserved["fail_count"] == 2
    assert current["score"] == 14 and current["fail_count"] == 1


def test_results_contain_manifest_hash(cases, replay_report):
    assert replay_report.manifest_hash == manifest_hash(cases)
    assert replay_report.manifest_hash in render_markdown(replay_report)


def test_results_contain_per_case_taxonomy(replay_report):
    assert all(hasattr(case, "failure_category") for case in replay_report.cases)


def test_results_contain_no_secrets_or_absolute_paths(replay_report):
    text = json.dumps(replay_report.model_dump(mode="json"))
    assert "OPENAI_API_KEY" not in text and "Authorization" not in text
    assert "C:\\" not in text and str(ROOT) not in text


def test_fallback_typed_outputs_contain_no_entity_ids(cases):
    for case in cases:
        payload = json.loads((PATHS.fallback_dir / f"{case.case_id}.json").read_text(encoding="utf-8"))
        assert not _contains_forbidden_id_key(payload["typed_interpreter_output"])


def test_fallback_never_confirms_regions(cases):
    for case in cases:
        payload = json.loads((PATHS.fallback_dir / f"{case.case_id}.json").read_text(encoding="utf-8"))
        assert all(region["status"] == "proposed" for region in payload["proposed_ir"]["regions"])


def test_designated_case_reaches_validation(replay_report):
    result = next(case for case in replay_report.cases if case.case_id == "bracket_combined_export")
    assert result.validation_status == "valid"


def test_designated_case_truthfully_blocks_without_cad_mapping(replay_report):
    result = next(case for case in replay_report.cases if case.case_id == "bracket_combined_export")
    assert result.export_result == {
        "status": "blocked",
        "code": "missing_region_mapping",
        "validation_status": "valid",
        "export_eligible": False,
    }
    assert "adapter" not in result.export_result
    assert "filename" not in result.export_result


def test_evaluation_fails_if_production_orchestration_injects_material(
    monkeypatch, cases
):
    import app.orchestration as orchestration
    from ir.schema import Material

    original = orchestration._build_intent

    def injecting_build_intent(**kwargs):
        intent = original(**kwargs)
        injected = Material(
            name="forbidden-production-material",
            model="linear_elastic_isotropic",
            authority="engineer_entered",
            E_MPa=1.0,
            nu=0.0,
        )
        return intent.model_copy(update={"materials": [injected]}, deep=True)

    monkeypatch.setattr(orchestration, "_build_intent", injecting_build_intent)
    result = evaluate_case(
        by_id(cases, "bracket_bottom_fixed"),
        paths=PATHS,
        mode="REPLAY",
    )
    assert result.status == "FAIL"
    assert result.failure_category == "material"


def test_blocked_intents_remain_blocked(cases):
    result = evaluate_case(by_id(cases, "bracket_combined_export"), paths=PATHS, mode="REPLAY")
    fallback_path = PATHS.fallback_dir / "bracket_combined_export.json"
    _, intent = load_fallback_record(
        fallback_path.read_text(encoding="utf-8"),
        source="eval/fallback/bracket_combined_export.json",
    )
    from ir.validate import validate_intent
    assert result.status == "PASS" and not validate_intent(intent).export_eligible


def test_export_eligibility_cannot_be_bypassed():
    fallback_path = PATHS.fallback_dir / "bracket_combined_export.json"
    _, intent = load_fallback_record(
        fallback_path.read_text(encoding="utf-8"),
        source="eval/fallback/bracket_combined_export.json",
    )
    inventory, _ = get_inventory(PATHS.fixture_dir / "bracket.step")
    metadata = CadModelMetadata(source_path=PATHS.fixture_dir / "bracket.step", source_name="bracket.step", source_sha256=inventory.file_sha256, source_cad_face_tags=tuple(face.tag for face in inventory.faces))
    with pytest.raises(ExportNotReadyError):
        export_abaqus_py(intent, metadata)


def test_pytest_replay_makes_no_openai_request(monkeypatch):
    monkeypatch.setattr(OpenAIStructuredOutputTransport, "complete", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called")))
    assert run_evaluation(root=ROOT, mode="REPLAY").score == 15


def test_no_evaluation_case_specific_branch_in_production(cases):
    production = "\n".join(path.read_text(encoding="utf-8") for folder in ("app", "ground", "llm", "ir", "export") for path in (ROOT / folder).rglob("*.py"))
    assert all(case.case_id not in production for case in cases)
    assert "Fix both bolt holes." not in production and "Fix the left side." not in production


def test_server_live_proposal_uses_session_and_blocks_export(tmp_path, cases):
    app = create_app(tmp_path / "models")
    case = by_id(cases, "bracket_bolt_holes_fixed")
    app.state.interpreter = Interpreter(transport=ReplayTransport(load_replay(case, PATHS.replay_dir)))
    model_id = upload(app)
    response = request(app, "POST", f"/session/{model_id}/interpret", json={"instruction": case.instruction, "clicked_entity_ids": []})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "LIVE" and body["state"] == "proposed"
    assert "entity_ids" not in body["intent"]["regions"][0]
    assert body["intent"]["regions"][0]["cad_face_target"][
        "source_face_tags"
    ] == [11, 12]
    assert request(app, "POST", f"/session/{model_id}/export-gate").status_code == 409


def test_server_one_clarification_remains_proposed(tmp_path, cases):
    app = create_app(tmp_path / "models")
    case = by_id(cases, "bracket_left_side_clarify")
    app.state.interpreter = Interpreter(transport=ReplayTransport(load_replay(case, PATHS.replay_dir)))
    model_id = upload(app)
    first = request(app, "POST", f"/session/{model_id}/interpret", json={"instruction": case.instruction, "clicked_entity_ids": []})
    assert first.json()["state"] == "clarification"
    chosen = request(app, "POST", f"/session/{model_id}/clarify", json={"intent_index": 0, "entity_ids": [1]})
    assert chosen.status_code == 200
    assert chosen.json()["intent"]["regions"][0]["status"] == "proposed"
    assert request(app, "POST", f"/session/{model_id}/clarify", json={"intent_index": 0, "entity_ids": [1]}).status_code == 409


def test_vertical_click_uses_central_y_axis_even_if_model_declares_z(tmp_path, cases):
    app = create_app(tmp_path / "models")
    case = by_id(cases, "bracket_vertical_click")
    payload = json.loads(json.dumps(load_replay(case, PATHS.replay_dir)))
    payload["intents"][0]["bc"]["components"] = ["z"]
    app.state.interpreter = Interpreter(transport=ReplayTransport(payload))
    model_id = upload(app)

    response = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": case.instruction, "clicked_entity_ids": [5]},
    )

    assert response.status_code == 200
    intent = response.json()["intent"]
    assert intent["bcs"][0]["components"] == ["y"]
    assert "entity_ids" not in intent["regions"][0]
    assert intent["regions"][0]["cad_face_target"]["source_face_tags"] == [5]
    assert intent["regions"][0]["selection_method"] == "user_click"
    assert any(
        "Vertical motion was interpreted as the Y displacement component"
        in assumption["text"]
        for assumption in intent["assumptions"]
    )


def test_vague_lateral_side_label_returns_real_clarification_candidates(
    tmp_path, cases
):
    app = create_app(tmp_path / "models")
    case = by_id(cases, "bracket_left_side_clarify")
    payload = {
        "intents": [
            {
                "op_list": [{"op": "labeled", "name": "left_face"}],
                "bc": {"type": "fixed_displacement", "components": ["x", "y", "z"]},
                "load": None,
                "target_description": "the left side",
            }
        ]
    }
    app.state.interpreter = Interpreter(transport=ReplayTransport(payload))
    model_id = upload(app)

    first = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": case.instruction, "clicked_entity_ids": []},
    )
    assert first.status_code == 200
    assert first.json()["state"] == "clarification"
    candidates = first.json()["grounding"]["results"][0]["clarification"][
        "candidate_sets"
    ]
    assert len(candidates) > 1
    assert any(candidate["entity_ids"] == [1] for candidate in candidates)
    assert first.json()["intent"] is None

    chosen = request(
        app,
        "POST",
        f"/session/{model_id}/clarify",
        json={"intent_index": 0, "entity_ids": [1]},
    )
    assert chosen.status_code == 200
    assert chosen.json()["intent"]["regions"][0]["cad_face_target"][
        "source_face_tags"
    ] == [1]
    assert chosen.json()["intent"]["regions"][0]["status"] == "proposed"


def test_server_fallback_is_labeled_and_not_confirmed(tmp_path):
    app = create_app(tmp_path / "models")
    model_id = upload(app)
    listed = request(app, "GET", f"/session/{model_id}/fallback-cases")
    assert "bracket_combined_export" in listed.json()["case_ids"]
    loaded = request(app, "POST", f"/session/{model_id}/fallback/bracket_combined_export")
    assert loaded.status_code == 200 and loaded.json()["mode"] == "REPLAY"
    assert all(region["status"] == "proposed" for region in loaded.json()["intent"]["regions"])


def test_raw_fixture_hashes_match_git_archive_and_reject_different_bytes(tmp_path):
    fixture_names = ("bracket.step", "plate_hole.step")
    git_executable = shutil.which("git")
    assert git_executable is not None
    attribute_lines = {
        line.strip()
        for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    }
    assert {"*.step -text", "*.stp -text", "*.inp -text", "*.stl -text"} <= attribute_lines
    archive_path = tmp_path / "fixtures.zip"
    subprocess.run(
        [
            git_executable,
            "archive",
            "--format=zip",
            f"--output={archive_path}",
            "HEAD",
            "--",
            *(f"tests/fixtures/{name}" for name in fixture_names),
        ],
        cwd=ROOT,
        check=True,
    )
    extracted = tmp_path / "archive"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)

    for name in fixture_names:
        checkout_bytes = (PATHS.fixture_dir / name).read_bytes()
        archive_bytes = (extracted / "tests" / "fixtures" / name).read_bytes()
        assert archive_bytes == checkout_bytes
        assert hashlib.sha256(archive_bytes).hexdigest() == hashlib.sha256(checkout_bytes).hexdigest()

    attributes = subprocess.run(
        [
            git_executable,
            "check-attr",
            "text",
            "--",
            *(f"tests/fixtures/{name}" for name in fixture_names),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert attributes.count("text: unset") == len(fixture_names)

    archived_bracket = (extracted / "tests" / "fixtures" / "bracket.step").read_bytes()
    app = create_app(tmp_path / "archive-models")
    uploaded = request(
        app,
        "POST",
        "/models",
        files={"file": ("bracket.step", archived_bracket, "application/step")},
    )
    assert uploaded.status_code == 201
    model_id = uploaded.json()["id"]
    listed = request(app, "GET", f"/session/{model_id}/fallback-cases")
    assert listed.status_code == 200
    assert "bracket_combined_export" in listed.json()["case_ids"]

    different_bytes = archived_bracket.replace(b"\n", b"\r\n")
    assert different_bytes != archived_bracket
    mismatched = request(
        app,
        "POST",
        "/models",
        files={"file": ("bracket.step", different_bytes, "application/step")},
    )
    assert mismatched.status_code == 201
    mismatched_id = mismatched.json()["id"]
    mismatch_list = request(app, "GET", f"/session/{mismatched_id}/fallback-cases")
    assert mismatch_list.status_code == 200
    assert "bracket_combined_export" not in mismatch_list.json()["case_ids"]
    rejected = request(
        app,
        "POST",
        f"/session/{mismatched_id}/fallback/bracket_combined_export",
    )
    assert rejected.status_code == 422


def test_frontend_exposes_instruction_clarification_and_fallback(tmp_path):
    app = create_app(tmp_path / "models")
    html = request(app, "GET", "/").text
    javascript = request(app, "GET", "/static/app.js").text
    for marker in ("instruction-input", "interpret-button", "clarification-candidates", "fallback-case"):
        assert marker in html
    assert "/interpret" in javascript and "/clarify" in javascript and "/fallback/" in javascript


def test_missing_api_key_is_a_safe_provider_configuration_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    fixture = PATHS.fixture_dir / "bracket.step"
    inventory, _ = get_inventory(fixture)
    with pytest.raises(InterpreterProviderError) as caught:
        Interpreter(max_retries=0).interpret(
            "Prevent vertical motion on this face.",
            summarize_face_inventory(inventory, analyze_cylinders(fixture)),
        )
    assert caught.value.code == "provider_not_configured"
    assert "OPENAI_API_KEY" in caught.value.safe_message


def test_interpret_endpoint_returns_structured_503_without_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    app = create_app(tmp_path / "models")
    model_id = upload(app)
    response = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={
            "instruction": "Prevent vertical motion on this face.",
            "clicked_entity_ids": [5],
        },
    )
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "provider_not_configured",
            "message": (
                "Live interpretation is unavailable because the OpenAI provider is not configured. "
                "Set OPENAI_API_KEY on the server or use a clearly labeled REPLAY fallback case."
            ),
            "mode": "LIVE",
            "fallback_available": True,
        }
    }


def test_provider_failure_does_not_corrupt_session_or_pending_state(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    app = create_app(tmp_path / "models")
    model_id = upload(app)
    before = request(app, "GET", f"/session/{model_id}/intent").json()
    failed = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": "Prevent vertical motion on this face.", "clicked_entity_ids": [5]},
    )
    after = request(app, "GET", f"/session/{model_id}/intent").json()
    assert failed.status_code == 503
    assert before == after
    assert after["intent"] is None and after["selected_entities"] == {}
    assert model_id not in app.state.pending_interpretations


def test_material_input_returns_structured_unsupported_response_without_session_mutation(
    tmp_path, cases
):
    app = create_app(tmp_path / "models")
    replay = ReplayTransport(
        load_replay(by_id(cases, "bracket_combined_export"), PATHS.replay_dir)
    )
    app.state.interpreter = Interpreter(transport=replay, max_retries=2)
    model_id = upload(app)
    instruction = (
        "Use steel with Young's modulus 210 GPa, Poisson's ratio 0.3, and density "
        "7850 kg/m^3. Fix both bolt holes, apply a total downward force of 5 kN "
        "to the top flange, and apply gravity in negative Z."
    )

    failed = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": instruction, "clicked_entity_ids": []},
    )
    snapshot = request(app, "GET", f"/session/{model_id}/intent")

    assert failed.status_code == 422
    assert failed.json() == {
        "detail": {
            "code": "material.combined_request_requires_separation",
            "message": CombinedMaterialInputError.safe_message,
            "mode": "LIVE",
            "supported_mechanism": "explicit_numeric_isotropic_properties",
        }
    }
    assert replay.calls == 0
    assert snapshot.status_code == 200
    assert snapshot.json()["intent"] is None
    assert model_id not in app.state.pending_interpretations


def test_frontend_exposes_provider_error_and_clears_busy_state():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="interpret-error"' in html and 'role="alert"' in html
    assert "interpretError.hidden = false" in javascript
    assert "typedOutput.textContent = `Interpretation unavailable:" in javascript
    assert 'interpretButton.removeAttribute("aria-busy")' in javascript
    assert 'interpretButton.textContent = "Interpret"' in javascript
    interpret_function = javascript.split("async function interpretInstruction()", 1)[1].split(
        "clarificationCandidates.addEventListener", 1
    )[0]
    assert "selectedClicks.clear" not in interpret_function


def test_same_instruction_twice_does_not_duplicate_or_change_review_state(tmp_path, cases):
    app = create_app(tmp_path / "models")
    case = by_id(cases, "bracket_bolt_holes_fixed")
    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(case, PATHS.replay_dir))
    )
    model_id = upload(app)
    request_body = {"instruction": case.instruction, "clicked_entity_ids": []}

    first = request(app, "POST", f"/session/{model_id}/interpret", json=request_body)
    second = request(app, "POST", f"/session/{model_id}/interpret", json=request_body)

    assert first.status_code == second.status_code == 200
    assert second.json()["intent"] == first.json()["intent"]
    assert len(second.json()["intent"]["regions"]) == 1
    assert len(second.json()["intent"]["bcs"]) == 1
    assert second.json()["intent"]["regions"][0]["status"] == "proposed"
    assert second.json()["notices"] == [
        "Equivalent condition already exists; duplicate was not added. "
        f"New source instruction: {case.instruction}"
    ]


def test_combined_prompt_omits_repeated_condition_but_adds_force_and_gravity(
    tmp_path, cases
):
    app = create_app(tmp_path / "models")
    fixed = by_id(cases, "bracket_bolt_holes_fixed")
    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(fixed, PATHS.replay_dir))
    )
    model_id = upload(app)
    first = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": fixed.instruction, "clicked_entity_ids": []},
    )
    assert first.status_code == 200

    combined = json.loads(
        json.dumps(load_replay(by_id(cases, "bracket_combined_export"), PATHS.replay_dir))
    )
    gravity = load_replay(by_id(cases, "bracket_gravity_neg_z"), PATHS.replay_dir)
    combined["intents"].append(gravity["intents"][0])
    combined_instruction = (
        "Fix both bolt holes, apply 5 kN downward on the top flange, and include gravity."
    )
    app.state.interpreter = Interpreter(transport=ReplayTransport(combined))
    response = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": combined_instruction, "clicked_entity_ids": []},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["notices"]) == 1
    assert combined_instruction in body["notices"][0]
    assert len(body["intent"]["bcs"]) == 1
    assert len(body["intent"]["loads"]) == 2
    assert len(body["intent"]["regions"]) == 2
    assert (
        sum(
            region["cad_face_target"]["source_face_tags"] == [11, 12]
            for region in body["intent"]["regions"]
        )
        == 1
    )
    gravity_loads = [load for load in body["intent"]["loads"] if load["type"] == "gravity"]
    assert len(gravity_loads) == 1 and gravity_loads[0]["region_ref"] is None


def test_equivalent_wording_and_units_deduplicate_after_normalization(tmp_path, cases):
    app = create_app(tmp_path / "models")
    first_case = by_id(cases, "plate_top_force_5000n")
    second_case = by_id(cases, "plate_top_force_5kn")
    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(first_case, PATHS.replay_dir))
    )
    model_id = upload(app, "plate_hole.step")
    first = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": first_case.instruction, "clicked_entity_ids": []},
    )
    assert first.status_code == 200

    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(second_case, PATHS.replay_dir))
    )
    second = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": second_case.instruction, "clicked_entity_ids": []},
    )

    assert second.status_code == 200
    assert len(second.json()["intent"]["regions"]) == 1
    assert len(second.json()["intent"]["loads"]) == 1
    assert second.json()["intent"]["loads"][0]["vector"] == [0.0, -5000.0, 0.0]
    assert len(second.json()["notices"]) == 1


def test_same_entities_with_different_condition_types_remain_separate(tmp_path, cases):
    app = create_app(tmp_path / "models")
    fixed = by_id(cases, "bracket_vertical_click")
    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(fixed, PATHS.replay_dir))
    )
    model_id = upload(app)
    first = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": fixed.instruction, "clicked_entity_ids": [5]},
    )
    assert first.status_code == 200

    app.state.interpreter = Interpreter(
        transport=ReplayTransport(
            {
                "intents": [
                    {
                        "op_list": [{"op": "find_faces", "surface_type": "Plane"}],
                        "bc": None,
                        "load": {"type": "pressure", "magnitude": "2 MPa"},
                        "target_description": "this face",
                    }
                ]
            }
        )
    )
    second = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": "Apply 2 MPa pressure here.", "clicked_entity_ids": [5]},
    )

    assert second.status_code == 200
    assert second.json()["notices"] == []
    assert len(second.json()["intent"]["regions"]) == 2
    assert all(
        "entity_ids" not in region
        and region["cad_face_target"]["source_face_tags"] == [5]
        for region in second.json()["intent"]["regions"]
    )
    assert len(second.json()["intent"]["bcs"]) == 1
    assert len(second.json()["intent"]["loads"]) == 1


def test_same_entities_with_different_displacement_components_remain_separate(
    tmp_path, cases
):
    app = create_app(tmp_path / "models")
    first_case = by_id(cases, "bracket_vertical_click")
    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(first_case, PATHS.replay_dir))
    )
    model_id = upload(app)
    first = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": first_case.instruction, "clicked_entity_ids": [5]},
    )
    assert first.status_code == 200

    app.state.interpreter = Interpreter(
        transport=ReplayTransport(
            {
                "intents": [
                    {
                        "op_list": [{"op": "find_faces", "surface_type": "Plane"}],
                        "bc": {"type": "fixed_displacement", "components": ["z"]},
                        "load": None,
                        "target_description": "this face",
                    }
                ]
            }
        )
    )
    second = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": "Prevent Z motion here.", "clicked_entity_ids": [5]},
    )

    assert second.status_code == 200
    assert second.json()["notices"] == []
    assert len(second.json()["intent"]["regions"]) == 2
    assert [bc["components"] for bc in second.json()["intent"]["bcs"]] == [["y"], ["z"]]


def test_repeated_gravity_is_one_load_and_step_target_stays_blocked(tmp_path, cases):
    app = create_app(tmp_path / "models")
    case = by_id(cases, "bracket_gravity_neg_z")
    app.state.interpreter = Interpreter(
        transport=ReplayTransport(load_replay(case, PATHS.replay_dir))
    )
    model_id = upload(app)
    request_body = {"instruction": case.instruction, "clicked_entity_ids": []}

    first = request(app, "POST", f"/session/{model_id}/interpret", json=request_body)
    second = request(app, "POST", f"/session/{model_id}/interpret", json=request_body)

    assert first.status_code == second.status_code == 200
    intent = second.json()["intent"]
    assert intent["regions"] == []
    assert len(intent["loads"]) == 1
    assert intent["loads"][0]["type"] == "gravity"
    assert intent["loads"][0]["region_ref"] is None
    assert intent["materials"] == []
    # Interpreting an instruction approves no engineering configuration: the
    # schema-version-2 decisions stay explicitly missing until an engineer
    # states them.
    assert intent["analysis"]["dimensionality"] is None
    assert intent["analysis"]["solver_target"] is None
    assert intent["analysis"]["coordinate_system"] is None
    assert intent["mesh_settings"] is None
    assert intent["solver_settings"] is None
    assert all("material" not in item["text"].lower() for item in intent["assumptions"])
    assert len(second.json()["notices"]) == 1

    # A gravity-only setup has no constraint and no engineering configuration,
    # so it is structurally incomplete and cannot export.  Supply both through
    # the normal revision route rather than lowering the bar for the export
    # assertions below.
    incomplete = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "abaqus_py"},
    )
    assert incomplete.status_code == 409
    assert "bc.missing" in incomplete.text
    assert "mesh.missing" in incomplete.text
    assert "solver.missing" in incomplete.text
    assert "analysis.dimensionality_missing" in incomplete.text

    supported = dict(intent)
    supported["analysis"] = {
        **intent["analysis"],
        "dimensionality": "3d_solid",
        "solver_target": "calculix",
        "coordinate_system": "global_cartesian",
    }
    supported["mesh_settings"] = {
        "global_element_size_mm": 1.0,
        "element_type": "tetrahedral",
        "element_order": "first_order",
        "mesher": "gmsh",
        "mesher_preset": "gmsh_tet_v1",
        "target_size_original": {"value": 1.0, "unit": "mm"},
    }
    supported["solver_settings"] = {
        "target": "calculix",
        "analysis_profile": "linear_static_v1",
        "requested_results": ["displacement", "stress", "reaction_force"],
    }
    supported["materials"] = [{
        "name": "engineer_steel",
        "model": "linear_elastic_isotropic",
        "authority": "engineer_entered",
        "E_MPa": 210000.0,
        "nu": 0.3,
        "density_tonne_per_mm3": 7.85e-9,
    }]
    supported["regions"] = [
        {
            "id": "support_face",
            "entity_type": "cad_face",
            "cad_face_target": {
                "resolution": "unresolved",
                "source_face_tags": [1],
            },
            "selection_method": "user_click",
            "confidence": 1.0,
            "source_instruction": "Fix the mounting face.",
            "status": "proposed",
        }
    ]
    supported["bcs"] = [
        {
            "type": "fixed_displacement",
            "region_ref": "support_face",
            "components": ["x", "y", "z"],
        }
    ]
    saved = request(app, "PUT", f"/session/{model_id}/intent", json=supported)
    assert saved.status_code == 200, saved.text
    confirmed = request(
        app,
        "POST",
        f"/session/{model_id}/confirm_region",
        json={"region_id": "support_face"},
    )
    assert confirmed.status_code == 409, confirmed.text
    assert "exact durable ModelVersion" in confirmed.text
    exported = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "abaqus_py"},
    )
    assert exported.status_code == 409, exported.text
    assert {
        issue["code"] for issue in exported.json()["blocking_issues"]
    } >= {
        "artifact.step_meshing_required",
        "artifact.mapping_not_verified",
        "region.cad_unresolved",
        "region.proposed",
    }


def test_frontend_displays_duplicate_notices():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="interpret-notices"' in html and 'role="status"' in html
    assert "paragraph.textContent = notice" in javascript
    assert "interpretNotices.hidden = !result.notices?.length" in javascript


def test_live_unavailable_report_has_no_invented_score(tmp_path):
    root = tmp_path / "repo"
    (root / "eval").mkdir(parents=True)
    (root / "tests").mkdir()
    import shutil
    shutil.copytree(PATHS.case_dir, root / "eval" / "cases")
    shutil.copytree(PATHS.fixture_dir, root / "tests" / "fixtures")
    md_path, json_path = write_live_unavailable_report(
        root=root,
        reason="OPENAI_API_KEY is required for LIVE evaluation; no replay score was substituted",
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "LIVE" and payload["status"] == "UNAVAILABLE"
    assert payload["score"] is None and payload["threshold_achieved"] is None
    assert "No replay score was substituted" in md_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# R4B2-AUDIT-02: the checked-in canonical replay reports must never make a
# claim the canonical generator does not reproduce.
# --------------------------------------------------------------------------

REPLAY_JSON = ROOT / "eval" / "results-replay.json"
REPLAY_MD = ROOT / "eval" / "results-replay.md"

#: ``revision`` is derived from the working tree (``<HEAD>[+dirty]``), so it is
#: provenance, not a claim about behaviour.  Every other byte of the canonical
#: output is deterministic and is compared exactly.
_REVISION_JSON_KEY = "revision"
_REVISION_MD_PREFIX = "- Code revision:"


def _canonical_replay_report():
    """Regenerate the canonical replay report without touching the repo."""

    return run_evaluation(root=ROOT, mode="REPLAY")


def _blocked_export_case(document: dict) -> dict:
    matches = [
        case
        for case in document["cases"]
        if case["case_id"] == "bracket_combined_export"
    ]
    assert len(matches) == 1
    return matches[0]


def test_checked_in_replay_reports_match_canonical_generation(tmp_path):
    """The checked-in replay artifacts are exactly what --replay produces."""

    report = _canonical_replay_report()
    assert report.mode == "REPLAY"
    assert report.total == 15
    assert report.pass_count == 13
    assert report.pass_after_clarification_count == 2
    assert report.fail_count == 0

    # Render into an isolated location; the repository copies are only read.
    isolated_root = tmp_path / "isolated"
    (isolated_root / "eval").mkdir(parents=True)
    md_path, json_path = write_report(report, root=isolated_root)
    assert md_path.parent == isolated_root / "eval"

    generated_json = json.loads(json_path.read_text(encoding="utf-8"))
    checked_in_json = json.loads(REPLAY_JSON.read_text(encoding="utf-8"))
    assert generated_json.pop(_REVISION_JSON_KEY) is not None
    checked_in_json.pop(_REVISION_JSON_KEY)
    assert generated_json == checked_in_json

    generated_md = md_path.read_text(encoding="utf-8").splitlines()
    checked_in_md = REPLAY_MD.read_text(encoding="utf-8").splitlines()
    assert len(generated_md) == len(checked_in_md)
    for generated_line, checked_in_line in zip(generated_md, checked_in_md):
        if generated_line.startswith(_REVISION_MD_PREFIX):
            assert checked_in_line.startswith(_REVISION_MD_PREFIX)
            continue
        assert generated_line == checked_in_line

    # Nothing was written into the repository by this regression.
    assert not (isolated_root / "eval" / "results.json").exists()


def test_checked_in_replay_reports_designated_case_as_truthfully_blocked():
    """bracket_combined_export must claim no artifact of any kind."""

    checked_in = json.loads(REPLAY_JSON.read_text(encoding="utf-8"))
    case = _blocked_export_case(checked_in)
    export_result = case["export_result"]
    assert export_result["status"] == "blocked"
    assert export_result["code"] == "missing_region_mapping"
    assert export_result["export_eligible"] is False
    for forbidden in ("adapter", "filename", "sha256", "bytes"):
        assert forbidden not in export_result, forbidden
    assert "bracket_abaqus.py" not in REPLAY_JSON.read_text(encoding="utf-8")

    markdown = REPLAY_MD.read_text(encoding="utf-8")
    row = next(
        line
        for line in markdown.splitlines()
        if line.startswith("| bracket_combined_export |")
    )
    assert "blocked (missing_region_mapping)" in row
    assert "bracket_abaqus.py" not in markdown

    # The freshly generated report agrees with the checked-in claim.
    generated = _canonical_replay_report().model_dump(mode="json")
    assert _blocked_export_case(generated)["export_result"] == export_result


def test_checked_in_replay_reports_carry_no_ordinal_mapping_claim():
    """Replay limitations state the truthful renderer boundary."""

    for path in (REPLAY_JSON, REPLAY_MD):
        text_content = path.read_text(encoding="utf-8")
        for obsolete in (
            "OCC tag n maps to imported part.faces[n-1]",
            "source_step_face_order",
            "original_entity_ids",
        ):
            assert obsolete not in text_content, (path.name, obsolete)

    limitations = json.loads(REPLAY_JSON.read_text(encoding="utf-8"))[
        "known_limitations"
    ]
    assert any(
        "Public CAD export stays blocked without a verified CAD-to-solver "
        "mapping" in item
        for item in limitations
    )
    assert any(
        "explicit synthetic solver mapping and solver-face universe" in item
        for item in limitations
    )
    assert any(
        "no production CAD-to-solver mapping is claimed" in item
        for item in limitations
    )


# --------------------------------------------------------------------------
# Superseded historical LIVE evidence (eval/results.*)
# --------------------------------------------------------------------------
#
# ``eval/results.json`` and ``eval/results.md`` record the Task 15 LIVE run of
# 2026-07-21.  They cannot be regenerated without a genuine LIVE run and
# provider credentials, and REPLAY is never substituted for LIVE, so they are
# preserved unaltered and framed as superseded historical evidence instead.

HISTORICAL_LIVE_JSON = ROOT / "eval" / "results.json"
HISTORICAL_LIVE_MD = ROOT / "eval" / "results.md"

#: The measurements of that run, which this remediation must never falsify.
HISTORICAL_LIVE_REVISION = "7bd789c60d9b9e8b812b6fb7c0f29212587072e0+dirty"
HISTORICAL_LIVE_DATE = "2026-07-21"
HISTORICAL_EXPORT_SHA256_PREFIX = "b33921"


def test_historical_live_results_are_labelled_superseded():
    """Neither file can be mistaken for the current R4b.2 report of record."""

    document = json.loads(HISTORICAL_LIVE_JSON.read_text(encoding="utf-8"))
    status = document["historical_status"]
    assert status["superseded"] is True
    assert status["recorded_revision"] == HISTORICAL_LIVE_REVISION
    assert status["recorded_date"] == HISTORICAL_LIVE_DATE
    assert status["current_report_of_record"] == "eval/results-replay.md"
    assert "SUPERSEDED HISTORICAL LIVE EVIDENCE" in status["summary"]
    assert "not the current report of record" in status["summary"]

    markdown = HISTORICAL_LIVE_MD.read_text(encoding="utf-8")
    assert markdown.splitlines()[0].startswith("# ")
    assert "SUPERSEDED" in markdown.splitlines()[0]
    banner = markdown.split("- Evaluation mode:", 1)[0]
    assert "NOT THE CURRENT REPORT OF RECORD" in banner
    assert HISTORICAL_LIVE_REVISION in banner
    assert HISTORICAL_LIVE_DATE in banner
    assert "results-replay.md" in banner


def test_historical_live_results_frame_their_obsolete_claims_as_historical():
    """The old export success and the old ordinal statement are not current."""

    document = json.loads(HISTORICAL_LIVE_JSON.read_text(encoding="utf-8"))
    obsolete = {
        item["kind"]: item
        for item in document["historical_status"]["obsolete_claims"]
    }
    assert set(obsolete) == {
        "obsolete_ordinal_mapping_limitation",
        "obsolete_successful_export_claim",
    }
    ordinal = obsolete["obsolete_ordinal_mapping_limitation"]["current_behavior"]
    assert "provenance only" in ordinal
    assert "never as solver identifiers" in ordinal
    assert "solver-face universe" in ordinal
    assert "blocked without a verified CAD-to-mesh mapping" in ordinal
    assert "R6" in ordinal
    export = obsolete["obsolete_successful_export_claim"]["current_behavior"]
    assert "missing_region_mapping" in export
    assert "export_eligible false" in export
    assert "No current Abaqus or CalculiX artifact is claimed" in export
    assert (
        document["historical_status"]["regeneration"].startswith(
            "No LIVE run was regenerated"
        )
    )

    markdown = HISTORICAL_LIVE_MD.read_text(encoding="utf-8")
    # Compare against a line-wrap-insensitive view of the blockquote banner.
    flowed = " ".join(
        line.lstrip("> ").strip() for line in markdown.splitlines()
    )
    assert "blocked with `missing_region_mapping`" in flowed
    assert "no current Abaqus or CalculiX artifact is claimed for it" in flowed
    assert 'imported `part.faces[n-1]`" is obsolete**' in flowed
    assert "is not a current architecture limitation" in flowed
    assert "No LIVE run was regenerated to add this notice" in flowed
    assert "REPLAY was not substituted for LIVE" in flowed
    # The obsolete claims are never left unqualified: every place the ordinal
    # statement still appears is inside historical framing.
    for index, line in enumerate(markdown.splitlines()):
        if "part.faces[n-1]" not in line:
            continue
        context = "\n".join(markdown.splitlines()[max(0, index - 6):index + 1])
        assert "obsolete" in context.lower() or "historical" in context.lower()


def test_historical_live_measurements_were_not_falsified():
    """The preserved run is byte-truthful: nothing was rewritten to look current."""

    document = json.loads(HISTORICAL_LIVE_JSON.read_text(encoding="utf-8"))
    assert document["mode"] == "LIVE"
    assert document["revision"] == HISTORICAL_LIVE_REVISION
    assert (document["score"], document["pass_count"], document["fail_count"]) == (
        15,
        13,
        0,
    )
    export_result = _blocked_export_case(document)["export_result"]
    assert export_result["export_eligible"] is True
    assert export_result["adapter"] == "abaqus_py"
    assert export_result["filename"] == "bracket_abaqus.py"
    assert export_result["sha256"].startswith(HISTORICAL_EXPORT_SHA256_PREFIX)
    assert export_result["bytes"] == 4144
    # The original limitation list is preserved verbatim rather than rewritten.
    assert (
        "Abaqus face ordering assumes OCC tag n maps to imported part.faces[n-1]."
        in document["known_limitations"]
    )


def test_current_replay_evidence_contradicts_the_historical_export_claim():
    """Current R4b.2 evidence reports the case as blocked, not exported."""

    historical = _blocked_export_case(
        json.loads(HISTORICAL_LIVE_JSON.read_text(encoding="utf-8"))
    )["export_result"]
    current = _blocked_export_case(
        json.loads(REPLAY_JSON.read_text(encoding="utf-8"))
    )["export_result"]
    assert historical["export_eligible"] is True
    assert current["export_eligible"] is False
    assert current["status"] == "blocked"
    assert current["code"] == "missing_region_mapping"
    assert "filename" not in current and "sha256" not in current


#: Documentation that speaks in the repository's present tense.  The frozen V1
#: records (``PROGRESS.md``, ``sprint-goal.md``, ``EXECUTION_PLAN.md``) and the
#: superseded LIVE/initial evaluation artifacts are historical by construction
#: and are excluded here; their framing is asserted by the tests above and by
#: the authority order in ``CLAUDE.md``.
_HISTORICAL_BY_CONSTRUCTION = {
    "PROGRESS.md",
    "sprint-goal.md",
    "EXECUTION_PLAN.md",
    "eval/results.md",
    "eval/results-initial.md",
    "eval/results-live-initial.md",
    "eval/results-replay-initial.md",
}

#: Phrasings that assert the retired CAD-tag-as-solver-ID mapping.
_OBSOLETE_MAPPING_PHRASES = (
    "source_step_face_order",
    "part.faces[n-1]",
    "part.faces[n - 1]",
    "original_entity_ids",
)

#: A statement may keep an obsolete phrase only when its immediate context
#: labels it as no longer current.
_HISTORICAL_MARKERS = (
    "historical",
    "superseded",
    "obsolete",
    "no longer",
    "removed",
    "is gone",
    "replaced",
    "never",
)


def test_current_documentation_carries_no_unqualified_ordinal_mapping_claim():
    """An unqualified ordinal CAD-to-solver claim cannot re-enter the docs."""

    scanned = 0
    for path in sorted(ROOT.rglob("*.md")):
        relative = path.relative_to(ROOT).as_posix()
        parts = set(path.relative_to(ROOT).parts)
        if parts & {".venv", "node_modules", ".git", "__pycache__"}:
            continue
        if relative in _HISTORICAL_BY_CONSTRUCTION:
            continue
        scanned += 1
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not any(phrase in line for phrase in _OBSOLETE_MAPPING_PHRASES):
                continue
            context = " ".join(lines[max(0, index - 8):index + 2]).lower()
            assert any(marker in context for marker in _HISTORICAL_MARKERS), (
                relative,
                index + 1,
                line,
            )
    assert scanned > 5
