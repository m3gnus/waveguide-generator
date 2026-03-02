import logging

import numpy as np
from typing import Callable, Dict, List, Optional, Tuple

from .contract import frequency_failure

logger = logging.getLogger(__name__)
from .deps import bempp_api
from .impedance import calculate_throat_impedance
from .device_interface import (
    boundary_device_interface,
    configure_opencl_safe_profile,
    is_opencl_buffer_error,
    potential_device_interface,
    selected_device_metadata,
)
from .directivity import calculate_directivity_index_from_pressure, calculate_directivity_patterns
from .observation import infer_observation_frame


def _resolve_observation_distance_m(polar_config: Optional[Dict], default: float = 1.0) -> float:
    if not isinstance(polar_config, dict):
        return float(default)
    try:
        distance = float(polar_config.get("distance", default))
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(distance) or distance <= 0:
        return float(default)
    return float(distance)


def _build_source_velocity(space_u, amplitude: float = 1.0):
    dof_count = getattr(space_u, "grid_dof_count", None)
    if dof_count is None:
        dof_count = getattr(space_u, "global_dof_count", 0)
    if int(dof_count) <= 0:
        raise ValueError("Source velocity space contains no DOFs (segments=[2] is empty).")

    coeffs = np.full(int(dof_count), complex(amplitude, 0.0), dtype=np.complex128)
    return bempp_api.GridFunction(space_u, coefficients=coeffs)


def solve_frequency(
    grid,
    k: float,
    c: float,
    rho: float,
    sim_type: str,
    throat_elements: np.ndarray = None,
    boundary_interface: Optional[str] = None,
    potential_interface: Optional[str] = None,
    use_burton_miller: bool = True,
    observation_distance_m: float = 1.0,
    observation_frame: Optional[Dict[str, np.ndarray]] = None,
) -> Tuple[float, complex, float]:
    """
    Solve BEM for a single frequency using exterior Helmholtz BIE in SI units.

    Args:
        grid: bempp grid object (coordinates in meters)
        k: wavenumber (2*pi*freq/c) in 1/m
        c: speed of sound (m/s)
        rho: air density (kg/m^3)
        sim_type: simulation type
        throat_elements: indices of throat elements for source
        use_burton_miller: use Burton-Miller formulation (avoids irregular frequencies)

    Returns:
        (spl_on_axis, throat_impedance, directivity_index)
    """
    omega = k * c
    boundary_interface = boundary_interface or boundary_device_interface()
    potential_interface = potential_interface or potential_device_interface()

    # P1 pressure everywhere; DP0 velocity restricted to tag=2 (throat disc) only.
    # segments=[2] means rigid walls (tag=1) and outer surfaces (tag=3) have no
    # velocity DOFs → implicit zero Neumann BC → only throat radiates.
    space_p = bempp_api.function_space(grid, "P", 1)
    space_u = bempp_api.function_space(grid, "DP", 0, segments=[2])

    identity = bempp_api.operators.boundary.sparse.identity(space_p, space_p, space_p)
    dlp = bempp_api.operators.boundary.helmholtz.double_layer(
        space_p, space_p, space_p, k, device_interface=boundary_interface
    )
    slp = bempp_api.operators.boundary.helmholtz.single_layer(
        space_u, space_p, space_p, k, device_interface=boundary_interface
    )

    # Explicit source-DOF excitation avoids orientation cancellation on symmetric meshes.
    u_total = _build_source_velocity(space_u, amplitude=1.0)
    neumann_fun = 1j * omega * rho * u_total

    if use_burton_miller:
        hyp = bempp_api.operators.boundary.helmholtz.hypersingular(
            space_p, space_p, space_p, k, device_interface=boundary_interface
        )
        adlp = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(
            space_u, space_p, space_p, k, device_interface=boundary_interface
        )
        rhs_identity = bempp_api.operators.boundary.sparse.identity(
            space_u, space_p, space_p
        )
        coupling = 1j / k
        lhs = 0.5 * identity - dlp - coupling * (-hyp)
        rhs = (-slp - coupling * (adlp + 0.5 * rhs_identity)) * neumann_fun
    else:
        lhs = dlp - 0.5 * identity
        rhs = slp * neumann_fun

    p_total, info = bempp_api.linalg.gmres(lhs, rhs, tol=1e-5)
    if info != 0:
        logger.warning("[BEM] GMRES did not converge (info=%d) at k=%.3f", info, k)

    frame = observation_frame if isinstance(observation_frame, dict) else infer_observation_frame(grid)
    origin_center = frame["origin_center"]
    obs_xyz = origin_center + frame["axis"] * float(observation_distance_m)
    obs_point = obs_xyz.reshape(3, 1)

    dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(
        space_p, obs_point, k, device_interface=potential_interface
    )
    slp_pot = bempp_api.operators.potential.helmholtz.single_layer(
        space_u, obs_point, k, device_interface=potential_interface
    )

    pressure_far = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total

    p_ref = 20e-6
    p_amplitude = np.abs(pressure_far[0, 0])
    spl = 20 * np.log10(p_amplitude / p_ref) if p_amplitude > 0 else 0.0

    impedance = calculate_throat_impedance(grid, p_total, throat_elements)

    di = calculate_directivity_index_from_pressure(
        grid, k, c, rho, p_total, u_total, space_p, space_u, omega, spl,
        device_interface=potential_interface,
        observation_frame=frame,
    )

    return float(spl), impedance, float(di)


def solve(
    mesh,
    frequency_range: List[float],
    num_frequencies: int,
    sim_type: str,
    polar_config: Optional[Dict] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    stage_callback: Optional[Callable[[str, Optional[float], Optional[str]], None]] = None,
    mesh_validation_mode: str = "warn",
    frequency_spacing: str = "linear",
    device_mode: str = "auto",
) -> Dict:
    """Run legacy BEM simulation path with explicit failure reporting."""
    if isinstance(mesh, dict):
        grid = mesh["grid"]
        throat_elements = mesh.get("throat_elements", np.array([]))
        wall_elements = mesh.get("wall_elements", np.array([]))
        mouth_elements = mesh.get("mouth_elements", np.array([]))
        unit_detection = mesh.get("unit_detection", {})
        logger.info(
            "[BEM] Mesh loaded: %d throat, %d wall, %d mouth elements",
            len(throat_elements), len(wall_elements), len(mouth_elements),
        )
    else:
        grid = mesh
        throat_elements = np.array([])
        unit_detection = {}

    num_frequencies = int(num_frequencies)
    if frequency_spacing == "log":
        frequencies = np.logspace(
            np.log10(frequency_range[0]), np.log10(frequency_range[1]), num_frequencies
        )
    else:
        frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)

    results = {
        "frequencies": frequencies.tolist(),
        "directivity": {"horizontal": [], "vertical": [], "diagonal": []},
        "spl_on_axis": {"frequencies": frequencies.tolist(), "spl": []},
        "impedance": {"frequencies": frequencies.tolist(), "real": [], "imaginary": []},
        "di": {"frequencies": frequencies.tolist(), "di": []},
        "metadata": {
            "solver_path": "legacy",
            "device_interface": selected_device_metadata(device_mode),
            "mesh_validation_mode": mesh_validation_mode,
            "unit_detection": unit_detection,
            "warnings": [],
            "warning_count": 0,
            "failures": [],
            "failure_count": 0,
            "partial_success": False,
        },
    }

    c = 343.0
    rho = 1.21
    boundary_interface = boundary_device_interface(device_mode)
    potential_interface = potential_device_interface(device_mode)
    observation_distance_m = _resolve_observation_distance_m(polar_config, default=1.0)
    observation_frame = infer_observation_frame(grid)

    success_count = 0
    opencl_safe_retry_consumed = False
    device_metadata = results.get("metadata", {}).get("device_interface")
    if stage_callback:
        stage_callback("setup", 1.0, "Legacy solver initialized")

    for i, freq in enumerate(frequencies):
        if progress_callback:
            progress_callback(i / len(frequencies))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                (i / len(frequencies)) if len(frequencies) > 0 else 1.0,
                f"Solving frequency {i + 1}/{len(frequencies)}",
            )

        logger.info("[BEM] Solving frequency %d/%d: %.1f Hz", i + 1, len(frequencies), freq)
        k = 2 * np.pi * freq / c

        try:
            spl, impedance, di = solve_frequency(
                grid,
                k,
                c,
                rho,
                sim_type,
                throat_elements=throat_elements,
                boundary_interface=boundary_interface,
                potential_interface=potential_interface,
                observation_distance_m=observation_distance_m,
                observation_frame=observation_frame,
            )
            success_count += 1
            results["spl_on_axis"]["spl"].append(float(spl))
            results["impedance"]["real"].append(float(impedance.real))
            results["impedance"]["imaginary"].append(float(impedance.imag))
            results["di"]["di"].append(float(di))
        except Exception as exc:
            if boundary_interface == "opencl" and is_opencl_buffer_error(exc):
                if isinstance(device_metadata, dict):
                    device_metadata["runtime_retry_attempted"] = True
                if not opencl_safe_retry_consumed:
                    opencl_safe_retry_consumed = True
                    safe_profile = configure_opencl_safe_profile()
                    if isinstance(device_metadata, dict):
                        device_metadata["runtime_profile"] = str(safe_profile.get("profile") or "safe_cpu")
                    logger.warning(
                        "[BEM] OpenCL runtime error at %.1f Hz; retrying with OpenCL safe CPU profile.", freq
                    )
                    try:
                        spl, impedance, di = solve_frequency(
                            grid,
                            k,
                            c,
                            rho,
                            sim_type,
                            throat_elements=throat_elements,
                            boundary_interface=boundary_interface,
                            potential_interface=potential_interface,
                            observation_distance_m=observation_distance_m,
                            observation_frame=observation_frame,
                        )
                        success_count += 1
                        if isinstance(device_metadata, dict):
                            device_metadata["runtime_retry_outcome"] = "opencl_recovered"
                            device_metadata["runtime_selected"] = "opencl"
                            device_metadata["interface"] = "opencl"
                            device_metadata["selected"] = "opencl"
                            device_metadata["selected_mode"] = "opencl_cpu"
                            device_metadata["device_type"] = "cpu"
                            if safe_profile.get("device_name"):
                                device_metadata["device_name"] = str(safe_profile.get("device_name"))
                            retry_detail = safe_profile.get("detail")
                            if retry_detail:
                                device_metadata["runtime_retry_detail"] = str(retry_detail)
                        results["spl_on_axis"]["spl"].append(float(spl))
                        results["impedance"]["real"].append(float(impedance.real))
                        results["impedance"]["imaginary"].append(float(impedance.imag))
                        results["di"]["di"].append(float(di))
                        continue
                    except Exception as retry_exc:
                        exc = retry_exc

                warning = {
                    "frequency_hz": float(freq),
                    "stage": "frequency_solve",
                    "code": "opencl_runtime_unavailable",
                    "detail": (
                        f"OpenCL buffer allocation failed and no numba fallback is enabled ({exc}). "
                        "Install/enable OpenCL drivers, then retry."
                    ),
                    "original_interface": "opencl",
                }
                results["metadata"]["warnings"].append(warning)
                results["metadata"]["warning_count"] = len(results["metadata"]["warnings"])
                logger.warning(
                    "[BEM] OpenCL runtime error at %.1f Hz; no numba fallback is enabled.", freq
                )
                if isinstance(device_metadata, dict):
                    device_metadata["runtime_selected"] = "opencl_unavailable"
                    device_metadata["runtime_fallback_reason"] = str(exc)
                    device_metadata["runtime_retry_outcome"] = "opencl_retry_failed"
                    device_metadata["interface"] = "unavailable"
                    device_metadata["selected"] = "opencl_unavailable"
                    device_metadata["fallback_reason"] = str(exc)

            logger.error("[BEM] Error at %.1f Hz: %s", freq, exc)
            results["metadata"]["failures"].append(
                frequency_failure(freq, "frequency_solve", "frequency_solve_failed", str(exc))
            )
            results["spl_on_axis"]["spl"].append(None)
            results["impedance"]["real"].append(None)
            results["impedance"]["imaginary"].append(None)
            results["di"]["di"].append(None)

    if stage_callback:
        stage_callback(
            "directivity",
            0.0,
            "Generating polar maps (horizontal/vertical/diagonal) and deriving DI from solved frequencies",
        )

    try:
        results["directivity"] = calculate_directivity_patterns(
            grid, frequencies, c, rho, sim_type, polar_config,
            observation_frame=observation_frame,
        )
    except Exception as exc:
        results["metadata"]["failures"].append(
            frequency_failure(float(frequencies[0]) if len(frequencies) else 0.0, "directivity", "directivity_failed", str(exc))
        )

    results["metadata"]["failure_count"] = len(results["metadata"]["failures"])
    results["metadata"]["partial_success"] = success_count > 0 and results["metadata"]["failure_count"] > 0

    if progress_callback:
        progress_callback(1.0)
    if stage_callback:
        stage_callback("directivity", 1.0, "Polar map and DI aggregation complete")
        stage_callback("finalizing", 1.0, "Packaging legacy solver results")

    return results
