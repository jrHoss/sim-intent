"""Task 18 runtime-mode separation tests (ADR-005).

Every application here is constructed explicitly with an immutable mode;
no test mutates a running application or the process environment consumed by
the module-level ``app = create_app()`` entry point.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.runtime_mode import (
    MODE_ENV_VAR,
    RuntimeMode,
    RuntimeModeError,
    resolve_runtime_mode,
)
from app.server import create_app
from llm.interpreter import InterpreterProviderError

BRACKET = Path(__file__).resolve().parent / "fixtures" / "bracket.step"
FALLBACK_LIST_PATH = "/session/{session_id}/fallback-cases"
FALLBACK_LOAD_PATH = "/session/{session_id}/fallback/{case_id}"
ALL_MODES = list(RuntimeMode)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    # Gmsh parsing installs signal handlers, so requests that trigger it must
    # run on the main thread (same pattern as tests/test_server.py).
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


def upload_bracket(app) -> str:
    response = request(
        app,
        "POST",
        "/models",
        files={"file": ("bracket.step", BRACKET.read_bytes(), "application/step")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class FailingTransport:
    """Provider transport that always fails with a typed LIVE error."""

    model = "unavailable-provider"

    def complete(self, request):
        raise InterpreterProviderError(
            "provider_unavailable", "provider is unavailable"
        )


def route_paths(app) -> set[str]:
    return {route.path for route in app.routes if isinstance(route, APIRoute)}


# --- resolve_runtime_mode -------------------------------------------------


def test_unset_mode_defaults_to_production():
    assert resolve_runtime_mode({}) is RuntimeMode.PRODUCTION


def test_empty_and_blank_mode_default_to_production():
    assert resolve_runtime_mode({MODE_ENV_VAR: ""}) is RuntimeMode.PRODUCTION
    assert resolve_runtime_mode({MODE_ENV_VAR: "   "}) is RuntimeMode.PRODUCTION


@pytest.mark.parametrize("mode", ALL_MODES)
def test_each_known_mode_resolves(mode: RuntimeMode):
    assert resolve_runtime_mode({MODE_ENV_VAR: mode.value}) is mode
    # Surrounding whitespace is tolerated; the value itself is exact.
    assert resolve_runtime_mode({MODE_ENV_VAR: f"  {mode.value}  "}) is mode


@pytest.mark.parametrize(
    "invalid",
    ["prod", "PRODUCTION", "Replay", "live", "evaluation", "fixtures", "0"],
)
def test_unknown_mode_is_a_startup_configuration_error(invalid: str):
    with pytest.raises(RuntimeModeError) as excinfo:
        resolve_runtime_mode({MODE_ENV_VAR: invalid})
    message = str(excinfo.value)
    assert MODE_ENV_VAR in message
    for mode in ALL_MODES:
        assert mode.value in message
    assert repr(invalid) in message


def test_resolution_reads_the_provided_mapping_not_ambient_state():
    assert (
        resolve_runtime_mode({MODE_ENV_VAR: "replay"}) is RuntimeMode.REPLAY
    )


# --- route matrix ---------------------------------------------------------


@pytest.mark.parametrize("mode", ALL_MODES)
def test_fallback_route_matrix(tmp_path, mode: RuntimeMode):
    app = create_app(tmp_path / "models", mode=mode)
    paths = route_paths(app)
    if mode.registers_fallback_routes:
        assert FALLBACK_LIST_PATH in paths
        assert FALLBACK_LOAD_PATH in paths
    else:
        assert FALLBACK_LIST_PATH not in paths
        assert FALLBACK_LOAD_PATH not in paths


def test_fallback_routes_present_only_in_replay_and_test():
    assert RuntimeMode.REPLAY.registers_fallback_routes
    assert RuntimeMode.TEST.registers_fallback_routes
    assert not RuntimeMode.PRODUCTION.registers_fallback_routes
    assert not RuntimeMode.LIVE_EVALUATION.registers_fallback_routes


@pytest.mark.parametrize(
    "mode", [RuntimeMode.PRODUCTION, RuntimeMode.LIVE_EVALUATION]
)
def test_fallback_endpoints_are_unregistered_404_not_403(tmp_path, mode):
    app = create_app(tmp_path / "models", mode=mode)
    client = TestClient(app)
    listing = client.get("/session/any/fallback-cases")
    loading = client.post("/session/any/fallback/bracket_combined_export")
    assert listing.status_code == 404
    assert loading.status_code == 404
    # An unregistered route yields FastAPI's plain 404, not a handler body.
    assert listing.json() == {"detail": "Not Found"}
    assert loading.json() == {"detail": "Not Found"}


def test_mode_gated_routes_are_the_only_route_difference(tmp_path):
    production = route_paths(create_app(tmp_path / "p", mode=RuntimeMode.PRODUCTION))
    test_mode = route_paths(create_app(tmp_path / "t", mode=RuntimeMode.TEST))
    assert test_mode - production == {FALLBACK_LIST_PATH, FALLBACK_LOAD_PATH}
    assert production - test_mode == set()


# --- immutability and ownership -------------------------------------------


@pytest.mark.parametrize("mode", ALL_MODES)
def test_app_state_carries_a_diagnostic_mode_copy(tmp_path, mode: RuntimeMode):
    app = create_app(tmp_path / "models", mode=mode)
    assert app.state.runtime_mode is mode


def test_constructed_application_mode_cannot_change_routes(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.PRODUCTION)
    before = route_paths(app)
    # Even a hostile later mutation of the diagnostic copy cannot register
    # the excluded routes: registration completed during construction and
    # handlers read the immutable construction-time closure value.
    app.state.runtime_mode = RuntimeMode.REPLAY
    assert route_paths(app) == before
    assert FALLBACK_LIST_PATH not in route_paths(app)


def test_mutating_state_copy_does_not_change_healthz(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.PRODUCTION)
    app.state.runtime_mode = RuntimeMode.REPLAY
    response = TestClient(app).get("/healthz")
    # /healthz reports the immutable construction mode, not the mutable copy.
    assert response.json() == {"status": "ok", "mode": "production"}


def test_production_cannot_become_replay_capable_by_state_mutation(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.PRODUCTION)
    app.state.runtime_mode = RuntimeMode.REPLAY
    client = TestClient(app)
    assert client.get("/session/any/fallback-cases").status_code == 404
    assert client.post("/session/any/fallback/case").status_code == 404
    assert FALLBACK_LIST_PATH not in route_paths(app)
    assert FALLBACK_LOAD_PATH not in route_paths(app)


def test_mutating_state_copy_does_not_change_fallback_available(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.PRODUCTION)
    model_id = upload_bracket(app)
    app.state.interpreter.transport = FailingTransport()
    # Hostile mutation after construction: capability hints must still
    # reflect the immutable construction mode.
    app.state.runtime_mode = RuntimeMode.REPLAY
    response = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": "Fix the bottom face."},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["fallback_available"] is False


def test_multiple_applications_with_different_modes_coexist(tmp_path):
    production = create_app(tmp_path / "p", mode=RuntimeMode.PRODUCTION)
    replay = create_app(tmp_path / "r", mode=RuntimeMode.REPLAY)
    assert FALLBACK_LIST_PATH not in route_paths(production)
    assert FALLBACK_LIST_PATH in route_paths(replay)
    assert production.state.runtime_mode is RuntimeMode.PRODUCTION
    assert replay.state.runtime_mode is RuntimeMode.REPLAY


# --- health endpoint ------------------------------------------------------


@pytest.mark.parametrize("mode", ALL_MODES)
def test_healthz_reports_exactly_status_and_mode(tmp_path, mode: RuntimeMode):
    app = create_app(tmp_path / "models", mode=mode)
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": mode.value}


def test_healthz_leaks_no_environment_or_paths(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.PRODUCTION)
    body = TestClient(app).get("/healthz").text
    assert "KEY" not in body
    assert "\\" not in body and "/" not in body.replace("/healthz", "")
    assert str(tmp_path) not in body


# --- LIVE never substitutes REPLAY ---------------------------------------


def test_production_provider_failure_reports_no_fallback(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.PRODUCTION)
    model_id = upload_bracket(app)
    app.state.interpreter.transport = FailingTransport()
    response = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": "Fix the bottom face."},
    )
    # The failure surfaced as a typed LIVE error; nothing REPLAY was served.
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["mode"] == "LIVE"
    assert detail["fallback_available"] is False


def test_replay_mode_provider_failure_still_never_substitutes(tmp_path):
    app = create_app(tmp_path / "models", mode=RuntimeMode.REPLAY)
    model_id = upload_bracket(app)
    app.state.interpreter.transport = FailingTransport()
    response = request(
        app,
        "POST",
        f"/session/{model_id}/interpret",
        json={"instruction": "Fix the bottom face."},
    )
    # Even where fallback routes exist, a LIVE failure is a typed 503 error;
    # REPLAY data is only ever served by the explicit, labeled fallback route.
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["mode"] == "LIVE"
    assert detail["fallback_available"] is True
