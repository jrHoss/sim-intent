"""Task 19 focused tests: versioned corpora, stamping evidence, legacy route.

Covers the checked-in payload families, the replay sidecar manifest (decision
D-1), the frozen evaluation manifest split (decision D-3), the stamping
migration evidence, and the single route-scoped legacy compatibility exception
(decision D-2).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _pytest.outcomes import Failed, Skipped

from app.record_versions import (
    FALLBACK_RECORD_MIGRATIONS,
    NESTED_INTENT_KEY,
    build_fallback_envelope,
    load_fallback_envelope,
    load_fallback_record,
)
from app.schema_compat import (
    LEGACY_UNVERSIONED_INTENT_VERSION,
    normalize_legacy_intent_payload,
)
from eval.schema import (
    load_cases,
    load_evaluation_case,
    manifest_hash,
    versioned_manifest_hash,
)
from eval.versioning import (
    EVALUATION_CASE_MIGRATIONS,
    REPLAY_MANIFEST_FILENAME,
    REPLAY_RECORD_MIGRATIONS,
    build_replay_manifest,
    content_sha256,
    load_replay_manifest,
    render_replay_manifest,
    verify_replay_directory,
)
from ir.schema_version import SCHEMA_VERSION_FIELD, SIMULATION_INTENT_SCHEMA_VERSION
from ir.versioning import (
    SIMULATION_INTENT_MIGRATIONS,
    MissingSchemaVersionError,
    PayloadStructureError,
    SchemaVersionError,
    dump_simulation_intent,
    load_simulation_intent,
)
from scripts import stamp_schema_versions as stamp_module
from scripts.stamp_schema_versions import (
    StampError,
    stamp_by_insertion,
    stamp_fallback_record,
    stamp_targets,
    strip_declared_versions,
    validate_evaluation_case_document,
    validate_simulation_intent_document,
)
from scripts.stamp_schema_versions import main as stamp_main
from tests.baseline_evidence import (
    BASELINE_COMMIT,
    REQUIRE_BASELINE_ENV,
    baseline_commit_available,
    baseline_evidence_required,
    require_baseline_object,
    resolve_baseline_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "eval" / "cases"
FALLBACK_DIR = ROOT / "eval" / "fallback"
REPLAY_DIR = ROOT / "eval" / "replay"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

FROZEN_MANIFEST_HASH = (
    "47c0d7275b9a065a7f5e3316ed60b7ffff58913e0b1e5045c857f663e1f6775b"
)

STAMPED_INTENT_DOCUMENTS = [
    "examples/bracket_confirmed_export_ready.json",
    "examples/bracket_sprint_goal.json",
    "examples/plate_hole_pressure.json",
    "docs/task13-bracket-demo.json",
]
STAMPED_CASE_DOCUMENTS = [
    f"eval/cases/{path.name}" for path in sorted(CASE_DIR.glob("*.json"))
]
STAMPED_FALLBACK_DOCUMENTS = [
    f"eval/fallback/{path.name}" for path in sorted(FALLBACK_DIR.glob("*.json"))
]


@pytest.fixture
def intent_payload() -> dict[str, Any]:
    return json.loads(
        (ROOT / "examples" / "bracket_sprint_goal.json").read_text(encoding="utf-8")
    )


BASELINE_EVIDENCE_CASES = (
    STAMPED_INTENT_DOCUMENTS + STAMPED_CASE_DOCUMENTS + STAMPED_FALLBACK_DOCUMENTS
)


# --------------------------------------------------------------------------
# Every stamped checked-in payload declares the current version
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relative", STAMPED_INTENT_DOCUMENTS)
def test_checked_in_intent_documents_load_through_the_authoritative_loader(relative):
    intent = load_simulation_intent(
        (ROOT / relative).read_text(encoding="utf-8"), source=relative
    )
    assert intent.schema_version == SIMULATION_INTENT_SCHEMA_VERSION


def test_all_evaluation_cases_declare_current_version():
    paths = sorted(CASE_DIR.glob("*.json"))
    assert len(paths) == 15
    for path in paths:
        case = load_evaluation_case(
            path.read_text(encoding="utf-8"), source=f"eval/cases/{path.name}"
        )
        assert case.schema_version == EVALUATION_CASE_MIGRATIONS.current_version


def test_all_fallback_records_declare_a_supported_version_and_load_current():
    """The frozen records stay at the version they were written at.

    They are genuine schema-version-1 setups.  Restamping them to the current
    version would hand each one the version-2 engineering decisions its author
    never made; instead the loader migrates them on read and every *loaded*
    intent carries the current version.
    """

    paths = sorted(FALLBACK_DIR.glob("*.json"))
    assert len(paths) == 15
    for path in paths:
        envelope, intent = load_fallback_record(
            path.read_text(encoding="utf-8"), source=f"eval/fallback/{path.name}"
        )
        assert (
            envelope[SCHEMA_VERSION_FIELD]
            == FALLBACK_RECORD_MIGRATIONS.current_version
        )
        assert intent.schema_version == SIMULATION_INTENT_SCHEMA_VERSION
        raw = json.loads(path.read_text(encoding="utf-8"))
        declared = raw[NESTED_INTENT_KEY][SCHEMA_VERSION_FIELD]
        assert (
            SIMULATION_INTENT_MIGRATIONS.minimum_supported_version
            <= declared
            <= SIMULATION_INTENT_SCHEMA_VERSION
        )


def test_evaluation_case_partial_ir_subsets_are_never_versioned():
    """The case record is versioned; its partial IR fragments are not."""

    for path in sorted(CASE_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert SCHEMA_VERSION_FIELD not in raw["expected_structured_ir_subset"]
        for condition in raw["expected_conditions"]:
            assert SCHEMA_VERSION_FIELD not in condition["expected_ir_subset"]


# --------------------------------------------------------------------------
# Fallback envelope delegation
# --------------------------------------------------------------------------


def test_fallback_loader_requires_both_envelope_and_nested_versions():
    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    without_envelope = dict(raw)
    without_envelope.pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(MissingSchemaVersionError):
        load_fallback_record(without_envelope, source="t")

    without_nested = copy.deepcopy(raw)
    without_nested[NESTED_INTENT_KEY].pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(MissingSchemaVersionError):
        load_fallback_record(without_nested, source="t")


def test_fallback_envelope_requires_its_declared_members():
    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    raw.pop("model_sha256")
    with pytest.raises(PayloadStructureError) as exc:
        load_fallback_envelope(raw, source="t")
    assert exc.value.details["missing"] == ["model_sha256"]


def test_fallback_loader_migrates_nested_intent_and_grounding_without_mutating_input():
    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    original_raw = copy.deepcopy(raw)
    envelope, intent = load_fallback_record(raw, source="t")
    dumped = dump_simulation_intent(intent)
    assert raw == original_raw
    assert envelope[NESTED_INTENT_KEY] == dumped
    # The nested body is migrated 1 -> 2 -> 3 on read: version-2 engineering
    # decisions stay missing and numeric CAD evidence becomes explicit legacy.
    assert dumped[SCHEMA_VERSION_FIELD] == SIMULATION_INTENT_SCHEMA_VERSION
    assert dumped["analysis"]["dimensionality"] is None
    assert dumped["analysis"]["solver_target"] is None
    assert dumped["analysis"]["coordinate_system"] is None
    assert dumped["mesh_settings"] is None
    assert dumped["solver_settings"] is None
    # ... and pre-existing conditions remain untouched.
    assert dumped["loads"] == raw[NESTED_INTENT_KEY]["loads"]
    assert dumped["bcs"] == raw[NESTED_INTENT_KEY]["bcs"]
    for migrated, original in zip(
        dumped["regions"], raw[NESTED_INTENT_KEY]["regions"], strict=True
    ):
        assert {
            key: value
            for key, value in migrated.items()
            if key != "cad_face_target"
        } == {
            key: value for key, value in original.items()
            if key != "entity_ids"
        }
        assert migrated["cad_face_target"]["resolution"] == "legacy_local_only"
        assert migrated["cad_face_target"]["source_face_tags"] == original["entity_ids"]
    for key in ("initial_grounding", "final_grounding"):
        migrated_region = envelope[key]["results"][0]["region"]
        original_region = raw[key]["results"][0]["region"]
        assert "entity_ids" not in migrated_region
        assert migrated_region["cad_face_target"]["resolution"] == (
            "legacy_local_only"
        )
        assert migrated_region["cad_face_target"]["source_face_tags"] == (
            original_region["entity_ids"]
        )


def test_build_fallback_envelope_emits_only_current_versions():
    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    _, intent = load_fallback_record(raw, source="t")
    body = {
        key: value
        for key, value in raw.items()
        if key not in {NESTED_INTENT_KEY, SCHEMA_VERSION_FIELD}
    }
    body[SCHEMA_VERSION_FIELD] = 99
    rebuilt = build_fallback_envelope(body, intent)
    assert rebuilt[SCHEMA_VERSION_FIELD] == FALLBACK_RECORD_MIGRATIONS.current_version
    assert (
        rebuilt[NESTED_INTENT_KEY][SCHEMA_VERSION_FIELD]
        == SIMULATION_INTENT_SCHEMA_VERSION
    )


# --------------------------------------------------------------------------
# Replay sidecar manifest (decision D-1)
# --------------------------------------------------------------------------


def test_replay_bodies_are_not_stamped():
    for path in sorted(REPLAY_DIR.glob("*.json")):
        if path.name == REPLAY_MANIFEST_FILENAME:
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        assert SCHEMA_VERSION_FIELD not in body


def test_replay_manifest_covers_every_replay_file_with_matching_hash():
    manifest = verify_replay_directory(REPLAY_DIR)
    assert manifest[SCHEMA_VERSION_FIELD] == REPLAY_RECORD_MIGRATIONS.current_version
    assert len(manifest["records"]) == 15


def test_replay_manifest_is_deterministic_and_matches_disk():
    built = build_replay_manifest(REPLAY_DIR)
    assert render_replay_manifest(built) == render_replay_manifest(
        build_replay_manifest(REPLAY_DIR)
    )
    on_disk = (REPLAY_DIR / REPLAY_MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert on_disk.replace("\r\n", "\n") == render_replay_manifest(built)


def test_replay_manifest_detects_drift(tmp_path):
    staging = tmp_path / "drift"
    shutil.copytree(REPLAY_DIR, staging)
    victim = staging / "bracket_bottom_fixed.json"
    victim.write_text(victim.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(PayloadStructureError) as exc:
        verify_replay_directory(staging)
    assert exc.value.details["drifted"] == ["bracket_bottom_fixed.json"]


def test_replay_manifest_detects_unlisted_record(tmp_path):
    staging = tmp_path / "unlisted"
    shutil.copytree(REPLAY_DIR, staging)
    (staging / "surprise.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PayloadStructureError) as exc:
        verify_replay_directory(staging)
    assert exc.value.details["unlisted"] == ["surprise.json"]


def test_replay_manifest_detects_absent_record(tmp_path):
    staging = tmp_path / "absent"
    shutil.copytree(REPLAY_DIR, staging)
    (staging / "bracket_bottom_fixed.json").unlink()
    with pytest.raises(PayloadStructureError) as exc:
        verify_replay_directory(staging)
    assert exc.value.details["absent"] == ["bracket_bottom_fixed.json"]


def test_replay_manifest_absence_is_a_typed_failure(tmp_path):
    staging = tmp_path / "nomanifest"
    shutil.copytree(REPLAY_DIR, staging)
    (staging / REPLAY_MANIFEST_FILENAME).unlink()
    with pytest.raises(PayloadStructureError):
        verify_replay_directory(staging)


def test_replay_manifest_loader_rejects_malformed_records():
    with pytest.raises(PayloadStructureError):
        load_replay_manifest({SCHEMA_VERSION_FIELD: 1, "records": {}}, source="t")
    with pytest.raises(PayloadStructureError):
        load_replay_manifest(
            {SCHEMA_VERSION_FIELD: 1, "records": {"a.json": "short"}}, source="t"
        )
    with pytest.raises(PayloadStructureError):
        load_replay_manifest(
            {SCHEMA_VERSION_FIELD: 1, "records": {"a.txt": "0" * 64}}, source="t"
        )
    with pytest.raises(MissingSchemaVersionError):
        load_replay_manifest({"records": {"a.json": "0" * 64}}, source="t")


def test_replay_manifest_hash_is_line_ending_independent(tmp_path):
    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    lf.write_bytes(b'{"intents": []}\n')
    crlf.write_bytes(b'{"intents": []}\r\n')
    assert content_sha256(lf) == content_sha256(crlf)


# --------------------------------------------------------------------------
# Stamping migration evidence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("relative", BASELINE_EVIDENCE_CASES)
def test_stamping_preserved_version_stripped_semantics_from_baseline(relative):
    """The migration evidence required before rewriting a checked-in payload.

    Compares against the real committed blobs at ``6f92b53``. Under
    ``SIM_INTENT_REQUIRE_BASELINE_EVIDENCE=1`` -- set by the required hosted CI
    jobs -- an unavailable baseline object fails instead of skipping. Without
    the flag it may skip, so the container images, which intentionally exclude
    ``.git``, still run every other test.
    """

    baseline_text = require_baseline_object(relative)
    baseline = json.loads(baseline_text)
    current = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    nested = (NESTED_INTENT_KEY,) if relative.startswith("eval/fallback/") else ()
    assert strip_declared_versions(current, nested) == strip_declared_versions(
        baseline, nested
    )
    assert SCHEMA_VERSION_FIELD not in baseline
    assert SCHEMA_VERSION_FIELD in current


def test_baseline_replay_bodies_are_byte_identical():
    for path in sorted(REPLAY_DIR.glob("*.json")):
        if path.name == REPLAY_MANIFEST_FILENAME:
            continue
        baseline_text = require_baseline_object(f"eval/replay/{path.name}")
        assert baseline_text.replace("\r\n", "\n") == path.read_text(
            encoding="utf-8"
        ).replace("\r\n", "\n")


# --------------------------------------------------------------------------
# Baseline-evidence policy (review correction B-1)
# --------------------------------------------------------------------------


def test_baseline_evidence_case_count_is_exactly_the_reviewed_set():
    assert len(BASELINE_EVIDENCE_CASES) == 34
    # 34 parametrised payload comparisons plus the single replay-body check.
    assert len(BASELINE_EVIDENCE_CASES) + 1 == 35


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
    ],
)
def test_required_flag_parsing(raw, expected):
    env = {} if raw is None else {REQUIRE_BASELINE_ENV: raw}
    assert baseline_evidence_required(env) is expected


def test_unavailable_baseline_with_required_flag_is_a_hard_failure(tmp_path):
    """A shallow checkout must fail the evidence gate, never skip it."""

    decision = resolve_baseline_evidence(
        "examples/bracket_sprint_goal.json",
        env={REQUIRE_BASELINE_ENV: "1"},
        root=tmp_path,
    )
    assert decision.action == "fail"
    assert BASELINE_COMMIT in decision.reason
    assert "fetch-depth: 0" in decision.reason

    with pytest.raises(Failed) as exc:
        require_baseline_object(
            "examples/bracket_sprint_goal.json",
            env={REQUIRE_BASELINE_ENV: "1"},
            root=tmp_path,
        )
    assert REQUIRE_BASELINE_ENV in str(exc.value)


def test_unavailable_baseline_without_the_flag_skips(tmp_path):
    """Container images intentionally exclude .git and may still skip."""

    decision = resolve_baseline_evidence(
        "examples/bracket_sprint_goal.json", env={}, root=tmp_path
    )
    assert decision.action == "skip"
    assert REQUIRE_BASELINE_ENV in decision.reason

    with pytest.raises(Skipped):
        require_baseline_object(
            "examples/bracket_sprint_goal.json", env={}, root=tmp_path
        )


def test_available_baseline_with_required_flag_executes_every_comparison():
    """With full history, all 35 comparisons must resolve to 'execute'."""

    if not baseline_commit_available():
        pytest.skip("this checkout has no baseline history to assert against")

    required = {REQUIRE_BASELINE_ENV: "1"}
    replay_bodies = [
        f"eval/replay/{path.name}"
        for path in sorted(REPLAY_DIR.glob("*.json"))
        if path.name != REPLAY_MANIFEST_FILENAME
    ]
    for relative in [*BASELINE_EVIDENCE_CASES, *replay_bodies]:
        decision = resolve_baseline_evidence(relative, env=required)
        assert decision.action == "execute", relative
        assert decision.text is not None


def test_baseline_commit_availability_probe_matches_git(tmp_path):
    assert baseline_commit_available(tmp_path) is False
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("no Git metadata in this environment")
    expected = (
        subprocess.run(
            [git, "cat-file", "-e", f"{BASELINE_COMMIT}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )
    assert baseline_commit_available() is expected


def test_stamper_check_is_clean_and_idempotent():
    assert stamp_main(["--check"]) == 0
    first = dict(stamp_targets(ROOT))
    second = dict(stamp_targets(ROOT))
    assert first == second
    for path, text in first.items():
        assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == text


# --------------------------------------------------------------------------
# A declared version is a claim, not proof (R3.1 finding 2)
# --------------------------------------------------------------------------


EXPLICIT_ANALYSIS_DECISIONS = {
    "dimensionality": "3d_solid",
    "solver_target": "calculix",
    "coordinate_system": "global_cartesian",
}
EXPLICIT_MESH_SETTINGS = {
    "global_element_size_mm": 1.0,
    "element_type": "tetrahedral",
    "element_order": "first_order",
    "mesher": "gmsh",
    "mesher_preset": "gmsh_tet_v1",
    "target_size_original": {"value": 1.0, "unit": "mm"},
}
EXPLICIT_SOLVER_SETTINGS = {
    "target": "calculix",
    "analysis_profile": "linear_static_v1",
    "requested_results": ["displacement", "stress", "reaction_force"],
}

LEGACY_INTENT_SOURCE = ROOT / "examples" / "bracket_confirmed_export_ready.json"


def legacy_intent_document() -> dict[str, Any]:
    """A genuine, valid schema-version-1 ``SimulationIntent`` document."""

    body = json.loads(LEGACY_INTENT_SOURCE.read_text(encoding="utf-8"))
    assert body[SCHEMA_VERSION_FIELD] == 1
    return body


def current_intent_document() -> dict[str, Any]:
    """The same setup, explicitly complete at the current schema version."""

    body = legacy_intent_document()
    body[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION
    body["analysis"] = {**body["analysis"], **EXPLICIT_ANALYSIS_DECISIONS}
    body["mesh_settings"] = dict(EXPLICIT_MESH_SETTINGS)
    body["solver_settings"] = dict(EXPLICIT_SOLVER_SETTINGS)
    for index, region in enumerate(body["regions"], start=1):
        if region["entity_type"] == "cad_face":
            region["cad_face_target"] = {
                "resolution": "resolved",
                "model_version_id": "fixture-model-version",
                "artifact_sha256": "a" * 64,
                "stable_identities": [f"gfi1:{index:064x}"],
                "source_face_tags": list(region["entity_ids"]),
            }
            region.pop("entity_ids")
    return body


def write_document(path: Path, body: dict[str, Any]) -> str:
    text = json.dumps(body, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


#: One corruption per failure mode the stamper must catch behind a supported
#: declared version.
MALFORMED_INTENT_MUTATIONS = {
    "malformed_nested_object": lambda body: body.update(
        {"analysis": {**body["analysis"], "units": "mm-N-MPa"}}
    ),
    "invalid_discriminator": lambda body: body["loads"][0].update({"type": "torque"}),
    "unsupported_unit": lambda body: body["analysis"]["units"].update({"force": "lbf"}),
    "missing_required_structure": lambda body: body.pop("regions"),
    "dangling_reference": lambda body: body["bcs"][0].update({"region_ref": "nowhere"}),
}


@pytest.mark.parametrize("mutation", sorted(MALFORMED_INTENT_MUTATIONS))
@pytest.mark.parametrize("declared", [1, SIMULATION_INTENT_SCHEMA_VERSION])
def test_stamper_rejects_a_malformed_document_behind_a_supported_version(
    tmp_path, mutation, declared
):
    """Version 1 and version 2 alike: the declaration proves nothing."""

    body = (
        legacy_intent_document() if declared == 1 else current_intent_document()
    )
    MALFORMED_INTENT_MUTATIONS[mutation](body)
    body[SCHEMA_VERSION_FIELD] = declared
    target = tmp_path / "malformed.json"
    write_document(target, body)

    with pytest.raises(StampError) as caught:
        stamp_by_insertion(
            target,
            SIMULATION_INTENT_MIGRATIONS,
            validate_simulation_intent_document,
        )
    assert_safe_diagnostic(caught.value, target)


def test_stamper_rejects_a_malformed_nested_fallback_intent(tmp_path):
    """The envelope may be perfect while the nested setup is not."""

    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    raw[NESTED_INTENT_KEY]["loads"] = [{"type": "torque", "region_ref": None}]
    target = tmp_path / "bracket_bottom_fixed.json"
    write_document(target, raw)

    with pytest.raises(StampError) as caught:
        stamp_fallback_record(target)
    assert_safe_diagnostic(caught.value, target)


def test_stamper_rejects_a_future_nested_intent_before_reading_its_body(tmp_path):
    """A current evaluation fallback envelope cannot mask a future intent."""

    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    raw[NESTED_INTENT_KEY] = current_intent_document()
    raw[NESTED_INTENT_KEY][SCHEMA_VERSION_FIELD] += 1
    target = tmp_path / "future_nested.json"
    original = write_document(target, raw)
    with pytest.raises(StampError) as caught:
        stamp_fallback_record(target)
    assert (
        "simulation_intent.schema_version_unsupported_future"
        in str(caught.value)
    )
    assert target.read_text(encoding="utf-8") == original
    assert_safe_diagnostic(caught.value, target)


def test_stamper_rejects_a_future_top_level_fallback_envelope(tmp_path):
    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    raw[SCHEMA_VERSION_FIELD] = FALLBACK_RECORD_MIGRATIONS.current_version + 1
    target = tmp_path / "future_fallback.json"
    original = write_document(target, raw)

    with pytest.raises(StampError) as caught:
        stamp_fallback_record(target)

    assert "fallback_record.schema_version_unsupported_future" in str(caught.value)
    assert target.read_text(encoding="utf-8") == original
    assert_safe_diagnostic(caught.value, target)


def test_stamper_rejects_a_future_nested_intent_in_a_legacy_envelope(tmp_path):
    """Even an unstamped envelope validates nested bounds before stamping."""

    raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    raw.pop(SCHEMA_VERSION_FIELD)
    raw[NESTED_INTENT_KEY] = current_intent_document()
    raw[NESTED_INTENT_KEY][SCHEMA_VERSION_FIELD] += 1
    target = tmp_path / "legacy_envelope_future_nested.json"
    original = write_document(target, raw)

    with pytest.raises(StampError) as caught:
        stamp_fallback_record(target)

    assert (
        "simulation_intent.schema_version_unsupported_future"
        in str(caught.value)
    )
    assert target.read_text(encoding="utf-8") == original
    assert_safe_diagnostic(caught.value, target)


def test_stamper_rejects_a_future_evaluation_case_envelope(tmp_path):
    source = CASE_DIR / "01_bracket_bottom_fixed.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw[SCHEMA_VERSION_FIELD] = EVALUATION_CASE_MIGRATIONS.current_version + 1
    target = tmp_path / "future_evaluation_case.json"
    original = write_document(target, raw)

    with pytest.raises(StampError) as caught:
        stamp_by_insertion(
            target,
            EVALUATION_CASE_MIGRATIONS,
            validate_evaluation_case_document,
        )

    assert "evaluation_case.schema_version_unsupported_future" in str(caught.value)
    assert target.read_text(encoding="utf-8") == original
    assert_safe_diagnostic(caught.value, target)


def assert_safe_diagnostic(error: StampError, target: Path) -> None:
    """The message names the file safely and leaks nothing internal."""

    message = str(error)
    assert target.name in message
    assert str(target.parent) not in message
    assert "Traceback" not in message
    assert "ValidationError" not in message
    assert "pydantic" not in message
    assert "\n" not in message


@pytest.mark.parametrize(
    ("build", "declared"),
    [
        (legacy_intent_document, 1),
        (current_intent_document, SIMULATION_INTENT_SCHEMA_VERSION),
    ],
)
def test_a_valid_document_at_a_supported_version_stays_byte_identical(
    tmp_path, build, declared
):
    """Validation must never become a silent rewrite to the current version."""

    body = build()
    assert body[SCHEMA_VERSION_FIELD] == declared
    target = tmp_path / f"valid_v{declared}.json"
    original = write_document(target, body)

    stamped = stamp_by_insertion(
        target, SIMULATION_INTENT_MIGRATIONS, validate_simulation_intent_document
    )
    assert stamped == original
    assert json.loads(stamped)[SCHEMA_VERSION_FIELD] == declared


def test_stamper_refuses_an_unsupported_declared_version(tmp_path):
    body = current_intent_document()
    body[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION + 1
    target = tmp_path / "future.json"
    write_document(target, body)
    with pytest.raises(StampError) as caught:
        stamp_by_insertion(
            target, SIMULATION_INTENT_MIGRATIONS, validate_simulation_intent_document
        )
    assert (
        "simulation_intent.schema_version_unsupported_future"
        in str(caught.value)
    )
    assert_safe_diagnostic(caught.value, target)


def test_stamper_inserts_and_validates_when_no_version_is_declared(tmp_path):
    body = current_intent_document()
    body.pop(SCHEMA_VERSION_FIELD)
    target = tmp_path / "unversioned.json"
    write_document(target, body)
    stamped = stamp_by_insertion(
        target, SIMULATION_INTENT_MIGRATIONS, validate_simulation_intent_document
    )
    assert json.loads(stamped)[SCHEMA_VERSION_FIELD] == (
        SIMULATION_INTENT_MIGRATIONS.current_version
    )

    # An unversioned document that is *invalid* is still refused.
    body["loads"][0]["type"] = "torque"
    invalid = tmp_path / "unversioned_invalid.json"
    write_document(invalid, body)
    with pytest.raises(StampError):
        stamp_by_insertion(
            invalid, SIMULATION_INTENT_MIGRATIONS, validate_simulation_intent_document
        )


def test_all_thirty_five_checked_in_payloads_validate(capsys):
    """Every approved target passes typed validation, not just version parsing."""

    targets = stamp_targets(ROOT)
    assert len(targets) == 35
    assert stamp_main(["--check"]) == 0
    assert "all 35 versioned payloads are stamped and current" in capsys.readouterr().out


def staged_repository(destination: Path) -> Path:
    for relative in ("examples", "eval/cases", "eval/fallback", "eval/replay"):
        shutil.copytree(ROOT / relative, destination / relative)
    (destination / "docs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "docs" / "task13-bracket-demo.json",
        destination / "docs" / "task13-bracket-demo.json",
    )
    return destination


@pytest.mark.parametrize("declared", [1, SIMULATION_INTENT_SCHEMA_VERSION])
def test_check_fails_for_a_malformed_checked_in_document(
    tmp_path, monkeypatch, capsys, declared
):
    """``--check`` is the gate; a supported declaration cannot slip past it."""

    staged = staged_repository(tmp_path / "repo")
    victim = staged / "examples" / "bracket_confirmed_export_ready.json"
    body = json.loads(victim.read_text(encoding="utf-8"))
    body[SCHEMA_VERSION_FIELD] = declared
    body["loads"][0]["type"] = "torque"
    write_document(victim, body)

    monkeypatch.setattr(stamp_module, "ROOT", staged)
    exit_code = stamp_main(["--check"])
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "stamping refused" in captured.err
    assert "examples/bracket_confirmed_export_ready.json" in captured.err
    assert str(staged) not in captured.err


def test_check_refuses_future_versions_without_rewriting(tmp_path, monkeypatch, capsys):
    staged = staged_repository(tmp_path / "repo")
    victim = staged / "eval" / "fallback" / "bracket_bottom_fixed.json"
    body = json.loads(victim.read_text(encoding="utf-8"))
    body[NESTED_INTENT_KEY] = current_intent_document()
    body[NESTED_INTENT_KEY][SCHEMA_VERSION_FIELD] += 1
    original = write_document(victim, body)

    monkeypatch.setattr(stamp_module, "ROOT", staged)
    assert stamp_main(["--check"]) == 2

    captured = capsys.readouterr()
    assert "simulation_intent.schema_version_unsupported_future" in captured.err
    assert victim.read_text(encoding="utf-8") == original
    assert str(staged) not in captured.err


def test_unstamped_families_remain_unstamped():
    untouched = [
        "fixtures/bracket_expected.json",
        "tests/fixtures/bracket_expected.json",
        "eval/results.json",
        "eval/results-replay.json",
        "eval/results-initial.json",
        "eval/results-live-initial.json",
        "eval/results-replay-initial.json",
    ]
    for relative in untouched:
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        assert SCHEMA_VERSION_FIELD not in payload, relative
    golden = (ROOT / "tests" / "golden" / "bracket_abaqus.py").read_text(
        encoding="utf-8"
    )
    assert SCHEMA_VERSION_FIELD not in golden


# --------------------------------------------------------------------------
# Frozen evaluation evidence (decision D-3)
# --------------------------------------------------------------------------


def test_frozen_manifest_hash_is_unchanged_by_record_versioning():
    cases = load_cases(CASE_DIR, fixture_dir=FIXTURE_DIR)
    assert all(
        case.schema_version == EVALUATION_CASE_MIGRATIONS.current_version
        for case in cases
    )
    assert manifest_hash(cases) == FROZEN_MANIFEST_HASH


def test_versioned_corpus_hash_is_separate_and_deterministic():
    cases = load_cases(CASE_DIR, fixture_dir=FIXTURE_DIR)
    versioned = versioned_manifest_hash(cases)
    assert versioned != manifest_hash(cases)
    assert len(versioned) == 64
    assert versioned == versioned_manifest_hash(
        load_cases(CASE_DIR, fixture_dir=FIXTURE_DIR)
    )


def test_versioned_corpus_hash_reacts_to_a_version_change():
    cases = load_cases(CASE_DIR, fixture_dir=FIXTURE_DIR)
    bumped = [cases[0].model_copy(update={"schema_version": 2}), *cases[1:]]
    assert versioned_manifest_hash(bumped) != versioned_manifest_hash(cases)
    # The frozen hash deliberately does not move; that is the point of D-3.
    assert manifest_hash(bumped) == manifest_hash(cases) == FROZEN_MANIFEST_HASH


# --------------------------------------------------------------------------
# Legacy route compatibility (decision D-2)
# --------------------------------------------------------------------------


def test_legacy_normalizer_only_fills_a_missing_key(intent_payload):
    without = dict(intent_payload)
    without.pop(SCHEMA_VERSION_FIELD)
    payload, normalized = normalize_legacy_intent_payload(json.dumps(without))
    assert normalized is True
    assert payload[SCHEMA_VERSION_FIELD] == LEGACY_UNVERSIONED_INTENT_VERSION

    payload, normalized = normalize_legacy_intent_payload(json.dumps(intent_payload))
    assert normalized is False
    # A declared version is passed through exactly, never rewritten forward.
    assert payload[SCHEMA_VERSION_FIELD] == intent_payload[SCHEMA_VERSION_FIELD]


@pytest.mark.parametrize(
    "declared",
    [SIMULATION_INTENT_SCHEMA_VERSION + 1, 99, "1", 0, -1, 1.0, True],
)
def test_legacy_normalizer_never_rewrites_a_declared_version(intent_payload, declared):
    payload = dict(intent_payload)
    payload[SCHEMA_VERSION_FIELD] = declared
    normalized_payload, normalized = normalize_legacy_intent_payload(
        json.dumps(payload)
    )
    assert normalized is False
    assert normalized_payload[SCHEMA_VERSION_FIELD] == declared
    with pytest.raises(SchemaVersionError):
        load_simulation_intent(normalized_payload, source="legacy")


def test_legacy_normalizer_does_not_inspect_payload_shape():
    payload, normalized = normalize_legacy_intent_payload('{"totally": "unrelated"}')
    assert normalized is True
    assert payload == {
        "totally": "unrelated",
        SCHEMA_VERSION_FIELD: LEGACY_UNVERSIONED_INTENT_VERSION,
    }


def test_legacy_exception_does_not_apply_to_files_or_other_families(intent_payload):
    without = dict(intent_payload)
    without.pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(MissingSchemaVersionError):
        load_simulation_intent(without, source="examples/x.json")

    case_raw = json.loads(
        (CASE_DIR / "01_bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    case_raw.pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(MissingSchemaVersionError):
        load_evaluation_case(case_raw, source="eval/cases/x.json")

    fallback_raw = json.loads(
        (FALLBACK_DIR / "bracket_bottom_fixed.json").read_text(encoding="utf-8")
    )
    fallback_raw.pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(MissingSchemaVersionError):
        load_fallback_record(fallback_raw, source="eval/fallback/x.json")
