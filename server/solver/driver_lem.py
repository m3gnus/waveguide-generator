"""Voltage-driven driver coupling for imported drive channels.

Ports the Fusion add-in's proven seams onto WG's solve path, using the
pinned ``hornlab-sim`` for the lumped physics:

- z_self from the channel's area-weighted surface pressure:
  ``z_self_eng = conj(-jω·⟨p⟩_solver) / S`` (the add-in's
  ``_solver_surface_avg_to_self_impedance``). ``S`` is the source's full
  physical area — symmetry images move with the source, and ⟨p⟩ from a
  symmetry-reduced solve equals the full-domain average.
- Basis scaling by cone **acceleration**, not velocity:
  ``p_V = (jω·U/S)·p_basis`` (using velocity here was the historical
  −6 dB/oct + −90° bug, ``docs/plans/per-driver-lem-coupling.md`` in the
  add-in repo).

Convention boundary: ``hornlab_sim`` speaks engineering ``e^{+jωt}``;
solver fields are ``e^{-iωt}``. Engineering scale factors are applied to
raw fields as complex conjugates — the same single-boundary rule as
``server/solver/combine.py``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from hornlab_sim.methods import bandpass, driver_coupling

from server.jobs.models import DriverSpec

_TWO_PI = 2.0 * np.pi


def hornlab_driver(spec: DriverSpec) -> bandpass.Driver:
    """Convert the Hornresp-unit wire spec into hornlab-sim's SI ``Driver``."""

    return bandpass.Driver(
        Sd=spec.sd_cm2 * 1.0e-4,
        Bl=spec.bl_t_m,
        Re=spec.re_ohm,
        Le=spec.le_mh * 1.0e-3,
        Mmd=spec.mmd_g * 1.0e-3 if spec.mmd_g is not None else None,
        Mms=spec.mms_g * 1.0e-3 if spec.mms_g is not None else None,
        Cms=spec.cms_m_per_n,
        Vas=spec.vas_l * 1.0e-3 if spec.vas_l is not None else None,
        Fs=spec.fs_hz,
        Rms=spec.rms_kg_per_s,
        Qms=spec.qms,
        n_drivers=spec.count,
    )


def self_impedance_from_surface_average(
    frequencies_hz: np.ndarray,
    surface_pressure_avg_raw: np.ndarray,
    area_m2: float,
) -> np.ndarray:
    """Engineering acoustic self-impedance from the raw solver ⟨p⟩ array."""

    omega = _TWO_PI * np.asarray(frequencies_hz, dtype=np.float64)
    raw = np.asarray(surface_pressure_avg_raw, dtype=np.complex128).reshape(-1)
    return np.conjugate(-1j * omega * raw) / float(area_m2)


def channel_drive_scaling(
    frequencies_hz: np.ndarray,
    surface_pressure_avg_raw: np.ndarray,
    area_m2: float,
    spec: DriverSpec,
    *,
    drive_voltage_v: float,
    rg_ohm: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Solve the coupled driver and return the raw-field scale + metadata.

    The returned array multiplies the channel's raw unit-acceleration
    fields; the payload records the electrical picture for the metadata
    contract (impedance in ohms, peak excursion, applied voltage).
    """

    frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
    if float(area_m2) <= 0.0 or not np.isfinite(float(area_m2)):
        raise ValueError("driver coupling requires a positive finite source area")
    z_self = self_impedance_from_surface_average(
        frequencies, surface_pressure_avg_raw, area_m2
    )
    driver = hornlab_driver(spec)
    coupled = driver_coupling.coupled_direct_radiator_response(
        frequencies,
        driver=driver,
        z_self=z_self,
        rear_chamber_volume_m3=(
            spec.rear_volume_l * 1.0e-3 if spec.rear_volume_l is not None else None
        ),
        drive_voltage_v=drive_voltage_v,
        rg_ohm=rg_ohm,
    )
    omega = _TWO_PI * frequencies
    # Cone acceleration per the applied voltage; conjugated onto raw fields.
    scale_eng = 1j * omega * coupled.cone_volume_velocity / float(area_m2)
    scale_raw = np.conjugate(scale_eng)

    excursion_mm = coupled.cone_excursion_m * 1.0e3
    peak_excursion_mm = float(np.max(excursion_mm)) if excursion_mm.size else 0.0
    warnings: list[str] = []
    if spec.xmax_mm is not None and peak_excursion_mm > spec.xmax_mm:
        warnings.append(
            f"peak cone excursion {peak_excursion_mm:.2f} mm exceeds Xmax "
            f"{spec.xmax_mm:g} mm at {drive_voltage_v:g} V"
        )
    mmd_correction_g = coupled.mmd_correction_kg * 1.0e3
    if mmd_correction_g:
        warnings.append(
            f"Mms was converted to Mmd by subtracting {mmd_correction_g:.3f} g "
            "of free-air radiation mass"
        )
    payload: dict[str, Any] = {
        "drive_voltage_v": float(drive_voltage_v),
        "rg_ohm": float(rg_ohm),
        "spec": spec.model_dump(mode="json", exclude_none=True),
        "electrical_impedance_ohm": {
            "frequencies": frequencies.tolist(),
            "real": coupled.electrical_input_impedance.real.tolist(),
            "imaginary": coupled.electrical_input_impedance.imag.tolist(),
        },
        "cone_excursion_mm": {
            "frequencies": frequencies.tolist(),
            "peak_mm": peak_excursion_mm,
            "values": excursion_mm.tolist(),
        },
        "mmd_correction_g": float(mmd_correction_g),
        "warnings": warnings,
        "scale_convention": (
            "engineering jw*U/S acceleration scale applied to exp(+ikr) "
            "solver fields as complex conjugates"
        ),
    }
    return scale_raw, payload
