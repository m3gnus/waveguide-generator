from __future__ import annotations

import os

import pytest

from server.solver.beat_runtime import cleanup, registry as r, spawn


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
