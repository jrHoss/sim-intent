"""Shared deterministic export contracts and capability errors (Task 14).

Export adapters consume only a validated :class:`SimulationIntent` and
explicit model metadata.  They do not read session, conversation, browser, or
LLM state.  This module also owns the final revalidation/confirmation gate so
direct adapter calls cannot bypass Task 13.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field, computed_field, model_validator

from geom.meshes import MeshInventory
from ir.schema import ExportBlockedError, SimulationIntent, StrictModel
from ir.validate import ValidationIssue, ValidationReport, validate_intent


AdapterName = Literal["abaqus_py", "ccx_inp"]


class ExportResult(StrictModel):
    """A generated text artifact; it never claims a solver was executed."""

    adapter_name: AdapterName
    suggested_filename: str = Field(
        min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    media_type: str = Field(min_length=1)
    artifact_text: str = Field(min_length=1)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: list[str]

    @model_validator(mode="after")
    def _deterministic_text_contract(self) -> "ExportResult":
        if "\r" in self.artifact_text:
            raise ValueError("export artifact must use LF line endings")
        actual = hashlib.sha256(self.artifact_text.encode("utf-8")).hexdigest()
        if actual != self.checksum_sha256:
            raise ValueError("artifact checksum does not match UTF-8 text")
        if not self.suggested_filename.isascii():
            raise ValueError("suggested filename must be ASCII-safe")
        if ".." in self.suggested_filename:
            raise ValueError("suggested filename must not contain traversal tokens")
        return self

    @computed_field
    @property
    def artifact_size(self) -> int:
        return len(self.artifact_text.encode("utf-8"))

    @property
    def artifact_bytes(self) -> bytes:
        return self.artifact_text.encode("utf-8")


class ExportAdapterError(RuntimeError):
    """Base class for safe, structured adapter failures."""

    code = "artifact_generation_failed"

    def __init__(self, message: str):
        super().__init__(message)
        self.safe_message = message


class ExportNotReadyError(ExportAdapterError):
    code = "export_not_ready"

    def __init__(self, report: ValidationReport):
        self.report = report
        blockers = [issue for issue in report.issues if issue.blocks_export]
        message = "Export is blocked by server-side intent validation."
        if blockers:
            message += " Resolve every reported blocking issue."
        super().__init__(message)


class UnsupportedModelTypeError(ExportAdapterError):
    code = "unsupported_model_type"


class UnsupportedEntityTypeError(ExportAdapterError):
    code = "unsupported_entity_type"


class UnsupportedLoadTypeError(ExportAdapterError):
    code = "unsupported_load_type"


class MissingRegionMappingError(ExportAdapterError):
    code = "missing_region_mapping"


class InvalidRegionReferenceError(ExportAdapterError):
    code = "invalid_region_reference"


class MissingMeshTopologyError(ExportAdapterError):
    code = "missing_mesh_topology"


class UnsafeNameError(ExportAdapterError):
    code = "unsafe_name"


class MissingMaterialAssignmentError(ExportAdapterError):
    code = "missing_material_assignment"


class ArtifactGenerationFailedError(ExportAdapterError):
    code = "artifact_generation_failed"


@dataclass(frozen=True)
class CadModelMetadata:
    """Explicit metadata for the exact STEP source used during grounding."""

    source_path: Path
    source_name: str
    source_sha256: str
    face_ids: tuple[int, ...]
    mapping_strategy: Literal["source_step_face_order"] = "source_step_face_order"


@dataclass(frozen=True)
class ElementFaceReference:
    """Faithful CalculiX element-face reference supplied by mesh topology."""

    element_id: int
    face_label: Literal["S1", "S2", "S3", "S4", "S5", "S6"]


@dataclass(frozen=True)
class MeshModelMetadata:
    """Explicit native mesh inventory and optional boundary topology mapping.

    Task 5 retains boundary-facet nodes but not owner element/local face labels.
    ``element_face_by_facet`` therefore defaults empty; pressure export must
    fail with ``missing_mesh_topology`` unless a trusted caller supplies it.
    """

    source_path: Path
    inventory: MeshInventory
    node_ids: tuple[int, ...]
    element_ids: tuple[int, ...]
    element_face_by_facet: Mapping[int, ElementFaceReference] = field(
        default_factory=dict
    )


def ensure_export_ready(intent: SimulationIntent) -> ValidationReport:
    """Recompute Task 13 validation and invoke the unchanged IR gate."""

    report = validate_intent(intent)
    try:
        intent.export_payload()
    except ExportBlockedError:
        raise ExportNotReadyError(report) from None
    if not report.export_eligible:
        raise ExportNotReadyError(report)
    return report


def make_result(
    *,
    adapter_name: AdapterName,
    suggested_filename: str,
    media_type: str,
    artifact_text: str,
    warnings: list[str],
) -> ExportResult:
    if "\r" in artifact_text:
        raise ArtifactGenerationFailedError("Generated artifact used invalid line endings.")
    return ExportResult(
        adapter_name=adapter_name,
        suggested_filename=suggested_filename,
        media_type=media_type,
        artifact_text=artifact_text,
        checksum_sha256=hashlib.sha256(artifact_text.encode("utf-8")).hexdigest(),
        warnings=warnings,
    )


def stable_name(prefix: str, raw: str, *, limit: int = 70) -> str:
    """Return a readable, deterministic solver identifier.

    Unsafe characters are replaced rather than interpolated into generated
    syntax.  A digest is appended when sanitization changes the spelling, so
    two distinct unsafe names cannot silently collapse onto the same token.
    """

    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise UnsafeNameError("A solver name is empty or contains a NUL character.")
    normalized = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", raw)).strip("_")
    if not normalized:
        normalized = "ITEM"
    token = f"{prefix}_{normalized}".upper()
    if normalized != raw or len(token) > limit:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
        token = f"{token[: max(1, limit - 9)].rstrip('_')}_{digest}"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", token) or len(token) > limit:
        raise UnsafeNameError("A deterministic safe solver name could not be created.")
    return token


def safe_artifact_stem(source_name: str) -> str:
    """ASCII filename stem with no path components or traversal tokens."""

    if not isinstance(source_name, str) or not source_name or "\x00" in source_name:
        raise UnsafeNameError("The source model name is unsafe.")
    basename = source_name.replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    normalized = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_-]", "_", stem)).strip("_-")
    if not normalized:
        normalized = "model"
    return normalized[:80]


def format_float(value: float) -> str:
    """Stable finite decimal formatting shared by both text adapters."""

    numeric = float(value)
    if not math.isfinite(numeric):
        raise ArtifactGenerationFailedError("A non-finite numeric value reached export.")
    if numeric == 0.0:
        return "0"
    return format(numeric, ".15g")


def file_checksum(path: Path) -> str:
    """Hash an explicitly supplied model path without exposing it on failure."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        raise MissingRegionMappingError(
            "The explicitly supplied source model could not be read."
        ) from None
    return digest.hexdigest()


def blocking_issues(error: ExportNotReadyError) -> list[ValidationIssue]:
    return [issue for issue in error.report.issues if issue.blocks_export]
