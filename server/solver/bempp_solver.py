"""Waveguide Generator adapter for hornlab-bempp-bem."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .result_mapping import (
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    observation_config,
)

try:
    from hornlab_bempp_bem import (  # type: ignore
        BIEFormulation,
        ObservationConfig,
        SolveConfig,
        solve as bempp_solve,
    )
except ImportError:  # pragma: no cover - exercised through runtime status
    BIEFormulation = None  # type: ignore[assignment]
    ObservationConfig = None  # type: ignore[assignment]
    SolveConfig = None  # type: ignore[assignment]
    bempp_solve = None  # type: ignore[assignment]


class BemppBemUnavailable(RuntimeError):
    """Raised when the BEMPP fallback solver dependency is unavailable."""


def _load_bempp_api() -> bool:
    global BIEFormulation, ObservationConfig, SolveConfig, bempp_solve
    if ObservationConfig is not None and SolveConfig is not None and bempp_solve is not None:
        return True
    try:
        from hornlab_bempp_bem import (  # type: ignore
            BIEFormulation as _BIEFormulation,
            ObservationConfig as _ObservationConfig,
            SolveConfig as _SolveConfig,
            solve as _solve,
        )
    except ImportError:
        return False
    BIEFormulation = _BIEFormulation
    ObservationConfig = _ObservationConfig
    SolveConfig = _SolveConfig
    bempp_solve = _solve
    return True


def opencl_runtime_status() -> dict[str, Any]:
    try:
        import pyopencl as cl  # type: ignore
    except Exception as exc:
        return {"available": False, "reason": f"pyopencl is unavailable: {exc}"}

    try:
        platforms = cl.get_platforms()
        device_count = 0
        platform_names: list[str] = []
        for platform in platforms:
            platform_names.append(str(getattr(platform, "name", "OpenCL platform")))
            try:
                device_count += len(platform.get_devices())
            except Exception:
                continue
    except Exception as exc:
        return {"available": False, "reason": f"OpenCL platform probe failed: {exc}"}

    if len(platforms) == 0 or device_count == 0:
        return {"available": False, "reason": "No OpenCL platforms/devices were found."}
    return {
        "available": True,
        "platformCount": len(platforms),
        "deviceCount": device_count,
        "platforms": platform_names,
        "reason": "OpenCL runtime detected.",
    }


def _chosen_assembly_backend(opencl_status: dict[str, Any] | None = None) -> str:
    status = opencl_status if isinstance(opencl_status, dict) else opencl_runtime_status()
    return "opencl" if status.get("available") else "numba"


def bempp_backend_status() -> dict[str, Any]:
    package_installed = _load_bempp_api()
    opencl_status = opencl_runtime_status()
    assembly_backend = _chosen_assembly_backend(opencl_status)
    if not package_installed:
        return {
            "available": False,
            "packageInstalled": False,
            "openclAvailable": bool(opencl_status.get("available")),
            "assemblyBackend": assembly_backend,
            "reason": (
                "hornlab-bempp-bem is not installed. Install with: "
                "pip install -r server/requirements-bempp.txt"
            ),
            "opencl": opencl_status,
        }

    if opencl_status.get("available"):
        reason = "hornlab-bempp-bem is installed; OpenCL runtime detected."
    else:
        reason = (
            "hornlab-bempp-bem is installed; using numba fallback because "
            f"{opencl_status.get('reason') or 'OpenCL is unavailable.'}"
        )
    return {
        "available": True,
        "packageInstalled": True,
        "openclAvailable": bool(opencl_status.get("available")),
        "assemblyBackend": assembly_backend,
        "reason": reason,
        "opencl": opencl_status,
    }


def is_bempp_solver_available() -> bool:
    return bool(bempp_backend_status().get("available"))


def _observation_config(request):
    return observation_config(
        request,
        ObservationConfig,
        BemppBemUnavailable,
        "hornlab-bempp-bem",
    )


def _phase_safe_device_interface(assembly_backend: str) -> str:
    return f"bempp-cl-{assembly_backend}"


def _bempp_formulation_standard():
    if BIEFormulation is None:
        return "standard"
    return getattr(BIEFormulation, "STANDARD", "standard")


def _precision_from_request(request) -> str:
    advanced = getattr(request, "advanced_settings", None)
    precision = getattr(advanced, "bem_precision", None)
    if precision in {"single", "double"}:
        return str(precision)
    return "single"


def _opencl_device_from_request(request) -> str:
    device_mode = str(getattr(request, "device_mode", "auto") or "auto").strip().lower()
    return "gpu" if device_mode == "opencl_gpu" else "cpu"


def solve_bempp_from_msh(
    msh_path: str | Path,
    request,
    *,
    progress_callback=None,
    stage_callback=None,
) -> dict[str, Any]:
    if not _load_bempp_api():
        raise BemppBemUnavailable(
            "hornlab-bempp-bem is not installed. Install with: "
            "pip install -r server/requirements-bempp.txt"
        )

    status = bempp_backend_status()
    if not status.get("available"):
        raise BemppBemUnavailable(status.get("reason") or "BEMPP solver backend is unavailable.")

    start_time = time.time()
    assembly_backend = str(status.get("assemblyBackend") or "numba")
    device_interface = _phase_safe_device_interface(assembly_backend)

    if stage_callback:
        stage_callback("setup", 0.0, "Configuring BEMPP BEM solve")

    def _progress(index: int, total: int, frequency_hz: float) -> None:
        if progress_callback:
            progress_callback(index / max(1, total))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                index / max(1, total),
                f"Solving frequency {index + 1}/{total} with BEMPP BEM",
            )

    config = SolveConfig(
        freq_min_hz=float(request.frequency_range[0]),
        freq_max_hz=float(request.frequency_range[1]),
        freq_count=int(request.num_frequencies),
        freq_spacing=str(request.frequency_spacing or "log"),
        formulation=_bempp_formulation_standard(),
        observation=_observation_config(request),
        progress_callback=_progress,
        mesh_scale=1.0,
        native_symmetry_plane=native_symmetry_plane(request),
        assembly_backend=assembly_backend,
        opencl_device=_opencl_device_from_request(request),
        precision=_precision_from_request(request),
    )
    result = bempp_solve(str(msh_path), config)

    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging BEMPP BEM solver results")

    metadata = {
        "solver_backend": "bempp",
        "engine": "hornlab-bempp-bem",
        "phase_time_convention": "exp(+ikr)",
        "assembly_backend": assembly_backend,
        "assemblyBackend": assembly_backend,
        "device_interface": {
            "selected": device_interface,
            device_interface: status,
            "assemblyBackend": assembly_backend,
        },
        "mesh_validation": {"mode": request.mesh_validation_mode, "backend": "hornlab-bempp-bem"},
        "performance": {
            "total_time_seconds": time.time() - start_time,
            "native_timings": dict(result.timings or {}),
        },
        "bempp": {
            "native_symmetry_plane": config.native_symmetry_plane,
            "assembly_backend": assembly_backend,
            "opencl_device": config.opencl_device,
            "precision": config.precision,
            "solver_log": json_safe_native_value(list(result.solver_log or [])),
        },
    }

    return build_solver_response(
        result=result,
        config=config,
        request=request,
        start_time=start_time,
        metadata=metadata,
    )
