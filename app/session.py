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

from ir.schema import ExportBlockedError, SimulationIntent, StrictModel

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
            record.intent = intent.model_copy(deep=True)
            self._refresh_derived_state(record)
            return self._snapshot(record)

    def confirm_region(self, model_id: str, region_id: str) -> SessionSnapshot:
        return self._transition(model_id, region_id, "confirmed")

    def reject_region(self, model_id: str, region_id: str) -> SessionSnapshot:
        return self._transition(model_id, region_id, "rejected")

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
            record.intent = SimulationIntent.model_validate(payload)
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
            try:
                # Reuse the architectural Task 1 confirmation gate.  Do not
                # reproduce its rules here.
                intent.export_payload()
                export_eligible = True
            except ExportBlockedError:
                pass
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
