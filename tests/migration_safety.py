"""Family-aware migration safety conformance harness (review correction B-2).

The first conformance test only inspected ``SIMULATION_INTENT_MIGRATIONS``, only
looked for *synthesis* from absence, never called ``approval_upgrades()``, and
was vacuous while the registries were empty.

This harness covers **all four** production registries, uses a complete
representative payload appropriate to each family, and classifies four distinct
failure modes:

``synthesis``
    a protected field that was absent is now present;
``deletion``
    a protected field that was present is now absent;
``mutation``
    a protected field changed value;
``approval_upgrade``
    the payload moved toward a more-approved state (``proposed -> confirmed``,
    ``pending -> accepted``, ``unvalidated``/``invalid -> valid``, or a
    family-specific equivalent).

Every registered edge is picked up automatically from
``registry.registered_edges``, so a future migration is audited without anyone
remembering to add a second test.

A change to a protected path is a violation *unless* that exact change is listed
in :data:`APPROVED_SAFETY_CHANGES`, which is the explicit migration-evidence
hook.  It is empty today because the production registries are empty.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, Literal, Mapping, Sequence

from app.record_versions import FALLBACK_RECORD_MIGRATIONS, NESTED_INTENT_KEY
from eval.versioning import EVALUATION_CASE_MIGRATIONS, REPLAY_RECORD_MIGRATIONS
from ir.schema_version import SCHEMA_VERSION_FIELD
from ir.versioning import (
    ABSENT,
    SAFETY_CRITICAL_PATHS,
    SIMULATION_INTENT_MIGRATIONS,
    MigrationRegistry,
    approval_upgrades,
    project_paths,
)

ROOT = Path(__file__).resolve().parents[1]

ViolationKind = Literal["synthesis", "deletion", "mutation", "approval_upgrade"]


@dataclass(frozen=True)
class SafetyViolation:
    kind: ViolationKind
    scope: str
    path: str
    before: Any
    after: Any

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.path}"

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        return f"{self.kind} at {self.key}: {self.before!r} -> {self.after!r}"


# --------------------------------------------------------------------------
# Family-specific protected paths
# --------------------------------------------------------------------------

EVALUATION_CASE_PROTECTED: Final[tuple[str, ...]] = (
    "case_id",
    "model_fixture",
    "instruction",
    "clarification_required",
    "clarification_response",
    "artifact_export_eligible",
    "expected_structured_ir_subset",
    "expected_conditions[].intent_index",
    "expected_conditions[].entity_ids",
    "expected_conditions[].region_entity_type",
    "expected_conditions[].condition_type",
    "expected_conditions[].components",
    "expected_conditions[].vector",
    "expected_conditions[].magnitude",
    "expected_conditions[].internal_unit",
    "expected_conditions[].expected_ir_subset",
    "click_evidence[].intent_index",
    "click_evidence[].entity_ids",
)

#: An evaluation case becoming export-eligible, or losing a required
#: clarification, weakens a safety gate exactly the way an approval upgrade
#: does, so it is treated as one.
EVALUATION_CASE_APPROVAL: Final[dict[str, tuple[Any, ...]]] = {
    "artifact_export_eligible": (True,),
    "clarification_required": (False,),
}

FALLBACK_ENVELOPE_PROTECTED: Final[tuple[str, ...]] = (
    "mode",
    "case_id",
    "model_fixture",
    "model_sha256",
    "clarification_used",
    "validation_status_before_review",
)

REPLAY_MANIFEST_PROTECTED: Final[tuple[str, ...]] = ("records",)


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NestedContract:
    """A payload embedded inside an envelope, audited in its own right."""

    key: str
    protected_paths: tuple[str, ...]
    use_intent_approval: bool = False
    approval_map: Mapping[str, tuple[Any, ...]] | None = None


@dataclass(frozen=True)
class FamilyContract:
    name: str
    registry: MigrationRegistry
    protected_paths: tuple[str, ...]
    representative: Callable[[], dict[str, Any]]
    use_intent_approval: bool = False
    approval_map: Mapping[str, tuple[Any, ...]] | None = None
    nested: tuple[NestedContract, ...] = ()


def _load(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


FAMILY_CONTRACTS: Final[tuple[FamilyContract, ...]] = (
    FamilyContract(
        name="simulation_intent",
        registry=SIMULATION_INTENT_MIGRATIONS,
        protected_paths=SAFETY_CRITICAL_PATHS,
        representative=lambda: _load("examples/bracket_sprint_goal.json"),
        use_intent_approval=True,
    ),
    FamilyContract(
        name="evaluation_case",
        registry=EVALUATION_CASE_MIGRATIONS,
        protected_paths=EVALUATION_CASE_PROTECTED,
        representative=lambda: _load("eval/cases/01_bracket_bottom_fixed.json"),
        approval_map=EVALUATION_CASE_APPROVAL,
    ),
    FamilyContract(
        name="fallback_record",
        registry=FALLBACK_RECORD_MIGRATIONS,
        protected_paths=FALLBACK_ENVELOPE_PROTECTED,
        representative=lambda: _load("eval/fallback/bracket_bottom_fixed.json"),
        nested=(
            NestedContract(
                key=NESTED_INTENT_KEY,
                protected_paths=SAFETY_CRITICAL_PATHS,
                use_intent_approval=True,
            ),
        ),
    ),
    FamilyContract(
        name="replay_record",
        registry=REPLAY_RECORD_MIGRATIONS,
        protected_paths=REPLAY_MANIFEST_PROTECTED,
        representative=lambda: _load("eval/replay/manifest.json"),
    ),
)


#: ``(family, from_version) -> {"scope:path", ...}`` explicitly approved by
#: recorded migration evidence.  Empty while the registries are empty.
APPROVED_SAFETY_CHANGES: Final[dict[tuple[str, int], frozenset[str]]] = {}


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _classify(
    scope: str, path: str, before: Sequence[Any], after: Sequence[Any]
) -> list[SafetyViolation]:
    violations: list[SafetyViolation] = []
    if len(before) != len(after):
        return [SafetyViolation("mutation", scope, path, before, after)]
    for old, new in zip(before, after):
        if old == new:
            continue
        if old is ABSENT:
            violations.append(SafetyViolation("synthesis", scope, path, old, new))
        elif new is ABSENT:
            violations.append(SafetyViolation("deletion", scope, path, old, new))
        else:
            violations.append(SafetyViolation("mutation", scope, path, old, new))
    return violations


def compare_protected(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    paths: Sequence[str],
    *,
    scope: str,
) -> list[SafetyViolation]:
    """Detect synthesis, deletion, and mutation of protected paths."""

    projected_before = project_paths(before, paths)
    projected_after = project_paths(after, paths)
    violations: list[SafetyViolation] = []
    for path in paths:
        violations.extend(
            _classify(scope, path, projected_before[path], projected_after[path])
        )
    return violations


def detect_approval_upgrades(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    upgrades: Mapping[str, tuple[Any, ...]],
    *,
    scope: str,
) -> list[SafetyViolation]:
    """Family-specific approval strengthening, mirroring approval_upgrades()."""

    violations: list[SafetyViolation] = []
    for path, approved_values in upgrades.items():
        old_values = project_paths(before, [path])[path]
        new_values = project_paths(after, [path])[path]
        for index, new_value in enumerate(new_values):
            old_value = old_values[index] if index < len(old_values) else ABSENT
            if new_value in approved_values and old_value != new_value:
                violations.append(
                    SafetyViolation(
                        "approval_upgrade",
                        scope,
                        f"{path}[{index}]",
                        old_value,
                        new_value,
                    )
                )
    return violations


def _intent_approval_violations(
    before: Mapping[str, Any], after: Mapping[str, Any], *, scope: str
) -> list[SafetyViolation]:
    """Run the *production* approval_upgrades() detector."""

    return [
        SafetyViolation("approval_upgrade", scope, path, old, new)
        for path, (old, new) in approval_upgrades(before, after).items()
    ]


def audit_transition(
    contract: FamilyContract,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    from_version: int,
    approved: Mapping[tuple[str, int], frozenset[str]] = APPROVED_SAFETY_CHANGES,
) -> list[SafetyViolation]:
    """Audit one before/after pair against a family contract."""

    violations = compare_protected(
        before, after, contract.protected_paths, scope=contract.name
    )
    if contract.use_intent_approval:
        violations += _intent_approval_violations(before, after, scope=contract.name)
    if contract.approval_map:
        violations += detect_approval_upgrades(
            before, after, contract.approval_map, scope=contract.name
        )

    for nested in contract.nested:
        nested_before = before.get(nested.key)
        nested_after = after.get(nested.key)
        scope = f"{contract.name}.{nested.key}"
        if not isinstance(nested_before, Mapping) or not isinstance(
            nested_after, Mapping
        ):
            violations.append(
                SafetyViolation(
                    "deletion" if isinstance(nested_before, Mapping) else "mutation",
                    scope,
                    nested.key,
                    type(nested_before).__name__,
                    type(nested_after).__name__,
                )
            )
            continue
        violations += compare_protected(
            nested_before, nested_after, nested.protected_paths, scope=scope
        )
        if nested.use_intent_approval:
            violations += _intent_approval_violations(
                nested_before, nested_after, scope=scope
            )
        if nested.approval_map:
            violations += detect_approval_upgrades(
                nested_before, nested_after, nested.approval_map, scope=scope
            )

    allowed = approved.get((contract.name, from_version), frozenset())
    return [violation for violation in violations if violation.key not in allowed]


def audit_registry(
    contract: FamilyContract,
    *,
    approved: Mapping[tuple[str, int], frozenset[str]] = APPROVED_SAFETY_CHANGES,
) -> dict[int, list[SafetyViolation]]:
    """Audit every registered edge of one family.

    Each edge is exercised by declaring a complete, current representative
    payload at that version and migrating it to the current version through the
    public registry path.  New edges are picked up automatically.
    """

    findings: dict[int, list[SafetyViolation]] = {}
    for edge in contract.registry.registered_edges:
        payload = copy.deepcopy(contract.representative())
        payload[SCHEMA_VERSION_FIELD] = edge
        before = copy.deepcopy(payload)
        after = contract.registry.migrate(payload)
        findings[edge] = audit_transition(
            contract, before, after, from_version=edge, approved=approved
        )
    return findings


def describe(violations: Sequence[SafetyViolation]) -> str:  # pragma: no cover
    return "\n".join(f"  - {violation}" for violation in violations)
