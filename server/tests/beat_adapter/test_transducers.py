"""Explicit v3 adoption: independent units/order, driver physics and refusal."""
from copy import deepcopy
from dataclasses import replace
import importlib.resources
import json

import numpy as np
import pytest

from server.solver.beat_adapter import transducers as t
from server.solver.beat_adapter.preflight import UnsupportedPhysics, validate_exterior_physics
from server.solver.beat_adapter.request import SourceBasis
from server.solver.beat_adapter.results import ResultContractError
from server.tests.beat_adapter.conftest import EventStream, wire

FRAME = {"origin": [0, 0, 0], "axis": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0]}


@pytest.fixture
def mesh(make_mesh):
    return make_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], [2, 3, 4, 5])


def driver(identifier="driver", tags=(2, 3), **changes):
    return replace(t.ExteriorDriver(identifier, tags, [0, 0, -8], 6, .0005, 7, .02, .001, 1.5), **changes)


@pytest.fixture
def built(mesh, layout):
    return t.build_transducer_request(mesh, drivers=[driver(), driver("shorted", (4, 5))],
                                      layout=layout, frame=FRAME, frequencies_hz=[500, 100],
                                      excitation_port_ids=["voltage:driver"])


def raw(built, frequency=500):
    ports, drivers, ids = built.wire["excitation_port_ids"], built.transducer_ids, built.matrix_ids
    kinds = {c["id"]: c["kind"] for c in built.wire["compiled_system"]["components"]}
    count, size = len(ports), len(drivers)
    metadata = {"component_ids": list(ids), "kinds": [kinds[i] for i in ids],
                "row_weights": [1] * len(ids), "surface_completion_factors": [1] * len(ids),
                "physical_driver_orbit_counts": [1] * len(ids), "definition": t.MATRIX_DEFINITION,
                "phasor_convention": built.wire["solver_options"]["phasor_convention"],
                "effective_volume_area_definition": t.AREA_DEFINITION,
                "effective_volume_area_m2": [.02, -.04][:len(ids)],
                "effective_volume_area_cancellation_ratio": [1.] * len(ids),
                "effective_volume_area_zero_or_near_cancelling": [False] * len(ids),
                "effective_volume_area_cancellation_relative_tolerance": .01,
                "reciprocity_max_rel": .012, "passivity_min_eig": -.003}
    quantities = []
    for output in built.wire["outputs"]:
        kind = output["quantity"]
        m = {}
        if kind == "exterior_pressure":
            values = np.full((count, len(output["options"]["points_m"])), 2 + 3j)
            unit, axes = "Pa", ["excitation", "observation"]
        elif kind == "radiation_impedance_matrix":
            values = np.eye(len(ids)) * (7 - 8j)
            if len(ids) == 2:
                values[0, 1], values[1, 0] = 2 - 1j, 2.01 - 1j
            unit, axes, m = "N*s/m", ["receiver_component", "source_component"], metadata
        else:
            values = np.full((count, size), 1 - 2j if kind == "diaphragm_velocity" else .4 + .2j)
            unit = "m/s" if kind == "diaphragm_velocity" else "A"
            axes = ["excitation", "transducer"]
            m = {"component_ids": list(drivers), "surface_completion_factors": [1] * size,
                 "physical_driver_orbit_counts": [1] * size}
        quantities.append(dict(id=output["id"], quantity=kind, unit=unit, axes=axes,
                               target_id=None, metadata=m, values=wire(values)))
    return dict(schema_version=2, freq_hz=frequency, excitation_port_ids=ports.copy(),
                quantities=quantities,
                diagnostics={k: built.wire["solver_options"][k] for k in
                             ("precision", "bem_backend", "symmetry", "phasor_convention")})


def test_builder_is_opt_in_schema_valid_and_preserves_closed_front_rear_tags(built, official_contract):
    official_contract(built)
    with pytest.raises(UnsupportedPhysics, match="contract_version"):
        validate_exterior_physics(built.wire)
    system = built.wire["compiled_system"]
    assert system["contract_version"] == 3
    assert system["components"][0]["boundary_ids"] == ["boundary:tag:2", "boundary:tag:3"]
    params = system["components"][0]["parameters"]
    assert params["motion_axis"] == [0, 0, -1]
    assert params["mmd_kg"] == .02
    assert "boundary_motion_signs" not in params
    assert system["metadata"]["undriven_transducer_termination"] == "shorted"
    assert built.transducer_ids == ("transducer:driver", "transducer:shorted")
    assert built.wire["excitation_port_ids"] == ["voltage:driver"]
    assert built.wire["solver_options"]["transducer_reference_voltage_v"] == 2.83


def test_mixed_ports_reversed_order_keep_typed_independent_axes(mesh, layout, official_contract):
    built = t.build_transducer_request(
        mesh, drivers=[driver(tags=(2, 3))], ideal_sources=[SourceBasis("throat", 4, port_id="ideal")],
        excitation_port_ids=["voltage:driver", "ideal"], layout=layout, frame=FRAME, frequencies_hz=[500])
    official_contract(built)
    row = t.parse_transducer_frequency(raw(built), built, 500)
    assert row.excitation_port_ids == ("voltage:driver", "ideal")
    assert row.transducer_ids == ("transducer:driver",)
    assert row.radiation.component_ids == ("component:ideal", "transducer:driver")
    assert row.current_rms_a.shape == (2, 1)
    assert row.pressure_rms_pa["horizontal"].shape[0] == 2
    with pytest.raises(ValueError, match="ideal velocity"):
        row.input_impedance_ohm("ideal")


@pytest.mark.parametrize("name,value", [("mmd_kg", 0), ("mmd_kg", np.nan), ("re_ohm", True),
                                        ("le_h", -1), ("cms_m_per_n", np.inf)])
def test_invalid_si_driver_inputs(mesh, layout, name, value):
    with pytest.raises(UnsupportedPhysics):
        t.build_transducer_request(mesh, drivers=[driver(**{name: value})], layout=layout,
                                   frame=FRAME, frequencies_hz=[500])


@pytest.mark.parametrize("change", ["extra_parameter", "backend", "symmetry", "wall", "bulk", "port", "overlap"])
def test_opt_in_guard_refuses_unadopted_models_without_mutation(built, change):
    wire = built.wire
    if change == "extra_parameter":
        wire["compiled_system"]["components"][0]["parameters"]["mms_kg"] = .025
    elif change == "backend":
        wire["solver_options"]["bem_backend"] = "metal"
    elif change == "symmetry":
        wire["solver_options"]["symmetry"] = "x"
    elif change == "wall":
        wire["compiled_system"]["boundaries"][0]["parameters"]["impedance"] = 100
    elif change == "bulk":
        wire["compiled_system"]["regions"][0]["loss_model"] = {"factor": .1}
    elif change == "port":
        wire["compiled_system"]["excitation_ports"][0]["kind"] = "normal_velocity"
    else:
        wire["compiled_system"]["components"][1]["boundary_ids"] = ["boundary:tag:2"]
    before = deepcopy(wire)
    with pytest.raises(UnsupportedPhysics):
        t.validate_transducer_request(built)
    assert wire == before


def test_rms_physics_and_no_acceleration_or_second_driver_scaling(built):
    row = t.parse_transducer_frequency(raw(built), built, 500)
    np.testing.assert_array_equal(row.pressure_rms_pa["horizontal"], 2 + 3j)
    np.testing.assert_array_equal(row.velocity_rms_m_per_s, 1 - 2j)
    assert row.input_impedance_ohm("voltage:driver") == pytest.approx(2.83 / (.4 + .2j))
    np.testing.assert_allclose(row.displacement_rms_m, (1 - 2j) / (-1j * 2 * np.pi * 500))
    np.testing.assert_allclose(row.excursion_peak_mm, np.sqrt(2) * 1000 * abs((1 - 2j) / (-1j * 2 * np.pi * 500)))
    np.testing.assert_allclose(row.spl_db("horizontal"), 20 * np.log10(abs(2 + 3j) / 20e-6))
    assert row.radiation.metadata["passivity_min_eig"] == -.003  # retain, never repair
    assert row.radiation.values[0, 1] != row.radiation.values[1, 0]
    areas = np.array([.02, -.04])
    np.testing.assert_allclose(row.radiation.acoustic_impedance(), row.radiation.values / np.outer(areas, areas))


def test_zero_current_is_undefined_and_dipole_conversion_refuses(built):
    data = raw(built)
    data["quantities"][-2]["values"] = wire([[0, .2]])
    m = data["quantities"][-1]["metadata"]
    m.update(effective_volume_area_m2=[0, -.04], effective_volume_area_cancellation_ratio=[0, 1],
             effective_volume_area_zero_or_near_cancelling=[True, False])
    row = t.parse_transducer_frequency(data, built, 500)
    assert row.input_impedance_ohm("voltage:driver") is None
    with pytest.raises(ResultContractError, match="cancelling"):
        row.radiation.acoustic_impedance()


@pytest.mark.parametrize("defect", ["phasor", "frequency", "ports", "order", "units", "axes", "dtype", "shape", "driver_ids", "matrix_ids", "weights", "cancellation", "nan_diagnostic"])
def test_changed_contracts_raise(built, defect):
    data = raw(built)
    if defect == "phasor":
        data["diagnostics"]["phasor_convention"] = "exp(+i omega t)"
    elif defect == "frequency":
        data["freq_hz"] = 100
    elif defect == "ports":
        data["excitation_port_ids"] = ["voltage:shorted"]
    elif defect == "order":
        data["quantities"].reverse()
    elif defect in {"units", "axes"}:
        data["quantities"][0]["unit" if defect == "units" else "axes"] = "wrong"
    elif defect in {"dtype", "shape"}:
        data["quantities"][-2]["values"][defect] = "complex64" if defect == "dtype" else [2, 1]
    elif defect == "driver_ids":
        data["quantities"][-2]["metadata"]["component_ids"].reverse()
    elif defect == "matrix_ids":
        data["quantities"][-1]["metadata"]["component_ids"].reverse()
    elif defect == "weights":
        data["quantities"][-1]["metadata"]["row_weights"] = [1, 2]
    elif defect == "cancellation":
        data["quantities"][-1]["metadata"]["effective_volume_area_zero_or_near_cancelling"] = [True, False]
    else:
        data["quantities"][-1]["metadata"]["reciprocity_max_rel"] = np.nan
    with pytest.raises(ResultContractError):
        t.parse_transducer_frequency(data, built, 500)


def test_completed_and_cancelled_prefixes_are_explicit_and_stream_closes(built):
    events = [{"type": "result", "result": raw(built, f)} for f in [500, 100]]
    stream = EventStream(events + [{"type": "completed", "solved_count": 2}])
    result = t.map_transducer_sweep(stream, built)
    np.testing.assert_array_equal(result.frequencies_hz, [500, 100])
    assert not result.cancelled and stream.closed == 1
    result = t.map_transducer_sweep(EventStream(events[:1] + [{"type": "cancelled", "solved_count": 1}]), built)
    assert result.cancelled and result.requested_frequency_count == 2 and len(result.rows) == 1


@pytest.mark.parametrize("terminal", [None, {"type": "completed", "solved_count": 1},
                                      {"type": "cancelled", "solved_count": True},
                                      {"type": "failed", "error": "network failed"}])
def test_truncation_and_failure_never_become_success(built, terminal):
    stream = EventStream([{"type": "result", "result": raw(built)}] + ([terminal] if terminal else []))
    with pytest.raises(ResultContractError):
        t.map_transducer_sweep(stream, built)
    assert stream.closed == 1


def test_real_capability_negotiation_requires_v3_matrix_and_all_undriven_kinds(built):
    from beat_engine.beat_contract import worker
    info = json.loads(importlib.resources.files("beat_engine.beat_contract").joinpath("worker-v1.json").read_text())
    info["backends"] = {"cpu": {"available": True}}
    worker.negotiate_submission(info, built.wire, "solve")
    for field in ["exterior_component_kinds", "optional_output_quantities", "contracts"]:
        candidate = deepcopy(info)
        if field == "contracts":
            candidate[field]["compiled_system"] = [1, 2]
        else:
            candidate.pop(field)
        with pytest.raises(worker.WorkerCompatibilityError):
            worker.negotiate_submission(candidate, built.wire, "solve")


@pytest.mark.parametrize("other_kind", ["electrodynamic_transducer", "ideal_velocity_source"])
def test_physical_group_alias_cannot_escape_component_ownership(built, other_kind):
    system = built.wire["compiled_system"]
    alias = deepcopy(system["boundaries"][0])
    alias["id"] = "alias:same-faces"
    system["boundaries"].append(alias)
    other = system["components"][1]
    other["boundary_ids"] = [alias["id"]]
    other["kind"] = other_kind
    if other_kind == "ideal_velocity_source":
        other["parameters"] = {}
        system["excitation_ports"][1]["kind"] = "normal_velocity"
    with pytest.raises(UnsupportedPhysics, match="physical group ownership"):
        t.validate_transducer_request(built)


def test_malformed_event_is_contract_error_and_closes(built):
    stream = EventStream([None])
    with pytest.raises(ResultContractError, match="Invalid event"):
        t.map_transducer_sweep(stream, built)
    assert stream.closed == 1


def test_installed_qualification_refuses_changed_dispatched_numerical_bytes(tmp_path):
    from scripts.beat_conformance.reference_exterior_transducer import validate_installed_provenance
    import hashlib
    required = ("julia_local/BeatEngineCompiledDriver.jl", "julia_local/exterior_lumped_network.jl",
                "julia_local/src/BeatEngineCoupled.jl")
    hashes = {}
    for name in required:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = {"engine": {"source_files_sha256": hashes}}
    validate_installed_provenance(provenance, tmp_path)
    hashes[required[0]] = "0" * 64
    with pytest.raises(ValueError, match="differs from verified pin"):
        validate_installed_provenance(provenance, tmp_path)
    hashes.pop(required[0])
    with pytest.raises(ValueError, match="omitted required"):
        validate_installed_provenance(provenance, tmp_path)
