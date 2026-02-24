"""
Optimized BEM solver with:
- Automatic symmetry detection and reduction
- Operator caching and reuse
- Configurable mesh/frequency safety policy
- Correct far-field polar evaluation
- Explicit failure reporting
"""

import inspect
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .contract import frequency_failure, normalize_mesh_validation_mode
from .deps import bempp_api
from .device_interface import (
    boundary_device_interface,
    configure_opencl_safe_profile,
    is_opencl_buffer_error,
    potential_device_interface,
    selected_device_metadata,
)
from .impedance import calculate_throat_impedance
from .directivity_correct import (
    calculate_directivity_patterns_correct,
    estimate_di_from_ka,
)
from .mesh_validation import calculate_mesh_statistics, validate_frequency_range
from .observation import infer_observation_frame
from .symmetry import (
    SymmetryType,
    apply_symmetry_reduction,
    check_excitation_symmetry,
    detect_geometric_symmetry,
    find_throat_center,
    validate_symmetry_reduction,
)


def _detect_gmres_kwargs() -> Dict[str, bool]:
    """Probe bempp_api.linalg.gmres signature once at import time."""
    if bempp_api is None:
        return {"use_strong_form": False, "return_iteration_count": False}
    try:
        params = inspect.signature(bempp_api.linalg.gmres).parameters
        return {
            "use_strong_form": "use_strong_form" in params,
            "return_iteration_count": "return_iteration_count" in params,
        }
    except (AttributeError, ValueError, TypeError):
        return {"use_strong_form": False, "return_iteration_count": False}


_GMRES_KWARGS = _detect_gmres_kwargs()


class CachedOperators:
    """Cache for BEMPP operators to reuse across frequencies."""

    def __init__(self, boundary_interface: str, potential_interface: str):
        self.grid = None
        self.space_p = None
        self.space_u = None
        self.identity = None
        self.rhs_identity = None
        self.boundary_interface = boundary_interface
        self.potential_interface = potential_interface
        self.dlp_cache = {}
        self.slp_cache = {}
        self.hyp_cache = {}
        self.adlp_cache = {}

    def get_or_create_spaces(self, grid):
        if self.grid is not grid or self.space_p is None:
            self.grid = grid
            self.space_p = bempp_api.function_space(grid, "P", 1)
            # DP0 velocity restricted to tag=2 (throat disc) only.
            # segments=[2] → rigid walls (tag=1) and outer surfaces (tag=3) have no
            # velocity DOFs → implicit zero Neumann BC → only throat radiates.
            self.space_u = bempp_api.function_space(grid, "DP", 0, segments=[2])
            self.identity = bempp_api.operators.boundary.sparse.identity(
                self.space_p, self.space_p, self.space_p
            )
            self.rhs_identity = bempp_api.operators.boundary.sparse.identity(
                self.space_u, self.space_p, self.space_p
            )
        return self.space_p, self.space_u, self.identity

    def get_or_create_operators(self, space_p, space_u, k: float, use_burton_miller: bool = True):
        k_key = f"{k:.6f}"
        if k_key not in self.dlp_cache:
            self.dlp_cache[k_key] = bempp_api.operators.boundary.helmholtz.double_layer(
                space_p, space_p, space_p, k, device_interface=self.boundary_interface
            )
            self.slp_cache[k_key] = bempp_api.operators.boundary.helmholtz.single_layer(
                space_u, space_p, space_p, k, device_interface=self.boundary_interface
            )
            if use_burton_miller:
                self.hyp_cache[k_key] = bempp_api.operators.boundary.helmholtz.hypersingular(
                    space_p, space_p, space_p, k, device_interface=self.boundary_interface
                )
                self.adlp_cache[k_key] = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(
                    space_u, space_p, space_p, k, device_interface=self.boundary_interface
                )
        return (
            self.dlp_cache[k_key],
            self.slp_cache[k_key],
            self.hyp_cache.get(k_key),
            self.adlp_cache.get(k_key),
        )


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


def apply_neumann_bc_on_symmetry_planes(grid, symmetry_info: Optional[Dict]) -> None:
    if symmetry_info is None or symmetry_info.get("symmetry_face_tag") is None:
        return
    print(f"[BEM] Symmetry planes detected (tag {symmetry_info['symmetry_face_tag']})")
    print("[BEM] Neumann BC (rigid) applied implicitly on symmetry planes")


def solve_frequency_cached(
    grid,
    k: float,
    c: float,
    rho: float,
    sim_type: str,
    cached_ops: CachedOperators,
    throat_elements: np.ndarray = None,
    use_burton_miller: bool = True,
    observation_distance_m: float = 1.0,
    observation_frame: Optional[Dict[str, np.ndarray]] = None,
) -> Tuple[float, complex, float, tuple, Optional[int]]:
    """Solve one frequency using cached operators and SI units."""
    omega = k * c

    space_p, space_u, identity = cached_ops.get_or_create_spaces(grid)
    ops = cached_ops.get_or_create_operators(space_p, space_u, k, use_burton_miller)

    u_total = _build_source_velocity(space_u, amplitude=1.0)
    neumann_fun = 1j * omega * rho * u_total

    if use_burton_miller and len(ops) == 4:
        dlp, slp, hyp, adlp = ops
        coupling = 1j / k
        lhs = 0.5 * identity - dlp - coupling * (-hyp)
        rhs = (-slp - coupling * (adlp + 0.5 * cached_ops.rhs_identity)) * neumann_fun
    else:
        dlp, slp = ops[:2]
        lhs = dlp - 0.5 * identity
        rhs = slp * neumann_fun

    gmres_call_kwargs: Dict = {"tol": 1e-5}
    if _GMRES_KWARGS["use_strong_form"]:
        gmres_call_kwargs["use_strong_form"] = True
    if _GMRES_KWARGS["return_iteration_count"]:
        gmres_call_kwargs["return_iteration_count"] = True

    gmres_result = bempp_api.linalg.gmres(lhs, rhs, **gmres_call_kwargs)
    if _GMRES_KWARGS["return_iteration_count"]:
        p_total, info, iter_count = gmres_result
    else:
        p_total, info = gmres_result
        iter_count = None
    if info != 0:
        print(f"[BEM] Warning: GMRES did not converge (info={info}) at k={k:.3f}")

    frame = observation_frame if isinstance(observation_frame, dict) else infer_observation_frame(grid)
    origin_center = frame["origin_center"]
    obs_xyz = origin_center + frame["axis"] * float(observation_distance_m)
    obs_point = obs_xyz.reshape(3, 1)

    dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(
        space_p, obs_point, k, device_interface=cached_ops.potential_interface
    )
    slp_pot = bempp_api.operators.potential.helmholtz.single_layer(
        space_u, obs_point, k, device_interface=cached_ops.potential_interface
    )
    pressure_far = dlp_pot * p_total - 1j * omega * rho * slp_pot * u_total

    p_ref = 20e-6
    p_amplitude = np.abs(pressure_far[0, 0])
    spl = 20 * np.log10(p_amplitude / p_ref) if p_amplitude > 0 else 0.0

    impedance = calculate_throat_impedance(grid, p_total, throat_elements)

    # Quick ka-based DI estimate (no potential operators needed).
    # Accurate DI is computed later from the batched directivity patterns.
    di = estimate_di_from_ka(grid, k, observation_frame=frame)

    return float(spl), impedance, float(di), (p_total, u_total, space_p, space_u), int(iter_count)


def solve_optimized(
    mesh,
    frequency_range: List[float],
    num_frequencies: int,
    sim_type: str,
    polar_config: Optional[Dict] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    stage_callback: Optional[Callable[[str, Optional[float], Optional[str]], None]] = None,
    enable_symmetry: bool = True,
    symmetry_tolerance: float = 1e-3,
    verbose: bool = True,
    mesh_validation_mode: str = "warn",
    use_burton_miller: bool = True,
    frequency_spacing: str = "linear",
    device_mode: str = "auto",
    enable_warmup: bool = True,
) -> Dict:
    """Run optimized BEM simulation with explicit metadata and failure reporting."""
    start_time = time.time()
    mesh_validation_mode = normalize_mesh_validation_mode(mesh_validation_mode)

    if isinstance(mesh, dict):
        grid = mesh["grid"]
        throat_elements = mesh.get("throat_elements", np.array([]))
        original_vertices = mesh.get("original_vertices")
        original_indices = mesh.get("original_indices")
        original_tags = mesh.get("original_surface_tags")
        unit_detection = mesh.get("unit_detection", {})
        mesh_metadata = mesh.get("mesh_metadata", {})
    else:
        grid = mesh
        throat_elements = np.array([])
        original_vertices = getattr(grid, "vertices", None)
        original_indices = getattr(grid, "elements", None)
        original_tags = getattr(grid, "domain_indices", None)
        unit_detection = {}
        mesh_metadata = {}

    c = 343.0
    rho = 1.21
    boundary_interface = boundary_device_interface(device_mode)
    potential_interface = potential_device_interface(device_mode)
    observation_distance_m = _resolve_observation_distance_m(polar_config, default=1.0)

    num_frequencies = int(num_frequencies)
    if frequency_spacing == "log":
        frequencies = np.logspace(
            np.log10(frequency_range[0]), np.log10(frequency_range[1]), num_frequencies
        )
    else:
        frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)

    symmetry_info = None
    reduction_factor = 1.0
    if stage_callback:
        stage_callback("setup", 0.0, "Preparing optimized solver")

    if enable_symmetry and original_vertices is not None:
        if verbose:
            print("\n" + "=" * 70)
            print("SYMMETRY DETECTION")
            print("=" * 70)

        try:
            symmetry_type, symmetry_planes = detect_geometric_symmetry(
                original_vertices, tolerance=symmetry_tolerance
            )

            if symmetry_type != SymmetryType.FULL:
                throat_center = find_throat_center(
                    original_vertices, throat_elements, original_indices
                )
                excitation_ok = check_excitation_symmetry(
                    throat_center, symmetry_planes, tolerance=1e-3
                )

                if excitation_ok:
                    if verbose:
                        print(f"[BEM] Symmetry detected: {symmetry_type.value}")
                        print(f"[BEM] Excitation centered: {throat_center}")

                    reduced_v, reduced_i, reduced_tags, symmetry_info = apply_symmetry_reduction(
                        original_vertices, original_indices, original_tags, symmetry_type, symmetry_planes
                    )
                    grid = bempp_api.grid_from_element_data(reduced_v, reduced_i, reduced_tags)
                    reduction_factor = float(symmetry_info["reduction_factor"])
                    validate_symmetry_reduction(symmetry_info, verbose=verbose)
                    apply_neumann_bc_on_symmetry_planes(grid, symmetry_info)
                    # Re-identify throat elements in reduced grid
                    throat_elements = np.where(reduced_tags == 2)[0]
                elif verbose:
                    print(f"[BEM] Symmetry rejected: excitation not centered ({throat_center})")
            elif verbose:
                print("[BEM] No symmetry detected - using full model")
        except Exception as exc:
            if verbose:
                print(f"[BEM] Symmetry detection failed: {exc}")
                print("[BEM] Falling back to full model")

    mesh_validation = {
        "mode": mesh_validation_mode,
        "enabled": mesh_validation_mode != "off",
        "is_valid": True,
        "warnings": [],
        "recommendations": [],
        "max_valid_frequency": None,
        "recommended_max_frequency": None,
        "elements_per_wavelength_at_max": None,
    }

    if mesh_validation_mode != "off":
        try:
            mesh_stats = calculate_mesh_statistics(grid.vertices, grid.elements)
            validation = validate_frequency_range(
                mesh_stats, (frequency_range[0], frequency_range[1]), c, elements_per_wavelength=6.0
            )
            mesh_validation.update(
                {
                    "is_valid": bool(validation.get("is_valid", True)),
                    "warnings": list(validation.get("warnings", [])),
                    "recommendations": list(validation.get("recommendations", [])),
                    "max_valid_frequency": validation.get("max_valid_frequency"),
                    "recommended_max_frequency": validation.get("recommended_max_frequency"),
                    "elements_per_wavelength_at_max": validation.get("elements_per_wavelength_at_max"),
                }
            )
            if mesh_validation_mode == "strict" and not mesh_validation["is_valid"]:
                warning_text = " | ".join(mesh_validation["warnings"]) or "mesh/frequency safety validation failed"
                raise ValueError(warning_text)
        except Exception as exc:
            if mesh_validation_mode == "strict":
                raise
            mesh_validation["warnings"].append(f"mesh validation unavailable: {exc}")

    axisymmetric_info = {
        "eligible": False,
        "reason": "feature_flag_disabled",
        "checks": {
            "feature_enabled": False,
            "sim_type": str(sim_type),
            "full_circle": bool((mesh_metadata or {}).get("fullCircle", False)),
        },
    }
    observation_frame = infer_observation_frame(grid)

    results = {
        "frequencies": frequencies.tolist(),
        "directivity": {"horizontal": [], "vertical": [], "diagonal": []},
        "spl_on_axis": {"frequencies": frequencies.tolist(), "spl": []},
        "impedance": {"frequencies": frequencies.tolist(), "real": [], "imaginary": []},
        "di": {"frequencies": frequencies.tolist(), "di": []},
        "metadata": {
            "symmetry": symmetry_info if symmetry_info else {"symmetry_type": "full", "reduction_factor": 1.0},
            "axisymmetric": axisymmetric_info,
            "device_interface": selected_device_metadata(device_mode),
            "mesh_validation": mesh_validation,
            "unit_detection": unit_detection,
            "warnings": [],
            "warning_count": 0,
            "failures": [],
            "failure_count": 0,
            "partial_success": False,
            "performance": {},
        },
    }

    cached_ops = CachedOperators(
        boundary_interface=boundary_interface,
        potential_interface=potential_interface,
    )
    solutions: List[Optional[tuple]] = []
    gmres_iterations: List[Optional[int]] = []
    opencl_safe_retry_consumed = False

    # Warm-up: front-load one-time JIT/OpenCL kernel compilation costs before
    # the timed frequency loop by assembling operators at a representative wavenumber.
    warmup_time_seconds = 0.0
    if enable_warmup and len(frequencies) > 0:
        _warmup_start = time.time()
        try:
            _k_warmup = 2 * np.pi * frequencies[len(frequencies) // 2] / c
            _sp, _su, _id = cached_ops.get_or_create_spaces(grid)
            _ops = cached_ops.get_or_create_operators(_sp, _su, _k_warmup, use_burton_miller)
            _dlp = _ops[0]
            _slp = _ops[1]
            if use_burton_miller and _ops[2] is not None:
                _hyp = _ops[2]
                _coupling = 1j / _k_warmup
                _lhs = 0.5 * _id - _dlp - _coupling * (-_hyp)
            else:
                _lhs = _dlp - 0.5 * _id
            _ = _lhs.strong_form()  # triggers assembly + OpenCL/numba compilation
            warmup_time_seconds = time.time() - _warmup_start
            if verbose:
                print(f"[BEM] Warm-up complete ({warmup_time_seconds:.2f}s)")
        except Exception as _warmup_exc:
            warmup_time_seconds = time.time() - _warmup_start
            if verbose:
                print(f"[BEM] Warm-up skipped ({_warmup_exc})")

    freq_start_time = time.time()
    success_count = 0
    device_metadata = results.get("metadata", {}).get("device_interface")

    for i, freq in enumerate(frequencies):
        if progress_callback:
            progress_callback(i / len(frequencies))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                (i / len(frequencies)) if len(frequencies) > 0 else 1.0,
                f"Solving frequency {i + 1}/{len(frequencies)}",
            )

        if verbose:
            print(f"[BEM] Solving {i + 1}/{len(frequencies)}: {freq:.1f} Hz", end="")

        k = 2 * np.pi * freq / c

        try:
            iter_start = time.time()
            spl, impedance, di, solution, iter_count = solve_frequency_cached(
                grid, k, c, rho, sim_type, cached_ops, throat_elements, use_burton_miller,
                observation_distance_m=observation_distance_m,
                observation_frame=observation_frame,
            )
            iter_time = time.time() - iter_start
            success_count += 1

            if verbose:
                print(f" -> {spl:.1f} dB, DI={di:.1f} dB, iters={iter_count} ({iter_time:.2f}s)")

            results["spl_on_axis"]["spl"].append(float(spl))
            results["impedance"]["real"].append(float(impedance.real))
            results["impedance"]["imaginary"].append(float(impedance.imag))
            results["di"]["di"].append(float(di))
            solutions.append(solution)
            gmres_iterations.append(iter_count)
        except Exception as exc:
            if boundary_interface == "opencl" and is_opencl_buffer_error(exc):
                if isinstance(device_metadata, dict):
                    device_metadata["runtime_retry_attempted"] = True
                if not opencl_safe_retry_consumed:
                    opencl_safe_retry_consumed = True
                    safe_profile = configure_opencl_safe_profile()
                    if isinstance(device_metadata, dict):
                        device_metadata["runtime_profile"] = str(safe_profile.get("profile") or "safe_cpu")
                    cached_ops = CachedOperators(
                        boundary_interface=boundary_interface,
                        potential_interface=potential_interface,
                    )
                    print(
                        f"[BEM] OpenCL runtime error at {freq:.1f} Hz; "
                        "retrying with OpenCL safe CPU profile."
                    )
                    try:
                        iter_start = time.time()
                        spl, impedance, di, solution, iter_count = solve_frequency_cached(
                            grid, k, c, rho, sim_type, cached_ops, throat_elements,
                            observation_distance_m=observation_distance_m,
                            observation_frame=observation_frame,
                        )
                        iter_time = time.time() - iter_start
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
                        if verbose:
                            print(f" -> {spl:.1f} dB, DI={di:.1f} dB, iters={iter_count} ({iter_time:.2f}s)")
                        results["spl_on_axis"]["spl"].append(float(spl))
                        results["impedance"]["real"].append(float(impedance.real))
                        results["impedance"]["imaginary"].append(float(impedance.imag))
                        results["di"]["di"].append(float(di))
                        solutions.append(solution)
                        gmres_iterations.append(iter_count)
                        continue
                    except Exception as retry_exc:
                        exc = retry_exc

                warning = {
                    "frequency_hz": float(freq),
                    "stage": "frequency_solve",
                    "code": "opencl_runtime_fallback_to_numba",
                    "detail": f"OpenCL buffer allocation failed; retrying this frequency with numba ({exc}).",
                    "original_interface": "opencl",
                    "fallback_interface": "numba",
                }
                results["metadata"]["warnings"].append(warning)
                results["metadata"]["warning_count"] = len(results["metadata"]["warnings"])
                print(
                    f"[BEM] OpenCL runtime error at {freq:.1f} Hz; "
                    "falling back to numba and retrying."
                )
                boundary_interface = "numba"
                potential_interface = "numba"
                cached_ops = CachedOperators(
                    boundary_interface=boundary_interface,
                    potential_interface=potential_interface,
                )
                if isinstance(device_metadata, dict):
                    device_metadata["runtime_selected"] = "numba"
                    device_metadata["runtime_fallback_reason"] = str(exc)
                    device_metadata["runtime_retry_outcome"] = "fell_back_to_numba"
                    device_metadata["selected_mode"] = "numba"
                    device_metadata["interface"] = "numba"
                    device_metadata["selected"] = "numba"
                    device_metadata["device_type"] = "cpu"
                    device_metadata["device_name"] = "Numba CPU"
                    device_metadata["fallback_reason"] = str(exc)
                try:
                    iter_start = time.time()
                    spl, impedance, di, solution, iter_count = solve_frequency_cached(
                        grid, k, c, rho, sim_type, cached_ops, throat_elements,
                        observation_distance_m=observation_distance_m,
                        observation_frame=observation_frame,
                    )
                    iter_time = time.time() - iter_start
                    success_count += 1
                    if verbose:
                        print(f" -> {spl:.1f} dB, DI={di:.1f} dB, iters={iter_count} ({iter_time:.2f}s)")
                    results["spl_on_axis"]["spl"].append(float(spl))
                    results["impedance"]["real"].append(float(impedance.real))
                    results["impedance"]["imaginary"].append(float(impedance.imag))
                    results["di"]["di"].append(float(di))
                    solutions.append(solution)
                    gmres_iterations.append(iter_count)
                    continue
                except Exception as retry_exc:
                    exc = retry_exc

            print(f" ERROR: {exc}")
            results["metadata"]["failures"].append(
                frequency_failure(freq, "frequency_solve", "frequency_solve_failed", str(exc))
            )
            results["spl_on_axis"]["spl"].append(None)
            results["impedance"]["real"].append(None)
            results["impedance"]["imaginary"].append(None)
            results["di"]["di"].append(None)
            solutions.append(None)
            gmres_iterations.append(None)

    freq_solve_time = time.time() - freq_start_time

    if verbose:
        print("\n[BEM] Computing directivity patterns...")
    if stage_callback:
        stage_callback(
            "directivity",
            0.0,
            "Generating polar maps (horizontal/vertical/diagonal) and deriving DI from solved frequencies",
        )

    directivity_start = time.time()

    # Detect total failure: if no frequency solved successfully, raise so that
    # the job is marked as 'error' instead of 'complete' with all-null arrays.
    if success_count == 0:
        failure_msgs = [f.get("detail", "unknown") for f in results["metadata"]["failures"][:3]]
        raise RuntimeError(
            f"All {len(frequencies)} frequencies failed to solve. "
            f"First failure(s): {'; '.join(failure_msgs)}"
        )

    valid_solutions = [(i, sol) for i, sol in enumerate(solutions) if sol is not None]
    if len(valid_solutions) > 0:
        indices, filtered_solutions = zip(*valid_solutions)
        filtered_freqs = frequencies[list(indices)]
        try:
            filtered_directivity = calculate_directivity_patterns_correct(
                grid, filtered_freqs, c, rho, list(filtered_solutions), polar_config,
                device_interface=potential_interface,
                observation_frame=observation_frame,
            )

            # Expand filtered directivity back to full frequency array so that
            # results["directivity"][plane][i] corresponds to results["frequencies"][i].
            # Failed frequencies get a null-placeholder pattern.
            _angle_count = 37
            _angle_start = 0.0
            _angle_end = 180.0
            if polar_config:
                ar = polar_config.get("angle_range", [0, 180, 37])
                _angle_start, _angle_end, _angle_count = float(ar[0]), float(ar[1]), int(ar[2])

            for plane in ("horizontal", "vertical", "diagonal"):
                filtered_patterns = filtered_directivity.get(plane, [])
                full_patterns = [None] * len(frequencies)
                for vi, global_i in enumerate(indices):
                    if vi < len(filtered_patterns):
                        full_patterns[global_i] = filtered_patterns[vi]
                # Fill gaps with null-placeholder so frontend can detect and skip
                placeholder_angles = np.linspace(_angle_start, _angle_end, _angle_count)
                placeholder = [[float(a), None] for a in placeholder_angles]
                for fi in range(len(full_patterns)):
                    if full_patterns[fi] is None:
                        full_patterns[fi] = placeholder
                results["directivity"][plane] = full_patterns

            # Refine DI from batched directivity patterns (replaces ka-based estimate).
            # Uses the on-axis SPL and the horizontal polar pattern that was already computed.
            for vi, global_i in enumerate(indices):
                spl_on_axis_val = results["spl_on_axis"]["spl"][global_i]
                if spl_on_axis_val is None:
                    continue
                h_pattern = results["directivity"]["horizontal"][global_i]
                if not h_pattern:
                    continue
                # Skip placeholder patterns (contain None dB values)
                if any(pt[1] is None for pt in h_pattern):
                    continue
                try:
                    # h_pattern is [[angle, dB_normalized], ...]; extract un-normalized SPL
                    angles_deg = np.array([pt[0] for pt in h_pattern])
                    spl_norm = np.array([pt[1] for pt in h_pattern])
                    # spl_norm is relative to norm_angle; recover absolute by adding on-axis SPL
                    # The norm_angle entry is 0 dB, and on-axis (0°) offset gives the shift
                    on_axis_idx = np.argmin(np.abs(angles_deg))
                    spl_abs = spl_norm - spl_norm[on_axis_idx] + spl_on_axis_val

                    # Integrate over hemisphere assuming axial symmetry of horizontal cut
                    theta_rad = np.deg2rad(angles_deg)
                    p_ref = 20e-6
                    p_vals = p_ref * 10 ** (spl_abs / 20)
                    intensities = p_vals ** 2
                    sin_theta = np.sin(theta_rad)
                    sin_theta = np.maximum(sin_theta, 0.01)

                    # Trapezoidal integration weighted by sin(theta)
                    avg_intensity = np.trapz(intensities * sin_theta, theta_rad)
                    total_weight = np.trapz(sin_theta, theta_rad)
                    if total_weight > 0 and avg_intensity > 0:
                        avg_i = avg_intensity / total_weight
                        p_on_axis = p_ref * 10 ** (spl_on_axis_val / 20)
                        di = 10 * np.log10(p_on_axis ** 2 / avg_i)
                        results["di"]["di"][global_i] = float(max(0.0, min(30.0, di)))
                except Exception:
                    pass  # Keep the ka-based estimate

        except Exception as exc:
            results["metadata"]["failures"].append(
                frequency_failure(
                    float(filtered_freqs[0]), "directivity", "directivity_failed", str(exc)
                )
            )

    directivity_time = time.time() - directivity_start
    total_time = time.time() - start_time

    results["metadata"]["failure_count"] = len(results["metadata"]["failures"])
    results["metadata"]["partial_success"] = success_count > 0 and results["metadata"]["failure_count"] > 0
    valid_iterations = [n for n in gmres_iterations if n is not None]
    avg_gmres = sum(valid_iterations) / len(valid_iterations) if valid_iterations else 0.0
    results["metadata"]["performance"] = {
        "total_time_seconds": total_time,
        "frequency_solve_time": freq_solve_time,
        "directivity_compute_time": directivity_time,
        "time_per_frequency": freq_solve_time / len(frequencies) if len(frequencies) > 0 else 0,
        "reduction_speedup": reduction_factor,
        "warmup_time_seconds": warmup_time_seconds,
        "gmres_iterations_per_frequency": gmres_iterations,
        "avg_gmres_iterations": round(avg_gmres, 1),
        "gmres_strong_form_supported": _GMRES_KWARGS["use_strong_form"],
    }

    if verbose:
        print("\n" + "=" * 70)
        print("SIMULATION COMPLETE")
        print("=" * 70)
        print(f"Total time: {total_time:.1f}s")
        if warmup_time_seconds > 0:
            print(f"Warm-up: {warmup_time_seconds:.2f}s")
        if len(frequencies) > 0:
            print(
                f"Frequency solve: {freq_solve_time:.1f}s "
                f"({freq_solve_time / len(frequencies):.2f}s per frequency)"
            )
        if valid_iterations:
            print(f"GMRES iterations: avg={avg_gmres:.1f}, min={min(valid_iterations)}, max={max(valid_iterations)}")
        print(f"Directivity compute: {directivity_time:.1f}s")
        if reduction_factor > 1.0:
            print(f"Symmetry speedup: {reduction_factor:.1f}x")
        print("=" * 70 + "\n")

    if progress_callback:
        progress_callback(1.0)
    if stage_callback:
        stage_callback("directivity", 1.0, "Polar map and DI aggregation complete")
        stage_callback("finalizing", 1.0, "Packaging optimized solver results")

    return results
