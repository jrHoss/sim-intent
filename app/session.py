"""Deterministic server-side selection sessions (Task 10).

Each uploaded model has exactly one lazily-created session, keyed by the
model's content-addressed id.  Region status remains part of the existing
``SimulationIntent`` IR; this module only owns the allowed state transitions
and session-derived viewer state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Literal, Union

from pydantic import Field

from ir.schema import (
    SimulationIntent,
    StrictModel,
    material_proposal_fingerprint,
)
from ir.validate import ValidationReport, validate_intent

EntityIds = Union[list[int], list[str]]


class RegionTransitionRequest(StrictModel):
    """Request body shared by the confirm and reject endpoints."""

    region_id: str = Field(min_length=1)


class SessionHighlight(StrictModel):
    """The active viewer highlight associated with one IR region."""

    entity_ids: EntityIds = Field(min_length=1)
    style: Literal["proposed", "confirmed"]


class SessionSnapshot(StrictModel):
    """Serializable state returned by every Task 10 session endpoint."""

    session_id: str
    model_id: str
    intent: SimulationIntent | None
    selected_entities: dict[str, EntityIds]
    highlight_state: dict[str, SessionHighlight]
    export_eligible: bool


class SessionIntentMissingError(RuntimeError):
    """Raised when a region transition is requested before an IR is saved."""


class SessionRegionMissingError(KeyError):
    """Raised when a requested region is absent from the current IR."""


class InvalidRegionTransitionError(RuntimeError):
    """Raised when a caller attempts to bypass the confirmation workflow."""


class SessionAssumptionMissingError(KeyError):
    """Raised when a requested assumption is absent from the current IR."""


class InvalidAssumptionTransitionError(RuntimeError):
    """Raised when an assumption transition is not pending -> terminal."""


@dataclass
class _SessionRecord:
    model_id: str
    intent: SimulationIntent | None = None
    selected_entities: dict[str, EntityIds] = field(default_factory=dict)
    highlight_state: dict[str, SessionHighlight] = field(default_factory=dict)


class SelectionSessionStore:
    """In-memory session store with atomic, deterministic transitions."""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = RLock()

    def get_or_create(self, model_id: str) -> SessionSnapshot:
        with self._lock:
            record = self._sessions.setdefault(model_id, _SessionRecord(model_id))
            return self._snapshot(record)

    def save_intent(
        self, model_id: str, intent: SimulationIntent
    ) -> SessionSnapshot:
        with self._lock:
            record = self._sessions.setdefault(model_id, _SessionRecord(model_id))
            self._validate_client_statuses(record.intent, intent)
            record.intent = self._with_computed_validation_status(intent)
            self._refresh_derived_state(record)
            return self._snapshot(record)

    def confirm_region(self, model_id: str, region_id: str) -> SessionSnapshot:
        return self._transition(model_id, region_id, "confirmed")

    def reject_region(self, model_id: str, region_id: str) -> SessionSnapshot:
        return self._transition(model_id, region_id, "rejected")

    def accept_assumption(self, model_id: str, assumption_id: str) -> SessionSnapshot:
        return self._transition_assumption(model_id, assumption_id, "accepted")

    def reject_assumption(self, model_id: str, assumption_id: str) -> SessionSnapshot:
        return self._transition_assumption(model_id, assumption_id, "rejected")

    def intent_and_report(
        self, model_id: str
    ) -> tuple[SimulationIntent, ValidationReport]:
        """Return an atomic deep-copy plus a freshly computed report."""

        with self._lock:
            record = self._sessions.setdefault(model_id, _SessionRecord(model_id))
            if record.intent is None:
                raise SessionIntentMissingError("session has no intent draft")
            intent = record.intent.model_copy(deep=True)
            return intent, validate_intent(intent)

    def _transition(
        self,
        model_id: str,
        region_id: str,
        target: Literal["confirmed", "rejected"],
    ) -> SessionSnapshot:
        with self._lock:
            record = self._sessions.setdefault(model_id, _SessionRecord(model_id))
            if record.intent is None:
                raise SessionIntentMissingError("session has no intent draft")

            matching = [region for region in record.intent.regions if region.id == region_id]
            if not matching:
                raise SessionRegionMissingError(region_id)
            region = matching[0]
            if region.status != "proposed":
                raise InvalidRegionTransitionError(
                    f"region '{region_id}' cannot transition from "
                    f"{region.status} to {target}; only proposed regions may transition"
                )

            regions = [
                existing.model_copy(update={"status": target})
                if existing.id == region_id
                else existing.model_copy(deep=True)
                for existing in record.intent.regions
            ]
            payload = record.intent.model_dump(mode="python")
            payload["regions"] = [item.model_dump(mode="python") for item in regions]
            record.intent = self._with_computed_validation_status(
                SimulationIntent.model_validate(payload)
            )
            self._refresh_derived_state(record)
            return self._snapshot(record)

    def _transition_assumption(
        self,
        model_id: str,
        assumption_id: str,
        target: Literal["accepted", "rejected"],
    ) -> SessionSnapshot:
        with self._lock:
            record = self._sessions.setdefault(model_id, _SessionRecord(model_id))
            if record.intent is None:
                raise SessionIntentMissingError("session has no intent draft")

            matching = [
                assumption
                for assumption in record.intent.assumptions
                if assumption.id == assumption_id
            ]
            if not matching:
                raise SessionAssumptionMissingError(assumption_id)
            assumption = matching[0]
            if assumption.status != "pending":
                raise InvalidAssumptionTransitionError(
                    f"assumption '{assumption_id}' cannot transition from "
                    f"{assumption.status} to {target}; only pending assumptions may transition"
                )

            decision_update: dict[str, str] = {"status": target}
            if target == "accepted":
                linked_materials = [
                    material
                    for material in record.intent.materials
                    if material.authority == "system_proposed"
                    and material.proposal_assumption_ref == assumption_id
                ]
                if len(linked_materials) > 1:
                    raise InvalidAssumptionTransitionError(
                        "a proposal decision must identify exactly one material"
                    )
                if linked_materials:
                    decision_update["material_proposal_fingerprint_sha256"] = (
                        material_proposal_fingerprint(linked_materials[0])
                    )
            assumptions = [
                existing.model_copy(update=decision_update)
                if existing.id == assumption_id
                else existing.model_copy(deep=True)
                for existing in record.intent.assumptions
            ]
            payload = record.intent.model_dump(mode="python")
            payload["assumptions"] = [
                item.model_dump(mode="python") for item in assumptions
            ]
            record.intent = self._with_computed_validation_status(
                SimulationIntent.model_validate(payload)
            )
            self._refresh_derived_state(record)
            return self._snapshot(record)

    @staticmethod
    def _validate_client_statuses(
        current: SimulationIntent | None, incoming: SimulationIntent
    ) -> None:
        """Keep confirmation/rejection status server-authoritative.

        A newly supplied or corrected region must be proposed.  Existing
        confirmed/rejected regions may be round-tripped unchanged, and a
        rejected region may be corrected by PUTing it again as proposed.
        """
        current_by_id = {} if current is None else {r.id: r for r in current.regions}
        incoming_ids = {region.id for region in incoming.regions}
        missing_managed = [
            region.id
            for region in (() if current is None else current.regions)
            if region.status in {"confirmed", "rejected"}
            and region.id not in incoming_ids
        ]
        if missing_managed:
            raise InvalidRegionTransitionError(
                "server-managed regions cannot be removed by intent PUT: "
                + ", ".join(missing_managed)
            )
        for region in incoming.regions:
            previous = current_by_id.get(region.id)
            if region.status == "proposed":
                if previous is not None and previous.status == "confirmed":
                    raise InvalidRegionTransitionError(
                        f"confirmed region '{region.id}' cannot be reopened by intent PUT"
                    )
                continue
            if previous is None or previous.status != region.status or previous != region:
                raise InvalidRegionTransitionError(
                    f"region '{region.id}' status '{region.status}' is server-managed"
                )

        current_assumptions = (
            {} if current is None else {item.id: item for item in current.assumptions}
        )
        incoming_assumption_ids = {item.id for item in incoming.assumptions}
        missing_assumptions = [
            item.id
            for item in (() if current is None else current.assumptions)
            if item.status in {"accepted", "rejected"}
            and item.id not in incoming_assumption_ids
        ]
        if missing_assumptions:
            raise InvalidAssumptionTransitionError(
                "server-managed assumptions cannot be removed by intent PUT: "
                + ", ".join(missing_assumptions)
            )
        for assumption in incoming.assumptions:
            previous = current_assumptions.get(assumption.id)
            if assumption.status == "pending":
                if assumption.material_proposal_fingerprint_sha256 is not None:
                    raise InvalidAssumptionTransitionError(
                        f"assumption '{assumption.id}' material fingerprint is server-managed"
                    )
                if previous is not None and previous.status != "pending":
                    raise InvalidAssumptionTransitionError(
                        f"assumption '{assumption.id}' status is server-managed"
                    )
                continue
            if (
                previous is None
                or previous.status != assumption.status
                or previous != assumption
            ):
                raise InvalidAssumptionTransitionError(
                    f"assumption '{assumption.id}' status '{assumption.status}' "
                    "is server-managed"
                )

        incoming_decisions = {item.id: item for item in incoming.assumptions}
        for material in incoming.materials:
            if material.authority != "system_proposed":
                continue
            decision = incoming_decisions.get(
                material.proposal_assumption_ref or ""
            )
            if decision is None or decision.status == "rejected":
                raise InvalidAssumptionTransitionError(
                    "a system-proposed material must reference a pending "
                    "decision or its exact accepted snapshot"
                )

        # An accepted proposal decision approves one exact material snapshot.
        # A changed system proposal must point to a new pending decision; an
        # explicit engineer-entered replacement must remove the proposal link.
        if current is not None:
            accepted_ids = {
                item.id
                for item in current.assumptions
                if item.status == "accepted"
                and item.material_proposal_fingerprint_sha256 is not None
            }
            current_materials = {
                item.proposal_assumption_ref: item
                for item in current.materials
                if item.authority == "system_proposed"
                and item.proposal_assumption_ref is not None
            }
            for material in incoming.materials:
                proposal_ref = material.proposal_assumption_ref
                if (
                    material.authority != "system_proposed"
                    or proposal_ref not in accepted_ids
                ):
                    continue
                previous_material = current_materials.get(proposal_ref)
                if (
                    previous_material is None
                    or material_proposal_fingerprint(material)
                    != material_proposal_fingerprint(previous_material)
                ):
                    raise InvalidAssumptionTransitionError(
                        "an accepted material proposal cannot be changed while "
                        "retaining its old decision; create a new pending proposal "
                        "or make an explicit engineer-entered material"
                    )

    @staticmethod
    def _refresh_derived_state(record: _SessionRecord) -> None:
        assert record.intent is not None
        # Rejected selections are retained in the IR for provenance, but are
        # removed from active selection/highlight state so correction is open.
        record.selected_entities = {
            region.id: list(region.entity_ids)
            for region in record.intent.regions
            if region.status != "rejected"
        }
        record.highlight_state = {
            region.id: SessionHighlight(
                entity_ids=list(region.entity_ids), style=region.status
            )
            for region in record.intent.regions
            if region.status in {"proposed", "confirmed"}
        }

    @staticmethod
    def _snapshot(record: _SessionRecord) -> SessionSnapshot:
        intent = record.intent.model_copy(deep=True) if record.intent is not None else None
        export_eligible = False
        if intent is not None:
            report = validate_intent(intent)
            export_eligible = report.export_eligible
            intent = intent.model_copy(
                update={"validation_status": report.validation_status}, deep=True
            )
        return SessionSnapshot(
            session_id=record.model_id,
            model_id=record.model_id,
            intent=intent,
            selected_entities={
                region_id: list(entity_ids)
                for region_id, entity_ids in record.selected_entities.items()
            },
            highlight_state={
                region_id: highlight.model_copy(deep=True)
                for region_id, highlight in record.highlight_state.items()
            },
            export_eligible=export_eligible,
        )

    @staticmethod
    def _with_computed_validation_status(intent: SimulationIntent) -> SimulationIntent:
        report = validate_intent(intent)
        return intent.model_copy(
            update={"validation_status": report.validation_status}, deep=True
        )
