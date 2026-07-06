"""Shared Waveguide Generator result mapping for BEM solve adapters."""

from __future__ import annotations

import enum
import time
from typing import Any

import numpy as np

from .contract import build_directivity_metadata
from .directivity_index import calculate_di_from_polar_patterns

REFERENCE_PRESSURE_PA = 20e-6
REFERENCE_RHO_C = 1.21 * 343.0


def json_safe_native_value(value: Any) -> Any:
    if isinstance(value, complex):
        return {"real": float(value.real), "imaginary": float(value.imag)}
    if isinstance(value, enum.Enum):
        return json_safe_native_value(value.value)
    if isinstance(value, np.generic):
        return json_safe_native_value(value.item())
    if isinstance(value, np.ndarray):
        return json_safe_native_value(value.tolist())
    if isinstance(value, dict):
        return {str(key): json_safe_native_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_native_value(item) for item in value]
    return value


def polar_config(request) -> dict[str, Any]:
    if request.polar_config is None:
        return {}
    return request.polar_config.model_dump()


def waveguide_quadrants(request) -> int:
    options = request.options if isinstance(getattr(request, "options", None), dict) else {}
    mesh_opts = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    waveguide_params = (
        mesh_opts.get("waveguide_params")
        if isinstance(mesh_opts.get("waveguide_params"), dict)
        else {}
    )
    try:
        return int(waveguide_params.get("quadrants", 1234))
    except (TypeError, ValueError):
        return 1234


def waveguide_sim_type(request) -> int:
    try:
        return int(float(getattr(request, "sim_type", 2)))
    except (TypeError, ValueError):
        return 2


def native_symmetry_plane(request) -> str | None:
    # HornLab/WG quadrants are in the transverse X/Y plane after mesher export.
    # 14: X >= 0 -> mirror across YZ. 12: Y >= 0 -> mirror across XZ.
    quadrants = waveguide_quadrants(request)
    if waveguide_sim_type(request) == 1:
        # Infinite baffle is NOT a full-3D symmetry reduction. The xy image plane
        # mirrors the horn into an hourglass double-horn radiating in free space
        # (front=back, grazing-loud, comb banding) -- it cannot represent a
        # flush-mounted recessed horn. The correct formulation is CircSym's
        # coupled-IB path (interior BEM + analytic Rayleigh aperture), which runs
        # automatically for circular waveguides. Full-3D infinite baffle is
        # therefore unsupported.
        raise ValueError(
            "Infinite baffle is supported only for circular waveguides via the "
            "CircSym solver (which runs automatically). The full-3D backend cannot "
            "solve infinite baffle: this geometry is not circular, or full-3D was "
            "forced. Use a circular waveguide, or switch to Free-standing or "
            "Enclosure mode."
        )
    return {
        1: "yz+xz",
        12: "xz",
        14: "yz",
        1234: None,
    }.get(quadrants)


def observation_config(request, observation_config_cls, unavailable_error, package_name: str):
    if observation_config_cls is None:
        raise unavailable_error(f"{package_name} is not installed.")
    polar = polar_config(request)
    angle_range = polar.get("angle_range") or [0.0, 180.0, 37]
    return observation_config_cls(
        planes=list(polar.get("enabled_axes") or ["horizontal", "vertical"]),
        distance_m=float(polar.get("distance", 2.0)),
        angle_min_deg=float(angle_range[0]),
        angle_max_deg=float(angle_range[1]),
        angle_count=int(angle_range[2]),
        origin=str(polar.get("observation_origin") or "mouth"),
    )


def directivity(result) -> dict[str, list[list[list[float | None]]]]:
    angles = [float(value) for value in np.asarray(result.observation_angles_deg).tolist()]
    directivity_db = np.asarray(result.directivity_db, dtype=float)
    out: dict[str, list[list[list[float | None]]]] = {}
    for plane_index, plane_name in enumerate(result.observation_planes):
        plane_rows: list[list[list[float | None]]] = []
        for freq_index in range(directivity_db.shape[0]):
            row = []
            for angle_index, angle in enumerate(angles):
                value = directivity_db[freq_index, plane_index, angle_index]
                row.append([angle, float(value) if np.isfinite(value) else None])
            plane_rows.append(row)
        out[str(plane_name)] = plane_rows
    return out


def spl_on_axis(result) -> list[float | None]:
    angles = np.asarray(result.observation_angles_deg, dtype=float)
    pressure = np.asarray(result.pressure_complex, dtype=np.complex128)
    if angles.ndim != 1 or angles.size == 0 or pressure.ndim != 3:
        return [None for _ in np.asarray(result.frequencies_hz).tolist()]

    on_axis_index = int(np.argmin(np.abs(angles)))
    amplitudes = np.abs(pressure[:, 0, on_axis_index])
    spl_values: list[float | None] = []
    for amplitude in amplitudes:
        value = float(amplitude)
        if value > 0.0 and np.isfinite(value):
            spl_values.append(float(20.0 * np.log10(value / REFERENCE_PRESSURE_PA)))
        else:
            spl_values.append(None)
    return spl_values


def phase_on_axis(result) -> list[float | None]:
    angles = np.asarray(result.observation_angles_deg, dtype=float)
    pressure = np.asarray(result.pressure_complex, dtype=np.complex128)
    if angles.ndim != 1 or angles.size == 0 or pressure.ndim != 3:
        return [None for _ in np.asarray(result.frequencies_hz).tolist()]

    on_axis_index = int(np.argmin(np.abs(angles)))
    values = pressure[:, 0, on_axis_index]
    phase_values: list[float | None] = []
    for value in values:
        amplitude = float(np.abs(value))
        if amplitude > 0.0 and np.isfinite(amplitude):
            phase_values.append(float(np.angle(value, deg=True)))
        else:
            phase_values.append(None)
    return phase_values


def specific_impedance_z_over_rho_c(result) -> np.ndarray:
    """Map solver raw unit-acceleration pressure to engineering Z/(rho*c)."""
    frequencies = np.asarray(result.frequencies_hz, dtype=float)
    raw_pressure = np.asarray(result.impedance, dtype=np.complex128)
    if raw_pressure.shape != frequencies.shape:
        raw_pressure = np.reshape(raw_pressure, frequencies.shape)

    omega = 2.0 * np.pi * frequencies
    physical_impedance = 1j * omega * raw_pressure
    return np.conjugate(physical_impedance) / REFERENCE_RHO_C


def _apply_solver_log_warnings(metadata: dict[str, Any]) -> None:
    """Surface per-frequency solver failures from solver_log.

    bempp marks GMRES convergence per frequency (``converged``); metal
    records the LAPACK return code (``lapack_info``) and conditioning
    diagnostics (``native_diagnostics.dense_solve_suspect``). Unreliable
    frequencies flip ``partial_success``; a suspect-conditioning frequency
    only warns.
    """
    solver_log: list | None = None
    for backend_key in ("metal", "bempp"):
        backend_meta = metadata.get(backend_key)
        if isinstance(backend_meta, dict) and isinstance(
            backend_meta.get("solver_log"), list
        ):
            solver_log = backend_meta["solver_log"]
            break
    if solver_log is None:
        return
    warnings = metadata.setdefault("warnings", [])
    unreliable = 0
    for entry in solver_log:
        if not isinstance(entry, dict):
            continue
        frequency = entry.get("frequency_hz")
        label = f"{float(frequency):.1f} Hz" if isinstance(frequency, (int, float)) else "unknown frequency"
        if entry.get("converged") is False:
            warnings.append(
                f"GMRES did not converge at {label}; SPL/DI at this frequency is unreliable."
            )
            unreliable += 1
        lapack_info = entry.get("lapack_info")
        if isinstance(lapack_info, (int, float)) and int(lapack_info) != 0:
            warnings.append(
                f"Dense LU solve failed (LAPACK info={int(lapack_info)}) at {label}; "
                "results at this frequency are unreliable."
            )
            unreliable += 1
        diagnostics = entry.get("native_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics.get("dense_solve_suspect") is True:
            warnings.append(
                f"Dense-solve conditioning is suspect at {label} (near a fictitious "
                "resonance); compare against neighbouring frequencies."
            )
    metadata["warning_count"] = len(warnings)
    if unreliable:
        metadata["partial_success"] = True


def build_solver_response(
    *,
    result,
    config,
    request,
    start_time: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    frequencies = [float(value) for value in np.asarray(result.frequencies_hz).tolist()]
    impedance = specific_impedance_z_over_rho_c(result)
    polar = polar_config(request)
    observation = {
        "requested_distance_m": float(config.observation.distance_m),
        "effective_distance_m": float(config.observation.distance_m),
        "adjusted": False,
        "observation_origin": config.observation.origin,
    }
    directivity_payload = directivity(result)
    di_per_plane = calculate_di_from_polar_patterns(directivity_payload)

    metadata.setdefault("warnings", [])
    metadata.setdefault("warning_count", 0)
    metadata.setdefault("failures", [])
    metadata.setdefault("failure_count", 0)
    metadata.setdefault("partial_success", False)
    # Per-frequency failure flags recorded by the solvers were embedded raw in
    # solver_log but never surfaced: a non-converged GMRES frequency (bempp)
    # or a failed dense LU (metal) rendered as normal data. Aggregate them
    # into the response-level warnings both adapters share.
    _apply_solver_log_warnings(metadata)
    metadata.setdefault("performance", {})
    metadata["performance"].setdefault("total_time_seconds", time.time() - start_time)
    metadata["observation"] = observation
    metadata["directivity"] = build_directivity_metadata(
        {
            **polar,
            "distance": config.observation.distance_m,
            "observation_origin": config.observation.origin,
        },
        observation,
    )
    metadata["impedance_units"] = "Z/(rho*c)"
    metadata["impedance_quantity"] = "specific_acoustic_impedance"
    metadata["impedance_drive"] = "unit_acceleration"

    return {
        "frequencies": frequencies,
        "directivity": directivity_payload,
        "spl_on_axis": {
            "frequencies": frequencies,
            "spl": spl_on_axis(result),
            "phase_degrees": phase_on_axis(result),
        },
        "impedance": {
            "frequencies": frequencies,
            "real": [float(value.real) for value in impedance],
            "imaginary": [float(value.imag) for value in impedance],
        },
        "di": {"frequencies": frequencies, "di": di_per_plane},
        "metadata": metadata,
    }
