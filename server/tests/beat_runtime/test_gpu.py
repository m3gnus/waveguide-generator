from __future__ import annotations

from contextlib import contextmanager

import pytest

from server.solver.beat_runtime import assets, gpu, hardware, installer, locks, manager, probe, provision, readiness, state, threads
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
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: {
        "metal": {"available": True, "reason": "Apple Silicon"},
        "cuda": {"available": False, "reason": "no device"},
        "rocm": {"available": False, "reason": "no device"},
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
def test_no_device_does_nothing(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: hardware_rows())

    def forbidden(*args, **kwargs):
        raise AssertionError("no-device path attempted provisioning")

    for module, name in ((locks, "provisioning_lock"), (assets, "engine_assets"),
                         (installer, "ensure_julia"), (probe, "fixture_identity")):
        monkeypatch.setattr(module, name, forbidden)
    root = tmp_path / "not-created"
    skipped = gpu.provision_gpu(root, backend=backend, status_cb=forbidden)
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "no device"
    assert not root.exists()


def hardware_rows():
    return {backend: {"available": False, "reason": reason} for backend, reason in (
        ("metal", "no device"), ("cuda", "no device"), ("rocm", "no device"),
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
    reads = []

    @contextmanager
    def lock(*args, **kwargs):
        nonlocal locked
        assert kwargs["backend"] == "metal"
        state.write_state(ready, root)
        locked = True
        yield
        locked = False

    def locked_read(*args, **kwargs):
        reads.append(locked)
        return read(*args, **kwargs)

    monkeypatch.setattr(locks, "provisioning_lock", lock)
    monkeypatch.setattr(state, "read_state", locked_read)
    assert gpu.provision_gpu(**options)["status"] == "ready"
    assert not calls and not workers
    assert reads == [False, True]
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


@pytest.mark.parametrize("backend,module,label", [("cuda", "CUDA", "CUDA"), ("rocm", "AMDGPU", "ROCm")])
def test_source_gpu_provision_readiness_and_launch_identity(cpu_provisioning, monkeypatch, backend, module, label):
    root, cpu, julia, calls, options = cpu_provisioning
    class CpuWorker(FakeWorker):
        def __init__(self, **launch):
            super().__init__([result(), COMPLETED])

        def terminate(self):
            pass

    cpu_setup = {key: value for key, value in options.items()
                 if key not in {"probe", "probe_contract", "probe_fixture_identity"}}
    readiness.provision_cpu(root, worker_factory=CpuWorker, **cpu_setup)
    before = (root / "state-cpu.json").read_bytes()
    project = cpu.root / f"julia_{backend}"
    project.mkdir()
    (project / "Project.toml").write_text(f"fixture {backend} project")
    engine = assets.EngineAssets(cpu.root, project, cpu.system_solver, cpu.source_solver)
    monkeypatch.setattr(assets, "engine_assets", lambda name: cpu if name == "cpu" else engine)
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs:
                        {name: {"available": name == backend, "reason": "inventory"}
                         for name in hardware.GPU_BACKENDS})
    workers, budgets = [], []

    class Worker(FakeWorker):
        def __init__(self, **launch):
            super().__init__([result(backend=backend), COMPLETED])
            self.worker_info["backends"][backend] = {"available": True}
            self.worker_info["compiled_worker"] = {
                "driver_mode": "source", "fallback_reason": "backend_has_no_compiled_bundle"}
            self.launch, self.terminated = launch, False
            workers.append(self)

        def terminate(self):
            self.terminated = True

    def ensure(*args, **kwargs):
        budgets.append(kwargs["required_bytes"])
        return str(julia)

    setup = {key: value for key, value in options.items()
             if key not in {"probe", "probe_contract", "probe_fixture_identity"}}
    calls.clear()
    saved = getattr(readiness, f"provision_{backend}")(root, worker_factory=Worker, ensure_julia=ensure, **setup)
    assert saved["status"] == "ready", saved
    assert budgets == [installer.GPU_REQUIRED_FREE_BYTES]
    assert [code for code, _ in calls] == ["using Pkg; Pkg.instantiate()", "using Pkg; Pkg.precompile()",
                                          f"import {module}; {module}.versioninfo(); exit({module}.functional() ? 0 : 1)"] + (
                                              [gpu.CUDSS_STEP] if backend == "cuda" else [])
    assert all(call["project"] == project for _, call in calls)
    assert (root / "state-cpu.json").read_bytes() == before
    assert workers[0].terminated and workers[0].stream.closed
    assert workers[0].launch["backend_label"] == label
    assert workers[0].request["solver_options"]["precision"] == "float32"
    assert workers[0].request["solver_options"]["bem_backend"] == backend
    query = {key: setup[key] for key in ("environ", "julia_executable", "julia_threads")}
    assert readiness.backend_readiness(backend, root, **query).ready
    key = manager.resolve_key(backend, environment=query["environ"], julia_executable=str(julia), julia_threads=3)
    assert key["backend"] == backend and key["julia_project"] == str(project)
    assert key["environment"]["BLAB_BEAT_ENGINE_GPU_BACKEND"] == backend
    # Matching records are reused without new artifacts or worker launches.
    assert gpu.provision_gpu(root, backend=backend, worker_factory=Worker, **setup) == saved
    assert len(workers) == 1
    saved["completion"]["bem_backend"] = "cpu"
    state.write_state(saved, root)
    assert not readiness.backend_readiness(backend, root, **query).ready
    assert readiness.backend_readiness("cpu", root, **query).ready


@pytest.mark.parametrize("backend", ["cuda", "rocm"])
def test_gpu_artifact_failure_preserves_cpu_and_records_backend(cpu_provisioning, monkeypatch, backend):
    root, _, _, _, options = cpu_provisioning
    provision.provision_cpu(root, **options)
    before = (root / "state-cpu.json").read_bytes()
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs:
                        {name: {"available": name == backend, "reason": "inventory"}
                         for name in hardware.GPU_BACKENDS})

    def fail(*args, **kwargs):
        raise RuntimeError("GPU artifacts offline")

    failed = gpu.provision_gpu(root, backend=backend, **dict(options, run_step=fail))
    assert failed["status"] == "failed" and failed["backend"] == backend
    assert "offline" in failed["error"]
    assert state.read_state(root, backend=backend) == failed
    assert (root / "state-cpu.json").read_bytes() == before


@pytest.mark.parametrize("backend", hardware.GPU_BACKENDS)
def test_low_depot_space_blocks_artifacts_with_existing_julia(cpu_provisioning, monkeypatch, tmp_path, backend):
    from types import SimpleNamespace

    root, _, julia, calls, options = cpu_provisioning
    provision.provision_cpu(root, **options)
    before = (root / "state-cpu.json").read_bytes()
    calls.clear()
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: {backend: {"available": True}})
    checked = []
    depot = tmp_path / "external-depot"
    depot.mkdir()
    monkeypatch.setattr(installer.shutil, "disk_usage", lambda path:
                        checked.append(path) or SimpleNamespace(free=installer.GPU_REQUIRED_FREE_BYTES - 1))
    assert julia.exists()
    failed = gpu.provision_gpu(root, backend=backend, **dict(options, depot=depot))
    assert failed["status"] == "failed" and failed["step"] == "check_disk_space"
    assert "Not enough free disk space" in failed["error"]
    assert checked == [depot]
    assert not calls
    assert (root / "state-cpu.json").read_bytes() == before


def test_cudss_step_is_optional_like_the_engine():
    # The engine loads CUDSS in a try; a machine without it still solves exterior CUDA.
    assert gpu.CUDSS_STEP.startswith("try; import CUDSS; catch")
    assert "@warn" in gpu.CUDSS_STEP and gpu.CUDSS_STEP.endswith("end")


@pytest.mark.parametrize("backend", hardware.GPU_BACKENDS)
def test_fresh_julia_install_is_not_checked_twice(cpu_provisioning, monkeypatch, tmp_path, backend):
    from types import SimpleNamespace

    root, _, julia, calls, options = cpu_provisioning
    monkeypatch.setattr(hardware, "gpu_hardware", lambda **kwargs: {backend: {"available": True}})
    checked = []
    monkeypatch.setattr(installer.shutil, "disk_usage", lambda path:
                        checked.append(path) or SimpleNamespace(free=installer.GPU_REQUIRED_FREE_BYTES - 1))

    def fresh(directory, **kwargs):
        # ensure_julia's own budget check already ran for this download.
        executable = root.absolute() / "julia" / "julia-fresh" / "bin" / "julia"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(julia.read_bytes())
        executable.chmod(0o755)
        return str(executable)

    saved = gpu.provision_gpu(root, backend=backend, **dict(options, ensure_julia=fresh))
    assert checked == []
    # Setup ran past the budget check (the probe itself is not under test here).
    assert saved["step"] == f"{backend}_probe", saved
