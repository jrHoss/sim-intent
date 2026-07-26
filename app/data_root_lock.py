"""Exclusive operating-system ownership of one local application data root."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from filelock import FileLock, Timeout


class DataRootInUseError(RuntimeError):
    """Another process currently owns the configured durable data root."""


class DataRootLock:
    """Hold a cross-platform advisory OS lock for an application lifespan.

    The lock file may remain after normal shutdown or a crash. Ownership is
    determined by the operating-system lock held by ``filelock``, not by file
    existence, so stale lock-file metadata does not block later startup. Lock
    files live outside the protected root so rejected owners cannot mutate it.
    """

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).resolve()
        identity = os.path.normcase(str(self.data_root))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.lock_directory = (
            Path(tempfile.gettempdir()).resolve() / "sim-intent-data-root-locks"
        )
        self.path = self.lock_directory / f"{digest}.lock"
        self._lock = FileLock(self.path, timeout=0)
        self._acquired = False

    def acquire(self) -> None:
        # Only the external lock directory may be created before ownership.
        # The protected data root is untouched until this OS lock succeeds.
        self.lock_directory.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire(timeout=0)
        except Timeout as exc:
            raise DataRootInUseError(
                "The configured sim-intent data root is already in use by "
                "another application process."
            ) from exc
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            self._lock.release()
            self._acquired = False

    def __enter__(self) -> "DataRootLock":
        self.acquire()
        return self

    def __exit__(self, *_args) -> None:
        self.release()
