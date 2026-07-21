"""Deterministic grounding of Task 11 intents onto Task 6 query results.

The language model stops at typed query operations.  This module serializes
those already-validated operations, executes the existing :class:`QueryEngine`,
and creates either a proposed Task 1 :class:`Region` or a strictly typed
clarification request.  It performs no language-model calls, unit conversion,
session mutation, validation, confirmation, or export.

Confidence is based on the margin between *candidate-set* scores.  Each set's
score is the arithmetic mean of its unchanged Task 6 member scores; its margin
is top minus runner-up, with the Task 6 convention of ``1.0`` when only one set
remains.  Query confidence is ``clamp(candidate_set_margin, 0.0, 1.0)``.  The
raw Task 6 per-entity scores and ``QueryResult.score_margin`` are retained in
``QueryEvidence``.  Grouping is used only when a structured operation defines
one selection set (for example, an exact-size hole group or a rank of N faces),
so tied members of one requested set are not mistaken for competing choices.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geom.cylinders import CylinderRecord, group_holes
from geom.inventory import FaceInventory
from ground.queries import Query, QueryEngine, QueryResult
from ir.schema import Region
from llm.interpreter import (
    BCPayload,
    Interpretation,
    InterpretedIntent,
    LoadPayload,
)


DEFAULT_AMBIGUITY_THRESHOLD = 0.15
DIRECT_CLICK_CONFIDENCE = 0.99


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CandidateEntitySet(StrictModel):
    """One highlightable alternative presented to the engineer."""

    entity_type: Literal["cad_face"] = "cad_face"
    entity_ids: list[int] = Field(min_length=1)
    inventory_sha256: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source: Literal["query", "user_click"]
    score: float | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class QueryEvidence(StrictModel):
    """Exact deterministic query input and output retained for auditability."""

    op_list: list[dict[str, Any]] = Field(min_length=1)
    entity_ids: list[int]
    per_candidate_scores: dict[int, float]
    score_margin: float = Field(ge=0.0)
    candidate_set_margin: float = Field(ge=0.0)


class ClickEvidence(StrictModel):
    """Trusted viewer/backend IDs bound to the inventory that produced them."""

    inventory_sha256: str = Field(min_length=1)
    entity_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_entity_ids(self) -> "ClickEvidence":
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("click entity_ids must be unique")
        return self

    @classmethod
    def for_inventory(
        cls, inventory: FaceInventory, entity_ids: Sequence[int]
    ) -> "ClickEvidence":
        return cls(
            inventory_sha256=inventory.file_sha256,
            entity_ids=sorted(int(entity_id) for entity_id in entity_ids),
        )


ClarificationReason = Literal[
    "low_score_margin",
    "multiple_candidates",
    "count_mismatch",
    "no_usable_candidate",
    "click_query_conflict",
    "click_inventory_mismatch",
]


class ClarificationRequest(StrictModel):
    """Typed, JSON-serializable ambiguity result for viewer highlighting."""

    kind: Literal["clarification_request"] = "clarification_request"
    reason: ClarificationReason
    question: str = Field(min_length=1)
    candidate_sets: list[CandidateEntitySet]
    query_evidence: QueryEvidence
    click_evidence: ClickEvidence | None = None
    source_instruction: str
    target_description: str = Field(min_length=1)
    requested_count: int | None = Field(default=None, ge=1)


class GroundedIntent(StrictModel):
    """Task-local wrapper retaining payload and grounding evidence together."""

    intent_index: int = Field(ge=0)
    source_instruction: str
    target_description: str = Field(min_length=1)
    bc: BCPayload | None = None
    load: LoadPayload | None = None
    query_evidence: QueryEvidence
    click_evidence: ClickEvidence | None = None
    region: Region | None = None
    clarification: ClarificationRequest | None = None

    @model_validator(mode="after")
    def _one_payload_and_one_outcome(self) -> "GroundedIntent":
        if (self.bc is None) == (self.load is None):
            raise ValueError("grounded intent requires exactly one BC/load payload")
        whole_model_gravity = (
            self.load is not None
            and self.load.type == "gravity"
            and self.region is None
            and self.clarification is None
        )
        if not whole_model_gravity and (self.region is None) == (self.clarification is None):
            raise ValueError("grounded intent requires exactly one grounding outcome")
        return self


class GroundingBatch(StrictModel):
    results: list[GroundedIntent] = Field(min_length=1)


class QueryExecutor(Protocol):
    def execute(self, query: Query | list[dict[str, Any]]) -> QueryResult: ...


_COUNT_WORDS = {"one": 1, "two": 2, "three": 3}
_EXPLICIT_COUNT_RE = re.compile(
    r"\b(one|two|three|[123])\s+"
    r"(?:(?:bolt|mounting|cylindrical|inner|outer|larger|smaller|wall|through|"
    r"similar-radius|selected|matching|top|bottom|upper|lower)\s+){0,3}"
    r"(holes?|faces?|edges?|nodes?|entities?|surfaces?|sets?)\b",
    re.IGNORECASE,
)


def _clamp_confidence(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def _json_ops(intent: InterpretedIntent) -> list[dict[str, Any]]:
    return [operation.model_dump(mode="json") for operation in intent.op_list]


def _walk_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for op in ops:
        flattened.append(op)
        for branch in op.get("queries", []):
            branch_ops = branch.get("ops", []) if isinstance(branch, dict) else []
            flattened.extend(_walk_ops(branch_ops))
    return flattened


def _structured_count(ops: list[dict[str, Any]]) -> int | None:
    """Recover exact cardinality encoded by the typed query itself."""
    exact_hole_group_counts: list[int] = []
    ranked_counts: list[int] = []
    for op in _walk_ops(ops):
        name = op.get("op")
        if name == "hole_groups":
            minimum = int(op.get("min_size", 1))
            maximum = int(op.get("max_size", 2**31 - 1))
            if minimum == maximum:
                exact_hole_group_counts.append(minimum)
        elif name in {"rank_by", "area_max", "area_min"}:
            ranked_counts.append(int(op.get("n", 1)))
    if exact_hole_group_counts:
        return exact_hole_group_counts[-1]
    if ranked_counts:
        return ranked_counts[-1]
    return None


def _explicit_count(source_instruction: str, target_description: str) -> int | None:
    """Parse only explicit 1-3 cardinalities immediately attached to entities."""
    # The per-intent target is checked first so a count belonging to another
    # clause in a multi-intent source instruction cannot leak across intents.
    for text in (target_description, source_instruction):
        match = _EXPLICIT_COUNT_RE.search(text)
        if match:
            token = match.group(1).lower()
            return _COUNT_WORDS.get(token, int(token) if token.isdigit() else None)
    return None


def _requested_count(
    ops: list[dict[str, Any]], source_instruction: str, target_description: str
) -> int | None:
    return _structured_count(ops) or _explicit_count(source_instruction, target_description)


def _axis_label(direction: Sequence[float]) -> str:
    dominant = max(range(3), key=lambda index: abs(float(direction[index])))
    sign = "+" if float(direction[dominant]) >= 0 else "-"
    return f"{sign}{'XYZ'[dominant]}"


def _candidate_label(
    entity_ids: list[int], cylinders: dict[int, CylinderRecord]
) -> str:
    records = [cylinders.get(entity_id) for entity_id in entity_ids]
    if records and all(record is not None and record.classification == "hole" for record in records):
        concrete = [record for record in records if record is not None]
        radius = f"{fmean(record.radius for record in concrete):.6g}"
        axis = _axis_label(concrete[0].axis_dir)
        if len(entity_ids) == 1:
            return f"hole (radius {radius} mm, axis {axis})"
        return f"{len(entity_ids)}-hole group (radius {radius} mm, axis {axis})"
    if len(entity_ids) == 1:
        return "matching face"
    return f"matching {len(entity_ids)}-face set"


def _candidate_set_score(
    entity_ids: list[int], per_candidate_scores: dict[int, float]
) -> float:
    return fmean(per_candidate_scores.get(entity_id, 0.0) for entity_id in entity_ids)


def _candidate_set_margin(candidate_sets: list[CandidateEntitySet]) -> float:
    scores = sorted(
        (candidate.score for candidate in candidate_sets if candidate.score is not None),
        reverse=True,
    )
    if not scores:
        return 0.0
    if len(scores) == 1:
        return 1.0
    return max(0.0, float(scores[0] - scores[1]))


def _query_candidate_sets(
    *,
    entity_ids: list[int],
    per_candidate_scores: dict[int, float],
    ops: list[dict[str, Any]],
    requested_count: int | None,
    inventory: FaceInventory,
    cylinders: dict[int, CylinderRecord],
) -> list[CandidateEntitySet]:
    """Build selection alternatives without running a second geometry query."""
    selected = set(entity_ids)
    grouped_ids: list[list[int]] = []
    flattened = _walk_ops(ops)
    exact_hole_group = any(
        op.get("op") == "hole_groups"
        and int(op.get("min_size", 1)) == int(op.get("max_size", 2**31 - 1))
        for op in flattened
    )
    exact_rank = any(
        op.get("op") in {"rank_by", "area_max", "area_min"}
        and requested_count is not None
        and int(op.get("n", 1)) == requested_count
        for op in flattened
    )

    if exact_hole_group:
        for group in group_holes(cylinders):
            tags = sorted(group.face_tags)
            if tags and set(tags) <= selected:
                grouped_ids.append(tags)
    elif exact_rank and requested_count == len(entity_ids):
        grouped_ids.append(sorted(entity_ids))
    elif requested_count and requested_count > 1 and requested_count == len(entity_ids):
        # A geometrically established Task 3 hole group is one candidate set,
        # even if Task 11 expressed it through radius/axis filters.
        for group in group_holes(cylinders):
            tags = sorted(group.face_tags)
            if set(tags) == selected:
                grouped_ids.append(tags)
                break

    represented = set().union(*(set(group) for group in grouped_ids)) if grouped_ids else set()
    grouped_ids.extend([[entity_id] for entity_id in entity_ids if entity_id not in represented])

    candidates = [
        CandidateEntitySet(
            entity_ids=tags,
            inventory_sha256=inventory.file_sha256,
            label=_candidate_label(tags, cylinders),
            source="query",
            score=float(_candidate_set_score(tags, per_candidate_scores)),
            confidence=_clamp_confidence(_candidate_set_score(tags, per_candidate_scores)),
        )
        for tags in grouped_ids
    ]
    return sorted(
        candidates,
        key=lambda candidate: (
            -(candidate.score if candidate.score is not None else float("-inf")),
            candidate.entity_ids,
        ),
    )


def _question(reason: ClarificationReason, *, requested_count: int | None) -> str:
    if reason == "no_usable_candidate":
        return "I could not find a usable geometry candidate for this target. Which region did you mean?"
    if reason == "count_mismatch":
        return (
            f"You requested {requested_count} entities, but the geometry query did not return "
            "that count. Which highlighted entities should be used?"
        )
    if reason == "low_score_margin":
        return "The best geometry alternatives score too similarly. Which highlighted candidate is correct?"
    if reason == "multiple_candidates":
        return "More than one geometry candidate remains. Which highlighted candidate is correct?"
    if reason == "click_inventory_mismatch":
        return "The click belongs to a different model inventory. Please click the intended region on this model."
    return "The clicked geometry conflicts with the text query. Should the click or a queried candidate be used?"


@dataclass
class GroundingEngine:
    inventory: FaceInventory
    cylinders: dict[int, CylinderRecord]
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD
    query_engine: QueryExecutor | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.ambiguity_threshold <= 1.0:
            raise ValueError("ambiguity_threshold must be in the range 0.0-1.0")
        if self.query_engine is None:
            self.query_engine = QueryEngine(self.inventory, self.cylinders)

    def ground_intent(
        self,
        source_instruction: str,
        intent: InterpretedIntent,
        *,
        intent_index: int = 0,
        click_evidence: ClickEvidence | None = None,
        region_id: str | None = None,
    ) -> GroundedIntent:
        """Ground one validated Task 11 intent without reinterpreting language."""
        ops = _json_ops(intent)
        query = Query.from_dict({"ops": ops})
        assert self.query_engine is not None
        query_result = self.query_engine.execute(query)
        requested_count = _requested_count(
            ops, source_instruction, intent.target_description
        )
        inventory_ids = {face.tag for face in self.inventory.faces}
        usable_ids = [entity_id for entity_id in query_result.entity_ids if entity_id in inventory_ids]
        candidate_sets = _query_candidate_sets(
            entity_ids=usable_ids,
            per_candidate_scores=query_result.per_candidate_scores,
            ops=ops,
            requested_count=requested_count,
            inventory=self.inventory,
            cylinders=self.cylinders,
        )
        set_margin = _candidate_set_margin(candidate_sets)
        query_evidence = QueryEvidence(
            op_list=ops,
            entity_ids=list(query_result.entity_ids),
            per_candidate_scores=dict(query_result.per_candidate_scores),
            score_margin=float(query_result.score_margin),
            candidate_set_margin=set_margin,
        )

        # Gravity is explicitly a whole-model load in the solver-neutral IR.
        # The real Task 6 query still executes and remains auditable, but no
        # arbitrary face selection is promoted into a region.
        if intent.load is not None and intent.load.type == "gravity":
            return GroundedIntent(
                intent_index=intent_index,
                source_instruction=source_instruction,
                target_description=intent.target_description,
                load=intent.load,
                query_evidence=query_evidence,
                click_evidence=click_evidence,
            )

        def clarify(
            reason: ClarificationReason,
            candidates: list[CandidateEntitySet] | None = None,
        ) -> GroundedIntent:
            request = ClarificationRequest(
                reason=reason,
                question=_question(reason, requested_count=requested_count),
                candidate_sets=candidates if candidates is not None else candidate_sets,
                query_evidence=query_evidence,
                click_evidence=click_evidence,
                source_instruction=source_instruction,
                target_description=intent.target_description,
                requested_count=requested_count,
            )
            return GroundedIntent(
                intent_index=intent_index,
                source_instruction=source_instruction,
                target_description=intent.target_description,
                bc=intent.bc,
                load=intent.load,
                query_evidence=query_evidence,
                click_evidence=click_evidence,
                clarification=request,
            )

        if len(usable_ids) != len(query_result.entity_ids) or not usable_ids:
            return clarify("no_usable_candidate")

        if click_evidence is not None:
            clicked = set(click_evidence.entity_ids)
            clicked_candidate = CandidateEntitySet(
                entity_ids=sorted(clicked),
                inventory_sha256=click_evidence.inventory_sha256,
                label="direct user click",
                source="user_click",
                confidence=DIRECT_CLICK_CONFIDENCE,
            )
            if click_evidence.inventory_sha256 != self.inventory.file_sha256:
                return clarify("click_inventory_mismatch", [*candidate_sets, clicked_candidate])
            if not clicked <= inventory_ids or not clicked <= set(usable_ids):
                return clarify("click_query_conflict", [*candidate_sets, clicked_candidate])
            if requested_count is not None and len(clicked) != requested_count:
                return clarify("count_mismatch", [*candidate_sets, clicked_candidate])
            region = Region(
                id=region_id or f"region_{intent_index + 1}",
                entity_type="cad_face",
                entity_ids=sorted(clicked),
                selection_method="user_click",
                confidence=DIRECT_CLICK_CONFIDENCE,
                source_instruction=source_instruction,
                status="proposed",
            )
            return GroundedIntent(
                intent_index=intent_index,
                source_instruction=source_instruction,
                target_description=intent.target_description,
                bc=intent.bc,
                load=intent.load,
                query_evidence=query_evidence,
                click_evidence=click_evidence,
                region=region,
            )

        if requested_count is not None and len(usable_ids) != requested_count:
            return clarify("count_mismatch")
        if set_margin < self.ambiguity_threshold:
            return clarify("low_score_margin")
        if len(candidate_sets) != 1:
            return clarify("multiple_candidates")

        region = Region(
            id=region_id or f"region_{intent_index + 1}",
            entity_type="cad_face",
            entity_ids=candidate_sets[0].entity_ids,
            selection_method="semantic_geometry_query",
            confidence=_clamp_confidence(set_margin),
            source_instruction=source_instruction,
            status="proposed",
        )
        return GroundedIntent(
            intent_index=intent_index,
            source_instruction=source_instruction,
            target_description=intent.target_description,
            bc=intent.bc,
            load=intent.load,
            query_evidence=query_evidence,
            click_evidence=click_evidence,
            region=region,
        )

    def ground_interpretation(
        self,
        source_instruction: str,
        interpretation: Interpretation,
        *,
        click_evidence_by_intent: dict[int, ClickEvidence] | None = None,
    ) -> GroundingBatch:
        clicks = click_evidence_by_intent or {}
        results = [
            self.ground_intent(
                source_instruction,
                intent,
                intent_index=index,
                click_evidence=clicks.get(index),
            )
            for index, intent in enumerate(interpretation.intents)
        ]
        return GroundingBatch(results=results)


def ground_interpretation(
    source_instruction: str,
    interpretation: Interpretation,
    inventory: FaceInventory,
    cylinders: dict[int, CylinderRecord],
    *,
    ambiguity_threshold: float = DEFAULT_AMBIGUITY_THRESHOLD,
    click_evidence_by_intent: dict[int, ClickEvidence] | None = None,
) -> GroundingBatch:
    """Convenience functional API for grounding a validated interpretation."""
    return GroundingEngine(
        inventory=inventory,
        cylinders=cylinders,
        ambiguity_threshold=ambiguity_threshold,
    ).ground_interpretation(
        source_instruction,
        interpretation,
        click_evidence_by_intent=click_evidence_by_intent,
    )
