"""Task 9 frontend-serving and interaction-contract tests."""

import asyncio
import logging

import httpx
import pytest

from app.server import create_app


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


@pytest.fixture
def viewer_app(tmp_path):
    return create_app(tmp_path / "models")


def test_frontend_and_static_assets_are_served(viewer_app):
    index = request(viewer_app, "GET", "/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "sim-intent viewer" in index.text
    assert 'src="/static/app.js"' in index.text
    assert "cdn.jsdelivr.net/npm/three" in index.text

    javascript = request(viewer_app, "GET", "/static/app.js")
    assert javascript.status_code == 200
    assert "GLTFLoader" in javascript.text
    assert 'fetch("/select"' in javascript.text
    assert 'new EventSource("/events")' in javascript.text

    stylesheet = request(viewer_app, "GET", "/static/styles.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")


def test_select_records_node_name(viewer_app, caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = request(viewer_app, "POST", "/select", json={"entity_id": 11})

    assert response.status_code == 200
    assert response.json() == {"entity_id": 11, "node_name": "face_11"}
    assert "Viewer selection recorded: face_11" in caplog.text


@pytest.mark.parametrize(
    ("sent_style", "canonical_style"),
    [
        ("confirmed", "confirmed"),
        ("proposed", "proposed"),
        ("candidate", "candidate"),
        ("fixed boundary condition", "fixed_boundary_condition"),
        ("fixed-BC", "fixed_boundary_condition"),
        ("load direction", "load_direction"),
        ("load", "load_direction"),
    ],
)
def test_highlight_accepts_all_task_9_styles(viewer_app, sent_style, canonical_style):
    response = request(
        viewer_app,
        "POST",
        "/highlight",
        json={"entity_ids": [11, 12], "style": sent_style},
    )
    assert response.status_code == 200
    assert response.json() == {
        "entity_ids": [11, 12],
        "style": canonical_style,
    }


def test_load_highlight_accepts_additive_direction_vector(viewer_app):
    response = request(
        viewer_app,
        "POST",
        "/highlight",
        json={"entity_ids": [4], "style": "load direction", "vector": [0, -1, 0]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "entity_ids": [4],
        "style": "load_direction",
        "vector": [0.0, -1.0, 0.0],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"entity_id": 0},
        {"entity_id": 11, "session_id": "task-10-scope-creep"},
    ],
)
def test_select_rejects_invalid_contract_payloads(viewer_app, payload):
    assert request(viewer_app, "POST", "/select", json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"entity_ids": [], "style": "confirmed"},
        {"entity_ids": [11, 11], "style": "confirmed"},
        {"entity_ids": [11], "style": "unknown"},
        {"entity_ids": [11], "style": "load", "vector": [0, 0, 0]},
        {"entity_ids": [11], "style": "load", "intent": {}},
    ],
)
def test_highlight_rejects_invalid_or_out_of_scope_payloads(viewer_app, payload):
    assert request(viewer_app, "POST", "/highlight", json=payload).status_code == 422
