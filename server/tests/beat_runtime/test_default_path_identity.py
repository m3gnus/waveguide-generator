"""The explicit HBB rollback preserves its provisioning and capability presentation."""

from __future__ import annotations

import os
from types import SimpleNamespace
from pathlib import Path

import pytest

from server.diagnostics import capabilities
from server.solver import beat_cpu_runtime


def test_rollback_provision_command_text_is_unchanged(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "hbb")
    monkeypatch.setattr(beat_cpu_runtime.sys, "executable", "/Apps/Waveguide Generator.app/bin/python3")
    assert beat_cpu_runtime.provision_command() == (
        '"/Apps/Waveguide Generator.app/bin/python3" -m hornlab_beat_bem.provision --backend cpu'
    )


def test_rollback_status_lines_still_record_and_notify(monkeypatch):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "hbb")
    seen = []
    monkeypatch.setattr(beat_cpu_runtime, "_record_step", lambda message: seen.append(("step", message)))
    monkeypatch.setattr(beat_cpu_runtime, "_notify_readiness_listeners", lambda: seen.append(("notify",)))
    beat_cpu_runtime._provision_status("Precompiling")
    assert seen == [("step", "Precompiling"), ("notify",)]


def test_official_status_lines_do_not_notify(monkeypatch):
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    seen = []
    monkeypatch.setattr(beat_cpu_runtime, "_notify_readiness_listeners", lambda: seen.append("notify"))
    beat_cpu_runtime._provision_status("Precompiling")
    assert seen == []


def test_capabilities_omit_official_runtime_unless_selected(monkeypatch):
    registry = SimpleNamespace(official_runtime_statuses={"cpu": "ready"})
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "hbb")
    assert capabilities._official_beat_runtime(registry) == {}
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    assert capabilities._official_beat_runtime(registry) == {"officialBeatRuntime": {"cpu": "ready"}}


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
def test_default_provision_command_uses_official(monkeypatch, backend):
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    command = beat_cpu_runtime.provision_command(backend=backend)
    assert "server.solver.beat_runtime.cli" in command
    assert command.endswith(f" --backend {backend}")


@pytest.mark.parametrize("value,official", [
    (None, True), ("", True), (" OffICIal ", True), (" HBB ", False), (" Legacy ", False),
])
def test_provider_values_are_case_insensitive(monkeypatch, caplog, value, official):
    from server.solver.beat_runtime import provider

    if value is None:
        monkeypatch.delenv(provider.PROVIDER_ENV, raising=False)
    else:
        monkeypatch.setenv(provider.PROVIDER_ENV, value)
    assert provider.official_selected() is official
    assert provider.official_selected({provider.PROVIDER_ENV: value or ""}) is official
    assert not caplog.records


def test_unknown_provider_warns_once_after_normalizing(monkeypatch, caplog):
    from server.solver.beat_runtime import provider

    monkeypatch.setattr(provider, "_warned_values", set())
    for value in (" future-provider ", "FUTURE-PROVIDER", "future-provider"):
        assert provider.official_selected({provider.PROVIDER_ENV: value})
    assert len(caplog.records) == 1
    assert "using the default official provider" in caplog.text


def test_default_preparation_ignores_existing_hbb_state(tmp_path, monkeypatch):
    from server.solver.beat_runtime import hardware, paths, readiness, state

    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    legacy = tmp_path / "hbb-runtime"
    legacy.mkdir()
    old = legacy / "state-cpu.json"
    old.write_text('{"status": "ready", "julia_executable": "old-hbb-julia"}')
    binary = legacy / "julia"
    binary.write_bytes(b"existing HBB runtime")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in legacy.iterdir()}
    env = {"HORNLAB_BEAT_RUNTIME_DIR": str(legacy), "WG2_BEAT_RUNTIME_DIR": str(tmp_path / "official")}
    expected = paths.runtime_dir(environ=env)
    monkeypatch.setattr(beat_cpu_runtime, "_provision_thread", None)
    monkeypatch.setattr(beat_cpu_runtime, "_preparation_in_flight", False)
    monkeypatch.setattr(beat_cpu_runtime, "_runtimes_prepared", False)
    monkeypatch.setattr(beat_cpu_runtime, "_import", lambda name: pytest.fail(f"HBB import: {name}"))
    monkeypatch.setattr(beat_cpu_runtime, "_notify_readiness_listeners", lambda: None)
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: {"metal": {"available": False}})
    monkeypatch.setattr(readiness, "expected_identity", lambda *args, **kwargs: {"julia_executable": None})
    # Real readiness reads the official root before asset discovery; it must not read HBB.
    reads = []
    read_state = state.read_state
    def guarded_read(directory, **kwargs):
        reads.append(directory)
        assert directory == expected
        return read_state(directory, **kwargs)
    monkeypatch.setattr(state, "read_state", guarded_read)
    prepared = []
    def provision(**kwargs):
        root = paths.runtime_dir(environ=kwargs["environ"])
        prepared.append(root)
        root.mkdir(parents=True)
        (root / "state-cpu.json").write_text('{"status": "in_progress"}')
    monkeypatch.setattr(readiness, "provision_cpu", provision)
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        assert not path.is_relative_to(legacy), f"HBB runtime accessed: {path}"
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        thread = beat_cpu_runtime.start_cpu_provisioning(environ=env, system="Linux")
        assert thread is not None
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert reads == [expected] and prepared == [expected]
    assert expected.name == paths.PROVIDER_ID
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in legacy.iterdir()} == before


def test_default_runtime_discovery_and_worker_roots_stay_in_suite_sandbox(
    sandbox_data_dir, monkeypatch,
):
    from server.solver.beat_runtime import discovery, paths, readiness, state

    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    runtime = paths.runtime_dir()
    workers = paths.worker_dir()
    configured_julia = Path(os.environ[discovery.JULIA_ENV_VAR])
    for path in (runtime, workers, configured_julia):
        assert path.is_relative_to(sandbox_data_dir)
    assert runtime != workers
    assert not configured_julia.exists()

    # Configured absence must stop discovery rather than reuse Julia from PATH
    # or a prepared runtime. No real process is launched by this lookup.
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: pytest.fail("PATH Julia lookup"))
    with pytest.raises(discovery.JuliaDiscoveryError, match="Invalid configured Julia"):
        discovery.discover_julia()

    reads = []
    original_read = state.read_state
    def guarded_read(directory, **kwargs):
        reads.append(directory)
        assert directory == runtime
        return original_read(directory, **kwargs)
    monkeypatch.setattr(state, "read_state", guarded_read)
    # Exercise the real readiness state lookup; supplied hardware facts bypass
    # only hardware discovery and the unrelated launch-signature cache.
    verdict = readiness.backend_readiness("cpu", hardware_facts={})
    assert not verdict.ready
    assert reads == [runtime]
