"""Startup-fixed runtime mode selection (Task 18, ADR-005).

Exactly one of four mutually exclusive runtime modes is selected when an
application instance is constructed and can never change on that instance:

- ``production``      — the default; REPLAY fallback routes are never
  registered and evaluation fixtures are unreachable.
- ``live_evaluation`` — labeled LIVE evaluation; fallback routes are likewise
  absent and a provider failure never substitutes REPLAY output.
- ``replay``          — deterministic checked-in REPLAY data, always labeled
  REPLAY, never presented as LIVE.
- ``test``            — the automated test suite; behaves like ``replay`` for
  route registration so the frozen fallback contract tests keep running.

The mode comes from the ``SIM_INTENT_MODE`` environment variable, resolved
once per constructed application. An unset or empty variable selects
``production`` (fail-safe). Any other unknown value is a startup
configuration error, never a silent default.
"""

from __future__ import annotations

import enum
import os
from typing import Mapping

MODE_ENV_VAR = "SIM_INTENT_MODE"


class RuntimeMode(enum.Enum):
    """One immutable process-startup runtime mode (ADR-005)."""

    PRODUCTION = "production"
    LIVE_EVALUATION = "live_evaluation"
    REPLAY = "replay"
    TEST = "test"

    @property
    def registers_fallback_routes(self) -> bool:
        """Whether REPLAY fallback routes may be registered at all."""
        return self in (RuntimeMode.REPLAY, RuntimeMode.TEST)


class RuntimeModeError(ValueError):
    """Raised at startup for an unknown runtime-mode configuration value."""


def resolve_runtime_mode(env: Mapping[str, str] | None = None) -> RuntimeMode:
    """Validate and return the configured runtime mode.

    ``env`` defaults to ``os.environ``; passing a mapping keeps the function
    deterministic and testable. Unset or empty selects ``production``.
    Unknown values raise :class:`RuntimeModeError` with the exact accepted
    vocabulary so a misconfigured deployment fails closed at startup.
    """

    source = os.environ if env is None else env
    raw = source.get(MODE_ENV_VAR)
    if raw is None or raw.strip() == "":
        return RuntimeMode.PRODUCTION
    try:
        return RuntimeMode(raw.strip())
    except ValueError:
        accepted = ", ".join(mode.value for mode in RuntimeMode)
        raise RuntimeModeError(
            f"{MODE_ENV_VAR} must be unset or one of: {accepted}; got {raw!r}"
        ) from None
