from __future__ import annotations

import os

import pytest

from server.solver.beat_runtime import assets, cleanup, installer, paths, provision, registry as r, spawn


@pytest.fixture
def cpu_provisioning(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    root = paths.runtime_dir()
    engine = tmp_path / "engine"
    project = engine / "cpu"
    project.mkdir(parents=True)
    (engine / "beat_contract").mkdir()
    for name in ("__init__.py", "beat_contract/system-v1.schema.json", "cpu/Project.toml", "cpu/solver.jl"):
        (engine / name).write_text("fixture")
    selected = assets.EngineAssets(engine, project, project / "solver.jl", project / "solver.jl")
    monkeypatch.setattr(assets, "engine_assets", lambda backend: selected)
    julia = tmp_path / "julia"
    julia.write_bytes(b"fake Julia")
    julia.chmod(0o755)
    calls = []

    def step(executable, code, **kwargs):
        calls.append((code, kwargs))
        kwargs["status_cb"]("Pkg progress \u2713")

    def probe(**kwargs):
        calls.append(("probe", kwargs))
        kwargs["status_cb"]("compiled solve")
        return {"finite": True, "nonzero": True, "terminal_count": 1}

    def forbidden(*args, **kwargs):
        raise AssertionError("real Julia or download forbidden")

    monkeypatch.setattr(installer, "download_archive", forbidden)
    monkeypatch.setattr(provision.julia_steps.subprocess, "Popen", forbidden)
    options = dict(
        environ={"PATH": "", paths.RUNTIME_DIR_ENV: str(tmp_path / "wg"), "SECRET": "not persisted"},
        julia_executable=str(julia), julia_threads=3, run_step=step, probe=probe,
        probe_contract="system-v1", probe_fixture_identity="fixture-sha", status_cb=lambda _: None,
    )
    return root, selected, julia, calls, options


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setattr(spawn, "HOST_MODULE", "server.tests.beat_runtime.fake_host_main")
    monkeypatch.setenv("WG2_BEAT_WORKER_DIR", str(tmp_path / "workers"))
    monkeypatch.setenv("WG2_BEAT_RUNTIME_DIR", str(tmp_path / "runtime"))
    fixture = tmp_path / "fixture"
    key = r.host_key({"backend": "cpu", "julia_executable": str(fixture / "julia"),
                      "julia_identity": "binary-content", "solver_script": str(fixture / "solver.jl"),
                      "julia_project": str(fixture / "project"), "julia_sysimage": str(fixture / "sysimage"),
                      "julia_threads": 2, "engine_fingerprint": "engine-content",
                      "runtime_fingerprint": "runtime-content",
                      "environment": {"TEST_EVENTS": str(tmp_path / "events"), "JULIA_NUM_THREADS": "2"}})
    children = []
    original = spawn.subprocess.Popen

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        if spawn.HOST_MODULE in args[0]:
            children.append(child)
        return child

    monkeypatch.setattr(spawn.subprocess, "Popen", popen)
    yield key, tmp_path / "registry", children
    directory = tmp_path / "registry"
    record = r.read_record(r.key_id(key), directory) if directory.exists() else None
    if record is not None and record.pid != os.getpid():
        cleanup.cleanup_host(record, key, directory, timeout=3)
    for child in children:
        if child.poll() is None:
            child.terminate()  # Only our recorded Popen objects.
        child.wait(timeout=3)
