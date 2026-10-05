from __future__ import annotations

import json

import pytest

from server.solver.beat_runtime import assets, gpu, hardware, identity, probe, readiness, state
from server.tests.beat_runtime.test_probe import COMPLETED, FakeWorker, result


@pytest.fixture
def proved_cpu(cpu_provisioning):
    root, engine, julia, calls, options = cpu_provisioning
    workers = []

    class Worker(FakeWorker):
        def __init__(self, **launch):
            super().__init__([result(), COMPLETED])
            self.launch = launch
            self.terminated = False
            workers.append(self)

        def terminate(self):
            self.terminated = True

    setup = {key: value for key, value in options.items()
             if key not in {"probe", "probe_contract", "probe_fixture_identity"}}
    saved = readiness.provision_cpu(root, worker_factory=Worker, **setup)
    assert saved["status"] == "ready", saved
    query = {key: setup[key] for key in ("environ", "julia_executable", "julia_threads")}
    return root, engine, julia, saved, query, workers, setup


def test_compiled_cpu_wiring_proves_current_launch(proved_cpu):
    root, _, julia, saved, query, workers, _ = proved_cpu
    verdict = readiness.backend_readiness("cpu", root, **query)
    assert verdict.ready and verdict.state == "ready"
    assert workers[0].terminated and workers[0].stream.closed
    assert workers[0].launch["julia_threads"] == 3
    assert workers[0].launch["environment"]["JULIA_NUM_THREADS"] == "3"
    assert workers[0].launch["julia_executable"] == str(julia)
    assert saved["probe_contract"] == probe.PROBE_CONTRACT
    assert saved["completion"]["finite_nonzero"] is True


@pytest.mark.parametrize("field", [
    "project", "engine_fingerprint", "runtime_fingerprint", "julia_executable",
    "julia_version", "julia_identity", "depot", "sysimage", "sysimage_identity",
    "probe_contract", "probe_fixture_identity", "environment",
])
def test_every_launch_identity_field_revokes_readiness(proved_cpu, field):
    root, _, _, saved, query, _, _ = proved_cpu
    saved[field] = {"BLAB_CHANGED": "1"} if field == "environment" else "changed"
    state.write_state(saved, root)
    assert readiness.backend_readiness("cpu", root, **query).state == "stale"


@pytest.mark.parametrize("field,value", [
    ("finite", False), ("nonzero", False), ("terminal_count", True), ("terminal_count", 2),
    ("finite_nonzero", False), ("result_count", True), ("result_count", 0),
    ("solved_count", 2), ("bem_backend", "metal"),
])
def test_incomplete_or_vacuous_saved_proof_is_unavailable(proved_cpu, field, value):
    root, _, _, saved, query, _, _ = proved_cpu
    saved["completion"][field] = value
    state.write_state(saved, root)
    assert not readiness.backend_readiness("cpu", root, **query).ready


@pytest.mark.parametrize("changed", ["source", "project", "julia", "runtime", "fixture", "threads", "environment", "removed"])
def test_success_is_not_stale_cached(proved_cpu, monkeypatch, changed):
    root, engine, julia, _, query, _, _ = proved_cpu
    assert readiness.backend_readiness("cpu", root, **query).ready
    if changed in {"source", "project"}:
        path = engine.root / "__init__.py" if changed == "source" else engine.project / "Project.toml"
        path.write_text("changed bytes")
    elif changed == "julia":
        julia.write_bytes(b"changed executable")
    elif changed == "removed":
        julia.unlink()
    elif changed == "runtime":
        monkeypatch.setattr(identity, "runtime_fingerprint", lambda: "new-runtime")
    elif changed == "fixture":
        monkeypatch.setattr(probe, "fixture_identity", lambda: "new-fixture")
    elif changed == "threads":
        query["julia_threads"] = 4
    else:
        query["environ"]["BLAB_NEW_OPTION"] = "1"
    assert not readiness.backend_readiness("cpu", root, **query).ready


@pytest.mark.parametrize("status", ["failed", "in_progress"])
def test_failed_and_interrupted_records_have_independent_reasons(proved_cpu, status):
    root, _, _, saved, query, _, _ = proved_cpu
    saved.update(status=status, step="instantiate", error="offline")
    state.write_state(saved, root)
    verdict = readiness.backend_readiness("cpu", root, **query)
    assert verdict.state == ("failed" if status == "failed" else "interrupted")
    assert ("offline" if status == "failed" else "instantiate") in verdict.reason


def test_metal_failure_leaves_cpu_ready(proved_cpu, monkeypatch):
    root, _, _, _, query, _, setup = proved_cpu
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {
        "metal": {"available": True, "reason": "eligible"},
    })
    before = (root / "state-cpu.json").read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("Metal artifacts offline")

    failed = gpu.provision_gpu(root, backend="metal", **dict(setup, run_step=fail))
    assert failed["status"] == "failed"
    assert (root / "state-cpu.json").read_bytes() == before
    statuses = readiness.beat_backend_statuses(root, **query)
    assert statuses["cpu"]["available"]
    assert not statuses["metal"]["available"] and "offline" in statuses["metal"]["reason"]
    assert readiness.beat_engine_status(root, **query)["backend"] == "cpu"
    for backend in ("cuda", "rocm"):
        assert statuses[backend]["reason"] == "not supported in this build"


@pytest.mark.parametrize("payload", ["{", '{"provider":"hornlab-beat"}', "{}"])
def test_corrupt_or_foreign_records_never_prove_ready(proved_cpu, payload):
    root, _, _, _, query, _, _ = proved_cpu
    (root / "state-cpu.json").write_text(payload)
    assert readiness.backend_readiness("cpu", root, **query).state == "unprovisioned"


def test_static_catalog_and_hardware_do_not_mean_usable(cpu_provisioning, monkeypatch):
    root, _, _, _, options = cpu_provisioning
    monkeypatch.setattr(hardware, "gpu_hardware", lambda: {"metal": {"available": True, "reason": "eligible"}})
    for backend in ("cpu", "metal"):
        verdict = readiness.backend_readiness(backend, root, environ=options["environ"], julia_executable=options["julia_executable"], julia_threads=3)
        assert not verdict.ready and verdict.state == "unprovisioned"
    assert not root.exists()


def test_missing_optional_assets_are_an_unavailable_reason(tmp_path, monkeypatch):
    def absent(*args):
        raise assets.AssetsUnavailable("Optional beat-engine is absent")

    monkeypatch.setattr(assets, "engine_assets", absent)
    verdict = readiness.backend_readiness("cpu", tmp_path / "runtime", environ={"PATH": ""})
    assert not verdict.ready and verdict.state == "package-unusable"
    assert not (tmp_path / "runtime").exists()


def test_invalidation_callbacks_are_guarded_and_removable():
    events = []

    def broken():
        raise RuntimeError("listener failed")

    def listener():
        events.append("changed")
        readiness.remove_readiness_listener(listener)

    readiness.add_readiness_listener(broken)
    readiness.add_readiness_listener(listener)
    readiness.add_readiness_listener(listener)
    try:
        readiness.probe_cache_clear(notify=False)
        assert not events
        readiness.probe_cache_clear()
        readiness.probe_cache_clear()
        assert events == ["changed"]
    finally:
        readiness.remove_readiness_listener(broken)
        readiness.remove_readiness_listener(listener)


def test_ready_without_numerical_evidence_stays_unavailable(proved_cpu):
    root, _, _, saved, query, _, _ = proved_cpu
    saved["completion"] = {"finite": True, "nonzero": True, "terminal_count": 1}
    state.write_state(saved, root)
    assert not readiness.backend_readiness("cpu", root, **query).ready
    assert json.loads((root / "state-cpu.json").read_text())["status"] == "ready"


def test_incomplete_builtin_record_is_reproved_without_force(proved_cpu):
    root, _, _, saved, query, workers, setup = proved_cpu
    saved["completion"] = {"finite": True, "nonzero": True, "terminal_count": 1}
    state.write_state(saved, root)
    before = len(workers)
    refreshed = readiness.provision_cpu(root, worker_factory=type(workers[0]), **setup)
    assert refreshed["completion"]["finite_nonzero"] is True
    assert len(workers) == before + 1
    assert readiness.backend_readiness("cpu", root, **query).ready


def test_configured_older_managed_julia_is_allowed_but_default_needs_upgrade(proved_cpu, monkeypatch):
    root, _, julia, saved, _, _, _ = proved_cpu
    older = root / "julia" / "1.12.6" / "bin" / "julia"
    older.parent.mkdir(parents=True)
    older.write_bytes(julia.read_bytes())
    older.chmod(0o755)
    state.write_julia({"origin": "managed", "selection": "configured", "executable": str(older),
                       "identity": readiness.discovery.executable_identity(older), "version": "1.12.6"}, root)
    monkeypatch.setenv(readiness.discovery.JULIA_ENV_VAR, str(older))
    saved.update(readiness.expected_identity("cpu", root, environ=None, julia_threads=3))
    state.write_state(saved, root)
    assert readiness.backend_readiness("cpu", root, environ=None, julia_threads=3).ready
    monkeypatch.delenv(readiness.discovery.JULIA_ENV_VAR)
    assert readiness.backend_readiness("cpu", root, julia_threads=3).state == "stale"


def test_lazy_public_worker_factory_publishes_completed_readiness(proved_cpu, monkeypatch):
    import sys
    from types import SimpleNamespace

    root, _, _, _, query, workers, setup = proved_cpu
    monkeypatch.setitem(sys.modules, "beat_engine", SimpleNamespace(EngineWorker=type(workers[0])))
    observed = []
    listener = lambda: observed.append(readiness.backend_readiness("cpu", root, **query).ready)
    readiness.add_readiness_listener(listener)
    try:
        record = readiness.provision_cpu(root, force=True, **setup)
        assert record["status"] == "ready" and observed == [True]
        assert len(workers) == 2 and workers[-1].terminated
    finally:
        readiness.remove_readiness_listener(listener)


def test_gpu_detection_error_does_not_hide_cpu(proved_cpu, monkeypatch):
    root, _, _, _, query, _, _ = proved_cpu

    def broken():
        raise OSError("inventory failed")

    monkeypatch.setattr(hardware, "gpu_hardware", broken)
    statuses = readiness.beat_backend_statuses(root, **query)
    assert statuses["cpu"]["available"]
    assert statuses["metal"]["state"] == "detection-failed"
    assert "inventory failed" in statuses["metal"]["reason"]
