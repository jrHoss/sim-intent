"""Natural-language interpreter producing typed, unresolved operations (Task 11).

The model sees a concise semantic summary, never a raw face inventory.  Its
output is limited to model-safe Task 6 operations and Task 7 value-plus-unit
payloads.  This module does not execute queries, resolve ids, create Regions,
or mutate application state.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, Union

from openai import OpenAI, OpenAIError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from geom.cylinders import CylinderRecord, group_holes
from geom.inventory import FaceInventory
from geom.labels import extreme_face_labels
from ground.queries import (
    LLM_SAFE_QUERY_OPERATION_NAMES,
    POSITION_PREDICATES,
    query_vocabulary,
)
from ground.semantics import (
    normalize_quantity,
    parse_quantity,
    semantics_vocabulary,
    supported_units,
)


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_MAX_OUTPUT_TOKENS = 2_048


class InterpreterError(RuntimeError):
    """Raised after all bounded interpretation attempts are invalid."""

    def __init__(self, message: str, *, attempts: int, last_reason: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_reason = last_reason


class InterpreterProviderError(RuntimeError):
    """Safe provider/configuration failure outside LLM parse taxonomy."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class UnsupportedMaterialInputError(RuntimeError):
    """A material request lacks the explicit properties needed for safety."""

    code = "material.properties_incomplete"
    safe_message = (
        "A material must state Young's modulus and Poisson's ratio numerically "
        "with a supported Young's-modulus unit. Named materials are not looked "
        "up automatically; density is optional unless gravity is used."
    )

    def __init__(
        self,
        code: str | None = None,
        safe_message: str | None = None,
    ) -> None:
        self.code = code or type(self).code
        self.safe_message = safe_message or type(self).safe_message
        super().__init__(self.safe_message)


class CombinedMaterialInputError(UnsupportedMaterialInputError):
    code = "material.combined_request_requires_separation"
    safe_message = (
        "Submit the numeric material proposal separately from boundary-condition "
        "or load instructions so no engineering condition is omitted."
    )


class UnsupportedCapabilityError(RuntimeError):
    """An explicit request outside the linear-static single-solid envelope."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HoleGroupSummary(StrictModel):
    count: int = Field(ge=1)
    radius_mm: float = Field(gt=0)
    axis_direction: list[float] = Field(min_length=3, max_length=3)

    @field_validator("axis_direction")
    @classmethod
    def _finite_nonzero_axis(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(component) for component in value):
            raise ValueError("axis direction must be finite")
        if math.sqrt(sum(component * component for component in value)) == 0:
            raise ValueError("axis direction cannot be zero")
        return value


class AreaSummary(StrictModel):
    minimum_mm2: float = Field(ge=0)
    maximum_mm2: float = Field(ge=0)
    largest_first_mm2: list[float] = Field(max_length=5)


class FaceInventorySummary(StrictModel):
    """Concise semantic inventory deliberately containing no entity ids."""

    source_name: str
    face_count: int = Field(ge=1)
    surface_type_counts: dict[str, int]
    available_labels: list[str]
    hole_groups: list[HoleGroupSummary]
    face_areas: AreaSummary


def summarize_face_inventory(
    inventory: FaceInventory,
    cylinders: dict[int, CylinderRecord] | None = None,
) -> FaceInventorySummary:
    """Build the only inventory representation intended for the LLM prompt."""
    areas = sorted((float(face.area) for face in inventory.faces), reverse=True)
    labels = sorted(f"{name}_face" for name in extreme_face_labels(inventory.faces))
    groups = [
        HoleGroupSummary(
            count=len(group.face_tags),
            radius_mm=round(float(group.radius), 6),
            axis_direction=[round(float(component), 6) for component in group.axis_dir],
        )
        for group in group_holes(cylinders or {})
    ]
    return FaceInventorySummary(
        source_name=inventory.source_name,
        face_count=len(inventory.faces),
        surface_type_counts=dict(sorted(Counter(face.surface_type for face in inventory.faces).items())),
        available_labels=labels,
        hole_groups=groups,
        face_areas=AreaSummary(
            minimum_mm2=min(areas),
            maximum_mm2=max(areas),
            largest_first_mm2=areas[:5],
        ),
    )


# Task 6 operation schema.  Only canonical constructor argument names are
# accepted; id-taking operations (adjacent_to/in_component) are absent.
class FindFacesOp(StrictModel):
    op: Literal["find_faces"]
    surface_type: str | None = None


class HolesOp(StrictModel):
    op: Literal["holes"]


class HoleGroupsOp(StrictModel):
    op: Literal["hole_groups"]
    min_size: int = Field(default=1, ge=1)
    max_size: int = Field(default=2**31 - 1, ge=1)

    @model_validator(mode="after")
    def _ordered_sizes(self) -> "HoleGroupsOp":
        if self.max_size < self.min_size:
            raise ValueError("max_size must be greater than or equal to min_size")
        return self


class FilterRadiusOp(StrictModel):
    op: Literal["filter_radius"]
    radius: float = Field(gt=0)
    rtol: float = Field(default=0.05, ge=0)


class FilterAxisOp(StrictModel):
    op: Literal["filter_axis"]
    direction: list[float] = Field(min_length=3, max_length=3)
    tol_deg: float = Field(default=2.0, ge=0, le=90)

    @field_validator("direction")
    @classmethod
    def _nonzero_direction(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(component) for component in value):
            raise ValueError("axis direction must be finite")
        if math.sqrt(sum(component * component for component in value)) == 0:
            raise ValueError("axis direction cannot be zero")
        return value


class RankByOp(StrictModel):
    op: Literal["rank_by"]
    position: Literal[
        "top",
        "upper",
        "highest",
        "above",
        "bottom",
        "lower",
        "lowest",
        "below",
        "largest",
        "area_max",
        "smallest",
        "area_min",
    ]
    n: int = Field(default=1, ge=1)


class AreaMaxOp(StrictModel):
    op: Literal["area_max"]
    n: int = Field(default=1, ge=1)


class AreaMinOp(StrictModel):
    op: Literal["area_min"]
    n: int = Field(default=1, ge=1)


class LabeledOp(StrictModel):
    op: Literal["labeled"]
    name: str = Field(min_length=1)


class QueryBranch(StrictModel):
    ops: list["QueryOperation"] = Field(min_length=1)


class IntersectOp(StrictModel):
    op: Literal["intersect"]
    queries: list[QueryBranch] = Field(min_length=1)


class UnionOp(StrictModel):
    op: Literal["union"]
    queries: list[QueryBranch] = Field(min_length=1)


class DifferenceOp(StrictModel):
    op: Literal["difference"]
    queries: list[QueryBranch] = Field(min_length=1)


QueryOperation = Annotated[
    Union[
        FindFacesOp,
        HolesOp,
        HoleGroupsOp,
        FilterRadiusOp,
        FilterAxisOp,
        RankByOp,
        AreaMaxOp,
        AreaMinOp,
        LabeledOp,
        IntersectOp,
        UnionOp,
        DifferenceOp,
    ],
    Field(discriminator="op"),
]
QueryBranch.model_rebuild()


Axis = Literal["x", "y", "z"]


class FixedDisplacementPayload(StrictModel):
    type: Literal["fixed_displacement"]
    components: list[Axis] = Field(min_length=1)

    @field_validator("components")
    @classmethod
    def _unique_components(cls, value: list[Axis]) -> list[Axis]:
        if len(value) != len(set(value)):
            raise ValueError("components must be unique")
        return value


class PrescribedDisplacementPayload(StrictModel):
    type: Literal["prescribed_displacement"]
    components: dict[Axis, str] = Field(min_length=1)

    @field_validator("components")
    @classmethod
    def _length_values(cls, value: dict[Axis, str]) -> dict[Axis, str]:
        for quantity in value.values():
            parse_quantity(quantity, expected_kind="length")
        return value


BCPayload = Annotated[
    Union[FixedDisplacementPayload, PrescribedDisplacementPayload],
    Field(discriminator="type"),
]


class DirectionalLoadPayload(StrictModel):
    magnitude: str
    direction: str = Field(min_length=1)


class ResultantSurfaceForcePayload(DirectionalLoadPayload):
    type: Literal["resultant_surface_force"]

    @field_validator("magnitude")
    @classmethod
    def _force_quantity(cls, value: str) -> str:
        parse_quantity(value, expected_kind="force")
        return value


class SurfaceTractionPayload(DirectionalLoadPayload):
    type: Literal["surface_traction"]

    @field_validator("magnitude")
    @classmethod
    def _stress_quantity(cls, value: str) -> str:
        parse_quantity(value, expected_kind="stress")
        return value


class PressurePayload(StrictModel):
    type: Literal["pressure"]
    magnitude: str

    @field_validator("magnitude")
    @classmethod
    def _stress_quantity(cls, value: str) -> str:
        parse_quantity(value, expected_kind="stress")
        return value


class GravityPayload(StrictModel):
    type: Literal["gravity"]
    direction: str = Field(min_length=1)


class ConcentratedForcePayload(DirectionalLoadPayload):
    type: Literal["concentrated_force"]

    @field_validator("magnitude")
    @classmethod
    def _force_quantity(cls, value: str) -> str:
        parse_quantity(value, expected_kind="force")
        return value


LoadPayload = Annotated[
    Union[
        ResultantSurfaceForcePayload,
        SurfaceTractionPayload,
        PressurePayload,
        GravityPayload,
        ConcentratedForcePayload,
    ],
    Field(discriminator="type"),
]


class InterpretedIntent(StrictModel):
    op_list: list[QueryOperation] = Field(min_length=1)
    bc: BCPayload | None = None
    load: LoadPayload | None = None
    target_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> "InterpretedIntent":
        if (self.bc is None) == (self.load is None):
            raise ValueError("each intent requires exactly one of bc or load")
        return self


class MaterialProposalPayload(StrictModel):
    """A numeric natural-language material proposal, never an approval."""

    name: str = Field(default="proposed_material", min_length=1)
    youngs_modulus: str = Field(min_length=1)
    poisson_ratio: float = Field(gt=-1.0, lt=0.5)
    density: str | None = Field(default=None, min_length=1)

    @field_validator("youngs_modulus")
    @classmethod
    def _stress_quantity(cls, value: str) -> str:
        parse_quantity(value, expected_kind="stress")
        return value

    @field_validator("density")
    @classmethod
    def _density_quantity(cls, value: str | None) -> str | None:
        if value is not None:
            numeric, unit = value.rsplit(maxsplit=1)
            normalize_quantity(float(numeric), unit, kind="density")
        return value


class Interpretation(StrictModel):
    intents: list[InterpretedIntent] = Field(default_factory=list)
    material_proposal: MaterialProposalPayload | None = None
    _attempts: int = PrivateAttr(default=1)

    @model_validator(mode="after")
    def _has_content(self) -> "Interpretation":
        if not self.intents and self.material_proposal is None:
            raise ValueError("an interpretation requires an engineering request")
        return self

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def retry_count(self) -> int:
        return self._attempts - 1


# OpenAI Structured Outputs supports a strict subset of JSON Schema and does
# not accept the ``oneOf`` emitted by Pydantic discriminated unions inside
# arrays.  Keep the strongly typed internal models above, but expose this
# provider-specific wire shape to the Responses API.  Every parsed wire value
# is converted back through ``Interpretation.model_validate`` before return.
WireOperationName = Literal[
    "find_faces",
    "holes",
    "hole_groups",
    "filter_radius",
    "filter_axis",
    "rank_by",
    "area_max",
    "area_min",
    "labeled",
    "intersect",
    "union",
    "difference",
]
WirePositionPredicate = Literal[
    "top",
    "upper",
    "highest",
    "above",
    "bottom",
    "lower",
    "lowest",
    "below",
    "largest",
    "area_max",
    "smallest",
    "area_min",
]


class OpenAIWireOperation(StrictModel):
    op: WireOperationName
    surface_type: str | None = None
    min_size: int | None = Field(default=None, ge=1)
    max_size: int | None = Field(default=None, ge=1)
    radius: float | None = Field(default=None, gt=0)
    rtol: float | None = Field(default=None, ge=0)
    direction: list[float] | None = Field(default=None, min_length=3, max_length=3)
    tol_deg: float | None = Field(default=None, ge=0, le=90)
    position: WirePositionPredicate | None = None
    n: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1)
    queries: list["OpenAIWireQueryBranch"] | None = Field(default=None, min_length=1)


class OpenAIWireQueryBranch(StrictModel):
    ops: list[OpenAIWireOperation] = Field(min_length=1)


OpenAIWireOperation.model_rebuild()


class OpenAIWireAxisValue(StrictModel):
    axis: Axis
    value: str


class OpenAIWireBCPayload(StrictModel):
    type: Literal["fixed_displacement", "prescribed_displacement"]
    components: list[Axis] | None = Field(default=None, min_length=1)
    component_values: list[OpenAIWireAxisValue] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _matching_components(self) -> "OpenAIWireBCPayload":
        if self.type == "fixed_displacement":
            if self.components is None or self.component_values is not None:
                raise ValueError("fixed_displacement requires only components")
        else:
            if self.component_values is None or self.components is not None:
                raise ValueError("prescribed_displacement requires only component_values")
            axes = [item.axis for item in self.component_values]
            if len(axes) != len(set(axes)):
                raise ValueError("component_values axes must be unique")
        return self


class OpenAIWireLoadPayload(StrictModel):
    type: Literal[
        "resultant_surface_force",
        "surface_traction",
        "pressure",
        "gravity",
        "concentrated_force",
    ]
    magnitude: str | None = None
    direction: str | None = Field(default=None, min_length=1)


class OpenAIWireIntent(StrictModel):
    op_list: list[OpenAIWireOperation] = Field(min_length=1)
    bc: OpenAIWireBCPayload | None = None
    load: OpenAIWireLoadPayload | None = None
    target_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> "OpenAIWireIntent":
        if (self.bc is None) == (self.load is None):
            raise ValueError("each intent requires exactly one of bc or load")
        return self


class OpenAIWireInterpretation(StrictModel):
    intents: list[OpenAIWireIntent] = Field(min_length=1)

    def to_internal_payload(self) -> dict[str, Any]:
        intents: list[dict[str, Any]] = []
        for intent in self.intents:
            bc: dict[str, Any] | None = None
            if intent.bc is not None:
                if intent.bc.type == "fixed_displacement":
                    bc = {"type": intent.bc.type, "components": intent.bc.components}
                else:
                    assert intent.bc.component_values is not None
                    bc = {
                        "type": intent.bc.type,
                        "components": {
                            item.axis: item.value for item in intent.bc.component_values
                        },
                    }
            intents.append(
                {
                    "op_list": [
                        operation.model_dump(mode="json", exclude_none=True)
                        for operation in intent.op_list
                    ],
                    "bc": bc,
                    "load": (
                        intent.load.model_dump(mode="json", exclude_none=True)
                        if intent.load is not None
                        else None
                    ),
                    "target_description": intent.target_description,
                }
            )
        return {"intents": intents}


_SCHEMA_OPERATION_NAMES = frozenset(
    {
        "find_faces",
        "holes",
        "hole_groups",
        "filter_radius",
        "filter_axis",
        "rank_by",
        "area_max",
        "area_min",
        "labeled",
        "intersect",
        "union",
        "difference",
    }
)
if _SCHEMA_OPERATION_NAMES != LLM_SAFE_QUERY_OPERATION_NAMES:
    raise RuntimeError("interpreter query schema is out of sync with Task 6 model-safe vocabulary")
if set(RankByOp.model_fields["position"].annotation.__args__) != POSITION_PREDICATES:
    raise RuntimeError("interpreter position predicates are out of sync with Task 6")


_FORBIDDEN_KEYS = frozenset(
    {
        "entity_id",
        "entity_ids",
        "face_id",
        "face_ids",
        "edge_id",
        "edge_ids",
        "node_id",
        "node_ids",
        "element_id",
        "element_ids",
        "nset",
        "nsets",
        "elset",
        "elsets",
        "node_set",
        "node_sets",
        "element_set",
        "element_sets",
        "set_name",
    }
)
_DIRECT_ENTITY_TEXT = re.compile(
    r"\b(?:faces?|edges?|nodes?|elements?)(?:[_\s-]*(?:ids?)?[_\s:#=\-]*)\d+\b",
    re.IGNORECASE,
)
_DIRECT_SET_TEXT = re.compile(r"(?<![A-Za-z0-9])(?:NSET|ELSET)(?![A-Za-z0-9])", re.IGNORECASE)


def _assert_no_direct_entity_references(value: Any, *, path: str = "output") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden direct entity selection field at {path}.{key}")
            _assert_no_direct_entity_references(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_direct_entity_references(item, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if _DIRECT_ENTITY_TEXT.search(value) or _DIRECT_SET_TEXT.search(value):
            raise ValueError(f"forbidden direct entity selection text at {path}")


def _validate_labels(value: Interpretation, summary: FaceInventorySummary) -> None:
    available = set(summary.available_labels)

    def visit(ops: list[QueryOperation]) -> None:
        for op in ops:
            if isinstance(op, LabeledOp) and op.name not in available:
                raise ValueError(f"unknown inventory label: {op.name!r}")
            if isinstance(op, (IntersectOp, UnionOp, DifferenceOp)):
                for branch in op.queries:
                    visit(branch.ops)

    for intent in value.intents:
        visit(intent.op_list)


@dataclass(frozen=True)
class ModelRequest:
    system: str
    messages: list[dict[str, str]]
    output_format: type[Interpretation]


class StructuredOutputTransport(Protocol):
    def complete(self, request: ModelRequest) -> Any: ...


class OpenAIStructuredOutputTransport:
    """Small OpenAI boundary so unit tests can replace the entire network."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._client = client
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
        self.max_output_tokens = max_output_tokens

    def complete(self, request: ModelRequest) -> Any:
        if self._client is None and not (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENAI_ADMIN_KEY")
        ):
            raise InterpreterProviderError(
                "provider_not_configured",
                "Live interpretation is unavailable because the OpenAI provider is not configured. Set OPENAI_API_KEY on the server or use a clearly labeled REPLAY fallback case.",
            )
        try:
            client = self._client if self._client is not None else OpenAI()
            response = client.responses.parse(
                model=self.model,
                max_output_tokens=self.max_output_tokens,
                instructions=request.system,
                input=request.messages,
                text_format=OpenAIWireInterpretation,
            )
        except InterpreterProviderError:
            raise
        except OpenAIError as exc:
            raise InterpreterProviderError(
                "provider_unavailable",
                "Live interpretation could not reach the configured OpenAI provider. Check the server-side credentials and provider availability, then retry.",
            ) from exc
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("OpenAI response did not contain parsed structured output")
        wire = (
            parsed
            if isinstance(parsed, OpenAIWireInterpretation)
            else OpenAIWireInterpretation.model_validate(parsed, strict=True)
        )
        return wire.to_internal_payload()


SYSTEM_PROMPT = """You are a simulation-intent interpreter. Translate the user's instruction into unresolved typed operations only.

Hard rules:
- Never output CAD face, edge, node, or element identifiers.
- Never output NSET or ELSET names.
- Use only the supplied Task 6 query operations and their schema fields.
- Preserve load and displacement quantities as value-plus-unit strings; do not convert units.
- Describe direction in words. Do not turn it into a numeric vector.
- Do not execute operations, resolve geometry, create Regions, score ambiguity, mutate state, highlight, validate a final IR, confirm, or export.
- Keep multiple conditions as separate intents in their original order.
- Material definitions are outside this schema; never encode or silently reinterpret them.
- Under the Task 7 model convention, qualitative vertical motion means the Y displacement component unless the user explicitly names another axis.
- A lateral directional "side" (left, right, front, or back) is not an exact face label. Use a broad planar find_faces operation so deterministic grounding can expose plausible candidates and confidence.
"""


_MATERIAL_PROPERTY_PATTERN = re.compile(
    r"(?:\byoung(?:'s|s)?\s+modulus\b|(?<!\w)[Ee]\s*(?:=|:)|"
    r"\bpoisson(?:'s|s)?\s+ratio\b|\bnu\s*(?:=|:)|ν\s*(?:=|:)|"
    r"\bdensity\b)",
    re.IGNORECASE,
)
_MATERIAL_REQUEST_PATTERN = re.compile(
    r"(?:\b(?:assign|define|specify|set|use)\s+(?:a\s+|the\s+)?"
    r"(?:[A-Za-z0-9_-]+\s+){0,3}material\b|"
    r"\bmaterial\s+(?:named\s+)?[A-Za-z][A-Za-z0-9_-]*\b|"
    r"\b(?:steel|alumin(?:um|ium)|titanium|copper)\s+material\b)",
    re.IGNORECASE,
)

_YOUNGS_MODULUS_PATTERN = re.compile(
    r"(?:\byoung(?:'s|s)?\s+modulus\b|(?<!\w)[Ee])\s*(?:=|:|of)?\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*"
    r"([A-Za-z][A-Za-z0-9/^³_-]*)\b",
    re.IGNORECASE,
)
_POISSON_RATIO_PATTERN = re.compile(
    r"(?:\bpoisson(?:'s|s)?\s+ratio\b|\bnu\b|ν)\s*(?:=|:|of)?\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\b",
    re.IGNORECASE,
)
_DENSITY_PATTERN = re.compile(
    r"\bdensity\s*(?:=|:|of)?\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*"
    r"([A-Za-z][A-Za-z0-9/^³_-]*)\b",
    re.IGNORECASE,
)
_MATERIAL_NAME_PATTERN = re.compile(
    r"\b(?:material\s+(?:named\s+)?|assign\s+)([A-Za-z][A-Za-z0-9_-]*)\b",
    re.IGNORECASE,
)

_UNSUPPORTED_CAPABILITY_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(
        r"(?:\bnon[- ]?linear\s+(?:analysis|simulation|solve|behavior|behaviour)\b|"
        r"\b(?:run|perform|use)\s+(?:a\s+)?non[- ]?linear\b)",
        re.I,
    ), "analysis.nonlinear_unsupported",
     "Nonlinear analysis is outside the supported linear-static envelope."),
    (re.compile(
        r"\b(?:thermal|heat[- ]transfer)\s+(?:analysis|simulation|solve)\b|"
        r"\b(?:run|perform)\s+(?:a\s+)?thermal\b",
        re.I,
    ), "analysis.thermal_unsupported",
     "Thermal analysis is not supported."),
    (re.compile(
        r"(?:\b(?:add|create|define|include|model|use)\s+(?:an?\s+)?"
        r"(?:(?:frictional|frictionless|bonded|surface[- ]to[- ]surface)\s+)?"
        r"contact\b|"
        r"\bcontact\s+(?:definition|interaction|pair|between|across)\b)",
        re.I,
    ), "interaction.contact_unsupported",
     "Contact is not supported for the single-solid preview."),
    (re.compile(
        r"(?:\b(?:dynamic|modal|transient|frequency[- ]response)\s+"
        r"(?:analysis|simulation|solve|study)\b|"
        r"\b(?:run|perform)\s+(?:a\s+)?(?:dynamic|modal|transient)\b)",
        re.I,
    ),
     "analysis.dynamics_unsupported", "Dynamic analysis is not supported."),
    (re.compile(
        r"(?:\b(?:plastic|plasticity|orthoplastic)\s+"
        r"(?:material|behavior|behaviour|model)\b|\byield stress\b)",
        re.I,
    ),
     "material.plastic_unsupported", "Plastic material behavior is not supported."),
    (re.compile(
        r"\b(?:orthotropic|anisotropic)\s+(?:material|behavior|behaviour|model)\b",
        re.I,
    ),
     "material.orthotropic_unsupported", "Orthotropic material behavior is not supported."),
    (re.compile(
        r"(?:"
        r"\b(?:analy[sz]e|simulate|solve|model)\s+"
        r"(?:(?:this|the)\s+)?(?:as\s+)?(?:an?\s+)?assembl(?:y|ies)\b"
        r"(?=\s*(?:[.!?,;:]|$|with\b|under\b|using\b|together\b))|"
        r"\bassembl(?:y|ies)[- ]+(?:analysis|simulation|study|model)\b|"
        r"\b(?:analy[sz]e|simulate|solve|model)\b[^.!?]{0,64}\b"
        r"(?:"
        r"(?:multiple|two|three|four|\d+)\s+(?:solids?|bodies|parts|components)|"
        r"multi[- ](?:solid|body|part)s?|"
        r"assembly\s+(?:components?|parts?|bodies|solids)"
        r")\b|"
        r"\b(?:apply|distribute)\s+(?:a\s+)?loads?\b[^.!?]{0,64}\b"
        r"(?:"
        r"(?:multiple|two|three|four|\d+)\s+(?:solids?|bodies|parts|components)|"
        r"multi[- ](?:solid|body|part)s?|"
        r"assembly\s+(?:components?|parts?|bodies|solids)"
        r")\b|"
        r"\b(?:multiple|multi[- ](?:solid|body|part)s?)"
        r"[- ]+(?:solid[- ]+)?(?:analysis|simulation|study|model)\b"
        r")",
        re.I,
    ),
     "geometry.multiple_solids_unsupported", "Assemblies and multiple solids are not supported."),
    (re.compile(
        r"(?:\b(?:shell|shells)\s+(?:analysis|model|elements?)\b|"
        r"\b(?:analy[sz]e|model|treat)\b.{0,24}\bas\s+(?:a\s+)?shells?\b)",
        re.I,
    ), "analysis.shell_unsupported",
     "Shell analysis is not supported."),
    (re.compile(
        r"(?:\b(?:beam|beams)\s+(?:analysis|model|elements?)\b|"
        r"\b(?:analy[sz]e|model|treat)\b.{0,24}\bas\s+(?:a\s+)?beams?\b)",
        re.I,
    ), "analysis.beam_unsupported",
     "Beam analysis is not supported."),
    (re.compile(r"\brotation(?:al)?\s+(?:constraint|displacement|dof)\b|\br[xyz]\s*=", re.I),
     "constraint.rotation_unsupported", "Rotational prescribed constraints are not supported."),
    (re.compile(r"\blocal coordinate\b|\bcylindrical coordinate\b|\blocal axes?\b", re.I),
     "coordinate_system.local_unsupported", "Local and cylindrical coordinate systems are not supported."),
)


def _reject_unsupported_capability(instruction: str) -> None:
    for pattern, code, message in _UNSUPPORTED_CAPABILITY_PATTERNS:
        if pattern.search(instruction):
            raise UnsupportedCapabilityError(code, message)


def _numeric_material_proposal(
    instruction: str,
) -> MaterialProposalPayload | None:
    """Parse the deliberately narrow supported numeric material grammar."""

    has_property = _MATERIAL_PROPERTY_PATTERN.search(instruction) is not None
    has_material_request = _MATERIAL_REQUEST_PATTERN.search(instruction) is not None
    if not has_property and not has_material_request:
        return None
    youngs = _YOUNGS_MODULUS_PATTERN.search(instruction)
    poisson = _POISSON_RATIO_PATTERN.search(instruction)
    if youngs is None or poisson is None:
        explicitly_unparsed = (
            (re.search(r"\byoung(?:'s|s)?\s+modulus\b|(?<!\w)[Ee]\s*(?:=|:)", instruction, re.I) and youngs is None)
            or (re.search(r"\bpoisson(?:'s|s)?\s+ratio\b|\bnu\s*(?:=|:)|ν\s*(?:=|:)", instruction, re.I) and poisson is None)
        )
        raise UnsupportedMaterialInputError(
            (
                "material.property_parse_failed"
                if explicitly_unparsed
                else "material.properties_incomplete"
            ),
            (
                "An explicitly supplied material property could not be parsed."
                if explicitly_unparsed
                else UnsupportedMaterialInputError.safe_message
            ),
        )
    if re.search(
        r"\b(?:fix(?:ed)?|force|load|gravity|pressure|traction|"
        r"prescribed displacement|displace)\b",
        instruction,
        re.IGNORECASE,
    ):
        raise CombinedMaterialInputError()
    density_mentioned = re.search(r"\bdensity\b", instruction, re.I) is not None
    density = _DENSITY_PATTERN.search(instruction)
    if density_mentioned and density is None:
        raise UnsupportedMaterialInputError(
            "material.property_parse_failed",
            "The explicitly supplied density could not be parsed.",
        )
    name_match = _MATERIAL_NAME_PATTERN.search(instruction)
    name = "proposed_material"
    if name_match is not None:
        candidate = name_match.group(1).lower()
        if candidate not in {"is", "with", "properties", "property"}:
            name = candidate
    def canonical_unit(raw: str, kind: str) -> str:
        for supported in supported_units(kind):  # type: ignore[arg-type]
            if supported.casefold() == raw.casefold():
                return supported
        raise UnsupportedMaterialInputError(
            "quantity.unsupported_unit",
            f"The explicitly supplied {kind} unit is unsupported.",
        )

    stress_unit = canonical_unit(youngs.group(2), "stress")
    youngs_value = float(youngs.group(1))
    youngs_quantity = normalize_quantity(youngs_value, stress_unit, kind="stress")
    if not math.isfinite(youngs_quantity.value) or youngs_quantity.value <= 0:
        raise UnsupportedMaterialInputError(
            "material.youngs_modulus_invalid",
            "Young's modulus must be finite and greater than zero.",
        )
    poisson_value = float(poisson.group(1))
    if not math.isfinite(poisson_value) or not (-1.0 < poisson_value < 0.5):
        raise UnsupportedMaterialInputError(
            "material.poissons_ratio_invalid",
            "Poisson's ratio must be finite and satisfy -1 < nu < 0.5.",
        )
    density_text = None
    if density is not None:
        density_unit = canonical_unit(density.group(2), "density")
        density_quantity = normalize_quantity(
            float(density.group(1)), density_unit, kind="density"
        )
        if not math.isfinite(density_quantity.value) or density_quantity.value <= 0:
            raise UnsupportedMaterialInputError(
                "material.density_invalid",
                "Density must be finite and greater than zero.",
            )
        density_text = f"{density.group(1)} {density_unit}"
    return MaterialProposalPayload(
        name=name,
        youngs_modulus=f"{youngs.group(1)} {stress_unit}",
        poisson_ratio=poisson_value,
        density=density_text,
    )


_VAGUE_LATERAL_SIDE_PATTERN = re.compile(
    r"\b(left|right|front|back)(?:-hand)?\s+side\b",
    re.IGNORECASE,
)


def _preserve_directional_side_ambiguity(
    interpretation: Interpretation,
    instruction: str,
) -> Interpretation:
    """Prevent a vague lateral side from becoming an exact extreme-face label."""

    intents = []
    for intent in interpretation.intents:
        match = _VAGUE_LATERAL_SIDE_PATTERN.search(intent.target_description)
        if match is None and len(interpretation.intents) == 1:
            match = _VAGUE_LATERAL_SIDE_PATTERN.search(instruction)
        if match is None or len(intent.op_list) != 1:
            intents.append(intent)
            continue
        operation = intent.op_list[0]
        expected_label = f"{match.group(1).lower()}_face"
        if not isinstance(operation, LabeledOp) or operation.name.lower() != expected_label:
            intents.append(intent)
            continue
        intents.append(
            intent.model_copy(
                update={
                    "op_list": [FindFacesOp(op="find_faces", surface_type="Plane")]
                },
                deep=True,
            )
        )
    return interpretation.model_copy(update={"intents": intents}, deep=True)


def build_user_prompt(instruction: str, summary: FaceInventorySummary) -> str:
    """Construct the deterministic, inspectable user prompt."""
    prompt_payload = {
        "original_instruction": instruction,
        "face_inventory_summary": summary.model_dump(mode="json"),
        "supported_task6_query_operations": query_vocabulary(llm_safe_only=True),
        "excluded_id_referencing_task6_operations": ["adjacent_to", "in_component"],
        "supported_task7_semantics": semantics_vocabulary(),
        "model_axis_convention": {
            "vertical_displacement_component": "y",
            "downward_load_direction": "negative Y",
        },
        "supported_boundary_conditions": ["fixed_displacement", "prescribed_displacement"],
    }
    return json.dumps(prompt_payload, sort_keys=True, separators=(",", ":"))


def _extract_payload(response: Any) -> Any:
    if isinstance(response, Interpretation):
        return response.model_dump(mode="json")
    if isinstance(response, str):
        return json.loads(response)
    if isinstance(response, dict):
        return response
    parsed = getattr(response, "output_parsed", None)
    if parsed is not None:
        return _extract_payload(parsed)
    raise ValueError("model response did not contain structured output")


def _safe_retry_reason(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, json.JSONDecodeError):
        return "malformed JSON"
    if "forbidden direct entity" in text:
        return "forbidden direct entity selection"
    if "unknown inventory label" in text:
        return "unknown inventory label"
    if "union_tag_invalid" in text or "unable to extract tag" in text:
        return "unknown query operation"
    return "schema validation failed"


class Interpreter:
    """Bounded-retry text interpreter with no geometry or state side effects."""

    def __init__(
        self,
        *,
        transport: StructuredOutputTransport | None = None,
        client: Any | None = None,
        model: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_retries: int = 2,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide transport or client, not both")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.transport = transport or OpenAIStructuredOutputTransport(
            client=client,
            model=model,
            max_output_tokens=max_output_tokens,
        )
        self.max_retries = max_retries

    def interpret(
        self,
        instruction: str,
        inventory_summary: FaceInventorySummary | dict[str, Any],
    ) -> Interpretation:
        if not instruction.strip():
            raise ValueError("instruction cannot be empty")
        _reject_unsupported_capability(instruction)
        material_proposal = _numeric_material_proposal(instruction)
        if material_proposal is not None:
            # The numeric material grammar is deterministic and has no geometry
            # operand. It returns a pending proposal without calling a model or
            # allowing a name-only database lookup.
            return Interpretation(
                intents=[], material_proposal=material_proposal
            )
        summary_payload = (
            inventory_summary.model_dump(mode="json")
            if isinstance(inventory_summary, FaceInventorySummary)
            else inventory_summary
        )
        _assert_no_direct_entity_references(summary_payload, path="inventory_summary")
        summary = FaceInventorySummary.model_validate(summary_payload, strict=True)
        messages = [{"role": "user", "content": build_user_prompt(instruction, summary)}]
        last_reason = "schema validation failed"

        for attempt in range(1, self.max_retries + 2):
            request = ModelRequest(
                system=SYSTEM_PROMPT,
                messages=[dict(message) for message in messages],
                output_format=Interpretation,
            )
            try:
                payload = _extract_payload(self.transport.complete(request))
                _assert_no_direct_entity_references(payload)
                result = Interpretation.model_validate(payload, strict=True)
                _validate_labels(result, summary)
                result = _preserve_directional_side_ambiguity(result, instruction)
                result._attempts = attempt
                return result
            except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
                last_reason = _safe_retry_reason(exc)
                if attempt > self.max_retries:
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"The previous response was rejected ({last_reason}). "
                            "Return a fresh response that follows the schema and all hard rules."
                        ),
                    }
                )

        attempts = self.max_retries + 1
        raise InterpreterError(
            f"LLM interpretation failed after {attempts} attempts: {last_reason}",
            attempts=attempts,
            last_reason=last_reason,
        )


def interpret_instruction(
    instruction: str,
    inventory_summary: FaceInventorySummary | dict[str, Any],
    **kwargs: Any,
) -> Interpretation:
    """Convenience wrapper around :class:`Interpreter`."""
    return Interpreter(**kwargs).interpret(instruction, inventory_summary)
