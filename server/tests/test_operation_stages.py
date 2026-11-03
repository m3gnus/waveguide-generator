"""Request disconnects kill real children; all three clients share stage history."""

import asyncio
import json
import logging
import os
from pathlib import Path
import socket
import threading
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


def test_real_socket_stop_reaches_create_app_and_kills_the_mesher(tmp_path, monkeypatch, caplog):
    """No fake receive/is_disconnected: exercise the shipped middleware stack."""
    import uvicorn
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus
    from server import app as app_module

    caplog.set_level(logging.DEBUG, logger="wg.requests")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.setblocking(False)
    except OSError as exc:
        listener.close()
        pytest.skip(f"loopback port unavailable: {exc}")
    marker = tmp_path / "live-child-marker"
    monkeypatch.setenv("WG_TEST_STAGE_MARKER", str(marker))
    monkeypatch.delenv(child.IN_PROCESS_ENV, raising=False)
    child.close_mesher_child()
    monkeypatch.setattr(core, "_build_stl_mesh_sync", _blocked_geometry)
    killed = threading.Event()
    native_kill = child._Channel.kill

    def kill(channel):
        native_kill(channel)
        killed.set()

    monkeypatch.setattr(child._Channel, "kill", kill)

    async def scenario():
        app = app_module.create_app(data_dir=tmp_path / "app")
        completed = []
        status = None

        async def observed(scope, receive, send):
            async def record(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    completed.append(status)
                await send(message)

            await app(scope, receive, record)

        # Lifespan prewarms unrelated native solvers. Route owners and every
        # production middleware still come from the real create_app.
        server = uvicorn.Server(uvicorn.Config(
            observed, lifespan="off", log_config=None, access_log=False,
            timeout_graceful_shutdown=1,
        ))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        writer = None
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        pytest.fail("uvicorn exited before accepting requests")
                    await asyncio.sleep(0.01)
                port = listener.getsockname()[1]

                async def request(method, path, host):
                    reader, connection = await asyncio.open_connection("127.0.0.1", port)
                    try:
                        connection.write((
                            f"{method} {path} HTTP/1.1\r\nHost: {host}\r\n"
                            "Content-Length: 0\r\nConnection: close\r\n\r\n"
                        ).encode())
                        await connection.drain()
                        raw = await reader.read()
                    finally:
                        connection.close()
                        await connection.wait_closed()
                    head, body = raw.split(b"\r\n\r\n", 1)
                    status_line, *headers = head.decode().split("\r\n")
                    return int(status_line.split()[1]), dict(
                        line.lower().split(": ", 1) for line in headers
                    ), body

                status_code, headers, body = await request("GET", "/health", "attacker.test")
                expected = json.dumps({
                    "detail": "Non-local Host rejected. Open Waveguide Generator "
                    "from the loopback URL printed by the launcher."
                }, separators=(",", ":")).encode()
                assert status_code == 403 and body == expected
                assert headers["content-type"] == "application/json"
                assert int(headers["content-length"]) == len(expected)
                records = [r for r in caplog.records if r.name == "wg.requests"]
                assert [(r.levelno, r.getMessage()) for r in records[:1]] == [(
                    logging.WARNING,
                    "Rejected GET /health for non-local Host 'attacker.test'; use the local application URL",
                )]
                assert len(records) == 2 and records[1].levelno == logging.INFO
                assert records[1].getMessage().startswith("GET /health -> 403 in ")
                caplog.clear()

                url = f"ws://127.0.0.1:{port}/ws/preview"
                async with connect(url, origin=f"http://127.0.0.1:{port}", proxy=None) as ws:
                    hello = json.loads(await ws.recv())
                    assert hello["kind"] == "hello"
                    await ws.send(json.dumps({
                        "v": 1, "epoch": hello["epoch"], "kind": "fixture-unsupported",
                        "seq": 42, "designRevision": 1,
                    }))
                    reply = json.loads(await ws.recv())
                    assert (reply["kind"], reply["code"], reply["seq"]) == ("error", "validation", 42)
                for path in ("/ws/preview", "/ws/jobs"):
                    with pytest.raises(InvalidStatus) as rejected:
                        async with connect(f"ws://127.0.0.1:{port}{path}", origin="https://example.com", proxy=None):
                            pytest.fail("a disallowed websocket Origin connected")
                    assert rejected.value.response.status_code == 403
                assert not [r for r in caplog.records if r.name == "wg.requests"]

                app.state.update_restart.approve("v2.0.1")
                for host in (f"127.0.0.1:{port}", "attacker.test"):
                    status_code, headers, body = await request("POST", "/api/cadlink/ingest", host)
                    assert status_code == 409
                    message = app.state.update_restart.refusal()
                    assert json.loads(body) == {"detail": message, "error": {
                        "schema_version": 1, "code": "update_restart_pending", "stage": "submission",
                        "message": message, "retryable": True, "details": {}, "client_request_id": None,
                    }}
                    assert headers["content-type"] == "application/json"
                    assert int(headers["content-length"]) == len(body)
                assert not [r for r in caplog.records if r.name == "wg.requests"]
                app.state.update_restart.release("fixture complete")
                completed.clear()
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                path, payload = _body("stl")
                body = json.dumps(payload).encode()
                writer.write((
                    f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
                ).encode() + body)
                await writer.drain()
                while not marker.exists():
                    await asyncio.sleep(0.02)
                # Work really started; there is no response to stop reading yet.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(reader.read(1), 0.05)
                writer.close()
                await writer.wait_closed()
            async with asyncio.timeout(4):
                while not killed.is_set() or not completed:
                    await asyncio.sleep(0.02)
            assert completed == [499], "a stopped export completed successfully"
            evidence = json.loads(marker.read_text())
            assert not pid_is_running(evidence["pid"])
            assert not Path(evidence["output"]).exists()
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            server.should_exit = True
            await asyncio.wait_for(task, 3)
            await app.state.jobs_runtime.shutdown()
            app.state.cadlink_store.close()

    try:
        asyncio.run(scenario())
    finally:
        listener.close()
        child.close_mesher_child()
