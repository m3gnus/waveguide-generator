"""Waveguide Generator adapter for hornlab-metal-bem."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .result_mapping import (
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    observation_config,
)

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


VALID_SOLVER_BACKENDS = {"auto", "metal", "bempp"}


class MetalBemUnavailable(RuntimeError):
    """Raised when the Metal solver dependency/runtime is unavailable."""


def normalize_solver_backend(value: Any) -> str:
    raw = str(value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "default": "auto",
        "native": "auto",
        "hornlab-metal": "metal",
        "metal-bem": "metal",
        "hornlab-metal-bem": "metal",
        "bempp-cl": "bempp",
        "bemppcl": "bempp",
        "previous": "bempp",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in VALID_SOLVER_BACKENDS:
        raise ValueError("solver_backend must be one of: auto, metal, bempp.")
    return normalized


def resolve_solver_backend(value: Any, *, mesh_strategy: Any = None) -> str:
    """Resolve the public backend selector to the concrete runtime backend."""

    normalized = normalize_solver_backend(value)
    if normalized != "auto":
        return normalized
    if metal_backend_status().get("available"):
        return "metal"
    try:
        from .bempp_solver import bempp_backend_status
    except Exception:
        bempp_status = {"available": False}
    else:
        bempp_status = bempp_backend_status()
    if bempp_status.get("available"):
        return "bempp"
    return "metal"


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
    helper_modified = None
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
            if raw_helper_path:
                try:
                    helper_modified = datetime.fromtimestamp(
                        Path(raw_helper_path).stat().st_mtime, tz=timezone.utc
                    ).isoformat()
                except OSError:
                    helper_modified = None
        except Exception:
            helper_path = None
            helper_source = None
            helper_build = None
            helper_modified = None

    return {
        "available": bool(status.available),
        "supportedPlatform": bool(status.supported_platform),
        "nativeExecutable": status.native_executable,
        "nativeHelperAvailable": bool(status.native_helper_available),
        "nativeHelperPath": helper_path,
        "nativeHelperSource": helper_source,
        "nativeHelperBuild": helper_build,
        # Stale solver runtimes are invisible otherwise: the server process
        # keeps its imported hornlab-metal-bem code until restart, so expose
        # when the helper binary on disk was last rebuilt.
        "nativeHelperModified": helper_modified,
        "reason": status.reason,
    }


def is_metal_solver_available() -> bool:
    return bool(metal_backend_status().get("available"))


def _native_symmetry_plane(request) -> str | None:
    return native_symmetry_plane(request)


def _observation_config(request):
    return observation_config(
        request,
        ObservationConfig,
        MetalBemUnavailable,
        "hornlab-metal-bem",
    )


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
        try:
            from .bempp_solver import bempp_backend_status
        except Exception:
            bempp_status = {"available": False, "reason": "BEMPP backend status unavailable."}
        else:
            bempp_status = bempp_backend_status()
        if not bempp_status.get("available"):
            raise MetalBemUnavailable(
                "No BEM solver backend is available. "
                f"Metal: {status.get('reason') or 'unavailable'}; "
                f"BEMPP: {bempp_status.get('reason') or 'unavailable'}. "
                "Install the BEMPP fallback with: pip install -r server/requirements-bempp.txt"
            )
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

    metadata = {
        "solver_backend": "metal",
        "device_interface": {"selected": "metal", "metal": status},
        "engine": "hornlab-metal-bem",
        "phase_time_convention": "exp(+ikr)",
        "mesh_validation": {"mode": request.mesh_validation_mode, "backend": "hornlab-metal-bem"},
        "performance": {
            "total_time_seconds": time.time() - start_time,
            "native_timings": dict(result.timings or {}),
        },
        "metal": {
            "native_symmetry_plane": config.native_symmetry_plane,
            "solver_log": json_safe_native_value(list(result.solver_log or [])),
            "native_diagnostics": json_safe_native_value(list(result.native_diagnostics or [])),
        },
    }

    return build_solver_response(
        result=result,
        config=config,
        request=request,
        start_time=start_time,
        metadata=metadata,
    )
