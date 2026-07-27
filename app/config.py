"""Local durable-data configuration."""

from __future__ import annotations

import os
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalDataConfig:
    """All application-owned durable paths, with deterministic test overrides."""

    root: Path
    quarantine_directory: Path | None = None
    max_source_upload_bytes: int = 64 * 1024 * 1024
    parser_timeout_seconds: float = 30.0
    parser_output_bytes: int = 256 * 1024
    stale_quarantine_age_seconds: float = 3600.0
    stale_quarantine_cleanup_limit: int = 100

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ValueError("data root must be absolute")
        if self.max_source_upload_bytes <= 0:
            raise ValueError("maximum source upload bytes must be positive")
        if not math.isfinite(self.parser_timeout_seconds) or self.parser_timeout_seconds <= 0 or self.parser_output_bytes <= 0:
            raise ValueError("parser limits must be positive")
        if not math.isfinite(self.stale_quarantine_age_seconds) or self.stale_quarantine_age_seconds < 0:
            raise ValueError("stale quarantine age must be non-negative")
        if self.stale_quarantine_cleanup_limit <= 0:
            raise ValueError("stale quarantine cleanup limit must be positive")
        quarantine = self.quarantine_root
        root = self.root.resolve()
        if quarantine == root or root not in quarantine.parents:
            raise ValueError("quarantine directory must be beneath the data root")
        blob_root = self.blob_root.resolve()
        if quarantine == blob_root or blob_root in quarantine.parents:
            raise ValueError("quarantine directory must not be inside the blob root")
        database_path = self.database_path.resolve()
        if quarantine == database_path or database_path in quarantine.parents:
            raise ValueError("quarantine directory must not contain the database path")
        lock_root = (
            Path(tempfile.gettempdir()).resolve() / "sim-intent-data-root-locks"
        )
        if quarantine == lock_root or lock_root in quarantine.parents:
            raise ValueError("quarantine directory must not be inside the lock root")

    @classmethod
    def from_env(cls) -> "LocalDataConfig":
        configured = os.environ.get("SIM_INTENT_DATA_ROOT")
        if configured:
            configured_path = Path(configured).expanduser()
            if not configured_path.is_absolute():
                raise ValueError("SIM_INTENT_DATA_ROOT must be an absolute path")
            root = configured_path.resolve()
        else:
            if sys.platform == "win32":
                base = Path(
                    os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
                )
            else:
                base = Path(
                    os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
                )
            root = (base / "sim-intent").resolve()
        config = cls(
            root,
            quarantine_directory=_quarantine_path(root),
            max_source_upload_bytes=_positive_int("SIM_INTENT_MAX_SOURCE_UPLOAD_BYTES", 64 * 1024 * 1024),
            parser_timeout_seconds=_positive_float("SIM_INTENT_PARSER_TIMEOUT_SECONDS", 30.0),
            parser_output_bytes=_positive_int("SIM_INTENT_PARSER_OUTPUT_BYTES", 256 * 1024),
            stale_quarantine_age_seconds=_nonnegative_float("SIM_INTENT_STALE_QUARANTINE_AGE_SECONDS", 3600.0),
            stale_quarantine_cleanup_limit=_positive_int("SIM_INTENT_STALE_QUARANTINE_CLEANUP_LIMIT", 100),
        )
        return config

    @property
    def database_path(self) -> Path:
        return self.root / "sim-intent.sqlite3"

    @property
    def blob_root(self) -> Path:
        return self.root / "blobs"

    @property
    def quarantine_root(self) -> Path:
        return (
            self.quarantine_directory.resolve()
            if self.quarantine_directory is not None
            else (self.root / "quarantine").resolve()
        )

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.resolve().as_posix()}"


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be positive") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be non-negative") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _quarantine_path(root: Path) -> Path | None:
    configured = os.environ.get("SIM_INTENT_QUARANTINE_DIR")
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError("SIM_INTENT_QUARANTINE_DIR must be an absolute path")
    return path.resolve()
