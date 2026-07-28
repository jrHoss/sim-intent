"""Task 19 focused tests: schema versions, migration registries, loaders.

Registry mechanics (sequencing, gaps, duplicates, obsolete versions) are proven
with *synthetic* test-owned registries.  The production registries are
legitimately empty because the pre-Task-19 shape of this repository is
explicitly version 1 and no earlier shape has ever existed; inventing a fake
1 -> 2 production migration to exercise the mechanism would make "version 1"
mean "field absent", which is exactly the absence-inference Task 19 forbids.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ir.schema import SimulationIntent
from ir.schema_version import (
    SCHEMA_VERSION_FIELD,
    SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION,
    SIMULATION_INTENT_SCHEMA_VERSION,
    VERSIONED_FAMILIES,
)
from ir.versioning import (
    ABSENT,
    MalformedSchemaVersionError,
    MigrationPathError,
    MigrationRegistry,
    MigrationRegistryError,
    MissingSchemaVersionError,
    ObsoleteSchemaVersionError,
    PayloadStructureError,
    SAFETY_CRITICAL_PATHS,
    SIMULATION_INTENT_MIGRATIONS,
    SchemaVersionError,
    UnsupportedFutureVersionError,
    approval_upgrades,
    dump_simulation_intent,
    is_positive_integer_version,
    load_simulation_intent,
    project_paths,
    safety_critical_differences,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def intent_payload() -> dict[str, Any]:
    """A complete SimulationIntent payload declaring the current version."""

    payload = json.loads(
        (EXAMPLES / "bracket_sprint_goal.json").read_text(encoding="utf-8")
    )
    payload[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION
    return payload


@pytest.fixture
def legacy_intent_payload() -> dict[str, Any]:
    """The checked-in example exactly as committed: a real version-1 payload.

    The synthetic-registry tests need a payload that is genuinely *below* the
    registry's current version, otherwise the registry runs zero migrations and
    the assertion under test becomes vacuous.
    """

    return json.loads(
        (EXAMPLES / "bracket_sprint_goal.json").read_text(encoding="utf-8")
    )


def make_registry(
    *,
    current: int,
    minimum: int = 1,
    family: str = "synthetic",
) -> MigrationRegistry:
    return MigrationRegistry(
        family=family, current_version=current, minimum_supported_version=minimum
    )


# --------------------------------------------------------------------------
# 1. Valid current-version round trip
# --------------------------------------------------------------------------


def test_current_version_round_trip(intent_payload):
    intent = load_simulation_intent(intent_payload, source="test")
    assert intent.schema_version == SIMULATION_INTENT_SCHEMA_VERSION

    dumped = dump_simulation_intent(intent)
    assert dumped[SCHEMA_VERSION_FIELD] == SIMULATION_INTENT_SCHEMA_VERSION

    reloaded = load_simulation_intent(dumped, source="test")
    assert reloaded == intent
    assert dump_simulation_intent(reloaded) == dumped


def test_loader_accepts_bytes_and_text(intent_payload):
    text = json.dumps(intent_payload)
    assert load_simulation_intent(text, source="t") == load_simulation_intent(
        text.encode("utf-8"), source="t"
    )


def test_family_table_matches_registry_constants():
    current, minimum = VERSIONED_FAMILIES["simulation_intent"]
    assert current == SIMULATION_INTENT_MIGRATIONS.current_version
    assert minimum == SIMULATION_INTENT_MIGRATIONS.minimum_supported_version
    assert minimum == SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION


# --------------------------------------------------------------------------
# 2-3. Explicit historical migration and sequential multi-step behaviour
# --------------------------------------------------------------------------


def test_explicit_historical_version_migrates_to_current():
    registry = make_registry(current=2)

    @registry.register(1)
    def _one_to_two(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = dict(payload)
        migrated["added_in_v2"] = True
        return migrated

    registry.validate()
    result = registry.migrate({SCHEMA_VERSION_FIELD: 1, "kept": "value"})
    assert result == {
        SCHEMA_VERSION_FIELD: 2,
        "kept": "value",
        "added_in_v2": True,
    }


def test_sequential_multi_step_migration_applies_every_edge_in_order():
    registry = make_registry(current=4)
    calls: list[int] = []

    def make(edge: int):
        @registry.register(edge)
        def _migration(payload: Mapping[str, Any]) -> dict[str, Any]:
            calls.append(edge)
            migrated = dict(payload)
            migrated.setdefault("trail", [])
            migrated["trail"] = [*migrated["trail"], edge]
            return migrated

        return _migration

    for edge in (1, 2, 3):
        make(edge)
    registry.validate()

    result = registry.migrate({SCHEMA_VERSION_FIELD: 1})
    assert calls == [1, 2, 3]
    assert result["trail"] == [1, 2, 3]
    assert result[SCHEMA_VERSION_FIELD] == 4


def test_multi_step_migration_starting_mid_chain_skips_earlier_edges():
    registry = make_registry(current=4)
    calls: list[int] = []

    for edge in (1, 2, 3):
        registry.register(edge)(
            lambda payload, edge=edge: (calls.append(edge), dict(payload))[1]
        )
    registry.validate()

    registry.migrate({SCHEMA_VERSION_FIELD: 3})
    assert calls == [3]


def test_migrations_never_see_or_manage_the_version_field():
    registry = make_registry(current=3)
    seen: list[bool] = []

    for edge in (1, 2):
        registry.register(edge)(
            lambda payload: (
                seen.append(SCHEMA_VERSION_FIELD in payload),
                dict(payload),
            )[1]
        )
    registry.validate()

    result = registry.migrate({SCHEMA_VERSION_FIELD: 1})
    assert seen == [False, False]
    assert result[SCHEMA_VERSION_FIELD] == 3


@pytest.mark.parametrize("emitted", [1, 2, 7])
def test_migration_setting_the_version_field_is_a_registry_defect(emitted):
    registry = make_registry(current=2)
    registry.register(1)(lambda payload: {**payload, SCHEMA_VERSION_FIELD: emitted})
    with pytest.raises(MigrationPathError) as exc:
        registry.migrate({SCHEMA_VERSION_FIELD: 1})
    assert "the registry owns" in exc.value.safe_message


def test_migration_returning_non_object_is_a_registry_defect():
    registry = make_registry(current=2)
    registry.register(1)(lambda payload: ["not", "an", "object"])
    with pytest.raises(MigrationPathError):
        registry.migrate({SCHEMA_VERSION_FIELD: 1})


# --------------------------------------------------------------------------
# 4-6. Gaps, duplicates, and unrepresentable skipping edges
# --------------------------------------------------------------------------


def test_migration_path_gap_is_rejected_by_validate():
    registry = make_registry(current=4)
    registry.register(1)(lambda payload: dict(payload))
    registry.register(3)(lambda payload: dict(payload))
    with pytest.raises(MigrationRegistryError) as exc:
        registry.validate()
    assert "missing edges [2]" in str(exc.value)


def test_migration_path_gap_raises_at_migration_time():
    registry = make_registry(current=4)
    registry.register(1)(lambda payload: dict(payload))
    registry.register(3)(lambda payload: dict(payload))
    with pytest.raises(MigrationPathError) as exc:
        registry.migrate({SCHEMA_VERSION_FIELD: 1})
    assert exc.value.details["from_version"] == 2
    assert exc.value.http_status == 500


def test_duplicate_migration_edge_is_rejected_at_registration():
    registry = make_registry(current=3)
    registry.register(1)(lambda payload: dict(payload))
    with pytest.raises(MigrationRegistryError) as exc:
        registry.register(1)(lambda payload: dict(payload))
    assert "duplicate migration edge 1 -> 2" in str(exc.value)


def test_skipping_edge_is_unrepresentable():
    import inspect

    signature = inspect.signature(MigrationRegistry.register)
    assert list(signature.parameters) == ["self", "from_version"]


def test_edge_outside_supported_window_is_rejected():
    registry = make_registry(current=3, minimum=2)
    with pytest.raises(MigrationRegistryError):
        registry.register(1)(lambda payload: dict(payload))
    with pytest.raises(MigrationRegistryError):
        registry.register(3)(lambda payload: dict(payload))


def test_registry_construction_rejects_invalid_bounds():
    with pytest.raises(MigrationRegistryError):
        make_registry(current=0)
    with pytest.raises(MigrationRegistryError):
        make_registry(current=2, minimum=3)
    with pytest.raises(MigrationRegistryError):
        MigrationRegistry(
            family="bad", current_version=True, minimum_supported_version=1
        )


def test_production_registries_validate_at_import():
    # validate() proves the registered edge set exactly covers
    # minimum .. current - 1, so neither a forgotten edge nor a forgotten empty
    # registry can pass silently.
    SIMULATION_INTENT_MIGRATIONS.validate()
    assert SIMULATION_INTENT_MIGRATIONS.minimum_supported_version == 1
    assert SIMULATION_INTENT_MIGRATIONS.current_version == (
        SIMULATION_INTENT_SCHEMA_VERSION
    )
    assert SIMULATION_INTENT_MIGRATIONS.registered_edges == tuple(
        range(
            SIMULATION_INTENT_MIGRATIONS.minimum_supported_version,
            SIMULATION_INTENT_MIGRATIONS.current_version,
        )
    )
    # R3.1 introduced version 2, so the 1 -> 2 edge must exist.
    assert 1 in SIMULATION_INTENT_MIGRATIONS.registered_edges


# --------------------------------------------------------------------------
# 7-10. Typed version failures
# --------------------------------------------------------------------------


def test_unsupported_future_version_rejected_without_partial_parsing():
    payload = {
        SCHEMA_VERSION_FIELD: SIMULATION_INTENT_SCHEMA_VERSION + 1,
        "analysis": "deliberately not an object",
    }
    with pytest.raises(UnsupportedFutureVersionError) as exc:
        load_simulation_intent(payload, source="future.json")
    assert exc.value.code == "schema_version_unsupported_future"
    assert exc.value.details["supported_max"] == SIMULATION_INTENT_SCHEMA_VERSION
    assert exc.value.details["declared"] == SIMULATION_INTENT_SCHEMA_VERSION + 1


def test_missing_version_rejected_even_though_the_model_has_a_default(
    intent_payload,
):
    payload = dict(intent_payload)
    payload.pop(SCHEMA_VERSION_FIELD)
    # The model itself would happily default the field ...
    assert (
        SimulationIntent.model_validate(payload).schema_version
        == SIMULATION_INTENT_SCHEMA_VERSION
    )
    # ... but the authoritative loader must not accept an undeclared version.
    with pytest.raises(MissingSchemaVersionError) as exc:
        load_simulation_intent(payload, source="unversioned.json")
    assert exc.value.code == "schema_version_missing"


@pytest.mark.parametrize(
    "declared",
    ["1", 1.0, 1.5, True, False, None, 0, -1, [1], {"v": 1}, "one"],
)
def test_malformed_version_rejected(intent_payload, declared):
    payload = dict(intent_payload)
    payload[SCHEMA_VERSION_FIELD] = declared
    with pytest.raises(MalformedSchemaVersionError) as exc:
        load_simulation_intent(payload, source="malformed.json")
    assert exc.value.code == "schema_version_malformed"


def test_obsolete_version_rejected():
    registry = make_registry(current=3, minimum=2)
    registry.register(2)(lambda payload: dict(payload))
    registry.validate()
    with pytest.raises(ObsoleteSchemaVersionError) as exc:
        registry.migrate({SCHEMA_VERSION_FIELD: 1})
    assert exc.value.details["minimum_supported"] == 2


def test_is_positive_integer_version_rejects_bool_and_float():
    assert is_positive_integer_version(1)
    assert not is_positive_integer_version(True)
    assert not is_positive_integer_version(1.0)
    assert not is_positive_integer_version(0)


def test_typed_errors_expose_safe_problem_details(intent_payload):
    payload = dict(intent_payload)
    payload.pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(SchemaVersionError) as exc:
        load_simulation_intent(payload, source="C:\\secret\\dir\\file.json")
    details = exc.value.problem_details()
    assert details["code"] == "schema_version_missing"
    assert details["retryable"] is False
    assert details["family"] == "simulation_intent"
    # Absolute host paths are reduced, never echoed verbatim.
    assert "C:" not in json.dumps(details)
    assert details["source"] == "dir/file.json"


# --------------------------------------------------------------------------
# 11-12. Malformed and partial payload bodies
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.pop("regions"), id="missing-regions"),
        pytest.param(lambda p: p.pop("analysis"), id="missing-analysis"),
        pytest.param(lambda p: p.pop("materials"), id="missing-materials"),
        pytest.param(
            lambda p: p["analysis"]["units"].pop("length"), id="missing-unit"
        ),
        pytest.param(
            lambda p: p["regions"][0].pop("confidence"), id="partial-region"
        ),
        pytest.param(
            lambda p: p["regions"][0].pop("source_instruction"),
            id="missing-provenance",
        ),
        pytest.param(
            lambda p: p["regions"].__setitem__(0, {"id": "r"}), id="stub-region"
        ),
        pytest.param(
            lambda p: p.__setitem__("regions", "not-a-list"), id="wrong-type"
        ),
        pytest.param(
            lambda p: p.__setitem__("unexpected_field", 1), id="extra-field"
        ),
    ],
)
def test_malformed_or_partial_historical_payload_rejected(intent_payload, mutate):
    payload = copy.deepcopy(intent_payload)
    mutate(payload)
    with pytest.raises(PayloadStructureError) as exc:
        load_simulation_intent(payload, source="partial.json")
    assert exc.value.code == "payload_structure_invalid"


@pytest.mark.parametrize(
    "raw", [[], "not json at all", b"{", 5, None, '["a"]', "null"]
)
def test_non_object_payload_rejected(raw):
    with pytest.raises(PayloadStructureError):
        load_simulation_intent(raw, source="bad.json")


# --------------------------------------------------------------------------
# 13. Idempotency at the public loader boundary
# --------------------------------------------------------------------------


def test_migration_idempotency_at_public_loader_boundary(intent_payload):
    once = load_simulation_intent(intent_payload, source="t")
    twice = load_simulation_intent(dump_simulation_intent(once), source="t")
    thrice = load_simulation_intent(dump_simulation_intent(twice), source="t")
    assert once == twice == thrice
    assert dump_simulation_intent(once) == dump_simulation_intent(thrice)


def test_current_version_payload_runs_zero_migrations():
    registry = make_registry(current=2)
    calls: list[int] = []
    registry.register(1)(
        lambda payload: (calls.append(1), dict(payload))[1]
    )
    registry.validate()
    registry.migrate({SCHEMA_VERSION_FIELD: 2})
    assert calls == []


# --------------------------------------------------------------------------
# 14-16. Safety-critical semantics
# --------------------------------------------------------------------------


def test_safety_critical_projection_marks_absence(intent_payload):
    projected = project_paths(intent_payload)
    assert projected["analysis.units.length"] == ("mm",)
    assert projected["regions[].status"] == ("proposed", "proposed")
    # The bracket example has no gravity load, so no load carries a magnitude.
    assert projected["loads[].magnitude"] == (ABSENT,)


def test_safety_critical_fields_preserved_across_migration(legacy_intent_payload):
    intent_payload = legacy_intent_payload
    registry = make_registry(current=2)
    registry.register(1)(lambda payload: {**payload, "cosmetic": "added"})
    registry.validate()

    migrated = registry.migrate(intent_payload)
    assert migrated[SCHEMA_VERSION_FIELD] == 2
    assert migrated["cosmetic"] == "added"  # the migration really ran
    assert safety_critical_differences(intent_payload, migrated) == {}


def test_no_silent_approval_or_confirmation_upgrade(legacy_intent_payload):
    intent_payload = legacy_intent_payload
    unsafe_registry = make_registry(current=2)

    def _unsafe(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for region in migrated["regions"]:
            region["status"] = "confirmed"
        for assumption in migrated["assumptions"]:
            assumption["status"] = "accepted"
        migrated["validation_status"] = "valid"
        return migrated

    unsafe_registry.register(1)(_unsafe)
    unsafe_registry.validate()

    migrated = unsafe_registry.migrate(intent_payload)
    upgrades = approval_upgrades(intent_payload, migrated)
    assert upgrades, "the detector must see a fabricated approval upgrade"
    assert "validation_status[0]" in upgrades
    assert any(key.startswith("regions[].status") for key in upgrades)
    assert any(key.startswith("assumptions[].status") for key in upgrades)

    # A conservative migration triggers nothing.
    safe_registry = make_registry(current=2)
    safe_registry.register(1)(lambda payload: {**payload, "note": "safe"})
    safe_registry.validate()
    assert approval_upgrades(
        intent_payload, safe_registry.migrate(intent_payload)
    ) == {}


# The production-registry safety conformance gate lives in
# tests/test_migration_safety.py (review correction B-2). It covers all four
# registries and classifies synthesis, deletion, mutation, and approval
# upgrades, replacing the single-registry synthesis-only check that used to
# live here.


# --------------------------------------------------------------------------
# 17. Writes emit only the current version
# --------------------------------------------------------------------------


def test_writes_emit_only_the_current_version(intent_payload):
    intent = load_simulation_intent(intent_payload, source="t")
    stale = intent.model_copy(update={"schema_version": 99})
    assert (
        dump_simulation_intent(stale)[SCHEMA_VERSION_FIELD]
        == SIMULATION_INTENT_SCHEMA_VERSION
    )


def test_export_payload_carries_the_current_version():
    payload = json.loads(
        (EXAMPLES / "bracket_confirmed_export_ready.json").read_text(
            encoding="utf-8"
        )
    )
    payload[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION
    intent = load_simulation_intent(payload, source="t")
    assert (
        intent.export_payload()[SCHEMA_VERSION_FIELD]
        == SIMULATION_INTENT_SCHEMA_VERSION
    )


def test_schema_version_field_is_declared_in_the_published_json_schema():
    from ir.schema import export_json_schema

    schema = export_json_schema()
    assert SCHEMA_VERSION_FIELD in schema["properties"]
    assert schema["properties"][SCHEMA_VERSION_FIELD]["minimum"] == 1
