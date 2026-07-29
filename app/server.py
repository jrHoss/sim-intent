"""FastAPI viewer backend (Task 8).

Uploaded models are stored by a deterministic id derived from their bytes and
safe source name. STEP inventories use :mod:`geom.inventory`; Abaqus INP
inventories use :mod:`geom.meshes`. Viewer glTF is emitted as JSON with an
embedded binary buffer and one named node per selectable face/group.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import re
import struct
import tempfile
import uuid
from contextlib import asynccontextmanager
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote

import gmsh
import meshio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.record_versions import load_fallback_record
from app.blob_store import (
    BlobStore, BlobIntegrityError, SourceStorageLimitExceededError,
)
from app.config import LocalDataConfig
from app.ingestion import IngestionService, QuarantinedUpload
from app.data_root_lock import DataRootLock
from app.migrations import upgrade_database
from app.persistence import (
    ModelVersion,
    Persistence,
    PersistenceConflictError,
    PersistenceNotFoundError,
    Project,
    SetupRequestConflictError,
    SetupRevision,
    SetupRevisionConflictError,
    SetupSourceSupersededError,
    SimulationSetup,
    create_sqlite_engine,
)
from app.problems import (
    PROBLEM_RESPONSES,
    ApiProblem,
    ProblemDetails,
    ProblemDetailsError,
    problem_response,
    validation_problem,
)
from app.runtime_mode import RuntimeMode, resolve_runtime_mode
from app.schema_compat import (
    LEGACY_INTENT_ROUTE,
    normalize_legacy_intent_payload,
)
from app.session import (
    InvalidAssumptionTransitionError,
    InvalidRegionTransitionError,
    RegionTransitionRequest,
    SelectionSessionStore,
    SessionHighlight,
    SessionAssumptionMissingError,
    SessionIntentMissingError,
    SessionRegionMissingError,
    SessionSnapshot,
)
from app.orchestration import (
    OrchestrationError,
    interpret_and_propose,
    merge_session_intents,
    propose_from_interpretation,
)
from export.abaqus_py import export_abaqus_py
from export.ccx_inp import export_ccx_inp
from export.common import (
    ArtifactCapability,
    CadModelMetadata,
    ExportAdapterError,
    ExportNotReadyError,
    MeshModelMetadata,
    UnsupportedModelTypeError,
    assess_artifact_capability,
    blocking_issues,
)
from geom.cylinders import analyze_cylinders
from geom.inventory import FaceInventory, get_inventory
from ground.engine import ClickEvidence, GroundingBatch
from geom.meshes import MeshInventory, _scan_inp_native_ids, load_mesh
from ir.schema import (
    Assumption,
    EngineeringConsistencyError,
    EntityType,
    LegacySimulationIntent,
    RegionStatus,
    SelectionMethod,
    SimulationIntent,
    StrictModel,
    ValidationStatus,
    material_proposal_fingerprint,
)
from ir.schema_version import API_CONTRACT_VERSION, SIMULATION_INTENT_SCHEMA_VERSION
from ir.validate import ValidationIssue, ValidationReport, validate_intent
from ir.versioning import SchemaVersionError, load_simulation_intent
from llm.interpreter import (
    DEFAULT_MODEL,
    Interpretation,
    Interpreter,
    InterpreterError,
    InterpreterProviderError,
    UnsupportedCapabilityError,
    UnsupportedMaterialInputError,
)

DEFAULT_MODEL_DIR = Path(".sim_intent_cache") / "models"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SUPPORTED_SUFFIXES = {".step": "step", ".stp": "step", ".inp": "inp"}
SELECTION_LOGGER = logging.getLogger("uvicorn.error")
APPLICATION_LOGGER = logging.getLogger("uvicorn.error")
CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class SelectRequest(BaseModel):
    """Frozen Task 8 click-selection request body."""

    model_config = ConfigDict(extra="forbid")
    entity_id: int = Field(gt=0)


class HighlightRequest(BaseModel):
    """Frozen highlight request plus an optional load-direction vector."""

    model_config = ConfigDict(extra="forbid")
    entity_ids: list[int] = Field(min_length=1)
    style: str
    vector: list[float] | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("entity_ids")
    @classmethod
    def entity_ids_are_positive_and_unique(cls, value: list[int]) -> list[int]:
        if any(entity_id <= 0 for entity_id in value):
            raise ValueError("entity ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("entity ids must be unique")
        return value

    @field_validator("style")
    @classmethod
    def normalize_style(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "fixed_bc": "fixed_boundary_condition",
            "load": "load_direction",
        }
        normalized = aliases.get(normalized, normalized)
        allowed = {
            "confirmed",
            "proposed",
            "candidate",
            "fixed_boundary_condition",
            "load_direction",
        }
        if normalized not in allowed:
            raise ValueError(f"style must be one of: {', '.join(sorted(allowed))}")
        return normalized

    @field_validator("vector")
    @classmethod
    def vector_is_finite_and_nonzero(
        cls, value: list[float] | None
    ) -> list[float] | None:
        if value is None:
            return value
        if not all(math.isfinite(component) for component in value):
            raise ValueError("load direction vector must be finite")
        if not any(component != 0.0 for component in value):
            raise ValueError("load direction vector must be nonzero")
        return value


class AuditRegion(StrictModel):
    """Region provenance plus all conditions that reference the region."""

    id: str
    entity_type: EntityType
    entity_ids: list[int] | list[str]
    selection_method: SelectionMethod
    confidence: float
    source_instruction: str
    status: RegionStatus
    boundary_conditions: list[dict[str, Any]]
    loads: list[dict[str, Any]]


class AuditResponse(StrictModel):
    """Backend source of truth for the Task 13 audit panel."""

    session_id: str
    model_id: str
    validation_status: ValidationStatus
    export_eligible: bool
    blocking_reasons: list[str]
    regions: list[AuditRegion]
    assumptions: list[Assumption]
    validation_report: ValidationReport


class ExportGateResponse(StrictModel):
    """Readiness only; Task 13 never claims to have generated an artifact."""

    session_id: str
    model_id: str
    status: Literal["blocked", "ready"]
    validation_status: ValidationStatus
    export_eligible: bool
    message: str
    blocking_issues: list[ValidationIssue]


class ArtifactExportRequest(StrictModel):
    """Select one Task 14 adapter; eligibility remains server-computed."""

    adapter: str = Field(min_length=1, max_length=40)


class InterpretRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=2_000)
    clicked_entity_ids: list[int] = Field(default_factory=list)

    @field_validator("clicked_entity_ids")
    @classmethod
    def _unique_clicks(cls, value: list[int]) -> list[int]:
        if any(entity_id <= 0 for entity_id in value) or len(value) != len(set(value)):
            raise ValueError("clicked entity IDs must be positive and unique")
        return value


class ClarificationChoice(StrictModel):
    intent_index: int = Field(ge=0)
    entity_ids: list[int] = Field(min_length=1)

    @field_validator("entity_ids")
    @classmethod
    def _valid_choice(cls, value: list[int]) -> list[int]:
        if any(entity_id <= 0 for entity_id in value) or len(value) != len(set(value)):
            raise ValueError("clarification entity IDs must be positive and unique")
        return value


class InterpretResponse(StrictModel):
    mode: Literal["LIVE", "REPLAY"]
    fallback: bool
    state: Literal["proposed", "clarification"]
    instruction: str
    interpretation: dict[str, Any]
    grounding: GroundingBatch
    intent: SimulationIntent | None
    clarification_count: int = Field(ge=0, le=1)
    model_name: str
    notices: list[str] = Field(default_factory=list)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project name must not be blank")
        return value


class ProjectResponse(StrictModel):
    schema_version: int = API_CONTRACT_VERSION
    id: str
    name: str
    created_at: str


class ModelVersionResponse(StrictModel):
    schema_version: int = API_CONTRACT_VERSION
    id: str
    model_id: str
    version: int
    source_sha256: str
    source_name: str
    size_bytes: int
    media_type: str
    model_kind: str
    created_at: str
    is_current: bool
    is_superseded: bool
    superseded_at: str | None
    superseded_by_version_id: str | None


class ModelUploadResponse(StrictModel):
    schema_version: int = API_CONTRACT_VERSION
    model_id: str
    model_version: ModelVersionResponse


def _require_current_intent_version(value: object) -> object:
    """Enforce the durable-write version boundary before nested defaults."""

    if not isinstance(value, dict):
        return value
    intent = value.get("intent")
    if not isinstance(intent, dict):
        return value
    if "schema_version" not in intent:
        raise EngineeringConsistencyError(
            "simulation_intent.schema_version_required",
            "durable setup writes require an explicit schema version",
        )
    declared = intent["schema_version"]
    if (
        isinstance(declared, bool)
        or not isinstance(declared, int)
        or declared < 1
    ):
        raise EngineeringConsistencyError(
            "simulation_intent.schema_version_invalid",
            "the schema version must be a positive integer",
        )
    if declared < SIMULATION_INTENT_SCHEMA_VERSION:
        raise EngineeringConsistencyError(
            "simulation_intent.schema_version_unsupported_legacy",
            "legacy schema versions are read-only and cannot be written",
        )
    if declared > SIMULATION_INTENT_SCHEMA_VERSION:
        raise EngineeringConsistencyError(
            "simulation_intent.schema_version_unsupported_future",
            "the schema version is newer than this server",
        )
    analysis = intent.get("analysis")
    if isinstance(analysis, dict):
        analysis_type = str(analysis.get("type", "")).lower()
        dimensionality = str(analysis.get("dimensionality", "")).lower()
        coordinates = str(analysis.get("coordinate_system", "")).lower()
        for token, code in (
            ("nonlinear", "analysis.nonlinear_unsupported"),
            ("thermal", "analysis.thermal_unsupported"),
            ("dynamic", "analysis.dynamics_unsupported"),
        ):
            if token in analysis_type:
                raise EngineeringConsistencyError(code, "the requested analysis mode is unsupported")
        for token, code in (
            ("shell", "analysis.shell_unsupported"),
            ("beam", "analysis.beam_unsupported"),
        ):
            if token in dimensionality:
                raise EngineeringConsistencyError(code, "the requested dimensionality is unsupported")
        if "local" in coordinates or "cylindrical" in coordinates:
            raise EngineeringConsistencyError(
                "coordinate_system.local_unsupported",
                "only the global Cartesian coordinate system is supported",
            )
    materials = intent.get("materials")
    for material in materials if isinstance(materials, list) else []:
        if not isinstance(material, dict):
            continue
        model = str(material.get("model", "")).lower()
        if "plastic" in model:
            raise EngineeringConsistencyError(
                "material.plastic_unsupported",
                "plastic material behavior is unsupported",
            )
        if "orthotropic" in model or "anisotropic" in model:
            raise EngineeringConsistencyError(
                "material.orthotropic_unsupported",
                "orthotropic material behavior is unsupported",
            )
    bcs = intent.get("bcs")
    for bc in bcs if isinstance(bcs, list) else []:
        if not isinstance(bc, dict):
            continue
        bc_type = str(bc.get("type", "")).lower()
        if "rotation" in bc_type or any(
            key in bc for key in ("rotations", "rx", "ry", "rz")
        ):
            raise EngineeringConsistencyError(
                "constraint.rotation_unsupported",
                "rotational prescribed constraints are unsupported",
            )
        if any(key in bc for key in ("coordinate_system_ref", "time_history", "amplitude")):
            code = (
                "coordinate_system.local_unsupported"
                if "coordinate_system_ref" in bc
                else "constraint.time_dependent_unsupported"
            )
            raise EngineeringConsistencyError(code, "the requested constraint mode is unsupported")
    if any(key in intent for key in ("contact", "contacts", "contact_pairs")):
        raise EngineeringConsistencyError(
            "interaction.contact_unsupported", "contact is unsupported"
        )
    if any(key in intent for key in ("assembly", "assemblies", "parts", "solids")):
        raise EngineeringConsistencyError(
            "geometry.multiple_solids_unsupported",
            "assemblies and multiple solids are unsupported",
        )
    return value


class SetupCreate(StrictModel):
    model_id: uuid.UUID
    model_version_id: uuid.UUID
    request_id: str = Field(min_length=1, max_length=200)
    intent: SimulationIntent

    _current_intent_version = model_validator(mode="before")(
        _require_current_intent_version
    )


class SetupMutation(StrictModel):
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=200)
    intent: SimulationIntent

    _current_intent_version = model_validator(mode="before")(
        _require_current_intent_version
    )


class SetupDecision(StrictModel):
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=200)


class SetupSummary(StrictModel):
    schema_version: int = API_CONTRACT_VERSION
    id: str
    project_id: str
    model_id: str
    model_version_id: str
    current_revision: int
    created_at: str
    updated_at: str
    model_version_is_current: bool
    is_stale: bool
    stale_reason: str | None
    stale_at: str | None


class SetupRevisionResponse(StrictModel):
    schema_version: int = API_CONTRACT_VERSION
    id: str
    setup_id: str
    revision: int
    parent_revision_id: str | None
    simulation_intent_schema_version: int
    intent_sha256: str
    mutation_type: str
    request_id: str
    created_at: str
    intent: SimulationIntent
    validation: ValidationReport
    selected_entities: dict[str, list[int] | list[str]]
    highlight_state: dict[str, SessionHighlight]
    engineering_ready: bool
    artifact_capability: ArtifactCapability
    export_eligible: bool


class SetupView(StrictModel):
    setup: SetupSummary
    current: SetupRevisionResponse


@dataclass
class PendingInterpretation:
    instruction: str
    interpretation: Interpretation
    click_evidence_by_intent: dict[int, ClickEvidence]
    grounding: GroundingBatch


class ViewerEventBroker:
    """Fan out transient Task 9 visual commands without session persistence."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        message = f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        for subscriber in tuple(self._subscribers):
            await subscriber.put(message)

    async def stream(self) -> AsyncIterator[str]:
        subscriber: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.add(subscriber)
        try:
            yield ": viewer-connected\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(subscriber.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers.discard(subscriber)


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    source_name: str
    kind: str
    path: Path


@dataclass(frozen=True)
class FaceMesh:
    face_id: int
    positions: list[tuple[float, float, float]]
    indices: list[int]


class ModelStore:
    """Filesystem-backed uploaded-model store with deterministic identifiers."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def add(self, source_name: str, content: bytes) -> ModelRecord:
        source_name = _safe_source_name(source_name)
        suffix = Path(source_name).suffix.lower()
        kind = SUPPORTED_SUFFIXES.get(suffix)
        if kind is None:
            raise HTTPException(
                status_code=415,
                detail="unsupported model format; expected STEP (.step/.stp) or Abaqus INP (.inp)",
            )
        if not content:
            raise HTTPException(status_code=400, detail="uploaded model is empty")

        digest = hashlib.sha256()
        digest.update(source_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        model_id = digest.hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        model_dir = self.root / model_id
        source_path = model_dir / source_name
        metadata_path = model_dir / "model.json"

        if metadata_path.is_file() and source_path.is_file():
            return ModelRecord(model_id, source_name, kind, source_path)

        model_dir.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)
        record = ModelRecord(model_id, source_name, kind, source_path)
        try:
            self.inventory(record)
        except Exception as exc:
            source_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            try:
                model_dir.rmdir()
            except OSError:
                pass
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=422, detail=f"could not parse model: {exc}") from exc

        metadata_path.write_text(
            json.dumps({"source_name": source_name, "kind": kind}, indent=2),
            encoding="utf-8",
        )
        return record

    def get(self, model_id: str) -> ModelRecord:
        if len(model_id) != 64 or any(c not in "0123456789abcdef" for c in model_id):
            raise HTTPException(status_code=404, detail="model not found")
        model_dir = self.root / model_id
        metadata_path = model_dir / "model.json"
        if not metadata_path.is_file():
            raise HTTPException(status_code=404, detail="model not found")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            source_name = _safe_source_name(metadata["source_name"])
            kind = metadata["kind"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="stored model metadata is invalid") from exc
        if kind not in {"step", "inp"}:
            raise HTTPException(status_code=500, detail="stored model kind is invalid")
        source_path = model_dir / source_name
        if not source_path.is_file():
            raise HTTPException(status_code=500, detail="stored model file is missing")
        return ModelRecord(model_id, source_name, kind, source_path)

    def inventory(self, record: ModelRecord) -> FaceInventory | MeshInventory:
        if record.kind == "step":
            inventory, _ = get_inventory(
                record.path, cache_dir=record.path.parent / "inventory-cache"
            )
            return inventory
        if record.kind == "inp":
            return load_mesh(record.path)
        raise ValueError(f"unsupported stored model kind: {record.kind}")


def create_app(
    storage_dir: str | Path = DEFAULT_MODEL_DIR,
    *,
    mode: RuntimeMode | None = None,
    data_config: LocalDataConfig | None = None,
) -> FastAPI:
    # Task 18 (ADR-005): the runtime mode is resolved exactly once per
    # constructed application. `resolved_mode` is the immutable closure value
    # that every mode consumer (route registration, /healthz, provider-error
    # capability hints) reads; `app.state.runtime_mode` is a diagnostic copy
    # only, and mutating it after construction changes nothing.
    resolved_mode = resolve_runtime_mode() if mode is None else mode
    # Task 19 (ADR-004, decision D-9): the backend OpenAPI document is the API
    # contract authority and ``API_CONTRACT_VERSION`` is its single source of
    # truth. No runtime endpoint publishes versions; the checked-in OpenAPI
    # snapshot, the generated TypeScript output, and the drift tests do.
    durable_config = data_config or (
        LocalDataConfig.from_env()
        if Path(storage_dir) == DEFAULT_MODEL_DIR
        else LocalDataConfig(Path(storage_dir).resolve().parent / "durable-data")
    )

    @asynccontextmanager
    async def durable_lifespan(application: FastAPI):
        root_lock = DataRootLock(durable_config.root)
        root_lock.acquire()
        persistence: Persistence | None = None
        try:
            # The inter-process root lock is acquired before every operation
            # that can inspect or mutate the database or blob tree. The
            # BlobStore RLock remains the narrower in-process coordination
            # boundary for publication/commit/cleanup across threads.
            durable_config.root.mkdir(parents=True, exist_ok=True)
            upgrade_database(durable_config.database_url)
            persistence = Persistence(
                create_sqlite_engine(durable_config.database_url),
                BlobStore(durable_config.blob_root),
                max_source_storage_bytes=durable_config.max_source_storage_bytes,
            )
            application.state.persistence = persistence
            ingestion = IngestionService(durable_config)
            application.state.ingestion = ingestion
            application.state.data_config = durable_config
            persistence.blobs.cleanup_temporary()
            ingestion.cleanup_stale()
            yield
        finally:
            if persistence is not None:
                persistence.dispose()
            if hasattr(application.state, "persistence"):
                del application.state.persistence
            if hasattr(application.state, "ingestion"):
                del application.state.ingestion
            root_lock.release()

    app = FastAPI(
        title="sim-intent viewer backend",
        version=str(API_CONTRACT_VERSION),
        lifespan=durable_lifespan,
    )
    app.state.runtime_mode = resolved_mode
    app.state.model_store = ModelStore(storage_dir)
    app.state.data_config = durable_config
    app.state.session_store = SelectionSessionStore()
    app.state.viewer_events = ViewerEventBroker()
    app.state.interpreter = Interpreter()
    app.state.pending_interpretations: dict[str, PendingInterpretation] = {}

    @app.middleware("http")
    async def request_correlation(request: Request, call_next):
        supplied = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = (
            supplied if CORRELATION_ID_RE.fullmatch(supplied) else str(uuid.uuid4())
        )
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    default_openapi = app.openapi

    def product_openapi() -> dict[str, Any]:
        schema = default_openapi()
        schema.setdefault("components", {}).setdefault("schemas", {})[
            "ProblemDetails"
        ] = ProblemDetails.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        return schema

    app.openapi = product_openapi

    @app.exception_handler(ProblemDetailsError)
    async def handle_problem(
        request: Request, error: ProblemDetailsError
    ) -> JSONResponse:
        return problem_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, error: RequestValidationError
    ) -> Response:
        if request.url.path.startswith("/api/v1/"):
            return validation_problem(request, error)
        return await request_validation_exception_handler(request, error)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request, error: HTTPException
    ) -> Response:
        if request.url.path.startswith("/api/v1/"):
            detail = (
                error.detail
                if isinstance(error.detail, str)
                else "The request could not be completed."
            )
            return problem_response(
                request,
                ApiProblem(
                    status=error.status_code,
                    code="api_request_invalid",
                    title="API request failed",
                    detail=detail,
                ),
            )
        return await http_exception_handler(request, error)

    def persistence() -> Persistence:
        return app.state.persistence

    def mesh_metadata(
        record: ModelRecord, inventory: MeshInventory
    ) -> MeshModelMetadata:
        node_ids, element_blocks = _scan_inp_native_ids(record.path)
        return MeshModelMetadata(
            source_path=record.path,
            inventory=inventory,
            node_ids=tuple(node_ids),
            element_ids=tuple(
                element_id
                for block in element_blocks
                for element_id in block
            ),
        )

    def model_capability(
        intent: SimulationIntent,
        record: ModelRecord,
        *,
        source_is_stale: bool = False,
    ) -> ArtifactCapability:
        metadata = None
        if record.kind == "inp":
            inventory = app.state.model_store.inventory(record)
            if isinstance(inventory, MeshInventory):
                metadata = mesh_metadata(record, inventory)
        return assess_artifact_capability(
            intent,
            model_kind=record.kind,
            model=metadata,
            source_is_stale=source_is_stale,
        )

    def capability_issues(
        capability: ArtifactCapability,
    ) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                code=code,
                severity="error",
                message="The selected artifact target cannot generate this setup.",
                blocks_export=True,
                object_type="artifact",
                field="solver_target",
            )
            for code in capability.blocking_issue_codes
        ]

    def project_report(
        report: ValidationReport,
        capability: ArtifactCapability,
    ) -> ValidationReport:
        combined_issues = [
            *report.issues,
            *capability_issues(capability),
        ]
        combined_issues.sort(
            key=lambda issue: (
                0 if issue.severity == "error" else 1,
                issue.code,
                issue.object_type or "",
                issue.object_id or "",
                issue.field or "",
                issue.message,
            )
        )
        return report.model_copy(
            update={
                "export_eligible": (
                    report.engineering_ready and capability.supported
                ),
                "issues": combined_issues,
            },
            deep=True,
        )

    def legacy_snapshot(
        session_id: str, snapshot: SessionSnapshot
    ) -> SessionSnapshot:
        if snapshot.intent is None:
            return snapshot.model_copy(update={"export_eligible": False}, deep=True)
        record = app.state.model_store.get(session_id)
        report = validate_intent(snapshot.intent)
        capability = model_capability(snapshot.intent, record)
        return snapshot.model_copy(
            update={
                "intent": snapshot.intent.model_copy(
                    update={"validation_status": report.validation_status},
                    deep=True,
                ),
                "export_eligible": (
                    report.engineering_ready and capability.supported
                ),
            },
            deep=True,
        )

    @app.get("/", include_in_schema=False)
    async def viewer_frontend() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # Safe infrastructure identity only: no secrets, paths, or env dumps.
        return {"status": "ok", "mode": resolved_mode.value}

    @app.post("/models", status_code=201)
    async def upload_model(
        request: Request, filename: str | None = Query(default=None)
    ) -> dict[str, str]:
        source_name, content = await _uploaded_file(request, filename)
        record = app.state.model_store.add(source_name, content)
        return {
            "id": record.model_id,
            "source_name": record.source_name,
            "kind": record.kind,
        }

    @app.post(
        "/api/v1/projects",
        status_code=201,
        response_model=ProjectResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def create_project(payload: ProjectCreate) -> ProjectResponse:
        return _project_response(persistence().create_project(payload.name))

    @app.get(
        "/api/v1/projects",
        response_model=list[ProjectResponse],
        responses=PROBLEM_RESPONSES,
    )
    async def list_projects() -> list[ProjectResponse]:
        return [_project_response(project) for project in persistence().list_projects()]

    @app.get(
        "/api/v1/projects/{project_id}",
        response_model=ProjectResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def read_project(project_id: uuid.UUID) -> ProjectResponse:
        project = persistence().get_project(str(project_id))
        if project is None:
            raise ApiProblem(
                status=404,
                code="project_not_found",
                title="Project not found",
                detail="The requested project does not exist.",
            )
        return _project_response(project)

    @app.post(
        "/api/v1/projects/{project_id}/models",
        status_code=201,
        response_model=ModelUploadResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def upload_project_model(
        project_id: uuid.UUID,
        request: Request,
        filename: str | None = Query(default=None),
    ) -> ModelUploadResponse:
        upload = await app.state.ingestion.receive(request, filename)
        try:
            await app.state.ingestion.parse(
                upload, request.state.correlation_id
            )
            model, version = persistence().create_model_version_from_file(
                project_id=str(project_id),
                source_name=upload.source_name,
                source_path=upload.path,
                source_sha256=upload.sha256,
                size_bytes=upload.size,
                model_kind=upload.kind,
            )
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc
        except SourceStorageLimitExceededError as exc:
            raise source_storage_problem() from exc
        finally:
            upload.path.unlink(missing_ok=True)
        return ModelUploadResponse(
            model_id=model.id,
            model_version=_version_response(version, model.current_version_id),
        )

    @app.post(
        "/api/v1/projects/{project_id}/models/{model_id}/versions",
        status_code=201,
        response_model=ModelUploadResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def upload_model_version(
        project_id: uuid.UUID,
        model_id: uuid.UUID,
        request: Request,
        filename: str | None = Query(default=None),
    ) -> ModelUploadResponse:
        upload = await app.state.ingestion.receive(request, filename)
        try:
            await app.state.ingestion.parse(
                upload, request.state.correlation_id
            )
            model, version = persistence().create_model_version_from_file(
                project_id=str(project_id),
                model_id=str(model_id),
                source_name=upload.source_name,
                source_path=upload.path,
                source_sha256=upload.sha256,
                size_bytes=upload.size,
                model_kind=upload.kind,
            )
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc
        except SourceStorageLimitExceededError as exc:
            raise source_storage_problem() from exc
        except PersistenceConflictError as exc:
            raise ApiProblem(
                status=409,
                code="model_project_conflict",
                title="Model conflict",
                detail="The model does not belong to the requested project.",
            ) from exc
        finally:
            upload.path.unlink(missing_ok=True)
        return ModelUploadResponse(
            model_id=model.id,
            model_version=_version_response(version, model.current_version_id),
        )

    @app.get(
        "/api/v1/models/{model_id}/versions",
        response_model=list[ModelVersionResponse],
        responses=PROBLEM_RESPONSES,
    )
    async def list_model_versions(model_id: uuid.UUID) -> list[ModelVersionResponse]:
        model_id_string = str(model_id)
        model = persistence().get_model(model_id_string)
        if model is None:
            raise _not_found_problem("model")
        return [
            _version_response(version, model.current_version_id)
            for version in persistence().list_versions(model_id_string)
        ]

    @app.get(
        "/api/v1/model-versions/{version_id}",
        response_model=ModelVersionResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def read_model_version(version_id: uuid.UUID) -> ModelVersionResponse:
        version = persistence().get_version(str(version_id))
        if version is None:
            raise _not_found_problem("model version")
        model = persistence().get_model(version.model_id)
        if model is None:
            raise _not_found_problem("model")
        return _version_response(version, model.current_version_id)

    def setup_summary(setup: SimulationSetup) -> SetupSummary:
        if setup.current_revision is None:
            raise RuntimeError("setup creation is incomplete")
        version = persistence().get_version(setup.model_version_id)
        if version is None:
            raise RuntimeError("setup model version is missing")
        model = persistence().get_model(setup.model_id)
        if model is None:
            raise RuntimeError("setup model is missing")
        return SetupSummary(
            id=setup.id, project_id=setup.project_id, model_id=setup.model_id,
            model_version_id=setup.model_version_id,
            current_revision=setup.current_revision,
            created_at=_utc_isoformat(setup.created_at), updated_at=_utc_isoformat(setup.updated_at),
            model_version_is_current=(
                setup.model_version_id == model.current_version_id
            ),
            is_stale=setup.is_stale, stale_reason=setup.stale_reason,
            stale_at=None if setup.stale_at is None else _utc_isoformat(setup.stale_at),
        )

    def revision_response(revision: SetupRevision) -> SetupRevisionResponse:
        intent = persistence().revision_intent(revision)
        setup = persistence().get_setup(revision.setup_id)
        if setup is None:
            raise RuntimeError("setup is missing")
        version = persistence().get_version(setup.model_version_id)
        if version is None:
            raise RuntimeError("setup model version is missing")
        engineering_report = validate_intent(
            intent, source_is_stale=setup.is_stale
        )
        try:
            content = persistence().read_version_bytes(version)
        except (BlobIntegrityError, OSError):
            # Historical/inconsistent metadata projections remain readable.
            # Without verified source bytes no native mapping can be claimed.
            capability = assess_artifact_capability(
                intent,
                model_kind=version.model_kind,
                model=None,
                source_is_stale=setup.is_stale,
            )
        else:
            with _materialized_model(
                version.source_name, version.model_kind, content
            ) as record:
                capability = model_capability(
                    intent, record, source_is_stale=setup.is_stale
                )
        report = project_report(engineering_report, capability)
        intent = intent.model_copy(update={"validation_status": report.validation_status}, deep=True)
        selected = {
            region.id: list(region.entity_ids) for region in intent.regions
            if region.status != "rejected"
        }
        highlights = {
            region.id: SessionHighlight(
                entity_ids=list(region.entity_ids), style=region.status
            )
            for region in intent.regions if region.status in {"proposed", "confirmed"}
        }
        return SetupRevisionResponse(
            id=revision.id, setup_id=revision.setup_id, revision=revision.revision,
            parent_revision_id=revision.parent_revision_id,
            simulation_intent_schema_version=revision.schema_version,
            intent_sha256=revision.intent_sha256,
            mutation_type=revision.mutation_type, request_id=revision.request_id,
            created_at=_utc_isoformat(revision.created_at), intent=intent,
            validation=report, selected_entities=selected,
            highlight_state=highlights,
            engineering_ready=engineering_report.engineering_ready,
            artifact_capability=capability,
            # Compatibility field now reflects the selected target rather than
            # overstating generic engineering readiness.
            export_eligible=report.export_eligible,
        )

    def setup_conflict(exc: PersistenceConflictError) -> ApiProblem:
        if isinstance(exc, SetupSourceSupersededError):
            code, detail = "setup_source_superseded", "The setup source version has been superseded."
        elif isinstance(exc, SetupRevisionConflictError):
            code, detail = "setup_revision_conflict", "The expected revision is stale."
        elif isinstance(exc, SetupRequestConflictError):
            code, detail = "setup_request_id_conflict", "The request ID was already used for a different mutation."
        else:
            code, detail = "setup_lineage_conflict", "The model lineage does not belong to the requested project."
        return ApiProblem(status=409, code=code, title="Setup conflict", detail=detail)

    @app.post(
        "/api/v1/projects/{project_id}/setups", status_code=201,
        response_model=SetupView, responses=PROBLEM_RESPONSES,
    )
    async def create_setup(project_id: uuid.UUID, payload: SetupCreate) -> SetupView:
        try:
            SelectionSessionStore._validate_client_statuses(None, payload.intent)
            report = validate_intent(payload.intent)
            intent = payload.intent.model_copy(
                update={"validation_status": report.validation_status}, deep=True
            )
            setup, revision = persistence().create_setup(
                project_id=str(project_id), model_id=str(payload.model_id),
                model_version_id=str(payload.model_version_id), intent=intent,
                request_id=payload.request_id,
            )
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc
        except PersistenceConflictError as exc:
            raise setup_conflict(exc) from exc
        except (InvalidRegionTransitionError, InvalidAssumptionTransitionError) as exc:
            raise ApiProblem(
                status=409,
                code="setup_transition_invalid",
                title="Invalid setup transition",
                detail=str(exc),
            ) from exc
        return SetupView(setup=setup_summary(setup), current=revision_response(revision))

    @app.get(
        "/api/v1/projects/{project_id}/setups",
        response_model=list[SetupSummary], responses=PROBLEM_RESPONSES,
    )
    async def list_project_setups(project_id: uuid.UUID) -> list[SetupSummary]:
        try:
            return [setup_summary(item) for item in persistence().list_setups(str(project_id))]
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc

    @app.get(
        "/api/v1/setups/{setup_id}", response_model=SetupView,
        responses=PROBLEM_RESPONSES,
    )
    async def read_setup(setup_id: uuid.UUID) -> SetupView:
        try:
            setup, revision = persistence().current_setup_revision(str(setup_id))
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc
        return SetupView(setup=setup_summary(setup), current=revision_response(revision))

    @app.get(
        "/api/v1/setups/{setup_id}/revisions",
        response_model=list[SetupRevisionResponse], responses=PROBLEM_RESPONSES,
    )
    async def list_revisions(setup_id: uuid.UUID) -> list[SetupRevisionResponse]:
        try:
            return [revision_response(item) for item in persistence().list_setup_revisions(str(setup_id))]
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc

    @app.get(
        "/api/v1/setups/{setup_id}/revisions/{revision_number}",
        response_model=SetupRevisionResponse, responses=PROBLEM_RESPONSES,
    )
    async def read_revision(setup_id: uuid.UUID, revision_number: int) -> SetupRevisionResponse:
        revision = persistence().get_revision(str(setup_id), revision_number)
        if revision is None:
            raise _not_found_problem("setup revision")
        return revision_response(revision)

    def mutate(setup_id: str, payload: SetupMutation, mutation_type: str) -> SetupRevisionResponse:
        try:
            current = persistence().get_revision(setup_id, payload.expected_revision)
            if current is None:
                # Preserve setup-not-found versus stale-revision problem codes.
                persistence().current_setup_revision(setup_id)
                raise SetupRevisionConflictError("stale setup revision")
            existing = persistence().revision_intent(current)
            SelectionSessionStore._validate_client_statuses(existing, payload.intent)
            report = validate_intent(payload.intent)
            intent = payload.intent.model_copy(update={"validation_status": report.validation_status}, deep=True)
            revision = persistence().mutate_setup(
                setup_id=setup_id, expected_revision=payload.expected_revision,
                request_id=payload.request_id, mutation_type=mutation_type, intent=intent,
            )
            return revision_response(revision)
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc
        except PersistenceConflictError as exc:
            raise setup_conflict(exc) from exc
        except (InvalidRegionTransitionError, InvalidAssumptionTransitionError) as exc:
            raise ApiProblem(status=409, code="setup_transition_invalid",
                             title="Invalid setup transition", detail=str(exc)) from exc

    @app.post(
        "/api/v1/setups/{setup_id}/revisions", status_code=201,
        response_model=SetupRevisionResponse, responses=PROBLEM_RESPONSES,
    )
    async def update_setup(setup_id: uuid.UUID, payload: SetupMutation) -> SetupRevisionResponse:
        return mutate(str(setup_id), payload, "intent_updated")

    def decide(setup_id: str, payload: SetupDecision, object_id: str,
               *, kind: Literal["region", "assumption"], target: str) -> SetupRevisionResponse:
        try:
            mutation_type = f"{kind}_{target}"
            mutation_payload = {
                "subject_type": kind,
                "subject_id": object_id,
                "action": target,
            }
            replay = persistence().replay_setup_mutation(
                setup_id=setup_id,
                expected_revision=payload.expected_revision,
                request_id=payload.request_id,
                mutation_type=mutation_type,
                mutation_payload=mutation_payload,
            )
            if replay is not None:
                return revision_response(replay)
            setup, current = persistence().current_setup_revision(setup_id)
            if setup.current_revision != payload.expected_revision:
                raise SetupRevisionConflictError("stale setup revision")
            intent = persistence().revision_intent(current)
            if kind == "region":
                items = intent.regions
                expected = "proposed"
            else:
                items = intent.assumptions
                expected = "pending"
            item = next((value for value in items if value.id == object_id), None)
            if item is None:
                raise ApiProblem(status=404, code=f"setup_{kind}_not_found",
                                 title=f"{kind.title()} not found",
                                 detail=f"The requested {kind} does not exist.")
            if item.status != expected:
                raise ApiProblem(status=409, code="setup_transition_invalid",
                                 title="Invalid setup transition",
                                 detail=f"Only {expected} {kind}s may be changed.")
            decision_update: dict[str, Any] = {"status": target}
            if kind == "assumption" and target == "accepted":
                linked_materials = [
                    material
                    for material in intent.materials
                    if material.authority == "system_proposed"
                    and material.proposal_assumption_ref == object_id
                ]
                if len(linked_materials) > 1:
                    raise ApiProblem(
                        status=409,
                        code="material_proposal_decision_ambiguous",
                        title="Invalid material proposal decision",
                        detail="A proposal decision must identify exactly one material.",
                    )
                if linked_materials:
                    decision_update["material_proposal_fingerprint_sha256"] = (
                        material_proposal_fingerprint(linked_materials[0])
                    )
            changed = [
                value.model_copy(update=decision_update)
                if value.id == object_id
                else value.model_copy(deep=True)
                for value in items
            ]
            body = intent.model_dump(mode="python")
            body["regions" if kind == "region" else "assumptions"] = [
                value.model_dump(mode="python") for value in changed
            ]
            updated = SimulationIntent.model_validate(body)
            report = validate_intent(updated)
            updated = updated.model_copy(update={"validation_status": report.validation_status}, deep=True)
            revision = persistence().mutate_setup(
                setup_id=setup_id, expected_revision=payload.expected_revision,
                request_id=payload.request_id, mutation_type=mutation_type,
                intent=updated, mutation_payload=mutation_payload,
            )
            return revision_response(revision)
        except PersistenceNotFoundError as exc:
            raise _not_found_problem(exc.resource) from exc
        except PersistenceConflictError as exc:
            raise setup_conflict(exc) from exc

    @app.post("/api/v1/setups/{setup_id}/regions/{region_id}/confirm", status_code=201,
              response_model=SetupRevisionResponse, responses=PROBLEM_RESPONSES)
    async def confirm_setup_region(setup_id: uuid.UUID, region_id: str, payload: SetupDecision) -> SetupRevisionResponse:
        return decide(str(setup_id), payload, region_id, kind="region", target="confirmed")

    @app.post("/api/v1/setups/{setup_id}/regions/{region_id}/reject", status_code=201,
              response_model=SetupRevisionResponse, responses=PROBLEM_RESPONSES)
    async def reject_setup_region(setup_id: uuid.UUID, region_id: str, payload: SetupDecision) -> SetupRevisionResponse:
        return decide(str(setup_id), payload, region_id, kind="region", target="rejected")

    @app.post("/api/v1/setups/{setup_id}/assumptions/{assumption_id}/accept", status_code=201,
              response_model=SetupRevisionResponse, responses=PROBLEM_RESPONSES)
    async def accept_setup_assumption(setup_id: uuid.UUID, assumption_id: str, payload: SetupDecision) -> SetupRevisionResponse:
        return decide(str(setup_id), payload, assumption_id, kind="assumption", target="accepted")

    @app.post("/api/v1/setups/{setup_id}/assumptions/{assumption_id}/reject", status_code=201,
              response_model=SetupRevisionResponse, responses=PROBLEM_RESPONSES)
    async def reject_setup_assumption(setup_id: uuid.UUID, assumption_id: str, payload: SetupDecision) -> SetupRevisionResponse:
        return decide(str(setup_id), payload, assumption_id, kind="assumption", target="rejected")

    async def durable_record(version_id: uuid.UUID) -> tuple[ModelVersion, bytes]:
        version = persistence().get_version(str(version_id))
        if version is None:
            raise _not_found_problem("model version")
        try:
            return version, persistence().read_version_bytes(version)
        except (BlobIntegrityError, OSError) as exc:
            raise ApiProblem(
                status=500,
                code="source_blob_integrity_failed",
                title="Stored model unavailable",
                detail="The stored source model is missing or failed verification.",
            ) from exc

    @app.get(
        "/api/v1/model-versions/{version_id}/inventory",
        responses=PROBLEM_RESPONSES,
    )
    async def durable_inventory(
        version_id: uuid.UUID, request: Request
    ) -> JSONResponse:
        version, content = await durable_record(version_id)
        with _materialized_model(version.source_name, version.model_kind, content) as record:
            upload = QuarantinedUpload(
                record.path, version.source_name, version.model_kind,
                version.size_bytes, version.source_sha256,
            )
            return JSONResponse(
                await app.state.ingestion.parse(
                    upload, request.state.correlation_id
                )
            )

    @app.get(
        "/api/v1/model-versions/{version_id}/gltf",
        responses=PROBLEM_RESPONSES,
    )
    async def durable_gltf(
        version_id: uuid.UUID, request: Request
    ) -> JSONResponse:
        version, content = await durable_record(version_id)
        with _materialized_model(version.source_name, version.model_kind, content) as record:
            upload = QuarantinedUpload(
                record.path, version.source_name, version.model_kind,
                version.size_bytes, version.source_sha256,
            )
            inventory_data = await app.state.ingestion.parse(
                upload, request.state.correlation_id
            )
            inventory = (
                FaceInventory.from_dict(inventory_data)
                if record.kind == "step"
                else MeshInventory.from_dict(inventory_data)
            )
            meshes = _tessellate_step(record.path) if record.kind == "step" else _tessellate_inp(record.path, inventory)
            response = JSONResponse(_build_gltf(meshes), media_type="model/gltf+json")
            response.headers["Content-Disposition"] = _content_disposition(
                f"{Path(version.source_name).stem}.gltf"
            )
            return response

    @app.post(
        "/api/v1/model-versions/{version_id}/interpret",
        response_model=InterpretResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def interpret_durable_model_version(
        version_id: uuid.UUID,
        payload: InterpretRequest,
        request: Request,
    ) -> InterpretResponse:
        """Return a grounded proposal without creating volatile session state.

        The proposal is intentionally read-only.  A browser that accepts it
        persists it through ``POST /api/v1/projects/{project_id}/setups``, so
        the durable setup aggregate remains the sole owner of engineering
        state and no ``SelectionSessionStore`` record is created or updated.
        """

        version, content = await durable_record(version_id)
        if version.model_kind != "step":
            raise ApiProblem(
                status=422,
                code="interpretation.step_required",
                title="Interpretation unavailable",
                detail=(
                    "Natural-language geometry interpretation currently "
                    "requires a STEP model. Use the engineering editor for "
                    "native INP regions."
                ),
            )
        with _materialized_model(
            version.source_name, version.model_kind, content
        ) as record:
            upload = QuarantinedUpload(
                record.path,
                version.source_name,
                version.model_kind,
                version.size_bytes,
                version.source_sha256,
            )
            inventory = FaceInventory.from_dict(
                await app.state.ingestion.parse(
                    upload, request.state.correlation_id
                )
            )
            clicks = (
                {
                    0: ClickEvidence.for_inventory(
                        inventory, payload.clicked_entity_ids
                    )
                }
                if payload.clicked_entity_ids
                else {}
            )
            try:
                proposal = interpret_and_propose(
                    instruction=payload.instruction,
                    inventory=inventory,
                    cylinders=analyze_cylinders(record.path),
                    interpreter=app.state.interpreter,
                    click_evidence_by_intent=clicks,
                )
            except UnsupportedMaterialInputError as exc:
                raise ApiProblem(
                    status=422,
                    code=exc.code,
                    title="Unsupported material request",
                    detail=exc.safe_message,
                    supported_mechanism=(
                        "explicit_numeric_isotropic_properties"
                    ),
                ) from exc
            except UnsupportedCapabilityError as exc:
                raise ApiProblem(
                    status=422,
                    code=exc.code,
                    title="Unsupported capability",
                    detail=exc.safe_message,
                ) from exc
            except InterpreterProviderError as exc:
                raise ApiProblem(
                    status=503,
                    code=exc.code,
                    title="Interpretation provider unavailable",
                    detail=exc.safe_message,
                    retryable=True,
                ) from exc
            except InterpreterError as exc:
                raise ApiProblem(
                    status=422,
                    code="llm_parse",
                    title="Instruction could not be interpreted",
                    detail=(
                        "The instruction did not produce a supported typed "
                        "engineering proposal."
                    ),
                    attempts=exc.attempts,
                ) from exc
            except (OrchestrationError, ValueError) as exc:
                raise ApiProblem(
                    status=422,
                    code="interpretation.invalid",
                    title="Instruction could not be grounded",
                    detail=(
                        "The instruction could not be grounded to the selected "
                        "model within the supported engineering envelope."
                    ),
                ) from exc
        return InterpretResponse(
            mode="LIVE",
            fallback=False,
            state=(
                "clarification" if proposal.clarifications else "proposed"
            ),
            instruction=payload.instruction,
            interpretation=proposal.interpretation.model_dump(mode="json"),
            grounding=proposal.grounding,
            intent=proposal.intent,
            clarification_count=0,
            model_name=getattr(
                app.state.interpreter.transport, "model", DEFAULT_MODEL
            ),
        )

    async def inventory_response(model_id: str) -> JSONResponse:
        record = app.state.model_store.get(model_id)
        inventory = app.state.model_store.inventory(record)
        return JSONResponse(inventory.to_dict())

    async def gltf_response(model_id: str) -> JSONResponse:
        record = app.state.model_store.get(model_id)
        inventory = app.state.model_store.inventory(record)
        if record.kind == "step":
            face_meshes = _tessellate_step(record.path)
        else:
            face_meshes = _tessellate_inp(record.path, inventory)
        response = JSONResponse(_build_gltf(face_meshes), media_type="model/gltf+json")
        response.headers["Content-Disposition"] = _content_disposition(
            f"{Path(record.source_name).stem}.gltf"
        )
        return response

    @app.post("/select")
    async def select_entity(selection: SelectRequest) -> dict[str, int | str]:
        node_name = f"face_{selection.entity_id}"
        SELECTION_LOGGER.info("Viewer selection recorded: %s", node_name)
        return {"entity_id": selection.entity_id, "node_name": node_name}

    @app.post("/highlight")
    async def highlight_entities(highlight: HighlightRequest) -> dict[str, Any]:
        payload = highlight.model_dump(exclude_none=True)
        await app.state.viewer_events.publish("highlight", payload)
        return payload

    @app.get("/events", include_in_schema=False)
    async def viewer_events() -> StreamingResponse:
        return StreamingResponse(
            app.state.viewer_events.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def step_geometry(model_id: str) -> tuple[ModelRecord, FaceInventory, dict]:
        record = app.state.model_store.get(model_id)
        if record.kind != "step":
            raise HTTPException(
                status_code=422,
                detail="Natural-language Task 15 grounding currently requires a STEP model.",
            )
        inventory = app.state.model_store.inventory(record)
        assert isinstance(inventory, FaceInventory)
        return record, inventory, analyze_cylinders(record.path)

    def save_proposal(
        model_id: str,
        proposal: SimulationIntent,
        *,
        source_instruction: str,
    ) -> tuple[SimulationIntent, list[str]]:
        current = app.state.session_store.get_or_create(model_id).intent
        merge_result = merge_session_intents(
            current,
            proposal,
            source_instruction=source_instruction,
        )
        snapshot = app.state.session_store.save_intent(model_id, merge_result.intent)
        assert snapshot.intent is not None
        return snapshot.intent, list(merge_result.duplicate_notices)

    async def publish_grounding(grounding: GroundingBatch) -> None:
        for result in grounding.results:
            if result.clarification is not None:
                for candidate in result.clarification.candidate_sets:
                    await app.state.viewer_events.publish(
                        "highlight", {"entity_ids": candidate.entity_ids, "style": "candidate"}
                    )
                continue
            if result.region is None:
                continue
            await app.state.viewer_events.publish(
                "highlight", {"entity_ids": result.region.entity_ids, "style": "proposed"}
            )
            if result.bc is not None:
                await app.state.viewer_events.publish(
                    "highlight",
                    {"entity_ids": result.region.entity_ids, "style": "fixed_boundary_condition"},
                )

    @app.post("/session/{session_id}/interpret", response_model=InterpretResponse)
    async def interpret_session_instruction(
        session_id: str, request: InterpretRequest
    ) -> InterpretResponse:
        _, inventory, cylinders = step_geometry(session_id)
        clicks = (
            {0: ClickEvidence.for_inventory(inventory, request.clicked_entity_ids)}
            if request.clicked_entity_ids
            else {}
        )
        try:
            proposal = interpret_and_propose(
                instruction=request.instruction,
                inventory=inventory,
                cylinders=cylinders,
                interpreter=app.state.interpreter,
                click_evidence_by_intent=clicks,
            )
        except InterpreterError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "llm_parse", "message": str(exc), "attempts": exc.attempts},
            ) from exc
        except UnsupportedMaterialInputError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": exc.code,
                    "message": exc.safe_message,
                    "mode": "LIVE",
                    "supported_mechanism": "explicit_numeric_isotropic_properties",
                },
            ) from exc
        except UnsupportedCapabilityError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": exc.code,
                    "message": exc.safe_message,
                    "mode": "LIVE",
                },
            ) from exc
        except InterpreterProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": exc.code,
                    "message": exc.safe_message,
                    "mode": "LIVE",
                    # Honest capability hint: fallback routes exist only in
                    # replay/test modes (Task 18); provider failure never
                    # substitutes REPLAY output in any mode.
                    "fallback_available": resolved_mode.registers_fallback_routes,
                },
            ) from exc
        except (OrchestrationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await publish_grounding(proposal.grounding)
        if proposal.clarifications:
            app.state.pending_interpretations[session_id] = PendingInterpretation(
                instruction=request.instruction,
                interpretation=proposal.interpretation,
                click_evidence_by_intent=clicks,
                grounding=proposal.grounding,
            )
            return InterpretResponse(
                mode="LIVE", fallback=False, state="clarification",
                instruction=request.instruction,
                interpretation=proposal.interpretation.model_dump(mode="json"),
                grounding=proposal.grounding, intent=None, clarification_count=1,
                model_name=getattr(app.state.interpreter.transport, "model", DEFAULT_MODEL),
            )
        assert proposal.intent is not None
        saved, notices = save_proposal(
            session_id,
            proposal.intent,
            source_instruction=request.instruction,
        )
        app.state.pending_interpretations.pop(session_id, None)
        return InterpretResponse(
            mode="LIVE", fallback=False, state="proposed", instruction=request.instruction,
            interpretation=proposal.interpretation.model_dump(mode="json"),
            grounding=proposal.grounding, intent=saved, clarification_count=0,
            model_name=getattr(app.state.interpreter.transport, "model", DEFAULT_MODEL),
            notices=notices,
        )

    @app.post("/session/{session_id}/clarify", response_model=InterpretResponse)
    async def clarify_session_instruction(
        session_id: str, choice: ClarificationChoice
    ) -> InterpretResponse:
        _, inventory, cylinders = step_geometry(session_id)
        pending = app.state.pending_interpretations.pop(session_id, None)
        if pending is None:
            raise HTTPException(status_code=409, detail="no unresolved clarification exists")
        alternatives = [
            candidate.entity_ids
            for result in pending.grounding.results
            if result.clarification is not None and result.intent_index == choice.intent_index
            for candidate in result.clarification.candidate_sets
        ]
        if sorted(choice.entity_ids) not in [sorted(ids) for ids in alternatives]:
            raise HTTPException(status_code=422, detail="choice must match a returned candidate set")
        clicks = dict(pending.click_evidence_by_intent)
        clicks[choice.intent_index] = ClickEvidence.for_inventory(inventory, choice.entity_ids)
        proposal = propose_from_interpretation(
            instruction=pending.instruction,
            interpretation=pending.interpretation,
            inventory=inventory,
            cylinders=cylinders,
            click_evidence_by_intent=clicks,
        )
        if proposal.clarifications:
            raise HTTPException(
                status_code=409,
                detail="the single allowed clarification did not resolve the intent",
            )
        assert proposal.intent is not None
        saved, notices = save_proposal(
            session_id,
            proposal.intent,
            source_instruction=pending.instruction,
        )
        await publish_grounding(proposal.grounding)
        return InterpretResponse(
            mode="LIVE", fallback=False, state="proposed", instruction=pending.instruction,
            interpretation=pending.interpretation.model_dump(mode="json"),
            grounding=proposal.grounding, intent=saved, clarification_count=1,
            model_name=getattr(app.state.interpreter.transport, "model", DEFAULT_MODEL),
            notices=notices,
        )

    if resolved_mode.registers_fallback_routes:
        # Task 18 (ADR-005): REPLAY fallback routes exist only in the replay
        # and test modes. In production and live_evaluation they are never
        # registered, so these paths return 404 and the checked-in
        # `eval/fallback/` fixtures are unreachable; the production image
        # additionally omits the eval tree entirely.

        def fallback_payload(session_id: str, case_id: str) -> tuple[dict[str, Any], SimulationIntent]:
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", case_id) is None:
                raise HTTPException(status_code=404, detail="fallback case not found")
            _, inventory, _ = step_geometry(session_id)
            path = Path(__file__).resolve().parents[1] / "eval" / "fallback" / f"{case_id}.json"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="fallback case not found")
            try:
                # Task 19: the authoritative versioned loader owns envelope
                # migration and delegates the nested SimulationIntent to its
                # own registry, so both must declare an explicit version.
                payload, intent = load_fallback_record(
                    path.read_text(encoding="utf-8"),
                    source=f"eval/fallback/{case_id}.json",
                )
                if payload.get("mode") != "REPLAY" or payload.get("model_sha256") != inventory.file_sha256:
                    raise ValueError("fallback model hash or mode does not match")
                Interpretation.model_validate(payload["typed_interpreter_output"], strict=True)
                if any(region.status != "proposed" for region in intent.regions):
                    raise ValueError("fallback regions must remain proposed")
            except SchemaVersionError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"fallback data is invalid: {exc.safe_message}",
                ) from exc
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=422, detail=f"fallback data is invalid: {exc}") from exc
            return payload, intent

        @app.get("/session/{session_id}/fallback-cases")
        async def list_session_fallback_cases(session_id: str) -> dict[str, Any]:
            _, inventory, _ = step_geometry(session_id)
            directory = Path(__file__).resolve().parents[1] / "eval" / "fallback"
            case_ids = []
            for path in sorted(directory.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if payload.get("mode") == "REPLAY" and payload.get("model_sha256") == inventory.file_sha256:
                    case_ids.append(payload.get("case_id"))
            return {"mode": "REPLAY", "model_id": session_id, "case_ids": case_ids}

        @app.post("/session/{session_id}/fallback/{case_id}", response_model=InterpretResponse)
        async def load_session_fallback(session_id: str, case_id: str) -> InterpretResponse:
            payload, intent = fallback_payload(session_id, case_id)
            instruction = next(
                (region.source_instruction for region in intent.regions),
                "Apply whole-model gravity.",
            )
            saved, notices = save_proposal(
                session_id,
                intent,
                source_instruction=instruction,
            )
            grounding = GroundingBatch.model_validate_json(
                json.dumps(payload["final_grounding"]), strict=True
            )
            await publish_grounding(grounding)
            return InterpretResponse(
                mode="REPLAY", fallback=True, state="proposed",
                instruction=instruction,
                interpretation=payload["typed_interpreter_output"], grounding=grounding,
                intent=saved, clarification_count=1 if payload.get("clarification_used") else 0,
                model_name="checked-in typed responses",
                notices=notices,
            )

    def ensure_uploaded_model(session_id: str) -> None:
        # Session ids are the deterministic uploaded-model ids.  Looking the
        # model up first prevents orphan sessions and cross-model state.
        app.state.model_store.get(session_id)

    @app.get("/session/{session_id}/intent", response_model=SessionSnapshot)
    async def get_session_intent(session_id: str) -> SessionSnapshot:
        ensure_uploaded_model(session_id)
        return legacy_snapshot(
            session_id, app.state.session_store.get_or_create(session_id)
        )

    @app.put("/session/{session_id}/intent", response_model=SessionSnapshot)
    async def put_session_intent(
        session_id: str, intent: LegacySimulationIntent, request: Request
    ) -> SessionSnapshot:
        ensure_uploaded_model(session_id)
        # Task 19 decision D-2: this frozen legacy route is the sole
        # compatibility exception. The typed body parameter above keeps the
        # published request contract and FastAPI's existing 422 envelope
        # unchanged; the cached raw body below is read only to inspect the
        # *presence* of a declared version, never to guess the payload shape.
        # An absent version is normalised through a route-scoped constant; a
        # declared malformed, obsolete, or future version still fails.
        try:
            normalized, _ = normalize_legacy_intent_payload(
                await request.body(), source=LEGACY_INTENT_ROUTE
            )
            load_simulation_intent(normalized, source=LEGACY_INTENT_ROUTE)
        except SchemaVersionError as exc:
            # D-4: legacy routes keep their existing error envelope shape.
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": exc.safe_message},
            ) from exc
        try:
            return legacy_snapshot(
                session_id,
                app.state.session_store.save_intent(session_id, intent),
            )
        except (InvalidRegionTransitionError, InvalidAssumptionTransitionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def transition_region(
        session_id: str,
        transition: RegionTransitionRequest,
        target: str,
    ) -> SessionSnapshot:
        ensure_uploaded_model(session_id)
        try:
            if target == "confirmed":
                return legacy_snapshot(
                    session_id,
                    app.state.session_store.confirm_region(
                        session_id, transition.region_id
                    ),
                )
            return legacy_snapshot(
                session_id,
                app.state.session_store.reject_region(
                    session_id, transition.region_id
                ),
            )
        except SessionIntentMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SessionRegionMissingError as exc:
            raise HTTPException(
                status_code=404, detail=f"region '{exc.args[0]}' not found"
            ) from exc
        except InvalidRegionTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/session/{session_id}/confirm_region", response_model=SessionSnapshot
    )
    async def confirm_session_region(
        session_id: str, transition: RegionTransitionRequest
    ) -> SessionSnapshot:
        return await transition_region(session_id, transition, "confirmed")

    @app.post(
        "/session/{session_id}/reject_region", response_model=SessionSnapshot
    )
    async def reject_session_region(
        session_id: str, transition: RegionTransitionRequest
    ) -> SessionSnapshot:
        return await transition_region(session_id, transition, "rejected")

    async def transition_assumption(
        session_id: str,
        assumption_id: str,
        target: Literal["accepted", "rejected"],
    ) -> SessionSnapshot:
        ensure_uploaded_model(session_id)
        try:
            if target == "accepted":
                return legacy_snapshot(
                    session_id,
                    app.state.session_store.accept_assumption(
                        session_id, assumption_id
                    ),
                )
            return legacy_snapshot(
                session_id,
                app.state.session_store.reject_assumption(
                    session_id, assumption_id
                ),
            )
        except SessionIntentMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SessionAssumptionMissingError as exc:
            raise HTTPException(
                status_code=404, detail=f"assumption '{exc.args[0]}' not found"
            ) from exc
        except InvalidAssumptionTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/session/{session_id}/assumptions/{assumption_id}/accept",
        response_model=SessionSnapshot,
    )
    async def accept_session_assumption(
        session_id: str, assumption_id: str
    ) -> SessionSnapshot:
        return await transition_assumption(session_id, assumption_id, "accepted")

    @app.post(
        "/session/{session_id}/assumptions/{assumption_id}/reject",
        response_model=SessionSnapshot,
    )
    async def reject_session_assumption(
        session_id: str, assumption_id: str
    ) -> SessionSnapshot:
        return await transition_assumption(session_id, assumption_id, "rejected")

    def audit_response(session_id: str) -> AuditResponse:
        record = app.state.model_store.get(session_id)
        try:
            intent, engineering_report = (
                app.state.session_store.intent_and_report(session_id)
            )
        except SessionIntentMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        capability = model_capability(intent, record)
        report = project_report(engineering_report, capability)
        regions = []
        for region in intent.regions:
            regions.append(
                AuditRegion(
                    **region.model_dump(mode="python"),
                    boundary_conditions=[
                        bc.model_dump(mode="json")
                        for bc in intent.bcs
                        if bc.region_ref == region.id
                    ],
                    loads=[
                        load.model_dump(mode="json")
                        for load in intent.loads
                        if load.region_ref == region.id
                    ],
                )
            )
        blocking = [issue for issue in report.issues if issue.blocks_export]
        return AuditResponse(
            session_id=session_id,
            model_id=session_id,
            validation_status=report.validation_status,
            export_eligible=report.export_eligible,
            blocking_reasons=[issue.message for issue in blocking],
            regions=regions,
            assumptions=[item.model_copy(deep=True) for item in intent.assumptions],
            validation_report=report,
        )

    @app.get("/session/{session_id}/audit", response_model=AuditResponse)
    async def get_session_audit(session_id: str) -> AuditResponse:
        return audit_response(session_id)

    @app.post(
        "/session/{session_id}/export-gate",
        response_model=ExportGateResponse,
    )
    async def check_session_export_gate(
        session_id: str,
    ) -> ExportGateResponse | JSONResponse:
        audit = audit_response(session_id)
        blocking = [
            issue
            for issue in audit.validation_report.issues
            if issue.blocks_export
        ]
        if not audit.export_eligible:
            response = ExportGateResponse(
                session_id=session_id,
                model_id=session_id,
                status="blocked",
                validation_status=audit.validation_status,
                export_eligible=False,
                message="Export is blocked; resolve every listed readiness issue.",
                blocking_issues=blocking,
            )
            return JSONResponse(
                status_code=409, content=response.model_dump(mode="json")
            )
        return ExportGateResponse(
            session_id=session_id,
            model_id=session_id,
            status="ready",
            validation_status=audit.validation_status,
            export_eligible=True,
            message=(
                "Task 13 readiness confirmed. No artifact was generated; "
                "solver artifact generation belongs to Task 14."
            ),
            blocking_issues=[],
        )

    @app.post("/session/{session_id}/export")
    async def export_session_artifact(
        session_id: str, request: ArtifactExportRequest
    ) -> Response:
        """Regenerate an artifact from stored confirmed IR and model metadata."""

        record = app.state.model_store.get(session_id)
        try:
            intent, engineering_report = (
                app.state.session_store.intent_and_report(session_id)
            )
        except SessionIntentMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        capability = model_capability(intent, record)
        report = project_report(engineering_report, capability)
        blockers = [issue for issue in report.issues if issue.blocks_export]
        if not report.export_eligible:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "export_not_ready",
                    "message": "Export is blocked; resolve every listed readiness issue.",
                    "validation_status": report.validation_status,
                    "export_eligible": False,
                    "blocking_issues": [
                        issue.model_dump(mode="json") for issue in blockers
                    ],
                },
            )

        adapter = request.adapter.strip().lower()
        if adapter not in {"abaqus_py", "ccx_inp"}:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "unknown_adapter",
                    "message": "Unknown export adapter; choose abaqus_py or ccx_inp.",
                    "adapter": adapter,
                },
            )

        try:
            inventory = app.state.model_store.inventory(record)
            if adapter == "abaqus_py":
                if record.kind != "step" or not isinstance(inventory, FaceInventory):
                    raise UnsupportedModelTypeError(
                        "The abaqus_py adapter requires a STEP/CAD model."
                    )
                metadata = CadModelMetadata(
                    source_path=record.path,
                    source_name=record.source_name,
                    source_sha256=inventory.file_sha256,
                    face_ids=tuple(sorted(face.tag for face in inventory.faces)),
                )
                result = export_abaqus_py(intent, metadata)
            else:
                if record.kind != "inp" or not isinstance(inventory, MeshInventory):
                    raise UnsupportedModelTypeError(
                        "The ccx_inp adapter requires an existing Abaqus INP mesh model."
                    )
                metadata = mesh_metadata(record, inventory)
                result = export_ccx_inp(intent, metadata)
        except ExportNotReadyError as exc:
            # The adapter independently recomputes Task 13 validation and the
            # SimulationIntent.export_payload() confirmation gate.
            return JSONResponse(
                status_code=409,
                content={
                    "code": exc.code,
                    "message": exc.safe_message,
                    "validation_status": exc.report.validation_status,
                    "export_eligible": False,
                    "blocking_issues": [
                        issue.model_dump(mode="json")
                        for issue in blocking_issues(exc)
                    ],
                },
            )
        except ExportAdapterError as exc:
            status_code = 500 if exc.code == "artifact_generation_failed" else 422
            return JSONResponse(
                status_code=status_code,
                content={
                    "code": exc.code,
                    "message": exc.safe_message,
                    "adapter": adapter,
                },
            )
        except Exception:
            # Do not expose exception text: parser/generator errors can contain
            # local paths or implementation details.
            return JSONResponse(
                status_code=500,
                content={
                    "code": "artifact_generation_failed",
                    "message": "Artifact generation failed unexpectedly.",
                    "adapter": adapter,
                },
            )

        return Response(
            content=result.artifact_bytes,
            media_type=result.media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{result.suggested_filename}"'
                ),
                "X-Artifact-SHA256": result.checksum_sha256,
                "X-Solver-Executed": "false",
            },
        )

    # Task 8 names the plural routes; CLAUDE.md freezes the singular viewer
    # contract. Both resolve through the same handlers and payload schemas.
    app.add_api_route(
        "/models/{model_id}/inventory", inventory_response, methods=["GET"]
    )
    app.add_api_route(
        "/model/{model_id}/inventory", inventory_response, methods=["GET"]
    )
    app.add_api_route("/models/{model_id}/gltf", gltf_response, methods=["GET"])
    app.add_api_route("/model/{model_id}/gltf", gltf_response, methods=["GET"])
    return app


async def _uploaded_file(
    request: Request, filename_query: str | None
) -> tuple[str, bytes]:
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: "
            + content_type.encode("latin-1")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
        if not message.is_multipart():
            raise HTTPException(status_code=400, detail="malformed multipart upload")
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") == "file":
                source_name = part.get_filename()
                if not source_name:
                    raise HTTPException(status_code=400, detail="uploaded file has no filename")
                return source_name, part.get_payload(decode=True) or b""
        raise HTTPException(status_code=400, detail="multipart upload requires a 'file' field")

    source_name = filename_query or request.headers.get("x-filename")
    if not source_name:
        raise HTTPException(
            status_code=400,
            detail="raw upload requires a filename query parameter or X-Filename header",
        )
    return source_name, body


def _safe_source_name(source_name: str) -> str:
    if (
        not source_name
        or source_name in {".", ".."}
        or "/" in source_name
        or "\\" in source_name
        or "\0" in source_name
        or Path(source_name).name != source_name
    ):
        raise HTTPException(status_code=400, detail="invalid source filename")
    return source_name


def _tessellate_step(path: Path) -> list[FaceMesh]:
    """Deterministically mesh STEP surfaces and keep triangles per CAD face."""
    if gmsh.isInitialized():
        raise RuntimeError("STEP tessellation requires exclusive use of gmsh")
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Mesh.MaxNumThreads1D", 1)
        gmsh.option.setNumber("Mesh.MaxNumThreads2D", 1)
        # A zero perturbation makes Gmsh's Delaunay triangulator reject
        # coincident projected points on this fixture. Fix the seed and retain
        # the documented tiny perturbation so output stays reproducible.
        gmsh.option.setNumber("Mesh.RandomSeed", 1)
        gmsh.option.setNumber("Mesh.RandomFactor", 1e-9)
        gmsh.model.add(f"viewer_{path.stem}")
        gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()
        gmsh.model.mesh.generate(2)

        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        coordinate_by_tag = {
            int(tag): (
                float(coordinates[3 * i]),
                float(coordinates[3 * i + 1]),
                float(coordinates[3 * i + 2]),
            )
            for i, tag in enumerate(node_tags)
        }
        result: list[FaceMesh] = []
        for _, face_tag in sorted(gmsh.model.getEntities(2), key=lambda entity: entity[1]):
            element_types, _, element_nodes = gmsh.model.mesh.getElements(2, face_tag)
            triangles: list[tuple[int, int, int]] = []
            for element_type, flattened in zip(element_types, element_nodes):
                _, dimension, _, node_count, _, primary_count = (
                    gmsh.model.mesh.getElementProperties(element_type)
                )
                if dimension != 2 or node_count != 3 or primary_count != 3:
                    raise ValueError(
                        f"face {face_tag} produced unsupported non-triangle elements"
                    )
                values = [int(tag) for tag in flattened]
                triangles.extend(
                    (values[i], values[i + 1], values[i + 2])
                    for i in range(0, len(values), 3)
                )
            if not triangles:
                raise ValueError(f"face {face_tag} produced no triangles")
            result.append(_face_mesh_from_tagged_triangles(face_tag, triangles, coordinate_by_tag))
        return result
    finally:
        gmsh.finalize()


def _tessellate_inp(path: Path, inventory: MeshInventory) -> list[FaceMesh]:
    """Build one selectable glTF mesh for each INP boundary facet group."""
    mesh = meshio.read(path, file_format="abaqus")
    native_node_ids, _ = _scan_inp_native_ids(path)
    if len(native_node_ids) != len(mesh.points):
        raise ValueError("INP native node ids do not align with mesh coordinates")
    coordinate_by_tag = {
        native_id: tuple(float(value) for value in mesh.points[i][:3])
        for i, native_id in enumerate(native_node_ids)
    }
    facet_by_id = {facet.id: facet for facet in inventory.facets}
    result = []
    for group in sorted(inventory.facet_groups, key=lambda item: item.id):
        triangles = [tuple(facet_by_id[fid].node_ids) for fid in group.facet_ids]
        result.append(_face_mesh_from_tagged_triangles(group.id, triangles, coordinate_by_tag))
    return result


def _face_mesh_from_tagged_triangles(
    face_id: int,
    triangles: list[tuple[int, int, int]],
    coordinate_by_tag: dict[int, tuple[float, float, float]],
) -> FaceMesh:
    """Canonicalize triangle order while preserving each triangle's winding."""
    canonical = []
    for triangle in triangles:
        lowest = min(range(3), key=triangle.__getitem__)
        rotated = triangle[lowest:] + triangle[:lowest]
        canonical.append(rotated)
    canonical.sort()
    used_tags = sorted({tag for triangle in canonical for tag in triangle})
    local_index = {tag: i for i, tag in enumerate(used_tags)}
    try:
        positions = [coordinate_by_tag[tag] for tag in used_tags]
    except KeyError as exc:
        raise ValueError(f"mesh references missing node {exc.args[0]}") from exc
    indices = [local_index[tag] for triangle in canonical for tag in triangle]
    return FaceMesh(face_id=face_id, positions=positions, indices=indices)


def _build_gltf(face_meshes: list[FaceMesh]) -> dict[str, Any]:
    """Encode face meshes as glTF 2.0 JSON with one embedded binary buffer."""
    if not face_meshes:
        raise ValueError("cannot create glTF without face meshes")
    if len({mesh.face_id for mesh in face_meshes}) != len(face_meshes):
        raise ValueError("glTF face ids must be unique")

    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []

    def align_four() -> None:
        binary.extend(b"\0" * (-len(binary) % 4))

    for face_mesh in sorted(face_meshes, key=lambda item: item.face_id):
        if not face_mesh.positions or not face_mesh.indices:
            raise ValueError(f"face {face_mesh.face_id} has empty geometry")
        align_four()
        position_offset = len(binary)
        for xyz in face_mesh.positions:
            binary.extend(struct.pack("<3f", *xyz))
        position_view = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": position_offset,
                "byteLength": len(face_mesh.positions) * 12,
                "target": 34962,
            }
        )
        position_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": position_view,
                "componentType": 5126,
                "count": len(face_mesh.positions),
                "type": "VEC3",
                "min": [min(p[axis] for p in face_mesh.positions) for axis in range(3)],
                "max": [max(p[axis] for p in face_mesh.positions) for axis in range(3)],
            }
        )

        align_four()
        index_offset = len(binary)
        for index in face_mesh.indices:
            binary.extend(struct.pack("<I", index))
        index_view = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": index_offset,
                "byteLength": len(face_mesh.indices) * 4,
                "target": 34963,
            }
        )
        index_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": index_view,
                "componentType": 5125,
                "count": len(face_mesh.indices),
                "type": "SCALAR",
                "min": [min(face_mesh.indices)],
                "max": [max(face_mesh.indices)],
            }
        )

        mesh_index = len(meshes)
        meshes.append(
            {
                "name": f"face_{face_mesh.face_id}",
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        )
        nodes.append({"name": f"face_{face_mesh.face_id}", "mesh": mesh_index})

    encoded = base64.b64encode(binary).decode("ascii")
    return {
        "asset": {"version": "2.0", "generator": "sim-intent Task 8"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [{"name": "default", "doubleSided": True}],
        "buffers": [
            {
                "byteLength": len(binary),
                "uri": "data:application/octet-stream;base64," + encoded,
            }
        ],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }


app = create_app()


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        created_at=_utc_isoformat(project.created_at),
    )


def _version_response(
    version: ModelVersion, current_version_id: str | None
) -> ModelVersionResponse:
    return ModelVersionResponse(
        id=version.id,
        model_id=version.model_id,
        version=version.version,
        source_sha256=version.source_sha256,
        source_name=version.source_name,
        size_bytes=version.size_bytes,
        media_type=version.media_type,
        model_kind=version.model_kind,
        created_at=_utc_isoformat(version.created_at),
        is_current=version.id == current_version_id,
        is_superseded=version.is_superseded,
        superseded_at=(
            None if version.superseded_at is None
            else _utc_isoformat(version.superseded_at)
        ),
        superseded_by_version_id=version.superseded_by_version_id,
    )


def _utc_isoformat(value) -> str:
    return (
        value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    ).astimezone(timezone.utc).isoformat()


def _content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    return f"inline; filename=\"model.gltf\"; filename*=UTF-8''{encoded}"


def _not_found_problem(resource: str) -> ApiProblem:
    normalized = resource.replace(" ", "_")
    return ApiProblem(
        status=404,
        code=f"{normalized}_not_found",
        title=f"{resource.title()} not found",
        detail=f"The requested {resource} does not exist.",
    )


def source_storage_problem() -> ApiProblem:
    return ApiProblem(
        status=507,
        code="source_storage_limit_exceeded",
        title="Insufficient source storage",
        detail="The configured durable source-storage capacity would be exceeded.",
    )


def _validate_source_upload(source_name: str, content: bytes) -> tuple[str, str]:
    source_name = _safe_source_name(source_name)
    kind = SUPPORTED_SUFFIXES.get(Path(source_name).suffix.lower())
    if kind is None:
        raise HTTPException(
            status_code=415,
            detail="unsupported model format; expected STEP (.step/.stp) or Abaqus INP (.inp)",
        )
    if not content:
        raise HTTPException(status_code=400, detail="uploaded model is empty")
    return source_name, kind


@contextmanager
def _materialized_model(
    source_name: str, kind: str, content: bytes
) -> Iterator[ModelRecord]:
    with tempfile.TemporaryDirectory(prefix="sim-intent-model-") as directory:
        path = Path(directory) / source_name
        path.write_bytes(content)
        yield ModelRecord("", source_name, kind, path)


def _validate_model_bytes(
    request: Request, source_name: str, kind: str, content: bytes
) -> None:
    with _materialized_model(source_name, kind, content) as record:
        try:
            ModelStore(record.path.parent).inventory(record)
        except Exception as exc:
            trace_id = (
                request.headers.get("x-correlation-id") or str(uuid.uuid4())
            )
            APPLICATION_LOGGER.exception(
                "Source-model parser failed trace_id=%s", trace_id
            )
            raise ApiProblem(
                status=422,
                code="source_model_parse_failed",
                title="Source model could not be parsed",
                detail="The uploaded source model could not be parsed.",
                trace_id=trace_id,
            ) from exc
