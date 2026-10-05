"""The mesher build runs in a killable child (server/mesh/child.py)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any

import pytest
from fastapi import HTTPException

from server.design.schema import DesignConfig
from server.mesh import builder as mesh_builder
from server.mesh.api import solver_mesh_response
from server.mesh.builder import clear_solver_mesh_cache
from server.mesh.child import (
    CRASH_MESSAGE,
    MesherChildError,
    MesherChildHost,
    MesherCrashError,
    MesherShuttingDownError,
    begin_mesher_child_shutdown,
    build_timeout_seconds,
    child_enabled,
    close_mesher_child,
    run_mesh_build,
)


# Test doubles. Module-level so the spawned child can import them by name.
def ok_build(value: Any = "built") -> dict[str, Any]:
    return {"value": value, "pid": os.getpid()}


def abort_build(*_args: Any) -> None:
    os.abort()


def segfault_build(*_args: Any) -> None:
    os.kill(os.getpid(), signal.SIGSEGV)


def exit_build(*_args: Any) -> None:
    os._exit(7)


def hang_build(*_args: Any) -> None:
    time.sleep(120)


def marked_hang_build(marker: str) -> None:
    Path(marker).write_text("building", encoding="utf-8")
    hang_build()


def raising_build(*_args: Any) -> None:
    raise ValueError("a plain mesher refusal")


class _OddError(Exception):
    """Pickles but cannot be rebuilt from its args: must still reach the caller."""

    def __init__(self, a: str, b: str) -> None:
        super().__init__(f"{a}/{b}")


def odd_error_build(*_args: Any) -> None:
    raise _OddError("x", "y")


@pytest.fixture()
def host():
    created = MesherChildHost()
    yield created
    created.close()


@pytest.fixture()
def shared_child(monkeypatch):
    # Deliberate: these tests want the real child, whatever the environment says.
    monkeypatch.delenv("WG2_TEST_MESH_IN_PROCESS", raising=False)
    close_mesher_child()
    yield
    close_mesher_child()


def _alive(pid: int) -> bool:
    """Liveness without signalling. On Windows ``os.kill(pid, 0)`` is
    ``TerminateProcess(pid, 0)``: it kills a live process, and it succeeds on
    an exited one whose process object is still referenced."""

    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def test_a_build_runs_in_another_process_and_the_child_is_reused(host) -> None:
    async def scenario() -> None:
        first = await host.run(ok_build, 1)
        second = await host.run(ok_build, 2)
        assert first["pid"] != os.getpid()
        assert first["pid"] == second["pid"]
        assert (first["value"], second["value"]) == (1, 2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "crash",
    [
        abort_build,
        exit_build,
        pytest.param(
            segfault_build,
            marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals"),
        ),
    ],
)
def test_a_crashing_child_is_a_named_error_and_the_next_build_works(host, crash) -> None:
    async def scenario() -> None:
        with pytest.raises(MesherCrashError) as caught:
            await host.run(crash)
        text = str(caught.value)
        assert text == CRASH_MESSAGE
        assert "shrinkage" in text and "surface fit" not in text
        # The server (this process) is alive and the host serves again.
        assert (await host.run(ok_build, "after"))["value"] == "after"

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_the_signal_is_logged_not_shown(host, caplog) -> None:
    async def scenario() -> None:
        with pytest.raises(MesherCrashError) as caught:
            await host.run(abort_build)
        assert "SIGABRT" not in str(caught.value)

    with caplog.at_level("ERROR", logger="wg.mesh"):
        asyncio.run(scenario())
    assert "SIGABRT" in caplog.text


def test_a_hang_is_stopped_at_the_deadline(host) -> None:
    async def scenario() -> None:
        began = time.monotonic()
        with pytest.raises(MesherCrashError, match="did not finish within 1 s"):
            await host.run(hang_build, timeout=1.0)
        assert time.monotonic() - began < 30  # includes a cold child start
        assert (await host.run(ok_build))["value"] == "built"

    asyncio.run(scenario())


def test_cancelling_kills_the_child_and_the_next_build_gets_a_new_one(host) -> None:
    class Cancelled(Exception):
        pass

    async def scenario() -> None:
        pid = (await host.run(ok_build))["pid"]
        began = time.monotonic()

        def cancel_cb() -> None:
            if time.monotonic() - began > 0.5:
                raise Cancelled()

        with pytest.raises(Cancelled):
            await host.run(hang_build, cancel_cb=cancel_cb)
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not _alive(pid)
        assert (await host.run(ok_build))["pid"] != pid

    asyncio.run(scenario())


def test_a_cancelled_task_ends_the_child_instead_of_waiting_out_the_deadline(host) -> None:
    async def scenario() -> None:
        pid = (await host.run(ok_build))["pid"]
        task = asyncio.create_task(host.run(hang_build, timeout=60))
        await asyncio.sleep(1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not _alive(pid)

    asyncio.run(scenario())


def test_python_errors_cross_the_boundary_as_themselves(host) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match="a plain mesher refusal"):
            await host.run(raising_build)
        with pytest.raises(MesherChildError, match="_OddError"):
            await host.run(odd_error_build)
        # Neither was a crash: the same child is still serving.
        assert (await host.run(ok_build))["value"] == "built"

    asyncio.run(scenario())


def test_close_kills_the_child(host) -> None:
    async def scenario() -> int:
        return (await host.run(ok_build))["pid"]

    pid = asyncio.run(scenario())
    host.close()
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_begin_shutdown_rejects_new_builds_and_prewarm_without_spawning(host) -> None:
    async def scenario() -> None:
        host.begin_shutdown()
        host.begin_shutdown()
        host.prewarm()
        with pytest.raises(MesherShuttingDownError, match="shutting down"):
            await host.run(ok_build)
        assert host._channel is None

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["quit", "marked_quit", "update_restart"])
def test_a_job_reaching_mesh_after_child_shutdown_is_interrupted(
    stop, monkeypatch, tmp_path, shared_child,
) -> None:
    """Reject a real builder submission before runtime.shutdown requests cancellation."""
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.jobs.models import SolveRequest
    from server.jobs.runtime import (
        JobRuntime,
        QUIT_INTERRUPTED_MESSAGE,
        QUIT_INTERRUPTED_STAGE_MESSAGE,
        UPDATE_RESTART_MESSAGE,
        UPDATE_RESTART_STAGE_MESSAGE,
    )
    from server.jobs.store import JobStore
    from server.mesh.child import get_mesher_child

    async def scenario():
        ready = asyncio.Event()
        reach_mesh = asyncio.Event()

        class MeshEngine:
            name = "bempp"

            async def run(self, request, *, cancel_cb, stage_cb):
                stage_cb("mesh", 0.0, "Building solver mesh")
                ready.set()
                await reach_mesh.wait()
                await mesh_builder.build_solver_mesh(
                    request.design, request.options, cancel_cb=cancel_cb, force_rebuild=True,
                )
                raise AssertionError("shutdown must reject the mesh submission")

        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("bempp", True, "test", "test")],
                factory=lambda _name: MeshEngine(),
            ),
        )
        try:
            job_id = await runtime.submit(SolveRequest.model_validate({
                "design": {"formula": "OSSE", "L": 120, "a": 45},
                "options": {"engine": "bempp", "stage_delay_ms": 0},
            }))
            await asyncio.wait_for(ready.wait(), 5)
            begin_mesher_child_shutdown()
            if stop == "update_restart":
                monkeypatch.setattr(runtime, "_restart_pending", lambda: True)
            if stop != "quit":
                runtime.mark_running_interrupted_by_quit("test quit")
            assert runtime.store.cancellation_state(job_id) == ("running", False)
            reach_mesh.set()
            await asyncio.wait_for(runtime.wait_idle(), 5)
            row = runtime.store.get_job_row(job_id)
            assert row["status"] == "cancelled"
            assert row["stage"] == "cancelled"
            assert row["stage_message"] == (
                UPDATE_RESTART_STAGE_MESSAGE if stop == "update_restart"
                else QUIT_INTERRUPTED_STAGE_MESSAGE
            )
            assert row["error_message"] == (
                UPDATE_RESTART_MESSAGE if stop == "update_restart"
                else QUIT_INTERRUPTED_MESSAGE
            )
            assert row["cancellation_requested"] is False
            assert get_mesher_child()._channel is None
        finally:
            reach_mesh.set()
            await runtime.shutdown()
            clear_solver_mesh_cache()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/api/solver-mesh", "/api/export/stl"])
def test_mesh_http_request_during_child_shutdown_returns_503(path, shared_child) -> None:
    from fastapi import FastAPI
    from server.exports.api import router as export_router
    from server.mesh.api import mount_solver_mesh
    from server.mesh.child import get_mesher_child

    app = FastAPI()
    mount_solver_mesh(app)
    app.include_router(export_router)
    clear_solver_mesh_cache()
    begin_mesher_child_shutdown()

    async def post():
        payload = {
            "design": {"formula": "OSSE", "L": 120, "a": 45},
        }
        if path == "/api/export/stl":
            payload["designRevision"] = 0
        body = json.dumps(payload).encode()
        delivered = False
        messages = []

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Keep the viewport connected so its watcher cannot win over shutdown.
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
            "client": ("testclient", 123), "server": ("testserver", 80),
        }, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return status, json.loads(content)

    try:
        status, response = asyncio.run(asyncio.wait_for(post(), 5))
        assert status == 503, response
        assert "shutting down" in response["detail"]
        assert get_mesher_child()._channel is None
    finally:
        clear_solver_mesh_cache()


def test_in_process_switch_is_ignored_in_a_bundled_app(monkeypatch) -> None:
    monkeypatch.setenv("WG2_TEST_MESH_IN_PROCESS", "1")
    monkeypatch.setenv("WG2_BUNDLE", "1")
    assert child_enabled()
    monkeypatch.delenv("WG2_BUNDLE")
    assert not child_enabled()


def test_in_process_switch_runs_on_the_gmsh_worker(monkeypatch) -> None:
    monkeypatch.setenv("WG2_TEST_MESH_IN_PROCESS", "1")

    async def scenario() -> None:
        from server.mesh.gmsh_worker import shutdown_gmsh_worker

        try:
            assert (await run_mesh_build(ok_build, 3))["pid"] == os.getpid()
        finally:
            await shutdown_gmsh_worker()

    asyncio.run(scenario())


def test_a_crashing_solve_mesh_is_a_422_that_names_the_fix(monkeypatch, shared_child) -> None:
    """Through the route the viewport uses: the mesher dies, the server answers."""

    monkeypatch.setattr(mesh_builder, "_build_sync", abort_build)
    clear_solver_mesh_cache()
    design = DesignConfig.model_validate(
        {"formula": "R-OSSE", "R": 150, "r0": 12.7, "a": 60, "a0": 15.5}
    )

    async def scenario() -> None:
        with pytest.raises(HTTPException) as refusal:
            await solver_mesh_response(design, "auto")
        assert refusal.value.status_code == 422
        assert refusal.value.detail == CRASH_MESSAGE
        # The same event loop and process still serve a build afterwards.
        assert (await run_mesh_build(ok_build, "again"))["value"] == "again"

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


def test_a_real_solve_mesh_builds_through_the_child(shared_child) -> None:
    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    clear_solver_mesh_cache()
    design = DesignConfig.model_validate(
        {"formula": "R-OSSE", "R": 150, "r0": 12.7, "a": 60, "a0": 15.5}
    )

    async def scenario() -> None:
        built = await mesh_builder.build_solver_mesh(design, {"mesh_validation_mode": "warn"})
        assert built["stats"]["triangle_count"] > 0
        assert built["msh_text"].startswith("$MeshFormat")

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


_ICW_CASE = {
    "formula": "ICW", "r0": 12.7, "a0": 18, "L": 120, "R": 110, "termination": "flat_baffle",
    "scale": 0.35,
    "simulation": {"sim_type": "infinite-baffle"},
    "mesh": {"wall_thickness": 0},
    "source": {"shape": "2"},
    "morph": {"target_shape": 1, "target_width": 700, "target_height": 90,
              "corner_radius": 44.9, "allow_shrinkage": 1, "fixed_part": 0.9},
}


@pytest.mark.slow
@pytest.mark.mesher_crash_repro
@pytest.mark.skipif(
    os.environ.get("WG2_RUN_MESHER_CRASH_REPRO") != "1",
    reason="opt in with WG2_RUN_MESHER_CRASH_REPRO=1 (about 90 s to the gmsh abort)",
)
def test_icw_adversarial_3_no_longer_kills_the_backend(shared_child) -> None:
    """The real ICW design with UI-allowed values: gmsh aborts (134/139) or hangs.

    On Windows gmsh refuses this design with an ordinary mesher error
    ("Adjacent nullptrs found") instead of aborting; that is also a survivable
    outcome, so the test asserts survival either way.
    """

    pytest.importorskip("gmsh")
    mesher = pytest.importorskip("hornlab_mesher")
    clear_solver_mesh_cache()
    design = DesignConfig.model_validate(_ICW_CASE)

    async def scenario() -> None:
        try:
            await mesh_builder.build_solver_mesh(design, {"mesh_validation_mode": "warn"})
        except MesherCrashError as exc:
            assert str(exc) == CRASH_MESSAGE
        except mesher.MesherError:
            pass
        # Reaching here at all means this process survived the mesher.
        assert (await run_mesh_build(ok_build, "alive"))["value"] == "alive"

    try:
        asyncio.run(scenario())
    finally:
        clear_solver_mesh_cache()


# ---- review round 1 regressions, each driven through a real spawned child ----


def root_build(*_args: Any) -> str | None:
    from server.platform.temp_session import spawned_directory_root

    return spawned_directory_root()


def partial_frame_target(connection, _session_root) -> None:
    """A real child that promises a 1 MB frame, sends 10 bytes, and stalls."""

    import struct

    connection.recv()  # the build request
    os.write(connection.fileno(), struct.pack("!i", 1_000_000) + b"x" * 10)
    time.sleep(120)


def failing_warmup_target(connection, session_root) -> None:
    """The real child loop with a warmup that raises."""

    import server.mesh.child as child_module

    def broken() -> None:
        raise RuntimeError("warmup failed")

    child_module._warm_this_process = broken
    child_module._child_main(connection, session_root)


def test_close_does_not_wait_for_a_running_build(host) -> None:
    async def scenario() -> None:
        task = asyncio.create_task(host.run(hang_build))
        await asyncio.sleep(1.5)  # the build is in the child
        began = time.monotonic()
        await asyncio.to_thread(host.close)
        assert time.monotonic() - began < 3
        with pytest.raises(MesherShuttingDownError, match="shutting down"):
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())


def test_shutdown_returns_promptly_during_a_viewport_build(shared_child) -> None:
    from server.mesh.gmsh_worker import shutdown_gmsh_worker

    async def scenario() -> None:
        task = asyncio.create_task(run_mesh_build(hang_build))
        await asyncio.sleep(1.5)
        began = time.monotonic()
        await shutdown_gmsh_worker()
        assert time.monotonic() - began < 5
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="raw POSIX pipe write")
@pytest.mark.parametrize("how", ["deadline", "cancel", "close"])
def test_a_stalled_partial_result_cannot_outlast_the_deadline_cancel_or_close(how) -> None:
    host = MesherChildHost(target=partial_frame_target)

    class Cancelled(Exception):
        pass

    async def scenario() -> None:
        began = time.monotonic()
        if how == "deadline":
            with pytest.raises(MesherCrashError, match="did not finish"):
                await host.run(ok_build, timeout=1.0)
        elif how == "cancel":
            def cancel_cb() -> None:
                if time.monotonic() - began > 1.0:
                    raise Cancelled()

            with pytest.raises(Cancelled):
                await host.run(ok_build, cancel_cb=cancel_cb)
        else:
            task = asyncio.create_task(host.run(ok_build))
            await asyncio.sleep(1.0)
            host.close()
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(task, 5)
        assert time.monotonic() - began < 15

    try:
        asyncio.run(scenario())
    finally:
        host.close()


def test_a_failed_warmup_does_not_answer_the_next_build() -> None:
    host = MesherChildHost(target=failing_warmup_target)

    async def scenario() -> None:
        host.prewarm()
        first = await host.run(ok_build, "design-A")
        second = await host.run(ok_build, "design-B")
        assert (first["value"], second["value"]) == ("design-A", "design-B")
        assert host._channel is not None and not host._channel.warm_pending

    try:
        asyncio.run(scenario())
    finally:
        host.close()


def test_a_stale_reply_is_dropped(host) -> None:
    async def scenario() -> None:
        await host.run(ok_build, "warm up the child")
        channel = host._channel
        assert channel is not None
        channel.events.put(("done", 999_999, {"value": "someone else's mesh"}))
        assert (await host.run(ok_build, "mine"))["value"] == "mine"

    asyncio.run(scenario())


def test_the_child_uses_the_servers_session_directory(host) -> None:
    from server.platform.temp_session import TemporarySession

    session = TemporarySession.create()
    session.activate()
    try:

        async def scenario() -> None:
            assert await host.run(root_build) == str(session.path)

        asyncio.run(scenario())
    finally:
        host.close()
        session.close(remove=True)


def test_there_is_no_default_deadline(monkeypatch) -> None:
    monkeypatch.delenv("WG2_MESH_BUILD_TIMEOUT_S", raising=False)
    assert build_timeout_seconds() is None
    monkeypatch.setenv("WG2_MESH_BUILD_TIMEOUT_S", "90")
    assert build_timeout_seconds() == 90.0
    monkeypatch.setenv("WG2_MESH_BUILD_TIMEOUT_S", "nonsense")
    assert build_timeout_seconds() is None


def test_cancelling_before_the_build_is_sent_leaves_the_warm_child_alone(host) -> None:
    class Cancelled(Exception):
        pass

    def cancel_now() -> None:
        raise Cancelled()

    async def scenario() -> None:
        pid = (await host.run(ok_build))["pid"]
        with pytest.raises(Cancelled):
            await host.run(ok_build, cancel_cb=cancel_now)
        assert _alive(pid)
        assert (await host.run(ok_build))["pid"] == pid

    asyncio.run(scenario())


def test_the_reader_is_gone_before_its_descriptor_is_released(host) -> None:
    """The fd-reuse race: a reader still alive when its fd closes can read the next pipe."""

    async def scenario() -> None:
        await host.run(ok_build)
        channel = host._channel
        assert channel is not None
        seen: list[bool] = []
        real_close = channel.connection.close

        def close_spy() -> None:
            seen.append(channel.reader.is_alive())
            real_close()

        channel.connection.close = close_spy  # type: ignore[method-assign]
        host._discard(channel, respawn=False)
        assert not channel.reader.is_alive()
        # Closed exactly once, by the reader itself, and not from kill().
        assert seen == [True] and channel.connection.closed

    asyncio.run(scenario())


def test_repeated_cancel_and_respawn_never_crosses_replies(host) -> None:
    class Cancelled(Exception):
        pass

    async def scenario() -> None:
        for round_number in range(12):
            began = time.monotonic()

            def cancel_cb(began=began) -> None:
                if time.monotonic() - began > 0.05 * (round_number % 3):
                    raise Cancelled()

            with pytest.raises(Cancelled):
                await host.run(hang_build, cancel_cb=cancel_cb)
            got = await asyncio.wait_for(host.run(ok_build, round_number), 60)
            assert got["value"] == round_number

    asyncio.run(scenario())


def test_a_cancelled_task_does_not_respawn_a_child(host) -> None:
    """Task cancellation alone must not prewarm a replacement child."""

    async def scenario() -> None:
        task = asyncio.create_task(host.run(hang_build))
        await asyncio.sleep(1.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        deadline = time.monotonic() + 5
        while host._channel is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        assert host._channel is None

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["cooperative", "task_cancel", "completed"])
def test_app_shutdown_does_not_respawn_the_mesh_child(
    mode, monkeypatch, tmp_path, shared_child,
) -> None:
    """Use the app's handlers and real runtime: cooperative cancel precedes task cancel."""
    import server.app as app_module
    import server.mesh.child as child_module
    import server.jobs.runtime as runtime_module
    from server.engines.registry import EngineInfo, EngineRegistry
    from server.jobs.models import SolveRequest
    from server.solver.base import EngineRunResult

    # Static assets are unused here; keep app construction independent of a SPA build.
    monkeypatch.setattr(app_module, "FRONTEND_DIST", tmp_path)
    monkeypatch.setattr(app_module, "detect_engines", lambda: [])
    # This build uses the child; it needs no unrelated in-process native warmup.
    async def skip_gmsh_thread_warmup():
        pass

    monkeypatch.setattr(app_module, "prewarm_gmsh_worker", skip_gmsh_thread_warmup)
    monkeypatch.setenv("WG2_SOLVER_WARMUP", "0")
    monkeypatch.setattr(runtime_module, "SHUTDOWN_TASK_TIMEOUT_SECONDS", 1.5)
    host = child_module.get_mesher_child()
    processes = set()
    ensure = host._ensure_locked

    def track_child():
        channel = ensure()
        processes.add(channel.process)
        return channel

    monkeypatch.setattr(host, "_ensure_locked", track_child)
    marker = tmp_path / "building"
    cooperative_cancel = threading.Event()
    task_cancel = asyncio.Event()

    class MeshEngine:
        name = "bempp"

        async def run(self, _request, *, cancel_cb, stage_cb):
            def checkpoint():
                try:
                    cancel_cb()
                except runtime_module._CancelledAtCheckpoint:
                    cooperative_cancel.set()
                    raise

            try:
                if mode == "completed":
                    await run_mesh_build(ok_build, cancel_cb=checkpoint)
                    return EngineRunResult(results={"metadata": {}})
                await run_mesh_build(marked_hang_build, str(marker), cancel_cb=checkpoint)
            except runtime_module._CancelledAtCheckpoint:
                if mode == "task_cancel":
                    # Leave cleanup pending so runtime.shutdown also exercises task.cancel().
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        task_cancel.set()
                        raise
                raise

    async def scenario():
        app = app_module.create_app(data_dir=tmp_path / "data")
        runtime = app.state.jobs_runtime
        runtime.engine_registry = EngineRegistry(
            detector=lambda: [EngineInfo("bempp", True, "test", "test")],
            factory=lambda _name: MeshEngine(),
        )
        request = SolveRequest.model_validate({
            "design": {"formula": "OSSE", "L": 120, "a": 45},
            "options": {"engine": "bempp", "stage_delay_ms": 0},
        })
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        shutdown_finished = False
        try:
            job_id = await runtime.submit(request)
            if mode == "completed":
                await asyncio.wait_for(runtime.wait_idle(), 30)
                assert runtime.store.get_job_row(job_id)["status"] == "complete"
            else:
                deadline = time.monotonic() + 30
                while not marker.exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert marker.exists(), "the build never entered the child"
            channel = host._channel
            assert channel is not None and len(processes) == 1
            began = time.monotonic()
            await asyncio.wait_for(lifespan.__aexit__(None, None, None), 3)
            shutdown_finished = True
            assert time.monotonic() - began < 3
            assert cooperative_cancel.is_set() == (mode != "completed")
            assert task_cancel.is_set() == (mode == "task_cancel")
            assert len(processes) == 1, "shutdown spawned a replacement mesh child"
            assert host._channel is None
            assert not channel.process.is_alive() and not channel.reader.is_alive()
        finally:
            close_mesher_child()
            if not shutdown_finished:
                await lifespan.__aexit__(None, None, None)

    asyncio.run(scenario())
