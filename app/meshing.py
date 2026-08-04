"""Application service for isolated deterministic STEP meshing."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.blob_store import (
    BlobCoordinationPathError,
    BlobCoordinationTimeoutError,
    BlobIntegrityError,
)
from app.config import LocalDataConfig
from app.gmsh_coordinator import GmshCoordinationError, GmshExecutionCoordinator
from app.persistence import (
    MeshLineageConflictError,
    MeshOwnershipMismatchError,
    MeshPersistenceError,
    MeshRequestConflictError,
    Persistence,
    PersistenceDatabaseError,
    PersistenceNotFoundError,
    SetupRevisionConflictError,
    SetupSourceSupersededError,
)
from ir.validate import validate_intent
from mesh.generation import (
    MeshGenerationError,
    build_mesh_artifacts,
    mesh_settings_hash,
)
from mesh.profile import GMSH_TET_V1

WORKER_PROTOCOL_VERSION = GMSH_TET_V1.worker_protocol_version
WORKER_REJECTIONS = {
    "empty_mesh", "gmsh_profile_option_unsupported", "gmsh_unavailable",
    "gmsh_version_unsupported", "invalid_cad", "mesh_generation_failed",
    "unsupported_solid_count", "unsupported_element_type",
}


class MeshingServiceError(RuntimeError):
    """Stable path-free failure suitable for later RFC 9457 translation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MeshingService:
    def __init__(
        self,
        persistence: Persistence,
        coordinator: GmshExecutionCoordinator,
        config: LocalDataConfig,
    ):
        self.persistence = persistence
        self.coordinator = coordinator
        self.config = config
        self._worker_command_prefix: list[str] | None = None

    async def generate_and_publish(
        self, *, project_id: str, model_id: str, model_version_id: str,
        setup_id: str, setup_revision_id: str, request_id: str,
        predecessor_mesh_revision_id: str | None = None,
    ):
        """Generate and publish exactly one R5.1 MeshRevision."""

        project = self.persistence.get_project(project_id)
        model = self.persistence.get_model(model_id)
        version = self.persistence.get_version(model_version_id)
        setup = self.persistence.get_setup(setup_id)
        revision = self.persistence.get_setup_revision_by_id(setup_revision_id)
        if any(item is None for item in (project, model, version, setup, revision)):
            raise MeshingServiceError("missing_source_or_setup")
        if not (
            model.project_id == project_id
            and version.model_id == model_id
            and setup.project_id == project_id
            and setup.model_id == model_id
            and setup.model_version_id == model_version_id
            and revision.setup_id == setup_id
        ):
            raise MeshingServiceError("wrong_owner_setup")
        suffix = Path(version.source_name).suffix.lower()
        if version.model_kind != "step" or suffix not in {".step", ".stp"}:
            raise MeshingServiceError("unsupported_source_type")
        try:
            intent = self.persistence.revision_intent(revision)
        except Exception as exc:
            raise MeshingServiceError("invalid_mesh_settings") from exc
        settings = intent.mesh_settings
        if settings is None:
            raise MeshingServiceError("invalid_mesh_settings")
        settings_digest = mesh_settings_hash(settings)

        existing = self.persistence.get_mesh_revision_by_request(
            project_id=project_id, request_id=request_id
        )
        if existing is not None:
            if self._same_request(
                existing, model_id=model_id, model_version_id=model_version_id,
                setup_id=setup_id, setup_revision_id=setup_revision_id,
                predecessor_mesh_revision_id=predecessor_mesh_revision_id,
                source_sha256=version.source_sha256,
                settings_hash=settings_digest,
            ):
                return self._read_replay_exact(existing)
            raise MeshingServiceError("mesh_request_conflict")

        if (
            model.current_version_id != model_version_id
            or version.is_superseded
            or setup.is_stale
        ):
            raise MeshingServiceError("stale_source")
        if setup.current_revision != revision.revision:
            raise MeshingServiceError("stale_setup_revision")
        report = validate_intent(intent)
        if not report.engineering_ready:
            raise MeshingServiceError("setup_not_eligible")
        try:
            source_bytes = self.persistence.read_version_bytes(version)
        except Exception as exc:
            raise MeshingServiceError("missing_or_stale_source") from exc

        try:
            async with self.coordinator.acquire("mesh"):
                raw_mesh = await self._run_worker(
                    source_bytes, suffix, settings.global_element_size_mm
                )
        except GmshCoordinationError as exc:
            raise MeshingServiceError(exc.code) from exc
        except MeshingServiceError:
            raise
        except OSError as exc:
            raise MeshingServiceError("mesh_worker_unavailable") from exc

        mesh_revision_id = str(uuid.uuid4())
        try:
            topology, quality = build_mesh_artifacts(
                raw_mesh, mesh_revision_id=mesh_revision_id,
                project_id=project_id, model_id=model_id,
                model_version_id=model_version_id, setup_id=setup_id,
                setup_revision_id=setup_revision_id,
                setup_revision_created_at=revision.created_at,
                source_model_sha256=version.source_sha256, settings=settings,
            )
        except MeshGenerationError as exc:
            code = {
                "malformed_worker_response": "mesh_worker_response_malformed",
                "settings_binding_mismatch": "invalid_mesh_settings",
            }.get(exc.code, exc.code)
            raise MeshingServiceError(code) from exc
        try:
            return self.persistence.create_mesh_revision(
                project_id=project_id, model_id=model_id,
                model_version_id=model_version_id, setup_id=setup_id,
                setup_revision_id=setup_revision_id,
                predecessor_mesh_revision_id=predecessor_mesh_revision_id,
                request_id=request_id, topology=topology, quality=quality,
            )
        except MeshRequestConflictError:
            replay = self.persistence.get_mesh_revision_by_request(
                project_id=project_id, request_id=request_id
            )
            if replay is not None and self._same_request(
                replay, model_id=model_id, model_version_id=model_version_id,
                setup_id=setup_id, setup_revision_id=setup_revision_id,
                predecessor_mesh_revision_id=predecessor_mesh_revision_id,
                source_sha256=version.source_sha256,
                settings_hash=settings_digest,
            ):
                return self._read_replay_exact(replay)
            raise MeshingServiceError("mesh_request_conflict")
        except SetupSourceSupersededError as exc:
            raise MeshingServiceError("stale_source") from exc
        except SetupRevisionConflictError as exc:
            raise MeshingServiceError("stale_setup_revision") from exc
        except MeshOwnershipMismatchError as exc:
            raise MeshingServiceError("wrong_owner_setup") from exc
        except MeshLineageConflictError as exc:
            raise MeshingServiceError("mesh_lineage_conflict") from exc
        except PersistenceNotFoundError as exc:
            raise MeshingServiceError("missing_source_or_setup") from exc
        except MeshPersistenceError as exc:
            code = ("invalid_mesh_artifact" if exc.code in {
                "malformed_mesh_artifact", "mesh_artifact_integrity_failure"
            } else "mesh_publication_failed")
            raise MeshingServiceError(code) from exc
        except PersistenceDatabaseError as exc:
            raise MeshingServiceError("mesh_publication_failed") from exc
        except (
            BlobCoordinationPathError, BlobCoordinationTimeoutError,
            BlobIntegrityError, OSError,
        ) as exc:
            raise MeshingServiceError("mesh_publication_failed") from exc

    def _read_replay_exact(self, record):
        """Resolve every successful replay through R5.1's exact-read boundary."""

        try:
            exact, _, _ = self.persistence.read_mesh_revision(
                record.id,
                project_id=record.project_id,
                model_id=record.model_id,
                model_version_id=record.model_version_id,
                setup_id=record.setup_id,
                setup_revision_id=record.setup_revision_id,
            )
        except (MeshPersistenceError, PersistenceNotFoundError) as exc:
            raise MeshingServiceError(
                "mesh_replay_integrity_failure"
            ) from exc
        return exact

    @staticmethod
    def _same_request(record, **expected: Any) -> bool:
        observed = {
            "model_id": record.model_id,
            "model_version_id": record.model_version_id,
            "setup_id": record.setup_id,
            "setup_revision_id": record.setup_revision_id,
            "predecessor_mesh_revision_id": record.predecessor_mesh_revision_id,
            "source_sha256": record.source_model_sha256,
            "settings_hash": record.mesh_settings_hash,
        }
        return observed == expected and (
            record.mesher_profile_id == GMSH_TET_V1.profile_id
            and record.mesher_profile_version == GMSH_TET_V1.profile_version
        )

    async def _run_worker(
        self, source_bytes: bytes, suffix: str, target_size_mm: float
    ) -> dict[str, Any]:
        root = self.config.root / "workers"
        try:
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink() or not root.is_dir():
                raise OSError
        except OSError as exc:
            raise MeshingServiceError("mesh_worker_unavailable") from exc
        prefix = self._worker_command_prefix or [
            sys.executable, "-m", "app.mesh_worker"
        ]
        repository_root = Path(__file__).resolve().parents[1]
        env = {"PATH": os.environ.get("PATH", ""),
               "PYTHONPATH": str(repository_root)}
        with tempfile.TemporaryDirectory(prefix="mesh-", dir=root) as raw_dir:
            operation_dir = Path(raw_dir)
            source = operation_dir / f"source{suffix}"
            source.write_bytes(source_bytes)
            command = [*prefix, source.name, repr(float(target_size_mm))]
            kwargs: dict[str, Any] = {}
            if os.name != "nt":
                kwargs["start_new_session"] = True
            else:
                kwargs["creationflags"] = 0x00000200
            process = None
            stdout_task = stderr_task = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, cwd=operation_dir,
                    env=env, **kwargs,
                )
                stdout_task = asyncio.create_task(
                    _read_bounded(process.stdout, self.config.mesher_output_bytes)
                )
                stderr_task = asyncio.create_task(
                    _read_bounded(process.stderr, self.config.mesher_output_bytes)
                )
                try:
                    await asyncio.wait_for(
                        process.wait(), self.config.mesher_timeout_seconds
                    )
                    stdout, oversized = await stdout_task
                    _, stderr_oversized = await stderr_task
                except TimeoutError as exc:
                    await _terminate(process, stdout_task, stderr_task)
                    raise MeshingServiceError("mesh_worker_timeout") from exc
                if oversized or stderr_oversized:
                    raise MeshingServiceError("mesh_worker_response_too_large")
                if process.returncode != 0:
                    raise MeshingServiceError("mesh_worker_crash")
            except MeshingServiceError:
                if process is not None and process.returncode is None:
                    await _terminate(process, stdout_task, stderr_task)
                raise
            except asyncio.CancelledError:
                if process is not None:
                    await _terminate(process, stdout_task, stderr_task)
                raise
            except Exception as exc:
                if process is not None and process.returncode is None:
                    await _terminate(process, stdout_task, stderr_task)
                raise MeshingServiceError("mesh_worker_unavailable") from exc
            try:
                response = json.loads(stdout)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MeshingServiceError("mesh_worker_response_malformed") from exc
            if not isinstance(response, dict) or set(response) not in (
                {"protocol_version", "operation", "status", "mesh"},
                {"protocol_version", "operation", "status", "code"},
            ) or response.get("protocol_version") != WORKER_PROTOCOL_VERSION \
                    or response.get("operation") != "mesh" \
                    or response.get("status") not in {"ok", "rejected"}:
                raise MeshingServiceError("mesh_worker_response_malformed")
            if response["status"] == "rejected":
                code = response.get("code")
                if code not in WORKER_REJECTIONS:
                    raise MeshingServiceError("mesh_worker_response_malformed")
                raise MeshingServiceError(code)
            if not isinstance(response.get("mesh"), dict):
                raise MeshingServiceError("mesh_worker_response_malformed")
            return response["mesh"]


async def _read_bounded(stream, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    oversized = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks), oversized
        size += len(chunk)
        if size > limit:
            oversized = True
        remaining = limit - sum(map(len, chunks))
        if remaining > 0:
            chunks.append(chunk[:remaining])


async def _terminate(process, *tasks) -> None:
    try:
        if process.returncode is None and os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    try:
        await asyncio.wait_for(process.wait(), 2.0)
    except TimeoutError:
        pass
    await asyncio.gather(*(task for task in tasks if task is not None),
                         return_exceptions=True)
