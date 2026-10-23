"""Acceptance checks across preparation producers and ordinary job lifecycle."""

import asyncio
import json
from pathlib import Path
import shutil

import pytest

from server.app import create_app
from server.mesh.artifact import read_verified_import_mesh
from test_mesh_import import RecordingEngine, register_mesh, registration, request
from test_mesh_replay import app_for, call
from test_real_pipeline import (
    ROUND_RETURN, _call, _close, _finish, _finite_series,
    _imported_engine, _imported_engine_expected, _settled_capabilities,
)
from test_real_pipeline import real_pipeline_session as real_pipeline_session


@pytest.fixture
def isolated_import_bempp_host(real_pipeline_session):
    """Spawn under this test's session, not an earlier app test's warm host.

    The application owns one session for its lifetime. This pytest process
    creates many, so a live global worker may still carry an earlier (or absent)
    session. Close only this process's host before probing/solving, and close its
    replacement before the dependent session fixture removes the directory.
    """
    from server.solver.bempp_process import shutdown_bempp_process

    shutdown_bempp_process()
    try:
        yield
    finally:
        shutdown_bempp_process()


@pytest.mark.parametrize("queued", [False, True], ids=["running", "queued"])
def test_standalone_cancellation_is_durable_and_duplicate_press_does_not_restart(tmp_path, queued):
    async def scenario():
        app, store, runtime, _ = app_for(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()

        class GateEngine(RecordingEngine):
            async def run(self, body, *, imported_record, cancel_cb, **kwargs):
                started.set()
                await release.wait()
                cancel_cb()
                return await super().run(body, imported_record=imported_record, **kwargs)

        engine = GateEngine()
        runtime.engine_registry.engine = engine
        try:
            record = register_mesh(store, tmp_path, registration())
            body = request(record).model_dump(mode="json")
            first = await call(app, "POST", "/api/solve", body)
            async with asyncio.timeout(3):
                await started.wait()
            if queued:
                # Another solve of the model while the first runs: other
                # settings, since the same ones would be the running job.
                body["client_request_id"] = "mesh-queued-cancel-press"
                body["options"]["frequencies_hz"] = [100, 300]
                cancelled = await call(app, "POST", "/api/solve", body)
                assert cancelled != first
            else:
                cancelled = first
            await call(app, "POST", f"/api/stop/{cancelled['job_id']}")
            release.set()
            await runtime.wait_idle(timeout=3)
            assert runtime.store.get_job_row(cancelled["job_id"])["status"] == "cancelled"
            assert await call(app, "POST", "/api/solve", body) == cancelled
            assert len(engine.calls) == int(queued)
        finally:
            release.set()
            await runtime.shutdown()
            store.close()

        app, store, runtime, engine = app_for(tmp_path)
        try:
            assert await call(app, "POST", "/api/solve", body) == cancelled
            assert runtime.store.get_job_row(cancelled["job_id"])["status"] == "cancelled"
            assert not engine.calls
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


@pytest.mark.real_runtime
@pytest.mark.usefixtures("isolated_import_bempp_host")
def test_equivalent_cad_and_file_meshes_use_same_engine_and_agree(tmp_path: Path):
    """Real CAD preparation, then that same full mesh as a CAD-free file import."""
    manifest = json.loads((ROUND_RETURN / "wgreturn.json").read_text())
    source = manifest["sources"][0]
    source_id = source["id"]
    workspace = tmp_path / "workspace"
    shutil.copytree(ROUND_RETURN, workspace / "cadlink" / "wgreturn" / "round.wgreturn")
    sizes = {"rigid_size_mm": 20.0, "transition_mm": 30.0,
             "source_size_mm": {source_id: float(source["suggested_resolution_mm"])}}

    async def scenario():
        app = create_app(data_dir=tmp_path / "cad-data")
        try:
            engine, reason = await _imported_engine(app)
            if engine is None:
                if _imported_engine_expected() is not None:
                    pytest.fail(reason)
                pytest.skip(reason)
            await _call(app, "POST", "/api/cad-workspace/select", {"path": str(workspace)})
            record = await _call(app, "POST", "/api/cadlink/ingest", {
                "bundlePath": "wgreturn/round.wgreturn", "symmetryMode": "full",
                "expectedDesignId": manifest["instances"][0]["design_id"],
                "mesh": {"rigidSizeMm": sizes["rigid_size_mm"],
                         "transitionMm": sizes["transition_mm"], "sourceSizeMm": sizes["source_size_mm"]},
            })
            options = {"engine": engine, "symmetry": "full", "frequencies_hz": [500.0, 1000.0],
                       "polar_config": {"angle_range": [-180, 180, 73]}}
            geometry = {"type": "imported", "ingest_id": record["ingest_id"],
                        "manifest_sha256": record["manifest_sha256"], "artifact_sha256": record["artifact_sha256"],
                        "drive_channels": [{"id": "acceptance", "source_ids": [source_id], "motion": "normal"}],
                        "mesh": sizes, "acknowledged_findings": [
                            f"{record['report_sha256']}:{item['id']}" for item in record["findings"] if item.get("blocking")
                        ]}
            job_id = (await _call(app, "POST", "/api/solve", {"geometry": geometry, "options": options}))["job_id"]
            cad = await _finish(app, job_id)
            retained = json.loads(app.state.cadlink_store.get_ingest(record["ingest_id"])["record_json"])
            text = read_verified_import_mesh(retained)
            tag = retained["source_tags"][source_id]
        finally:
            await _close(app)

        # No exchange folder or CAD project is configured in this workspace.
        app = create_app(data_dir=tmp_path / "file-data")
        try:
            await _settled_capabilities(app.state.engine_registry)
            record = (await _call(app, "POST", "/api/models/files", {
                "filename": "same-prepared-surface.msh", "msh_text": text, "units": "m",
                "forward": "+z", "up": "+y", "source_tags": [tag],
            }))["prepared"]
            geometry = {"type": "imported", "ingest_id": record["ingest_id"],
                        "manifest_sha256": record["manifest_sha256"], "artifact_sha256": record["artifact_sha256"],
                        "drive_channels": [{"id": "acceptance", "source_ids": [f"group_{tag}"], "motion": "normal"}]}
            job_id = (await _call(app, "POST", "/api/solve", {"geometry": geometry, "options": options}))["job_id"]
            file = await _finish(app, job_id)
            assert cad["metadata"]["solve_execution"]["engine"] == file["metadata"]["solve_execution"]["engine"] == engine
            for name in ("frequencies", "channel_order"):
                assert cad[name] == file[name]
            cad_spl = _finite_series(cad["channels"]["acceptance"]["spl_on_axis"]["spl"], "CAD SPL", 2)
            file_spl = _finite_series(file["channels"]["acceptance"]["spl_on_axis"]["spl"], "file SPL", 2)
            assert max(abs(a - b) for a, b in zip(cad_spl, file_spl)) < 0.001
        finally:
            await _close(app)

    asyncio.run(scenario())
