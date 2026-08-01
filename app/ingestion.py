"""Bounded quarantine upload and isolated parser orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from app.config import LocalDataConfig
from app.gmsh_coordinator import (
    GmshCoordinationError,
    GmshExecutionCoordinator,
)
from app.problems import ApiProblem

SUPPORTED = {".step": "step", ".stp": "step", ".inp": "inp"}
LOGGER = logging.getLogger("uvicorn.error")
PORTABLE_NAME = re.compile(r"^[A-Za-z0-9 _.-]{1,200}$")
MAX_MULTIPART_OVERHEAD = 1024 * 1024
MAX_PART_HEADERS = 64 * 1024


@dataclass(frozen=True)
class QuarantinedUpload:
    path: Path
    source_name: str
    kind: str
    size: int
    sha256: str


class IngestionService:
    def __init__(
        self, config: LocalDataConfig,
        gmsh_coordinator: GmshExecutionCoordinator | None = None,
    ):
        self.config = config
        self.root = config.quarantine_root.resolve()
        self._worker_command_prefix: list[str] | None = None
        self.gmsh_coordinator = gmsh_coordinator or GmshExecutionCoordinator(
            wait_timeout_seconds=config.gmsh_slot_wait_seconds,
            max_pending=config.gmsh_slot_max_pending,
        )

    def cleanup_stale(self) -> int:
        if not self.root.is_dir() or self.root.is_symlink():
            return 0
        cutoff = time.time() - self.config.stale_quarantine_age_seconds
        removed = 0
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return 0
        for entry in entries:
            if removed >= self.config.stale_quarantine_cleanup_limit:
                break
            try:
                if entry.is_file(follow_symlinks=False) and entry.name.startswith("upload-"):
                    stat = entry.stat(follow_symlinks=False)
                    if stat.st_mtime <= cutoff:
                        Path(entry.path).unlink(missing_ok=True)
                        removed += 1
            except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError):
                continue
        return removed

    async def receive(self, request: Request, filename_query: str | None) -> QuarantinedUpload:
        length = request.headers.get("content-length")
        if length:
            try:
                declared_length = int(length)
            except ValueError:
                declared_length = None
            allowance = MAX_MULTIPART_OVERHEAD if request.headers.get("content-type", "").lower().startswith("multipart/form-data") else 0
            if (
                declared_length is not None
                and declared_length > self.config.max_source_upload_bytes + allowance
            ):
                raise _problem(413, "upload_too_large", "Upload too large", "The source upload exceeds the configured size limit.")
        self.root.mkdir(parents=True, exist_ok=True)
        content_type = request.headers.get("content-type", "")
        if content_type.lower().startswith("multipart/form-data"):
            return await self._receive_multipart(request, content_type)
        name = filename_query or request.headers.get("x-filename")
        if not name:
            raise _problem(400, "unsafe_filename", "Unsafe filename", "A safe source filename is required.")
        name, kind = _validate_name(name)
        path, size, digest = await self._stream(
            request, self.config.max_source_upload_bytes, Path(name).suffix.lower()
        )
        if size == 0:
            path.unlink(missing_ok=True)
            raise _problem(400, "empty_upload", "Empty upload", "The uploaded source file is empty.")
        _validate_signature(path, kind)
        return QuarantinedUpload(path, name, kind, size, digest)

    async def _stream(
        self, request: Request, limit: int, suffix: str
    ) -> tuple[Path, int, str]:
        fd, raw = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=self.root)
        path = Path(raw)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as target:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > limit:
                        raise _problem(413, "upload_too_large", "Upload too large", "The source upload exceeds the configured size limit.")
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            return path, size, digest.hexdigest()
        except asyncio.CancelledError:
            path.unlink(missing_ok=True)
            raise
        except ApiProblem:
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise _problem(400, "interrupted_upload", "Upload interrupted", "The source upload was interrupted.") from exc

    async def _receive_multipart(self, request: Request, content_type: str) -> QuarantinedUpload:
        boundary_match = re.search(
            r"(?:^|;)\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))",
            content_type,
            re.IGNORECASE,
        )
        if boundary_match is None:
            raise _problem(400, "malformed_multipart", "Malformed multipart upload", "The multipart upload is malformed.")
        boundary_text = boundary_match.group(1) or boundary_match.group(2)
        try:
            boundary = boundary_text.encode("ascii")
            if not boundary or len(boundary) > 200 or b"\r" in boundary or b"\n" in boundary:
                raise ValueError
        except (UnicodeEncodeError, ValueError) as exc:
            raise _problem(400, "malformed_multipart", "Malformed multipart upload", "The multipart upload is malformed.") from exc

        marker = b"--" + boundary
        delimiter = b"\r\n" + marker
        buffer = bytearray()
        state = "start"
        total = 0
        file_count = 0
        target = None
        path: Path | None = None
        name = kind = None
        size = 0
        digest = hashlib.sha256()

        def write_file(data: bytes) -> None:
            nonlocal size
            if target is None or not data:
                return
            size += len(data)
            if size > self.config.max_source_upload_bytes:
                raise _problem(413, "upload_too_large", "Upload too large", "The source upload exceeds the configured size limit.")
            target.write(data)
            digest.update(data)

        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.config.max_source_upload_bytes + MAX_MULTIPART_OVERHEAD:
                    raise _problem(413, "upload_too_large", "Upload too large", "The source upload exceeds the configured size limit.")
                buffer.extend(chunk)
                while True:
                    if state == "start":
                        if len(buffer) < len(marker) + 2:
                            break
                        if not buffer.startswith(marker + b"\r\n"):
                            raise ValueError
                        del buffer[:len(marker) + 2]
                        state = "headers"
                    elif state == "headers":
                        end = buffer.find(b"\r\n\r\n")
                        if end < 0:
                            if len(buffer) > MAX_PART_HEADERS:
                                raise ValueError
                            break
                        headers = bytes(buffer[:end])
                        del buffer[:end + 4]
                        disposition = next(
                            (line for line in headers.split(b"\r\n") if line.lower().startswith(b"content-disposition:")),
                            None,
                        )
                        if disposition is None:
                            raise ValueError
                        field = _disposition_parameter(disposition, b"name")
                        filename = _disposition_parameter(disposition, b"filename")
                        if field == b"file":
                            if filename is None or file_count:
                                raise ValueError
                            decoded = filename.decode("utf-8")
                            name, kind = _validate_name(decoded)
                            fd, raw = tempfile.mkstemp(
                                prefix="upload-", suffix=Path(name).suffix.lower(), dir=self.root
                            )
                            path = Path(raw)
                            target = os.fdopen(fd, "wb")
                            file_count += 1
                        state = "body"
                    elif state == "body":
                        end = buffer.find(delimiter)
                        if end < 0:
                            retain = len(delimiter) + 4
                            if len(buffer) > retain:
                                write_file(bytes(buffer[:-retain]))
                                del buffer[:-retain]
                            break
                        write_file(bytes(buffer[:end]))
                        del buffer[:end + len(delimiter)]
                        if target is not None:
                            target.flush()
                            os.fsync(target.fileno())
                            target.close()
                            target = None
                        if buffer.startswith(b"--"):
                            del buffer[:2]
                            if buffer.startswith(b"\r\n"):
                                del buffer[:2]
                            state = "done"
                        elif buffer.startswith(b"\r\n"):
                            del buffer[:2]
                            state = "headers"
                        else:
                            raise ValueError
                    else:
                        buffer.clear()
                        break
            if state != "done" or file_count != 1 or path is None or name is None or kind is None:
                raise ValueError
            if size == 0:
                raise _problem(400, "empty_upload", "Empty upload", "The uploaded source file is empty.")
            _validate_signature(path, kind)
            return QuarantinedUpload(path, name, kind, size, digest.hexdigest())
        except ApiProblem:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _problem(400, "malformed_multipart", "Malformed multipart upload", "The multipart upload is malformed.") from exc
        finally:
            if target is not None:
                target.close()
            if path is not None and (state != "done" or size == 0):
                path.unlink(missing_ok=True)

    async def parse(
        self, upload: QuarantinedUpload, trace_id: str | None = None
    ) -> dict:
        if upload.kind != "step":
            return await self._parse_isolated(upload, trace_id)
        try:
            async with self.gmsh_coordinator.acquire("parse"):
                return await self._parse_isolated(upload, trace_id)
        except GmshCoordinationError as exc:
            title = (
                "Geometry worker busy"
                if exc.code == "gmsh_slot_saturated"
                else "Geometry worker wait timed out"
            )
            raise _problem(
                503, exc.code, title,
                "The shared geometry execution slot is temporarily unavailable.",
            ) from exc

    async def _parse_isolated(
        self, upload: QuarantinedUpload, trace_id: str | None = None
    ) -> dict:
        prefix = self._worker_command_prefix or [
            sys.executable, "-m", "app.parser_worker"
        ]
        command = [*prefix, upload.kind, str(upload.path)]
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        kwargs = {}
        job = None
        if os.name != "nt":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = 0x00000200
        stdout_task = stderr_task = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[1], env=env, **kwargs
            )
            if os.name == "nt":
                try:
                    job = _WindowsJob(process.pid)
                except Exception:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 2.0)
                    raise
            try:
                stdout_task = asyncio.create_task(
                    _read_bounded(process.stdout, self.config.parser_output_bytes, strict=True)
                )
                stderr_task = asyncio.create_task(
                    _read_bounded(process.stderr, self.config.parser_output_bytes, strict=False)
                )
                await asyncio.wait_for(process.wait(), self.config.parser_timeout_seconds)
                stdout_result, stderr_result = await asyncio.gather(stdout_task, stderr_task)
            except asyncio.CancelledError:
                await _terminate(process, job, stdout_task, stderr_task)
                raise
            except TimeoutError:
                await _terminate(process, job, stdout_task, stderr_task)
                raise _problem(422, "parser_timeout", "Parser timed out", "The source parser exceeded its configured time limit.")
            finally:
                if job is not None:
                    job.close()
                    job = None
            stdout, stdout_truncated = stdout_result
            stderr, stderr_truncated = stderr_result
            if stdout_truncated:
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser failed.")
            if process.returncode != 0:
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser exited abnormally.")
            try:
                result = json.loads(stdout)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser returned an invalid response.") from exc
            if not isinstance(result, dict):
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser returned an invalid response.")
            if result.get("protocol_version") != 1 or result.get("kind") != upload.kind or result.get("status") not in {"valid", "invalid", "error"}:
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser returned an invalid response.")
            if result["status"] == "invalid":
                raise _problem(422, "invalid_source_content", "Invalid source content", "The uploaded STEP or INP content is invalid.")
            if result["status"] == "error":
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser failed.")
            inventory = result.get("inventory")
            if not isinstance(inventory, dict) or inventory.get("file_sha256") != upload.sha256:
                _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser returned an invalid response.")
            if upload.kind == "step":
                analytic_surfaces = result.get("geometry_identity_surfaces")
                if not isinstance(analytic_surfaces, dict):
                    _log_parser_diagnostic(trace_id, stderr, stderr_truncated)
                    raise _problem(422, "parser_crash", "Parser failed", "The isolated source parser returned an invalid response.")
                inventory["_geometry_identity_surfaces"] = analytic_surfaces
            inventory["source_name"] = upload.source_name
            return inventory
        except ApiProblem:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _problem(500, "parser_parent_failure", "Parser unavailable", "The source parser could not be started.") from exc
        finally:
            if job is not None:
                job.close()


async def _terminate(
    process: asyncio.subprocess.Process,
    job,
    *reader_tasks: asyncio.Task | None,
) -> None:
    try:
        if job is not None:
            job.close()
        elif process.returncode is None and os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    for task in reader_tasks:
        if task is not None and not task.done():
            task.cancel()
    try:
        await asyncio.wait_for(process.wait(), 2.0)
    except TimeoutError:
        pass
    await asyncio.gather(
        *(task for task in reader_tasks if task is not None),
        return_exceptions=True,
    )


async def _read_bounded(
    stream: asyncio.StreamReader | None, limit: int, *, strict: bool
) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
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
        if not oversized or not strict:
            remaining = limit - sum(map(len, chunks))
            if remaining > 0:
                chunks.append(chunk[:remaining])


def _validate_name(name: str) -> tuple[str, str]:
    if (
        not PORTABLE_NAME.fullmatch(name)
        or name in {".", ".."}
        or name.startswith(".")
        or name.endswith((" ", "."))
        or ".." in Path(name).parts
        or Path(name).name != name
        or Path(name).is_absolute()
    ):
        raise _problem(400, "unsafe_filename", "Unsafe filename", "The source filename is unsafe.")
    suffixes = Path(name).suffixes
    if len(suffixes) != 1 or suffixes[0].lower() not in SUPPORTED:
        raise _problem(415, "unsupported_source_type", "Unsupported source type", "Expected a STEP (.step/.stp) or INP (.inp) source file.")
    stem = Path(name).stem.upper()
    if (
        stem in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"(?:COM|LPT)[1-9]", stem)
    ):
        raise _problem(400, "unsafe_filename", "Unsafe filename", "The source filename is unsafe.")
    return name, SUPPORTED[suffixes[0].lower()]


def _validate_signature(path: Path, kind: str) -> None:
    if kind == "inp":
        return
    with path.open("rb") as stream:
        prefix = stream.read(4096).lstrip()
    if not prefix.upper().startswith(b"ISO-10303-21"):
        path.unlink(missing_ok=True)
        raise _problem(422, "invalid_source_content", "Invalid source content", "The file content does not match its source type.")


def _disposition_parameter(header: bytes, key: bytes) -> bytes | None:
    pattern = rb"(?:^|;)\s*" + re.escape(key) + rb"=\"([^\"]*)\""
    match = re.search(pattern, header.split(b":", 1)[-1], re.IGNORECASE)
    return match.group(1) if match else None


def _log_parser_diagnostic(
    trace_id: str | None, diagnostic: bytes, truncated: bool
) -> None:
    text = diagnostic.decode("utf-8", errors="replace")
    LOGGER.error(
        "Isolated parser failure trace_id=%s diagnostic=%s%s",
        trace_id or "unassigned",
        text,
        " [truncated]" if truncated else "",
    )


class _WindowsJob:
    """Kill-on-close Job Object containing the parser and all descendants."""

    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        process_handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process_handle:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "OpenProcess failed")
        try:
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                error = ctypes.get_last_error()
                kernel32.CloseHandle(handle)
                raise OSError(error, "AssignProcessToJobObject failed")
        finally:
            kernel32.CloseHandle(process_handle)
        self._kernel32 = kernel32
        self._handle = handle

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _problem(status: int, code: str, title: str, detail: str) -> ApiProblem:
    return ApiProblem(status=status, code=code, title=title, detail=detail)
