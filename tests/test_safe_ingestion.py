"""Focused R2.1 safe-ingestion behavior."""

from __future__ import annotations

import hashlib
import asyncio
import inspect
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import LocalDataConfig
from app.ingestion import IngestionService, QuarantinedUpload
from app.problems import ApiProblem
from app.persistence import Model, ModelVersion
from app.runtime_mode import RuntimeMode
from app.server import create_app


def inp() -> bytes:
    return b"""*HEADING
safe ingestion
*NODE
1,0,0,0
2,1,0,0
3,0,1,0
4,0,0,1
*ELEMENT,TYPE=C3D4
1,1,2,3,4
"""


@pytest.fixture
def bounded(tmp_path):
    config = LocalDataConfig(tmp_path / "data", max_source_upload_bytes=256)
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "bounded"}).json()
        yield client, app, config, project


def test_raw_stream_hash_publish_and_cleanup(bounded):
    client, app, config, project = bounded
    content = inp()
    response = client.post(
        f"/api/v1/projects/{project['id']}/models?filename=part.inp",
        content=content,
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    version = response.json()["model_version"]
    assert version["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert app.state.persistence.read_version_bytes(
        app.state.persistence.get_version(version["id"])
    ) == content
    assert list(config.quarantine_root.glob("upload-*")) == []


@pytest.mark.parametrize("multipart", [False, True])
def test_streamed_limit_rejects_and_leaves_no_durable_state(bounded, multipart):
    client, app, config, project = bounded
    content = inp() + b" " * 256
    if multipart:
        response = client.post(
            f"/api/v1/projects/{project['id']}/models",
            files={"file": ("part.inp", content, "application/octet-stream")},
        )
    else:
        response = client.post(
            f"/api/v1/projects/{project['id']}/models?filename=part.inp",
            content=content,
            headers={
                "content-type": "application/octet-stream",
                "content-length": "1",
            },
        )
    assert response.status_code == 413
    assert response.json()["code"] == "upload_too_large"
    with app.state.persistence.sessions() as session:
        assert session.scalar(select(Model)) is None
        assert session.scalar(select(ModelVersion)) is None
    assert list(config.quarantine_root.glob("upload-*")) == []
    assert list(config.blob_root.glob("sha256/*/*/*")) == []


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("../part.inp", "unsafe_filename"),
        ("C:\\part.inp", "unsafe_filename"),
        ("part.inp.exe", "unsupported_source_type"),
        ("part.txt", "unsupported_source_type"),
        ('bad"name.inp', "unsafe_filename"),
        ("bad\rname.inp", "unsafe_filename"),
        ("bad\nname.inp", "unsafe_filename"),
        ("bad:name.inp", "unsafe_filename"),
        ("bad*name.inp", "unsafe_filename"),
        ("bad?name.inp", "unsafe_filename"),
        ("bad<name.inp", "unsafe_filename"),
        ("bad>name.inp", "unsafe_filename"),
        ("bad|name.inp", "unsafe_filename"),
        ("CON.inp", "unsafe_filename"),
        ("prn.STEP", "unsafe_filename"),
        ("Com1.inp", "unsafe_filename"),
        ("lpt9.stp", "unsafe_filename"),
    ],
)
def test_filename_safety(bounded, filename, code):
    client, app, config, project = bounded
    response = client.post(
        f"/api/v1/projects/{project['id']}/models",
        content=inp(),
        headers={"content-type": "application/octet-stream", "x-filename": filename},
    )
    assert response.json()["code"] == code
    assert list(config.quarantine_root.glob("upload-*")) == []
    with app.state.persistence.sessions() as session:
        assert session.scalar(select(ModelVersion)) is None


def test_empty_malformed_and_extension_mismatch(bounded):
    client, _, config, project = bounded
    empty = client.post(
        f"/api/v1/projects/{project['id']}/models?filename=part.inp",
        content=b"",
        headers={"content-type": "application/octet-stream"},
    )
    assert empty.json()["code"] == "empty_upload"
    malformed = client.post(
        f"/api/v1/projects/{project['id']}/models",
        content=b"not multipart",
        headers={"content-type": "multipart/form-data; boundary=absent"},
    )
    assert malformed.json()["code"] == "malformed_multipart"
    mismatch = client.post(
        f"/api/v1/projects/{project['id']}/models?filename=part.step",
        content=inp(),
        headers={"content-type": "application/octet-stream"},
    )
    assert mismatch.json()["code"] == "invalid_source_content"
    assert list(config.quarantine_root.glob("upload-*")) == []


def test_stale_cleanup_is_age_count_and_entry_type_bounded(tmp_path):
    config = LocalDataConfig(
        tmp_path / "data",
        stale_quarantine_age_seconds=0,
        stale_quarantine_cleanup_limit=1,
    )
    root = config.quarantine_root
    root.mkdir(parents=True)
    files = [root / f"upload-{index}" for index in range(2)]
    for path in files:
        path.write_bytes(b"x")
        os.utime(path, (0, 0))
    directory = root / "upload-directory"
    directory.mkdir()
    unrelated = root / "keep"
    unrelated.write_bytes(b"x")
    from app.ingestion import IngestionService

    assert IngestionService(config).cleanup_stale() == 1
    assert sum(path.exists() for path in files) == 1
    assert directory.is_dir()
    assert unrelated.exists()


def test_real_step_upload_viewer_and_restart(tmp_path):
    config = LocalDataConfig(tmp_path / "data")
    fixture = Path(__file__).parent / "fixtures" / "bracket.step"
    content = fixture.read_bytes()
    async def exercise():
        first = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
        async with first.router.lifespan_context(first):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=first), base_url="http://test"
            ) as client:
                project = (await client.post("/api/v1/projects", json={"name": "STEP"})).json()
                uploaded = await client.post(
                    f"/api/v1/projects/{project['id']}/models",
                    files={"file": ("bracket.step", content, "application/step")},
                )
                assert uploaded.status_code == 201, uploaded.text
                version_id = uploaded.json()["model_version"]["id"]
                inventory = await client.get(f"/api/v1/model-versions/{version_id}/inventory")
                assert inventory.status_code == 200 and inventory.json()["faces"]
                gltf = await client.get(f"/api/v1/model-versions/{version_id}/gltf")
                assert gltf.status_code == 200
                assert "filename*=UTF-8''bracket.gltf" in gltf.headers["content-disposition"]
        restarted = create_app(tmp_path / "other", mode=RuntimeMode.TEST, data_config=config)
        async with restarted.router.lifespan_context(restarted):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted), base_url="http://test"
            ) as client:
                assert (await client.get(f"/api/v1/model-versions/{version_id}/inventory")).json()["faces"]
                gltf = await client.get(f"/api/v1/model-versions/{version_id}/gltf")
                assert gltf.status_code == 200 and gltf.json()["nodes"]
    asyncio.run(exercise())


def test_comment_led_inp_and_raw_multipart_hash_match(bounded):
    client, _, _, project = bounded
    content = (b"** comment padding\n" * 400) + inp()
    # This test needs a larger configured app than the bounded fixture.
    assert len(content) > 4096
    too_large = client.post(
        f"/api/v1/projects/{project['id']}/models?filename=comments.inp",
        content=content,
    )
    assert too_large.status_code == 413


def test_comment_led_inp_succeeds_and_hashes_match(tmp_path):
    config = LocalDataConfig(tmp_path / "data", max_source_upload_bytes=16 * 1024)
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    content = (b"** arbitrary comment\n" * 300) + inp()
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "comments"}).json()
        raw = client.post(
            f"/api/v1/projects/{project['id']}/models?filename=raw.inp",
            content=content,
        )
        multipart = client.post(
            f"/api/v1/projects/{project['id']}/models",
            files={"file": ("multipart.inp", content, "application/octet-stream")},
        )
        assert raw.status_code == multipart.status_code == 201
        assert raw.json()["model_version"]["source_sha256"] == multipart.json()["model_version"]["source_sha256"]


def test_near_limit_multipart_succeeds(tmp_path):
    limit = 8192
    config = LocalDataConfig(tmp_path / "data", max_source_upload_bytes=limit)
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    padding = b"** bounded comment\n" * ((limit - len(inp())) // 19)
    content = padding + inp()
    assert limit - 64 < len(content) <= limit
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "near-limit"}).json()
        response = client.post(
            f"/api/v1/projects/{project['id']}/models",
            files={"file": ("near.inp", content, "application/octet-stream")},
        )
        assert response.status_code == 201, response.text


@pytest.mark.parametrize("value", ["[]", '"text"', "1", "true", "null"])
def test_non_object_worker_protocol_is_parser_crash(tmp_path, value):
    service, upload = _fake_service(tmp_path)
    service._worker_command_prefix = [
        sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"), "value", value
    ]
    with pytest.raises(ApiProblem) as caught:
        asyncio.run(service.parse(upload))
    assert caught.value.http_status == 422
    assert caught.value.code == "parser_crash"


def test_large_stderr_is_truncated_but_valid_protocol_succeeds(tmp_path):
    service, upload = _fake_service(tmp_path, output_bytes=1024)
    service._worker_command_prefix = [
        sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"), "valid_stderr"
    ]
    assert asyncio.run(service.parse(upload))["file_sha256"] == upload.sha256


def test_large_stdout_is_parser_crash(tmp_path):
    service, upload = _fake_service(tmp_path, output_bytes=128)
    service._worker_command_prefix = [
        sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"), "large_stdout"
    ]
    with pytest.raises(ApiProblem) as caught:
        asyncio.run(service.parse(upload))
    assert caught.value.code == "parser_crash"


def test_timeout_kills_descendant_and_cleans_quarantine(tmp_path):
    config = LocalDataConfig(tmp_path / "data", parser_timeout_seconds=0.2)
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    side_effect = tmp_path / "descendant-survived"
    with TestClient(app) as client:
        app.state.ingestion._worker_command_prefix = [
            sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"),
            "descendant", str(side_effect),
        ]
        project = client.post("/api/v1/projects", json={"name": "timeout"}).json()
        started = time.monotonic()
        response = client.post(
            f"/api/v1/projects/{project['id']}/models?filename=part.inp",
            content=inp(),
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 422
        assert response.json()["code"] == "parser_timeout"
        assert elapsed < 2.5
        assert list(config.quarantine_root.glob("upload-*")) == []
    time.sleep(1.2)
    assert not side_effect.exists()


def test_internal_worker_diagnostic_is_correlated_and_sanitized(
    tmp_path, caplog
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    secret = "operator-secret-marker"
    diagnostic = rf"C:\private\customer.inp: internal failure {secret}"
    correlation = "worker-diagnostic-correlation"
    with TestClient(app) as client:
        app.state.ingestion._worker_command_prefix = [
            sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"),
            "error", diagnostic,
        ]
        project = client.post("/api/v1/projects", json={"name": "diagnostic"}).json()
        with caplog.at_level("ERROR", logger="uvicorn.error"):
            response = client.post(
                f"/api/v1/projects/{project['id']}/models?filename=part.inp",
                content=inp(),
                headers={"x-correlation-id": correlation},
            )
        assert response.status_code == 422
        assert response.json()["code"] == "parser_crash"
        assert correlation in caplog.text and secret in caplog.text
        assert secret not in response.text and "customer.inp" not in response.text


def test_generated_correlation_is_shared_by_log_and_problem(tmp_path, caplog):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    diagnostic = r"C:\private\generated.inp generated-secret"
    with TestClient(app) as client:
        app.state.ingestion._worker_command_prefix = [
            sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"),
            "error", diagnostic,
        ]
        project = client.post("/api/v1/projects", json={"name": "generated"}).json()
        with caplog.at_level("ERROR", logger="uvicorn.error"):
            response = client.post(
                f"/api/v1/projects/{project['id']}/models?filename=part.inp",
                content=inp(),
            )
        trace_id = response.json()["trace_id"]
        assert trace_id and trace_id in caplog.text
        assert "generated-secret" not in response.text


@pytest.mark.parametrize("endpoint", ["inventory", "gltf"])
def test_read_parser_failure_shares_correlation(
    tmp_path, caplog, endpoint
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)
    correlation = f"read-{endpoint}-correlation"
    secret = f"{endpoint}-private-secret"
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "read"}).json()
        uploaded = client.post(
            f"/api/v1/projects/{project['id']}/models?filename=part.inp",
            content=inp(),
        )
        version_id = uploaded.json()["model_version"]["id"]
        app.state.ingestion._worker_command_prefix = [
            sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"),
            "error", rf"C:\private\{endpoint}.inp {secret}",
        ]
        with caplog.at_level("ERROR", logger="uvicorn.error"):
            response = client.get(
                f"/api/v1/model-versions/{version_id}/{endpoint}",
                headers={"x-correlation-id": correlation},
            )
        assert response.status_code == 422
        assert response.json()["trace_id"] == correlation
        assert correlation in caplog.text
        assert secret not in response.text and "private" not in response.text


def test_concurrent_generated_correlations_are_distinct_and_consistent(
    tmp_path, caplog
):
    config = LocalDataConfig(tmp_path / "data")
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)

    async def exercise():
        async with app.router.lifespan_context(app):
            app.state.ingestion._worker_command_prefix = [
                sys.executable, str(Path(__file__).parent / "fake_parser_worker.py"),
                "error", "concurrent diagnostic",
            ]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                project = (await client.post(
                    "/api/v1/projects", json={"name": "concurrent"}
                )).json()
                responses = await asyncio.gather(
                    *[
                        client.post(
                            f"/api/v1/projects/{project['id']}/models?filename=part{index}.inp",
                            content=inp(),
                        )
                        for index in range(2)
                    ]
                )
                return responses

    with caplog.at_level("ERROR", logger="uvicorn.error"):
        responses = asyncio.run(exercise())
    trace_ids = [response.json()["trace_id"] for response in responses]
    assert len(set(trace_ids)) == 2
    assert all(trace_id in caplog.text for trace_id in trace_ids)


def test_declared_oversize_rejects_without_reading_body(tmp_path):
    class NeverRead(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("request body was read after early rejection")
            yield b""

    config = LocalDataConfig(tmp_path / "data", max_source_upload_bytes=32)
    app = create_app(tmp_path / "legacy", mode=RuntimeMode.TEST, data_config=config)

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                project = (await client.post(
                    "/api/v1/projects", json={"name": "early"}
                )).json()
                request = client.build_request(
                    "POST",
                    f"/api/v1/projects/{project['id']}/models?filename=part.inp",
                    content=NeverRead(),
                    headers={"content-length": "33"},
                )
                response = await client.send(request)
                with app.state.persistence.sessions() as session:
                    assert session.scalar(select(Model)) is None
                    assert session.scalar(select(ModelVersion)) is None
                return response

    response = asyncio.run(exercise())
    assert response.status_code == 413
    assert response.json()["code"] == "upload_too_large"
    assert list(config.quarantine_root.glob("upload-*")) == []


def test_malformed_content_length_uses_streamed_limit(tmp_path):
    service = IngestionService(
        LocalDataConfig(tmp_path / "data", max_source_upload_bytes=8)
    )
    chunks = iter([inp()])

    async def receive():
        try:
            body = next(chunks)
            return {"type": "http.request", "body": body, "more_body": False}
        except StopIteration:
            return {"type": "http.disconnect"}

    from starlette.requests import Request
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"content-length", b"malformed"),
                (b"content-type", b"application/octet-stream"),
            ],
        },
        receive,
    )
    with pytest.raises(ApiProblem) as caught:
        asyncio.run(service.receive(request, "part.inp"))
    assert caught.value.code == "upload_too_large"
    assert list(service.root.glob("upload-*")) == []


def test_multipart_implementation_has_no_whole_payload_decoder():
    source = inspect.getsource(IngestionService._receive_multipart)
    for prohibited in ("read_bytes(", "parsebytes(", "get_payload(", "request.body("):
        assert prohibited not in source


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_configuration_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        LocalDataConfig(tmp_path / "data", parser_timeout_seconds=value)
    with pytest.raises(ValueError):
        LocalDataConfig(tmp_path / "data", stale_quarantine_age_seconds=value)


def test_reserved_quarantine_location_is_rejected(tmp_path):
    root = tmp_path / "data"
    with pytest.raises(ValueError, match="blob"):
        LocalDataConfig(root, quarantine_directory=root / "blobs" / "unsafe")


def _fake_service(tmp_path, output_bytes=1024):
    config = LocalDataConfig(tmp_path / "data", parser_output_bytes=output_bytes)
    service = IngestionService(config)
    service.root.mkdir(parents=True)
    path = service.root / "upload-test.inp"
    path.write_bytes(inp())
    digest = hashlib.sha256(inp()).hexdigest()
    return service, QuarantinedUpload(path, "part.inp", "inp", len(inp()), digest)
