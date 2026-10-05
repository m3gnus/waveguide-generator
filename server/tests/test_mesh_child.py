"""The mesher build runs in a killable child (server/mesh/child.py)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
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
    monkeypatch.delenv("WG2_TEST_MESH_IN_PROCESS", raising=False)
    close_mesher_child()
    yield
    close_mesher_child()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
    """The real ICW design with UI-allowed values: gmsh aborts (134/139) or hangs."""

    pytest.importorskip("gmsh")
    pytest.importorskip("hornlab_mesher")
    clear_solver_mesh_cache()
    design = DesignConfig.model_validate(_ICW_CASE)

    async def scenario() -> None:
        try:
            await mesh_builder.build_solver_mesh(design, {"mesh_validation_mode": "warn"})
        except MesherCrashError as exc:
            assert str(exc) == CRASH_MESSAGE
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
        with pytest.raises(RuntimeError, match="shutting down"):
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
