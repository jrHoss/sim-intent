"""Migration safety conformance gate (review correction B-2).

Two things are proven here:

1. every production registry passes the conformance audit, and every future
   registered edge is picked up automatically;
2. the harness is **not vacuous** -- deliberately unsafe synthetic migrations
   are rejected for deletion, mutation, synthesis, approval upgrades, and
   unsafe nested ``proposed_ir`` changes, while a safe metadata-only migration
   is accepted.

Production registries stay empty: ``current == minimum == 1`` for every family
and no fake ``1 -> 2`` migration is invented.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))

from app.record_versions import FALLBACK_RECORD_MIGRATIONS, NESTED_INTENT_KEY
from eval.versioning import EVALUATION_CASE_MIGRATIONS, REPLAY_RECORD_MIGRATIONS
from ir.schema_version import SCHEMA_VERSION_FIELD
from ir.versioning import SIMULATION_INTENT_MIGRATIONS, MigrationRegistry
from tests.migration_safety import (
    FAMILY_CONTRACTS,
    FamilyContract,
    SafetyViolation,
    audit_registry,
    audit_transition,
    describe,
)

ALL_PRODUCTION_REGISTRIES = (
    SIMULATION_INTENT_MIGRATIONS,
    EVALUATION_CASE_MIGRATIONS,
    FALLBACK_RECORD_MIGRATIONS,
    REPLAY_RECORD_MIGRATIONS,
)


def contract_for(name: str) -> FamilyContract:
    return next(item for item in FAMILY_CONTRACTS if item.name == name)


def kinds(violations: list[SafetyViolation]) -> set[str]:
    return {violation.kind for violation in violations}


def paths(violations: list[SafetyViolation]) -> set[str]:
    return {violation.key for violation in violations}


def run_unsafe(contract: FamilyContract, transform) -> list[SafetyViolation]:
    """Audit a synthetic 1 -> 2 migration built from ``transform``."""

    registry = MigrationRegistry(
        family=f"synthetic_{contract.name}",
        current_version=2,
        minimum_supported_version=1,
    )
    registry.register(1)(transform)
    registry.validate()

    payload = copy.deepcopy(contract.representative())
    payload[SCHEMA_VERSION_FIELD] = 1
    before = copy.deepcopy(payload)
    after = registry.migrate(payload)
    return audit_transition(contract, before, after, from_version=1)


# --------------------------------------------------------------------------
# Coverage of every production registry
# --------------------------------------------------------------------------


def test_every_production_registry_has_a_contract():
    """Requirement 8: a new registry cannot escape the harness unnoticed."""

    covered = {id(contract.registry) for contract in FAMILY_CONTRACTS}
    assert covered == {id(registry) for registry in ALL_PRODUCTION_REGISTRIES}
    assert {contract.name for contract in FAMILY_CONTRACTS} == {
        "simulation_intent",
        "evaluation_case",
        "fallback_record",
        "replay_record",
    }


@pytest.mark.parametrize(
    "contract", FAMILY_CONTRACTS, ids=lambda item: item.name
)
def test_representative_payload_is_complete_and_current(contract: FamilyContract):
    payload = contract.representative()
    assert isinstance(payload, dict) and payload
    # A checked-in representative may legitimately remain at an older supported
    # version -- ``audit_registry`` restamps it per edge anyway -- but it must
    # never declare a version this build cannot load.
    declared = payload[SCHEMA_VERSION_FIELD]
    assert (
        contract.registry.minimum_supported_version
        <= declared
        <= contract.registry.current_version
    )
    for path in contract.protected_paths:
        assert isinstance(path, str) and path


@pytest.mark.parametrize(
    "contract", FAMILY_CONTRACTS, ids=lambda item: item.name
)
def test_production_registry_passes_the_safety_conformance_audit(
    contract: FamilyContract,
):
    findings = audit_registry(contract)
    for edge, violations in findings.items():
        assert not violations, (
            f"{contract.name} edge {edge} -> {edge + 1} violates migration "
            f"safety:\n{describe(violations)}"
        )
    if not findings:
        # No edges yet. Assert the precondition that makes that correct, so an
        # accidentally-empty registry cannot pass this test silently.
        assert contract.registry.registered_edges == ()
        assert (
            contract.registry.current_version
            == contract.registry.minimum_supported_version
            == 1
        )
        contract.registry.validate()


def test_audit_picks_up_new_edges_automatically():
    """A newly registered edge is audited without editing any test."""

    contract = contract_for("simulation_intent")
    registry = MigrationRegistry(
        family="synthetic_autopickup", current_version=3, minimum_supported_version=1
    )
    registry.register(1)(lambda payload: dict(payload))
    registry.register(2)(lambda payload: dict(payload))
    registry.validate()
    replacement = FamilyContract(
        name=contract.name,
        registry=registry,
        protected_paths=contract.protected_paths,
        representative=contract.representative,
        use_intent_approval=True,
    )
    findings = audit_registry(replacement)
    assert sorted(findings) == [1, 2]
    assert all(not violations for violations in findings.values())


# --------------------------------------------------------------------------
# Non-vacuous proof: deletion
# --------------------------------------------------------------------------


def test_harness_rejects_deletion_of_region_entity_ids():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for region in migrated["regions"]:
            region.pop("entity_ids")
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "deletion" in kinds(violations)
    assert "simulation_intent:regions[].entity_ids" in paths(violations)


def test_harness_rejects_deletion_of_bc_components():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for bc in migrated["bcs"]:
            bc.pop("components")
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "deletion" in kinds(violations)
    assert "simulation_intent:bcs[].components" in paths(violations)


# --------------------------------------------------------------------------
# Non-vacuous proof: mutation
# --------------------------------------------------------------------------


def test_harness_rejects_mutation_of_bc_components():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for bc in migrated["bcs"]:
            bc["components"] = ["x"]
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "mutation" in kinds(violations)
    assert "simulation_intent:bcs[].components" in paths(violations)


def test_harness_rejects_mutation_of_canonical_units():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["analysis"]["units"]["force"] = "kN"
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "mutation" in kinds(violations)
    assert "simulation_intent:analysis.units.force" in paths(violations)


# --------------------------------------------------------------------------
# Non-vacuous proof: synthesis
# --------------------------------------------------------------------------


def test_harness_rejects_synthesis_of_an_absent_safety_critical_field():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for material in migrated["materials"]:
            material["density_tonne_per_mm3"] = 7.85e-9
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "synthesis" in kinds(violations)
    assert (
        "simulation_intent:materials[].density_tonne_per_mm3" in paths(violations)
    )


# --------------------------------------------------------------------------
# Non-vacuous proof: approval upgrades
# --------------------------------------------------------------------------


def test_harness_rejects_proposed_to_confirmed():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for region in migrated["regions"]:
            region["status"] = "confirmed"
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "approval_upgrade" in kinds(violations)
    assert any("regions[].status" in path for path in paths(violations))


def test_harness_rejects_pending_to_accepted():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for assumption in migrated["assumptions"]:
            assumption["status"] = "accepted"
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "approval_upgrade" in kinds(violations)
    assert any("assumptions[].status" in path for path in paths(violations))


def test_harness_rejects_unvalidated_to_valid():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["validation_status"] = "valid"
        return migrated

    violations = run_unsafe(contract_for("simulation_intent"), transform)
    assert "approval_upgrade" in kinds(violations)
    assert any("validation_status" in path for path in paths(violations))


def test_harness_rejects_evaluation_case_export_eligibility_upgrade():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["artifact_export_eligible"] = True
        return migrated

    violations = run_unsafe(contract_for("evaluation_case"), transform)
    assert "approval_upgrade" in kinds(violations)
    assert any("artifact_export_eligible" in path for path in paths(violations))


def test_harness_rejects_dropping_a_required_clarification():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["clarification_required"] = False
        migrated["clarification_response"] = None
        return migrated

    # The default representative has no required clarification, so this case
    # uses one that does and the True -> False transition is genuinely made.
    base = contract_for("evaluation_case")
    contract = FamilyContract(
        name=base.name,
        registry=base.registry,
        protected_paths=base.protected_paths,
        representative=lambda: load_json(
            "eval/cases/04_bracket_inner_pressure_clarify.json"
        ),
        approval_map=base.approval_map,
    )
    assert contract.representative()["clarification_required"] is True
    violations = run_unsafe(contract, transform)
    assert "approval_upgrade" in kinds(violations)
    assert any("clarification_required" in path for path in paths(violations))


# --------------------------------------------------------------------------
# Non-vacuous proof: nested proposed_ir inside a fallback envelope
# --------------------------------------------------------------------------


def test_harness_rejects_unsafe_nested_proposed_ir_mutation():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for region in migrated[NESTED_INTENT_KEY]["regions"]:
            region["entity_ids"] = [999]
        return migrated

    violations = run_unsafe(contract_for("fallback_record"), transform)
    assert "mutation" in kinds(violations)
    assert (
        f"fallback_record.{NESTED_INTENT_KEY}:regions[].entity_ids"
        in paths(violations)
    )


def test_harness_rejects_nested_proposed_ir_approval_upgrade():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        for region in migrated[NESTED_INTENT_KEY]["regions"]:
            region["status"] = "confirmed"
        migrated[NESTED_INTENT_KEY]["validation_status"] = "valid"
        return migrated

    violations = run_unsafe(contract_for("fallback_record"), transform)
    assert "approval_upgrade" in kinds(violations)
    assert any(
        path.startswith(f"fallback_record.{NESTED_INTENT_KEY}:")
        for path in paths(violations)
    )


def test_harness_rejects_dropping_the_nested_intent_entirely():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated.pop(NESTED_INTENT_KEY)
        return migrated

    violations = run_unsafe(contract_for("fallback_record"), transform)
    assert "deletion" in kinds(violations)


def test_harness_rejects_envelope_provenance_mutation():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["model_sha256"] = "0" * 64
        return migrated

    violations = run_unsafe(contract_for("fallback_record"), transform)
    assert "mutation" in kinds(violations)
    assert "fallback_record:model_sha256" in paths(violations)


# --------------------------------------------------------------------------
# Non-vacuous proof: replay manifest
# --------------------------------------------------------------------------


def test_harness_rejects_replay_manifest_record_tampering():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        first = sorted(migrated["records"])[0]
        migrated["records"][first] = "0" * 64
        return migrated

    violations = run_unsafe(contract_for("replay_record"), transform)
    assert "mutation" in kinds(violations)
    assert "replay_record:records" in paths(violations)


def test_harness_rejects_replay_manifest_record_deletion():
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated.pop("records")
        return migrated

    violations = run_unsafe(contract_for("replay_record"), transform)
    assert "deletion" in kinds(violations)


# --------------------------------------------------------------------------
# Requirement 10: a safe migration must be accepted
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contract", FAMILY_CONTRACTS, ids=lambda item: item.name
)
def test_harness_accepts_a_safe_metadata_only_migration(contract: FamilyContract):
    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["migration_note"] = "added by a safe non-safety-critical migration"
        return migrated

    violations = run_unsafe(contract, transform)
    assert violations == [], describe(violations)


def test_approved_change_hook_can_whitelist_an_exact_change():
    """Requirement 4: a mutation is a violation *unless* evidence approves it."""

    contract = contract_for("simulation_intent")

    def transform(payload: Mapping[str, Any]) -> dict[str, Any]:
        migrated = copy.deepcopy(dict(payload))
        migrated["validation_status"] = "invalid"
        return migrated

    registry = MigrationRegistry(
        family="synthetic_approved", current_version=2, minimum_supported_version=1
    )
    registry.register(1)(transform)
    registry.validate()
    payload = copy.deepcopy(contract.representative())
    payload[SCHEMA_VERSION_FIELD] = 1
    before = copy.deepcopy(payload)
    after = registry.migrate(payload)

    unapproved = audit_transition(contract, before, after, from_version=1)
    assert "simulation_intent:validation_status" in paths(unapproved)

    approved = audit_transition(
        contract,
        before,
        after,
        from_version=1,
        approved={("simulation_intent", 1): frozenset({"simulation_intent:validation_status"})},
    )
    assert approved == []
