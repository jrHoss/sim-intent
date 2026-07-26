"""Local durable-data configuration."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalDataConfig:
    """All application-owned durable paths, with deterministic test overrides."""

    root: Path

    @classmethod
    def from_env(cls) -> "LocalDataConfig":
        configured = os.environ.get("SIM_INTENT_DATA_ROOT")
        if configured:
            configured_path = Path(configured).expanduser()
            if not configured_path.is_absolute():
                raise ValueError("SIM_INTENT_DATA_ROOT must be an absolute path")
            return cls(configured_path.resolve())
        if sys.platform == "win32":
            base = Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            )
        else:
            base = Path(
                os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
            )
        return cls((base / "sim-intent").resolve())

    @property
    def database_path(self) -> Path:
        return self.root / "sim-intent.sqlite3"

    @property
    def blob_root(self) -> Path:
        return self.root / "blobs"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.resolve().as_posix()}"
