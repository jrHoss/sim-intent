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

from app.server import create_app
from eval.harness import (
    HarnessPaths,
    ReplayTransport,
    _clicks,
    _contains_forbidden_id_key,
    evaluate_case,
    load_replay,
    render_markdown,
    run_evaluation,
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
    Interpreter,
    InterpreterProviderError,
    OpenAIStructuredOutputTransport,
    UnsupportedMaterialInputError,
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


def test_designated_case_reaches_artifact_generation(replay_report):
    result = next(case for case in replay_report.cases if case.case_id == "bracket_combined_export")
    assert result.export_result["adapter"] == "abaqus_py"
    assert result.export_result["filename"].endswith(".py")


def test_blocked_intents_remain_blocked(cases):
    result = evaluate_case(by_id(cases, "bracket_combined_export"), paths=PATHS, mode="REPLAY")
    fallback = json.loads((PATHS.fallback_dir / "bracket_combined_export.json").read_text(encoding="utf-8"))
    from ir.schema import SimulationIntent
    from ir.validate import validate_intent
    intent = SimulationIntent.model_validate(fallback["proposed_ir"], strict=True)
    assert result.status == "PASS" and not validate_intent(intent).export_eligible


def test_export_eligibility_cannot_be_bypassed():
    from ir.schema import SimulationIntent
    payload = json.loads((PATHS.fallback_dir / "bracket_combined_export.json").read_text(encoding="utf-8"))
    intent = SimulationIntent.model_validate(payload["proposed_ir"], strict=True)
    inventory, _ = get_inventory(PATHS.fixture_dir / "bracket.step")
    metadata = CadModelMetadata(source_path=PATHS.fixture_dir / "bracket.step", source_name="bracket.step", source_sha256=inventory.file_sha256, face_ids=tuple(face.tag for face in inventory.faces))
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
    assert body["intent"]["regions"][0]["entity_ids"] == [11, 12]
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
    assert intent["regions"][0]["entity_ids"] == [5]
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
    assert chosen.json()["intent"]["regions"][0]["entity_ids"] == [1]
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
            "code": "unsupported_material_input",
            "message": UnsupportedMaterialInputError.safe_message,
            "mode": "LIVE",
            "supported_mechanism": "reviewed_default_density_for_gravity",
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
    assert sum(region["entity_ids"] == [11, 12] for region in body["intent"]["regions"]) == 1
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
    assert all(region["entity_ids"] == [5] for region in second.json()["intent"]["regions"])
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


def test_repeated_gravity_is_one_whole_model_load_and_exports_once(tmp_path, cases):
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
    assert intent["materials"] == [
        {
            "name": "steel",
            "model": "linear_elastic_isotropic",
            "E_MPa": 210000.0,
            "nu": 0.3,
            "density_tonne_per_mm3": 7.85e-9,
        }
    ]
    density_assumptions = [
        assumption
        for assumption in intent["assumptions"]
        if "density=7850 kg/m^3 = 7.85e-9 tonne/mm^3" in assumption["text"]
    ]
    assert len(density_assumptions) == 1
    assert density_assumptions[0]["criticality"] == "unit_critical"
    assert density_assumptions[0]["status"] == "pending"
    assert len(second.json()["notices"]) == 1

    for assumption in intent["assumptions"]:
        if assumption["criticality"] == "unit_critical":
            accepted = request(
                app,
                "POST",
                f"/session/{model_id}/assumptions/{assumption['id']}/accept",
            )
            assert accepted.status_code == 200
    exported = request(
        app,
        "POST",
        f"/session/{model_id}/export",
        json={"adapter": "abaqus_py"},
    )
    assert exported.status_code == 200
    assert exported.text.count("model.Gravity(") == 1
    assert exported.text.count("material.Density(") == 1
    assert "material.Density(table=((7.85e-09,),))" in exported.text
    assert exported.text.count("region=instance.sets['ALL_SOLID_CELLS']") == 1


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
