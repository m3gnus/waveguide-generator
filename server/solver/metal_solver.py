"""Waveguide Generator adapter for hornlab-metal-bem."""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .result_mapping import (
    build_solver_response,
    json_safe_native_value,
    native_symmetry_plane,
    observation_config,
    response_solver_log,
    waveguide_sim_type,
)
from .formulation import complex_k_shift_from_request, formulation_from_request

try:
    from hornlab_metal_bem import MeridianMesh, ObservationConfig, native_config, solve, solve_circsym
    from hornlab_metal_bem.backends import discover_metal_backend
    from hornlab_metal_bem.metal.native import discover_native_runtime
except ImportError:  # pragma: no cover - exercised through runtime status
    MeridianMesh = None  # type: ignore[assignment]
    ObservationConfig = None  # type: ignore[assignment]
    native_config = None  # type: ignore[assignment]
    solve = None  # type: ignore[assignment]
    solve_circsym = None  # type: ignore[assignment]
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
    return is_metal_fast_solve_ready()


def is_apple_silicon_host() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def is_metal_fast_solve_ready(status: dict[str, Any] | None = None) -> bool:
    status = status if status is not None else metal_backend_status()
    if not status.get("available"):
        return False
    if not is_apple_silicon_host():
        return True
    return bool(
        status.get("nativeHelperAvailable")
        and status.get("nativeHelperBuild") == "release"
    )


def metal_fast_solve_unavailable_reason(status: dict[str, Any] | None = None) -> str:
    status = status if status is not None else metal_backend_status()
    if is_metal_fast_solve_ready(status):
        return ""
    if not status.get("available"):
        return status.get("reason") or "Metal solver backend is unavailable."
    if is_apple_silicon_host() and not is_metal_fast_solve_ready(status):
        helper_build = str(status.get("nativeHelperBuild") or "missing")
        helper_path = str(status.get("nativeHelperPath") or "unknown")
        reason = str(status.get("reason") or "").strip()
        detail = f"build={helper_build} path={helper_path}"
        if reason:
            detail = f"{detail} reason={reason}"
        return (
            "Metal BEM fastest solve requires the Swift native release helper. "
            f"{detail}; run: npm run build:metal-helper"
        )
    return "Metal solver backend is unavailable."


def _native_symmetry_plane(request) -> str | None:
    return native_symmetry_plane(request)


def _float_option(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number


def _waveguide_params(request) -> dict[str, Any]:
    options = request.options if isinstance(request.options, dict) else {}
    mesh_options = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    params = mesh_options.get("waveguide_params")
    return dict(params) if isinstance(params, dict) else {}


def _aperture_tag_from_mesh_metadata(mesh_metadata: Any) -> int | None:
    if not isinstance(mesh_metadata, dict):
        return None
    for key in ("apertureTag", "aperture_tag"):
        raw_value = mesh_metadata.get(key)
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid coupled infinite-baffle aperture tag {raw_value!r} "
                f"reported by hornlab-waveguide-mesher metadata key {key!r}."
            ) from exc
        if not numeric_value.is_integer():
            raise ValueError(
                f"Invalid coupled infinite-baffle aperture tag {raw_value!r} "
                f"reported by hornlab-waveguide-mesher metadata key {key!r}; "
                "expected an integer physical tag."
            )
        value = int(numeric_value)
        if value <= 0:
            raise ValueError(
                f"Invalid coupled infinite-baffle aperture tag {raw_value!r} "
                f"reported by hornlab-waveguide-mesher metadata key {key!r}; "
                "expected a positive integer physical tag."
            )
        return value
    return None


def _native_check_open_edges(request) -> bool:
    if waveguide_sim_type(request) == 1:
        return True

    if _native_symmetry_plane(request) is None:
        return True

    params = _waveguide_params(request)
    if not params:
        return True

    enc_depth = _float_option(params.get("enc_depth"), 0.0)
    wall_thickness = _float_option(params.get("wall_thickness"), 0.0)

    # Bare no-wall reduced-domain free-standing meshes are genuinely open
    # shells: their mouth rims are legitimate free edges for the mirrored solve.
    # Closed topologies keep the strict native guard so an off-plane open edge
    # (which should not exist) is caught as a real defect.
    if enc_depth <= 0.0 and wall_thickness <= 0.0:
        return False
    return True


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
    cancellation_callback=None,
    source_motion: str | None = None,
    mesh_metadata: dict[str, Any] | None = None,
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
        if cancellation_callback:
            cancellation_callback()
        if progress_callback:
            progress_callback(index / max(1, total))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                index / max(1, total),
                f"Solving frequency {index + 1}/{total} with Metal BEM",
            )

    aperture_tag: int | None = None
    if waveguide_sim_type(request) == 1:
        aperture_tag = _aperture_tag_from_mesh_metadata(mesh_metadata)
        if aperture_tag is None:
            raise MetalBemUnavailable(
                "Full-3D infinite-baffle Metal solve requires the coupled IB "
                "aperture tag, but hornlab-waveguide-mesher did not report "
                "apertureTag/aperture_tag in mesh metadata."
            )

    config_kwargs = {
        "freq_min_hz": float(request.frequency_range[0]),
        "freq_max_hz": float(request.frequency_range[1]),
        "freq_count": int(request.num_frequencies),
        "freq_spacing": str(request.frequency_spacing or "log"),
        "formulation": formulation_from_request(request, allow_burton_miller=False),
        "complex_k_shift": complex_k_shift_from_request(request),
        "observation": _observation_config(request),
        "progress_callback": _progress,
        "mesh_scale": 1.0,
        "native_symmetry_plane": _native_symmetry_plane(request),
        "native_check_open_edges": _native_check_open_edges(request),
    }
    if aperture_tag is not None:
        config_kwargs["aperture_tag"] = aperture_tag
        # metal-bem load validation recognizes this aperture-tagged topology as
        # an interior-domain coupled IB mesh, so keep validation enabled.
        config_kwargs["mesh_validate"] = True
    # Axial (rigid-piston) vs normal (breathing cap) source BC. Only forwarded
    # when explicitly requested so an older metal-bem (no source_motion) keeps
    # working for the default normal source.
    if source_motion is not None:
        config_kwargs["source_motion"] = source_motion
    try:
        config = native_config(**config_kwargs)
    except TypeError as exc:
        message = str(exc)
        if "source_motion" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support axial source motion. "
                "Install the updated hornlab-metal-bem package for an axial "
                "(rigid-piston) source."
            ) from exc
        if "aperture_tag" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support the coupled "
                "infinite-baffle full-3D path. Install the updated "
                "hornlab-metal-bem package for native aperture_tag/Rayleigh "
                "aperture coupling."
            ) from exc
        if "formulation" in message or "complex_k_shift" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support explicit BEM formulation "
                "options. Install the updated hornlab-metal-bem package."
            ) from exc
        raise
    result = solve(str(msh_path), config)

    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging Metal BEM solver results")

    metadata = {
        "solver_backend": "metal",
        "solver_mode": "full_3d",
        "device_interface": {"selected": "metal", "metal": status},
        "engine": "hornlab-metal-bem",
        "phase_time_convention": "exp(+ikr)",
        "mesh_validation": {"mode": request.mesh_validation_mode, "backend": "hornlab-metal-bem"},
        "performance": {
            "total_time_seconds": time.time() - start_time,
            "native_timings": dict(result.timings or {}),
        },
        "metal": {
            "solver_mode": "full_3d",
            "native_symmetry_plane": config.native_symmetry_plane,
            "native_check_open_edges": getattr(
                config,
                "native_check_open_edges",
                config_kwargs["native_check_open_edges"],
            ),
            "formulation": getattr(config, "formulation", config_kwargs["formulation"]),
            "complex_k_shift": getattr(config, "complex_k_shift", config_kwargs["complex_k_shift"]),
            "solver_log": json_safe_native_value(response_solver_log(result.solver_log)),
            "native_diagnostics": json_safe_native_value(list(result.native_diagnostics or [])),
        },
    }
    config_aperture_tag = getattr(config, "aperture_tag", aperture_tag)
    if config_aperture_tag is not None:
        config_aperture_tag = int(config_aperture_tag)
        metadata["metal"]["aperture_tag"] = config_aperture_tag
    if waveguide_sim_type(request) == 1:
        metadata["infinite_baffle"] = {
            "backend": "full_3d_coupled",
            "aperture_tag": int(config_aperture_tag if config_aperture_tag is not None else aperture_tag),
            "source": "hornlab-waveguide-mesher",
        }

    return build_solver_response(
        result=result,
        config=config,
        request=request,
        start_time=start_time,
        metadata=metadata,
    )


def solve_circsym_from_params(
    waveguide_params: dict[str, Any],
    request,
    *,
    progress_callback=None,
    stage_callback=None,
    cancellation_callback=None,
    source_motion: str | None = None,
) -> dict[str, Any]:
    if native_config is None or solve_circsym is None or MeridianMesh is None:
        raise MetalBemUnavailable(
            "Installed hornlab-metal-bem does not support CircSym. "
            "Install the updated hornlab-metal-bem package."
        )

    try:
        from hornlab_mesher import build_meridian
        from solver.mesher_adapter import waveguide_payload_to_mesher_config
    except ImportError as exc:
        raise MetalBemUnavailable(
            "hornlab-waveguide-mesher is required to build a CircSym meridian."
        ) from exc

    status = metal_backend_status()
    if not status.get("available"):
        raise MetalBemUnavailable(status.get("reason") or "Metal solver backend is unavailable.")

    start_time = time.time()

    if stage_callback:
        stage_callback("setup", 0.0, "Configuring CircSym solve")

    def _progress(index: int, total: int, frequency_hz: float) -> None:
        if progress_callback:
            progress_callback(index / max(1, total))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                index / max(1, total),
                f"Solving frequency {index + 1}/{total} with CircSym",
            )

    def _on_frequency_result(index: int, frequency_hz: float, entry: dict[str, Any]) -> bool:
        if cancellation_callback:
            cancellation_callback()
        return True

    meridian_build = build_meridian(waveguide_payload_to_mesher_config(waveguide_params))
    meridian = meridian_build.as_metal_meridian(MeridianMesh)

    config_kwargs = {
        "freq_min_hz": float(request.frequency_range[0]),
        "freq_max_hz": float(request.frequency_range[1]),
        "freq_count": int(request.num_frequencies),
        "freq_spacing": str(request.frequency_spacing or "log"),
        "formulation": formulation_from_request(request, allow_burton_miller=False),
        "complex_k_shift": complex_k_shift_from_request(request),
        "observation": _observation_config(request),
        "progress_callback": _progress,
        "circsym_baffle_z": meridian_build.baffle_z,
    }
    # Infinite baffle: the mesher tags a mouth-aperture disc; naming it here
    # switches metal-bem to the exact coupled-IB path (interior BEM + Rayleigh
    # aperture), which is the only correct flush-mount formulation.
    aperture_tag = (meridian_build.metadata or {}).get("apertureTag")
    if aperture_tag is not None:
        config_kwargs["circsym_aperture_tag"] = int(aperture_tag)
    if cancellation_callback is not None:
        config_kwargs["on_frequency_result"] = _on_frequency_result
    if source_motion is not None:
        config_kwargs["source_motion"] = source_motion
    try:
        config = native_config(**config_kwargs)
    except TypeError as exc:
        message = str(exc)
        if "circsym_baffle_z" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support CircSym baffle configuration. "
                "Install the updated hornlab-metal-bem package."
            ) from exc
        if "circsym_aperture_tag" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support the coupled infinite-baffle "
                "CircSym path. Install the updated hornlab-metal-bem package."
            ) from exc
        if "source_motion" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support axial source motion. "
                "Install the updated hornlab-metal-bem package for an axial "
                "(rigid-piston) source."
            ) from exc
        if "on_frequency_result" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support cancellable CircSym sweeps. "
                "Install the updated hornlab-metal-bem package."
            ) from exc
        if "formulation" in message or "complex_k_shift" in message:
            raise MetalBemUnavailable(
                "Installed hornlab-metal-bem does not support explicit BEM formulation "
                "options. Install the updated hornlab-metal-bem package."
            ) from exc
        raise

    result = solve_circsym(meridian, config)

    if stage_callback:
        stage_callback("finalizing", 1.0, "Packaging CircSym solver results")

    circsym_aperture_tag = getattr(
        config,
        "circsym_aperture_tag",
        config_kwargs.get("circsym_aperture_tag"),
    )
    metadata = {
        "solver_backend": "metal",
        "solver_mode": "circsym",
        "device_interface": {"selected": "metal", "metal": status},
        "engine": "hornlab-metal-bem",
        "phase_time_convention": "exp(+ikr)",
        "mesh_validation": {"mode": request.mesh_validation_mode, "backend": "hornlab-metal-bem-circsym"},
        "performance": {
            "total_time_seconds": time.time() - start_time,
            "native_timings": dict(result.timings or {}),
        },
        "metal": {
            "solver_mode": "circsym",
            "circsym_baffle_z": getattr(config, "circsym_baffle_z", meridian_build.baffle_z),
            "formulation": getattr(config, "formulation", config_kwargs["formulation"]),
            "complex_k_shift": getattr(config, "complex_k_shift", config_kwargs["complex_k_shift"]),
            "solver_log": json_safe_native_value(response_solver_log(result.solver_log)),
            "native_diagnostics": json_safe_native_value(list(result.native_diagnostics or [])),
            "meridian": json_safe_native_value(dict(meridian_build.metadata or {})),
        },
    }
    if circsym_aperture_tag is not None:
        circsym_aperture_tag = int(circsym_aperture_tag)
        metadata["metal"]["aperture_tag"] = circsym_aperture_tag
        if waveguide_sim_type(request) == 1:
            metadata["infinite_baffle"] = {
                "backend": "circsym_coupled",
                "aperture_tag": circsym_aperture_tag,
                "source": "hornlab-waveguide-mesher",
            }

    return build_solver_response(
        result=result,
        config=config,
        request=request,
        start_time=start_time,
        metadata=metadata,
    )
