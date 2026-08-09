from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from server.app import create_app


async def _request(
    app: Any,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: str = "",
) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {
                "type": "http.request",
                "body": json.dumps(body).encode() if body is not None else b"",
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"host", b"127.0.0.1:3100")]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query.encode("ascii"),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 3100),
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    response_body = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return start["status"], response_body


def _solve_body(delay_ms: int = 1) -> dict[str, Any]:
    return {
        "design": {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "simulation": {"f1": 300, "f2": 3000, "num_frequencies": 4},
        },
        "options": {"engine": "dryrun", "stage_delay_ms": delay_ms},
    }


def test_openapi_documents_complete_jobs_surface(tmp_path: Path) -> None:
    schema = create_app(data_dir=tmp_path).openapi()
    paths = schema["paths"]
    assert {
        "/api/solve",
        "/api/stop/{job_id}",
        "/api/status/{job_id}",
        "/api/results/{job_id}",
        "/api/mesh-artifact/{job_id}",
        "/api/jobs",
        "/api/jobs/{job_id}/metadata",
        "/api/jobs/clear-failed",
        "/api/jobs/{job_id}",
    } <= set(paths)
    solve_schema = paths["/api/solve"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert "$ref" in solve_schema
    job_properties = schema["components"]["schemas"]["JobItem"]["properties"]
    assert {"run_number", "parent_job_id"} <= set(job_properties)


def test_dryrun_http_lifecycle_metadata_results_and_delete(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        solve_body = _solve_body()
        solve_body["parent_job_id"] = "parent-job"
        status, raw = await _request(app, "POST", "/api/solve", body=solve_body)
        assert status == 200
        job_id = json.loads(raw)["job_id"]
        await app.state.jobs_runtime.wait_idle()

        status, raw = await _request(app, "GET", f"/api/status/{job_id}")
        assert status == 200
        detail = json.loads(raw)
        assert detail["status"] == "complete"
        assert detail["run_number"] == 1
        assert detail["parent_job_id"] == "parent-job"
        status, raw = await _request(app, "GET", f"/api/results/{job_id}")
        assert status == 200
        assert len(json.loads(raw)["frequencies"]) == 4
        status, raw = await _request(
            app,
            "PATCH",
            f"/api/jobs/{job_id}/metadata",
            body={"label": "  Reference  ", "rating": 5},
        )
        assert status == 200 and json.loads(raw) == {"status": "ok"}
        status, raw = await _request(app, "GET", "/api/jobs", query="limit=10&offset=0")
        item = json.loads(raw)["items"][0]
        assert item["label"] == "Reference" and item["rating"] == 5
        assert item["run_number"] == 1
        assert item["parent_job_id"] == "parent-job"
        status, raw = await _request(app, "DELETE", f"/api/jobs/{job_id}")
        assert status == 200 and json.loads(raw)["deleted"] is True
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_dryrun_submit_is_rejected_when_guard_is_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WG2_ENABLE_DRYRUN", raising=False)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        status, raw = await _request(app, "POST", "/api/solve", body=_solve_body())
        assert status == 503
        assert "WG2_ENABLE_DRYRUN=1" in json.loads(raw)["detail"]
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())
