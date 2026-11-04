"""Independent circuit oracles, including archived real BEAT v3 CPU bases."""

import io
from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import AssemblyWiringGroup
from server.solver.combine import deserialize_channel_bases, serialize_channel_bases
from server.solver.driver_network import prepare_network_bases, solve_networks

W1 = "00000000-0000-4000-8000-000000000001"
W2 = "00000000-0000-4000-8000-000000000002"
NETWORK = "00000000-0000-4000-8000-000000000010"


def driver(name, **extra):
    return {"kind": "driver", "radiator_id": name, **extra}


def branch(kind, *children):
    return {"kind": kind, "children": list(children)}


def group(tree):
    return AssemblyWiringGroup(id=NETWORK, label="LF", network=tree)


@pytest.mark.parametrize(("kind", "impedance", "volts"), [
    ("series", 10, [2.4, 3.6]), ("parallel", 2.4, [6, 6]),
])
def test_resistive_drivers(kind, impedance, volts):
    solved = solve_networks(100, np.diag([1/4, 1/6]), [W1, W2],
        [group(branch(kind, driver(W1), driver(W2)))], source_voltage={NETWORK: 6})
    np.testing.assert_allclose(solved.voltage, volts, atol=1e-12)
    assert abs(6 / solved.input_current[NETWORK] - impedance) < 1e-9


@pytest.mark.parametrize("frequency", [10, 100, 1000, 20000])
def test_rlc_ladder_and_nested_parallel(frequency):
    omega = 2 * np.pi * frequency
    # R + (C || (L + d1 + d2))
    tree = branch("series", {"kind": "R", "value": 2}, branch("parallel",
        {"kind": "C", "value": 10e-6}, branch("series", {"kind": "L", "value": .003}, driver(W1), driver(W2))))
    solved = solve_networks(frequency, np.diag([1/4, 1/6]), [W1, W2], [group(tree)], source_voltage={NETWORK: 1})
    expected = 2 + 1 / (1j * omega * 10e-6 + 1 / (10 + 1j * omega * .003))
    assert abs(1 / solved.input_current[NETWORK] - expected) < 1e-9
    tree = branch("series", branch("parallel", driver(W1), driver(W2)), {"kind": "L", "value": .003})
    solved = solve_networks(frequency, np.diag([1/4, 1/6]), [W1, W2], [group(tree)], source_voltage={NETWORK: 1})
    assert abs(1 / solved.input_current[NETWORK] - (2.4 + 1j * omega * .003)) < 1e-9


def test_inverted_driver_and_induced_current_obey_port_orientation():
    y = np.array([[.2, .03], [.03, .25]])
    induced = np.array([.01j, .02])
    solved = solve_networks(100, y, [W1, W2],
        [group(branch("series", driver(W1), driver(W2, invert=True)))],
        source_voltage={NETWORK: 2}, induced_current=induced)
    assert abs(solved.voltage[0] - solved.voltage[1] - 2) < 1e-12
    assert abs(solved.current[0] + solved.current[1]) < 1e-12
    np.testing.assert_allclose(solved.current, y @ solved.voltage + induced)


def test_multiple_amplifiers_and_unwired_driver_share_one_coupled_multiport():
    d3, d4, d5 = [f"00000000-0000-4000-8000-{index:012d}" for index in (3, 4, 5)]
    second = "00000000-0000-4000-8000-000000000011"
    series = group(branch("series", driver(W1), driver(W2)))
    parallel = AssemblyWiringGroup(id=second, label="LF 2", network=branch("parallel", driver(d3), driver(d4, invert=True)))
    y = np.diag([.2, .25, .15, .18, .3]).astype(complex) + .01j * np.ones((5, 5))
    induced = np.array([.01, .02j, .03, .04j, .05])
    solved = solve_networks(500, y, [W1, W2, d3, d4, d5], [series, parallel],
        source_voltage={NETWORK: 3 + 1j, second: 2 - 1j}, fixed_voltage={d5: 4}, induced_current=induced)
    np.testing.assert_allclose(solved.voltage[:2].sum(), 3 + 1j, atol=1e-12)
    np.testing.assert_allclose(solved.current[0], solved.current[1], atol=1e-12)
    np.testing.assert_allclose(solved.voltage[2:], [2 - 1j, -2 + 1j, 4], atol=1e-12)
    np.testing.assert_allclose(solved.input_current[NETWORK], solved.current[0], atol=1e-12)
    np.testing.assert_allclose(solved.input_current[second], solved.current[2] - solved.current[3], atol=1e-12)
    np.testing.assert_allclose(solved.current, y @ solved.voltage + induced, atol=1e-12)


def basis(frequencies, pressure):
    return SimpleNamespace(frequencies_hz=np.asarray(frequencies), pressure_complex=np.asarray(pressure)[:, None, :],
        observation_angles_deg=np.array([0., 30., 60.]), observation_planes=["horizontal"], sphere_pressure_complex=None)


def test_native_payload_versioning_and_legacy_bytes():
    results = {W1: basis([100, 500], [[1, 2, 3], [2, 3, 4]])}
    legacy = serialize_channel_bases(results)
    with np.load(io.BytesIO(legacy)) as archive:
        assert int(archive["version"]) == 1
    assert not hasattr(deserialize_channel_bases(legacy)["results_by_id"][W1], "native_current")
    results[W1].native_current = np.array([[.1j], [.2j]])
    results[W1].native_velocity = np.array([[1j], [2j]])
    results[W1].native_driver_ids = [W1]
    results[W1].native_reference_voltage = 2.83
    packed = serialize_channel_bases(results)
    with np.load(io.BytesIO(packed)) as archive:
        assert int(archive["version"]) == 2
    restored = deserialize_channel_bases(packed)["results_by_id"][W1]
    np.testing.assert_array_equal(restored.native_current, results[W1].native_current)
    np.testing.assert_array_equal(restored.native_velocity, results[W1].native_velocity)
    del results[W1].native_current
    assert serialize_channel_bases(results) == legacy


@pytest.mark.parametrize(("kind", "value"), [("L", .01), ("C", .0001)])
def test_passive_native_basis_transformation_conjugates_phase_once(kind, value):
    result = basis([100, 500], [[1+2j, 2, 3], [2+1j, 3, 4]])
    result.native_driver_ids, result.native_reference_voltage = [W1], 2.83
    result.native_current = np.full((2, 1), 2.83/6, complex)
    result.native_velocity = result.native_current * .5
    geometry = SimpleNamespace(wiring_groups=[group(branch("series", driver(W1), {"kind": kind, "value": value}))], drive_voltage_v=5)
    prepared = prepare_network_bases({W1: result}, geometry)
    omega = 2*np.pi*result.frequencies_hz
    impedance = 1j*omega*value if kind == "L" else 1/(1j*omega*value)
    expected_current = 5/(6+impedance)
    coefficients = np.conj(6*expected_current/2.83)
    np.testing.assert_allclose(prepared.results[W1].pressure_complex,
                               result.pressure_complex*coefficients[:, None, None], atol=1e-12)
    diagnostics = prepared.diagnostics({W1: np.ones(2)})["drivers"][W1]["current_rms_a"]
    np.testing.assert_allclose(np.asarray(diagnostics["real"])+1j*np.asarray(diagnostics["imaginary"]), expected_current, atol=1e-12)


@pytest.mark.parametrize("kind", ["series", "parallel"])
def test_real_tiny_beat_cpu_bases_against_independent_multiport_equations(kind):
    """The two-frequency 1g proof is real CPU output, not synthetic acoustics.

    The frozen fixture contains currents/velocity and pressure at three angles
    from three tetrahedra, including a prescribed HF source. No engine starts.
    """
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "wiring-cpu-bases.npz"
    with np.load(fixture, allow_pickle=False) as archive:
        frequencies, currents, velocities, pressures = (archive[key] for key in ("frequencies", "current", "velocity", "pressure"))
    names = [W1, W2, "hf"]
    results = {}
    for i, name in enumerate(names):
        result = basis(frequencies, pressures[:, i])
        result.native_current, result.native_velocity = currents[:, i], velocities[:, i]
        result.native_driver_ids, result.native_reference_voltage = [W1, W2], 2.83
        results[name] = result
    geometry = SimpleNamespace(wiring_groups=[group(branch(kind, driver(W1), driver(W2)))], drive_voltage_v=2.83)
    # Exercise the durable artifact as well as the in-memory preparation.
    prepared = prepare_network_bases(deserialize_channel_bases(serialize_channel_bases(results))["results_by_id"], geometry)
    assert np.max(abs(currents[:, 0, 1])) > 1e-6  # mutual loading is measurable
    for f in range(len(frequencies)):
        y = np.conj(currents[f, :2].T) / 2.83
        if kind == "series":
            z = np.linalg.inv(y)
            amplifier_current = 2.83 / z.sum()
            voltage = z @ np.full(2, amplifier_current)
            expected_z = z.sum()
        else:
            voltage = np.full(2, 2.83)
            expected_z = 1 / y.sum()
        coefficients = np.conj(voltage / 2.83)
        expected_pressure = coefficients @ pressures[f, :2]
        np.testing.assert_allclose(prepared.results[W1].pressure_complex[f, 0], expected_pressure, rtol=1e-12, atol=1e-14)
        np.testing.assert_allclose(20*np.log10(abs(prepared.results[W1].pressure_complex[f, 0])/20e-6),
                                   20*np.log10(abs(expected_pressure)/20e-6), atol=1e-10)
        diagnostic = prepared.diagnostics({W1: np.ones(len(frequencies))})["networks"][NETWORK]["input_impedance_ohm"]
        actual_z = diagnostic["real"][f] + 1j*diagnostic["imaginary"][f]
        assert abs(actual_z - expected_z) < 1e-9
        uncoupled_z = sum(1/y[i, i] for i in range(2)) if kind == "series" else 1/np.trace(y)
        assert abs(actual_z - uncoupled_z) > 1e-4
        # HF acts with both amplifier terminals shorted; in series its induced
        # currents redistribute the woofer terminal voltages instead of staying shorted.
        hf_voltage = np.conj(prepared.voltage[f, 2])
        hf_current = y @ hf_voltage + np.conj(currents[f, 2])
        if kind == "series":
            assert abs(hf_voltage.sum()) < 1e-12
            assert abs(hf_current[0] - hf_current[1]) < 1e-12
            assert np.max(abs(hf_voltage)) > 1e-5
        else:
            np.testing.assert_allclose(hf_voltage, 0, atol=1e-12)
