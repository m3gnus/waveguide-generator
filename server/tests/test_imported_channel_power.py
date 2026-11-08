"""Physical acoustic watts of RMS-driven imported channels (WGNUM-1).

Independent derivation, a pulsating sphere of radius ``a`` with uniform normal
velocity. Under exp(-iwt) with outgoing exp(+ikr) the pressure is

    p(r) = rho*c * v * (a/r) * (ika / (1 + ika)) * exp(ik(r - a)),

so the surface pressure is ``p(a) = rho*c*v*ika/(1+ika)``. The time-average
radiated power of a harmonic field is the mean of p(t)*v(t). For a phasor
whose magnitude is the RMS value that mean is ``Re(p*conj(v))``; for a phasor
whose magnitude is the peak value it is ``0.5*Re(p*conj(v))``. With an RMS
surface velocity ``U`` this gives

    W = rho*c * 4*pi*a^2 * U^2 * (ka)^2 / (1 + (ka)^2),

and, since the field is omnidirectional, ``W = 4*pi*r^2 * |p_rms(r)|^2 / (rho*c)``
at any radius. The native solvers integrate power from peak phasors
(``0.5*Re(p*conj(v))`` over the driven faces, ``|p|^2/(2*rho*c)`` over the
sphere). WG's LEM is driven by an RMS generator voltage, its volume velocity is
RMS, and WG reports SPL as ``20*log10(|p|/20 uPa)``, i.e. it treats the scaled
fields as RMS phasors. The watts reported with those fields must therefore be
the RMS formula above, twice the native peak-phasor number.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from hornlab_metal_bem.bie import integrate_driven_surface_power
from hornlab_metal_bem.observation import integrate_sphere_radiated_power
from server.jobs.models import SolveRequest
from server.solver.driver_lem import channel_drive_scaling
from server.solver.imported_channels import apply_channel_driver, normalize_prescribed_result

RHO = 1.205
C = 343.0
RADIUS_M = 0.05
OBSERVATION_M = 2.0
FREQUENCIES = np.asarray([200.0, 1000.0, 4000.0])
TAG = 103
SPEC = {
    "sd_cm2": 210.0, "bl_t_m": 10.5, "re_ohm": 5.3, "le_mh": 0.5,
    "mmd_g": 12.0, "cms_m_per_n": 4.0e-4, "rms_kg_per_s": 1.2,
}


def _area() -> float:
    return 4.0 * np.pi * RADIUS_M ** 2


def _analytic_rms_power(u_rms: np.ndarray) -> np.ndarray:
    ka = 2.0 * np.pi * FREQUENCIES / C * RADIUS_M
    return RHO * C * _area() * np.abs(u_rms) ** 2 * ka ** 2 / (1.0 + ka ** 2)


def _native_unit_acceleration_sphere() -> SimpleNamespace:
    """What a native solver returns for the sphere driven at unit acceleration."""

    omega = 2.0 * np.pi * FREQUENCIES
    k = omega / C
    ka = k * RADIUS_M
    velocity = 1.0 / (-1j * omega)  # unit acceleration under exp(-iwt)
    surface = RHO * C * velocity * (1j * ka / (1.0 + 1j * ka))
    far = surface * (RADIUS_M / OBSERVATION_M) * np.exp(1j * k * (OBSERVATION_M - RADIUS_M))
    weights = np.full(4, np.pi)  # an omnidirectional field: any 4*pi split
    sphere = np.repeat(far[:, None], weights.size, axis=1)
    return SimpleNamespace(
        frequencies_hz=FREQUENCIES.copy(),
        pressure_complex=far[:, None, None].copy(),
        sphere_pressure_complex=sphere,
        impedance=surface.copy(),
        surface_pressure_avg={TAG: surface.copy()},
        radiated_power_surface_w=integrate_driven_surface_power(
            surface[:, None], velocity[:, None], np.asarray([_area()])),
        radiated_power_sphere_w=integrate_sphere_radiated_power(
            sphere, distance_m=OBSERVATION_M, solid_angle_weights_sr=weights,
            air_density=RHO, speed_of_sound=C),
    )


def _request(normalization: str | None) -> SolveRequest:
    geometry = {
        "type": "imported", "ingest_id": "wgi_" + "0" * 26,
        "manifest_sha256": "sha256:" + "1" * 64, "artifact_sha256": "sha256:" + "2" * 64,
        "drive_channels": [{"id": "driver", "source_ids": ["source-c"], "driver": SPEC}],
    }
    if normalization is not None:
        geometry["excitation_normalization"] = normalization
    return SolveRequest.model_validate(
        {"geometry": geometry, "options": {"frequencies_hz": FREQUENCIES.tolist()}})


def _record() -> dict:
    return {"sources": [{"id": "source-c", "observed": {"total_area_mm2": _area() * 1e6}}]}


def _assert_rms_watts(result: SimpleNamespace, u_rms: np.ndarray) -> None:
    expected = _analytic_rms_power(u_rms)
    from_spl = 4.0 * np.pi * OBSERVATION_M ** 2 * np.abs(result.pressure_complex[:, 0, 0]) ** 2 / (RHO * C)
    np.testing.assert_allclose(from_spl, expected, rtol=1e-9)
    np.testing.assert_allclose(result.radiated_power_surface_w, expected, rtol=1e-9)
    np.testing.assert_allclose(result.radiated_power_sphere_w, expected, rtol=1e-9)


def test_native_sphere_fixture_is_peak_phasor_power() -> None:
    """Guard the fixture: the native integrators give half the RMS formula."""

    native = _native_unit_acceleration_sphere()
    u_unit = 1.0 / (2.0 * np.pi * FREQUENCIES)
    np.testing.assert_allclose(native.radiated_power_surface_w, 0.5 * _analytic_rms_power(u_unit), rtol=1e-12)
    np.testing.assert_allclose(native.radiated_power_sphere_w, 0.5 * _analytic_rms_power(u_unit), rtol=1e-12)


@pytest.mark.parametrize("normalization", ["acceleration_legacy", "rms_velocity"])
def test_voltage_driven_channel_reports_rms_watts(normalization: str) -> None:
    """2.83 V RMS into the driver: watts match the analytic RMS sphere power."""

    request = _request(normalization)
    native = _native_unit_acceleration_sphere()
    # The LEM's own RMS cone velocity, computed before any field is touched.
    scale, _payload = channel_drive_scaling(
        FREQUENCIES, native.surface_pressure_avg[TAG], _area(),
        request.geometry.drive_channels[0].driver, drive_voltage_v=2.83, rg_ohm=0.0)
    u_rms = np.abs(scale) / (2.0 * np.pi * FREQUENCIES)

    result = _native_unit_acceleration_sphere()
    normalize_prescribed_result(result, request.geometry)
    apply_channel_driver(request.geometry.drive_channels[0], result, _record(), {"source-c": TAG},
                         drive_voltage_v=2.83, rg_ohm=0.0)

    _assert_rms_watts(result, u_rms)


def test_rms_velocity_basis_reports_watts_for_one_metre_per_second_rms() -> None:
    """A prescribed 1 m/s RMS channel (design assembly) with no driver."""

    request = _request("rms_velocity")
    result = _native_unit_acceleration_sphere()
    normalize_prescribed_result(result, request.geometry)

    _assert_rms_watts(result, np.ones_like(FREQUENCIES))


def test_legacy_unit_acceleration_basis_keeps_native_watts() -> None:
    """No RMS drive is declared: the unit basis keeps the native numbers."""

    request = _request(None)
    result = _native_unit_acceleration_sphere()
    native = _native_unit_acceleration_sphere()
    normalize_prescribed_result(result, request.geometry)

    np.testing.assert_array_equal(result.radiated_power_surface_w, native.radiated_power_surface_w)
    np.testing.assert_array_equal(result.radiated_power_sphere_w, native.radiated_power_sphere_w)
