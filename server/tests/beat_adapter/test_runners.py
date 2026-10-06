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
from scripts.beat_conformance.run_agreement import metre_mesh
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
                               "regular_quadrature_mode": "wavelength" if inputs.backend == "cpu" else "fixed",
                               "regular_quadrature_order": 2 if inputs.backend == "cpu" else 4, "dense_solve_method": "lu",
                               "engine_provenance": {"runtime": {"julia_threads": inputs.threads},
                                                     "execution": {"backend": inputs.backend,
                                                                   "device": "Test GPU" if inputs.backend == "metal" else "Test CPU"}}}}
                                       for _ in inputs.frequencies_hz])


@pytest.mark.parametrize("precision", ["float32", "float64"])
@pytest.mark.parametrize("symmetry", ["full", "yz", "yz+xz"])
def test_inputs_freeze_actual_coordinates_normals_and_options(inputs, precision, symmetry):
    inputs = replace(inputs, precision=precision, symmetry=symmetry)
    compiled = inputs.compiled()
    assert compiled.wire["frequencies_hz"] == [500., 300., 450.]
    assert compiled.wire["solver_options"]["precision"] == precision
    assert compiled.wire["solver_options"]["regular_quadrature_mode"] == "wavelength"
    settings = inputs.settings()
    assert settings["threads"] == 1 and settings["source_motion"] == "normal"
    assert np.allclose(np.linalg.norm(settings["normals"], axis=1), 1.)
    assert settings["observation_points"]["sphere"] == compiled.layout.points_m["sphere"].tolist()
    assert inputs.mesh_bytes == PROBE_MESH.encode()


@pytest.mark.parametrize("change", [{"threads": "auto"}, {"threads": True}, {"threads": 0}, {"symmetry": "xz"}])
def test_nonidentical_or_implicit_inputs_refuse_before_launch(inputs, change):
    with pytest.raises(ValueError):
        replace(inputs, **change).compiled()


def test_official_runner_is_only_a_managed_launch_selection(inputs):
    selection = runners.official_runner("fake-julia", threads=2, engine_source=Path("fake-source"))(inputs.compiled())
    assert selection == recorder.EngineRun("fake-julia", 2, runtime_mode="child", engine_source=Path("fake-source"))


@pytest.fixture
def fake_hbb(inputs, monkeypatch, tmp_path):
    state = {"revision": "a" * 40, "editable": False, "calls": []}
    class Distribution:
        def read_text(self, name):
            return json.dumps({"vcs_info": {"commit_id": state["revision"]}, "dir_info": {"editable": state["editable"]}})
        def locate_file(self, name):
            return tmp_path / "installed" / name
    def solve(path, frequencies, config):
        state["calls"].append((Path(path).read_bytes(), frequencies, config))
        assert config.persistent_worker is False
        return native(replace(inputs, precision="float64" if config.solve_precision == "double" else "float32",
                              symmetry=config.native_symmetry_plane or "full", backend=config.beat_backend,
                              threads=config.julia_threads))
    package = SimpleNamespace(__file__=tmp_path / "installed/hornlab_beat_bem/__init__.py",
                              ObservationConfig=lambda **kw: SimpleNamespace(**kw),
                              SolveConfig=lambda **kw: SimpleNamespace(**kw), solve_frequencies=solve)
    monkeypatch.setitem(sys.modules, "hornlab_beat_bem", package)
    monkeypatch.setattr(runners.metadata, "distribution", lambda name: Distribution())
    return state


def test_hbb_receives_same_bytes_order_precision_quadrature_and_threads(inputs, fake_hbb, tmp_path):
    result = runners.hbb_runner(inputs, julia_executable="fake-julia", expected_revision="a" * 40, directory=tmp_path / "output")
    mesh, frequencies, config = fake_hbb["calls"][0]
    assert mesh == result.mesh_bytes == inputs.mesh_bytes
    assert frequencies == inputs.frequencies_hz
    assert config.solve_precision == "double" and config.julia_threads == 1
    assert config.air_density == inputs.density and config.sound_speed == inputs.sound_speed
    assert config.regular_quadrature_mode == "wavelength" and config.quadrature_order == 4 and config.singular_order == 4
    assert config.observation.sphere_grid == (3, 4)
    assert not list(tmp_path.glob("hbb-reference-*"))


@pytest.mark.parametrize("field,value", [("revision", "b" * 40), ("editable", True)])
def test_wrong_or_editable_hbb_pin_never_solves(inputs, fake_hbb, tmp_path, field, value):
    fake_hbb[field] = value
    with pytest.raises(ValueError, match="current WG pin"):
        runners.hbb_runner(inputs, julia_executable="fake", expected_revision="a" * 40, directory=tmp_path / "output")
    assert not fake_hbb["calls"]


@pytest.mark.parametrize("field,value", [("cancelled", True), ("frequencies_hz", np.array([300., 450., 500.])),
                                        ("sphere_pressure_complex", np.ones((3, 1)))])
def test_result_conversion_rejects_partial_or_mismatched_evidence(inputs, field, value):
    result = native(inputs)
    setattr(result, field, value)
    with pytest.raises(ValueError):
        runners.result_set(inputs, result, revision="a" * 40)


def test_uniform_sphere_power_and_di_have_physical_normalization(inputs):
    result = runners.result_set(inputs, native(inputs), revision="a" * 40)
    assert np.allclose(result.di_db, 0.)
    assert np.allclose(result.power_w, 4 * np.pi * inputs.distance_m**2 / (2 * inputs.density * inputs.sound_speed))


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


def test_shared_metre_artifact_preserves_connectivity_tags_and_ids(inputs):
    scaled = metre_mesh(inputs.mesh_bytes, .001)
    from server.solver.beat_adapter.mesh import read_surface
    before, after = read_surface(inputs.mesh_bytes.decode()), read_surface(scaled.decode())
    assert after.node_ids == before.node_ids and after.element_ids == before.element_ids
    assert np.array_equal(after.faces, before.faces) and np.array_equal(after.tags, before.tags)
    assert np.array_equal(after.points_m, before.points_m * .001)


@pytest.mark.parametrize("precision,symmetry", [("float32", "yz"), ("float64", "yz+xz")])
def test_hbb_reduced_precision_selection_is_explicit(inputs, fake_hbb, tmp_path, precision, symmetry):
    selected = replace(inputs, precision=precision, symmetry=symmetry)
    runners.hbb_runner(selected, julia_executable="fake", expected_revision="a" * 40, directory=tmp_path / "output")
    config = fake_hbb["calls"][0][2]
    assert config.solve_precision == {"float32": "single", "float64": "double"}[precision]
    assert config.native_symmetry_plane == symmetry
    record = json.loads((tmp_path / "output/hbb-run.json").read_text())
    assert record["engine_status"] == "passed" and record["real_solved_count"] == 3
    assert "API-validated rows" in record["count_source"] and "not terminal events" in record["count_source"]


def test_hbb_transport_failure_retains_failed_real_record(inputs, fake_hbb, monkeypatch, tmp_path):
    def fail(*args):
        raise RuntimeError("HBB fake failed before results")
    monkeypatch.setattr(sys.modules["hornlab_beat_bem"], "solve_frequencies", fail)
    with pytest.raises(RuntimeError, match="fake failed"):
        runners.hbb_runner(inputs, julia_executable="fake", expected_revision="a" * 40, directory=tmp_path / "output")
    record = json.loads((tmp_path / "output/hbb-run.json").read_text())
    assert record["engine_status"] == "failed" and record["real_solved_count"] == 0
    assert not list(tmp_path.glob("hbb-reference-*"))


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


def test_runner_refuses_hbb_directory_before_import_or_write(inputs, monkeypatch, tmp_path):
    directory = tmp_path / "hbb-runtime"
    monkeypatch.setenv("HORNLAB_BEAT_RUNTIME_DIR", str(directory))
    with pytest.raises(ValueError, match="overlaps"):
        runners.hbb_runner(inputs, julia_executable="fake", expected_revision="a" * 40, directory=directory)
    assert not directory.exists()


@pytest.mark.parametrize("change", [{"angle_range": (10., 90., 9)}, {"sphere_grid": None}])
def test_hbb_unrepresentable_observation_scope_refuses_before_any_engine(inputs, change):
    with pytest.raises(ValueError, match="on-axis cuts"):
        replace(inputs, **change).compiled()


def test_pressure_acceptance_has_no_failure_text_on_passing_evidence(inputs):
    from scripts.beat_conformance.run_agreement import accept_pressure
    result = native(inputs)
    assert accept_pressure(result) == {"passed": True, "failures": []}
    result.pressure_complex[:] = 0
    assert accept_pressure(result)["passed"] is False


@pytest.mark.parametrize("backend,threads", [("cpu", 1), ("metal", 6)])
def test_engine_record_status_after_failed_agreement_and_cli_never_uses_comparator(inputs, monkeypatch, tmp_path, backend, threads):
    from dataclasses import asdict
    from scripts.beat_conformance import run_agreement
    from server.solver.beat_adapter.results import SweepResult
    precision = "float64" if backend == "cpu" else "float32"
    name = "full-float64" if backend == "cpu" else "metal-full-float32"
    monkeypatch.setattr("server.solver.beat_runtime.threads._performance_core_count", lambda: 8)
    path = tmp_path / "input.msh"
    path.write_bytes(inputs.mesh_bytes)
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["run_agreement", "--mesh", str(path), "--frequencies", "500,300,450",
                                    "--frequency-step", "200", "--prominence-db", "1", "--precision", precision, "--backend", backend,
                                    "--julia", "fake", "--output-dir", str(output)])
    state = {"hbb": False}
    mapped = native(replace(inputs, sphere_grid=(19, 24), backend=backend, precision=precision, threads=threads))
    mapped.sphere_pressure_complex = np.ones((3, 19 * 24), dtype=complex)
    sweep = SweepResult(mapped.frequencies_hz, mapped.pressure_complex, np.zeros((3, 2, 19)), mapped.impedance,
                        mapped.observation_angles_deg, mapped.observation_planes, mapped.sphere_pressure_complex,
                        None, None, None, None, False, 3, mapped.solver_log)
    def record_case(case, **kwargs):
        assert "comparator" not in kwargs and not state["hbb"]
        # The engine snapshot never shares the final record's file name.
        assert case.name == name + "-engine"
        assert case.backend == backend
        assert kwargs["solve"](case.make_request()).julia_threads == threads
        return {"status": "passed", "qualified": True,
                "case": {"backend": backend, "precision": precision},
                "mesh_sha256": "fake-packed-mesh", "evidence_mode": "real",
                "runtime": {"engine_revision": "b" * 40}, "result": asdict(sweep), "comparison": {"ran": False}}
    def reference(selected, **kwargs):
        state["hbb"] = True
        # While HBB runs, no top-level record for the case exists yet.
        assert not (output / name / f"{name}.json").exists()
        return runners.result_set(selected, mapped, revision="a" * 40)
    def compare(*args, **kwargs):
        assert state["hbb"]
        assert args[1].recorder_record["engine_status"] == "passed"
        assert args[1].recorder_sha256 == recorder.record_sha256(args[1].recorder_record)
        return {"passed": False, "resonances": {}, "failures": ["declared test failure"], "limitations": []}
    monkeypatch.setattr(run_agreement, "run_case", record_case)
    monkeypatch.setattr(run_agreement, "hbb_runner", reference)
    monkeypatch.setattr(run_agreement, "compare_results", compare)
    assert run_agreement.main() == 1
    record = json.loads((output / name / f"{name}.json").read_text())
    agreement = json.loads((output / name / "agreement.json").read_text())
    assert record["status"] == "failed" and record["qualified"] is False
    assert record["engine_status"] == "passed" and record["engine_qualified"] is True
    assert record["comparison"] == {"ran": True, "passed": False, "record": "agreement.json",
                                    "record_sha256": agreement["record_sha256"]}


def test_conformance_cli_callback_is_lazy_and_requires_explicit_job_selection(inputs, monkeypatch, tmp_path):
    monkeypatch.delenv("WG2_BEAT_JULIA", raising=False)
    with pytest.raises(ValueError, match="WG2_BEAT_JULIA"):
        runners.solve(inputs.compiled())
    monkeypatch.setenv("WG2_BEAT_JULIA", "fake-julia")
    monkeypatch.setenv("JULIA_NUM_THREADS", "2")
    monkeypatch.setenv("WG_BEAT_ENGINE_SRC", str(tmp_path))
    assert runners.solve(inputs.compiled()) == recorder.EngineRun("fake-julia", 2, runtime_mode="child", engine_source=tmp_path)


@pytest.mark.parametrize("axis", [(500., 500.000001, 500.1), (500., 502., 504.)])
def test_hbb_frequency_quantization_refuses_before_either_launch(inputs, fake_hbb, tmp_path, monkeypatch, axis):
    selected = replace(inputs, frequencies_hz=axis)
    if axis[1] == 502.:
        assert selected.compiled().wire["frequencies_hz"] == list(axis)
        return
    with pytest.raises(ValueError, match="representable in Float32"):
        selected.compiled()
    with pytest.raises(ValueError, match="representable in Float32"):
        runners.hbb_runner(selected, julia_executable="fake", expected_revision="a" * 40,
                           directory=tmp_path / "output")
    assert not fake_hbb["calls"] and not (tmp_path / "output").exists()
    from scripts.beat_conformance import run_agreement
    monkeypatch.setattr(sys, "argv", ["agreement", "--mesh", "unused", "--frequencies", "500,500.000001,500.1",
                                    "--frequency-step", "1", "--prominence-db", "1", "--precision", "float64",
                                    "--julia", "fake", "--output-dir", str(tmp_path / "output")])
    monkeypatch.setattr(run_agreement, "run_case", lambda *a, **kw: pytest.fail("Engine launched"))
    with pytest.raises(ValueError, match="representable in Float32"):
        run_agreement.main()


@pytest.mark.parametrize("field,value", [("backend", "metal"), ("regular_quadrature_order", 4),
                                        ("phasor_convention", "exp(+i omega t)")])
def test_vacuous_settings_equality_uses_native_diagnostics(inputs, fake_hbb, tmp_path, monkeypatch, field, value):
    result = native(inputs)
    for row in result.solver_log:
        row["native_diagnostics"].pop("bem_backend")
        row["native_diagnostics"][field] = value
    monkeypatch.setattr(sys.modules["hornlab_beat_bem"], "solve_frequencies", lambda *args: result)
    if field == "regular_quadrature_order":
        observed = runners.hbb_runner(inputs, julia_executable="fake", expected_revision="a" * 40,
                                      directory=tmp_path / "output")
        assert observed.settings["quadrature.regular_quadrature_order"] == [4, 4, 4]
    else:
        with pytest.raises(ValueError, match="Observed"):
            runners.hbb_runner(inputs, julia_executable="fake", expected_revision="a" * 40,
                               directory=tmp_path / "output")


def test_vacuous_settings_equality_marks_missing_hbb_fields_declared(inputs, fake_hbb, tmp_path, monkeypatch):
    from dataclasses import make_dataclass
    result = native(inputs)
    for row in result.solver_log:
        for key in ("precision", "phasor_convention", "engine_provenance"):
            row["native_diagnostics"].pop(key)
    mesh = inputs.settings()["mesh"]
    MeshInfo = make_dataclass("MeshInfo", ["n_vertices", "n_triangles", "physical_tag_areas_m2"])
    result.mesh_info = MeshInfo(mesh["node_count"], mesh["triangle_count"], mesh["tag_areas_m2"])
    monkeypatch.setattr(sys.modules["hornlab_beat_bem"], "solve_frequencies", lambda *args: result)
    observed = runners.hbb_runner(inputs, julia_executable="fake", expected_revision="a" * 40,
                                  directory=tmp_path / "output")
    for name in ("precision", "time_convention", "threads", "normals", "observation_points.sphere"):
        assert observed.setting_evidence[name]["status"] == "declared"
    assert observed.setting_evidence["mesh.node_count"]["status"] == "observed"
    assert observed.setting_evidence["hbb_mesh_info_sha256"]["value"]
    assert observed.recorder_sha256 == recorder.record_sha256(observed.recorder_record)


@pytest.mark.parametrize("layout", ["hornlab_beat_bem/__init__.py", "src/beat_engine/__init__.py"])
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


def test_output_isolation_refuses_installed_package_and_dist_root(fake_hbb, tmp_path):
    for directory in (tmp_path / "installed", tmp_path / "installed/hornlab_beat_bem/evidence"):
        with pytest.raises(ValueError, match="source/package"):
            runners.output_directory(directory)


def test_thread_count_launch_selection_is_frozen_and_pin_path_ignores_cwd(inputs, monkeypatch, tmp_path):
    from scripts.beat_conformance.run_agreement import hbb_pin
    pin = hbb_pin()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pins.json").write_text('{}')
    assert hbb_pin() == pin
    selected = replace(inputs, threads=3)
    assert runners.official_runner("fake", threads=selected.threads)(selected.compiled()).julia_threads == 3


def test_official_observation_layout_is_declared_not_observed(inputs):
    from scripts.beat_conformance import settings
    mapped = native(inputs)
    _, evidence = settings.observed_settings(inputs.settings(), mapped, official=True, native_symmetry="off")
    official_values, _ = settings.observed_settings(inputs.settings(), mapped, official=True, native_symmetry="off")
    for name in ("observation_angles_deg", "observation_planes"):
        assert evidence[name]["status"] == "declared" and name in official_values
    _, hbb = settings.observed_settings(inputs.settings(), mapped, official=False, native_symmetry="off")
    assert hbb["observation_angles_deg"]["status"] == "observed"


def test_official_and_hbb_settings_share_keys_for_frozen_comparison(inputs):
    """A key present on one side only made the real rerun refuse to compare (job f140)."""
    from scripts.beat_conformance import settings
    mapped = native(inputs)
    official, _ = settings.observed_settings(inputs.settings(), mapped, official=True, native_symmetry="off")
    hbb, _ = settings.observed_settings(inputs.settings(), mapped, official=False, native_symmetry="off")
    assert {"observation_angles_deg", "observation_planes"} <= official.keys() & hbb.keys()


def test_metal_freezes_fixed_quadrature_and_public_hbb_backend(inputs, fake_hbb, tmp_path):
    selected = replace(inputs, backend="metal", precision="float32", threads=6)
    wire = selected.compiled().wire
    assert wire["solver_options"]["bem_backend"] == "metal"
    assert wire["solver_options"]["regular_quadrature_mode"] == "fixed"
    assert wire["solver_options"]["quadrature_order"] == 4
    result = runners.hbb_runner(selected, julia_executable="fake", expected_revision="a" * 40,
                                directory=tmp_path / "output")
    config = fake_hbb["calls"][0][2]
    assert config.beat_backend == "metal" and config.solve_precision == "single"
    assert config.regular_quadrature_mode == "fixed" and config.julia_threads == 6
    assert result.settings["backend"] == "metal"
    assert result.setting_evidence["backend"]["status"] == "observed"


def test_metal_float64_refuses_before_hbb_launch(inputs, fake_hbb, tmp_path):
    with pytest.raises(ValueError, match="Metal agreement requires float32"):
        runners.hbb_runner(replace(inputs, backend="metal"), julia_executable="fake",
                           expected_revision="a" * 40, directory=tmp_path / "output")
    assert not fake_hbb["calls"] and not (tmp_path / "output").exists()


@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("defect", ["cpu", "missing", "conflicting", "late_cpu"])
def test_metal_result_must_have_native_backend_on_every_row(inputs, official, defect):
    from scripts.beat_conformance.settings import observed_settings
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
        observed_settings(selected.settings(), mapped, official=official, native_symmetry="off")


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


def test_hbb_metal_cpu_fallback_leaves_failed_record(inputs, fake_hbb, monkeypatch, tmp_path):
    selected = replace(inputs, backend="metal", precision="float32")
    monkeypatch.setattr(sys.modules["hornlab_beat_bem"], "solve_frequencies", lambda *args: native(inputs))
    with pytest.raises(ValueError, match="native backend"):
        runners.hbb_runner(selected, julia_executable="fake", expected_revision="a" * 40,
                           directory=tmp_path / "output")
    record = json.loads((tmp_path / "output/hbb-run.json").read_text())
    assert record["engine_status"] == "failed" and record["case"]["backend"] == "metal"


def test_cli_metal_float64_refuses_before_reading_mesh(monkeypatch, tmp_path):
    from scripts.beat_conformance import run_agreement
    monkeypatch.setattr(sys, "argv", ["run_agreement", "--mesh", str(tmp_path / "absent.msh"),
                                    "--frequencies", "500,502,504", "--frequency-step", "2",
                                    "--prominence-db", "0.1", "--precision", "float64", "--backend", "metal",
                                    "--julia", "fake", "--output-dir", str(tmp_path / "output")])
    with pytest.raises(SystemExit) as error:
        run_agreement.main()
    assert error.value.code == 2 and not (tmp_path / "output").exists()


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
@pytest.mark.parametrize("missing", ["blas_threads", "backend"])
@pytest.mark.parametrize("row_index", [0, -1])
def test_missing_blas_or_backend_observation_fails_closed_on_each_row(inputs, backend, official, missing, row_index):
    from scripts.beat_conformance.settings import observed_settings
    selected = replace(inputs, backend=backend, precision="float32" if backend == "metal" else "float64")
    mapped = native(selected)
    diagnostic = mapped.solver_log[row_index]["native_diagnostics"]
    if missing == "backend":
        diagnostic.pop("backend")
        diagnostic.pop("bem_backend")
    else:
        diagnostic.pop(missing)
    with pytest.raises(ValueError, match="blas_threads|native backend"):
        observed_settings(selected.settings(), mapped, official=official, native_symmetry="off")


@pytest.mark.parametrize("execution", [None, "cpu_fused_burton_miller", "cpu_default"])
def test_hbb_metal_request_echo_cannot_hide_cpu_execution(inputs, fake_hbb, tmp_path, monkeypatch, execution):
    selected = replace(inputs, backend="metal", precision="float32")
    mapped = native(selected)
    diagnostic = mapped.solver_log[-1]["native_diagnostics"]
    diagnostic.pop("regular_assembly_mode")
    if execution is not None:
        diagnostic["regular_assembly_mode"] = execution
    diagnostic["metal_pipeline"] = False  # HBB emits this on CPU as well.
    monkeypatch.setattr(sys.modules["hornlab_beat_bem"], "solve_frequencies", lambda *a: mapped)
    with pytest.raises(ValueError, match="Metal execution evidence"):
        runners.hbb_runner(selected, julia_executable="fake", expected_revision="a" * 40,
                           directory=tmp_path / "output")
    record = json.loads((tmp_path / "output/hbb-run.json").read_text())
    assert record["engine_status"] == "failed"


@pytest.mark.parametrize("official", [False, True])
def test_cpu_native_backend_accepts_real_driver_diagnostic_spellings(inputs, official):
    from scripts.beat_conformance.settings import observed_settings, validate_native_backend
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
    _, evidence = observed_settings(inputs.settings(), mapped, official=official, native_symmetry="off")
    assert evidence["backend"]["status"] == evidence["blas_threads"]["status"] == "observed"
    assert evidence["precision"]["status"] == ("observed" if official else "declared")


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


def agreement_cli(monkeypatch, tmp_path, *, threads="1"):
    from scripts.beat_conformance import run_agreement
    monkeypatch.setattr(sys, "argv", ["run_agreement", "--mesh", str(tmp_path / "input.msh"),
                                    "--frequencies", "500,502,504", "--frequency-step", "2",
                                    "--prominence-db", "0.1", "--precision", "float32", "--backend", "metal",
                                    "--julia", "fake", "--threads", threads, "--output-dir", str(tmp_path / "output")])
    return run_agreement


@pytest.mark.parametrize("threads", ["0", "-1", "nonsense", "1.5", ""])
def test_invalid_threads_are_argparse_errors_before_io(monkeypatch, tmp_path, capsys, threads):
    run_agreement = agreement_cli(monkeypatch, tmp_path, threads=threads)
    with pytest.raises(SystemExit) as error:
        run_agreement.main()
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()


def test_nonempty_output_refuses_probe_fails_before_solve_retry(inputs, monkeypatch, tmp_path, capsys):
    run_agreement = agreement_cli(monkeypatch, tmp_path)
    (tmp_path / "input.msh").write_bytes(inputs.mesh_bytes)
    calls = []
    def fail_probe(*args, **kwargs):
        calls.append("probe")
        raise RuntimeError("fake device probe failed before solve")
    monkeypatch.setattr(recorder, "verify_runtime", fail_probe)
    monkeypatch.setattr(runners, "managed_solve", lambda *a, **kw: pytest.fail("Solve launched"))
    # An initial failure has a failed agreement and no final passed record.
    assert run_agreement.main() == 1
    directory = tmp_path / "output/metal-full-float32"
    assert not json.loads((directory / "agreement.json").read_text())["passed"]
    assert not (directory / "metal-full-float32.json").exists()
    # Simulate an earlier successful run in the reused directory; a retry must
    # refuse before the failing probe can coexist with that stale success.
    recorder.write_record(directory / "metal-full-float32.json", {"status": "passed", "qualified": True})
    recorder.write_record(directory / "agreement.json", {"passed": True})
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(SystemExit) as error:
        run_agreement.main()
    assert error.value.code == 2 and calls == ["probe"]
    assert "empty output directory" in capsys.readouterr().err
    assert all(p.read_bytes() == content for p, content in before.items())
