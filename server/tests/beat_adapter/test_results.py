"""Phase, area/copy normalization, SPL and trace semantics against HBB."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from server.solver.beat_adapter.results import (
    ResultContractError, acceleration_scale, decode_complex_values,
    mean_pressure_from_force, parse_frequency,
)
from .conftest import EventStream, wire


CONTROLS = json.loads((Path(__file__).parent / "fixtures/hbb_controls.json").read_text())


def parse(raw, layout, **kwargs):
    arguments = {"frequency_hz": 500.0, "layout": layout, "source_area_m2": 0.004,
                 "excitation_port_id": "source", "precision": "float64", "trace_counts": (4, 2)}
    arguments.update(kwargs)
    return parse_frequency(raw, **arguments)


@pytest.mark.parametrize("symmetry,copies", [("off", 1), ("x", 2), ("xy", 4), ("ground", 1)])
def test_force_impedance_round_trip_and_symmetry_matches_hbb(layout, raw_result, symmetry, copies):
    mean_pressure = 3.5 - 1.25j
    reduced_area = 0.004
    force = mean_pressure * reduced_area * copies
    raw = raw_result(symmetry=symmetry)
    raw["quantities"][4]["values"] = wire([force])
    row = parse(raw, layout, symmetry=symmetry)
    omega = 2 * np.pi * 500
    recovered_mean = -1j * omega * row.impedance
    assert recovered_mean == pytest.approx(mean_pressure)
    assert recovered_mean * reduced_area * copies == pytest.approx(force)
    assert row.radiation_impedance == pytest.approx(force)
    # Independently invert HBB's legacy display packing; official has neither
    # the factor ten nor the negated/halved imaginary wire component.
    legacy_force = CONTROLS["impedance_force_factor"] * force
    pair = [legacy_force.real / 2, -legacy_force.imag / 2]
    hbb_mean = (2 * pair[0] - 2j * pair[1]) / (CONTROLS["impedance_force_factor"] * reduced_area * copies)
    assert row.impedance == pytest.approx(hbb_mean / (-1j * omega))
    rho_c = 1.2041 * 343
    assert np.conj(-1j * omega * row.impedance) / rho_c == pytest.approx(np.conj(mean_pressure) / rho_c)


@pytest.mark.parametrize("precision", ["float32", "float64"])
def test_pressure_sphere_and_traces_share_signed_acceleration_and_order(layout, raw_result, precision):
    row = parse(raw_result(precision=precision), layout, precision=precision)
    scale = 1 / (-1j * 2 * np.pi * 500)
    np.testing.assert_allclose(row.pressure_complex, np.tile([1 + 2j, 2 + 2j, 3 + 2j], (3, 1)) * scale)
    np.testing.assert_allclose(row.sphere_pressure_complex, (np.arange(12) + 1 + 2j) * scale)
    np.testing.assert_allclose(row.surface_pressure_complex, np.array([9 + 2j, 1 - 3j, 7 + 4j, 2 + 5j]) * scale)
    np.testing.assert_allclose(row.surface_neumann_complex, np.array([11 - 2j, 3 + 7j]) * scale)
    # This control would fail for conjugation, a reversed time derivative,
    # sorting the traces, or confusing P1 nodes with DP0 faces.
    assert not np.allclose(row.pressure_complex, np.conj(row.pressure_complex))
    np.testing.assert_allclose(row.spl_db, 20 * np.log10(np.abs(row.pressure_complex) / 20e-6))


def test_axial_generalized_force_requires_w4_boundary_derived_mean(layout, raw_result):
    with pytest.raises(ResultContractError, match="W4.*boundary-derived"):
        parse(raw_result(), layout, source_motion="axial")
    row = parse(raw_result(), layout, source_motion="axial", mean_pressure_velocity=2 - 3j)
    assert row.impedance == pytest.approx((2 - 3j) * acceleration_scale(500))
    assert row.radiation_impedance == 4 - 5j  # retained separately, not divided into the old meaning


def test_optional_sphere_and_surface_traces_remain_none(raw_result):
    from server.solver.beat_adapter.observations import build_observations

    layout = build_observations(angle_range=(-90, 90, 3), planes=("horizontal",), sphere_grid=None)
    raw = raw_result(traces=False)
    raw["quantities"] = [raw["quantities"][2], raw["quantities"][4]]
    row = parse(raw, layout, trace_counts=None)
    assert row.sphere_pressure_complex is None
    assert row.surface_pressure_complex is None and row.surface_neumann_complex is None


def test_absolute_spl_and_per_cut_directivity_match_hbb_with_null_reference(layout, raw_result, run_sweep):
    arrays = [np.array([[0j, 1 + 0j, 0.5 + 0j], [1e-15 + 0j, 0j, 2 + 0j], [0j, 0j, 0j]]),
              np.array([[2 + 3j, 0.25 + 0j, 1 + 0j], [0.5 + 0j, 4 + 0j, 0j], [1 + 0j, 2 + 0j, 4 + 0j]])]
    events = []
    for frequency, pressures in zip([500, 1000], arrays):
        raw = raw_result(frequency)
        for index in range(3):
            raw["quantities"][index]["values"] = wire(pressures[index:index + 1])
        events.append({"type": "result", "result": raw})
    events.append({"type": "completed", "solved_count": 2})
    mapped = run_sweep(EventStream(events))
    np.testing.assert_allclose(mapped.directivity_db, CONTROLS["null_reference_directivity_db"], atol=1e-13, rtol=0)
    np.testing.assert_array_equal(mapped.spl_norm_db, mapped.directivity_db)
    assert mapped.directivity_reference_index == CONTROLS["directivity_reference_index"]
    assert mapped.directivity_reference_deg == CONTROLS["directivity_reference_deg"]
    assert np.isfinite(mapped.directivity_db).all()
    assert np.isneginf(mapped.spl_db[0, 1, 1])
    assert mapped.directivity_db[0, 1, 2] == pytest.approx(150.0570025461)
    assert mapped.spl_db[0, 0, 1] != mapped.directivity_db[0, 0, 1]


@pytest.mark.parametrize("change", [
    lambda r: r.update(schema_version=1), lambda r: r.update(schema_version=True),
    lambda r: r.update(freq_hz=True), lambda r: r.update(freq_hz=float("nan")),
    lambda r: r.update(excitation_port_ids=["other"]),
    lambda r: r.update(excitation_port_ids=["source", "extra"]),
    lambda r: r["quantities"].append(copy.deepcopy(r["quantities"][0])),
    lambda r: r["quantities"].pop(),
    lambda r: r["quantities"].reverse(),
    lambda r: r["quantities"][0].update(id="other"),
    lambda r: r["quantities"][0].update(quantity="bem_boundary_pressure"),
    lambda r: r["quantities"][0].update(unit="dB"),
    lambda r: r["quantities"][0].update(target_id="other"),
    lambda r: r["quantities"][0].update(axes=["observation", "excitation"]),
    lambda r: r["quantities"][0].update(values=None),
    lambda r: r["quantities"][0]["values"].update(dtype="float64"),
    lambda r: r["quantities"][0]["values"].update(order="F"),
    lambda r: r["quantities"][0]["values"].update(byte_order="big"),
    lambda r: r["quantities"][0]["values"].update(shape=[True, 3]),
    lambda r: r["quantities"][0]["values"].update(content_base64="a!"),
    lambda r: r["quantities"][0]["values"].update(content_base64=""),
    lambda r: r["quantities"][0].update(values=wire([[complex(float("nan"), 0), 1j, 0j]])),
    lambda r: r["quantities"][0].update(values=wire([[complex(0, float("inf")), 1j, 0j]])),
    lambda r: r["quantities"][5].update(values=wire([[1, 2, 3]])),
    lambda r: r["quantities"][6].update(axes=["excitation", "bem_node"]),
    lambda r: r["diagnostics"].update(phasor_convention="exp(+i omega t)"),
    lambda r: r["diagnostics"].pop("phasor_convention"),
    lambda r: r["diagnostics"].update(symmetry="xy"),
    lambda r: r["diagnostics"].update(bem_backend="metal"),
    lambda r: r["diagnostics"].update(precision="float32"),
])
def test_result_contract_violations_fail_closed(layout, raw_result, change):
    raw = raw_result()
    change(raw)
    with pytest.raises(ResultContractError):
        parse(raw, layout)


@pytest.mark.parametrize("area", [0, -1, float("inf"), float("nan")])
def test_invalid_source_area_is_refused(area):
    with pytest.raises(ValueError, match="Source area"):
        mean_pressure_from_force(1 + 2j, source_area_m2=area)


def test_decoder_preserves_c_order_for_multiple_excitation_rows():
    values = np.array([[1 + 2j, 3 - 4j], [5 + 6j, 7 - 8j]])
    np.testing.assert_array_equal(decode_complex_values(wire(values), (2, 2)), values)


def test_optional_hbb_private_impedance_factor_crosscheck():
    sweep = pytest.importorskip("hornlab_beat_bem.sweep",
                               reason="Optional HBB impedance cross-check needs hornlab_beat_bem")
    assert sweep._BEAT_IMPEDANCE_FORCE_FACTOR == CONTROLS["impedance_force_factor"]
