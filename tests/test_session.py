"""Task 10 selection-session state and API tests."""

import asyncio

import httpx
import pytest

from app.server import create_app
from ir.schema import SimulationIntent


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


def minimal_inp(label: str) -> bytes:
    return f"""*HEADING
{label}
*NODE
10, 0, 0, 0
20, 1, 0, 0
30, 0, 1, 0
40, 0, 0, 1
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
""".encode()


def upload(app, filename: str = "one.inp") -> str:
    response = request(
        app,
        "POST",
        "/models",
        content=minimal_inp(filename),
        headers={"X-Filename": filename, "Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def intent_payload(first_ids=None, second_ids=None) -> dict:
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
                "entity_type": "mesh_face",
                "entity_ids": first_ids or [1],
                "selection_method": "user_click",
                "confidence": 1.0,
                "source_instruction": "fix this face",
                "status": "proposed",
            },
            {
                "id": "loaded_region",
                "entity_type": "mesh_face",
                "entity_ids": second_ids or [2],
                "selection_method": "semantic_geometry_query",
                "confidence": 0.9,
                "source_instruction": "load that face",
                "status": "proposed",
            },
        ],
        "bcs": [
            {
                "type": "fixed_displacement",
                "region_ref": "fixed_region",
                "components": ["x", "y", "z"],
            }
        ],
        "loads": [
            {
                "type": "resultant_surface_force",
                "region_ref": "loaded_region",
                "vector": [0, -5000, 0],
            }
        ],
        "assumptions": [],
        "validation_status": "unvalidated",
    }


@pytest.fixture
def session_app(tmp_path):
    return create_app(tmp_path / "models")


def save_intent(app, model_id: str, payload=None) -> dict:
    response = request(
        app,
        "PUT",
        f"/session/{model_id}/intent",
        json=payload or intent_payload(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def transition(app, model_id: str, action: str, region_id: str) -> httpx.Response:
    return request(
        app,
        "POST",
        f"/session/{model_id}/{action}_region",
        json={"region_id": region_id},
    )


def test_uploaded_model_session_is_lazily_created_and_retrieved(session_app):
    model_id = upload(session_app)
    first = request(session_app, "GET", f"/session/{model_id}/intent")
    second = request(session_app, "GET", f"/session/{model_id}/intent")

    assert first.status_code == 200
    assert first.json() == second.json() == {
        "session_id": model_id,
        "model_id": model_id,
        "intent": None,
        "selected_entities": {},
        "highlight_state": {},
        "export_eligible": False,
    }


def test_ir_draft_round_trips_and_populates_selection_state(session_app):
    model_id = upload(session_app)
    saved = save_intent(session_app, model_id)
    retrieved = request(session_app, "GET", f"/session/{model_id}/intent")

    assert retrieved.status_code == 200
    assert retrieved.json() == saved
    assert saved["intent"] == SimulationIntent.model_validate(
        intent_payload()
    ).model_dump(mode="json")
    assert saved["selected_entities"] == {
        "fixed_region": [1],
        "loaded_region": [2],
    }
    assert saved["highlight_state"]["fixed_region"]["style"] == "proposed"
    assert saved["export_eligible"] is False


def test_proposed_region_can_be_confirmed(session_app):
    model_id = upload(session_app)
    save_intent(session_app, model_id)

    response = transition(session_app, model_id, "confirm", "fixed_region")

    assert response.status_code == 200
    state = response.json()
    assert state["intent"]["regions"][0]["status"] == "confirmed"
    assert state["highlight_state"]["fixed_region"]["style"] == "confirmed"
    assert state["export_eligible"] is False


def test_proposed_region_can_be_rejected_and_is_reopened(session_app):
    model_id = upload(session_app)
    save_intent(session_app, model_id)

    rejected = transition(session_app, model_id, "reject", "fixed_region")

    assert rejected.status_code == 200
    state = rejected.json()
    assert state["intent"]["regions"][0]["status"] == "rejected"
    assert "fixed_region" not in state["selected_entities"]
    assert "fixed_region" not in state["highlight_state"]
    assert state["export_eligible"] is False

    corrected = state["intent"]
    corrected["regions"][0]["entity_ids"] = [3]
    corrected["regions"][0]["status"] = "proposed"
    reopened = save_intent(session_app, model_id, corrected)
    assert reopened["intent"]["regions"][0]["status"] == "proposed"
    assert reopened["selected_entities"]["fixed_region"] == [3]
    assert reopened["highlight_state"]["fixed_region"]["style"] == "proposed"
    assert reopened["export_eligible"] is False


def test_all_regions_confirmed_is_export_eligible_via_existing_gate(session_app):
    model_id = upload(session_app)
    save_intent(session_app, model_id)

    first = transition(session_app, model_id, "confirm", "fixed_region")
    final = transition(session_app, model_id, "confirm", "loaded_region")

    assert first.json()["export_eligible"] is False
    assert final.status_code == 200
    state = final.json()
    assert [region["status"] for region in state["intent"]["regions"]] == [
        "confirmed",
        "confirmed",
    ]
    assert state["export_eligible"] is True
    SimulationIntent.model_validate(state["intent"]).export_payload()


def test_unknown_model_missing_draft_and_region_errors_are_clean(session_app):
    unknown = "0" * 64
    response = request(session_app, "GET", f"/session/{unknown}/intent")
    assert response.status_code == 404
    assert response.json()["detail"] == "model not found"

    model_id = upload(session_app)
    no_draft = transition(session_app, model_id, "confirm", "fixed_region")
    assert no_draft.status_code == 409
    assert no_draft.json()["detail"] == "session has no intent draft"

    save_intent(session_app, model_id)
    missing = transition(session_app, model_id, "confirm", "missing")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "region 'missing' not found"


def test_invalid_ir_and_state_transitions_are_clean(session_app):
    model_id = upload(session_app)
    invalid = intent_payload()
    invalid["loads"][0]["region_ref"] = "missing"
    response = request(
        session_app, "PUT", f"/session/{model_id}/intent", json=invalid
    )
    assert response.status_code == 422

    direct_confirmation = intent_payload()
    direct_confirmation["regions"][0]["status"] = "confirmed"
    response = request(
        session_app,
        "PUT",
        f"/session/{model_id}/intent",
        json=direct_confirmation,
    )
    assert response.status_code == 409
    assert "server-managed" in response.json()["detail"]

    save_intent(session_app, model_id)
    assert transition(session_app, model_id, "confirm", "fixed_region").status_code == 200
    repeated = transition(session_app, model_id, "confirm", "fixed_region")
    assert repeated.status_code == 409
    assert "only proposed regions" in repeated.json()["detail"]


def test_rejected_region_cannot_be_removed_to_bypass_export_gate(session_app):
    model_id = upload(session_app)
    save_intent(session_app, model_id)
    rejected = transition(session_app, model_id, "reject", "fixed_region").json()
    assert transition(
        session_app, model_id, "confirm", "loaded_region"
    ).status_code == 200

    without_rejected = rejected["intent"]
    without_rejected["regions"] = [without_rejected["regions"][1]]
    without_rejected["bcs"] = []
    response = request(
        session_app,
        "PUT",
        f"/session/{model_id}/intent",
        json=without_rejected,
    )

    assert response.status_code == 409
    assert "cannot be removed" in response.json()["detail"]
    current = request(session_app, "GET", f"/session/{model_id}/intent").json()
    assert current["intent"]["regions"][0]["status"] == "rejected"
    assert current["export_eligible"] is False


def test_sessions_are_isolated_between_uploaded_models(session_app):
    first_id = upload(session_app, "first.inp")
    second_id = upload(session_app, "second.inp")
    assert first_id != second_id

    first_state = save_intent(
        session_app, first_id, intent_payload(first_ids=[1], second_ids=[2])
    )
    second_empty = request(session_app, "GET", f"/session/{second_id}/intent")
    assert first_state["intent"] is not None
    assert second_empty.status_code == 200
    assert second_empty.json()["intent"] is None

    second_state = save_intent(
        session_app, second_id, intent_payload(first_ids=[3], second_ids=[4])
    )
    transition(session_app, first_id, "confirm", "fixed_region")

    first_after = request(session_app, "GET", f"/session/{first_id}/intent").json()
    second_after = request(session_app, "GET", f"/session/{second_id}/intent").json()
    assert first_after["selected_entities"]["fixed_region"] == [1]
    assert first_after["intent"]["regions"][0]["status"] == "confirmed"
    assert second_after == second_state
    assert second_after["selected_entities"]["fixed_region"] == [3]
    assert second_after["intent"]["regions"][0]["status"] == "proposed"
