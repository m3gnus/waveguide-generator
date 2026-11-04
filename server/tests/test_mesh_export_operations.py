"""Retained-mesh downloads use the request-owned cancellation/stage owner."""

import asyncio
import json
from uuid import uuid4

import pytest

from server.jobs import mesh_replay
from server.jobs.operation_progress import mount_operation_progress
from server.mesh import builder
from test_jobs_api import _complete_job
from test_mesh_import import register_mesh, registration, request
from test_mesh_replay import app_for
from test_operation_stages import _http


@pytest.mark.parametrize("kind", ["msh", "replay"])
@pytest.mark.parametrize("outcome", ["stop", "disconnect", "success"])
def test_mesh_export_preparation_is_request_owned(tmp_path, monkeypatch, kind, outcome):
    async def scenario():
        app, store, runtime, _ = app_for(tmp_path)
        mount_operation_progress(app)
        entered, release = asyncio.Event(), asyncio.Event()
        checks = []
        if kind == "replay":
            record = register_mesh(store, tmp_path, registration())
            runtime.store.create_job(_complete_job("export", request=request(record), mesh_discarded=False))
            expected = mesh_replay.build_mesh_replay(runtime, store, "export")
            original_prepare = mesh_replay.prepare_registration

            async def prepare(body, cancel_cb=None, **options):
                assert cancel_cb is not None
                entered.set()
                while not release.is_set():
                    checks.append(1)
                    cancel_cb()
                    await asyncio.sleep(0.005)
                return await original_prepare(body, cancel_cb, **options)

            monkeypatch.setattr(mesh_replay, "prepare_registration", prepare)
            path = "/api/jobs/export/mesh-replay"
        else:
            runtime.store.create_job(_complete_job("export"))
            expected = b"$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"

            async def build(design, options, cancel_cb=None, progress_cb=None, *, force_rebuild=False):
                assert force_rebuild and cancel_cb is not None and progress_cb is not None
                progress_cb("mesh_prepare", 0.0, "Preparing mesh")
                entered.set()
                while not release.is_set():
                    checks.append(1)
                    cancel_cb()
                    await asyncio.sleep(0.005)
                progress_cb("mesh_validate", 1.0, "Validated mesh")
                return {"msh_text": expected.decode()}

            monkeypatch.setattr(builder, "build_solver_mesh", build)
            path = "/api/mesh-artifact/export"
        operation_id = str(uuid4())
        disconnected = tmp_path / "disconnected"
        pending = asyncio.create_task(_http(app, "GET", path, operation_id=operation_id, marker=disconnected))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            status, _, progress = await _http(app, "GET", f"/api/operation-progress/{operation_id}")
            assert status == 200
            assert json.loads(progress) == {"stages": ["resolve"], "total": 4}
            if outcome == "stop":
                assert (await _http(app, "DELETE", f"/api/operation-progress/{operation_id}"))[0] == 204
            elif outcome == "disconnect":
                disconnected.touch()
            else:
                release.set()
            status, headers, payload = await asyncio.wait_for(pending, 2)
            assert checks
            if outcome == "success":
                assert status == 200
                if kind == "msh":
                    assert payload == expected
                    assert headers[b"x-wg2-mesh-regenerated"] == b"1"
                else:
                    assert payload == json.dumps(expected, separators=(",", ":"), ensure_ascii=False).encode()
                assert json.loads(headers[b"x-wg-operation-stages"]) == {
                    "stages": ["resolve", "mesh", "check", "write"], "total": 4,
                }
            else:
                assert status == 499, payload
            # Finished preparations can no longer receive Stop.
            assert (await _http(app, "DELETE", f"/api/operation-progress/{operation_id}"))[0] == 409
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
