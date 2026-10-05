from __future__ import annotations

from contextlib import contextmanager

import pytest

from server.solver.beat_runtime import assets, gpu, hardware, installer, locks, probe, provision, state, threads
from server.tests.beat_runtime.test_probe import COMPLETED, FakeWorker, result


@pytest.fixture
def metal_provisioning(cpu_provisioning, monkeypatch):
    root, cpu, julia, calls, cpu_options = cpu_provisioning
    provision.provision_cpu(**cpu_options)
    before = (root / "state-cpu.json").read_bytes()
    project = cpu.root / "julia_metal"
    project.mkdir()
    (project / "Project.toml").write_text("fake Metal project")
    metal = assets.EngineAssets(cpu.root, project, cpu.system_solver, cpu.source_solver)
    monkeypatch.setattr(assets, "engine_assets", lambda backend: metal if backend == "metal" else cpu)
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {
        "metal": {"available": True, "reason": "Apple Silicon"},
        "cuda": {"available": False, "reason": hardware.UNSUPPORTED},
        "rocm": {"available": False, "reason": hardware.UNSUPPORTED},
    })
    workers = []

    class Worker(FakeWorker):
        def __init__(self, **launch):
            super().__init__([result(backend="metal"), COMPLETED])
            self.launch = launch
            self.terminated = False
            workers.append(self)

        def terminate(self):
            self.terminated = True

    options = {key: value for key, value in cpu_options.items()
               if key not in {"probe", "probe_contract", "probe_fixture_identity"}}
    options.update(worker_factory=Worker, julia_threads="auto")
    monkeypatch.setattr(threads, "_performance_core_count", lambda: 8)
    calls.clear()
    return root, metal, julia, calls, workers, options, before


@pytest.mark.parametrize("backend", [None, "metal", "cuda", "rocm"])
def test_no_device_or_unsupported_backend_does_nothing(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: hardware_rows())

    def forbidden(*args, **kwargs):
        raise AssertionError("no-device path attempted provisioning")

    for module, name in ((locks, "provisioning_lock"), (assets, "engine_assets"),
                         (installer, "ensure_julia"), (probe, "fixture_identity")):
        monkeypatch.setattr(module, name, forbidden)
    root = tmp_path / "not-created"
    skipped = gpu.provision_gpu(root, backend=backend, status_cb=forbidden)
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == (hardware.UNSUPPORTED if backend in {"cuda", "rocm"} else "no device")
    assert not root.exists()


def hardware_rows():
    return {backend: {"available": False, "reason": reason} for backend, reason in (
        ("metal", "no device"), ("cuda", hardware.UNSUPPORTED), ("rocm", hardware.UNSUPPORTED),
    )}


def test_compiled_metal_wiring_and_cpu_independence(metal_provisioning, monkeypatch):
    root, metal, julia, calls, workers, options, before = metal_provisioning
    budgets = []
    original = installer.ensure_julia

    def ensure(*args, **kwargs):
        budgets.append(kwargs["required_bytes"])
        return original(*args, **kwargs)

    ready = gpu.provision_gpu(**options, backend=None, ensure_julia=ensure)
    assert ready["status"] == "ready" and ready["backend"] == "metal"
    assert ready == state.read_state(root, backend="metal")
    assert (root / "state-cpu.json").read_bytes() == before
    assert budgets == [installer.GPU_REQUIRED_FREE_BYTES]
    assert [code for code, _ in calls] == [
        "using Pkg; Pkg.instantiate()", "using Pkg; Pkg.precompile()",
        "import Metal; Metal.versioninfo(); exit(Metal.functional() ? 0 : 1)",
    ]
    assert all(call["project"] == metal.project for _, call in calls)
    assert not any("several GB" in call["label"] for _, call in calls)
    worker = workers[0]
    assert worker.launch == dict(julia_executable=str(julia), solver_script=metal.system_solver,
                                julia_project=metal.project, julia_threads=6,
                                environment=calls[0][1]["environment"], backend_label="Metal")
    assert worker.launch["environment"]["JULIA_NUM_THREADS"] == "6"
    assert worker.launch["environment"]["BLAB_BEAT_ENGINE_GPU_BACKEND"] == "metal"
    assert worker.request["schema_version"] == 1
    assert worker.request["solver_options"]["bem_backend"] == "metal"
    assert worker.request["frequencies_hz"] == [1000.0]
    assert worker.stream.closed and worker.terminated and not worker.path.exists()
    assert ready["probe_contract"] == probe.PROBE_CONTRACT
    assert ready["probe_fixture_identity"] == probe.fixture_identity()
    assert ready["completion"]["finite_nonzero"] is True
    assert ready["completion"]["result_count"] == ready["completion"]["solved_count"] == 1
    assert not (root / "state.json").exists()



def test_locked_recheck_reuses_only_metal(metal_provisioning, monkeypatch):
    root, _, _, calls, workers, options, before = metal_provisioning
    ready = gpu.provision_gpu(**options)
    (root / "state-metal.json").unlink()
    calls.clear()
    workers.clear()
    locked = False
    read = state.read_state

    @contextmanager
    def lock(*args, **kwargs):
        nonlocal locked
        assert kwargs["backend"] == "metal"
        state.write_state(ready, root)
        locked = True
        yield
        locked = False

    def locked_read(*args, **kwargs):
        assert locked
        return read(*args, **kwargs)

    monkeypatch.setattr(locks, "provisioning_lock", lock)
    monkeypatch.setattr(state, "read_state", locked_read)
    assert gpu.provision_gpu(**options)["status"] == "ready"
    assert not calls and not workers
    assert (root / "state-cpu.json").read_bytes() == before


@pytest.mark.parametrize("flag", ["force", "retry"])
def test_force_retry_and_guarded_callback(metal_provisioning, capsys, flag):
    _, _, _, calls, workers, options, _ = metal_provisioning
    gpu.provision_gpu(**options)
    calls.clear()
    workers.clear()

    def broken_console(message):
        raise UnicodeError("broken console")

    assert gpu.provision_gpu(**dict(options, status_cb=broken_console, **{flag: True}))["status"] == "ready"
    assert len(calls) == 3 and len(workers) == 1
    assert capsys.readouterr().err.count("status callback failed (UnicodeError)") == 1


def test_unknown_backend_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Unknown GPU backend"):
        gpu.provision_gpu(tmp_path, backend="cpu")
