from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys

import pytest

from server.engines.registry import EngineInfo, EngineRegistry, _official_runtime_statuses, detect_engines
from server.solver import beat, beat_cpu_runtime as facade
from server.solver.beat_runtime import cli, hardware, identity, locks, paths, provider, readiness, state
from server.tests.test_beat_cpu_runtime import _install_stub_package, _ready_state
from server.tests.beat_runtime import test_readiness

_REAL_POPEN = subprocess.Popen
proved_cpu = test_readiness.proved_cpu


@pytest.fixture
def selected(monkeypatch, tmp_path):
    monkeypatch.setenv(provider.PROVIDER_ENV, "official")
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "data"))
    for name, value in (("_provision_thread", None), ("_provision_step", None),
                        ("_preparation_in_flight", False), ("_gpu_stage_backend", None),
                        ("_gpu_stage_step", None), ("_runtimes_prepared", False)):
        monkeypatch.setattr(facade, name, value)
    yield
    thread = facade._provision_thread
    if thread is not None:
        thread.join(3)
        assert not thread.is_alive()


def test_default_off_import_isolation_in_fresh_interpreter(tmp_path):
    env = dict(os.environ, WG2_BEAT_RUNTIME_DIR=str(tmp_path / "data"))
    env.pop(provider.PROVIDER_ENV, None)
    code = """
import sys
from scripts import bootstrap
from server.solver import beat_cpu_runtime, beat
from server.engines.registry import EngineRegistry
registry = EngineRegistry()
beat.beat_status.cache_clear()
beat_cpu_runtime.remove_readiness_listener(registry._cpu_listener)
loaded = {name for name in sys.modules if name.startswith('server.solver.beat_runtime.')}
assert loaded == {'server.solver.beat_runtime.provider'}, loaded
"""
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=20)
    assert not (tmp_path / "data").exists()


def test_default_off_official_notifications_cannot_reach_registry(monkeypatch):
    monkeypatch.delenv(provider.PROVIDER_ENV, raising=False)
    registry = EngineRegistry()
    before = registry._refresh_revision
    readiness.probe_cache_clear()
    assert registry._refresh_revision == before
    facade.remove_readiness_listener(registry._cpu_listener)


@pytest.mark.parametrize("hbb_ready,official_ready", [(False, True), (True, False)])
def test_production_registry_availability_follows_solve_adapter(selected, monkeypatch, tmp_path, hbb_ready, official_ready):
    julia = tmp_path / "julia"
    julia.write_text("fake executable")
    project = tmp_path / "project"
    project.mkdir()
    (project / "Project.toml").write_text("fixture project")
    package = _install_stub_package(monkeypatch, project=project, state=_ready_state(project, julia))
    package.beat_backend_statuses = lambda: {name: {"available": False} for name in beat.BEAT_BACKENDS}
    monkeypatch.setattr(beat, "_load_api", lambda: package if hbb_ready else None)
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        readiness.BackendReadiness(official_ready, "ready" if official_ready else "stale", "official proof"))

    async def scenario():
        registry = EngineRegistry(detector=lambda: detect_engines(names=("beat-cpu", "beat-metal")), cpu_refresh=True)
        try:
            entries = {entry.name: entry for entry in await registry.capabilities()}
            await registry._refresh_cpu_backend()
            assert entries["beat-cpu"].available == hbb_ready
            assert not entries["beat-metal"].available
            assert registry.official_runtime_statuses["cpu"]["available"] == official_ready
        finally:
            await registry.shutdown_prewarm()

    asyncio.run(scenario())


def test_stdout_burst_coalesces_notifications_and_readiness_computations(selected, monkeypatch):
    calls, notices = [], []
    monkeypatch.setattr(beat, "_load_api", lambda: None)
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        calls.append(backend) or readiness.BackendReadiness(True, "ready", "proof"))
    listener = lambda: (notices.append(True), _official_runtime_statuses())
    facade.add_readiness_listener(listener)
    try:
        monkeypatch.setattr(facade, "_preparation_in_flight", True)
        facade._record_step("instantiate")
        for line in range(1000):
            facade._provision_status(f"Pkg stdout {line}")
            assert _official_runtime_statuses()["cpu"]["state"] == "provisioning"
        facade._record_step("precompile")
        facade._record_step("precompile")
        monkeypatch.setattr(facade, "_preparation_in_flight", False)
        facade._notify_readiness_listeners()
        assert len(notices) <= 3
        assert calls == list(readiness.BACKENDS)  # Exactly one CPU identity computation.
    finally:
        facade.remove_readiness_listener(listener)


def test_backend_manifest_scope_preserves_cpu_after_metal_instantiate_failure(proved_cpu, monkeypatch):
    root, engine, _, saved, query, _, setup = proved_cpu
    metal = engine.root / "julia_metal"
    metal.mkdir()
    (metal / "Project.toml").write_text("metal project")
    manifest = metal / "Manifest-v1.12.toml"
    manifest.write_text("before")
    assert readiness.backend_readiness("cpu", root, **query).ready
    before_metal = identity.engine_fingerprint(engine, backend="metal")
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {"metal": {"available": True, "reason": "fixture"}})

    def fail(*args, **kwargs):
        manifest.write_text("changed Metal manifest bytes")
        raise RuntimeError("Metal instantiate failed")

    failed = readiness.provision_metal(root, **dict(setup, run_step=fail))
    assert failed["status"] == "failed"
    assert state.read_state(root, backend="cpu") == saved
    assert readiness.backend_readiness("cpu", root, **query).ready
    assert identity.engine_fingerprint(engine, backend="metal") != before_metal


@pytest.mark.parametrize("operation", ["provision", "clear-cache"])
def test_cross_process_state_stamp_refreshes_live_registry(selected, monkeypatch, tmp_path, operation):
    root = paths.runtime_dir()
    root.mkdir(parents=True)
    target = root / "state-cpu.json"
    target.write_text('{"ready": false}')
    computations = []

    def verdict(backend, *args, **kwargs):
        computations.append(backend)
        ready = json.loads(target.read_text())["ready"]
        return readiness.BackendReadiness(ready, "ready" if ready else "stale", "external record")

    monkeypatch.setattr(readiness, "backend_readiness", verdict)
    monkeypatch.setattr(beat, "_load_api", lambda: None)

    async def scenario():
        registry = EngineRegistry(detector=lambda: [EngineInfo("beat-cpu", False, "HBB absent", None)], cpu_refresh=True)
        try:
            await registry.capabilities()
            await registry._refresh_cpu_backend()
            assert not registry.official_runtime_statuses["cpu"]["available"]
            before = len(computations)
            await registry.capabilities()
            await registry._refresh_cpu_backend()
            assert len(computations) == before
            if operation == "provision":
                code = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('{\"ready\": true}')"
                command = [sys.executable, "-c", code, str(target)]
            else:
                # Keep bytes unchanged; external clear-cache must still invalidate.
                code = "from server.solver.beat_runtime.cli import main; import sys; sys.exit(main(sys.argv[1:]))"
                command = [sys.executable, "-c", code, "clear-cache", "--dir", str(root)]
                os.utime(target, ns=(1, 1))
                registry._check_official_state()
                await registry._refresh_cpu_backend()
                before = len(computations)
            subprocess.run(command, check=True, timeout=10)
            entries = await registry.capabilities()
            await registry._refresh_cpu_backend()
            assert len(computations) > before
            assert registry.official_runtime_statuses["cpu"]["available"] == (operation == "provision")
            assert not entries[0].available
        finally:
            await registry.shutdown_prewarm()

    asyncio.run(scenario())


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_cwd_independent_provision_command_platform_quoting(selected, monkeypatch, tmp_path, system):
    app_root = tmp_path / "app root 'quoted'"
    executable = r'C:\Program Files\WG\python.exe' if system == "Windows" else "/app root/python's executable"
    monkeypatch.setenv("WG2_APP_ROOT", str(app_root))
    monkeypatch.setattr(facade.sys, "executable", executable)
    monkeypatch.setattr(facade.platform, "system", lambda: system)
    code = (f"import sys, runpy; sys.path.insert(0, {str(app_root.resolve())!r}); "
            "runpy.run_module('server.solver.beat_runtime.cli', run_name='__main__')")
    command = [executable, "-c", code, "--backend", "cpu"]
    rendered = facade.provision_command()
    if system == "Windows":
        assert rendered == subprocess.list2cmdline(command)
    else:
        assert shlex.split(rendered) == command


def test_cwd_independent_provision_command_runs_from_unrelated_directory(selected, monkeypatch, tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "beat_engine.py").write_text("raise ImportError('fixture engine absent')")
    env = dict(os.environ, PYTHONPATH=str(fake))
    command = shlex.split(facade.provision_command())
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "not importable" in result.stderr
    assert not paths.runtime_dir().exists()


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_cpu_only_host_starts_no_gpu_check_thread_or_notifications(selected, monkeypatch, system):
    monkeypatch.setattr(readiness, "backend_readiness", lambda *args, **kwargs:
                        readiness.BackendReadiness(True, "ready", "ready CPU"))
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {"metal": {"available": False}})
    monkeypatch.setattr(facade, "_notify_readiness_listeners", lambda: pytest.fail("no work must not notify"))
    assert facade.start_cpu_provisioning(system=system) is None


@pytest.mark.parametrize("backend", ["cuda", "rocm"])
def test_unsupported_backend_explicit_cli_exit(backend, capsys):
    assert cli.main(["--backend", backend]) == 1
    assert "not supported in this build" in capsys.readouterr().out


def test_cross_process_provisioning_lock_prevents_interrupted_verdict(proved_cpu, monkeypatch):
    root, _, _, saved, query, _, _ = proved_cpu
    saved.update(status="in_progress", step="instantiate")
    state.write_state(saved, root)
    code = """
from pathlib import Path
import sys
from server.solver.beat_runtime.locks import provisioning_lock
root = Path(sys.argv[1])
with provisioning_lock(root, backend='cpu'):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    process = _REAL_POPEN([sys.executable, "-c", code, str(root)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        verdict = readiness.backend_readiness("cpu", root, **query)
        assert verdict.state == "provisioning" and "instantiate" in verdict.reason
        monkeypatch.setenv(provider.PROVIDER_ENV, "official")
        monkeypatch.setattr(facade, "_preparation_in_flight", False)
        assert facade.cpu_runtime_readiness(None).state == "provisioning"
    finally:
        try:
            process.communicate(input="\n", timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
    assert not locks.provisioning_active(root)
    assert readiness.backend_readiness("cpu", root, **query).state == "interrupted"


def test_selector_exact_value_warns_once_and_uses_process_environment(selected, monkeypatch, caplog):
    provider._warned_values.clear()
    for value in ("OFFICIAL", "Official", "hbb", "invalid"):
        assert not provider.official_selected({provider.PROVIDER_ENV: value})
        assert not provider.official_selected({provider.PROVIDER_ENV: value})
    assert len(caplog.records) == 4
    assert provider.official_selected({provider.PROVIDER_ENV: " official "})
    monkeypatch.setattr(facade, "_start_official_provisioning", lambda env: "official")
    # Launch options cannot silently select a different provider from status/listeners.
    assert facade.start_cpu_provisioning(environ={provider.PROVIDER_ENV: "hbb"}, system="Linux") == "official"
