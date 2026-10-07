"""Production provider routing with managed fake engine events; no Julia."""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.contracts.conventions import SOLVER_TIME_CONVENTION
from server.solver import beat, beat_imported, official_beat as bridge
from server.solver.beat_runtime import manager, readiness, registry
from server.solver.context import SolverContext
from server.platform import temp_session
from server.tests import test_imported_beat as cad
from server.tests.beat_adapter.hbb_snapshot import assert_production_snapshot

MESH = Path(__file__).resolve().parents[1] / "solver" / "warmup_mesh.msh"


def _context(**overrides):
    values = dict(design=None, frequency_range=(500., 2000.), num_frequencies=3)
    values.update(overrides)
    return SolverContext(**values)


def _wire(values):
    data = np.asarray(values, dtype="<c8")
    return dict(encoding="base64", dtype="complex64", shape=list(data.shape),
                order="C", byte_order="little",
                content_base64=base64.b64encode(data.tobytes()).decode("ascii"))


def _result(request, frequency):
    mesh = request["compiled_system"]["meshes"][0]["mesh_data"]
    ports = len(request["excitation_port_ids"])
    nodes = mesh["points"]["shape"][0]
    faces = mesh["cells"][0]["connectivity"]["shape"][0]
    quantities = []
    for output in request["outputs"]:
        kind = output["quantity"]
        if kind == "exterior_pressure":
            count = len(output["options"]["points_m"])
            axis, unit = "observation", "Pa"
            # Smooth complex free-field phase keeps adaptive interpolation meaningful.
            values = np.full((ports, count), np.exp(-2j*np.pi*frequency*2/343) / (1+frequency/1000))
        elif kind == "radiation_impedance":
            axis, unit, values = "radiator", "N*s/m", np.full(ports, 9000+100j)
        elif kind == "bem_boundary_pressure":
            axis, unit, values = "bem_node", "Pa", np.tile(np.arange(nodes)+2+3j, (ports, 1))
        else:
            axis, unit, values = "bem_face", "Pa/m", np.tile(np.arange(faces)+5-2j, (ports, 1))
        quantities.append(dict(id=output["id"], quantity=kind, unit=unit, target_id=None,
                               axes=[axis] if axis == "radiator" else ["excitation", axis],
                               values=_wire(values)))
    options = request["solver_options"]
    return dict(schema_version=2, freq_hz=float(np.float32(frequency)),
                excitation_port_ids=request["excitation_port_ids"], quantities=quantities,
                diagnostics={name: options[name] for name in
                             ("precision", "symmetry", "bem_backend", "phasor_convention")})


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("WG2_BEAT_PROVIDER", "official")
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        readiness.BackendReadiness(True, "ready", "matching compiled proof"))
    monkeypatch.setattr(manager, "resolve_key", lambda backend, **options: registry.host_key({
        "backend": backend, "julia_executable": str(tmp_path / "julia"),
        "julia_identity": "fixture", "solver_script": str(tmp_path / "solver.jl"),
        "julia_project": str(tmp_path / "project"), "julia_sysimage": None,
        "julia_threads": 2, "engine_fingerprint": "fixture", "runtime_fingerprint": "fixture",
        "environment": {"JULIA_NUM_THREADS": "2"},
    }))
    state = SimpleNamespace(requests=[], paths=[], streams=[], workers=[], calls=[],
                            cancel_after=None, cancel_submission=None, mutate=None, result_count=0, fail=False,
                            block_after=None, blocking=False)

    class Worker:
        worker_info = {"type": "ready"}

        def __init__(self, *args, **kwargs):
            state.workers.append(self)

        def ensure_started(self, *, status_callback=None):
            state.calls.append("start")
            if status_callback:
                for message in ("Initializing BEAT Engine", "Julia: precompiling", "BEAT Engine ready"):
                    status_callback(message)

        def submit(self, path, **kwargs):
            state.calls.append("submit")
            if kwargs.get("status_callback"):
                kwargs["status_callback"]("Julia: compiling solve")
            state.paths.append(path)
            request = json.loads(path.read_text())
            state.requests.append(request)

            def events():
                solved = 0
                if state.fail:
                    yield {"type": "failed", "error": "assembly failed"}
                    return
                for frequency in request["frequencies_hz"]:
                    if solved == state.block_after:
                        import time
                        state.blocking = True
                        deadline = time.monotonic() + 1
                        while not Path(request["cancel_path"]).exists() and time.monotonic() < deadline:
                            time.sleep(.005)
                        raise OSError("blocked read interrupted")
                    if ((state.cancel_after is not None and solved == state.cancel_after
                         and (state.cancel_submission is None
                              or state.cancel_submission == len(state.requests)))
                            or Path(request["cancel_path"]).exists()):
                        yield {"type": "cancelled", "solved_count": solved}
                        return
                    raw = _result(request, frequency)
                    if state.mutate:
                        state.mutate(raw)
                    solved += 1
                    state.result_count += 1
                    yield {"type": "result", "result": raw}
                yield {"type": "completed", "solved_count": solved}

            class Stream:
                iterator = events()
                closed = False

                def __iter__(self):
                    return self

                def __next__(self):
                    return next(self.iterator)

                def close(self):
                    self.closed = True
                    self.iterator.close()

            stream = Stream()
            state.streams.append(stream)
            return stream

        def terminate(self):
            state.calls.append("terminate")

        def detach(self):
            state.calls.append("detach")

        shutdown = terminate

    def validate(request):
        assert request["schema_version"] == 1
        assert request["solver_options"]["phasor_convention"] == SOLVER_TIME_CONVENTION
        state.calls.append("validate")

    def negotiate(info, request, operation):
        assert info == Worker.worker_info and operation == "solve"
        assert Path(request["cancel_path"]).parent.is_relative_to(tmp_path)
        state.calls.append("negotiate")

    original_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "beat_engine.beat_contract.worker":
            return SimpleNamespace(validate_solve_request=validate, negotiate_submission=negotiate)
        if name.startswith("hornlab_beat_bem"):
            pytest.fail("official path imported HBB")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(beat, "_load_api", lambda: pytest.fail("official path used HBB"))
    monkeypatch.setattr(beat_imported, "_load_api", lambda: pytest.fail("official CAD path used HBB"))
    # Exercise host-mode admission/cache/session with an in-process fake transport.
    monkeypatch.setattr(manager, "HostedWorker", Worker)
    runtime = manager.WorkerManager(mode="host", directory=tmp_path / "workers")
    monkeypatch.setattr(bridge, "get_manager", lambda: runtime)
    state.manager = runtime
    yield state
    runtime.shutdown()
    assert all(stream.closed for stream in state.streams)
    assert all(not path.exists() for path in state.paths)


@pytest.mark.parametrize("backend", ["cpu", "metal", "cuda", "rocm"])
@pytest.mark.parametrize("motion", ["normal", "axial"])
@pytest.mark.parametrize("adaptive", [False, True])
def test_parametric_production(runtime, backend, motion, adaptive):
    frames, progress = [], []
    context = _context(source_motion=motion, num_frequencies=24 if adaptive else 3,
                       adaptive_frequency_sampling=adaptive)
    context.polar_config.update(enabled_axes=["vertical", "diagonal", "horizontal"],
                               spherical_sampling=True, spherical_theta_count=3,
                               spherical_phi_count=4, inclination=23.)
    response = beat.solve_beat_from_msh_text(
        MESH.read_text(), context, backend=backend,
        result_callback=lambda i, frame: frames.append((i, frame)), progress_callback=progress.append,
    )
    assert response["metadata"]["engine"] == "beat-engine"
    assert response["metadata"]["beat"]["precision"] == "single"
    assert response["frequencies"] == sorted(response["frequencies"])
    assert len(response["frequencies"]) == context.num_frequencies
    assert response["_field_traces"] is not None
    assert response["_field_trace_unavailable_reason"] is None
    assert frames and progress
    assert len(runtime.workers) == 1
    assert runtime.calls.index("negotiate") < runtime.calls.index("submit")
    wire = runtime.requests[0]
    assert wire["compiled_system"]["contract_version"] == (2 if motion == "axial" else 1)
    assert wire["solver_options"]["regular_quadrature_mode"] == "fixed"
    assert [output["id"] for output in wire["outputs"]][:3] == [
        "pressure:vertical", "pressure:diagonal", "pressure:horizontal"]
    assert adaptive or wire["frequencies_hz"][:2] == [500., 2000.]
    assert "terminate" not in runtime.calls


@pytest.mark.parametrize("available,selected", [(("cuda", "rocm", "metal", "cpu"), "cuda"),
                                                 (("rocm", "metal", "cpu"), "rocm"),
                                                 (("metal", "cpu"), "metal"), (("cpu",), "cpu")])
def test_auto_production_uses_hbb_backend_order(runtime, monkeypatch, available, selected):
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *a, **k:
                        readiness.BackendReadiness(backend in available, "ready", "mock proof"))
    response = beat.solve_beat_from_msh_text(MESH.read_text(), _context())
    assert response["metadata"]["beat_backend"] == selected
    assert runtime.requests[0]["solver_options"]["bem_backend"] == selected


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("axial", [False, True])
@pytest.mark.parametrize("backend", ["cpu", "cuda", "rocm"])
def test_imported_production(runtime, adaptive, axial, backend):
    msh = cad.MESH_Z if axial else cad.MESH
    request = cad._request(drive_channels=[
        {"id": "left", "source_ids": ["source-a", "source-b"], "motion": "axial" if axial else "normal"},
        {"id": "right", "source_ids": ["source-c"]},
    ])
    request.options.adaptive_frequency_sampling = adaptive
    if adaptive:
        request.options.frequency_range = [100., 1000.]
        request.options.frequencies_hz = None
        request.options.num_frequencies = 24
    frames = []
    result = beat_imported.solve_imported_beat_from_msh_text(
        msh, request, cad._record(msh_text=msh), backend=backend,
        result_callback=lambda i, frame: frames.append((i, frame)),
    )
    assert result["channel_order"] == ["left", "right"]
    assert set(result["channels"]) == {"left", "right"}
    assert "impedance" not in result["channels"]["left"]
    assert "impedance" in result["channels"]["right"]
    assert result["_field_traces"] is not None
    assert result["_channel_bases_npz"]
    assert frames and [i for i, _ in frames] == list(range(len(frames)))
    assert set(result["metadata"]["beat_solver_frame"]) == {"rotation_rows", "origin_m", "note"}
    assert len(runtime.workers) == 1
    # Independent ports retain original tags and unrotated node/face order.
    system = runtime.requests[0]["compiled_system"]
    assert system["metadata"]["source_tags"] == {"source-a": 101, "source-b": 102}
    points = system["meshes"][0]["mesh_data"]["points"]
    decoded = np.frombuffer(base64.b64decode(points["data"]), dtype="<f8").reshape(points["shape"])
    np.testing.assert_allclose(decoded[0], [.01, 0, 0] if not axial else [0, 0, 0])


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("adaptive", [False, True])
def test_cancelled_prefix_is_packaged(runtime, imported, adaptive):
    runtime.cancel_after = 1
    if imported:
        request = cad._request(drive_channels=[{"id": "right", "source_ids": ["source-c"]}],
                               mesh={"rigid_size_mm": 8., "transition_mm": 20.,
                                     "source_size_mm": {"source-c": 4.}})
        request.options.adaptive_frequency_sampling = adaptive
        if adaptive:
            request.options.frequencies_hz = None
            request.options.frequency_range = [100., 1000.]
            request.options.num_frequencies = 24
        result = beat_imported.solve_imported_beat_from_msh_text(
            cad.MESH, request, cad._record(), backend="cpu")
    else:
        context = _context(adaptive_frequency_sampling=adaptive, num_frequencies=24 if adaptive else 3)
        result = beat.solve_beat_from_msh_text(MESH.read_text(), context, backend="cpu")
    assert len(result["frequencies"]) == 1
    assert result["metadata"]["cancelled"] is True
    assert result["_field_traces"] is not None


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.update(freq_hz=1.),
    lambda raw: raw.update(schema_version=1),
    lambda raw: raw.update(excitation_port_ids=["wrong"]),
    lambda raw: raw["quantities"][0].update(unit="dB"),
    lambda raw: raw["quantities"][0]["values"].update(content_base64="bad"),
    lambda raw: raw["diagnostics"].update(phasor_convention="exp(+i omega t)"),
    lambda raw: raw["diagnostics"].update(bem_backend="metal"),
    lambda raw: raw["quantities"].reverse(),
])
def test_protocol_failures_close_and_remove_staging(runtime, mutation):
    runtime.mutate = mutation
    with pytest.raises(bridge.OfficialBeatProtocolError):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert all(stream.closed for stream in runtime.streams)
    assert all(not path.exists() for path in runtime.paths)


def test_worker_failure(runtime):
    runtime.fail = True
    with pytest.raises(bridge.OfficialBeatProtocolError, match="assembly failed"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")


def test_result_callback_failure_releases_worker(runtime):
    def fail(*args):
        raise LookupError("callback failed")
    with pytest.raises(LookupError, match="callback failed"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu", result_callback=fail)
    beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert len(runtime.workers) == 1


def test_stale_readiness_refuses_before_submission(runtime, monkeypatch):
    monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                        readiness.BackendReadiness(False, "stale", "compiled proof is stale"))
    with pytest.raises(beat.BeatUnavailable, match="compiled proof is stale"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert not runtime.requests


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("zip_creator_system", [0, 3])
def test_selector_off_calls_hbb_and_never_imports_engine(monkeypatch, tmp_path, imported, adaptive,
                                                       zip_creator_system):
    import zipfile

    original_zip_info = zipfile.ZipInfo.__init__

    def zip_info(self, *args, **kwargs):
        original_zip_info(self, *args, **kwargs)
        self.create_system = zip_creator_system

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", zip_info)
    monkeypatch.delenv("WG2_BEAT_PROVIDER", raising=False)
    monkeypatch.setattr(beat.time, "time", lambda: 1700000000.)
    monkeypatch.setattr(temp_session, "_active_root", str(tmp_path))
    original = importlib.import_module
    imports = []
    def watched(name, *args, **kwargs):
        imports.append(name)
        if name.startswith("beat_engine"):
            pytest.fail("default path imported official engine")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(importlib, "import_module", watched)
    package = cad._RecordingBeat()
    statuses = {"cpu": dict(available=True, backend="cpu", surface_traces=False, reason="HBB")}
    monkeypatch.setattr(beat, "_load_api", lambda: package)
    monkeypatch.setattr(beat_imported, "_load_api", lambda: package)
    monkeypatch.setattr(beat, "beat_backend_statuses", lambda: statuses)
    monkeypatch.setattr(beat_imported, "beat_backend_statuses", lambda: statuses)
    # Constants are part of the fake HBB package too.
    def hbb_import(name, *args, **kwargs):
        if name == "hornlab_beat_bem._constants":
            imports.append(name)
            return SimpleNamespace(SPEED_OF_SOUND=343.)
        return watched(name, *args, **kwargs)
    monkeypatch.setattr(importlib, "import_module", hbb_import)
    request = cad._request()
    request.options.adaptive_frequency_sampling = adaptive
    if adaptive:
        request.options.frequency_range = [100., 1000.]
        request.options.frequencies_hz = None
        request.options.num_frequencies = 24
    result = (beat_imported.solve_imported_beat_from_msh_text(
        cad.MESH, request, cad._record(), backend="cpu") if imported else
        beat.solve_beat_from_msh_text(
            MESH.read_text(), _context(adaptive_frequency_sampling=adaptive,
                                      num_frequencies=24 if adaptive else 3), backend="cpu"))
    assert package.solves
    metadata = result["metadata"]
    assert (metadata["solver_engine"]["package"] if imported else metadata["engine"]) == "hornlab-beat-bem"
    assert not any(name.startswith("beat_engine") for name in imports)
    expected = json.loads((Path(__file__).parent / "beat_adapter/fixtures/hbb_production.json").read_text())
    key = ("imported" if imported else "parametric") + ("_adaptive" if adaptive else "")
    assert_production_snapshot(result, expected[key], key, Path(__file__).parent / "beat_adapter/fixtures")



@pytest.mark.parametrize("traces", [False, True])
def test_imported_driver_uses_pressure_loading_and_scales_traces(runtime, traces):
    from server.solver.driver_lem import channel_drive_scaling
    from server.solver.combine import deserialize_channel_bases

    driver = dict(sd_cm2=210., bl_t_m=10.5, re_ohm=5.3, le_mh=.5,
                  mmd_g=12., cms_m_per_n=4e-4, rms_kg_per_s=1.2)
    request = cad._request(drive_channels=[
        {"id": "left", "source_ids": ["source-a", "source-b"]},
        {"id": "right", "source_ids": ["source-c"], "driver": driver},
    ])
    request.options.polar_config.field_plane = traces
    record = cad._record()
    # Driver loading uses the ingestion record's physical area for the piston.
    record["sources"][2]["observed"] = {"total_area_mm2": 21000.}
    result = beat_imported.solve_imported_beat_from_msh_text(cad.MESH, request, record, backend="cpu")
    channel = result["channels"]["right"]
    frequencies = np.asarray(result["frequencies"])
    # source-c's triangle nodes 1,3,4: arithmetic P1 mean, independent of force output.
    mean = ((2+3j)+(4+3j)+(5+3j))/3
    acceleration_pressure = mean / (-2j*np.pi*frequencies)
    scale, expected = channel_drive_scaling(
        frequencies, acceleration_pressure, .021, request.geometry.drive_channels[1].driver,
        drive_voltage_v=request.geometry.drive_voltage_v, rg_ohm=request.geometry.rg_ohm)
    np.testing.assert_allclose(channel["impedance"]["real"], expected["electrical_impedance_ohm"]["real"])
    np.testing.assert_allclose(channel["impedance"]["imaginary"], expected["electrical_impedance_ohm"]["imaginary"])
    assert channel["metadata"]["impedance_units"] == "ohms"
    bases = deserialize_channel_bases(result["_channel_bases_npz"])
    raw = np.exp(-2j*np.pi*frequencies*2/343)/(1+frequencies/1000)/(-2j*np.pi*frequencies)
    np.testing.assert_allclose(bases["results_by_id"]["right"].pressure_complex[:, 0, 0], raw*scale, rtol=1e-6)
    assert bool(result["_field_traces"] is not None) == traces
    if traces:
        artifact = result["_field_traces"]
        expected_pressure = (np.arange(4)+2+3j)[None, :] / (-2j*np.pi*frequencies[:, None]) * scale[:, None]
        np.testing.assert_allclose(artifact.channels[1].pressure_p1, expected_pressure, rtol=1e-6)
    assert all("surface:pressure" in [o["id"] for o in r["outputs"]] for r in runtime.requests)


@pytest.mark.parametrize("mode", ["ground", "baffle", "y-half"])
def test_production_refusals_precede_worker(runtime, mode):
    from server.solver.ground_plane import GroundPlane

    context = _context(ground_plane=GroundPlane("y", 1.) if mode == "ground" else None,
                       sim_type=1 if mode == "baffle" else 2,
                       quadrants=12 if mode == "y-half" else 1234)
    message = {"ground": "cannot apply a rigid ground plane",
               "baffle": "infinite-baffle", "y-half": "BEAT native symmetry"}[mode]
    with pytest.raises(beat.BeatUnavailable, match=message):
        beat.solve_beat_from_msh_text(MESH.read_text(), context, backend="cpu")
    assert not runtime.requests


def test_worker_warmup_and_production_share_the_manager(runtime):
    from server.solver.beat_runtime import warmup

    warmup.warm_up(beat_backend="cpu", mode="worker", worker_manager=runtime.manager)
    client = runtime.manager.get_worker("cpu")
    beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert runtime.manager.get_worker("cpu") is client
    assert len(runtime.workers) == 1


@pytest.mark.parametrize("imported", [False, True])
def test_callback_cancellation_reaches_caller_after_emitted_rows(runtime, imported):
    cancelled = []
    def cancel():
        if cancelled:
            raise RuntimeError("requested cancellation")
    def publish(*args):
        cancelled.append(True)
    with pytest.raises(RuntimeError, match="requested cancellation"):
        if imported:
            request = cad._request()
            beat_imported.solve_imported_beat_from_msh_text(
                cad.MESH, request, cad._record(), backend="cpu",
                cancellation_callback=cancel, result_callback=publish)
        else:
            beat.solve_beat_from_msh_text(
                MESH.read_text(), _context(), backend="cpu", cancellation_callback=cancel,
                result_callback=publish)
    assert cancelled


def test_registry_official_readiness_without_hbb(runtime, monkeypatch):
    import asyncio
    from server.engines.registry import EngineRegistry, detect_engines

    async def scenario():
        engines = EngineRegistry(cpu_refresh=True, detector=lambda: detect_engines(names=("beat-cpu", "beat-metal", "beat-cuda", "beat-rocm")))
        try:
            rows = await engines.capabilities()
            assert all(row.available and row.field_traces for row in rows)
            monkeypatch.setattr(readiness, "backend_readiness", lambda backend, *args, **kwargs:
                                readiness.BackendReadiness(False, "stale", "new identity"))
            readiness.probe_cache_clear()
            await engines._refresh_cpu_backend()
            assert all(not row.available for row in await engines.capabilities())
        finally:
            await engines.shutdown_prewarm()
    asyncio.run(scenario())


def test_cancellation_during_second_channel_exports_shared_artifact_axis(runtime):
    from server.solver.combine import deserialize_channel_bases

    runtime.cancel_after, runtime.cancel_submission = 1, 2
    result = beat_imported.solve_imported_beat_from_msh_text(
        cad.MESH, cad._request(), cad._record(), backend="cpu")
    assert len(result["channels"]["left"]["frequencies"]) == 3
    assert result["channels"]["right"]["frequencies"] == [100.]
    artifact = result["_field_traces"]
    np.testing.assert_equal(artifact.frequencies_hz, [100.])
    assert all(member.pressure_p1.shape[0] == 1 for member in artifact.channels)
    bases = deserialize_channel_bases(result["_channel_bases_npz"])
    assert all(member.pressure_complex.shape[0] == 1 for member in bases["results_by_id"].values())


def test_empty_cancellation_and_startup_callback_keep_errors(runtime):
    runtime.cancel_after = 0
    with pytest.raises(beat.BeatUnavailable, match="before any results"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    original = RuntimeError("cancelled before startup")
    def cancel():
        raise original
    with pytest.raises(RuntimeError) as error:
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu", cancellation_callback=cancel)
    assert error.value is original


def test_fresh_official_import_has_no_hbb_dependency(tmp_path):
    import os
    import subprocess
    import sys

    code = """
import importlib.abc, sys
class RefuseHBB(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('hornlab_beat_bem'):
            raise AssertionError('official import reached HBB: ' + fullname)
sys.meta_path.insert(0, RefuseHBB())
from server.solver import beat, beat_imported, official_beat
assert not any(name.startswith('hornlab_beat_bem') for name in sys.modules)
assert not any(name.startswith('beat_engine') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15,
                   env=dict(os.environ, WG2_BEAT_PROVIDER="official",
                            WG2_BEAT_RUNTIME_DIR=str(tmp_path / "runtime")))
    assert not (tmp_path / "runtime").exists()


def test_official_imported_combined_channel(runtime):
    request = cad._request(combine={"members": ["left", "right"], "crossovers_hz": [500.]})
    result = beat_imported.solve_imported_beat_from_msh_text(
        cad.MESH, request, cad._record(), backend="cpu")
    name = request.geometry.combine.id
    assert result["channel_order"] == ["left", "right", name]
    assert result["channels"][name]["metadata"]["engine"] == "beat-engine"
    assert "impedance" not in result["channels"][name]
    assert result["channels"][name]["frequencies"] == result["frequencies"]


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("rows", [0, 1])
@pytest.mark.parametrize("error_kind", ["cancel", "sqlite", "interrupt"])
def test_callback_errors_reach_production_caller(runtime, imported, adaptive, rows, error_kind):
    import sqlite3
    from server.jobs.runtime import _CancelledAtCheckpoint

    original = {"cancel": _CancelledAtCheckpoint("job cancelled"),
                "sqlite": sqlite3.OperationalError("database busy"),
                "interrupt": KeyboardInterrupt("interrupted")}[error_kind]

    def cancel():
        if runtime.result_count >= rows:
            raise original

    with pytest.raises(type(original)) as caught:
        if imported:
            request = cad._request()
            request.options.adaptive_frequency_sampling = adaptive
            if adaptive:
                request.options.frequencies_hz = None
                request.options.frequency_range = [100., 1000.]
                request.options.num_frequencies = 24
            beat_imported.solve_imported_beat_from_msh_text(
                cad.MESH, request, cad._record(), backend="cpu", cancellation_callback=cancel)
        else:
            context = _context(adaptive_frequency_sampling=adaptive,
                               num_frequencies=24 if adaptive else 3)
            beat.solve_beat_from_msh_text(MESH.read_text(), context, backend="cpu",
                                         cancellation_callback=cancel)
    assert caught.value is original
    assert runtime.result_count == rows


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("rows", [0, 1])
@pytest.mark.parametrize("error_kind", ["cancel", "sqlite", "interrupt"])
def test_blocked_production_read_preserves_monitor_error(runtime, imported, rows, error_kind):
    import sqlite3
    from server.jobs.runtime import _CancelledAtCheckpoint

    runtime.block_after = rows
    original = {"cancel": _CancelledAtCheckpoint("job cancelled"),
                "sqlite": sqlite3.OperationalError("database busy"),
                "interrupt": KeyboardInterrupt("interrupted")}[error_kind]

    def cancel():
        if runtime.blocking:
            raise original

    with pytest.raises(type(original)) as caught:
        if imported:
            beat_imported.solve_imported_beat_from_msh_text(
                cad.MESH, cad._request(), cad._record(), backend="cpu", cancellation_callback=cancel)
        else:
            beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu",
                                         cancellation_callback=cancel)
    assert caught.value is original
    assert runtime.result_count == rows


@pytest.mark.parametrize("imported", [False, True])
def test_startup_compile_status_reaches_stages(runtime, imported):
    stages = []
    if imported:
        beat_imported.solve_imported_beat_from_msh_text(
            cad.MESH, cad._request(), cad._record(), backend="cpu",
            stage_callback=lambda *args: stages.append(args))
    else:
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu",
                                     stage_callback=lambda *args: stages.append(args))
    for message in ("Initializing BEAT Engine", "Julia: precompiling", "BEAT Engine ready",
                    "Julia: compiling solve"):
        assert any(stage == "setup" and text == message for stage, _, text in stages)


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("adaptive", [False, True])
def test_compiled_topology_built_once_per_channel(runtime, monkeypatch, imported, adaptive):
    from server.solver.beat_adapter import request as adapter

    name = "build_imported_request" if imported else "build_parametric_request"
    original = getattr(adapter, name)
    builds = []

    def build(*args, **kwargs):
        result = original(*args, **kwargs)
        builds.append(result)
        return result

    monkeypatch.setattr(adapter, name, build)
    if imported:
        request = cad._request()
        request.options.adaptive_frequency_sampling = adaptive
        if adaptive:
            request.options.frequencies_hz = None
            request.options.frequency_range = [100., 1000.]
            request.options.num_frequencies = 24
        beat_imported.solve_imported_beat_from_msh_text(cad.MESH, request, cad._record(), backend="cpu")
    else:
        beat.solve_beat_from_msh_text(
            MESH.read_text(), _context(adaptive_frequency_sampling=adaptive,
                                      num_frequencies=24 if adaptive else 3), backend="cpu")
    assert len(builds) == (2 if imported else 1)
    assert all(request["compiled_system"] in [built.wire["compiled_system"] for built in builds]
               for request in runtime.requests)


def test_missing_source_frame_refuses_before_worker(runtime):
    text = MESH.read_text().replace("2 2 2 2 ", "2 2 9 9 ")
    with pytest.raises(beat.BeatUnavailable, match="authoritative source-tag-2 frame"):
        beat.solve_beat_from_msh_text(text, _context(), backend="cpu")
    assert not runtime.requests


@pytest.mark.parametrize("error_type", ["assets", "julia"])
def test_runtime_discovery_errors_map_to_unavailable(runtime, monkeypatch, error_type):
    from server.solver.beat_adapter.request import build_parametric_request
    from server.solver.beat_runtime.assets import AssetsUnavailable
    from server.solver.beat_runtime.discovery import JuliaDiscoveryError

    request = build_parametric_request(MESH.read_text(), _context())
    error = (AssetsUnavailable if error_type == "assets" else JuliaDiscoveryError)("runtime missing")

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(runtime.manager, "get_worker", unavailable)
    with pytest.raises(bridge.OfficialBeatUnavailable, match="runtime missing"):
        bridge.solve_compiled(request, channel_id="source")
    assert not runtime.requests


def test_explicit_scaled_mesh_and_precision_reach_response_artifacts(runtime, monkeypatch):
    from server.solver.beat_adapter.mesh import read_surface, scale_msh_text

    context = _context()
    text = scale_msh_text(MESH.read_text(), 1000.)
    # Fake outputs must use the negotiated precision too.
    real_wire = _wire

    def double_wire(values):
        data = np.asarray(values, dtype="<c16")
        return dict(encoding="base64", dtype="complex128", shape=list(data.shape),
                    order="C", byte_order="little",
                    content_base64=base64.b64encode(data.tobytes()).decode("ascii"))

    monkeypatch.setitem(globals(), "_wire", double_wire)
    response = bridge.solve_official_beat_from_msh_text(
        text, context, mesh_scale_to_m=.001, precision="float64")
    monkeypatch.setitem(globals(), "_wire", real_wire)
    artifact = response["_field_traces"]
    np.testing.assert_allclose(read_surface(artifact.mesh_text).points_m,
                               read_surface(MESH.read_text()).points_m)
    assert response["metadata"]["beat"]["precision"] == "double"
    wire = runtime.requests[0]
    points = np.asarray(wire["outputs"][0]["options"]["points_m"])
    from server.solver.beat_adapter.request import build_parametric_request
    built = build_parametric_request(text, context, mesh_scale_to_m=.001, precision="float64")
    np.testing.assert_allclose(np.linalg.norm(points - built.frame["origin"], axis=1), 2.)


def test_cancelled_first_channel_does_not_advertise_missing_channels(runtime, monkeypatch):
    runtime.cancel_after, runtime.cancel_submission = 1, 1
    metadata_names = []
    serialize = beat_imported.serialize_channel_bases

    def record(results, *, metadata_by_id):
        metadata_names.extend(metadata_by_id)
        return serialize(results, metadata_by_id=metadata_by_id)

    monkeypatch.setattr(beat_imported, "serialize_channel_bases", record)
    request = cad._request(combine={"members": ["left", "right"], "crossovers_hz": [500.]})
    result = beat_imported.solve_imported_beat_from_msh_text(cad.MESH, request, cad._record(), backend="cpu")
    assert result["channel_order"] == list(result["channels"]) == ["left"]
    assert metadata_names == ["left"]


def test_selected_registry_reports_official_version(runtime, monkeypatch):
    import asyncio
    from server.engines.registry import EngineRegistry, detect_engines

    monkeypatch.setattr(bridge, "version", lambda distribution: "1.2.3" if distribution == "beat-engine" else None)

    async def scenario():
        engines = EngineRegistry(cpu_refresh=True, detector=lambda: detect_engines(names=("beat-cpu", "beat-metal", "beat-cuda", "beat-rocm")))
        try:
            assert all(row.version == "1.2.3" for row in await engines.capabilities())
            await engines._refresh_cpu_backend()
            assert all(row.version == "1.2.3" for row in await engines.capabilities())
        finally:
            await engines.shutdown_prewarm()
    asyncio.run(scenario())


@pytest.mark.parametrize("imported", [False, True])
def test_request_builder_value_errors_map_to_production_refusals(runtime, monkeypatch, imported):
    from server.solver.beat_adapter import request as adapter

    def refuse(*args, **kwargs):
        raise ValueError("unrepresentable source frame")

    monkeypatch.setattr(adapter, "build_imported_request" if imported else "build_parametric_request", refuse)
    with pytest.raises(beat.BeatUnavailable, match="^unrepresentable source frame$"):
        if imported:
            beat_imported.solve_imported_beat_from_msh_text(cad.MESH, cad._request(), cad._record(), backend="cpu")
        else:
            beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert not runtime.requests


def test_selected_registry_keeps_version_while_provisioning(runtime, monkeypatch):
    from server.engines.registry import _official_runtime_statuses
    from server.solver import beat_cpu_runtime

    monkeypatch.setattr(bridge, "version", lambda distribution: "1.2.3")
    monkeypatch.setattr(beat_cpu_runtime, "cpu_preparation_in_flight", lambda: True)
    monkeypatch.setattr(beat_cpu_runtime, "cpu_runtime_readiness", lambda package: SimpleNamespace(ready=False, state="provisioning", reason="preparing"))
    monkeypatch.setattr(beat_cpu_runtime, "gpu_preparation_reason", lambda backend: "preparing")
    statuses = _official_runtime_statuses()
    assert all(not status["available"] and status["version"] == "1.2.3" for status in statuses.values())


@pytest.mark.parametrize("backend", ["cuda", "rocm"])
def test_fork_cad_gpu_requires_accurate(runtime, monkeypatch, backend):
    monkeypatch.setattr(beat, "_load_api", lambda: pytest.fail("fork CAD used HBB"))
    monkeypatch.setattr(beat_imported, "_load_api", lambda: pytest.fail("fork CAD used HBB"))
    request = cad._request()
    request.options.accuracy = "fast"
    engine = beat.BeatEngine(backend)
    with pytest.raises(beat.BeatUnavailable, match="only in Accurate"):
        asyncio.run(engine.run(request, cancel_cb=lambda: None, stage_cb=lambda *_: None,
                               imported_record=cad._record()))
    assert not runtime.requests
    request.options.accuracy = "accurate"
    result = asyncio.run(engine.run(request, cancel_cb=lambda: None, stage_cb=lambda *_: None,
                                   imported_record=cad._record()))
    assert runtime.requests
    assert {wire["solver_options"]["bem_backend"] for wire in runtime.requests} == {backend}
    assert result.results["metadata"]["solver_engine"]["device"] == backend


def test_profile_logs_complete_wg_timing_sequence(runtime, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("WG2_BEAT_PROFILE", "1")
    caplog.set_level(logging.INFO)
    beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    marks = [record.message.split(": ")[1].split(" elapsed_s=")[0]
             for record in caplog.records if record.message.startswith("BEAT profile wg:")]
    assert marks == ["solve start", "statuses done", "request built", "worker acquired", "submitted",
                     "first event", "first result", "last result", "completed", "mapped", "closed"]


def test_ui_refresh_and_reprobe_request_full_proof(runtime, monkeypatch):
    import asyncio
    from server.engines.registry import EngineRegistry, detect_engines
    from server.solver.beat_runtime import warm_cache

    observed = []
    original = bridge.production_statuses
    monkeypatch.setattr(bridge, "production_statuses", lambda **kwargs: (observed.append(kwargs), original(**kwargs))[1])

    async def scenario():
        engines = EngineRegistry(cpu_refresh=True, detector=lambda: detect_engines(names=("beat-cpu", "beat-metal")))
        try:
            await engines.capabilities()
            before = warm_cache.generation()
            await engines.refresh_official_readiness()
            assert warm_cache.generation() > before
            assert all(row.available for row in await engines.capabilities())
        finally:
            await engines.shutdown_prewarm()
    asyncio.run(scenario())
    beat.reprobe_package_backend_statuses()
    assert observed[-1] == {"force_refresh": True}


@pytest.mark.parametrize("backend", ["cpu", "metal"])
def test_explicit_solve_proves_only_selected_backend(runtime, monkeypatch, backend):
    observed = []
    original = readiness.backend_readiness
    monkeypatch.setattr(readiness, "backend_readiness", lambda name, *a, **kw:
                        (observed.append(name), original(name, *a, **kw))[1])
    beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend=backend)
    assert observed == [backend]
    observed.clear()
    assert set(bridge.production_statuses()) == set(readiness.BACKENDS)
    assert observed == list(readiness.BACKENDS)


@pytest.mark.parametrize("phase", ["get_worker", "acquire", "ensure_started", "submit"])
@pytest.mark.parametrize("error_name", ["discovery", "record", "host", "os"])
def test_prelease_runtime_failure_revokes_proof(runtime, monkeypatch, phase, error_name):
    from server.solver.beat_runtime import discovery, warm_cache
    from server.solver.beat_runtime.client import HostError

    error_type = {"discovery": discovery.JuliaDiscoveryError, "record": registry.RecordRefused,
                  "host": HostError, "os": OSError}[error_name]
    def fail(*a, **kw):
        raise error_type("runtime launch failed")
    if phase == "get_worker":
        monkeypatch.setattr(runtime.manager, "get_worker", fail)
    else:
        managed = runtime.manager.get_worker("cpu")
        if phase == "acquire":
            monkeypatch.setattr(managed, "acquire", fail)
        else:
            monkeypatch.setattr(managed.worker, phase, fail)
    before = warm_cache.generation()
    with pytest.raises(bridge.OfficialBeatUnavailable, match="runtime launch failed"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert warm_cache.generation() > before


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_plain_host_launch_errors_are_unavailable_and_revoke_proof(runtime, monkeypatch, error_type):
    from server.solver.beat_runtime import warm_cache

    def fail(*a, **kw):
        raise error_type("host launch failed")
    monkeypatch.setattr(runtime.manager, "get_worker", fail)
    before = warm_cache.generation()
    with pytest.raises(bridge.OfficialBeatUnavailable, match="host launch failed"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert warm_cache.generation() > before


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_request_or_compatibility_failure_keeps_cached_proof(runtime, monkeypatch, error_type):
    from server.solver.beat_runtime import warm_cache

    original = bridge.validated_negotiator
    def refuse(*a, **kw):
        raise error_type("request incompatible")
    monkeypatch.setattr(bridge, "validated_negotiator", lambda *a: (original(*a), refuse)[1])
    before = warm_cache.generation()
    with pytest.raises(error_type, match="request incompatible"):
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    assert warm_cache.generation() == before


@pytest.mark.parametrize("phase", ["shutdown", "cancelled"])
def test_prelease_ownership_closed_is_preserved(runtime, monkeypatch, phase):
    from server.solver.beat_runtime import warm_cache
    from server.solver.beat_runtime.ownership import OwnershipClosed

    if phase == "shutdown":
        runtime.manager.shutdown()
    else:
        error = OwnershipClosed("BEAT session cancelled before startup")
        def cancelled(*args, **kwargs):
            raise error
        monkeypatch.setattr(runtime.manager, "get_worker", cancelled)
    before = warm_cache.generation()
    with pytest.raises(OwnershipClosed) as caught:
        beat.solve_beat_from_msh_text(MESH.read_text(), _context(), backend="cpu")
    if phase == "cancelled":
        assert caught.value is error
    assert warm_cache.generation() == before


def test_prelease_unsupported_backend_is_preserved(runtime, monkeypatch):
    from server.solver.beat_runtime import warm_cache

    monkeypatch.setattr(manager, "resolve_key", manager._resolve_key)
    monkeypatch.setattr(bridge, "validated_negotiator", lambda *a: None)
    request = SimpleNamespace(wire={"solver_options": {"bem_backend": "unknown"}})
    before = warm_cache.generation()
    with pytest.raises(manager.UnsupportedBackend, match="supports CPU, Metal, CUDA and ROCm"):
        bridge.solve_compiled(request, channel_id="fixture", worker_manager=runtime.manager)
    assert warm_cache.generation() == before
