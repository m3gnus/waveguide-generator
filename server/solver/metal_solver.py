"""Waveguide Generator adapter for hornlab-metal-bem."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from .contract import build_directivity_metadata
from .directivity_correct import calculate_di_from_polar_patterns

try:
    from hornlab_metal_bem import ObservationConfig, native_config, solve
    from hornlab_metal_bem.backends import discover_metal_backend
    from hornlab_metal_bem.metal.native import discover_native_runtime
except ImportError:  # pragma: no cover - exercised through runtime status
    ObservationConfig = None  # type: ignore[assignment]
    native_config = None  # type: ignore[assignment]
    solve = None  # type: ignore[assignment]
    discover_metal_backend = None  # type: ignore[assignment]
    discover_native_runtime = None  # type: ignore[assignment]


VALID_SOLVER_BACKENDS = {"auto", "bempp", "metal"}
REFERENCE_PRESSURE_PA = 20e-6


class MetalBemUnavailable(RuntimeError):
    """Raised when the Metal solver dependency/runtime is unavailable."""


def normalize_solver_backend(value: Any) -> str:
    raw = str(value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "default": "auto",
        "native": "auto",
        "bempp-cl": "bempp",
        "bempp_cl": "bempp",
        "previous": "bempp",
        "hornlab-metal": "metal",
        "metal-bem": "metal",
        "hornlab-metal-bem": "metal",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in VALID_SOLVER_BACKENDS:
        raise ValueError("solver_backend must be one of: auto, bempp, metal.")
    return normalized


def resolve_solver_backend(value: Any, *, mesh_strategy: Any = None) -> str:
    """Resolve the public backend selector to the concrete runtime backend."""

    backend = normalize_solver_backend(value)
    if backend == "auto":
        if is_metal_solver_available():
            return "metal"
        return "bempp"
    return backend


def metal_backend_status() -> dict[str, Any]:
    if discover_metal_backend is None:
        return {
            "available": False,
            "supportedPlatform": False,
            "nativeHelperAvailable": False,
            "reason": "hornlab-metal-bem is not installed.",
        }
    try:
        status = discover_metal_backend()
    except Exception as exc:
        return {
            "available": False,
            "supportedPlatform": False,
            "nativeHelperAvailable": False,
            "reason": str(exc),
        }
    helper_path = None
    helper_source = None
    helper_build = None
    if discover_native_runtime is not None:
        try:
            native_status = discover_native_runtime(run_smoke_test=False)
            raw_helper_path = getattr(native_status, "helper_executable_path", None)
            helper_path = str(raw_helper_path) if raw_helper_path else None
            helper_source = getattr(native_status, "helper_source", None)
            helper_parts = getattr(raw_helper_path, "parts", ()) if raw_helper_path else ()
            if "release" in helper_parts:
                helper_build = "release"
            elif "debug" in helper_parts:
                helper_build = "debug"
            elif raw_helper_path:
                helper_build = "custom"
        except Exception:
            helper_path = None
            helper_source = None
            helper_build = None

    return {
        "available": bool(status.available),
        "supportedPlatform": bool(status.supported_platform),
        "nativeExecutable": status.native_executable,
        "nativeHelperAvailable": bool(status.native_helper_available),
        "nativeHelperPath": helper_path,
        "nativeHelperSource": helper_source,
        "nativeHelperBuild": helper_build,
        "reason": status.reason,
    }


def is_metal_solver_available() -> bool:
    return bool(metal_backend_status().get("available"))


def _json_safe_native_value(value: Any) -> Any:
    if isinstance(value, complex):
        return {"real": float(value.real), "imaginary": float(value.imag)}
    if isinstance(value, np.generic):
        return _json_safe_native_value(value.item())
    if isinstance(value, np.ndarray):
        return _json_safe_native_value(value.tolist())
    if isinstance(value, dict):
        return {str(key): _json_safe_native_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_native_value(item) for item in value]
    return value


def _polar_config(request) -> dict[str, Any]:
    if request.polar_config is None:
        return {}
    return request.polar_config.model_dump()


def _waveguide_quadrants(request) -> int:
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


def _native_symmetry_plane(request) -> str | None:
    # HornLab/WG quadrants are in the transverse X/Y plane after mesher export.
    # 14: X >= 0 -> mirror across YZ. 12: Y >= 0 -> mirror across XZ.
    return {
        1: "yz+xz",
        12: "xz",
        14: "yz",
        1234: None,
    }.get(_waveguide_quadrants(request))


def _observation_config(request):
    if ObservationConfig is None:
        raise MetalBemUnavailable("hornlab-metal-bem is not installed.")
    polar = _polar_config(request)
    angle_range = polar.get("angle_range") or [0.0, 180.0, 37]
    return ObservationConfig(
        planes=list(polar.get("enabled_axes") or ["horizontal", "vertical"]),
        distance_m=float(polar.get("distance", 2.0)),
        angle_min_deg=float(angle_range[0]),
        angle_max_deg=float(angle_range[1]),
        angle_count=int(angle_range[2]),
        origin=str(polar.get("observation_origin") or "mouth"),
    )


def _directivity(result) -> dict[str, list[list[list[float | None]]]]:
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


def _spl_on_axis(result) -> list[float | None]:
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


def _phase_on_axis(result) -> list[float | None]:
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


def solve_metal_from_msh(
    msh_path: str | Path,
    request,
    *,
    progress_callback=None,
    stage_callback=None,
) -> dict[str, Any]:
    if native_config is None or solve is None:
        raise MetalBemUnavailable("hornlab-metal-bem is not installed.")
    status = metal_backend_status()
    if not status.get("available"):
        raise MetalBemUnavailable(status.get("reason") or "Metal solver backend is unavailable.")

    start_time = time.time()

    if stage_callback:
        stage_callback("setup", 0.0, "Configuring Metal BEM solve")

    def _progress(index: int, total: int, frequency_hz: float) -> None:
        if progress_callback:
            progress_callback(index / max(1, total))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                index / max(1, total),
                f"Solving frequency {index + 1}/{total} with Metal BEM",
            )

    config = native_config(
        freq_min_hz=float(request.frequency_range[0]),
        freq_max_hz=float(request.frequency_range[1]),
        freq_count=int(request.num_frequencies),
        freq_spacing=str(request.frequency_spacing or "log"),
        observation=_observation_config(request),
        progress_callback=_progress,
        mesh_scale=1.0,
        native_symmetry_plane=_native_symmetry_plane(request),
    )
    result = solve(str(msh_path), config)

    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging Metal BEM solver results")

    frequencies = [float(value) for value in np.asarray(result.frequencies_hz).tolist()]
    impedance = np.asarray(result.impedance)
    polar = _polar_config(request)
    observation = {
        "requested_distance_m": float(config.observation.distance_m),
        "effective_distance_m": float(config.observation.distance_m),
        "adjusted": False,
        "observation_origin": config.observation.origin,
    }
    directivity = _directivity(result)
    di_per_plane = calculate_di_from_polar_patterns(directivity)
    metadata = {
        "solver_backend": "metal",
        "device_interface": {"selected": "metal", "metal": status},
        "mesh_validation": {"mode": request.mesh_validation_mode, "backend": "hornlab-metal-bem"},
        "warnings": [],
        "warning_count": 0,
        "failures": [],
        "failure_count": 0,
        "partial_success": False,
        "performance": {
            "total_time_seconds": time.time() - start_time,
            "native_timings": dict(result.timings or {}),
        },
        "observation": observation,
        "directivity": build_directivity_metadata(
            {
                **polar,
                "distance": config.observation.distance_m,
                "observation_origin": config.observation.origin,
            },
            observation,
        ),
        "metal": {
            "native_symmetry_plane": config.native_symmetry_plane,
            "solver_log": _json_safe_native_value(list(result.solver_log or [])),
            "native_diagnostics": _json_safe_native_value(list(result.native_diagnostics or [])),
        },
    }

    return {
        "frequencies": frequencies,
        "directivity": directivity,
        "spl_on_axis": {
            "frequencies": frequencies,
            "spl": _spl_on_axis(result),
            "phase_degrees": _phase_on_axis(result),
        },
        "impedance": {
            "frequencies": frequencies,
            "real": [float(value.real) for value in impedance],
            "imaginary": [float(value.imag) for value in impedance],
        },
        "di": {"frequencies": frequencies, "di": di_per_plane},
        "metadata": metadata,
    }
