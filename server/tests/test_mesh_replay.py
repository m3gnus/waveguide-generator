"""Replay packages carry inputs across empty databases, never CAD approvals."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

from fastapi import FastAPI
import pytest

from server.cadlink.store import CadLinkStore
from server.contracts.canonical import canonical_utf8_strict_json
from server.jobs.api import create_jobs_router
from server.jobs.mesh_replay import _digest, mount_mesh_replay
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from test_imported_jobs import _AlwaysRegistry
from test_jobs_api import _request
from test_mesh_import import RecordingEngine, register_mesh, registration, request


def app_for(data_dir):
    store = CadLinkStore.for_data_dir(data_dir)
    engine = RecordingEngine()
    runtime = JobRuntime(JobStore.for_data_dir(data_dir), cadlink_store=store,
                         engine_registry=_AlwaysRegistry(engine))
    runtime.store.initialize()
    app = FastAPI()
    app.state.data_dir, app.state.cadlink_store, app.state.jobs_runtime = data_dir, store, runtime
    mount_mesh_replay(app)
    app.include_router(create_jobs_router(runtime))
    return app, store, runtime, engine


async def call(app, method, path, body=None, expected=200):
    status, raw = await _request(app, method, path, body=body)
    assert status == expected, (status, raw)
    return json.loads(raw)


async def exported(app, store, runtime):
    record = register_mesh(store, app.state.data_dir, registration(units="cm", forward="+x", up="+z"))
    body = request(record)
    body.options.frequencies_hz = [103.0, 201.5, 1001.0]
    body.options.polar_config.distance = 3.25
    body.geometry.drive_voltage_v = 1.7
    job_id = await runtime.submit(body)
    await runtime.wait_idle()
    package = await call(app, "GET", f"/api/jobs/{job_id}/mesh-replay")
    return record, job_id, package


def rehash(package):
    package["content_sha256"] = _digest(canonical_utf8_strict_json(
        {key: value for key, value in package.items() if key != "content_sha256"}
    ))


def test_package_rehydrates_idempotently_in_fresh_workspace_and_uses_ordinary_solve(tmp_path):
    async def scenario():
        app, store, runtime, engine = app_for(tmp_path / "origin")
        try:
            record, job_id, package = await exported(app, store, runtime)
            first_request = runtime.store.get_job_row(job_id)["config_json"]
            first_mesh = engine.calls[0][1]
            # The source no longer exists at its original location.
            Path(record["mesh_store_path"]).with_name("original.msh").unlink()
            assert (await call(app, "GET", f"/api/jobs/{job_id}/mesh-replay", expected=409))["detail"]
        finally:
            await runtime.shutdown()
            store.close()

        app, store, runtime, engine = app_for(tmp_path / "fresh")
        try:
            assert (await _request(app, "GET", "/api/cadlink/returns"))[0] == 404
            restored, same = await asyncio.gather(
                call(app, "POST", "/api/mesh-replay", package),
                call(app, "POST", "/api/mesh-replay", package),
            )
            assert same == restored
            assert runtime.store.list_jobs()[1] == 0
            assert restored["record"]["ingest_id"] != record["ingest_id"]
            assert restored["record"]["producer"] == "mesh-file"
            assert restored["record"]["msh_text"] == first_mesh
            body = deepcopy(restored["request"])
            assert body["options"] == first_request["options"]
            assert body["geometry"] == {
                **first_request["geometry"], "ingest_id": restored["record"]["ingest_id"],
            }
            assert body["client_request_id"] is None and body["parent_job_id"] is None
            body["client_request_id"] = "fresh-replay-press"
            accepted = await call(app, "POST", "/api/solve", body)
            assert await call(app, "POST", "/api/solve", body) == accepted
            await runtime.wait_idle()
            assert runtime.store.get_job_row(accepted["job_id"])["status"] == "complete"
            assert len(engine.calls) == 1
            assert engine.calls[0][1] == first_mesh
            assert engine.calls[0][0]["options"] == first_request["options"]
            # The mapping, like the retained mesh, survives a backend restart.
            store.close()
            store.initialize()
            assert await call(app, "POST", "/api/mesh-replay", package) == restored
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change,rehash_after", [
    (lambda p: p["registration"].update(units="m"), False),
    (lambda p: p.update(solver_msh_text=p["solver_msh_text"] + "changed"), True),
    (lambda p: p["registration"].update(up="+x"), True),
    (lambda p: p.update(original_sha256="sha256:" + "0" * 64), True),
    (lambda p: p.update(schema_version=999), False),
    (lambda p: p.update(kind="wg-cad-replay"), False),
    (lambda p: p.update(axial_contract="future-axis-contract"), True),
    (lambda p: p.update(mesh_store_path="/untrusted/mesh.msh"), True),
    (lambda p: p["registration"].update(normalisation={"assembly_frame_is_solver_frame": True}), True),
    (lambda p: p["request"]["options"].update(engine="auto"), True),
    (lambda p: p["request"]["geometry"].update(required_features=["native-source-contour-v1"]), True),
    (lambda p: p["request"]["geometry"].update(drive_channels=[{"id": "wrong", "source_ids": ["wrong"]}]), True),
    (lambda p: p["request"]["geometry"].update(drive_channels=[]), True),
    (lambda p: p["request"]["geometry"].update(manifest_sha256="sha256:" + "0" * 64), True),
])
def test_corrupt_or_unsupported_packages_are_refused_before_registration(tmp_path, change, rehash_after):
    async def scenario():
        app, store, runtime, _ = app_for(tmp_path)
        try:
            _, _, original = await exported(app, store, runtime)
            package = deepcopy(original)
            change(package)
            if rehash_after:
                rehash(package)
            before = list((tmp_path / "imports" / "files").iterdir())
            await call(app, "POST", "/api/mesh-replay", package, expected=422)
            assert list((tmp_path / "imports" / "files").iterdir()) == before
            assert len(store.list_model_ingests("mesh-file:" + original["original_sha256"])) == 1
            assert runtime.store.list_jobs()[1] == 1
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_replay_uses_verified_import_after_job_mesh_pruning(tmp_path):
    async def scenario():
        app, store, runtime, _ = app_for(tmp_path)
        try:
            _, job_id, package = await exported(app, store, runtime)
            runtime.store.prune_terminal_jobs(prune_results=False, mesh_grace_minutes=0)
            assert runtime.store.get_mesh_artifact(job_id) is None
            assert await call(app, "GET", f"/api/jobs/{job_id}/mesh-replay") == package
            # A conflicting *present* artifact still fails closed.
            runtime.store.store_mesh_artifact(job_id, "changed")
            await call(app, "GET", f"/api/jobs/{job_id}/mesh-replay", expected=409)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("contract", [None, "frame-axis-v1", "per-source-axis-v2"])
def test_export_checks_recorded_axial_semantics_instead_of_relabelling(tmp_path, contract):
    async def scenario():
        app, store, runtime, engine = app_for(tmp_path)
        original_run = engine.run

        async def run(body, *, imported_record, **kwargs):
            result = await original_run(body, imported_record=imported_record, **kwargs)
            if contract:
                result.results["channels"]["group_2"]["metadata"]["axial_contract"] = contract
            return result

        engine.run = run
        try:
            record = register_mesh(store, tmp_path, registration())
            body = request(record)
            body.geometry.drive_channels[0].motion = "axial"
            job_id = await runtime.submit(body)
            await runtime.wait_idle()
            path = f"/api/jobs/{job_id}/mesh-replay"
            if contract != "per-source-axis-v2":
                response = await call(app, "GET", path, expected=409)
                assert "axial" in response["detail"]
            else:
                package = await call(app, "GET", path)
                opened = await call(app, "POST", "/api/mesh-replay", package)
                assert opened["request"]["geometry"]["drive_channels"][0]["motion"] == "axial"
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_cad_runs_are_not_relabelled_as_portable_file_meshes(tmp_path, monkeypatch):
    async def scenario():
        from server.jobs import mesh_replay

        app, store, runtime, _ = app_for(tmp_path)
        try:
            record, job_id, _ = await exported(app, store, runtime)
            monkeypatch.setattr(mesh_replay, "get_prepared_mesh", lambda *_: {**record, "producer": "cadlink"})
            response = await call(app, "GET", f"/api/jobs/{job_id}/mesh-replay", expected=409)
            assert "CAD models" in response["detail"]
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
