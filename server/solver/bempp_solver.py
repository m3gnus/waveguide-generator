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
    waveguide_quadrants,
    waveguide_sim_type,
)
from .formulation import complex_k_shift_from_request, formulation_from_request
from .metal_solver import _float_option, _waveguide_params
from .axisymmetry import reject_bempp_circsym_request

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


def _reject_reduced_domain_request(request) -> None:
    symmetry_plane = native_symmetry_plane(request)
    if symmetry_plane is None:
        return
    raise ValueError(
        "BEMPP fallback solves require a full-domain mesh. "
        f"Got Mesh.Quadrants={waveguide_quadrants(request)!r}, which maps to "
        f"native_symmetry_plane={symmetry_plane!r}. Use Mesh.Quadrants=1234, "
        "or select the Metal backend for native symmetry solves."
    )


def _reject_infinite_baffle_request(request) -> None:
    if waveguide_sim_type(request) != 1:
        return
    raise ValueError(
        "The BEMPP fallback cannot solve coupled infinite-baffle requests. "
        "Use the Metal backend: circular waveguides may route to CircSym "
        "coupled IB, and full-3D infinite baffle uses native aperture_tag/"
        "Rayleigh aperture coupling."
    )


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

    # Platform presence is not sufficient: bempp-cl needs an initializable CPU
    # device/context (e.g. Apple's OpenCL framework lists a GPU but offers no
    # usable CPU device). Verify with the exact call the solve path makes; the
    # result is lru_cached inside the package, so a successful probe is reused.
    device_name = None
    try:
        from hornlab_bempp_bem.device import configure_opencl
    except ImportError:
        pass
    else:
        try:
            device_name = configure_opencl("cpu")
        except Exception as exc:
            return {
                "available": False,
                "platformCount": len(platforms),
                "deviceCount": device_count,
                "platforms": platform_names,
                "reason": f"OpenCL device initialization failed: {exc}",
            }
    return {
        "available": True,
        "platformCount": len(platforms),
        "deviceCount": device_count,
        "platforms": platform_names,
        "device": device_name,
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


def _bempp_formulation_from_request(request):
    formulation = formulation_from_request(request, allow_burton_miller=True)
    if BIEFormulation is None:
        return formulation
    enum_names = {
        "standard": "STANDARD",
        "complex_k": "COMPLEX_K",
        "burton_miller": "BURTON_MILLER",
    }
    return getattr(BIEFormulation, enum_names[formulation], formulation)


def _precision_from_request(request) -> str:
    advanced = getattr(request, "advanced_settings", None)
    precision = getattr(advanced, "bem_precision", None)
    if precision in {"single", "double"}:
        return str(precision)
    return "single"


def _opencl_device_from_request(request) -> str:
    device_mode = str(getattr(request, "device_mode", "auto") or "auto").strip().lower()
    return "gpu" if device_mode == "opencl_gpu" else "cpu"


def _require_closed_mesh(request) -> bool:
    """Closed-mode meshes (enclosure / thickened wall) must arrive sealed.

    Mirrors the bare-shell derivation in metal_solver._native_check_open_edges
    minus the symmetry precondition: bempp always solves the full domain, so
    the only legitimately open mesh is a bare wall-less horn. Unknown payloads
    (imported meshes without waveguide_params) stay permissive.
    """
    params = _waveguide_params(request)
    if not params:
        return False
    enc_depth = _float_option(params.get("enc_depth"), 0.0)
    wall_thickness = _float_option(params.get("wall_thickness"), 0.0)
    return enc_depth > 0.0 or wall_thickness > 0.0


def solve_bempp_from_msh(
    msh_path: str | Path,
    request,
    *,
    progress_callback=None,
    stage_callback=None,
    cancellation_callback=None,
    source_motion: str | None = None,
    mesh_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _reject_infinite_baffle_request(request)
    reject_bempp_circsym_request(request)
    _reject_reduced_domain_request(request)

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
        if cancellation_callback:
            cancellation_callback()
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
        formulation=_bempp_formulation_from_request(request),
        complex_k_shift=complex_k_shift_from_request(request),
        observation=_observation_config(request),
        progress_callback=_progress,
        mesh_scale=1.0,
        native_symmetry_plane=native_symmetry_plane(request),
        assembly_backend=assembly_backend,
        opencl_device=_opencl_device_from_request(request),
        precision=_precision_from_request(request),
    )
    # Axial (rigid-piston) source motion. Feature-detect like require_closed_mesh
    # below so an older pinned bempp still serves the default normal source; a
    # non-normal request against an old bempp fails loudly rather than silently
    # downgrading to normal.
    if source_motion and str(source_motion).lower() != "normal":
        if not hasattr(config, "source_motion"):
            raise BemppBemUnavailable(
                "Installed hornlab-bempp-bem does not support axial source motion; "
                "update the pinned hornlab-bempp-bem (>= 4638578)."
            )
        config.source_motion = str(source_motion).lower()
    # hornlab-bempp-bem newer than the 8c112bb pin validates surface closure
    # at load (a closed-mode mesh with open edges is a leaking model this
    # backend would otherwise solve silently). Feature-detect so the current
    # public pin keeps working until the requirements bump.
    if hasattr(config, "require_closed_mesh"):
        config.require_closed_mesh = _require_closed_mesh(request)
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
            "formulation": json_safe_native_value(config.formulation),
            "complex_k_shift": float(config.complex_k_shift),
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
