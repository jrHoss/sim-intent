"""Headless Task 15 evaluation over the production interpretation pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from fastapi.testclient import TestClient
from pydantic import Field

from app.config import LocalDataConfig
from app.orchestration import interpret_and_propose, propose_from_interpretation
from app.record_versions import build_fallback_envelope
from app.runtime_mode import RuntimeMode
from app.server import create_app
from eval.schema import EvaluationCase, ExpectedCondition, load_cases, manifest_hash
from eval.versioning import verify_replay_directory
from export.abaqus_py import export_abaqus_py
from export.common import CadModelMetadata, MissingRegionMappingError
from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, get_inventory
from ground.engine import ClickEvidence
from ir.schema import (
    SimulationIntent,
    StrictModel,
    canonical_cad_numeric_membership,
    region_entity_membership,
)
from ir.validate import validate_intent
from ir.versioning import load_simulation_intent
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
FailureCategory = Literal[
    "grounding",
    "unit",
    "ambiguity-unflagged",
    "llm-parse",
    "material",
]
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
    # Task 19 decision D-1: replay bodies are the strict Interpretation LLM
    # wire contract and stay byte-unchanged.  Their version is declared once by
    # the sidecar manifest, which is verified before any body is trusted.
    verify_replay_directory(replay_dir)
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
            entity_sets.append(
                _canonical_region_membership(result.region)
            )
        else:
            obj = loads_by_ref[result.region.id]
            entity_sets.append(
                _canonical_region_membership(result.region)
            )
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


def _canonical_region_membership(region) -> list[int]:
    values = region_entity_membership(region)
    if region.entity_type == "cad_face":
        return list(canonical_cad_numeric_membership(values))
    return sorted(int(value) for value in values)


def _canonical_expected_membership(condition: ExpectedCondition) -> list[int]:
    if condition.region_entity_type == "cad_face":
        return list(canonical_cad_numeric_membership(condition.entity_ids))
    return sorted(condition.entity_ids)


def _current_expected_ir_subset(case: EvaluationCase) -> dict[str, Any]:
    """Project frozen numeric CAD expectations onto the current v3 contract."""

    expected = json.loads(json.dumps(case.expected_structured_ir_subset))
    regions = expected.get("regions")
    if not isinstance(regions, list):
        return expected
    conditions = {
        condition.intent_index: condition for condition in case.expected_conditions
    }
    for index, region in enumerate(regions):
        condition = conditions.get(index)
        if (
            not isinstance(region, dict)
            or condition is None
            or condition.region_entity_type != "cad_face"
        ):
            continue
        legacy_ids = region.pop("entity_ids", None)
        if isinstance(legacy_ids, list):
            region["cad_face_target"] = {
                "source_face_tags": list(
                    canonical_cad_numeric_membership(legacy_ids)
                )
            }
    return expected


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
    expected_ids = [
        _canonical_expected_membership(item)
        for item in case.expected_conditions
    ]
    expected_types = [item.condition_type for item in case.expected_conditions]
    expected_values = [_condition_value(item) for item in case.expected_conditions]
    if actual_ids != expected_ids:
        return "grounding", "Exact grounded entity sets differ from frozen ground truth."
    if actual_types != expected_types:
        return "unit", "Condition type differs from the frozen force/pressure/BC semantics."
    if not _subset(expected_values, actual_values) or not _subset(actual_values, expected_values):
        return "unit", "Normalized components, vector, magnitude, or internal units differ."
    return None, "All frozen expected fields match."


#: The engineering configuration the simulated engineer states during review.
#:
#: Natural-language interpretation never supplies any of it: ``app`` leaves the
#: schema-version-2 analysis, meshing and solver decisions explicitly missing so
#: a proposal cannot become ready without a deliberate engineering-setup
#: revision.  The evaluation harness plays the engineer, so it must make that
#: revision itself, in the open, exactly as :func:`_confirm_validate_export`
#: already plays the engineer for region confirmation and assumption
#: acceptance.  It lives here, in evaluation-only code that the supported
#: runtime image physically excludes, so no production path can acquire an
#: engineering decision from it.
EVALUATION_ANALYSIS_DECISIONS: dict[str, str] = {
    "dimensionality": "3d_solid",
    "solver_target": "calculix",
    "coordinate_system": "global_cartesian",
}
EVALUATION_MESH_SETTINGS: dict[str, Any] = {
    "global_element_size_mm": 1.0,
    "element_type": "tetrahedral",
    "element_order": "first_order",
    "mesher": "gmsh",
    "mesher_preset": "gmsh_tet_v1",
    "target_size_original": {"value": 1.0, "unit": "mm"},
}
EVALUATION_SOLVER_SETTINGS: dict[str, Any] = {
    "target": "calculix",
    "analysis_profile": "linear_static_v1",
    "requested_results": ["displacement", "stress", "reaction_force"],
}
EVALUATION_MATERIAL: dict[str, Any] = {
    "name": "steel",
    "model": "linear_elastic_isotropic",
    "authority": "engineer_entered",
    "E_MPa": 210_000.0,
    "nu": 0.3,
    "density_tonne_per_mm3": 7.85e-9,
}


@dataclass(frozen=True)
class DurableReviewEvidence:
    """Immutable-revision evidence captured by the evaluation workflow."""

    setup_id: str
    revision_count_before_engineering: int
    revision_count_after_engineering: int
    incomplete_revision: dict[str, Any]
    prior_revision_after_update: dict[str, Any]
    configured_revision: dict[str, Any]
    replay_revision: dict[str, Any]


def evaluation_engineering_revision_payload(
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the explicit engineer-authored full-intent request body.

    The returned mapping is not used as an in-memory evaluation shortcut.  Its
    only consumer submits it to the durable revision endpoint with optimistic
    concurrency and idempotency metadata.
    """

    payload = json.loads(json.dumps(dict(intent)))
    payload["analysis"] = {
        **payload["analysis"],
        **EVALUATION_ANALYSIS_DECISIONS,
    }
    payload["mesh_settings"] = dict(EVALUATION_MESH_SETTINGS)
    payload["solver_settings"] = dict(EVALUATION_SOLVER_SETTINGS)
    if not payload["materials"]:
        payload["materials"] = [dict(EVALUATION_MATERIAL)]
    return payload


def _require_response(response, status: int, action: str) -> Any:
    if response.status_code != status:
        raise RuntimeError(
            f"durable evaluation {action} failed with HTTP {response.status_code}"
        )
    body = response.json()
    return body


def submit_evaluation_engineering_revision(
    client: TestClient,
    *,
    case_id: str,
    setup_id: str,
    incomplete_revision: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Submit one explicit configuration revision through the product API."""

    request_body = {
        "expected_revision": incomplete_revision["revision"],
        "request_id": f"evaluation-{case_id}-engineering-v1",
        "intent": evaluation_engineering_revision_payload(
            incomplete_revision["intent"]
        ),
    }
    response = client.post(
        f"/api/v1/setups/{setup_id}/revisions",
        json=request_body,
    )
    return (
        _require_response(response, 201, "engineering revision"),
        request_body,
    )


def persist_evaluation_setup_to_incomplete(
    client: TestClient,
    *,
    case: EvaluationCase,
    intent: SimulationIntent,
    fixture: Path,
) -> tuple[str, dict[str, Any]]:
    """Persist the interpreted setup and perform normal review decisions."""

    project = _require_response(
        client.post(
            "/api/v1/projects",
            json={"name": f"Evaluation {case.case_id}"},
        ),
        201,
        "project creation",
    )
    uploaded = _require_response(
        client.post(
            f"/api/v1/projects/{project['id']}/models",
            files={
                "file": (
                    case.model_fixture,
                    fixture.read_bytes(),
                    "application/octet-stream",
                )
            },
        ),
        201,
        "model persistence",
    )
    resolved_intent = client.app.state.persistence.resolve_cad_regions_for_version(
        intent,
        uploaded["model_version"]["id"],
    )
    created = _require_response(
        client.post(
            f"/api/v1/projects/{project['id']}/setups",
            json={
                "model_id": uploaded["model_id"],
                "model_version_id": uploaded["model_version"]["id"],
                "request_id": f"evaluation-{case.case_id}-create-v1",
                "intent": resolved_intent.model_dump(mode="json"),
            },
        ),
        201,
        "setup persistence",
    )
    setup_id = created["setup"]["id"]
    current = created["current"]

    for region in list(current["intent"]["regions"]):
        if region["status"] != "proposed":
            continue
        current = _require_response(
            client.post(
                f"/api/v1/setups/{setup_id}/regions/{region['id']}/confirm",
                json={
                    "expected_revision": current["revision"],
                    "request_id": (
                        f"evaluation-{case.case_id}-confirm-{region['id']}"
                    ),
                },
            ),
            201,
            "region confirmation",
        )

    for assumption in list(current["intent"]["assumptions"]):
        if assumption["status"] != "pending":
            continue
        current = _require_response(
            client.post(
                f"/api/v1/setups/{setup_id}/assumptions/"
                f"{assumption['id']}/accept",
                json={
                    "expected_revision": current["revision"],
                    "request_id": (
                        f"evaluation-{case.case_id}-accept-{assumption['id']}"
                    ),
                },
            ),
            201,
            "assumption acceptance",
        )

    if current["validation"]["readiness_status"] != "structurally_incomplete":
        raise RuntimeError(
            "durable evaluation setup was expected to remain "
            "structurally_incomplete before engineering review"
        )
    return setup_id, current


def _durable_review(
    case: EvaluationCase,
    intent: SimulationIntent,
    fixture: Path,
) -> tuple[SimulationIntent, Any, DurableReviewEvidence]:
    """Run the same persisted review sequence as the durable product."""

    with tempfile.TemporaryDirectory(
        prefix=f"sim-intent-eval-{case.case_id}-"
    ) as temporary:
        workspace = Path(temporary)
        app = create_app(
            workspace / "legacy-models",
            mode=RuntimeMode.TEST,
            data_config=LocalDataConfig(workspace / "durable-data"),
        )
        with TestClient(app) as client:
            setup_id, incomplete = persist_evaluation_setup_to_incomplete(
                client,
                case=case,
                intent=intent,
                fixture=fixture,
            )
            history_before = _require_response(
                client.get(f"/api/v1/setups/{setup_id}/revisions"),
                200,
                "revision history read",
            )

            configured, request_body = submit_evaluation_engineering_revision(
                client,
                case_id=case.case_id,
                setup_id=setup_id,
                incomplete_revision=incomplete,
            )
            history_after = _require_response(
                client.get(f"/api/v1/setups/{setup_id}/revisions"),
                200,
                "updated revision history read",
            )
            if len(history_after) != len(history_before) + 1:
                raise RuntimeError(
                    "engineering update did not create exactly one revision"
                )

            configured_read = _require_response(
                client.get(
                    f"/api/v1/setups/{setup_id}/revisions/"
                    f"{configured['revision']}"
                ),
                200,
                "engineering revision read",
            )
            prior = _require_response(
                client.get(
                    f"/api/v1/setups/{setup_id}/revisions/"
                    f"{incomplete['revision']}"
                ),
                200,
                "prior revision read",
            )
            replay = _require_response(
                client.post(
                    f"/api/v1/setups/{setup_id}/revisions",
                    json=request_body,
                ),
                201,
                "engineering request replay",
            )
            if replay["id"] != configured["id"]:
                raise RuntimeError(
                    "exact engineering request replay created another revision"
                )
            history_after_replay = _require_response(
                client.get(f"/api/v1/setups/{setup_id}/revisions"),
                200,
                "post-replay revision history read",
            )
            if len(history_after_replay) != len(history_after):
                raise RuntimeError(
                    "exact engineering request replay changed revision history"
                )

            durable_intent = load_simulation_intent(
                configured_read["intent"],
                source=(
                    f"evaluation/{case.case_id}/revision/"
                    f"{configured_read['revision']}"
                ),
            )
            report = validate_intent(durable_intent)
            evidence = DurableReviewEvidence(
                setup_id=setup_id,
                revision_count_before_engineering=len(history_before),
                revision_count_after_engineering=len(history_after),
                incomplete_revision=incomplete,
                prior_revision_after_update=prior,
                configured_revision=configured_read,
                replay_revision=replay,
            )
            return durable_intent, report, evidence


def _confirm_validate_export(
    case: EvaluationCase,
    intent: SimulationIntent,
    inventory: FaceInventory,
    fixture: Path,
) -> tuple[str, dict[str, Any] | None, DurableReviewEvidence]:
    confirmed, report, evidence = _durable_review(case, intent, fixture)
    if not case.artifact_export_eligible:
        return report.validation_status, None, evidence
    metadata = CadModelMetadata(
        source_path=fixture,
        source_name=case.model_fixture,
        source_sha256=inventory.file_sha256,
        source_cad_face_tags=tuple(sorted(face.tag for face in inventory.faces)),
    )
    try:
        result = export_abaqus_py(confirmed, metadata)
    except MissingRegionMappingError:
        return report.validation_status, {
            "status": "blocked",
            "code": "missing_region_mapping",
            "validation_status": report.validation_status,
            "export_eligible": False,
        }, evidence
    return report.validation_status, {
        "status": "generated",
        "adapter": result.adapter_name,
        "filename": result.suggested_filename,
        "sha256": result.checksum_sha256,
        "bytes": result.artifact_size,
        "validation_status": report.validation_status,
        "export_eligible": report.export_eligible,
    }, evidence


def evaluate_case(
    case: EvaluationCase,
    *,
    paths: HarnessPaths,
    mode: Mode,
    live_interpreter: Interpreter | None = None,
    write_fallback: bool = False,
) -> CaseResult:
    expected_ids = [
        _canonical_expected_membership(item)
        for item in case.expected_conditions
    ]
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
        # Production interpretation must never inject a material.  The later
        # durable engineer-authored revision may add its visible fixture
        # material, but the proposal itself is required to stay empty.
        if category is None and intent.materials != []:
            category = "material"
            explanation = (
                "Production interpretation injected a material into the proposal."
            )

        # The immutable corpus predates this explicit no-injection assertion
        # and contains the old demo steel in some historical subsets.  Keep
        # those bytes untouched; the assertion above replaces that expectation.
        expected_top_level = _current_expected_ir_subset(case)
        expected_top_level.pop("materials", None)
        if category is None and not _subset(
            expected_top_level, intent.model_dump(mode="json")
        ):
            category, explanation = "grounding", "The expected top-level IR subset does not match."
        for region in intent.regions:
            if (
                region.source_instruction != case.instruction
                or not region_entity_membership(region)
            ):
                category, explanation = "grounding", "Required region provenance is incomplete."
                break

        initial_validation = validate_intent(intent)
        validation_status, export_result, _ = _confirm_validate_export(
            case, intent, inventory, fixture
        )
        status: Status = "FAIL" if category else ("PASS_AFTER_CLARIFICATION" if observed else "PASS")

        if write_fallback:
            paths.fallback_dir.mkdir(parents=True, exist_ok=True)
            # Task 19: writes emit only current versions -- the envelope
            # version from the fallback registry and the nested intent version
            # from the SimulationIntent registry.
            fallback = build_fallback_envelope(
                {
                    "mode": "REPLAY",
                    "case_id": case.case_id,
                    "model_fixture": case.model_fixture,
                    "model_sha256": inventory.file_sha256,
                    "typed_interpreter_output": interpreter_output,
                    "initial_grounding": initial_grounding.model_dump(mode="json"),
                    "final_grounding": proposal.grounding.model_dump(mode="json"),
                    "clarification_used": observed,
                    "validation_status_before_review": initial_validation.validation_status,
                },
                intent,
            )
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
            "Public CAD export stays blocked without a verified CAD-to-solver mapping; source CAD face tags are retained as provenance only and are never used as solver face IDs.",
            "The private Abaqus renderer is exercised only by focused tests that supply an explicit synthetic solver mapping and solver-face universe; no production CAD-to-solver mapping is claimed.",
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
        if result.export_result is None:
            export = "-"
        elif result.export_result.get("status") == "blocked":
            export = f"blocked ({result.export_result['code']})"
        else:
            export = (
                f"{result.export_result['filename']} "
                f"({result.export_result['sha256'][:12]})"
            )
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
