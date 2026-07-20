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
import struct
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import gmsh
import meshio
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.session import (
    InvalidAssumptionTransitionError,
    InvalidRegionTransitionError,
    RegionTransitionRequest,
    SelectionSessionStore,
    SessionAssumptionMissingError,
    SessionIntentMissingError,
    SessionRegionMissingError,
    SessionSnapshot,
)
from export.abaqus_py import export_abaqus_py
from export.ccx_inp import export_ccx_inp
from export.common import (
    CadModelMetadata,
    ExportAdapterError,
    ExportNotReadyError,
    MeshModelMetadata,
    UnsupportedModelTypeError,
    blocking_issues,
)
from geom.inventory import FaceInventory, get_inventory
from geom.meshes import MeshInventory, _scan_inp_native_ids, load_mesh
from ir.schema import (
    Assumption,
    EntityType,
    RegionStatus,
    SelectionMethod,
    SimulationIntent,
    StrictModel,
    ValidationStatus,
)
from ir.validate import ValidationIssue, ValidationReport

DEFAULT_MODEL_DIR = Path(".sim_intent_cache") / "models"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SUPPORTED_SUFFIXES = {".step": "step", ".stp": "step", ".inp": "inp"}
SELECTION_LOGGER = logging.getLogger("uvicorn.error")


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


def create_app(storage_dir: str | Path = DEFAULT_MODEL_DIR) -> FastAPI:
    app = FastAPI(title="sim-intent viewer backend")
    app.state.model_store = ModelStore(storage_dir)
    app.state.session_store = SelectionSessionStore()
    app.state.viewer_events = ViewerEventBroker()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def viewer_frontend() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

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
        response.headers["Content-Disposition"] = (
            f'inline; filename="{Path(record.source_name).stem}.gltf"'
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

    def ensure_uploaded_model(session_id: str) -> None:
        # Session ids are the deterministic uploaded-model ids.  Looking the
        # model up first prevents orphan sessions and cross-model state.
        app.state.model_store.get(session_id)

    @app.get("/session/{session_id}/intent", response_model=SessionSnapshot)
    async def get_session_intent(session_id: str) -> SessionSnapshot:
        ensure_uploaded_model(session_id)
        return app.state.session_store.get_or_create(session_id)

    @app.put("/session/{session_id}/intent", response_model=SessionSnapshot)
    async def put_session_intent(
        session_id: str, intent: SimulationIntent
    ) -> SessionSnapshot:
        ensure_uploaded_model(session_id)
        try:
            return app.state.session_store.save_intent(session_id, intent)
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
                return app.state.session_store.confirm_region(
                    session_id, transition.region_id
                )
            return app.state.session_store.reject_region(
                session_id, transition.region_id
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
                return app.state.session_store.accept_assumption(
                    session_id, assumption_id
                )
            return app.state.session_store.reject_assumption(
                session_id, assumption_id
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
        ensure_uploaded_model(session_id)
        try:
            intent, report = app.state.session_store.intent_and_report(session_id)
        except SessionIntentMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
            intent, report = app.state.session_store.intent_and_report(session_id)
        except SessionIntentMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
                node_ids, element_blocks = _scan_inp_native_ids(record.path)
                metadata = MeshModelMetadata(
                    source_path=record.path,
                    inventory=inventory,
                    node_ids=tuple(node_ids),
                    element_ids=tuple(
                        element_id
                        for block in element_blocks
                        for element_id in block
                    ),
                )
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
