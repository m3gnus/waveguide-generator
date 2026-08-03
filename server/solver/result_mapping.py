"""Map every native backend onto the versioned v2 result contract.

The numerical semantics port v1 ``server/solver/result_mapping.py:20-391``:
20 µPa SPL, raw wrapped pressure phase, engineering-sign ``Z/(rho*c)``, one
common frequency axis, null invalid samples, partial-success diagnostics,
plane DI, and the balloon four-state/beam-shape contract.
"""

from __future__ import annotations

import enum
import math
import time
from typing import Any

import numpy as np

from .beam_shape import beam_shape_summary
from .context import SolverContext
from .contract import build_directivity_metadata, frequency_failure
from .directivity_index import calculate_di_from_polar_patterns
from .quadrants import native_symmetry_plane_for_quadrants


REFERENCE_PRESSURE_PA = 20.0e-6
REFERENCE_RHO_C = 1.21 * 343.0
_BALLOON_FLOOR_AMPLITUDE = REFERENCE_PRESSURE_PA * 10.0 ** (-120.0 / 20.0)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def json_safe_native_value(value: Any) -> Any:
    """Convert native/NumPy values without permitting JSON NaN/Infinity."""

    if isinstance(value, complex):
        return {"real": _finite(value.real), "imaginary": _finite(value.imag)}
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
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def response_solver_log(solver_log: Any) -> list[Any]:
    """Strip duplicated sphere pressure (v1 ``result_mapping.py:49-65``)."""

    entries: list[Any] = []
    for entry in solver_log or []:
        if isinstance(entry, dict) and "observation_sphere_pressure_complex" in entry:
            entry = {
                key: value
                for key, value in entry.items()
                if key != "observation_sphere_pressure_complex"
            }
        entries.append(entry)
    return entries


def native_symmetry_plane(context: SolverContext) -> str | None:
    return native_symmetry_plane_for_quadrants(context.quadrants)


def observation_config(
    context: SolverContext,
    observation_config_cls: Any,
    unavailable_error: type[Exception],
    package_name: str,
) -> Any:
    """Build native observation config with the v1 sphere-feature fallback."""

    if observation_config_cls is None:
        raise unavailable_error(f"{package_name} is not installed.")
    polar = context.polar_config
    angle_range = polar.get("angle_range") or [0.0, 180.0, 37]
    kwargs: dict[str, Any] = {
        "planes": list(polar.get("enabled_axes") or ["horizontal", "vertical"]),
        "distance_m": float(polar.get("distance", 2.0)),
        "angle_min_deg": float(angle_range[0]),
        "angle_max_deg": float(angle_range[1]),
        "angle_count": int(angle_range[2]),
        "origin": str(polar.get("observation_origin") or "mouth"),
    }
    if polar.get("spherical_sampling"):
        kwargs["sphere_grid"] = (
            int(polar.get("spherical_theta_count") or 37),
            int(polar.get("spherical_phi_count") or 72),
        )
        if context.sim_type == 1:
            kwargs["sphere_theta_max_deg"] = 90.0
    try:
        return observation_config_cls(**kwargs)
    except TypeError as exc:
        if "sphere" not in str(exc) or "sphere_grid" not in kwargs:
            raise
        kwargs.pop("sphere_grid", None)
        kwargs.pop("sphere_theta_max_deg", None)
        return observation_config_cls(**kwargs)


def directivity(result: Any) -> dict[str, list[list[list[float | None]]]]:
    """Map engine dB patterns, preserving unavailable cells as null."""

    try:
        angles = np.asarray(result.observation_angles_deg, dtype=float)
        values = np.asarray(result.directivity_db, dtype=float)
        frequency_count = len(np.asarray(result.frequencies_hz).reshape(-1))
    except (TypeError, ValueError):
        return {}
    if angles.ndim != 1 or values.ndim != 3:
        return {}
    finite_angle_indices = [
        index for index, angle in enumerate(angles) if math.isfinite(float(angle))
    ]
    output: dict[str, list[list[list[float | None]]]] = {}
    for plane_index, plane_name in enumerate(result.observation_planes):
        if plane_index >= values.shape[1]:
            break
        plane_rows: list[list[list[float | None]]] = []
        for frequency_index in range(frequency_count):
            row: list[list[float | None]] = []
            for angle_index in finite_angle_indices:
                if angle_index >= values.shape[2]:
                    continue
                level = (
                    _finite(values[frequency_index, plane_index, angle_index])
                    if frequency_index < values.shape[0]
                    else None
                )
                row.append([float(angles[angle_index]), level])
            plane_rows.append(row)
        output[str(plane_name)] = plane_rows
    return output


def _on_axis_pressure(result: Any) -> np.ndarray | None:
    angles = np.asarray(result.observation_angles_deg, dtype=float)
    pressure = np.asarray(result.pressure_complex, dtype=np.complex128)
    if angles.ndim != 1 or angles.size == 0 or pressure.ndim != 3 or pressure.shape[1] == 0:
        return None
    usable = np.flatnonzero(np.isfinite(angles))
    usable = usable[usable < pressure.shape[2]]
    if usable.size == 0:
        return None
    angle_index = int(usable[np.argmin(np.abs(angles[usable]))])
    if angle_index >= pressure.shape[2]:
        return None
    return pressure[:, 0, angle_index]


def spl_on_axis(result: Any) -> list[float | None]:
    values = _on_axis_pressure(result)
    count = len(np.asarray(result.frequencies_hz))
    if values is None:
        return [None] * count
    output: list[float | None] = []
    for value in values:
        amplitude = _finite(np.abs(value))
        if amplitude is None or amplitude <= 0.0:
            output.append(None)
            continue
        spl = 20.0 * (math.log10(amplitude) - math.log10(REFERENCE_PRESSURE_PA))
        output.append(spl if math.isfinite(spl) else None)
    count = len(np.asarray(result.frequencies_hz).reshape(-1))
    return (output + [None] * count)[:count]


def phase_on_axis(result: Any) -> list[float | None]:
    """Raw wrapped pressure phase in degrees (v1 lines 167-182)."""

    values = _on_axis_pressure(result)
    count = len(np.asarray(result.frequencies_hz))
    if values is None:
        return [None] * count
    output: list[float | None] = []
    for value in values:
        amplitude = _finite(np.abs(value))
        output.append(float(np.angle(value, deg=True)) if amplitude is not None and amplitude > 0 else None)
    count = len(np.asarray(result.frequencies_hz).reshape(-1))
    return (output + [None] * count)[:count]


def specific_impedance_z_over_rho_c(result: Any) -> list[complex | None]:
    """Convert unit-acceleration pressure using v=a/(-iω), then conjugate.

    This is the sign/drive convention from v1
    ``server/solver/result_mapping.py:185-200``.
    """

    frequencies = np.asarray(result.frequencies_hz, dtype=float)
    raw_pressure = np.asarray(result.impedance, dtype=np.complex128).reshape(-1)
    output: list[complex | None] = []
    for index, frequency in enumerate(frequencies.reshape(-1)):
        if index >= raw_pressure.size:
            output.append(None)
            continue
        mapped = np.conjugate(-1j * 2.0 * np.pi * frequency * raw_pressure[index]) / REFERENCE_RHO_C
        output.append(
            complex(mapped)
            if math.isfinite(float(mapped.real)) and math.isfinite(float(mapped.imag))
            else None
        )
    return output


def _apply_solver_log_warnings(metadata: dict[str, Any]) -> None:
    """Surface GMRES/LAPACK failures without blanking usable arrays."""

    solver_log = None
    for backend_key in ("metal", "bempp"):
        backend = metadata.get(backend_key)
        if isinstance(backend, dict) and isinstance(backend.get("solver_log"), list):
            solver_log = backend["solver_log"]
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
            warnings.append(f"GMRES did not converge at {label}; SPL/DI at this frequency is unreliable.")
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
                f"Dense-solve conditioning is suspect at {label} (near a fictitious resonance); "
                "compare against neighbouring frequencies."
            )
    metadata["warning_count"] = len(warnings)
    if unreliable:
        metadata["partial_success"] = True


def _balloon_grid_from_result(result: Any) -> dict[str, Any] | None:
    """Apply v1 theta-major validation, -120 dB floor, and pole normalization."""

    pressure = getattr(result, "sphere_pressure_complex", None)
    theta_deg = getattr(result, "sphere_theta_deg", None)
    phi_deg = getattr(result, "sphere_phi_deg", None)
    if pressure is None or theta_deg is None or phi_deg is None:
        return None
    try:
        pressure = np.asarray(pressure, dtype=np.complex128)
        theta_flat = np.asarray(theta_deg, dtype=float)
        phi_flat = np.asarray(phi_deg, dtype=float)
        frequency_count = len(np.asarray(result.frequencies_hz).reshape(-1))
    except (TypeError, ValueError):
        return None
    if (
        pressure.ndim != 2
        or pressure.shape[0] != frequency_count
        or theta_flat.ndim != 1
        or phi_flat.ndim != 1
        or theta_flat.size != pressure.shape[1]
        or phi_flat.size != pressure.shape[1]
        or not np.isfinite(theta_flat).all()
        or not np.isfinite(phi_flat).all()
        or not np.isfinite(pressure.real).all()
        or not np.isfinite(pressure.imag).all()
    ):
        return None
    theta_axis = np.unique(theta_flat)
    if theta_axis.size < 2 or theta_flat.size % theta_axis.size != 0:
        return None
    phi_count = theta_flat.size // theta_axis.size
    if phi_count < 3:
        return None
    theta_grid = theta_flat.reshape(theta_axis.size, phi_count)
    phi_grid = phi_flat.reshape(theta_axis.size, phi_count)
    phi_axis = phi_grid[0]
    if (
        not np.all(np.diff(theta_axis) > 0.0)
        or not np.all(np.diff(phi_axis) > 0.0)
        or not np.allclose(theta_grid, theta_axis[:, None])
        or not np.allclose(phi_grid, phi_axis[None, :])
    ):
        return None
    amplitude = np.maximum(np.abs(pressure), _BALLOON_FLOOR_AMPLITUDE)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        spl = 20.0 * (np.log10(amplitude) - math.log10(REFERENCE_PRESSURE_PA))
    normalized = spl - spl[:, 0][:, None]
    if not np.isfinite(normalized).all():
        return None
    return {
        "theta_deg": theta_axis,
        "phi_deg": phi_axis,
        "spl_norm_db": normalized.reshape(pressure.shape[0], theta_axis.size, phi_count),
        "hemisphere": bool(theta_axis[-1] <= 90.0 + 1.0e-9),
    }


def build_solver_response(
    *,
    result: Any,
    config: Any,
    context: SolverContext,
    start_time: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build the common JSON shape; dry-run remains a strict subset of it."""

    frequencies = [_finite(value) for value in np.asarray(result.frequencies_hz).tolist()]
    if any(value is None for value in frequencies):
        raise ValueError("native solver returned a non-finite frequency axis")
    frequency_values = [float(value) for value in frequencies if value is not None]
    impedance = specific_impedance_z_over_rho_c(result)
    observation = {
        "requested_distance_m": float(config.observation.distance_m),
        "effective_distance_m": float(config.observation.distance_m),
        "adjusted": False,
        "observation_origin": str(config.observation.origin),
    }
    patterns = directivity(result)

    metadata.setdefault("warnings", [])
    metadata.setdefault("warning_count", 0)
    metadata.setdefault("failures", [])
    metadata.setdefault("failure_count", len(metadata["failures"]))
    metadata.setdefault("partial_success", False)
    failures = metadata["failures"]
    expected = len(frequency_values)
    raw_counts = {
        "pressure": (
            int(np.asarray(getattr(result, "pressure_complex", [])).shape[0])
            if np.asarray(getattr(result, "pressure_complex", [])).ndim >= 1
            else 0
        ),
        "directivity": (
            int(np.asarray(getattr(result, "directivity_db", [])).shape[0])
            if np.asarray(getattr(result, "directivity_db", [])).ndim >= 1
            else 0
        ),
        "impedance": int(np.asarray(getattr(result, "impedance", [])).size),
    }
    for quantity, actual in raw_counts.items():
        if actual != expected:
            failures.append(
                frequency_failure(
                    frequency_values[min(actual, expected - 1)] if expected else 0.0,
                    "postprocess",
                    "native_axis_mismatch",
                    f"{quantity} returned {actual} samples for {expected} frequencies; unavailable samples were padded with null",
                )
            )
    if any(actual != expected for actual in raw_counts.values()):
        metadata["partial_success"] = True
    metadata["failure_count"] = len(failures)
    _apply_solver_log_warnings(metadata)
    metadata.setdefault("performance", {})
    metadata["performance"].setdefault("total_time_seconds", time.time() - start_time)
    metadata["observation"] = observation
    metadata["directivity"] = build_directivity_metadata(
        {
            **context.polar_config,
            "distance": config.observation.distance_m,
            "observation_origin": config.observation.origin,
        },
        observation,
    )
    metadata.update(
        {
            "result_contract_version": 1,
            "phase_quantity": "raw_wrapped_pressure_phase",
            "phase_units": "degrees",
            "impedance_units": "Z/(rho*c)",
            "impedance_quantity": "specific_acoustic_impedance",
            "impedance_drive": "unit_acceleration",
            "source_motion": context.source_motion,
            "quadrants": context.quadrants,
        }
    )

    response: dict[str, Any] = {
        "frequencies": frequency_values,
        "directivity": patterns,
        "spl_on_axis": {
            "frequencies": frequency_values,
            "spl": spl_on_axis(result),
            "phase_degrees": phase_on_axis(result),
        },
        "impedance": {
            "frequencies": frequency_values,
            "real": [_finite(value.real) if value is not None else None for value in impedance],
            "imaginary": [_finite(value.imag) if value is not None else None for value in impedance],
        },
        "di": {
            "frequencies": frequency_values,
            "di": calculate_di_from_polar_patterns(patterns),
        },
        "metadata": metadata,
    }

    balloon = _balloon_grid_from_result(result)
    requested = bool(context.polar_config.get("spherical_sampling"))
    configured = getattr(config.observation, "sphere_grid", None) is not None
    metadata["balloon_sampling"] = {
        "requested": requested,
        "configured": configured,
        "available": balloon is not None,
        "status": (
            "available"
            if balloon is not None
            else "backend_unsupported"
            if requested and not configured
            else "missing_result"
            if requested
            else "disabled"
        ),
    }
    if balloon is not None:
        response["balloon"] = {
            "frequencies": frequency_values,
            "theta_deg": [round(float(value), 3) for value in balloon["theta_deg"]],
            "phi_deg": [round(float(value), 3) for value in balloon["phi_deg"]],
            "spl_norm_db": json_safe_native_value(np.round(balloon["spl_norm_db"], 2)),
            "distance_m": float(config.observation.distance_m),
            "hemisphere": balloon["hemisphere"],
        }
        summary = beam_shape_summary(
            balloon["theta_deg"],
            balloon["phi_deg"],
            balloon["spl_norm_db"],
            frequency_values,
            hemisphere=balloon["hemisphere"],
        )
        if summary is not None:
            response["beam_shape"] = summary
    return response


__all__ = [
    "REFERENCE_PRESSURE_PA",
    "REFERENCE_RHO_C",
    "build_solver_response",
    "directivity",
    "json_safe_native_value",
    "native_symmetry_plane",
    "observation_config",
    "phase_on_axis",
    "response_solver_log",
    "specific_impedance_z_over_rho_c",
    "spl_on_axis",
]
