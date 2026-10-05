from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

from server.platform import temp_session
from server.solver.beat_runtime import assets, manager, registry, threads
from server.solver.beat_runtime.ownership import OwnershipClosed
from server.solver.beat_runtime.session import SolveSession
from server.tests.beat_runtime.fake_host_worker import events


@pytest.fixture(autouse=True)
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))


@pytest.fixture
def engine_tree(tmp_path, monkeypatch):
    root = tmp_path / "engine"
    project = root / "project"
    project.mkdir(parents=True)
    for path in (root / "__init__.py", root / "solver.jl", root / "source.jl",
                 project / "Project.toml", project / "Manifest-v1.12.toml",
                 root / "beat_contract" / "system-v1.schema.json"):
        path.parent.mkdir(exist_ok=True)
        path.write_text("fixture")
    engine = assets.EngineAssets(root, project, root / "solver.jl", root / "source.jl")
    monkeypatch.setattr(assets, "engine_assets", lambda backend: engine)
    monkeypatch.setattr(threads, "_performance_core_count", lambda: 8)
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    return engine


def test_key_resolves_paths_threads_content_and_effective_environment(engine_tree, tmp_path):
    options = dict(julia_executable=sys.executable, environment={"BLAB_TEST": "1"})
    key = manager.resolve_key("metal", **options)
    assert key["julia_threads"] == 6
    assert key["environment"]["JULIA_NUM_THREADS"] == "6"
    assert key["environment"]["BLAB_TEST"] == "1"
    assert key["environment"]["JULIA_DEPOT_PATH"].endswith("wg-beat-engine/depot")
    assert Path(key["julia_executable"]).is_absolute()
    assert Path(key["solver_script"]).is_absolute()
    assert manager.resolve_key("metal", **options) == key
    (engine_tree.project / "Manifest-v1.12.toml").write_text("changed")
    assert manager.resolve_key("metal", **options) != key
    assert manager.resolve_key("metal", **dict(options, julia_threads=2))["julia_threads"] == 2
    assert manager.resolve_key("metal", **dict(options, environment={"BLAB_TEST": "2"})) != key
    image = tmp_path / "sysimage"
    image.write_bytes(b"one")
    before = manager.resolve_key("cpu", **options, julia_sysimage=image)
    image.write_bytes(b"two")
    assert manager.resolve_key("cpu", **options, julia_sysimage=image) != before


def test_child_cache_key_start_count_and_quit(engine_tree):
    built = []

    class Worker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.terminated = 0
            built.append(self)

        def terminate(self):
            self.terminated += 1

    runtime = manager.WorkerManager(mode="child", engine_factory=Worker)
    options = dict(julia_executable=sys.executable, environment={})
    first = runtime.get_worker("metal", **options)
    assert runtime.get_worker("metal", **options) is first
    assert first.worker.kwargs["julia_threads"] == 6
    assert first.worker.kwargs["environment"]["JULIA_NUM_THREADS"] == "6"
    assert runtime.get_worker("metal", **options, julia_threads=2) is not first
    runtime.detach()
    assert [w.terminated for w in built] == [1, 1]
    with pytest.raises(OwnershipClosed):
        runtime.get_worker("metal", **options)
    with pytest.raises(OwnershipClosed):
        first.acquire(lambda: None)


def test_shutdown_closes_all_admission_and_releases_clients_after_failure(engine_tree):
    closed = []

    class Worker:
        def __init__(self, **kwargs):
            self.count = kwargs["julia_threads"]

        def terminate(self):
            closed.append(self.count)
            if self.count == 1:
                raise RuntimeError("retirement failed")

    runtime = manager.WorkerManager(mode="child", engine_factory=Worker)
    clients = [runtime.get_worker("cpu", julia_executable=sys.executable,
                                  julia_threads=count, environment={}) for count in (1, 2)]
    with pytest.raises(RuntimeError, match="retirement failed"):
        runtime.shutdown()
    assert closed == [1, 2]
    for client in clients:
        with pytest.raises(OwnershipClosed):
            client.acquire(lambda: None)


def test_host_manager_holds_lease_reuses_worker_and_quit_detaches(launch, monkeypatch):
    key, directory, children = launch
    monkeypatch.setattr(manager, "resolve_key", lambda *args, **kwargs: copy.deepcopy(key))
    runtime = manager.WorkerManager(directory=directory)
    client = runtime.get_worker()
    for name in ("warm-up", "production"):
        with SolveSession() as session:
            session.submit(client, {"name": name})
            assert list(session.events())[-1]["type"] == "completed"
    host_pid = client.worker.host_pid
    instance = client.worker.worker_instance
    assert runtime.get_worker() is client
    assert [e["type"] for e in events(key)].count("started") == 1
    runtime.detach()
    assert children[0].poll() is None
    successor = manager.WorkerManager(directory=directory)
    try:
        with SolveSession() as session:
            other = successor.get_worker()
            session.submit(other, {"name": "relaunch"})
            assert list(session.events())[-1]["type"] == "completed"
        assert other.worker.host_pid == host_pid
        assert other.worker.worker_instance == instance
    finally:
        successor.shutdown()
    assert registry.read_record(registry.key_id(key), directory) is None


def test_host_quit_cancels_active_work_without_killing_host(launch, monkeypatch, tmp_path):
    key, directory, children = launch
    monkeypatch.setattr(manager, "resolve_key", lambda *args, **kwargs: key)
    runtime = manager.WorkerManager(directory=directory)
    with SolveSession() as session:
        session.submit(runtime.get_worker(), {"name": "abandoned", "release_path": str(tmp_path / "never")})
        active_events = session.events()
        assert next(active_events)["type"] == "result"
        runtime.detach()
    assert children[0].poll() is None
    successor = manager.WorkerManager(directory=directory)
    try:
        with SolveSession() as session:
            session.submit(successor.get_worker(), {"name": "successor"})
            assert list(session.events())[-1]["type"] == "completed"
    finally:
        successor.shutdown()
