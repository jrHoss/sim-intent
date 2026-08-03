"""One bounded application-owned slot shared by parsing and meshing."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager


class GmshCoordinationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class GmshExecutionCoordinator:
    """Serialize Gmsh workers while bounding both waiters and wait time."""

    def __init__(self, *, wait_timeout_seconds: float = 5.0, max_pending: int = 2):
        if wait_timeout_seconds <= 0 or max_pending <= 0:
            raise ValueError("Gmsh coordinator limits must be positive")
        self.wait_timeout_seconds = wait_timeout_seconds
        self.max_pending = max_pending
        self._slot = asyncio.Semaphore(1)
        self._guard = threading.Lock()
        self._pending = 0

    @property
    def pending(self) -> int:
        with self._guard:
            return self._pending

    @asynccontextmanager
    async def acquire(self, operation: str):
        if operation not in {"parse", "mesh"}:
            raise ValueError("unsupported Gmsh operation")
        with self._guard:
            if self._pending >= self.max_pending:
                raise GmshCoordinationError("gmsh_slot_saturated")
            self._pending += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._slot.acquire(), self.wait_timeout_seconds
                )
                acquired = True
            except TimeoutError as exc:
                raise GmshCoordinationError("gmsh_slot_timeout") from exc
            yield
        finally:
            if acquired:
                self._slot.release()
            with self._guard:
                self._pending -= 1
