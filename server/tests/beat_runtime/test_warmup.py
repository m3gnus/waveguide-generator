from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from server.app import create_app
from server.platform import temp_session
from server.solver import beat_cpu_runtime as facade, warmup as app_warmup
from server.solver.beat_runtime import cleanup, manager, paths, probe, readiness, registry, warmup
from server.solver.beat_runtime.ownership import OwnershipClosed
from server.solver.beat_runtime.session import SolveSession
from server.tests.beat_runtime.fake_host_worker import EngineWorker, events, wait_until
from server.tests.beat_runtime.test_manager import engine_tree  # noqa: F401
from server.tests.test_startup_performance import _beat_quit_hook


@pytest.fixture
def runtime_factory(engine_tree, launch, monkeypatch, tmp_path):  # noqa: F811
    import server.app as app_module

    static = tmp_path / "static"
    static.mkdir()
    monkeypatch.setattr(app_module, "FRONTEND_DIST", static)
    key, _, children = launch
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setenv("WG2_BEAT_JULIA", sys.executable)
    monkeypatch.setenv("TEST_EVENTS", key["environment"]["TEST_EVENTS"])
    monkeypatch.setenv("TEST_COMPILED", "1")
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    runtimes = []

    def make(mode="host"):
        runtime = manager.WorkerManager(mode=mode, directory=paths.worker_dir(),
                                       engine_factory=EngineWorker if mode == "child" else None)
        runtimes.append(runtime)
        monkeypatch.setattr(manager, "_default_manager", runtime)
        return runtime

    yield make, key, children
    for runtime in runtimes:
        runtime.shutdown()
    directory = paths.worker_dir()
    if directory.exists():
        for record_path in directory.glob("*.json"):
            if record_path.name.endswith(".key.json"):
                continue
            record = registry.read_record(record_path.stem, directory)
            if record is not None:
                cleanup.cleanup_host(record, record.key, directory, timeout=3)


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
def test_selected_backend_warmup_and_solve_reuse_key_threads_host(runtime_factory, backend):
    make, event_key, _ = runtime_factory
    runtime = make()
    app_warmup._warm_beat(backend)
    client = runtime.get_worker(backend)
    host_pid, instance = client.worker.host_pid, client.worker.worker_instance
    key = client.worker.key
    expected_threads = 6 if backend == "metal" else 8
    assert key["julia_threads"] == expected_threads
    assert key["environment"]["JULIA_NUM_THREADS"] == str(expected_threads)
    assert key["backend"] == backend
    with SolveSession() as session:
        mesh = session.directory / "solve.msh"
        mesh.write_bytes(probe._FIXTURE.read_bytes())
        session.submit(runtime.get_worker(backend), probe.build_request(mesh, backend=backend))
        assert list(session.events())[-1]["type"] == "completed"
    assert client.worker.host_pid == host_pid
    assert client.worker.worker_instance == instance
    assert runtime.get_worker(backend) is client
    assert [event["type"] for event in events(event_key)].count("started") == 1
    assert [event["type"] for event in events(event_key)].count("submitted") == 2


@pytest.mark.parametrize("mode", ["host", "child"])
@pytest.mark.parametrize("operation", ["warmup", "solve"])
def test_quit_during_work_retires_engine_and_closes_default(runtime_factory, monkeypatch, tmp_path,
                                                         mode, operation):
    make, key, children = runtime_factory
    monkeypatch.setenv("TEST_PROBE_GATE", str(tmp_path / "never"))
    runtime = make(mode)
    client = runtime.get_worker()
    errors = []

    def work():
        try:
            if operation == "warmup":
                app_warmup._warm_beat("cpu")
            else:
                with SolveSession() as session:
                    mesh = session.directory / "solve.msh"
                    mesh.write_bytes(probe._FIXTURE.read_bytes())
                    session.submit(runtime.get_worker(), probe.build_request(mesh))
                    list(session.events())
        except (RuntimeError, OSError) as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        wait_until(lambda: any(e["type"] == "reading" for e in events(key)), timeout=5)
        started = time.monotonic()
        asyncio.run(_beat_quit_hook(create_app(data_dir=tmp_path / "data"))())
        assert time.monotonic() - started < 2
        thread.join(3)
        assert not thread.is_alive()
        wait_until(lambda: any(e["type"] == "terminated" for e in events(key)))
        assert manager.get_manager() is runtime
        with pytest.raises(OwnershipClosed):
            runtime.get_worker()
        with pytest.raises(OwnershipClosed):
            client.acquire(lambda: None)
        if mode == "host":
            # The detached host has its own idle lifetime. An explicit later
            # qualifier shutdown reaps it; no abandoned engine keeps running.
            assert children[0].poll() is None
            record = next(p for p in paths.worker_dir().glob("*.json") if not p.name.endswith(".key.json"))
            host = registry.read_record(record.stem, paths.worker_dir())
            cleanup.cleanup_host(host, host.key, paths.worker_dir(), timeout=3)
            children[0].wait(timeout=3)
    finally:
        (tmp_path / "never").touch()
        thread.join(3)


def test_idle_quit_detaches_and_host_exits_at_its_idle_deadline(runtime_factory, tmp_path):
    make, _, children = runtime_factory
    runtime = make()
    client = runtime.get_worker()
    client.worker.idle_timeout = 0.1
    warmup.warm_up(mode="tiny")
    asyncio.run(_beat_quit_hook(create_app(data_dir=tmp_path / "data"))())
    children[0].wait(timeout=3)
    assert registry.read_record(registry.key_id(client.worker.key), paths.worker_dir()) is None


def test_off_and_invalid_modes_never_access_the_manager(monkeypatch):
    monkeypatch.setattr(warmup, "get_manager", lambda: pytest.fail("manager accessed"))
    warmup.warm_up(mode="off")
    with pytest.raises(ValueError, match="mode"):
        warmup.warm_up(mode="other")


def test_worker_mode_starts_without_a_submission_or_closing_admission(runtime_factory):
    make, key, _ = runtime_factory
    runtime = make()
    warmup.warm_up(mode="worker")
    client = runtime.get_worker()
    assert client.worker.worker_info is not None
    assert not any(e["type"] == "submitted" for e in events(key))
    assert runtime.get_worker() is client


@pytest.mark.parametrize("warmup_mode", ["tiny", "worker"])
def test_quit_cancels_cold_start_before_ensure_started_completes(runtime_factory, monkeypatch,
                                                               tmp_path, warmup_mode):
    make, key, _ = runtime_factory
    gate = tmp_path / "startup-release"
    marker = tmp_path / "startup-entered"
    monkeypatch.setenv("TEST_START_GATE", str(gate))
    monkeypatch.setenv("TEST_START_ONCE_MARKER", str(marker))
    runtime = make()
    errors = []

    def work():
        try:
            warmup.warm_up(mode=warmup_mode)
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        wait_until(marker.exists, timeout=5)
        assert not any(e["type"] == "started" for e in events(key))
        started = time.monotonic()
        asyncio.run(_beat_quit_hook(create_app(data_dir=tmp_path / "data"))())
        thread.join(1)
        assert time.monotonic() - started < 2
        assert not thread.is_alive() and errors
        # Release only after proving Quit woke the cold-start caller.
        gate.touch()
        wait_until(lambda: any(e["type"] == "terminated" for e in events(key)))
        with pytest.raises(OwnershipClosed):
            runtime.get_worker()
    finally:
        gate.touch()
        thread.join(3)


def test_tiny_probe_failure_before_iteration_explicitly_closes_events(runtime_factory, monkeypatch):
    make, _, _ = runtime_factory
    make()
    closed = []

    class UnreadEvents:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(SolveSession, "events", lambda self: UnreadEvents())

    def fail_before_iteration(worker, *, directory, backend):
        request = directory / "probe.json"
        request.write_text('{"name":"unread probe"}')
        worker.submit(request)
        return SimpleNamespace(ready=False, reason="negotiation failed before iteration")

    monkeypatch.setattr(warmup, "compiled_probe", fail_before_iteration)
    with pytest.raises(RuntimeError, match="before iteration"):
        warmup.warm_up(mode="tiny")
    assert closed == [True]


@pytest.mark.parametrize("selector,official", [("", True), (" official ", True), ("hbb", False), ("legacy", False), ("Official", True)])
def test_shared_selector_lifecycle_and_readiness_decisions(runtime_factory, monkeypatch, tmp_path,
                                                          selector, official):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", selector)
    calls = []
    monkeypatch.setattr(warmup, "warm_up", lambda **kwargs: calls.append("official-warmup"))
    monkeypatch.setattr(manager, "_default_manager",
                        SimpleNamespace(detach=lambda: calls.append("official-quit")))
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", SimpleNamespace(
        warm_up=lambda **kwargs: calls.append("hbb-warmup"),
        shutdown_workers=lambda: calls.append("hbb-quit")))
    app_warmup._warm_beat("cpu")
    asyncio.run(_beat_quit_hook(create_app(data_dir=tmp_path / "data"))())
    prefix = "official" if official else "hbb"
    assert calls == [f"{prefix}-warmup", f"{prefix}-quit"]
    assert ("server.solver.beat_runtime.cli" in facade.provision_command()) == official
    monkeypatch.setattr(facade, "_preparation_in_flight", False)
    monkeypatch.setattr(facade, "_import", lambda name: None)
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend:
                        readiness.BackendReadiness(True, "ready", "fixture proof"))
    assert facade.cpu_runtime_readiness(None).ready == official
