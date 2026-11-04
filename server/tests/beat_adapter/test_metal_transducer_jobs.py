"""Native Metal exterior networks reuse the voltage request and result owners."""

from copy import deepcopy
from dataclasses import replace
import asyncio
from pathlib import Path
import subprocess
import sys
import json
from types import SimpleNamespace

import numpy as np
import pytest

from server.engines import registry
from server.platform import feature_flags
from server.solver import beat_transducer_imported as jobs, metal, metal_worker
from server.solver.beat_adapter.transducers import TransducerSweep, parse_transducer_frequency
from server.solver.beat_adapter.results import ResultContractError, decode_complex_values
from server.tests.beat_adapter.conftest import wire
from server.tests.beat_adapter.test_transducer_jobs import (
    record as record_fixture, request, solid as solid_fixture,
)
from server.tests.beat_adapter.test_transducers import raw

record = record_fixture
solid = solid_fixture
FLAG = "WG2_METAL_EXTERIOR_TRANSDUCERS"


def metal_request(*, mixed=False):
    payload = request().model_dump(mode="json")
    payload["options"]["engine"] = "metal"
    payload["geometry"]["drive_voltage_v"] = 4.0
    if mixed:
        payload["geometry"]["required_features"] = ["wg-design-assembly-v1"]
        payload["geometry"]["drive_channels"][1] = {
            "id": "ideal", "source_ids": ["c", "d"], "motion": "normal",
        }
    return type(request()).model_validate(payload)


def native_record(compiled, frequency):
    data = raw(compiled, frequency)
    # Match the worker's mixed precision contract without running a native solve.
    for item in data["quantities"]:
        if item["quantity"] == "exterior_pressure":
            item["values"] = wire(np.full(item["values"]["shape"], 2 + 3j), "float32")
        if item["quantity"] == "radiation_impedance_matrix":
            item["metadata"]["effective_volume_area_m2"] = [.02] * len(compiled.matrix_ids)
    data["diagnostics"]["engine_provenance"] = {"implementation": "hornlab-metal-bem"}
    return data


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), ("0", False),
    ("false", False), ("unknown", False), ("1", True), ("true", True), (" ON ", True)])
def test_flag_defaults_off(value, expected):
    env = {} if value is None else {FLAG: value}
    assert feature_flags.metal_exterior_transducers_enabled(environ=env) is expected


@pytest.mark.parametrize("enabled", [False, True])
def test_registry_advertises_only_enabled_complete_worker(monkeypatch, enabled):
    monkeypatch.setenv(FLAG, "1" if enabled else "0")
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True})
    info = registry.detect_engines(names=("metal",))[0]
    assert ("exterior-transducers" in info.imported_features) is enabled
    assert "passive-cardioid" in info.imported_features


@pytest.mark.parametrize("key", ["components", "outputs", "compiled_system_versions",
    "motion_profiles", "symmetry", "phasor_conventions", "precisions"])
def test_incomplete_installed_worker_is_not_advertised(monkeypatch, key):
    import hornlab_metal_bem

    monkeypatch.setenv(FLAG, "1")
    capabilities = deepcopy(hornlab_metal_bem.compiled_capabilities())
    capabilities[key] = []
    monkeypatch.setattr(hornlab_metal_bem, "compiled_capabilities", lambda: capabilities)
    assert "exterior-transducers" not in metal.imported_features_for({"available": True})


@pytest.mark.parametrize("failure", ["unavailable", "old_worker", "bad_capabilities"])
def test_unusable_worker_is_not_advertised(monkeypatch, failure):
    import hornlab_metal_bem

    monkeypatch.setenv(FLAG, "1")
    if failure == "old_worker":
        monkeypatch.setattr(metal_worker, "worker_available", lambda: False)
    if failure == "bad_capabilities":
        monkeypatch.setattr(hornlab_metal_bem, "compiled_capabilities", lambda: None)
    assert "exterior-transducers" not in metal.imported_features_for({"available": failure != "unavailable"})


def test_disabled_direct_solve_refuses_before_native_or_lem(monkeypatch, solid, record):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(metal, "metal_status", lambda: pytest.fail("native probe"))
    with pytest.raises(metal.MetalUnavailable, match="WG2_METAL_EXTERIOR_TRANSDUCERS.*BEAT"):
        metal.solve_imported_metal_from_msh_text(solid, metal_request(), record)
    assert "WG2_METAL_EXTERIOR_TRANSDUCERS" in metal.MetalEngine().imported_preflight(
        record, solid, request=metal_request())


@pytest.mark.parametrize("mixed", [False, True])
def test_route_uses_same_request_and_unscaled_results(monkeypatch, worker, solid, record, mixed):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True})
    monkeypatch.setattr(metal, "native_solve_multi_source", lambda *a, **k: pytest.fail("ideal path"))
    monkeypatch.setattr("server.solver.imported_channels.apply_channel_driver",
                        lambda *a, **k: pytest.fail("second LEM"))
    if mixed:
        record["producer"] = "design-assembly"
    requested = metal_request(mixed=mixed)
    if mixed:
        record["required_features"] = ["wg-design-assembly-v1"]
        record["excitations"] = [
            {"id": c.id, "source_ids": c.source_ids, "motion": c.motion,
             **({"motion_axis": [1, 0, 0]} if c.exterior_transducer else {})}
            for c in requested.geometry.drive_channels
        ]
    expected, indices = jobs.compile_request(solid, requested, record, backend="metal")
    prepare_worker(worker, expected)
    result = metal.solve_imported_metal_from_msh_text(solid, requested, record,
        result_callback=lambda i, frame: worker.partial.append(frame))
    assert worker.requests == [expected.wire]
    assert_worker_stopped(worker)
    engine = result["metadata"]["solver_engine"]
    assert engine["engine"] == "metal" and engine["package"] == "hornlab-metal-bem"
    assert engine["provider"] == "native"
    assert engine["formulation"] == "complex_k" and engine["complex_k_shift"] == .005
    rows = tuple(parse_transducer_frequency(native_record(expected, f), expected, f)
                 for f in expected.wire["frequencies_hz"])
    reference = jobs.package_sweep(TransducerSweep(rows, False, len(rows)), expected, requested, record, started=0,
                                   port_indices=indices, solver_engine=engine)
    for channel in requested.geometry.drive_channels:
        for key in ("spl_on_axis", "impedance", "acoustic_impedance", "phase"):
            assert result["channels"][channel.id].get(key) == reference["channels"][channel.id].get(key)
    assert result["metadata"]["radiation_impedance_matrix"] == reference["metadata"]["radiation_impedance_matrix"]
    assert result["channels"]["driver"]["metadata"]["drive"]["voltage_v"] == 4
    assert result["channels"]["driver"]["metadata"]["source_normalization"] == "native_rms_voltage"
    assert result["channels"]["driver"]["metadata"]["beat_transducer"]["current_rms_a"][0][0] == {
        "real": .4, "imaginary": .2}
    assert worker.finished and not worker.partial
    if mixed:
        assert result["channels"]["ideal"]["metadata"]["source_normalization"] == "native_rms_velocity"
        assert "impedance" not in result["channels"]["ideal"]
        assert result["_channel_bases_npz"]


@pytest.mark.parametrize("wired", [False, True])
@pytest.mark.parametrize("normalization", ["rms_velocity", "acceleration_legacy"])
@pytest.mark.parametrize("weight", [-.25, 0.])
def test_signed_axial_prescribed_basis_and_wiring_share_metal_owner(
    monkeypatch, worker, solid, record, wired, normalization, weight,
):
    from server.solver.combine import deserialize_channel_bases

    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True})
    driver_id = "00000000-0000-4000-8000-000000000001"
    network_id = "00000000-0000-4000-8000-000000000010"
    payload = metal_request(mixed=True).model_dump(mode="json")
    geometry = payload["geometry"]
    geometry["excitation_normalization"] = normalization
    geometry["drive_channels"][0]["id"] = driver_id
    ideal = geometry["drive_channels"][1]
    ideal.update(motion="axial", patch_weights={"c": weight, "d": weight})
    if wired:
        geometry["driver_groups"] = [{"id": "lf", "channel_ids": [driver_id], "label": "LF"}]
        geometry["wiring_groups"] = [{"id": network_id, "label": "LF", "network": {
            "kind": "series", "children": [
                {"kind": "driver", "radiator_id": driver_id}, {"kind": "R", "value": 2.0},
            ],
        }}]
    requested = type(request()).model_validate(payload)
    # The record declares independent axial directions; producer is irrelevant.
    record.update(producer="cad", required_features=geometry["required_features"], excitations=[
        {"id": driver_id, "source_ids": ["a", "b"], "motion": "axial", "motion_axis": [1, 0, 0]},
        {"id": "ideal", "source_ids": ["c", "d"], "motion": "axial",
         "motion_axis": [0, 0, 1], "patch_weights": ideal["patch_weights"]},
    ])
    compiled, _ = jobs.compile_request(solid, requested, record, backend="metal")
    assert metal.MetalEngine().imported_preflight(record, solid, request=requested) is None
    components = compiled.wire["compiled_system"]["components"]
    prescribed = [component for component in components if component["kind"] == "ideal_velocity_source"]
    assert len(prescribed) == 2
    assert all(component["parameters"]["motion_axis"] == [0, 0, 1] for component in prescribed)
    prepare_worker(worker, compiled)
    result = metal.solve_imported_metal_from_msh_text(solid, requested, record)
    assert_worker_stopped(worker)
    bases = deserialize_channel_bases(result["_channel_bases_npz"])["results_by_id"]
    scale = np.full(len(compiled.wire["frequencies_hz"]), 2 * weight, complex)
    if normalization == "acceleration_legacy":
        scale /= -2j * np.pi * np.asarray(compiled.wire["frequencies_hz"])
    np.testing.assert_allclose(bases["ideal"].pressure_complex[:, 0, 0], scale * (2 + 3j))
    np.testing.assert_allclose(bases["ideal"].sphere_pressure_complex[:, 0], scale * (2 + 3j))
    if wired:
        assert network_id in result["channel_order"]
        assert result["channels"][network_id]["metadata"]["solver_engine"]["engine"] == "metal"
        np.testing.assert_allclose(bases["ideal"].native_current[:, 0], scale * (.4 + .2j))
        np.testing.assert_allclose(bases["ideal"].native_velocity[:, 0], scale * (1 - 2j))


def test_signed_driver_weights_refuse_before_metal_worker(monkeypatch, solid, record):
    monkeypatch.setenv(FLAG, "1")
    requested = metal_request()
    requested.geometry.drive_channels[0].patch_weights = {"a": 1., "b": -.25}
    refusal = metal.MetalEngine().imported_preflight(record, solid, request=requested)
    assert "signed or nonunit patch weights" in refusal and "fixed velocity" in refusal


@pytest.mark.parametrize("mixed", [False, True])
def test_metal_admission_and_solve_do_not_import_beat_engine(solid, record, mixed):
    requested = metal_request(mixed=mixed)
    if mixed:
        record["producer"] = "design-assembly"
        record["required_features"] = ["wg-design-assembly-v1"]
        record["excitations"] = [
            {"id": c.id, "source_ids": c.source_ids, "motion": c.motion,
             **({"motion_axis": [1, 0, 0]} if c.exterior_transducer else {})}
            for c in requested.geometry.drive_channels
        ]
    compiled, _ = jobs.compile_request(solid, requested, record, backend="metal")
    payload = {
        "mesh": solid, "request": requested.model_dump(mode="json"), "record": record,
        "compiled": compiled.wire,
        "rows": [native_record(compiled, f) for f in compiled.wire["frequencies_hz"]],
    }
    # A fresh interpreter also catches imports hidden by test collection. Block
    # the actual architecture contract's engine modules while both entry points
    # compile, validate and package a complete stubbed native sweep.
    completed = subprocess.run(
        [sys.executable, "-c", """
import configparser
import importlib.abc
import json
import os
import sys

config = configparser.ConfigParser()
config.read('.importlinter')
forbidden = config['importlinter:contract:solver-engine-beat']['forbidden_modules'].split()

class NoBeatEngine(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('Metal reached BEAT engine module: ' + fullname)

sys.meta_path.insert(0, NoBeatEngine())
from server.jobs.models import SolveRequest
from server.solver import metal, metal_worker
from server.solver.beat_adapter.transducers import TransducerSweep, parse_transducer_frequency

payload = json.load(sys.stdin)
request = SolveRequest.model_validate(payload['request'])
os.environ['WG2_METAL_EXTERIOR_TRANSDUCERS'] = '1'
metal.metal_status = lambda: {'available': True}
metal_worker.transducer_worker_available = lambda: True
validated = []
def validate(compiled):
    assert compiled.wire == payload['compiled']
    validated.append(compiled)
metal_worker.validate_worker_request = validate

def solve(compiled, **callbacks):
    assert compiled.wire == payload['compiled']
    rows = tuple(parse_transducer_frequency(raw, compiled, frequency)
                 for raw, frequency in zip(payload['rows'], compiled.wire['frequencies_hz']))
    return TransducerSweep(rows, False, len(rows))
metal_worker.solve_transducer_worker = solve

assert metal.MetalEngine().imported_preflight(
    payload['record'], payload['mesh'], request=request) is None
assert len(validated) == 1
partial = []
result = metal.solve_imported_metal_from_msh_text(
    payload['mesh'], request, payload['record'],
    result_callback=lambda revision, frame: partial.append(frame))
assert not partial
assert result['metadata']['solver_engine']['package'] == 'hornlab-metal-bem'
assert result['channel_order'] == [channel.id for channel in request.geometry.drive_channels]
"""],
        cwd=Path(__file__).resolve().parents[3], input=json.dumps(payload),
        capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_admission_validates_complete_request_before_job(monkeypatch, solid, record):
    from server.jobs.runtime.imported_refusals import _imported_preflight_refusal

    monkeypatch.setenv(FLAG, "1")
    seen = []
    def reject(compiled):
        seen.append(compiled)
        raise ValueError("installed worker lacks chamber model")
    monkeypatch.setattr(metal_worker, "validate_worker_request", reject)
    req = metal_request()
    refusal = asyncio.run(_imported_preflight_refusal(metal.MetalEngine(), record, solid,
                                                     req.geometry.drive_channels, req))
    assert "chamber model" in refusal and "BEAT" in refusal
    assert seen[0].wire["solver_options"]["transducer_reference_voltage_v"] == 4


@pytest.mark.parametrize("enabled", [False, True])
def test_submission_obeys_gate_and_native_validator(monkeypatch, solid, record, enabled):
    from server.jobs.runtime.core import ImportedSolveRefusal, resolve_imported_submission

    monkeypatch.setenv(FLAG, "1" if enabled else "0")
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True})
    engines = registry.EngineRegistry(detector=lambda: registry.detect_engines(names=("metal",)),
                                      factory=lambda _: metal.MetalEngine())
    call = resolve_imported_submission(metal_request(), engines, imported_record=record,
                                       imported_msh_text=solid, request_refusal=None)
    if enabled:
        assert asyncio.run(call).engine_name == "metal"
    else:
        with pytest.raises(ImportedSolveRefusal):
            asyncio.run(call)


def test_exterior_route_never_drops_fem_regions(monkeypatch, solid, record):
    monkeypatch.setenv(FLAG, "1")
    record["fem_regions"] = [{"id": "interior"}]
    with pytest.raises(metal.MetalUnavailable, match="FEM regions.*BEAT.*exterior-only"):
        metal.solve_imported_metal_from_msh_text(solid, metal_request(), record)


@pytest.mark.parametrize("enabled", [False, True])
def test_saved_auto_voltage_jobs_keep_beat_provider(monkeypatch, solid, record, enabled):
    from server.jobs.runtime.core import resolve_imported_submission

    monkeypatch.setenv(FLAG, "1" if enabled else "0")
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True})
    beat = registry.EngineInfo("beat-cpu", True, "ready", "test", geometry_sources=("imported",),
                               imported_features=("exterior-transducers",))
    engines = registry.EngineRegistry(
        detector=lambda: [*registry.detect_engines(names=("metal",)), beat],
        factory=lambda name: metal.MetalEngine() if name == "metal" else object())
    saved = metal_request().model_dump(mode="json")
    saved["options"]["engine"] = "auto"
    req = type(request()).model_validate(saved)
    result = asyncio.run(resolve_imported_submission(req, engines, imported_record=record,
                        imported_msh_text=solid, request_refusal=None))
    assert result.engine_name == "beat-cpu"
    assert result.symmetry_metadata["solver_plan"]["requested"] == "auto"


def compiled_result(compiled):
    """Supply numerical arrays to the provider's real result encoder."""
    from hornlab_metal_bem.compiled import CompiledQuantity, CompiledResult

    rows = [native_record(compiled, f) for f in compiled.wire["frequencies_hz"]]
    quantities = {}
    for index, item in enumerate(rows[0]["quantities"]):
        values = np.stack([decode_complex_values(row["quantities"][index]["values"],
                           tuple(item["values"]["shape"])) for row in rows])
        quantities[item["id"]] = CompiledQuantity(
            id=item["id"], quantity=item["quantity"], unit=item["unit"],
            axes=("frequency", *item["axes"]), values=values,
            metadata=item["metadata"], target_id=item["target_id"],
        )
    return CompiledResult(
        frequencies_hz=np.asarray(compiled.wire["frequencies_hz"]),
        excitation_port_ids=tuple(compiled.wire["excitation_port_ids"]),
        pressure={}, native_results=(), quantities=quantities, diagnostics=rows[0]["diagnostics"],
    )


@pytest.fixture
def worker(monkeypatch):
    """Use the installed worker, thread, event stream and encoder; stub computation."""
    from hornlab_metal_bem import beat_worker as provider

    state = SimpleNamespace(instances=[], submissions=[], requests=[], computed=[],
                            result=None, finished=False, observed=[])
    original_started = provider.MetalEngineWorker.ensure_started
    original_submit = provider.MetalEngineWorker.submit
    monkeypatch.setattr(provider, "_metal_availability", lambda: {"available": True})

    def started(self, **kwargs):
        if self not in state.instances:
            state.instances.append(self)
        original_started(self, **kwargs)

    def submit(self, request, **kwargs):
        state.requests.append(request)
        stream = original_submit(self, request, **kwargs)
        state.submissions.append(stream)
        return stream

    def compute(request, *, base_dir, cancel_cb, native_formulation, complex_k_shift, native_progress_cb):
        assert request == state.expected_wire
        assert native_formulation == "complex_k" and complex_k_shift == .005
        for index, frequency in enumerate(request["frequencies_hz"]):
            cancel_cb()
            state.computed.append(frequency)
            state.observed.append(list(state.partial))
            native_progress_cb(index, frequency, {})
        state.finished = True
        return state.result

    monkeypatch.setattr(provider.MetalEngineWorker, "ensure_started", started)
    monkeypatch.setattr(provider.MetalEngineWorker, "submit", submit)
    monkeypatch.setattr(provider, "solve_compiled", compute)
    return state


def prepare_worker(state, compiled):
    state.expected_wire = deepcopy(compiled.wire)
    state.result = compiled_result(compiled)
    state.partial = []


def assert_worker_stopped(state):
    assert len(state.instances) == 1
    assert state.instances[0].worker_info is None
    assert all(not stream.running for stream in state.submissions)


def test_worker_decodes_driven_and_shorted_network_without_scaling(worker, solid, record):
    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    # Only the first voltage port is excited; both physical drivers remain in the network.
    compiled = replace(compiled, wire={**compiled.wire, "excitation_port_ids": ["voltage:driver"]})
    prepare_worker(worker, compiled)
    original_wire = deepcopy(compiled.wire)
    statuses = []
    result = metal_worker.solve_transducer_worker(compiled, on_frequency_result=worker.partial.append,
                                                 status_callback=statuses.append)
    assert compiled.wire == original_wire
    assert worker.requests == [compiled.wire]
    assert worker.instances[0].native_formulation == "complex_k"
    assert worker.instances[0].complex_k_shift == .005
    assert_worker_stopped(worker)
    assert statuses == ["Solving on hornlab-metal-bem native Metal"]
    assert worker.finished and worker.observed == [[], []]
    assert not worker.partial  # This experimental route publishes only the durable final.
    for actual in result.rows:
        expected = parse_transducer_frequency(native_record(compiled, actual.frequency_hz),
                                              compiled, actual.frequency_hz)
        np.testing.assert_array_equal(actual.velocity_rms_m_per_s, expected.velocity_rms_m_per_s)
        np.testing.assert_array_equal(actual.current_rms_a, expected.current_rms_a)
        np.testing.assert_array_equal(actual.radiation.values, expected.radiation.values)
        for plane in actual.pressure_rms_pa:
            assert actual.pressure_rms_pa[plane].tobytes() == expected.pressure_rms_pa[plane].tobytes()
        assert actual.input_impedance_ohm("voltage:driver") == 4 / (.4 + .2j)
        assert actual.transducer_ids == ("transducer:driver", "transducer:shorted")
        assert actual.diagnostics["engine_provenance"]["implementation"] == "hornlab-metal-bem"


@pytest.mark.parametrize("failure", ["decode", "status", "native_cancel", "negotiate", "startup", "submit"])
def test_worker_closes_and_preserves_errors(monkeypatch, worker, solid, record, failure):
    from beat_engine.beat_contract.worker import WorkerCompatibilityError
    from hornlab_metal_bem import beat_worker as provider

    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    prepare_worker(worker, compiled)
    stopped = LookupError("caller stopped")

    def stop(*args, **kwargs):
        raise stopped

    if failure == "decode":
        worker.result = replace(worker.result, excitation_port_ids=())
    elif failure == "negotiate":
        original = provider.worker_announcement
        def announcement(availability):
            info = original(availability)
            info["contracts"]["compiled_system"] = [1]
            return info
        monkeypatch.setattr(provider, "worker_announcement", announcement)
    elif failure == "startup":
        original = provider.MetalEngineWorker.ensure_started
        def started(self, **kwargs):
            original(self, **kwargs)
            stop()
        monkeypatch.setattr(provider.MetalEngineWorker, "ensure_started", started)
    elif failure == "submit":
        monkeypatch.setattr(provider.MetalEngineWorker, "submit", stop)

    def cancel():
        if worker.computed:
            stop()

    expected = (ResultContractError if failure == "decode" else
                WorkerCompatibilityError if failure == "negotiate" else LookupError)
    with pytest.raises(expected) as caught:
        metal_worker.solve_transducer_worker(compiled,
            status_callback=stop if failure == "status" else None,
            cancellation_callback=cancel if failure == "native_cancel" else None)
    if expected is LookupError:
        assert caught.value is stopped
    assert_worker_stopped(worker)
    if failure in {"startup", "negotiate", "submit"}:
        assert not worker.submissions


def test_worker_cancellation_discards_computed_prefix(worker, solid, record):
    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    prepare_worker(worker, compiled)
    stopped = LookupError("caller stopped")

    def cancel():
        if worker.computed:
            raise stopped

    with pytest.raises(LookupError) as caught:
        metal_worker.solve_transducer_worker(compiled, cancellation_callback=cancel,
                                              on_frequency_result=worker.partial.append)
    assert caught.value is stopped
    assert worker.computed == compiled.wire["frequencies_hz"][:1]
    assert not worker.partial and not worker.finished
    assert_worker_stopped(worker)


def test_provider_cancellation_emits_zero_results(worker, solid, record):
    from hornlab_metal_bem import MetalEngineWorker

    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    prepare_worker(worker, compiled)
    def cancel(*_):
        raise InterruptedError("stop after one computed frequency")
    native = MetalEngineWorker(native_formulation="complex_k", native_progress_cb=cancel)
    try:
        native.ensure_started()
        stream = native.submit(compiled.wire)
        events = list(stream)
        stream.close()
    finally:
        native.terminate()
    assert events == [
        {"type": "status", "message": "Solving on hornlab-metal-bem native Metal"},
        {"type": "cancelled", "solved_count": 0},
    ]
    assert worker.computed == compiled.wire["frequencies_hz"][:1]
    assert not worker.finished
    assert_worker_stopped(worker)


def test_worker_native_cancellation_explains_retry(monkeypatch, worker, solid, record):
    from hornlab_metal_bem import beat_worker as provider

    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    prepare_worker(worker, compiled)
    def compute(*args, **kwargs):
        raise InterruptedError("native computation stopped")
    monkeypatch.setattr(provider, "solve_compiled", compute)
    with pytest.raises(RuntimeError, match="partial results are unavailable.*Retry.*BEAT"):
        metal_worker.solve_transducer_worker(compiled, on_frequency_result=worker.partial.append)
    assert not worker.partial
    assert_worker_stopped(worker)


def test_worker_rejects_unsupported_request_before_construction(monkeypatch, worker, solid, record):
    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    def reject(_):
        raise ValueError("unsupported driver option")
    monkeypatch.setattr(metal_worker, "validate_worker_request", reject)
    with pytest.raises(ValueError, match="unsupported driver option.*BEAT"):
        metal_worker.solve_transducer_worker(compiled)
    assert not worker.instances
