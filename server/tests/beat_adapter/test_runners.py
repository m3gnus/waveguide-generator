"""Broker runners use fake public streams and isolated mesh artifacts."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.beat_conformance import recorder, runners
from server.solver.beat_adapter.capabilities import PROBE_MESH
from server.solver.beat_runtime.manager import ManagedWorker


@pytest.fixture
def inputs():
    return runners.FrozenExterior(PROBE_MESH.encode(), (500., 300., 450.), sphere_grid=(3, 4))


def native(inputs):
    compiled = inputs.compiled()
    return SimpleNamespace(frequencies_hz=np.asarray(inputs.frequencies_hz),
                           pressure_complex=np.ones((3, 2, 19), dtype=complex),
                           impedance=np.ones(3, dtype=complex),
                           sphere_pressure_complex=np.ones((3, 12), dtype=complex),
                           observation_angles_deg=compiled.layout.angles_deg,
                           observation_planes=list(compiled.layout.planes), cancelled=False, is_partial=False,
                           solver_log=[{"native_diagnostics": {
                               "backend": inputs.backend, "bem_backend": inputs.backend, "precision": inputs.precision,
                               "phasor_convention": "exp(-i omega t)",
                               "symmetry": compiled.wire["solver_options"]["symmetry"],
                               "blas_threads": inputs.threads,
                               "regular_assembly_mode": inputs.backend + "_fused_burton_miller",
                               "regular_quadrature_mode": "fixed",
                               "regular_quadrature_order": 4, "dense_solve_method": "lu",
                               "engine_provenance": {"runtime": {"julia_threads": inputs.threads},
                                                     "execution": {"backend": inputs.backend,
                                                                   "device": "Test GPU" if inputs.backend == "metal" else "Test CPU"}}}}
                                       for _ in inputs.frequencies_hz])


@pytest.mark.parametrize("precision", ["float32", "float64"])
@pytest.mark.parametrize("symmetry", ["full", "yz", "yz+xz"])
def test_inputs_freeze_coordinates_and_options(inputs, precision, symmetry):
    inputs = replace(inputs, precision=precision, symmetry=symmetry)
    compiled = inputs.compiled()
    assert compiled.wire["frequencies_hz"] == [500., 300., 450.]
    assert compiled.wire["solver_options"]["precision"] == precision
    assert compiled.wire["solver_options"]["regular_quadrature_mode"] == "fixed"
    assert inputs.mesh_bytes == PROBE_MESH.encode()


@pytest.mark.parametrize("change", [{"threads": "auto"}, {"threads": True}, {"threads": 0}, {"symmetry": "xz"}])
def test_nonidentical_or_implicit_inputs_refuse_before_launch(inputs, change):
    with pytest.raises(ValueError):
        replace(inputs, **change).compiled()


def test_official_runner_is_only_a_managed_launch_selection(inputs):
    selection = runners.official_runner("fake-julia", threads=2, engine_source=Path("fake-source"))(inputs.compiled())
    assert selection == recorder.EngineRun("fake-julia", 2, runtime_mode="child", engine_source=Path("fake-source"))


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("traces", [False, True])
@pytest.mark.parametrize("backend", ["cpu", "metal"])
def test_managed_solve_stages_negotiates_maps_and_closes(inputs, raw_result, layout, monkeypatch, tmp_path, fail, traces, backend):
    inputs = replace(inputs, backend=backend, precision="float32" if backend == "metal" else inputs.precision,
                     threads=6 if backend == "metal" else inputs.threads)
    compiled = replace(inputs.compiled(), layout=layout, surface_traces=traces)
    compiled.wire["outputs"] = []  # Fake contract accepts a wire; real validation stays engine-owned.
    state = {"shutdown": 0, "closed": 0, "negotiated": False}
    if backend == "cpu":
        monkeypatch.setenv("BLAB_BEAT_CPU_BLAS_THREADS", "2")
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "2")
    monkeypatch.setenv("JULIA_PKG_OFFLINE", "true")
    class Stream:
        def __init__(self):
            rows = [raw_result(f, traces=True, precision=inputs.precision) for f in inputs.frequencies_hz]
            from .conftest import wire
            for row in rows:
                row["diagnostics"].update(bem_backend=backend, precision=inputs.precision)
                if traces:
                    row["quantities"][-1]["values"] = wire([[11 - 2j] * 4], inputs.precision)
                else:
                    row["quantities"] = [q for q in row["quantities"] if q["id"] != "surface:neumann"]
            self.iterator = iter([{"type": "result", "result": row} for row in rows]
                                 + [{"type": "completed", "solved_count": 3}])
        def __iter__(self):
            return self
        def __next__(self):
            if fail:
                raise RuntimeError("fake transport failure")
            return next(self.iterator)
        def close(self):
            state["closed"] += 1
    class Worker:
        worker_info = {"public": True}
        def ensure_started(self):
            state["started"] = True
        def submit(self, path, **kwargs):
            assert state["negotiated"] and state["started"]
            wire = json.loads(path.read_text())
            assert Path(wire["cancel_path"]).parent == path.parent
            state["staging"] = path.parent
            return Stream()
        def terminate(self):
            state["terminated"] = True
    client = ManagedWorker(Worker(), "child")
    class Manager:
        def __init__(self, mode):
            assert mode == "child"
        def get_worker(self, backend, **options):
            assert options["julia_threads"] == inputs.threads
            if backend == "metal":
                assert options["environment"]["OPENBLAS_NUM_THREADS"] == "7"
                assert not any(k.startswith("BLAB_") for k in options["environment"])
                assert options["environment"]["JULIA_PKG_OFFLINE"] == "true"
            else:
                assert "environment" not in options
            return client
        def shutdown(self):
            state["shutdown"] += 1
            client.close(detach=False)
    def negotiate(info, wire, label):
        assert info == {"public": True} and label == "solve"
        state["negotiated"] = True
    monkeypatch.setitem(sys.modules, "beat_engine.beat_contract.worker", SimpleNamespace(
        validate_solve_request=lambda wire: None, negotiate_submission=negotiate))
    monkeypatch.setattr(runners, "WorkerManager", Manager)
    monkeypatch.setattr("server.solver.beat_runtime.session.temporary_directory_root", lambda: str(tmp_path))
    events = []
    facts = {"julia_executable": "fake", "project": str(tmp_path), "solver_script": str(tmp_path / "solver.jl")}
    if fail:
        with pytest.raises(RuntimeError, match="transport"):
            runners.managed_solve(compiled, recorder.EngineRun("fake", inputs.threads), facts, events)
    else:
        result = runners.managed_solve(compiled, recorder.EngineRun("fake", inputs.threads), facts, events)
        assert result.frequencies_hz.tolist() == list(inputs.frequencies_hz)
        assert (result.surface_neumann_complex is not None) == traces
        if traces:
            assert result.surface_neumann_complex.shape == (3, 4)
        assert events == [{"type": "completed", "solved_count": 3}]
    assert state["shutdown"] == 1 and state["closed"] and state["terminated"]
    assert not state["staging"].exists()
    if backend == "cpu":
        assert runners.os.environ["BLAB_BEAT_CPU_BLAS_THREADS"] == "2"
    assert runners.os.environ["OPENBLAS_NUM_THREADS"] == "2"


def test_recorder_managed_path_observes_terminals_without_direct_worker(inputs, monkeypatch):
    facts = {"backend": "cpu", "device_class": "cpu", "device_name": "fake CPU",
             "julia_executable": "fake", "julia_version": "julia version fake",
             "engine_path": "fake", "engine_revision": "a" * 40, "engine_fingerprint": "fake",
             "artifact_kind": "installed", "device_kernel_verified": False}
    monkeypatch.setattr(recorder, "verify_runtime", lambda *args, **kwargs: dict(facts))
    def direct(**kwargs):
        raise AssertionError("Managed selection used raw EngineWorker")
    monkeypatch.setattr(recorder, "_engine_worker", direct)
    def managed(compiled, selection, observed, terminal):
        assert compiled.wire == inputs.compiled().wire and selection.runtime_mode == "child"
        terminal.append({"type": "completed", "solved_count": 3})
        return native(inputs)
    monkeypatch.setattr(runners, "managed_solve", managed)
    record = {}
    evidence = recorder._observed_solve(inputs.compiled(), runners.official_runner("fake", threads=inputs.threads)(inputs.compiled()), record)
    assert record["observations"]["solve_count"] == {"status": "observed", "value": 3}
    assert record["runtime_mode"] == "child"
    assert evidence.runtime.engine_revision == "a" * 40


def test_conformance_cli_callback_is_lazy_and_requires_explicit_job_selection(inputs, monkeypatch, tmp_path):
    monkeypatch.delenv("WG2_BEAT_JULIA", raising=False)
    with pytest.raises(ValueError, match="WG2_BEAT_JULIA"):
        runners.solve(inputs.compiled())
    monkeypatch.setenv("WG2_BEAT_JULIA", "fake-julia")
    monkeypatch.setenv("JULIA_NUM_THREADS", "2")
    monkeypatch.setenv("WG_BEAT_ENGINE_SRC", str(tmp_path))
    assert runners.solve(inputs.compiled()) == recorder.EngineRun("fake-julia", 2, runtime_mode="child", engine_source=tmp_path)


@pytest.mark.parametrize("layout", ["beat_engine/__init__.py", "src/beat_engine/__init__.py"])
def test_output_isolation_refuses_source_checkout_and_symlink(tmp_path, layout):
    root = tmp_path / "checkout"
    source = root / layout
    source.parent.mkdir(parents=True)
    source.write_text("# fake engine")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    for directory in (root, root / "evidence", alias / "evidence"):
        with pytest.raises(ValueError, match="source/package"):
            runners.output_directory(directory)
    assert not (root / "evidence").exists()


@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("defect", ["cpu", "missing", "conflicting", "late_cpu"])
def test_metal_result_must_have_native_backend_on_every_row(inputs, official, defect):
    from scripts.beat_conformance.settings import validate_native_backend
    selected = replace(inputs, backend="metal", precision="float32")
    mapped = native(selected)
    diagnostic = mapped.solver_log[-1 if defect == "late_cpu" else 0]["native_diagnostics"]
    if defect == "missing":
        diagnostic.pop("bem_backend")
        diagnostic.pop("backend")
    elif defect == "conflicting":
        diagnostic["backend"] = "cpu"
    else:
        diagnostic.update(bem_backend="cpu", backend="cpu")
    with pytest.raises(ValueError, match="native backend"):
        validate_native_backend(mapped, selected.backend, official=official)


@pytest.mark.parametrize("defect", [None, "cpu", "missing", "wrong_device", "device_backend"])
def test_recorder_metal_managed_selection_matches_independent_device(inputs, monkeypatch, defect):
    selected = replace(inputs, backend="metal", precision="float32", threads=6)
    mapped = native(selected)
    diagnostic = mapped.solver_log[-1]["native_diagnostics"]
    if defect == "cpu":
        diagnostic.update(bem_backend="cpu", backend="cpu")
    elif defect == "missing":
        diagnostic["engine_provenance"]["execution"].pop("device")
    elif defect == "wrong_device":
        diagnostic["engine_provenance"]["execution"]["device"] = "CPU"
    elif defect == "device_backend":
        diagnostic["engine_provenance"]["execution"]["backend"] = "cpu"
    facts = {"device_class": "gpu", "device_name": "Test GPU", "device_kernel_verified": True,
             "julia_executable": "fake", "julia_version": "julia version fake", "engine_path": "fake",
             "engine_revision": "a" * 40, "engine_fingerprint": "fake", "artifact_kind": "installed",
             "project": "fake/julia_metal", "solver_script": "fake/coupled_solver.jl"}
    def verify(executable, backend, **kwargs):
        assert backend == "metal"
        return dict(facts)
    def managed(compiled, selection, observed, terminal):
        assert compiled.wire["solver_options"]["bem_backend"] == "metal"
        assert observed["project"].endswith("julia_metal") and selection.julia_threads == 6
        terminal.append({"type": "completed", "solved_count": 3})
        return mapped
    monkeypatch.setattr(recorder, "verify_runtime", verify)
    monkeypatch.setattr(runners, "managed_solve", managed)
    record = {}
    selection = runners.official_runner("fake", threads=6)(selected.compiled())
    if defect:
        with pytest.raises(ValueError, match="backend|device"):
            recorder._observed_solve(selected.compiled(), selection, record)
    else:
        evidence = recorder._observed_solve(selected.compiled(), selection, record)
        assert evidence.runtime.device_kernel_verified
        assert record["observations"]["native_device_name"]["value"] == "Test GPU"


@pytest.mark.parametrize("override", ["BLAB_METAL_REGULAR_KERNEL_MODE", "BLAB_METAL_SINGULAR_MODE",
                                      "BLAB_BEAT_CPU_BLAS_THREADS"])
def test_metal_assembly_policy_override_refuses_before_probe_or_worker(inputs, monkeypatch, override):
    selected = replace(inputs, backend="metal", precision="float32", threads=6)
    monkeypatch.setenv(override, "pair_owned" if override.endswith("KERNEL_MODE") else "1")
    monkeypatch.setattr(recorder, "verify_runtime", lambda *a, **kw: pytest.fail("Probe launched"))
    monkeypatch.setattr(runners, "import_module", lambda *a: pytest.fail("Worker imported"))
    selection = runners.official_runner("fake", threads=6)(selected.compiled())
    with pytest.raises(ValueError, match="BLAB_.*" + override):
        recorder._observed_solve(selected.compiled(), selection, {})
    with pytest.raises(ValueError, match="BLAB_"):
        runners.managed_solve(selected.compiled(), selection, {}, [])


def test_metal_operator_matrix_policy_refuses_blas_reservation(inputs, monkeypatch):
    selected = replace(inputs, backend="metal", precision="float32", threads=6)
    compiled = selected.compiled()
    compiled.wire["solver_options"]["burton_miller_assembly"] = "operator_matrices"
    monkeypatch.setattr(recorder, "verify_runtime", lambda *a, **kw: pytest.fail("Probe launched"))
    with pytest.raises(ValueError, match="direct_system"):
        recorder._observed_solve(compiled, recorder.EngineRun("fake", 6, runtime_mode="child"), {})


@pytest.mark.parametrize("backend", ["cpu", "metal"])
@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("row_index", [0, -1])
def test_missing_backend_observation_fails_closed_on_each_row(inputs, backend, official, row_index):
    from scripts.beat_conformance.settings import validate_native_backend
    selected = replace(inputs, backend=backend, precision="float32" if backend == "metal" else "float64")
    mapped = native(selected)
    diagnostic = mapped.solver_log[row_index]["native_diagnostics"]
    diagnostic.pop("backend")
    diagnostic.pop("bem_backend")
    with pytest.raises(ValueError, match="native backend"):
        validate_native_backend(mapped, selected.backend, official=official)


@pytest.mark.parametrize("official", [False, True])
def test_cpu_native_backend_accepts_real_driver_diagnostic_spellings(inputs, official):
    from scripts.beat_conformance.settings import validate_native_backend
    mapped = native(inputs)
    # Frozen spellings from coupled_solver.jl's exterior diagnostics/provenance
    # and HBB BeatEngineDriver.jl; HBB exposes no precision/provenance here.
    diagnostic = ({"bem_backend": "cpu", "precision": "float64", "blas_threads": 1,
                   "linear_solver": "cpu_dense_lu", "burton_miller_assembly": "operator_matrices",
                   "engine_provenance": {"execution": {"backend": "cpu", "device": "apple_m1"}}}
                  if official else
                  {"backend": "cpu", "blas_threads": 1, "dense_solve_method": "lu",
                   "regular_assembly_mode": "cpu_fused_burton_miller", "metal_pipeline": False})
    mapped.solver_log = [{"native_diagnostics": dict(diagnostic)} for _ in inputs.frequencies_hz]
    validate_native_backend(mapped, "cpu", official=official)


@pytest.mark.parametrize("threads,launch_count", [(1, "1"), (6, "7")])
def test_worker_only_openblas_override_is_declared_in_record(inputs, monkeypatch, threads, launch_count):
    selected = replace(inputs, backend="metal", precision="float32", threads=threads)
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "2")
    # Fail the independent probe so even pre-solve failure records keep launch policy.
    def fail(*args, **kwargs):
        raise RuntimeError("fake device probe failed")
    monkeypatch.setattr(recorder, "verify_runtime", fail)
    record = {}
    with pytest.raises(RuntimeError, match="probe failed"):
        recorder._observed_solve(selected.compiled(), recorder.EngineRun("fake", threads, runtime_mode="child"), record)
    assert record["launch_settings"]["status"] == "declared"
    assert record["launch_settings"]["scope"] == "official child worker"
    assert record["launch_settings"]["environment"] == {"OPENBLAS_NUM_THREADS": launch_count}
    assert runners.os.environ["OPENBLAS_NUM_THREADS"] == "2"
