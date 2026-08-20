from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from server.app import create_app
from server.jobs.models import SolveRequest


async def _request_with_headers(
    app: Any,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: str = "",
) -> tuple[int, bytes, dict[str, str]]:
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
    response_headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in start.get("headers", [])
    }
    return start["status"], response_body, response_headers


async def _request(
    app: Any,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: str = "",
) -> tuple[int, bytes]:
    status, response_body, _ = await _request_with_headers(
        app,
        method,
        path,
        body=body,
        query=query,
    )
    return status, response_body


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


def _complete_job(
    job_id: str,
    *,
    request: SolveRequest | None = None,
    stored_design: bool = True,
    mesh_discarded: bool = True,
) -> dict[str, Any]:
    solve_request = request or SolveRequest.model_validate(_solve_body(delay_ms=0))
    config = solve_request.model_dump(mode="json") if stored_design else {}
    now = "2026-08-12T00:00:00"
    return {
        "id": job_id,
        "status": "complete",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "started_at": now,
        "completed_at": now,
        "progress": 1.0,
        "stage": "complete",
        "stage_message": "Complete",
        "config_json": config,
        "config_summary_json": {"formula_type": "OSSE"},
        "has_results": False,
        "has_mesh_artifact": not mesh_discarded,
        "script_snapshot": (
            solve_request.design_snapshot.model_dump(mode="json")
            if stored_design and solve_request.design_snapshot is not None
            else None
        ),
        "task_metadata": (
            {"mesh_discarded_at": now} if mesh_discarded else {}
        ),
    }


def test_openapi_documents_complete_jobs_surface(tmp_path: Path) -> None:
    schema = create_app(data_dir=tmp_path).openapi()
    paths = schema["paths"]
    assert {
        "/api/solve",
        "/api/stop/{job_id}",
        "/api/jobs/{job_id}/retry",
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
    assert {"run_number", "parent_job_id", "solve_options"} <= set(job_properties)


def test_dryrun_http_lifecycle_metadata_results_and_delete(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        solve_body = _solve_body()
        solve_body["parent_job_id"] = "parent-job"
        solve_body["client_request_id"] = "external-run-7"
        solve_body["client_metadata"] = {"campaign": "api-contract"}
        status, raw = await _request(app, "POST", "/api/solve", body=solve_body)
        assert status == 200
        accepted = json.loads(raw)
        job_id = accepted["job_id"]
        assert accepted["client_request_id"] == "external-run-7"
        await app.state.jobs_runtime.wait_idle()

        status, raw = await _request(app, "GET", f"/api/status/{job_id}")
        assert status == 200
        detail = json.loads(raw)
        assert detail["status"] == "complete"
        assert detail["client_request_id"] == "external-run-7"
        assert detail["client_metadata"] == {"campaign": "api-contract"}
        assert detail["run_number"] == 1
        assert detail["parent_job_id"] == "parent-job"
        assert detail["solve_options"]["engine"] == "dryrun"
        assert detail["solve_options"]["stage_delay_ms"] == 1
        assert detail["has_mesh_artifact"] is True
        status, raw = await _request(app, "GET", f"/api/results/{job_id}")
        assert status == 200
        results = json.loads(raw)
        assert len(results["frequencies"]) == 4
        assert results["result_kind"] == "parametric"
        assert results["client_request_id"] == "external-run-7"
        assert results["client_metadata"] == {"campaign": "api-contract"}
        assert results["provenance"]["request_sha256"]
        stored_mesh = app.state.jobs_runtime.store.get_mesh_artifact(job_id)
        assert stored_mesh is not None
        status, raw, headers = await _request_with_headers(
            app, "GET", f"/api/mesh-artifact/{job_id}"
        )
        assert status == 200
        assert raw == stored_mesh.encode("utf-8")
        assert headers["content-type"] == "text/plain; charset=utf-8"
        assert "x-wg2-mesh-regenerated" not in headers
        assert "x-wg2-mesher-version" not in headers
        status, raw = await _request(app, "GET", "/api/mesh-artifact/unknown-job")
        assert status == 404
        assert json.loads(raw)["detail"] == "Job not found"
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


def test_discarded_mesh_is_regenerated_from_stored_design_for_each_download(
    tmp_path: Path, monkeypatch
) -> None:
    import server.jobs.runtime as jobs_runtime
    import server.mesh.builder as mesh_builder

    request = SolveRequest.model_validate(
        {
            **_solve_body(delay_ms=0),
            "design": {
                **_solve_body(delay_ms=0)["design"],
                "mesh": {"length_segments": 17, "angular_segments": 28},
            },
        }
    )
    calls: list[tuple[float | None, float | None, bool]] = []
    regenerated_mesh = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n// regenerated\n"

    async def fake_build_solver_mesh(design, options, *, force_rebuild=False):
        calls.append(
            (
                design.root.mesh.length_segments.constant_value(),
                design.root.mesh.angular_segments.constant_value(),
                force_rebuild,
            )
        )
        return {"msh_text": regenerated_mesh}

    monkeypatch.setattr(mesh_builder, "build_solver_mesh", fake_build_solver_mesh)
    monkeypatch.setattr(
        jobs_runtime.importlib.metadata,
        "version",
        lambda package: "7.8.9" if package == "hornlab-waveguide-mesher" else "unknown",
    )

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        store.create_job(_complete_job("discarded", request=request))

        for _ in range(2):
            status, raw, headers = await _request_with_headers(
                app, "GET", "/api/mesh-artifact/discarded"
            )
            assert status == 200
            assert raw == regenerated_mesh.encode("utf-8")
            assert headers["content-type"] == "text/plain; charset=utf-8"
            assert headers["x-wg2-mesh-regenerated"] == "1"
            assert headers["x-wg2-mesher-version"] == "7.8.9"
            assert store.get_mesh_artifact("discarded") is None

        assert calls == [(17.0, 28.0, True), (17.0, 28.0, True)]
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_discarded_mesh_regenerates_on_the_domain_the_job_solved(
    tmp_path: Path, monkeypatch
) -> None:
    """A quarter-domain solve must not come back as a full-domain mesh.

    ``mesh.quadrants`` in the snapshot is only what the user *declared*; symmetry
    resolution is free to pick a smaller domain and the solve ran on that one, so
    the rebuild has to replay the resolution the job recorded.
    """

    import server.mesh.builder as mesh_builder

    body = _solve_body(delay_ms=0)
    request = SolveRequest.model_validate(
        {**body, "design": {**body["design"], "mesh": {"quadrants": 1234}}}
    )
    rebuilt_quadrants: list[float | None] = []

    async def fake_build_solver_mesh(design, options, *, force_rebuild=False):
        rebuilt_quadrants.append(design.root.mesh.quadrants.constant_value())
        return {"msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"}

    monkeypatch.setattr(mesh_builder, "build_solver_mesh", fake_build_solver_mesh)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()

        reduced = _complete_job("reduced", request=request)
        reduced["task_metadata"]["symmetry"] = {
            "requested": "auto",
            "resolved_quadrants": 1,
        }
        store.create_job(reduced)
        # No recorded resolution: a v1 row, whose declared domain is the one v1
        # meshed. Leave it alone rather than assuming a full domain for it.
        store.create_job(_complete_job("unrecorded", request=request))

        for job_id in ("reduced", "unrecorded"):
            status, _raw, headers = await _request_with_headers(
                app, "GET", f"/api/mesh-artifact/{job_id}"
            )
            assert status == 200
            assert headers["x-wg2-mesh-regenerated"] == "1"

        assert rebuilt_quadrants == [1.0, 1234.0]
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


def test_discarded_mesh_without_stored_design_remains_gone(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        store.create_job(_complete_job("no-design", stored_design=False))

        status, raw, headers = await _request_with_headers(
            app, "GET", "/api/mesh-artifact/no-design"
        )
        assert status == 410
        assert "no design was stored" in json.loads(raw)["detail"]
        assert "x-wg2-mesh-regenerated" not in headers
        assert store.get_mesh_artifact("no-design") is None
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


def test_metadata_patch_maps_malformed_stored_merge_shape_to_422(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        now = "2026-08-12T00:00:00"
        store.create_job(
            {
                "id": "imported",
                "status": "complete",
                "created_at": now,
                "updated_at": now,
                "queued_at": now,
                "completed_at": now,
                "progress": 1.0,
                "stage": "complete",
                "stage_message": "complete",
                "config_json": {"design": {"formula": "OSSE", "L": 120}},
                "config_summary_json": {"formula_type": "OSSE"},
                "task_metadata": {"exported_files": "not-a-list"},
            }
        )

        status, raw = await _request(
            app,
            "PATCH",
            "/api/jobs/imported/metadata",
            body={"exported_files": ["fixed.step"]},
        )

        assert status == 422
        assert json.loads(raw) == {
            "detail": "Stored exported_files metadata must be a list of strings"
        }
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())
