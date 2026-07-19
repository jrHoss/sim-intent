"""Task 8 DoD tests: upload/inventory API and per-face glTF backend."""

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from app.server import FaceMesh, _build_gltf, create_app
from geom.inventory import get_inventory

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRACKET = FIXTURES / "bracket.step"


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_request(app, method, path, **kwargs))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    return create_app(tmp_path_factory.mktemp("server_models"))


@pytest.fixture(scope="module")
def uploaded_bracket(server):
    response = request(
        server,
        "POST",
        "/models",
        files={"file": ("bracket.step", BRACKET.read_bytes(), "application/step")},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def bracket_gltf(server, uploaded_bracket):
    response = request(server, "GET", f"/models/{uploaded_bracket['id']}/gltf")
    assert response.status_code == 200, response.text
    return response


def test_upload_bracket_is_content_addressed(server, uploaded_bracket):
    assert len(uploaded_bracket["id"]) == 64
    assert uploaded_bracket["source_name"] == "bracket.step"
    assert uploaded_bracket["kind"] == "step"

    repeated = request(
        server,
        "POST",
        "/models",
        files={"file": ("bracket.step", BRACKET.read_bytes(), "application/step")},
    )
    assert repeated.status_code == 201
    assert repeated.json() == uploaded_bracket


def test_bracket_inventory_matches_task_2(server, uploaded_bracket, tmp_path):
    expected, _ = get_inventory(BRACKET, cache_dir=tmp_path / "expected-cache")
    response = request(server, "GET", f"/models/{uploaded_bracket['id']}/inventory")
    assert response.status_code == 200
    assert response.json() == expected.to_dict()


def test_singular_frozen_contract_aliases(server, uploaded_bracket):
    plural = request(server, "GET", f"/models/{uploaded_bracket['id']}/inventory")
    singular = request(server, "GET", f"/model/{uploaded_bracket['id']}/inventory")
    assert singular.status_code == 200
    assert singular.json() == plural.json()


def test_bracket_gltf_has_one_named_node_per_face(bracket_gltf):
    assert bracket_gltf.headers["content-type"].startswith("model/gltf+json")
    gltf = bracket_gltf.json()
    names = [node["name"] for node in gltf["nodes"]]
    assert names == [f"face_{tag}" for tag in range(1, 13)]
    assert gltf["scenes"][0]["nodes"] == list(range(12))
    assert [mesh["name"] for mesh in gltf["meshes"]] == names
    assert all(node["mesh"] == index for index, node in enumerate(gltf["nodes"]))


def test_bracket_gltf_embedded_buffer_and_accessors_are_consistent(bracket_gltf):
    gltf = bracket_gltf.json()
    prefix = "data:application/octet-stream;base64,"
    uri = gltf["buffers"][0]["uri"]
    assert uri.startswith(prefix)
    binary = base64.b64decode(uri.removeprefix(prefix), validate=True)
    assert len(binary) == gltf["buffers"][0]["byteLength"]
    assert len(gltf["bufferViews"]) == 24
    assert len(gltf["accessors"]) == 24
    for view in gltf["bufferViews"]:
        assert view["byteOffset"] % 4 == 0
        assert view["byteOffset"] + view["byteLength"] <= len(binary)
    for mesh in gltf["meshes"]:
        primitive = mesh["primitives"][0]
        positions = gltf["accessors"][primitive["attributes"]["POSITION"]]
        indices = gltf["accessors"][primitive["indices"]]
        assert positions["componentType"] == 5126
        assert positions["type"] == "VEC3"
        assert positions["count"] >= 3
        assert indices["componentType"] == 5125
        assert indices["type"] == "SCALAR"
        assert indices["count"] >= 3
        assert indices["count"] % 3 == 0
        assert indices["max"][0] < positions["count"]


def test_gltf_is_deterministic(server, uploaded_bracket, bracket_gltf):
    repeated = request(server, "GET", f"/models/{uploaded_bracket['id']}/gltf")
    assert repeated.status_code == 200
    assert repeated.content == bracket_gltf.content


def _minimal_inp() -> bytes:
    return b"""*HEADING
single tetrahedron
*NODE
10, 0, 0, 0
20, 1, 0, 0
30, 0, 1, 0
40, 0, 0, 1
*ELEMENT, TYPE=C3D4, ELSET=SOLID
100, 10, 20, 30, 40
*NSET, NSET=BASE
10, 20, 30
"""


def test_inp_upload_inventory_and_gltf(server):
    uploaded = request(
        server,
        "POST",
        "/models",
        content=_minimal_inp(),
        headers={"X-Filename": "tetra.inp", "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text
    model_id = uploaded.json()["id"]

    inventory = request(server, "GET", f"/models/{model_id}/inventory")
    assert inventory.status_code == 200
    payload = inventory.json()
    assert payload["format"] == "abaqus"
    assert payload["n_nodes"] == 4
    assert payload["n_elements"] == 1
    assert {region["name"] for region in payload["regions"]} == {"BASE", "SOLID"}

    gltf_response = request(server, "GET", f"/models/{model_id}/gltf")
    assert gltf_response.status_code == 200
    gltf = gltf_response.json()
    assert [node["name"] for node in gltf["nodes"]] == [
        f"face_{group['id']}" for group in payload["facet_groups"]
    ]


@pytest.mark.parametrize(
    ("kwargs", "status", "detail"),
    [
        ({"content": b"data"}, 400, "raw upload requires"),
        (
            {"files": {"wrong": ("bracket.step", BRACKET.read_bytes())}},
            400,
            "requires a 'file' field",
        ),
        ({"files": {"file": ("empty.step", b"")}}, 400, "empty"),
        ({"files": {"file": ("model.obj", b"data")}}, 415, "unsupported model format"),
        ({"files": {"file": ("../bracket.step", BRACKET.read_bytes())}}, 400, "invalid source"),
        ({"files": {"file": ("bad.step", b"not a STEP model")}}, 422, "could not parse"),
    ],
)
def test_upload_errors(server, kwargs, status, detail):
    response = request(server, "POST", "/models", **kwargs)
    assert response.status_code == status
    assert detail in response.json()["detail"]


@pytest.mark.parametrize("path", ["/models/not-an-id/inventory", "/model/" + "0" * 64 + "/gltf"])
def test_unknown_model_is_404(server, path):
    response = request(server, "GET", path)
    assert response.status_code == 404
    assert response.json()["detail"] == "model not found"


def test_gltf_builder_rejects_invalid_geometry():
    with pytest.raises(ValueError, match="without face meshes"):
        _build_gltf([])
    duplicate = FaceMesh(1, [(0.0, 0.0, 0.0)], [0, 0, 0])
    with pytest.raises(ValueError, match="unique"):
        _build_gltf([duplicate, duplicate])
    with pytest.raises(ValueError, match="empty geometry"):
        _build_gltf([FaceMesh(1, [], [])])
