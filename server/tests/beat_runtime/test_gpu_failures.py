from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import assets, gpu, hardware, probe, state
from server.tests.beat_runtime.test_gpu import metal_provisioning as metal_provisioning
from server.tests.beat_runtime.test_probe import COMPLETED, result


@pytest.mark.parametrize("failing", ["instantiate", "precompile", "metal_device", "metal_probe", "assets"])
def test_failure_and_retry_preserve_cpu(metal_provisioning, monkeypatch, failing):
    root, _, _, calls, workers, options, before = metal_provisioning
    broken = dict(options)

    def step(*args, **kwargs):
        if failing in args[1] or failing == "metal_device" and "Metal" in args[1]:
            raise RuntimeError("artifact or functionality failure")

    broken["run_step"] = step
    if failing == "metal_probe":
        factory = options["worker_factory"]

        def bad_worker(**kwargs):
            worker = factory(**kwargs)
            worker.stream.events = [result(real=0, imag=0, backend="metal"), COMPLETED]
            return worker

        broken["worker_factory"] = bad_worker
    original_assets = assets.engine_assets
    if failing == "assets":
        def absent(*args):
            raise assets.AssetsUnavailable("missing wheel assets")
        monkeypatch.setattr(assets, "engine_assets", absent)
    failed = gpu.provision_gpu(**broken)
    assert failed["status"] == "failed"
    assert failed["step"] == ("resolve_assets" if failing == "assets" else failing)
    assert failed["completion"] == {}
    assert (root / "state-cpu.json").read_bytes() == before
    assert state.read_state(root, backend="metal") == failed
    assert all(worker.terminated and worker.stream.closed for worker in workers)
    monkeypatch.setattr(assets, "engine_assets", original_assets)
    assert gpu.provision_gpu(**options)["status"] == "ready"


def test_worker_retirement_failure_is_not_ready(metal_provisioning):
    root, _, _, _, workers, options, before = metal_provisioning
    factory = options["worker_factory"]

    def refuse_retirement(**kwargs):
        worker = factory(**kwargs)

        def fail():
            raise RuntimeError("retirement failed")

        worker.terminate = fail
        return worker

    failed = gpu.provision_gpu(**dict(options, worker_factory=refuse_retirement))
    assert failed["status"] == "failed" and "retirement failed" in failed["error"]
    assert workers[0].stream.closed
    assert (root / "state-cpu.json").read_bytes() == before


@pytest.mark.parametrize("backend,functional", [("cpu", True), ("metal", False)])
def test_device_check_cannot_substitute_for_compiled_metal(metal_provisioning, backend, functional):
    root, _, _, calls, workers, options, before = metal_provisioning
    factory = options["worker_factory"]

    def wrong_worker(**kwargs):
        worker = factory(**kwargs)
        worker.stream.events = [result(backend=backend), COMPLETED]
        worker.worker_info["backends"]["metal"]["available"] = functional
        return worker

    failed = gpu.provision_gpu(**dict(options, worker_factory=wrong_worker))
    assert len(calls) == 3  # Artifact/functionality step returned successfully.
    assert failed["status"] == "failed" and failed["step"] == "metal_probe"
    assert (root / "state-cpu.json").read_bytes() == before
    assert workers[0].terminated and workers[0].stream.closed


@pytest.mark.parametrize("field", ["probe_contract", "probe_fixture_identity"])
def test_default_probe_refuses_false_identity(metal_provisioning, field):
    root, _, _, _, workers, options, before = metal_provisioning
    failed = gpu.provision_gpu(**dict(options, **{field: "wrong"}))
    assert failed["status"] == "failed" and failed["step"] == "metal_probe"
    assert workers[0].terminated
    assert (root / "state-cpu.json").read_bytes() == before


@pytest.mark.parametrize("change", ["project", "threads", "executable", "environment", "fixture"])
def test_changed_metal_identity_requires_new_compiled_solve(metal_provisioning, monkeypatch, tmp_path, change):
    _, metal, julia, calls, workers, options, _ = metal_provisioning
    gpu.provision_gpu(**options)
    calls.clear()
    workers.clear()
    if change == "project":
        (metal.project / "Project.toml").write_text("edited Metal project")
    elif change == "threads":
        options["julia_threads"] = 2
    elif change == "executable":
        julia.write_bytes(b"changed executable")
    elif change == "environment":
        options["environ"]["BLAB_POLICY"] = "changed"
    else:
        fixture = tmp_path / "probe.msh"
        fixture.write_bytes(probe._FIXTURE.read_bytes() + b"\n")
        monkeypatch.setattr(probe, "_FIXTURE", fixture)
    ready = gpu.provision_gpu(**options)
    assert ready["status"] == "ready"
    assert len(calls) == 3 and len(workers) == 1
    assert workers[0].stream.closed and workers[0].terminated


def test_default_worker_uses_optional_public_api(metal_provisioning, monkeypatch):
    _, _, _, _, workers, options, _ = metal_provisioning
    factory = options.pop("worker_factory")
    monkeypatch.setitem(sys.modules, "beat_engine", SimpleNamespace(EngineWorker=factory))
    assert gpu.provision_gpu(**options)["status"] == "ready"
    assert workers[0].launch["backend_label"] == "Metal" and workers[0].terminated


def test_optional_package_missing_is_recorded(metal_provisioning, monkeypatch):
    root, _, _, _, _, options, before = metal_provisioning
    options.pop("worker_factory")
    monkeypatch.setitem(sys.modules, "beat_engine", None)
    failed = gpu.provision_gpu(**options)
    assert failed["status"] == "failed" and failed["step"] == "metal_probe"
    assert "beat_engine" in failed["error"]
    assert (root / "state-cpu.json").read_bytes() == before


def test_no_device_preserves_both_existing_records(metal_provisioning, monkeypatch):
    root, _, _, _, _, options, before = metal_provisioning
    gpu.provision_gpu(**options)
    metal_before = (root / "state-metal.json").read_bytes()
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {
        "metal": {"available": False, "reason": "no device"},
    })
    assert gpu.provision_gpu(**dict(options, force=True))["status"] == "skipped"
    assert (root / "state-cpu.json").read_bytes() == before
    assert (root / "state-metal.json").read_bytes() == metal_before
    assert state.read_state(root, backend="cpu")["status"] == "ready"


@pytest.mark.parametrize("missing", ["both", "probe_contract", "probe_fixture_identity"])
def test_custom_metal_probe_requires_declared_identity(metal_provisioning, missing):
    root, _, _, _, workers, options, before = metal_provisioning
    calls = []

    def custom(**kwargs):
        calls.append(kwargs)
        return {"finite": True, "nonzero": True, "terminal_count": 1, "bem_backend": "metal"}

    options.update(probe=custom, probe_contract="custom-contract", probe_fixture_identity="custom-fixture")
    for field in ("probe_contract", "probe_fixture_identity"):
        if missing in {"both", field}:
            options.pop(field)
    failed = gpu.provision_gpu(**options)
    assert failed["status"] == "failed" and "identity are required" in failed["error"]
    assert not calls and not workers
    assert (root / "state-cpu.json").read_bytes() == before


@pytest.mark.parametrize("backend_evidence", [None, "cpu", "metal"])
def test_custom_metal_completion_requires_backend_evidence(metal_provisioning, backend_evidence):
    root, _, _, _, _, options, before = metal_provisioning
    completion = {"finite": True, "nonzero": True, "terminal_count": 1}
    if backend_evidence is not None:
        completion["bem_backend"] = backend_evidence
    result = gpu.provision_gpu(**options, probe=lambda **kw: completion,
                               probe_contract="custom-contract", probe_fixture_identity="custom-fixture")
    assert result["status"] == ("ready" if backend_evidence == "metal" else "failed")
    assert (root / "state-cpu.json").read_bytes() == before


def test_custom_probe_cannot_seed_default_metal_readiness(metal_provisioning):
    _, _, _, calls, workers, options, _ = metal_provisioning
    completion = {"finite": True, "nonzero": True, "terminal_count": 1, "bem_backend": "metal"}
    # Even an injection declaring the built-in labels cannot claim default proof.
    ready = gpu.provision_gpu(**options, probe=lambda **kw: completion,
                              probe_contract=probe.PROBE_CONTRACT, probe_fixture_identity=probe.fixture_identity())
    assert ready["status"] == "ready" and not workers
    assert ready["probe_contract"] == f"custom:{probe.PROBE_CONTRACT}"
    calls.clear()
    default = gpu.provision_gpu(**options)
    assert default["status"] == "ready" and len(workers) == 1 and len(calls) == 3
    assert default["probe_contract"] == probe.PROBE_CONTRACT
    assert default["completion"]["bem_backend"] == "metal"


def test_default_metal_reproves_record_without_backend_evidence(metal_provisioning):
    root, _, _, calls, workers, options, _ = metal_provisioning
    ready = gpu.provision_gpu(**options)
    ready["completion"].pop("bem_backend")
    state.write_state(ready, root)
    calls.clear()
    workers.clear()
    assert gpu.provision_gpu(**options)["status"] == "ready"
    assert len(workers) == 1 and len(calls) == 3


@pytest.mark.parametrize("destination", ["project", "depot", "depot_symlink"])
def test_metal_julia_write_destinations_refuse_hbb(metal_provisioning, tmp_path, monkeypatch, destination):
    root, _, _, calls, workers, options, before = metal_provisioning
    legacy = tmp_path / "hbb"
    legacy.mkdir()
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(legacy))
    options["environ"]["HORNLAB_BEAT_RUNTIME_DIR"] = str(legacy)
    if destination == "project":
        options["julia_project"] = legacy / "julia_metal"
    elif destination == "depot":
        options["environ"]["JULIA_DEPOT_PATH"] = str(legacy / "depot")
    else:
        (root / "depot").symlink_to(legacy, target_is_directory=True)
    failed = gpu.provision_gpu(**options)
    assert failed["status"] == "failed" and "overlaps" in failed["error"]
    assert not workers and not calls and list(legacy.iterdir()) == []
    assert (root / "state-cpu.json").read_bytes() == before
