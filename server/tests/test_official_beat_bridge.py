"""Production provider routing with managed fake engine events; no Julia."""

from __future__ import annotations

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
                            cancel_after=None, cancel_submission=None, mutate=None, result_count=0, fail=False)

    class Worker:
        worker_info = {"type": "ready"}

        def __init__(self, *args, **kwargs):
            state.workers.append(self)

        def ensure_started(self):
            state.calls.append("start")

        def submit(self, path, **kwargs):
            state.calls.append("submit")
            state.paths.append(path)
            request = json.loads(path.read_text())
            state.requests.append(request)

            def events():
                solved = 0
                if state.fail:
                    yield {"type": "failed", "error": "assembly failed"}
                    return
                for frequency in request["frequencies_hz"]:
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


@pytest.mark.parametrize("backend", ["cpu", "metal"])
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
    assert response["frequencies"] == sorted(response["frequencies"])
    assert len(response["frequencies"]) == context.num_frequencies
    assert response["_field_traces"] is not None
    assert response["_field_trace_unavailable_reason"] is None
    assert frames and progress
    assert len(runtime.workers) == 1
    assert runtime.calls.index("negotiate") < runtime.calls.index("submit")
    wire = runtime.requests[0]
    assert wire["compiled_system"]["contract_version"] == (2 if motion == "axial" else 1)
    assert wire["solver_options"]["regular_quadrature_mode"] == ("wavelength" if backend == "cpu" else "fixed")
    assert [output["id"] for output in wire["outputs"]][:3] == [
        "pressure:vertical", "pressure:diagonal", "pressure:horizontal"]
    assert adaptive or wire["frequencies_hz"][:2] == [500., 2000.]
    assert "terminate" not in runtime.calls


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("axial", [False, True])
def test_imported_production(runtime, adaptive, axial):
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
        msh, request, cad._record(msh_text=msh), backend="cpu",
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
            request.options.frequency_range = [100., 1000.]
            request.options.frequencies_hz = None
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
def test_selector_off_calls_hbb_and_never_imports_engine(monkeypatch, tmp_path, imported, adaptive):
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
    snapshot = _snapshot(result)
    expected = json.loads((Path(__file__).parent / "beat_adapter/fixtures/hbb_production.json").read_text())
    key = ("imported" if imported else "parametric") + ("_adaptive" if adaptive else "")
    assert snapshot == expected[key]


def _snapshot(value):
    """Freeze the response, including binary artifact identity, for HBB parity."""
    import hashlib

    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {k: _snapshot(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot(v) for v in value]
    return value


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
    with pytest.raises((beat.BeatUnavailable, ValueError)):
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
def test_callback_cancellation_retains_emitted_rows(runtime, imported):
    cancelled = []
    def cancel():
        if cancelled:
            raise RuntimeError("requested cancellation")
    def publish(*args):
        cancelled.append(True)
    if imported:
        request = cad._request()
        result = beat_imported.solve_imported_beat_from_msh_text(
            cad.MESH, request, cad._record(), backend="cpu",
            cancellation_callback=cancel, result_callback=publish)
    else:
        result = beat.solve_beat_from_msh_text(
            MESH.read_text(), _context(), backend="cpu", cancellation_callback=cancel,
            result_callback=publish)
    assert result["frequencies"] == [100.] if imported else result["frequencies"] == [500.]
    assert result["metadata"]["cancelled"]


def test_registry_official_readiness_without_hbb(runtime, monkeypatch):
    import asyncio
    from server.engines.registry import EngineRegistry, detect_engines

    async def scenario():
        engines = EngineRegistry(cpu_refresh=True, detector=lambda: detect_engines(names=("beat-cpu", "beat-metal")))
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
