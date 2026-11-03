"""Request disconnects kill real children; all three clients share stage history."""

import asyncio
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import pytest

from server.cadlink.store import CadLinkStore
from server.exports import api, core, wglink_service
from server.jobs.operation_progress import mount_operation_progress
from server.jobs.request_cancellation import ClientDisconnected
from server.mesh import builder, child
from server.platform.instance import pid_is_running
from test_design_assembly_record import UID, assembly, design, woofer
from test_models_api import app_for
from test_wglink_export import _install_fake_write


def _blocked_geometry(*args, **kwargs):
    # Make a real partial artifact inside the request's parent-owned directory.
    output = Path(args[1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "partial").write_bytes(b"unfinished")
    Path(os.environ["WG_TEST_STAGE_MARKER"]).write_text(json.dumps({
        "pid": os.getpid(), "output": str(output),
    }))
    time.sleep(120)


async def _http(app, method, path, body=None, *, operation_id=None, marker=None):
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        # is_disconnected polls this without waiting on another request body.
        return {"type": "http.disconnect"} if marker and marker.exists() else {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    headers = [(b"content-type", b"application/json"), (b"idempotency-key", b"stage-send")]
    if operation_id:
        headers.append((b"x-wg-operation-id", operation_id.encode()))
    target, _, query = path.partition("?")
    await asyncio.wait_for(app({
        "type": "http", "method": method, "path": target, "raw_path": target.encode(),
        "query_string": query.encode(), "headers": headers, "scheme": "http",
        "server": ("test", 80), "client": ("test", 1), "http_version": "1.1",
    }, receive, send), 25)
    start = next(m for m in messages if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), data


def _mount(tmp_path, store):
    app, runtime = app_for(tmp_path, store)
    app.include_router(api.router)
    mount_operation_progress(app)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app.state.cad_workspace = SimpleNamespace(selected_path=lambda: workspace)
    app.state.cadlink_store = store
    app.state.data_dir = tmp_path
    return app, runtime, workspace


def _body(kind):
    if kind == "build":
        return "/api/models/assembly/compile", {
            "design": design(assembly=assembly(woofer(enabled=False))),
            "design_uid": UID, "design_name": "Stages", "design_revision": 1,
            "build_session": str(uuid4()),
        }
    return ("/api/export/wglink" if kind == "send" else f"/api/export/{kind}"), {
        "design": {"formula": "OSSE", "L": 40, "a": 45},
        "designRevision": 1, "baseName": "Stages",
    }


@pytest.fixture
def stage_store(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    store.initialize()
    yield store
    store.close()


@pytest.mark.parametrize("kind", ["build", "stl", "step", "step?body=surface", "send"])
def test_abort_kills_child_and_removes_output(kind, tmp_path, monkeypatch, stage_store):
    marker = tmp_path / "child-marker"
    monkeypatch.setenv("WG_TEST_STAGE_MARKER", str(marker))
    monkeypatch.delenv(child.IN_PROCESS_ENV, raising=False)
    child.close_mesher_child()
    target = {
        "build": (builder, "_build_assembly_sync"), "stl": (core, "_build_stl_mesh_sync"),
        "step": (core, "_build_step_solid_sync"), "step?body=surface": (core, "_build_step_sync"),
        "send": (wglink_service, "build_bundle"),
    }[kind]
    monkeypatch.setattr(*target, _blocked_geometry)

    async def scenario():
        app, runtime, workspace = _mount(tmp_path, stage_store)
        try:
            path, payload = _body(kind)
            status, _, _ = await _http(app, "POST", path, payload, marker=marker)
            assert status == 499
            evidence = json.loads(marker.read_text())
            assert not pid_is_running(evidence["pid"]), "the abandoned mesher child is still alive"
            assert not Path(evidence["output"]).exists(), "partial geometry survived Stop"
            assert stage_store.publication_exports() == []
            assert stage_store.publication_reservation_for_key("stage-send") is None
            assert stage_store._read_one("SELECT count(*) AS n FROM imported_models", ())["n"] == 0
            assert not list(workspace.rglob("*.wglink"))
            assert not list(stage_store.db_path.with_suffix(".publications").glob("*.json"))
        finally:
            await runtime.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        child.close_mesher_child()


@pytest.mark.parametrize("kind", ["build", "stl", "step", "send"])
def test_completed_stages_reach_the_client_in_order(kind, tmp_path, monkeypatch, stage_store):
    # Actual preparation, serialization, validation and publication; only the
    # outbound CAD writer is substituted, as in its identity contract tests.
    monkeypatch.setenv(child.IN_PROCESS_ENV, "1")
    _install_fake_write(monkeypatch)
    monkeypatch.setattr(api, "focus_cad", lambda: False)
    monkeypatch.setattr(api, "_handoff_waiting_notice", lambda _: None)

    async def scenario():
        app, runtime, _ = _mount(tmp_path, stage_store)
        try:
            operation_id = str(uuid4())
            path, payload = _body(kind)
            status, headers, response = await _http(app, "POST", path, payload, operation_id=operation_id)
            assert status == 200, response.decode(errors="replace")
            expected = {"stages": ["resolve", "mesh", "check", "publish" if kind in ("build", "send") else "write"], "total": 4}
            assert json.loads(headers[b"x-wg-operation-stages"]) == expected
            status, _, progress = await _http(app, "GET", f"/api/operation-progress/{operation_id}")
            assert status == 200
            assert json.loads(progress) == expected
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_progress_ids_are_validated_and_isolated():
    app = FastAPI()
    mount_operation_progress(app)
    assert asyncio.run(_http(app, "GET", "/api/operation-progress/not-an-id"))[0] == 422
    assert asyncio.run(_http(app, "GET", f"/api/operation-progress/{uuid4()}"))[0] == 404


@pytest.mark.parametrize("kind", ["build", "send"])
def test_stop_after_validation_cannot_publish(kind, tmp_path, monkeypatch, stage_store):
    from server.cadlink.intake.design_assembly import compile_design_assembly

    monkeypatch.setenv(child.IN_PROCESS_ENV, "1")
    _install_fake_write(monkeypatch)
    stopped = False

    def check():
        if stopped:
            raise ClientDisconnected()

    def progress(stage):
        nonlocal stopped
        if stage == "check":
            stopped = True

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _, payload = _body(kind)
    with pytest.raises(ClientDisconnected):
        if kind == "build":
            asyncio.run(compile_design_assembly(stage_store, tmp_path, payload["design"],
                design_uid=UID, design_name="Stages", design_revision=1,
                build_session=str(uuid4()), cancel_cb=check, progress_cb=progress))
        else:
            wglink_service.export_wglink_sync(api.WgLinkExportRequest.model_validate(payload),
                stage_store, workspace, "stage-send", cancel_cb=check, progress_cb=progress)
    assert stage_store.publication_exports() == []
    assert stage_store.publication_reservation_for_key("stage-send") is None
    assert stage_store._read_one("SELECT count(*) AS n FROM imported_models", ())["n"] == 0
    assert not list(workspace.rglob("*.wglink"))
    assert not list((tmp_path / "imports").rglob("solve.msh"))
    assert not list(stage_store.db_path.with_suffix(".publications").glob("*.json"))


@pytest.mark.parametrize("kind", ["stl", "step", "surface"])
def test_export_stage_callbacks_preserve_exact_bytes_from_the_same_native_write(kind, monkeypatch):
    from hornlab_mesher import printable_surface_tags
    from server.design.schema import DesignConfig
    from server.mesh.gmsh_worker import run_on_gmsh_worker

    monkeypatch.setenv(child.IN_PROCESS_ENV, "1")
    if kind != "stl":
        import gmsh

        native_write = gmsh.write
        written = []

        def same_native_write(path, *args, **kwargs):
            # OCC stamps native writes with time and can renumber entities.
            # Replay the *same* native bytes to compare the surrounding export
            # path byte for byte, including its original header and timestamp.
            if written:
                Path(path).write_bytes(written[0])
            else:
                native_write(path, *args, **kwargs)
                written.append(Path(path).read_bytes())

        monkeypatch.setattr(gmsh, "write", same_native_write)
    model = DesignConfig.model_validate({"formula": "OSSE", "L": 40, "a": 45})
    synchronous, instrumented = {
        "stl": (core._build_stl_mesh_sync, core.build_stl),
        "step": (core._build_step_solid_sync, core.build_step_solid),
        "surface": (core._build_step_sync, core.build_step),
    }[kind]

    async def scenario():
        original = await run_on_gmsh_worker(synchronous, model.model_dump(mode="json"))
        stages = []
        result = await instrumented(model, cancel_cb=lambda: None, progress_cb=stages.append)
        assert stages == ["resolve", "mesh"]
        if kind == "stl":
            expected = core.binary_stl(original["vertices"], original["indices"], original["surfaceTags"],
                keep_tags=printable_surface_tags({"formula": model.formula}))
            assert result.data == expected
        else:
            assert result.step_text == original.step_text

    asyncio.run(scenario())
