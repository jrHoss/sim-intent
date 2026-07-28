"""Task 19 legacy-route regression tests for schema versioning.

Decision D-2 allows exactly one compatibility exception: the frozen legacy
``PUT /session/{session_id}/intent`` route may normalise an *absent*
``schema_version`` through a route-scoped constant.  These tests pin that
behaviour, prove a declared version is never rewritten, and prove the frozen
route contract and error envelope are otherwise unchanged (decision D-4).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.schema_compat import LEGACY_UNVERSIONED_INTENT_VERSION
from app.server import create_app
from ir.schema_version import SCHEMA_VERSION_FIELD, SIMULATION_INTENT_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]

MINIMAL_INP = """*HEADING
task19 schema version route
*NODE
10, 0, 0, 0
20, 1, 0, 0
30, 0, 1, 0
40, 0, 0, 1
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
"""


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


@pytest.fixture
def session_app(tmp_path):
    return create_app(tmp_path / "models")


@pytest.fixture
def model_id(session_app) -> str:
    response = request(
        session_app,
        "POST",
        "/models",
        content=MINIMAL_INP.encode(),
        headers={
            "X-Filename": "task19.inp",
            "Content-Type": "application/octet-stream",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def unversioned_intent() -> dict[str, Any]:
    return {
        "analysis": {
            "type": "static_structural",
            "units": {"length": "mm", "force": "N", "stress": "MPa"},
        },
        "materials": [
            {
                "name": "steel",
                "model": "linear_elastic_isotropic",
                "E_MPa": 210000,
                "nu": 0.3,
            }
        ],
        "regions": [
            {
                "id": "fixed_region",
                "entity_type": "node_set",
                "entity_ids": [1],
                "selection_method": "user_click",
                "confidence": 0.9,
                "source_instruction": "Fix the base.",
                "status": "proposed",
            }
        ],
        "bcs": [
            {
                "type": "fixed_displacement",
                "region_ref": "fixed_region",
                "components": ["x", "y", "z"],
            }
        ],
        "loads": [],
        "assumptions": [],
        "validation_status": "unvalidated",
    }


# --------------------------------------------------------------------------
# The one approved compatibility exception
# --------------------------------------------------------------------------


def test_legacy_put_accepts_an_unversioned_body(session_app, model_id):
    response = request(
        session_app,
        "PUT",
        f"/session/{model_id}/intent",
        json=unversioned_intent(),
    )
    assert response.status_code == 200, response.text
    saved = response.json()["intent"]
    # The route normalises an absent version to the legacy version, then the
    # authoritative loader migrates it forward; writes emit only the current
    # version.
    assert LEGACY_UNVERSIONED_INTENT_VERSION == 1
    assert saved[SCHEMA_VERSION_FIELD] == SIMULATION_INTENT_SCHEMA_VERSION


def test_legacy_unversioned_body_gains_no_engineering_decisions(
    session_app, model_id
):
    """The compatibility exception must not approve anything by migrating."""

    response = request(
        session_app,
        "PUT",
        f"/session/{model_id}/intent",
        json=unversioned_intent(),
    )
    assert response.status_code == 200, response.text
    saved = response.json()["intent"]
    assert saved["analysis"]["dimensionality"] is None
    assert saved["analysis"]["solver_target"] is None
    assert saved["analysis"]["coordinate_system"] is None
    assert saved["mesh_settings"] is None
    assert saved["solver_settings"] is None
    assert response.json()["export_eligible"] is False


def test_legacy_put_accepts_an_explicit_current_version(session_app, model_id):
    payload = unversioned_intent()
    payload[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION
    response = request(
        session_app, "PUT", f"/session/{model_id}/intent", json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["intent"][SCHEMA_VERSION_FIELD] == (
        SIMULATION_INTENT_SCHEMA_VERSION
    )


def test_legacy_put_rejects_an_unsupported_future_version(session_app, model_id):
    payload = unversioned_intent()
    payload[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_SCHEMA_VERSION + 1
    response = request(
        session_app, "PUT", f"/session/{model_id}/intent", json=payload
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "schema_version_unsupported_future"


def test_future_version_write_is_not_persisted(session_app, model_id):
    payload = unversioned_intent()
    payload[SCHEMA_VERSION_FIELD] = 99
    assert (
        request(
            session_app, "PUT", f"/session/{model_id}/intent", json=payload
        ).status_code
        == 422
    )
    snapshot = request(session_app, "GET", f"/session/{model_id}/intent")
    assert snapshot.status_code == 200
    assert snapshot.json()["intent"] is None


@pytest.mark.parametrize("declared", ["1", 1.5, None, 0, -1, [1]])
def test_legacy_put_rejects_a_malformed_version(session_app, model_id, declared):
    payload = unversioned_intent()
    payload[SCHEMA_VERSION_FIELD] = declared
    response = request(
        session_app, "PUT", f"/session/{model_id}/intent", json=payload
    )
    # Either FastAPI's own frozen 422 envelope or the typed loader rejects it;
    # both are 422 and neither writes state (D-4 keeps the legacy shapes).
    assert response.status_code == 422


def test_get_intent_returns_the_declared_version(session_app, model_id):
    request(
        session_app, "PUT", f"/session/{model_id}/intent", json=unversioned_intent()
    )
    response = request(session_app, "GET", f"/session/{model_id}/intent")
    assert response.status_code == 200
    assert response.json()["intent"][SCHEMA_VERSION_FIELD] == (
        SIMULATION_INTENT_SCHEMA_VERSION
    )


def test_region_transition_preserves_the_schema_version(session_app, model_id):
    request(
        session_app, "PUT", f"/session/{model_id}/intent", json=unversioned_intent()
    )
    response = request(
        session_app,
        "POST",
        f"/session/{model_id}/confirm_region",
        json={"region_id": "fixed_region"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["intent"][SCHEMA_VERSION_FIELD] == (
        SIMULATION_INTENT_SCHEMA_VERSION
    )


# --------------------------------------------------------------------------
# The frozen request contract is unchanged
# --------------------------------------------------------------------------


def test_legacy_put_request_contract_is_still_the_typed_model(session_app):
    schema = session_app.openapi()
    operation = schema["paths"]["/session/{session_id}/intent"]["put"]
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"].endswith("/SimulationIntent")
    # Reading the raw body for the compatibility check must not turn the
    # request body into an untyped payload.
    assert "SimulationIntent" in schema["components"]["schemas"]


def test_schema_version_is_optional_in_the_published_request_schema(session_app):
    schema = session_app.openapi()
    intent_schema = schema["components"]["schemas"]["SimulationIntent"]
    assert SCHEMA_VERSION_FIELD in intent_schema["properties"]
    assert SCHEMA_VERSION_FIELD not in intent_schema.get("required", [])


# --------------------------------------------------------------------------
# The REPLAY fallback route uses the authoritative loader
# --------------------------------------------------------------------------


def test_fallback_route_rejects_an_unversioned_record(tmp_path, monkeypatch):
    """A fallback file without a declared version must not load."""

    from app import record_versions

    record = json.loads(
        (ROOT / "eval" / "fallback" / "bracket_bottom_fixed.json").read_text(
            encoding="utf-8"
        )
    )
    record.pop(SCHEMA_VERSION_FIELD)
    with pytest.raises(Exception) as exc:
        record_versions.load_fallback_record(record, source="t")
    assert "schema_version" in str(exc.value)
