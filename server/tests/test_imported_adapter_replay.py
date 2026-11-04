"""Legacy fixtures frozen at 9b2ee5383 through engine dispatch and retention.

Only native numerical sweeps/device probes are substituted. Request/setup decoding,
engine dispatch, normalization, impedance mapping, LEM and recombination are real.
"""

import asyncio
import hashlib
import io
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from server.jobs.imported_setup import setup_of_request, solve_request_for, validate_setup
from server.jobs.models import ChannelCombineSpec
from server.solver import beat, bempp, bempp_process, metal, official_beat
from server.solver.beat_adapter.transducers import TransducerSweep, parse_transducer_frequency
from server.solver.recombine import recombine_stored_results
from server.tests import test_imported_bempp as cad
from server.tests.beat_adapter.test_assembly_native_solve import DRIVER, distinct
from server.tests.test_driver_lem import _spec

FIXTURE = Path(__file__).parent / "fixtures/imported_adapter_replay.json"
# The oracle was captured on macOS, where every replay reproduces it bit for bit.
# Linux and Windows libm round the LEM transfer function's transcendental terms
# differently in the last place: CI saw 1-2 ulp (at most 4.5e-16 relative) in cone
# excursion, SPL and phase, and different bytes in the LEM-scaled pressure bases.
# Off macOS every float meets the oracle at REPLAY_RTOL (about 200 times the observed
# difference) and the pressure bases meet their frozen arrays, which must hash to the
# oracle; everything else stays exact.
FROZEN_ARRAYS = FIXTURE.with_name("imported_adapter_replay_arrays.npz")
EXACT_PLATFORM = sys.platform == "darwin"
REPLAY_RTOL = 1e-13
# Compiled assemblies admit exterior transducers, not the legacy DriverSpec LEM.
# Their driven replay is covered by the coupled and mixed cases below.
CASES = [(origin, engine, mode) for origin in ("assembly", "cad", "file")
         for engine in ("beat-cpu", "bempp", "metal") for mode in ("prescribed", "lem")
         if origin != "assembly" or mode != "lem"]
CASES += [(origin, "beat-cpu", "coupled") for origin in ("assembly", "cad", "file")]
CASES += [("assembly", "beat-cpu", "mixed")]


def saved_request(origin, engine, mode, **changes):
    channels = [{"id": name, "source_ids": [source]} for name, source in
                (("left", "source-a"), ("right", "source-c"))]
    if mode == "lem":
        channels[1]["driver"] = _spec().model_dump(mode="json", exclude_none=True)
    if mode in ("coupled", "mixed"):
        for channel in channels if mode == "coupled" else channels[1:]:
            channel["motion"] = "axial"
            channel["exterior_transducer"] = DRIVER
    request = cad._request(
        drive_channels=channels,
        required_features=["wg-design-assembly-v1"] if origin == "assembly" else [],
        mesh={"rigid_size_mm": 8., "transition_mm": 20.,
              "source_size_mm": {"source-a": 3., "source-c": 4.}},
        **changes,
    )
    request.options.engine = engine
    request.options.polar_config.field_plane = False
    return request


def _native(frequencies, index, *, angles=None, planes=None, surface_average=True):
    angles = np.asarray([-180., 0., 180.] if angles is None else angles)
    planes = ["horizontal"] if planes is None else list(planes)
    pressure = np.full((len(frequencies), len(planes), len(angles)), (index + 1) * (1 + 2j) * 20e-6)
    return SimpleNamespace(
        frequencies_hz=np.asarray(frequencies), observation_angles_deg=angles, observation_planes=planes,
        pressure_complex=pressure, directivity_db=np.zeros(pressure.shape),
        impedance=np.full(len(frequencies), 0.01 + 0.02j),
        surface_pressure_avg=({tag: np.full(len(frequencies), 0.01 + 0.02j) for tag in (101, 103)}
                              if surface_average else None),
        solver_log=[], timings={}, native_diagnostics=[],
    )


def dispatch(monkeypatch, request, origin, *, surface_average=True):
    record = cad._record()
    if any(c.exterior_transducer for c in request.geometry.drive_channels):
        from server.tests.test_imported_beat import _mesh
        from server.solver.imported import mesh_text_sha256
        solid = _mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
                      [(101, (1, 3, 2)), (103, (1, 2, 4)), (102, (1, 4, 3)), (1, (2, 3, 4))])
        record.update(_execution_msh_text=solid, mesh_content_sha256=mesh_text_sha256(solid))
    record["producer"] = {"assembly": "design-assembly", "cad": "cad", "file": "mesh-file"}[origin]
    record["required_features"] = request.geometry.required_features
    record["excitations"] = [dict(id=c.id, source_ids=c.source_ids, motion=c.motion,
                                  **({"motion_axis": c.exterior_transducer.motion_axis} if c.exterior_transducer else {}))
                             for c in request.geometry.drive_channels]
    for source in record["sources"]:
        source["observed"] = {"total_area_mm2": 21000.}
    wire = request.model_dump_json()
    setup, exact = setup_of_request(json.loads(wire), record)
    assert exact
    replay = solve_request_for(validate_setup(setup), ingest_id=request.geometry.ingest_id,
                               manifest_sha256=request.geometry.manifest_sha256,
                               artifact_sha256=request.geometry.artifact_sha256, acknowledged_findings=[])
    assert replay.model_dump_json() == wire
    monkeypatch.setattr(official_beat, "production_statuses", lambda **_: {"cpu": {"available": True}})
    monkeypatch.setattr(official_beat, "engine_version", lambda: "fixture")
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "fixture"})
    monkeypatch.setattr(metal, "native_config", lambda **kw: SimpleNamespace(**kw))
    captured = []

    def beat_native(compiled, **kwargs):
        captured.append(compiled.wire)
        return {name: _native(compiled.wire["frequencies_hz"], i,
                              angles=compiled.layout.angles_deg, planes=compiled.layout.planes,
                              surface_average=surface_average)
                for i, name in enumerate(compiled.channel_ports)}

    def coupled_native(compiled, **kwargs):
        captured.append(compiled.wire)
        rows = tuple(parse_transducer_frequency(distinct(compiled, f), compiled, f)
                     for f in compiled.wire["frequencies_hz"])
        return TransducerSweep(rows, False, len(rows))

    def metal_native(_mesh, sources, config, frequencies_hz=None):
        captured.append({"sources": [{str(k): [v.real, v.imag] for k, v in s.items()} for s in sources]})
        return [_native(frequencies_hz, i, surface_average=surface_average) for i in range(len(sources))]

    monkeypatch.setattr(official_beat, "solve_compiled", beat_native)
    monkeypatch.setattr(official_beat, "solve_transducer_compiled", coupled_native)
    monkeypatch.setattr(metal, "native_solve_multi_source", metal_native)
    package = cad._RecordingBempp()
    cad._install(monkeypatch, package)

    def bempp_native(path, frequencies, config):
        result = _native(frequencies, len(captured), surface_average=surface_average)
        captured.append({"sources": config.velocity_sources})
        return result

    monkeypatch.setattr(bempp, "bempp_solve_frequencies", bempp_native)

    async def worker(msh_text, request, record, **kwargs):
        return bempp_process._solve_payload(
            {"kind": "imported", "msh_text": msh_text, "request": request, "record": record},
            stage=lambda *_: None, result=lambda *_: None)

    monkeypatch.setattr(bempp_process, "solve_imported_bempp_in_process", worker)
    engine = {"beat-cpu": beat.BeatEngine("cpu"), "bempp": bempp.BemppEngine(), "metal": metal.MetalEngine()}[request.options.engine]
    outcome = asyncio.run(engine.run(replay, cancel_cb=lambda: None, stage_cb=lambda *_: None,
                                     imported_record=record))
    assert captured, "the engine must dispatch a native solve"
    return wire, setup, outcome


def characterization(monkeypatch, origin, engine, mode):
    request = saved_request(origin, engine, mode)
    wire, setup, outcome = dispatch(monkeypatch, request, origin)
    with np.load(io.BytesIO(outcome.channel_bases), allow_pickle=False) as archive:
        bases = {name: archive[name] for name in archive.files}
    arrays = {name: hashlib.sha256(array.tobytes()).hexdigest() for name, array in bases.items()}
    channels = {}
    for name, channel in outcome.results["channels"].items():
        channels[name] = {key: channel[key] for key in ("spl_on_axis", "impedance", "acoustic_impedance") if key in channel}
        channels[name]["driver"] = channel["metadata"].get("driver")
    # Retained fields also feed the ordinary post-run combine owner.
    combine = ChannelCombineSpec.model_validate({"members": ["left", "right"], "reference": "left", "channels": {
        name: {"gain": {"mode": "manual", "db": 0}, "delay": {"mode": "manual", "ms": 0}}
        for name in ("left", "right")}})
    combined = recombine_stored_results(outcome.results, outcome.channel_bases, combine, request)["channels"]["combined"]
    return {"request_json": wire, "setup": setup, "basis_arrays_sha256": arrays, "channels": channels,
            "recombined_spl": combined["spl_on_axis"]}, bases


def assert_replays(actual, expected, path="$"):
    """Off-macOS oracle comparison: floats within REPLAY_RTOL, everything else exact."""
    if isinstance(expected, float):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), path
        assert math.isclose(actual, expected, rel_tol=REPLAY_RTOL, abs_tol=0), f"{path}: {actual!r} != {expected!r}"
    elif isinstance(expected, (dict, list)):
        assert type(actual) is type(expected), path
        assert len(actual) == len(expected), path
        if isinstance(expected, dict):
            assert actual.keys() == expected.keys(), path
        for key in (expected if isinstance(expected, dict) else range(len(expected))):
            assert_replays(actual[key], expected[key], f"{path}.{key}")
    else:
        assert actual == expected, path


@pytest.mark.parametrize("origin,engine,mode", CASES)
def test_frozen_legacy_adapter_replay(monkeypatch, origin, engine, mode):
    case = f"{origin}/{engine}/{mode}"
    expected = json.loads(FIXTURE.read_text())[case]
    actual, bases = characterization(monkeypatch, origin, engine, mode)
    if EXACT_PLATFORM:
        assert actual == expected
        return
    with np.load(FROZEN_ARRAYS, allow_pickle=False) as archive:
        frozen = {name.removeprefix(f"{case}/"): archive[name] for name in archive.files if name.startswith(f"{case}/")}
    hashes, oracle = actual.pop("basis_arrays_sha256"), expected.pop("basis_arrays_sha256")
    assert hashes.keys() == oracle.keys()
    for name in hashes:
        if hashes[name] != oracle[name]:
            assert name in frozen, f"{name} differs from the oracle and has no frozen array"
            assert bases[name].dtype == frozen[name].dtype
            np.testing.assert_allclose(bases[name], frozen[name], rtol=REPLAY_RTOL, atol=0, err_msg=name)
    assert_replays(actual, expected)


def test_frozen_arrays_are_the_oracle_bytes():
    expected = json.loads(FIXTURE.read_text())
    with np.load(FROZEN_ARRAYS, allow_pickle=False) as archive:
        assert archive.files
        for key in archive.files:
            case, name = key.rsplit("/", 1)
            assert hashlib.sha256(archive[key].tobytes()).hexdigest() == expected[case]["basis_arrays_sha256"][name], key


@pytest.mark.parametrize("engine", ["beat-cpu", "bempp", "metal"])
@pytest.mark.parametrize("mode", ["prescribed", "lem"])
@pytest.mark.parametrize("surface_average", [False, True])
def test_explicit_basis_has_identical_impedance_and_voltage_output(monkeypatch, engine, mode, surface_average):
    from server.solver.combine import deserialize_channel_bases

    outcomes = []
    for normalization in ("acceleration_legacy", "rms_velocity"):
        request = saved_request("cad", engine, mode, excitation_normalization=normalization)
        with monkeypatch.context() as patch:
            outcomes.append(dispatch(patch, request, "cad", surface_average=surface_average)[2])
    acceleration, velocity = outcomes
    for name in ("left", "right"):
        a = acceleration.results["channels"][name]
        v = velocity.results["channels"][name]
        for component in ("real", "imaginary"):
            np.testing.assert_allclose(v["impedance"][component], a["impedance"][component], rtol=1e-13)
        if mode == "lem" and name == "right":
            np.testing.assert_allclose(v["spl_on_axis"]["spl"], a["spl_on_axis"]["spl"], atol=1e-12)
            np.testing.assert_allclose(v["spl_on_axis"]["phase_degrees"], a["spl_on_axis"]["phase_degrees"], atol=1e-12)
            np.testing.assert_allclose(v["metadata"]["driver"]["cone_excursion_mm"]["values"],
                                       a["metadata"]["driver"]["cone_excursion_mm"]["values"], rtol=1e-13)
    a = deserialize_channel_bases(acceleration.channel_bases)["results_by_id"]["right"]
    v = deserialize_channel_bases(velocity.channel_bases)["results_by_id"]["right"]
    scale = 1 if mode == "lem" else (-2j * np.pi * np.asarray(a.frequencies_hz))[:, None, None]
    np.testing.assert_allclose(v.pressure_complex, a.pressure_complex * scale, rtol=1e-13)
