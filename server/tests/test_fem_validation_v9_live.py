"""V9 port outputs through the F11 live pipeline and the managed coupled bridge.

Opt in through the compute broker exactly like F11: WG2_RUN_LIVE=1,
WG2_FEM_REGIONS=1, WG2_BEAT_PROVIDER=official and a provisioned
WG2_BEAT_RUNTIME_DIR. WG2_FEM_VALIDATION_DIR optionally receives JSON evidence.

Conventions (docs/plans/CADLINK-FEM-REGIONS.md, ``fem_ports``): port velocity is
the area-weighted normal velocity, positive OUT of the bounded FEM region, in
the engineering exp(+j omega t) convention; an ideal FEM drive is a unit normal
acceleration, also positive out of the air. Volume velocities below are
converted to the solver exp(-i omega t) convention before comparison.
"""
from __future__ import annotations

import base64
import os
from types import SimpleNamespace

import numpy as np
import pytest

from server.contracts.conventions import engineering_to_solver
from server.solver.beat_adapter.results import acceleration_scale
from server.tests.fem_validation import (
    C, CHAMBER_VOLUME_M3, PORT_LENGTH_M, PORT_RADIUS_M, comparison_evidence, helmholtz_frequency,
    write_evidence,
)
from server.tests import test_fem_validation_pipeline_live as f11
from server.tests.test_fem_coupled_beat_live import worker as worker_fixture
from server.tests.test_fem_electrodynamic import driver

_pytest_config = getattr(pytest.mark, "_config", None)
if _pytest_config is not None:
    _pytest_config.addinivalue_line("markers", "live: real native solver qualification")

pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.real_runtime,
              pytest.mark.skipif(os.environ.get("WG2_RUN_LIVE") != "1",
                                 reason="set WG2_RUN_LIVE=1; run V9 through the compute broker")]

managed_cpu = f11.managed_cpu  # F11's managed CPU child fixture, reused unchanged.
worker = worker_fixture
CONSERVATION_TOLERANCE = .02
LINEARITY_TOLERANCE = .02  # Task gate; plan §2.3 asks 1e-6, recorded in evidence.
POWER_TOLERANCE = .02  # Task gate; plan §2.3 asks 1e-9, recorded in evidence.
PLAN_LINEARITY_TOLERANCE, PLAN_POWER_TOLERANCE = 1e-6, 1e-9
SD_M2 = .040 ** 2  # F11 ported box: the whole 40 x 40 mm chamber floor is the diaphragm.
TUNING_HZ = helmholtz_frequency(C, np.pi * PORT_RADIUS_M**2, CHAMBER_VOLUME_M3,
                                PORT_LENGTH_M, .85 * PORT_RADIUS_M, .61 * PORT_RADIUS_M)
# Lumped port/diaphragm flow ratio is 1/(1 - (f/fH)^2): 1.0036 and 1.0082 here,
# so chamber compliance alone uses under half of the 2% conservation budget.
BELOW_TUNING = (TUNING_HZ * np.array([.06, .09])).tolist()


def phasors(values):
    """Decode a packed complex128 block or a JSON list of {real, imaginary}."""
    if isinstance(values, dict) and "content_base64" in values:
        return np.frombuffer(base64.b64decode(values["content_base64"]), dtype="<c16").reshape(values["shape"])
    array = np.asarray(values, dtype=object)
    return np.array([complex(z["real"], z["imaginary"]) for z in array.flat]).reshape(array.shape)


def surface_area(field, region_index, group_id):
    """Area [m²] of the retained sidecar triangles of one tagged FEM group."""
    names = field[f"surface_names_{region_index}"].tolist()
    index, = [i for i, name in enumerate(names) if f"|group={group_id}|" in name]
    vertices = field[f"points_mm_{region_index}"][field[f"surface_triangles_{region_index}_{index}"]] * .001
    return float(np.linalg.norm(np.cross(vertices[:, 1] - vertices[:, 0],
                                         vertices[:, 2] - vertices[:, 0]), axis=1).sum() / 2)


def port_volume_velocity(ports):
    """Solver-convention port volume velocity [m³/s], positive out of the FEM air."""
    interface, = ports["interfaces"]
    assert interface["symmetry_multiplier"] == 1
    velocity = engineering_to_solver(phasors(interface["average_normal_velocity"]))
    return velocity * interface["full_model_area_m2"]


def linearity(ports):
    """Max over planes of |interface + other exterior - total| / max|total| per frequency."""
    errors, other_present = {}, "other_exterior_radiated_pressure" in ports
    for plane, packed in ports["total_radiated_pressure"].items():
        total = phasors(packed)
        parts = sum(phasors(i["radiated_pressure"][plane]) for i in ports["interfaces"])
        if other_present:
            parts = parts + phasors(ports["other_exterior_radiated_pressure"][plane])
        assert np.isfinite(total).all() and np.all(np.abs(total) > 0)
        errors[plane] = (np.max(np.abs(parts - total), axis=-1) / np.max(np.abs(total), axis=-1)).tolist()
    worst = max(max(values) for values in errors.values())
    return worst, {"per_plane_per_frequency": errors, "max_relative_error": worst,
                   "normalization": "per-frequency max |total| over the plane's observations",
                   "tolerance": LINEARITY_TOLERANCE, "plan_tolerance": PLAN_LINEARITY_TOLERANCE,
                   "other_exterior_column_present": other_present,
                   "passed": bool(worst <= LINEARITY_TOLERANCE)}


def test_v9_ported_ideal_drive_port_flow_conservation_and_linearity(managed_cpu, tmp_path):
    frequencies = BELOW_TUNING
    record, envelope, field, details = f11._pipeline(tmp_path, "ported", frequencies)
    ports = envelope["channels"]["drive-lf"]["metadata"]["fem_ports"]
    assert ports["drive_normalization"] == "unit_acceleration"
    assert ports["normal_orientation"] == "bounded_region_outward"
    port = port_volume_velocity(ports)
    area = surface_area(field, 0, "cone")
    # Unit outward acceleration -> outward velocity acceleration_scale(f) (solver).
    diaphragm = area * np.array([acceleration_scale(f) for f in frequencies])
    lumped = -diaphragm / (1 - (np.asarray(frequencies) / TUNING_HZ) ** 2)
    worst, linear = linearity(ports)
    # The F11 box radiates only through its port: no exterior moving surface,
    # so the other-exterior column is absent and interface == total.
    assert not linear["other_exterior_column_present"]
    checks = {"linearity_interface_plus_other_equals_total": linear}
    write_evidence("v9-ported-ideal", frequencies, port, -diaphragm, CONSERVATION_TOLERANCE, record,
                   quantity="port_volume_velocity_m3_s_solver_convention_vs_minus_diaphragm",
                   diaphragm_area_m2=area, tuning_hz=TUNING_HZ,
                   informational_lumped_compliance=comparison_evidence(port, lumped, CONSERVATION_TOLERANCE),
                   checks=checks, **details)
    np.testing.assert_allclose(port, -diaphragm, rtol=CONSERVATION_TOLERANCE, atol=0,
                               err_msg="V9: port volume velocity must cancel the diaphragm's below tuning")
    assert worst <= LINEARITY_TOLERANCE, f"V9 linearity: {linear}"


def test_v9_ported_electrodynamic_input_power_and_port_flow(managed_cpu, tmp_path):
    frequencies = BELOW_TUNING
    spec = driver(sd_m2=SD_M2)
    record, envelope, field, details = f11._pipeline(tmp_path, "ported", frequencies, transducer=spec)
    channel = envelope["channels"]["drive-lf"]
    metadata = channel["metadata"]
    ports = metadata["fem_ports"]
    assert ports["drive_normalization"] == "voltage_rms"
    assert metadata["sd_source"] == "declared"
    power = metadata["driver"]["input_power_w"]
    assert power["amplitude_convention"] == "rms"
    current = phasors(power["channel_current_rms_a"])  # engineering convention
    voltage = metadata["drive"]["voltage_v"]
    impedance = np.asarray(channel["impedance"]["real"], dtype=float)
    reported = np.asarray(power["values"], dtype=float)
    recomputed = np.real(voltage * np.conj(current))
    joule = impedance * np.abs(current) ** 2
    # Diaphragm conservation: motion_axis +z drives the floor INTO the air, whose
    # outward normal there is -z, so outward diaphragm flow is -Sd v.
    velocity = phasors(metadata["beat_transducer"]["velocity_rms_m_per_s"])[:, 0]  # solver convention
    area = surface_area(field, 0, "cone")
    port = port_volume_velocity(ports)
    worst, linear = linearity(ports)
    checks = {
        "input_power_nonnegative": {"values_w": reported.tolist(), "passed": bool(np.all(reported >= 0))},
        "input_power_equals_re_zin_i_squared": {
            **comparison_evidence(reported, joule, POWER_TOLERANCE), "plan_tolerance": PLAN_POWER_TOLERANCE},
        "input_power_equals_re_v_conj_i": comparison_evidence(reported, recomputed, POWER_TOLERANCE),
        "port_flow_conservation": comparison_evidence(port, area * velocity, CONSERVATION_TOLERANCE),
        "linearity_interface_plus_other_equals_total": linear,
    }
    write_evidence("v9-ported-electrodynamic", frequencies, reported, joule, POWER_TOLERANCE, record,
                   quantity="input_power_w_vs_re_zin_abs_i_squared", sd_m2=SD_M2, diaphragm_area_m2=area,
                   reference_voltage_rms_v=voltage, tuning_hz=TUNING_HZ, checks=checks, **details)
    assert np.all(np.isfinite(reported)) and np.all(reported >= 0), f"V9: Re(V I*) < 0: {reported}"
    np.testing.assert_allclose(reported, joule, rtol=POWER_TOLERANCE, atol=0,
                               err_msg="V9: Re(V I*) differs from Re(Zin)|I|^2")
    np.testing.assert_allclose(reported, recomputed, rtol=POWER_TOLERANCE, atol=0)
    np.testing.assert_allclose(port, area * velocity, rtol=CONSERVATION_TOLERANCE, atol=0,
                               err_msg="V9: port volume velocity must cancel the voice-coil diaphragm's")
    assert worst <= LINEARITY_TOLERANCE, f"V9 linearity: {linear}"


def test_v9_exterior_side_driver_interface_plus_other_exterior_equals_total(tmp_path, worker):
    """The F11 box has no exterior source; V7's exterior-driven chamber does."""
    from server.solver.official_beat import solve_coupled_compiled
    from server.solver.result_mapping import map_coupled_port_response
    from server.tests.test_component_symmetry import exterior_driven_requests

    request, _ = exterior_driven_requests(tmp_path)  # full model; the half is V7's
    sweep = solve_coupled_compiled(request, worker_manager=SimpleNamespace(get_worker=lambda *a, **k: worker))
    response = {"frequencies": request.wire["frequencies_hz"], "metadata": {}}
    map_coupled_port_response(response, sweep, request, "drive-lf")
    ports = response["metadata"]["fem_ports"]
    assert ports["drive_normalization"] == "voltage_rms"
    worst, linear = linearity(ports)
    assert linear["other_exterior_column_present"]
    other = np.concatenate([phasors(p) for p in ports["other_exterior_radiated_pressure"].values()], axis=-1)
    interface = np.concatenate([phasors(p) for p in ports["interfaces"][0]["radiated_pressure"].values()], axis=-1)
    # Both columns must carry real radiation, or the identity is trivial.
    contribution = {"other_exterior_max_pa": np.abs(other).max(axis=-1).tolist(),
                    "interface_max_pa": np.abs(interface).max(axis=-1).tolist()}
    record = {"fem_regions": [{"region_id": request.regions[0].record["region_id"],
                               "stats": {"nodes": len(request.regions[0].domain.points_mm),
                                         "tetrahedra": len(request.regions[0].domain.tetrahedra)}}]}
    total = np.concatenate([phasors(p) for p in ports["total_radiated_pressure"].values()], axis=-1)
    # Port and driver radiation can cancel at some angles, so the headline is
    # normalized like the gate: max|total| + max|residual| against max|total|.
    scale = np.abs(total).max(axis=-1)
    residual = np.abs(interface + other - total).max(axis=-1)
    write_evidence("v9-exterior-side-linearity", request.wire["frequencies_hz"], scale + residual, scale,
                   LINEARITY_TOLERANCE, record, quantity="max_abs_total_plus_max_residual_pa_per_frequency",
                   contribution=contribution, checks={"linearity_interface_plus_other_equals_total": linear})
    assert np.all(np.abs(other) > 0) and np.all(np.abs(interface) > 0), contribution
    assert worst <= LINEARITY_TOLERANCE, f"V9 linearity: {linear}"
