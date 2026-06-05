"""Waveguide Generator adapter for hornlab-metal-bem."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from .contract import build_directivity_metadata

try:
    from hornlab_metal_bem import ObservationConfig, native_config, solve
    from hornlab_metal_bem.backends import discover_metal_backend
except ImportError:  # pragma: no cover - exercised through runtime status
    ObservationConfig = None  # type: ignore[assignment]
    native_config = None  # type: ignore[assignment]
    solve = None  # type: ignore[assignment]
    discover_metal_backend = None  # type: ignore[assignment]


VALID_SOLVER_BACKENDS = {"auto", "bempp", "metal"}
METAL_COMPATIBLE_MESH_STRATEGIES = {"hornlab_mesher"}


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
        normalized_strategy = str(mesh_strategy or "").strip().lower()
        if (
            normalized_strategy in METAL_COMPATIBLE_MESH_STRATEGIES
            and is_metal_solver_available()
        ):
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
    return {
        "available": bool(status.available),
        "supportedPlatform": bool(status.supported_platform),
        "nativeExecutable": status.native_executable,
        "nativeHelperAvailable": bool(status.native_helper_available),
        "reason": status.reason,
    }


def is_metal_solver_available() -> bool:
    return bool(metal_backend_status().get("available"))


def _polar_config(request) -> dict[str, Any]:
    if request.polar_config is None:
        return {}
    return request.polar_config.model_dump()


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
            "solver_log": list(result.solver_log or []),
            "native_diagnostics": list(result.native_diagnostics or []),
        },
    }

    return {
        "frequencies": frequencies,
        "directivity": directivity,
        "spl_on_axis": {"frequencies": frequencies, "spl": [0.0 for _ in frequencies]},
        "impedance": {
            "frequencies": frequencies,
            "real": [float(value.real) for value in impedance],
            "imaginary": [float(value.imag) for value in impedance],
        },
        "di": {"frequencies": frequencies, "di": []},
        "metadata": metadata,
    }
