"""Native Metal exterior networks reuse the voltage request and result owners."""

from copy import deepcopy
from dataclasses import replace
import asyncio

import numpy as np
import pytest

from server.engines import registry
from server.platform import feature_flags
from server.solver import beat_transducer_imported as jobs, metal, metal_worker
from server.solver.beat_adapter.transducers import TransducerSweep, parse_transducer_frequency
from server.solver.beat_adapter.results import ResultContractError
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
def test_route_uses_same_request_and_unscaled_results(monkeypatch, solid, record, mixed):
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
    captured, partial, sweeps = [], [], []

    def solve(compiled, **callbacks):
        captured.append(compiled)
        rows = tuple(parse_transducer_frequency(native_record(compiled, f), compiled, f)
                     for f in compiled.wire["frequencies_hz"])
        sweep = TransducerSweep(rows, False, len(rows))
        sweeps.append(sweep)
        for count in range(1, len(rows) + 1):
            callbacks["on_frequency_result"](TransducerSweep(rows[:count], False, len(rows)))
        return sweep

    monkeypatch.setattr(metal_worker, "solve_transducer_worker", solve)
    result = metal.solve_imported_metal_from_msh_text(solid, requested, record,
        result_callback=lambda i, frame: partial.append(frame))
    assert captured[0].wire == expected.wire
    engine = result["metadata"]["solver_engine"]
    assert engine["engine"] == "metal" and engine["package"] == "hornlab-metal-bem"
    assert engine["provider"] == "native"
    assert engine["formulation"] == "complex_k" and engine["complex_k_shift"] == .005
    reference = jobs.package_sweep(sweeps[0], expected, requested, record, started=0,
                                   port_indices=indices, solver_engine=engine)
    for channel in requested.geometry.drive_channels:
        for key in ("spl_on_axis", "impedance", "acoustic_impedance", "phase"):
            assert result["channels"][channel.id].get(key) == reference["channels"][channel.id].get(key)
    assert result["metadata"]["radiation_impedance_matrix"] == reference["metadata"]["radiation_impedance_matrix"]
    assert result["channels"]["driver"]["metadata"]["drive"]["voltage_v"] == 4
    assert result["channels"]["driver"]["metadata"]["source_normalization"] == "native_rms_voltage"
    assert result["channels"]["driver"]["metadata"]["beat_transducer"]["current_rms_a"][0][0] == {
        "real": .4, "imaginary": .2}
    assert all(p["metadata"]["solver_engine"] == engine for p in partial)
    if mixed:
        assert result["channels"]["ideal"]["metadata"]["source_normalization"] == "native_rms_velocity"
        assert "impedance" not in result["channels"]["ideal"]
        assert result["_channel_bases_npz"]


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


@pytest.fixture
def worker(monkeypatch):
    import hornlab_metal_bem
    from hornlab_metal_bem.beat_worker import worker_announcement

    instances = []

    class Worker:
        def __init__(self, *, native_formulation, complex_k_shift, native_progress_cb):
            self.options = (native_formulation, complex_k_shift)
            self.progress = native_progress_cb
            self.worker_info = worker_announcement({"available": True})
            self.closed = self.terminated = self.submitted = False
            self.cancelled = False
            self.mutate = lambda data: None
            instances.append(self)

        def ensure_started(self):
            pass

        def submit(self, request, *, status_callback=None):
            self.submitted = True
            self.request = request
            try:
                if status_callback:
                    status_callback("native network")
                for index, frequency in enumerate(request["frequencies_hz"]):
                    self.progress(index, frequency, {})
                    data = native_record(self.compiled, frequency)
                    self.mutate(data)
                    yield {"type": "result", "result": data}
                    if self.cancelled:
                        yield {"type": "cancelled", "solved_count": index + 1}
                        return
                yield {"type": "completed", "solved_count": len(request["frequencies_hz"])}
            finally:
                self.closed = True

        def terminate(self):
            self.terminated = True

    monkeypatch.setattr(hornlab_metal_bem, "MetalEngineWorker", Worker)
    return instances, Worker


def install_worker(monkeypatch, worker, compiled, configure=lambda _: None):
    instances, Worker = worker
    original = Worker.ensure_started
    def started(self):
        self.compiled = compiled
        configure(self)
        original(self)
    monkeypatch.setattr(Worker, "ensure_started", started)
    return instances


def test_worker_decodes_driven_and_shorted_network_without_scaling(monkeypatch, worker, solid, record):
    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    # Only the first voltage port is excited; both physical drivers remain in the network.
    compiled = replace(compiled, wire={**compiled.wire, "excitation_port_ids": ["voltage:driver"]})
    instances = install_worker(monkeypatch, worker, compiled)
    original_wire = deepcopy(compiled.wire)
    partial, statuses = [], []
    result = metal_worker.solve_transducer_worker(compiled, on_frequency_result=partial.append,
                                                 status_callback=statuses.append)
    assert compiled.wire == original_wire
    assert instances[0].request is compiled.wire
    assert instances[0].options == ("complex_k", .005)
    assert instances[0].closed and instances[0].terminated
    assert statuses == ["native network"] and len(partial) == 2
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


@pytest.mark.parametrize("failure", ["decode", "callback", "native_cancel", "negotiate"])
def test_worker_closes_and_preserves_errors(monkeypatch, worker, solid, record, failure):
    from beat_engine.beat_contract.worker import WorkerCompatibilityError

    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    def configure(instance):
        if failure == "decode":
            instance.mutate = lambda data: data.update(excitation_port_ids=[])
        if failure == "negotiate":
            instance.worker_info["contracts"]["compiled_system"] = [1]
    instances = install_worker(monkeypatch, worker, compiled, configure)
    def stopped(*_):
        raise LookupError("caller stopped")
    def cancel():
        if instances and instances[0].submitted:
            stopped()
    expected = LookupError if failure in {"callback", "native_cancel"} else (WorkerCompatibilityError, ResultContractError)
    with pytest.raises(expected):
        metal_worker.solve_transducer_worker(compiled,
            on_frequency_result=stopped if failure == "callback" else None,
            cancellation_callback=cancel if failure == "native_cancel" else None)
    assert instances[0].terminated
    assert instances[0].closed if failure != "negotiate" else not instances[0].submitted


def test_worker_preserves_cancelled_prefix(monkeypatch, worker, solid, record):
    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    instances = install_worker(monkeypatch, worker, compiled, lambda w: setattr(w, "cancelled", True))
    result = metal_worker.solve_transducer_worker(compiled)
    assert result.cancelled and len(result.rows) == 1 and result.requested_frequency_count == 2
    assert instances[0].closed and instances[0].terminated


def test_worker_rejects_unsupported_request_before_construction(monkeypatch, worker, solid, record):
    compiled, _ = jobs.compile_request(solid, metal_request(), record, backend="metal")
    def reject(_):
        raise ValueError("unsupported driver option")
    monkeypatch.setattr(metal_worker, "validate_worker_request", reject)
    with pytest.raises(ValueError, match="unsupported driver option.*BEAT"):
        metal_worker.solve_transducer_worker(compiled)
    assert not worker[0]
