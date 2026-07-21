"""Headless Task 15 evaluation over the production interpretation pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from app.orchestration import interpret_and_propose, propose_from_interpretation
from app.session import SelectionSessionStore
from eval.schema import EvaluationCase, ExpectedCondition, load_cases, manifest_hash
from export.abaqus_py import export_abaqus_py
from export.common import CadModelMetadata
from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, get_inventory
from ground.engine import ClickEvidence
from ir.schema import SimulationIntent, StrictModel
from ir.validate import validate_intent
from llm.interpreter import (
    DEFAULT_MODEL,
    Interpretation,
    Interpreter,
    InterpreterError,
    ModelRequest,
    StructuredOutputTransport,
)


Mode = Literal["LIVE", "REPLAY"]
Status = Literal["PASS", "PASS_AFTER_CLARIFICATION", "FAIL"]
FailureCategory = Literal["grounding", "unit", "ambiguity-unflagged", "llm-parse"]
_GIT_EXECUTABLE = shutil.which("git")


class CaseResult(StrictModel):
    case_id: str
    status: Status
    expected_entity_ids: list[list[int]]
    actual_entity_ids: list[list[int]]
    expected_condition_types: list[str]
    actual_condition_types: list[str]
    expected_normalized_values: list[dict[str, Any]]
    actual_normalized_values: list[dict[str, Any]]
    clarification_expected: bool
    clarification_observed: bool
    clarifications_used: int = Field(ge=0)
    failure_category: FailureCategory | None = None
    explanation: str
    validation_status: str | None = None
    export_result: dict[str, Any] | None = None
    interpreter_output: dict[str, Any] | None = None
    harness_error: str | None = None


class EvaluationReport(StrictModel):
    mode: Mode
    revision: str
    model_name: str
    manifest_hash: str
    fixture_hashes: dict[str, str]
    total: int
    pass_count: int
    pass_after_clarification_count: int
    fail_count: int
    score: int
    threshold_achieved: bool
    clarifications_used: int
    cases: list[CaseResult]
    known_limitations: list[str]


class ReplayTransport(StructuredOutputTransport):
    """Sanitized typed-operation response; the real Interpreter still validates it."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls = 0

    def complete(self, request: ModelRequest) -> dict[str, Any]:
        self.calls += 1
        return json.loads(json.dumps(self.payload))


@dataclass(frozen=True)
class HarnessPaths:
    root: Path
    case_dir: Path
    fixture_dir: Path
    replay_dir: Path
    fallback_dir: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "HarnessPaths":
        base = Path(root)
        return cls(
            root=base,
            case_dir=base / "eval" / "cases",
            fixture_dir=base / "tests" / "fixtures",
            replay_dir=base / "eval" / "replay",
            fallback_dir=base / "eval" / "fallback",
        )


def _revision(root: Path) -> str:
    if _GIT_EXECUTABLE is None:
        return "unknown"
    try:
        commit = subprocess.run(
            [_GIT_EXECUTABLE, "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            [_GIT_EXECUTABLE, "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return commit + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _contains_forbidden_id_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower().replace("-", "_") in {
                "entity_id", "entity_ids", "face_id", "face_ids", "node_id", "node_ids",
                "element_id", "element_ids", "nset", "elset",
            }
            or _contains_forbidden_id_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_id_key(item) for item in value)
    return False


def load_replay(case: EvaluationCase, replay_dir: Path) -> dict[str, Any]:
    path = replay_dir / f"{case.case_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _contains_forbidden_id_key(payload):
        raise ValueError(f"replay response for {case.case_id} contains entity IDs")
    Interpretation.model_validate(payload, strict=True)
    return payload


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _subset(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            _subset(value, actual[index]) for index, value in enumerate(expected)
        )
    if isinstance(expected, float) or isinstance(actual, float):
        try:
            return abs(float(expected) - float(actual)) <= 1e-9 * max(1.0, abs(float(expected)))
        except (TypeError, ValueError):
            return False
    return expected == actual


def _condition_value(condition: ExpectedCondition) -> dict[str, Any]:
    if condition.components is not None:
        return {"components": condition.components, "unit": condition.internal_unit}
    if condition.vector is not None:
        return {"vector": condition.vector, "unit": condition.internal_unit}
    return {"magnitude": condition.magnitude, "unit": condition.internal_unit}


def _actual_conditions(intent: SimulationIntent, grounding) -> tuple[list[list[int]], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    entity_sets: list[list[int]] = []
    types: list[str] = []
    values: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    bcs = {bc.region_ref: bc for bc in intent.bcs}
    loads_by_ref = {load.region_ref: load for load in intent.loads if load.region_ref is not None}
    gravity = [load for load in intent.loads if load.type == "gravity"]
    for result in grounding.results:
        if result.region is None:
            obj = gravity.pop(0)
            entity_sets.append([])
        elif result.bc is not None:
            obj = bcs[result.region.id]
            entity_sets.append(sorted(int(value) for value in result.region.entity_ids))
        else:
            obj = loads_by_ref[result.region.id]
            entity_sets.append(sorted(int(value) for value in result.region.entity_ids))
        dumped = obj.model_dump(mode="json")
        types.append(obj.type)
        objects.append(dumped)
        if hasattr(obj, "components"):
            unit = "none" if obj.type == "fixed_displacement" else "mm"
            values.append({"components": dumped["components"], "unit": unit})
        elif hasattr(obj, "magnitude"):
            values.append({"magnitude": dumped["magnitude"], "unit": "MPa"})
        else:
            unit = "mm/s^2" if obj.type == "gravity" else ("MPa" if obj.type == "surface_traction" else "N")
            values.append({"vector": dumped["vector"], "unit": unit})
    return entity_sets, types, values, objects


def _clicks(case: EvaluationCase, inventory: FaceInventory) -> dict[int, ClickEvidence]:
    return {
        click.intent_index: ClickEvidence.for_inventory(inventory, click.entity_ids)
        for click in case.click_evidence
    }


def _classify_mismatch(
    case: EvaluationCase,
    actual_ids: list[list[int]],
    actual_types: list[str],
    actual_values: list[dict[str, Any]],
) -> tuple[FailureCategory | None, str]:
    expected_ids = [sorted(item.entity_ids) for item in case.expected_conditions]
    expected_types = [item.condition_type for item in case.expected_conditions]
    expected_values = [_condition_value(item) for item in case.expected_conditions]
    if actual_ids != expected_ids:
        return "grounding", "Exact grounded entity sets differ from frozen ground truth."
    if actual_types != expected_types:
        return "unit", "Condition type differs from the frozen force/pressure/BC semantics."
    if not _subset(expected_values, actual_values) or not _subset(actual_values, expected_values):
        return "unit", "Normalized components, vector, magnitude, or internal units differ."
    return None, "All frozen expected fields match."


def _confirm_validate_export(case: EvaluationCase, intent: SimulationIntent, inventory: FaceInventory, fixture: Path) -> tuple[str, dict[str, Any] | None]:
    store = SelectionSessionStore()
    snapshot = store.save_intent(case.case_id, intent)
    for region in snapshot.intent.regions if snapshot.intent else []:
        snapshot = store.confirm_region(case.case_id, region.id)
    for assumption in snapshot.intent.assumptions if snapshot.intent else []:
        if assumption.status == "pending" and assumption.criticality == "unit_critical":
            snapshot = store.accept_assumption(case.case_id, assumption.id)
    confirmed, report = store.intent_and_report(case.case_id)
    if not case.artifact_export_eligible:
        return report.validation_status, None
    metadata = CadModelMetadata(
        source_path=fixture,
        source_name=case.model_fixture,
        source_sha256=inventory.file_sha256,
        face_ids=tuple(sorted(face.tag for face in inventory.faces)),
    )
    result = export_abaqus_py(confirmed, metadata)
    return report.validation_status, {
        "adapter": result.adapter_name,
        "filename": result.suggested_filename,
        "sha256": result.checksum_sha256,
        "bytes": result.artifact_size,
        "validation_status": report.validation_status,
        "export_eligible": report.export_eligible,
    }


def evaluate_case(
    case: EvaluationCase,
    *,
    paths: HarnessPaths,
    mode: Mode,
    live_interpreter: Interpreter | None = None,
    write_fallback: bool = False,
) -> CaseResult:
    expected_ids = [sorted(item.entity_ids) for item in case.expected_conditions]
    expected_types = [item.condition_type for item in case.expected_conditions]
    expected_values = [_condition_value(item) for item in case.expected_conditions]
    fixture = paths.fixture_dir / case.model_fixture
    interpreter_output: dict[str, Any] | None = None
    try:
        inventory, _ = get_inventory(fixture, cache_dir=paths.root / ".sim_intent_cache" / "eval")
        cylinders = analyze_cylinders(fixture)
        if mode == "REPLAY":
            replay = load_replay(case, paths.replay_dir)
            interpreter = Interpreter(transport=ReplayTransport(replay))
        else:
            assert live_interpreter is not None
            interpreter = live_interpreter
        proposal = interpret_and_propose(
            instruction=case.instruction,
            inventory=inventory,
            cylinders=cylinders,
            interpreter=interpreter,
            click_evidence_by_intent=_clicks(case, inventory),
        )
        interpreter_output = proposal.interpretation.model_dump(mode="json")
        initial_grounding = proposal.grounding
        clarifications = proposal.clarifications
        observed = bool(clarifications)
        used = len(clarifications)

        if case.clarification_required and not observed:
            return CaseResult(
                case_id=case.case_id, status="FAIL", expected_entity_ids=expected_ids,
                actual_entity_ids=[], expected_condition_types=expected_types, actual_condition_types=[],
                expected_normalized_values=expected_values, actual_normalized_values=[],
                clarification_expected=True, clarification_observed=False, clarifications_used=0,
                failure_category="ambiguity-unflagged",
                explanation="The system selected a region where the frozen case requires clarification.",
                interpreter_output=interpreter_output,
            )
        if observed:
            if not case.clarification_required or case.clarification_response is None:
                return CaseResult(
                    case_id=case.case_id, status="FAIL", expected_entity_ids=expected_ids,
                    actual_entity_ids=[], expected_condition_types=expected_types, actual_condition_types=[],
                    expected_normalized_values=expected_values, actual_normalized_values=[],
                    clarification_expected=case.clarification_required, clarification_observed=True,
                    clarifications_used=used, failure_category="grounding",
                    explanation="Unexpected clarification prevented a final grounded intent.",
                    interpreter_output=interpreter_output,
                )
            if used != 1:
                raise RuntimeError("more than one clarification was returned")
            clicks = _clicks(case, inventory)
            action = case.clarification_response
            clicks[action.intent_index] = ClickEvidence.for_inventory(inventory, action.entity_ids)
            proposal = propose_from_interpretation(
                instruction=case.instruction,
                interpretation=proposal.interpretation,
                inventory=inventory,
                cylinders=cylinders,
                click_evidence_by_intent=clicks,
            )
            if proposal.clarifications:
                used += len(proposal.clarifications)
                raise RuntimeError("a second clarification was required")

        if proposal.intent is None:
            raise RuntimeError("grounding completed without a proposed IR")
        intent = proposal.intent
        actual_ids, actual_types, actual_values, actual_objects = _actual_conditions(intent, proposal.grounding)
        category, explanation = _classify_mismatch(case, actual_ids, actual_types, actual_values)
        if category is None:
            for expected, actual in zip(case.expected_conditions, actual_objects):
                if not _subset(expected.expected_ir_subset, actual):
                    category, explanation = "unit", "The expected condition IR subset does not match."
                    break
        if category is None and not _subset(case.expected_structured_ir_subset, intent.model_dump(mode="json")):
            category, explanation = "grounding", "The expected top-level IR subset does not match."
        for region in intent.regions:
            if region.source_instruction != case.instruction or not region.entity_ids:
                category, explanation = "grounding", "Required region provenance is incomplete."
                break

        initial_validation = validate_intent(intent)
        validation_status, export_result = _confirm_validate_export(case, intent, inventory, fixture)
        status: Status = "FAIL" if category else ("PASS_AFTER_CLARIFICATION" if observed else "PASS")

        if write_fallback:
            paths.fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback = {
                "mode": "REPLAY",
                "case_id": case.case_id,
                "model_fixture": case.model_fixture,
                "model_sha256": inventory.file_sha256,
                "typed_interpreter_output": interpreter_output,
                "initial_grounding": initial_grounding.model_dump(mode="json"),
                "final_grounding": proposal.grounding.model_dump(mode="json"),
                "proposed_ir": intent.model_dump(mode="json"),
                "clarification_used": observed,
                "validation_status_before_review": initial_validation.validation_status,
            }
            (paths.fallback_dir / f"{case.case_id}.json").write_text(
                json.dumps(fallback, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return CaseResult(
            case_id=case.case_id, status=status, expected_entity_ids=expected_ids,
            actual_entity_ids=actual_ids, expected_condition_types=expected_types,
            actual_condition_types=actual_types, expected_normalized_values=expected_values,
            actual_normalized_values=actual_values, clarification_expected=case.clarification_required,
            clarification_observed=observed, clarifications_used=used,
            failure_category=category, explanation=explanation,
            validation_status=validation_status, export_result=export_result,
            interpreter_output=interpreter_output,
        )
    except InterpreterError as exc:
        return CaseResult(
            case_id=case.case_id, status="FAIL", expected_entity_ids=expected_ids,
            actual_entity_ids=[], expected_condition_types=expected_types, actual_condition_types=[],
            expected_normalized_values=expected_values, actual_normalized_values=[],
            clarification_expected=case.clarification_required, clarification_observed=False,
            clarifications_used=0, failure_category="llm-parse",
            explanation=f"Interpreter failed after bounded retries: {exc.last_reason}.",
            interpreter_output=interpreter_output,
        )
    except Exception as exc:  # harness errors remain separate from taxonomy
        return CaseResult(
            case_id=case.case_id, status="FAIL", expected_entity_ids=expected_ids,
            actual_entity_ids=[], expected_condition_types=expected_types, actual_condition_types=[],
            expected_normalized_values=expected_values, actual_normalized_values=[],
            clarification_expected=case.clarification_required, clarification_observed=False,
            clarifications_used=0, explanation="Unexpected evaluation harness error.",
            interpreter_output=interpreter_output,
            harness_error=f"{type(exc).__name__}: {exc}",
        )


def run_evaluation(
    *,
    root: str | Path,
    mode: Mode,
    write_fallback: bool = False,
) -> EvaluationReport:
    paths = HarnessPaths.from_root(root)
    cases = load_cases(paths.case_dir, fixture_dir=paths.fixture_dir)
    if mode == "LIVE" and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for LIVE evaluation; no replay score was substituted")
    model_name = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL) if mode == "LIVE" else "checked-in typed responses"
    live = Interpreter(model=model_name) if mode == "LIVE" else None
    results = [
        evaluate_case(
            case, paths=paths, mode=mode, live_interpreter=live,
            write_fallback=write_fallback and mode == "REPLAY",
        )
        for case in cases
    ]
    pass_count = sum(result.status == "PASS" for result in results)
    clarified_count = sum(result.status == "PASS_AFTER_CLARIFICATION" for result in results)
    score = pass_count + clarified_count
    fixture_hashes = {
        name: hashlib.sha256((paths.fixture_dir / name).read_bytes()).hexdigest()
        for name in sorted({case.model_fixture for case in cases})
    }
    return EvaluationReport(
        mode=mode,
        revision=_revision(paths.root),
        model_name=model_name,
        manifest_hash=manifest_hash(cases),
        fixture_hashes=fixture_hashes,
        total=len(results),
        pass_count=pass_count,
        pass_after_clarification_count=clarified_count,
        fail_count=sum(result.status == "FAIL" for result in results),
        score=score,
        threshold_achieved=score >= 12,
        clarifications_used=sum(result.clarifications_used for result in results),
        cases=results,
        known_limitations=[
            "No solver was executed and the Abaqus artifact was not run in Abaqus.",
            "Abaqus face ordering assumes OCC tag n maps to imported part.faces[n-1].",
            "Click evidence is supported; general screenshot or drawing recognition is not.",
            "No meshing, contact, nonlinear, thermal, dynamic, or result-validation workflow is included.",
            "The optional CalculiX live check requires an installed ccx executable.",
        ],
    )


def render_markdown(report: EvaluationReport) -> str:
    lines = [
        "# Task 15 evaluation results", "", f"- Evaluation mode: **{report.mode}**",
        f"- Code revision: `{report.revision}`", f"- Configured model: `{report.model_name}`",
        f"- Case manifest SHA-256: `{report.manifest_hash}`",
        "- Fixture hashes: " + ", ".join(f"`{name}={digest}`" for name, digest in report.fixture_hashes.items()),
        f"- Score: **{report.score}/{report.total}**", f"- PASS: {report.pass_count}",
        f"- PASS_AFTER_CLARIFICATION: {report.pass_after_clarification_count}",
        f"- FAIL: {report.fail_count}", f"- 12/15 threshold achieved: **{'yes' if report.threshold_achieved else 'no'}**",
        f"- Clarifications used: {report.clarifications_used}", "",
        "| Case | Status | Expected IDs | Actual IDs | Expected type | Actual type | Expected normalized | Actual normalized | Clarification E/O | Failure | Export |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in report.cases:
        compact = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
        export = "-" if result.export_result is None else f"{result.export_result['filename']} ({result.export_result['sha256'][:12]})"
        failure = result.failure_category or ("HARNESS_ERROR" if result.harness_error else "-")
        lines.append(
            f"| {result.case_id} | {result.status} | `{compact(result.expected_entity_ids)}` | `{compact(result.actual_entity_ids)}` | "
            f"`{compact(result.expected_condition_types)}` | `{compact(result.actual_condition_types)}` | "
            f"`{compact(result.expected_normalized_values)}` | `{compact(result.actual_normalized_values)}` | "
            f"{'yes' if result.clarification_expected else 'no'}/{'yes' if result.clarification_observed else 'no'} | {failure} | {export} |"
        )
        if result.status == "FAIL":
            lines.append(f"| ↳ {result.case_id} detail |  |  |  |  |  |  |  |  | {result.explanation} | {result.harness_error or '-'} |")
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {item}" for item in report.known_limitations)
    lines.extend(["", "Replay reports measure deterministic regression only and are never presented as live LLM performance.", ""])
    return "\n".join(lines)


def write_report(report: EvaluationReport, *, root: str | Path) -> tuple[Path, Path]:
    base = Path(root) / "eval"
    suffix = "" if report.mode == "LIVE" else "-replay"
    json_path = base / f"results{suffix}.json"
    md_path = base / f"results{suffix}.md"
    initial_stem = "results-live-initial" if report.mode == "LIVE" else "results-replay-initial"
    initial_json = base / f"{initial_stem}.json"
    initial_md = base / f"{initial_stem}.md"
    if json_path.is_file() and not initial_json.exists():
        initial_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    if md_path.is_file() and not initial_md.exists():
        initial_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path, json_path


def write_live_unavailable_report(*, root: str | Path, reason: str) -> tuple[Path, Path]:
    """Record an honest live-evaluation precondition failure without a score."""

    paths = HarnessPaths.from_root(root)
    cases = load_cases(paths.case_dir, fixture_dir=paths.fixture_dir)
    fixture_hashes = {
        name: hashlib.sha256((paths.fixture_dir / name).read_bytes()).hexdigest()
        for name in sorted({case.model_fixture for case in cases})
    }
    payload = {
        "mode": "LIVE",
        "status": "UNAVAILABLE",
        "revision": _revision(paths.root),
        "model_name": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        "manifest_hash": manifest_hash(cases),
        "fixture_hashes": fixture_hashes,
        "score": None,
        "threshold_achieved": None,
        "reason": reason,
        "replay_results": "results-replay.md",
    }
    json_path = paths.root / "eval" / "results.json"
    md_path = paths.root / "eval" / "results.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Task 15 live evaluation results",
                "",
                "- Evaluation mode: **LIVE**",
                "- Status: **UNAVAILABLE**",
                f"- Code revision: `{payload['revision']}`",
                f"- Configured model: `{payload['model_name']}`",
                f"- Case manifest SHA-256: `{payload['manifest_hash']}`",
                "- Fixture hashes: " + ", ".join(
                    f"`{name}={digest}`" for name, digest in fixture_hashes.items()
                ),
                "- Score: **not run**",
                "- 12/15 threshold: **not evaluated in LIVE mode**",
                "",
                "## Reason",
                "",
                reason,
                "",
                "No replay score was substituted or labeled as live. Deterministic replay evidence is in `eval/results-replay.md`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return md_path, json_path
