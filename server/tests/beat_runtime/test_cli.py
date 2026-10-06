from __future__ import annotations

import json

import pytest

from server.solver.beat_runtime import assets, cli, hardware, readiness


@pytest.mark.parametrize("backend,status,code", [("cpu", "ready", 0), ("cpu", "failed", 1),
                                                 ("metal", "ready", 0), ("metal", "failed", 1)])
def test_provision_flags_and_exit_codes(tmp_path, monkeypatch, backend, status, code):
    calls = []
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {"metal": {"available": True}})
    monkeypatch.setattr(assets, "engine_assets", lambda name: None)

    def action(directory, **options):
        calls.append((directory, options))
        return {"status": status}

    monkeypatch.setattr(readiness, "provision_cpu" if backend == "cpu" else "provision_metal", action)
    root = tmp_path / "runtime"
    assert cli.main(["provision", "--backend", backend, "--force", "--retry", "--dir", str(root),
                     "--julia", "explicit", "--project", str(tmp_path / "project"),
                     "--threads", "2", "--depot", str(tmp_path / "depot")]) == code
    assert calls == [(root, dict(force=True, retry=True, julia_executable="explicit",
                                julia_project=tmp_path / "project", julia_threads=2, depot=tmp_path / "depot"))]
    assert not root.exists()


@pytest.mark.parametrize("args", [["--if-gpu"], ["--if-nvidia-gpu"], ["--backend", "metal", "--if-gpu"],
                                  ["--backend", "cuda", "--if-gpu"], ["--backend", "rocm", "--if-gpu"]])
def test_hardware_gate_is_quiet_noop_without_engine(monkeypatch, capsys, args):
    monkeypatch.setattr(hardware, "detect_gpu_backend", lambda: None)
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {"metal": {"available": False}})
    monkeypatch.setattr(assets, "engine_assets", lambda *args: pytest.fail("hardware gate bypassed"))
    assert cli.main(args) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("gate", ["--if-gpu", "--if-nvidia-gpu"])
def test_cpu_with_gpu_gate_is_usage_error(gate):
    with pytest.raises(SystemExit) as error:
        cli.main(["--backend", "cpu", gate])
    assert error.value.code == 2


@pytest.mark.parametrize("threads", ["0", "-1", "invalid", "1.5"])
def test_bad_threads_are_usage_error(threads):
    with pytest.raises(SystemExit) as error:
        cli.main(["--backend", "cpu", "--threads", threads])
    assert error.value.code == 2


def test_missing_engine_is_nonfatal_to_setup_and_creates_nothing(tmp_path, monkeypatch, capsys):
    def absent(backend):
        raise assets.AssetsUnavailable("Optional beat-engine package is not importable")

    monkeypatch.setattr(assets, "engine_assets", absent)
    monkeypatch.setattr(readiness, "provision_cpu", lambda *args, **kwargs: pytest.fail("no engine must not install"))
    root = tmp_path / "runtime"
    assert cli.main(["--backend", "cpu", "--dir", str(root)]) == 1
    assert "beat-engine" in capsys.readouterr().err
    assert not root.exists()


def test_status_without_engine_and_cache_clear_are_read_only(tmp_path, monkeypatch, capsys):
    def absent(backend):
        raise assets.AssetsUnavailable("Optional engine absent")

    monkeypatch.setattr(assets, "engine_assets", absent)
    root = tmp_path / "runtime"
    assert cli.main(["status", "--backend", "cpu", "--dir", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)["cpu"]
    assert not status["available"] and status["state"] == "package-unusable"
    calls = []
    monkeypatch.setattr(readiness, "probe_cache_clear", lambda **kwargs: calls.append(kwargs))
    assert cli.main(["clear-cache", "--dir", str(root)]) == 0
    assert calls == [dict(directory=root, persist=True)] and not root.exists()


def test_default_auto_never_provisions_cpu(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu_backend", lambda: None)
    monkeypatch.setattr(readiness, "provision_cpu", lambda *args, **kwargs: pytest.fail("CPU needs opt-in"))
    assert cli.main([]) == 0


def test_auto_gpu_dispatches_metal(monkeypatch):
    monkeypatch.setattr(hardware, "detect_gpu_backend", lambda: "metal")
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {"metal": {"available": True}})
    monkeypatch.setattr(assets, "engine_assets", lambda backend: None)
    calls = []
    monkeypatch.setattr(readiness, "provision_metal", lambda *args, **kwargs: calls.append(kwargs) or {"status": "ready"})
    assert cli.main(["--if-gpu"]) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("depot_chain", [False, True], ids=["matching-depot", "broker-depot-mismatch"])
def test_provision_explicit_external_julia_then_status_without_julia(
    cpu_provisioning, tmp_path, monkeypatch, capsys, depot_chain,
):
    import os

    from server.solver.beat_runtime import discovery, paths, state
    from server.tests.beat_runtime.test_probe import COMPLETED, FakeWorker, result

    _, _, julia, _, setup = cpu_provisioning
    # Mirror the CLI's explicit --dir and --depot, and the inherited broker env.
    root, depot = tmp_path / "runtime", tmp_path / "depot"
    for name in list(os.environ):
        if name.startswith(("JULIA_", "BLAB_")):
            monkeypatch.delenv(name)
    monkeypatch.delenv(discovery.JULIA_ENV_VAR, raising=False)
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(root))
    inherited = str(depot) + (os.pathsep + str(tmp_path / "cached-depot") if depot_chain else "")
    monkeypatch.setenv("JULIA_DEPOT_PATH", inherited)
    monkeypatch.setenv("JULIA_PKG_OFFLINE", "true")
    monkeypatch.setenv("JULIA_NUM_THREADS", "1")
    monkeypatch.setattr(discovery.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(readiness.threads, "resolve_julia_threads", lambda backend, count: 8)

    class Worker(FakeWorker):
        def __init__(self, **launch):
            super().__init__([result(), COMPLETED])

        def terminate(self):
            pass

    original = readiness.provision_cpu
    monkeypatch.setattr(readiness, "provision_cpu", lambda *a, **k:
                        original(*a, **k, run_step=setup["run_step"], worker_factory=Worker))
    assert cli.main(["provision", "--backend", "cpu", "--dir", str(root),
                     "--julia", str(julia), "--depot", str(depot)]) == 0
    assert "BEAT CPU runtime is ready." in capsys.readouterr().out
    saved = state.read_state(root, backend="cpu")
    assert saved["status"] == "ready" and saved["julia_version"] is None
    record = state.read_julia(root)
    assert record["origin"] == "external" and record["selection"] == "explicit" and record["version"] is None
    assert discovery.discover_julia(root=root) == str(julia)
    assert cli.main(["status", "--backend", "cpu", "--dir", str(root)]) == 0
    status = json.loads(capsys.readouterr().out)["cpu"]
    assert status["state"] == ("stale" if depot_chain else "ready")
    assert status["available"] is not depot_chain
    assert readiness.backend_readiness("cpu", root).ready is not depot_chain
    # The original broker's depot chain is a different launch identity; repeating
    # --julia alone cannot make that identity ready. Matching --depot does.
    assert cli.main(["status", "--backend", "cpu", "--dir", str(root), "--depot", str(depot)]) == 0
    assert json.loads(capsys.readouterr().out)["cpu"]["available"]
    # An environment override is a base, unlike the explicit CLI directory.
    assert paths.runtime_dir() == root / paths.PROVIDER_ID
    assert not readiness.backend_readiness("cpu").ready
    # Do not execute, download or version-probe Julia even for later queries.
    julia.write_bytes(b"changed executable")
    assert not readiness.backend_readiness("cpu", root, depot=depot).ready
