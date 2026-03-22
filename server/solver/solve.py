"""
HornBEMSolver: Optimized BEM solver for waveguide/horn acoustic simulations.

Class-based design pre-computes function spaces and identity operators once
so the per-frequency solve loop avoids redundant operator setup.

Key behaviours:
- Full DP0 velocity space; throat DOFs driven, all others zeroed (inert rigid wall).
- Burton-Miller BIE formulation by default (avoids fictitious resonances).
- Batched far-field evaluation for horizontal + vertical polar patterns.
- Parallel frequency sweep via ProcessPoolExecutor (optional, configurable workers).
- Preserves all metadata/failure/cancellation/opencl-retry contracts expected by the API.

The module also re-exports the solve_optimized() function so that BEMSolver.solve()
can continue using it unchanged.
"""

import inspect
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

import multiprocessing as mp
import numpy as np

from .contract import (
    build_directivity_metadata,
    frequency_failure,
    normalize_mesh_validation_mode,
    normalize_directivity_planes,
)
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
from .observation import infer_observation_frame, resolve_safe_observation_distance


# ---------------------------------------------------------------------------
# GMRES kwargs detection
# ---------------------------------------------------------------------------

def _detect_gmres_return_iteration_count() -> bool:
    """Probe bempp_api.linalg.gmres signature once at import time."""
    if bempp_api is None:
        return False
    try:
        params = inspect.signature(bempp_api.linalg.gmres).parameters
        return "return_iteration_count" in params
    except (AttributeError, ValueError, TypeError):
        return False


_GMRES_RETURN_ITERATION_COUNT = _detect_gmres_return_iteration_count()
_VALID_BEM_PRECISIONS = {"single", "double"}


def _normalize_bem_precision(value: Optional[str]) -> str:
    raw = str(value or "single").strip().lower()
    if raw not in _VALID_BEM_PRECISIONS:
        raise ValueError("bem_precision must be one of: single, double.")
    return raw


def _configure_bempp_precision(precision: str) -> None:
    if bempp_api is not None and hasattr(bempp_api, "DEFAULT_PRECISION"):
        setattr(bempp_api, "DEFAULT_PRECISION", precision)


def _numpy_dtype_for_precision(precision: str) -> type:
    """
    Return the numpy complex dtype corresponding to a BEM precision setting.

    Args:
        precision: "single" or "double" (any casing)

    Returns:
        np.complex64 for "single", np.complex128 for "double"
    """
    normalized = _normalize_bem_precision(precision)
    return np.complex64 if normalized == "single" else np.complex128


def _operator_kwargs(device_interface: str, precision: str) -> Dict[str, str]:
    return {
        "device_interface": device_interface,
        "precision": precision,
    }


# ---------------------------------------------------------------------------
# Parallel frequency chunk helper (module-level, picklable)
# ---------------------------------------------------------------------------

def _solve_frequency_chunk(solver_cfg: dict, frequencies: Sequence[float]):
    """
    Worker entry point for parallel frequency evaluation.
    solver_cfg is a plain dict with all config needed to re-construct HornBEMSolver.
    This function runs in a spawned child process.
    """
    child_solver = HornBEMSolver.from_config(solver_cfg)
    observation_distance_m = float(solver_cfg.get("observation_distance_m", 2.0))
    return child_solver.solve_frequencies(
        list(frequencies),
        show_progress=False,
        observation_distance_m=observation_distance_m,
    )


def _split_frequencies_evenly(
    frequencies: np.ndarray, worker_count: int
) -> List[np.ndarray]:
    if worker_count <= 1 or len(frequencies) == 0:
        return [frequencies]
    return [chunk for chunk in np.array_split(frequencies, worker_count) if len(chunk) > 0]


# ---------------------------------------------------------------------------
# HornBEMSolver
# ---------------------------------------------------------------------------

class HornBEMSolver:
    """
    BEM acoustic solver for horn/waveguide simulations.

    Pre-computes function spaces and identity operators on construction;
    amortises that cost over the full frequency sweep.

    Args:
        grid: bempp Grid object (vertices in metres).
        physical_tags: np.ndarray shape (num_triangles,) — physical surface tag per triangle.
        sound_speed: Speed of sound in m/s (default 343.0).
        rho: Air density in kg/m³ (default 1.21).
        tag_throat: Physical tag value for the driven disc (default 2).
        boundary_interface: BEMPP device interface for boundary operators.
        potential_interface: BEMPP device interface for potential operators.
        bem_precision: 'single' or 'double' (default 'single').
        use_burton_miller: Use Burton-Miller BIE formulation (default True).
    """

    def __init__(
        self,
        grid,
        physical_tags: np.ndarray,
        sound_speed: float = 343.0,
        rho: float = 1.21,
        tag_throat: int = 2,
        boundary_interface: str = "opencl",
        potential_interface: str = "opencl",
        bem_precision: str = "single",
        use_burton_miller: bool = True,
    ):
        self.grid = grid
        self.physical_tags = np.asarray(physical_tags, dtype=np.int32)
        self.c = float(sound_speed)
        self.rho = float(rho)
        self.tag_throat = int(tag_throat)
        self.boundary_interface = boundary_interface
        self.potential_interface = potential_interface
        self.bem_precision = bem_precision
        self.use_burton_miller = use_burton_miller

        # Pre-compute spaces (full mesh — velocity zeroed outside throat)
        self.p1_space = bempp_api.function_space(grid, "P", 1)
        self.dp0_space = bempp_api.function_space(grid, "DP", 0)

        # Identity operators (frequency-independent)
        self.lhs_identity = bempp_api.operators.boundary.sparse.identity(
            self.p1_space, self.p1_space, self.p1_space
        )
        self.rhs_identity = bempp_api.operators.boundary.sparse.identity(
            self.dp0_space, self.p1_space, self.p1_space
        )

        # Geometry setup
        self._setup_driver_geometry()

        # Unit velocity excitation (amplitude 1 m/s, scaled later)
        self.unit_velocity_fun = self._create_unit_velocity()

    @classmethod
    def from_config(cls, cfg: dict) -> "HornBEMSolver":
        """
        Reconstruct a HornBEMSolver from a plain-dict config (used in spawned workers).

        The config dict must contain:
        - 'vertices': (3, N) array
        - 'elements': (3, M) array of int32
        - 'physical_tags': (M,) array of int32
        - and all scalar solver params
        """
        vertices = np.asarray(cfg["vertices"])
        elements = np.asarray(cfg["elements"], dtype=np.int32)
        physical_tags = np.asarray(cfg["physical_tags"], dtype=np.int32)
        grid = bempp_api.Grid(vertices, elements, physical_tags)
        return cls(
            grid=grid,
            physical_tags=physical_tags,
            sound_speed=cfg.get("sound_speed", 343.0),
            rho=cfg.get("rho", 1.21),
            tag_throat=cfg.get("tag_throat", 2),
            boundary_interface=cfg.get("boundary_interface", "opencl"),
            potential_interface=cfg.get("potential_interface", "opencl"),
            bem_precision=cfg.get("bem_precision", "single"),
            use_burton_miller=cfg.get("use_burton_miller", True),
        )

    def to_config(self) -> dict:
        """Serialise solver config to a plain dict for subprocess spawning."""
        return {
            "vertices": self.grid.vertices,
            "elements": self.grid.elements,
            "physical_tags": self.physical_tags,
            "sound_speed": self.c,
            "rho": self.rho,
            "tag_throat": self.tag_throat,
            "boundary_interface": self.boundary_interface,
            "potential_interface": self.potential_interface,
            "bem_precision": self.bem_precision,
            "use_burton_miller": self.use_burton_miller,
        }

    # ------------------------------------------------------------------
    # Geometry setup
    # ------------------------------------------------------------------

    def _setup_driver_geometry(self) -> None:
        """Identify throat/enclosure elements and pre-compute throat geometry."""
        n_elements = self.dp0_space.global_dof_count
        tags = self.physical_tags

        # In DP0, DOFs map 1:1 to triangles
        if len(tags) != n_elements:
            # Graceful fallback: assume all elements use tag 2 if counts mismatch
            logger.warning(
                "[HornBEM] physical_tags length %d != DP0 DOF count %d; "
                "using tag-2 assumption for all elements.",
                len(tags), n_elements,
            )
            tags = np.full(n_elements, self.tag_throat, dtype=np.int32)
            self.physical_tags = tags

        self.driver_dofs = np.where(tags == self.tag_throat)[0]
        self.enclosure_dofs = np.where(tags != self.tag_throat)[0]

        if len(self.driver_dofs) == 0:
            raise ValueError(
                f"No throat elements found for tag_throat={self.tag_throat}. "
                "Check mesh physical tags."
            )

        # Areas of throat elements (grid.volumes gives triangle areas, shape (M,))
        self.throat_element_areas = self.grid.volumes[self.driver_dofs]

        # P1 global DOF indices for each throat element: shape (num_throat, 3)
        self.throat_p1_dofs = self.p1_space.local2global[self.driver_dofs]

        logger.info(
            "[HornBEM] Driven surface: %d elements.  Enclosure: %d elements.",
            len(self.driver_dofs), len(self.enclosure_dofs),
        )

    def _create_unit_velocity(self):
        """Create DP0 GridFunction: 1 m/s on throat elements, 0 elsewhere."""
        coeffs = np.zeros(self.dp0_space.global_dof_count, dtype=_numpy_dtype_for_precision(self.bem_precision))
        coeffs[self.driver_dofs] = 1.0
        return bempp_api.GridFunction(self.dp0_space, coefficients=coeffs)

    # ------------------------------------------------------------------
    # Single-frequency solve
    # ------------------------------------------------------------------

    def _solve_single_frequency(
        self,
        freq: float,
        observation_frame: Optional[Dict] = None,
        observation_distance_m: float = 2.0,
        polar_evaluation_points: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    ) -> Tuple[float, complex, float, tuple, Optional[int]]:
        """
        Solve BEM at a single frequency.

        Args:
            freq: Frequency in Hz.
            observation_frame: Inferred mesh frame dict (axis, origin_center, etc.)
            observation_distance_m: Far-field evaluation distance in metres.
            polar_evaluation_points: Tuple (horizontal_pts, vertical_pts), each (3, N).
                If None, only the on-axis SPL point is evaluated.

        Returns:
            (spl_on_axis, impedance_complex, di_estimate, solution_tuple, iter_count)
            where solution_tuple = (p_total, u_total, p1_space, dp0_space)
        """
        omega = 2.0 * np.pi * freq
        k = omega / self.c

        velocity_fun = self.unit_velocity_fun
        neumann_fun = 1j * self.rho * omega * velocity_fun

        op_kwargs = _operator_kwargs(self.boundary_interface, self.bem_precision)

        dlp = bempp_api.operators.boundary.helmholtz.double_layer(
            self.p1_space, self.p1_space, self.p1_space, k, **op_kwargs
        )
        slp = bempp_api.operators.boundary.helmholtz.single_layer(
            self.dp0_space, self.p1_space, self.p1_space, k, **op_kwargs
        )

        if self.use_burton_miller:
            hyp = bempp_api.operators.boundary.helmholtz.hypersingular(
                self.p1_space, self.p1_space, self.p1_space, k, **op_kwargs
            )
            adlp = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(
                self.dp0_space, self.p1_space, self.p1_space, k, **op_kwargs
            )
            coupling = 1j / k
            lhs = 0.5 * self.lhs_identity - dlp - coupling * (-hyp)
            rhs = (-slp - coupling * (adlp + 0.5 * self.rhs_identity)) * neumann_fun
        else:
            lhs = dlp - 0.5 * self.lhs_identity
            rhs = slp * neumann_fun

        gmres_call_kwargs: Dict = {"tol": 1e-5}
        if _GMRES_RETURN_ITERATION_COUNT:
            gmres_call_kwargs["return_iteration_count"] = True

        gmres_result = bempp_api.linalg.gmres(lhs, rhs, **gmres_call_kwargs)
        if _GMRES_RETURN_ITERATION_COUNT:
            p_total, info, iter_count = gmres_result
        else:
            p_total, info = gmres_result
            iter_count = None
        if info != 0:
            logger.warning("[HornBEM] GMRES did not converge (info=%d) at %.1f Hz", info, freq)

        # On-axis SPL
        frame = (
            observation_frame
            if isinstance(observation_frame, dict)
            else infer_observation_frame(self.grid)
        )
        origin_center = frame["origin_center"]
        obs_xyz = origin_center + frame["axis"] * float(observation_distance_m)
        obs_point = obs_xyz.reshape(3, 1)

        pot_kwargs = _operator_kwargs(self.potential_interface, self.bem_precision)
        dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(
            self.p1_space, obs_point, k, **pot_kwargs
        )
        slp_pot = bempp_api.operators.potential.helmholtz.single_layer(
            self.dp0_space, obs_point, k, **pot_kwargs
        )
        pressure_on_axis = dlp_pot * p_total - slp_pot * neumann_fun

        p_ref = 20e-6
        p_amplitude = np.abs(pressure_on_axis[0, 0])
        spl = 20 * np.log10(p_amplitude / p_ref) if p_amplitude > 0 else 0.0

        # Impedance
        impedance = calculate_throat_impedance_horn(
            self.grid, p_total, self.driver_dofs,
            self.throat_p1_dofs, self.throat_element_areas
        )

        # Quick ka-based DI estimate; refined later from polar patterns
        di = estimate_di_from_ka(self.grid, k, observation_frame=frame)

        solution = (p_total, velocity_fun, self.p1_space, self.dp0_space)
        return float(spl), impedance, float(di), solution, iter_count

    # ------------------------------------------------------------------
    # Frequency sweep helpers
    # ------------------------------------------------------------------

    def solve_frequencies(
        self,
        frequencies: Sequence[float],
        show_progress: bool = True,
        observation_frame: Optional[Dict] = None,
        observation_distance_m: float = 2.0,
    ) -> Tuple[List[Tuple], List[Optional[int]]]:
        """
        Solve for a sequence of frequencies (single-process).

        Returns:
            (results_list, iteration_counts)
            results_list items: (freq, spl, impedance, di, solution_tuple) or None on failure
            iteration_counts: GMRES iteration count per frequency (or None on failure)
        """
        frequencies = np.asarray(frequencies, dtype=float)
        if observation_frame is None:
            observation_frame = infer_observation_frame(self.grid)

        results = []
        iter_counts = []
        for i, freq in enumerate(frequencies):
            if show_progress:
                logger.info("[HornBEM] [%d/%d] %.1f Hz", i + 1, len(frequencies), freq)
            try:
                spl, impedance, di, solution, iters = self._solve_single_frequency(
                    freq,
                    observation_frame=observation_frame,
                    observation_distance_m=observation_distance_m,
                )
                results.append((freq, spl, impedance, di, solution))
                iter_counts.append(iters)
            except Exception as exc:
                logger.error("[HornBEM] Error at %.1f Hz: %s", freq, exc)
                results.append(None)
                iter_counts.append(None)
        return results, iter_counts

    def _resolve_worker_count(self, frequency_count: int, requested: int) -> int:
        if requested < 1:
            raise ValueError("workers must be >= 1.")
        return min(requested, max(1, frequency_count), os.cpu_count() or 1)

    def solve_sweep_parallel(
        self,
        frequencies: np.ndarray,
        worker_count: int,
        observation_frame: Optional[Dict] = None,
        observation_distance_m: float = 2.0,
    ) -> Tuple[List, List]:
        """Parallel frequency sweep using spawned worker processes."""
        chunks = _split_frequencies_evenly(frequencies, worker_count)
        ctx = mp.get_context("spawn")
        cfg = self.to_config()
        # Inject observation params into config for workers
        cfg["observation_distance_m"] = observation_distance_m

        chunk_results: dict = {}
        chunk_iters: dict = {}
        completed = 0

        with ProcessPoolExecutor(max_workers=len(chunks), mp_context=ctx) as executor:
            futures = {
                executor.submit(_solve_frequency_chunk, cfg, chunk.tolist()): idx
                for idx, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                idx = futures[future]
                polar_chunk, iter_chunk = future.result()
                chunk_results[idx] = polar_chunk
                chunk_iters[idx] = iter_chunk
                completed += len(polar_chunk)
                logger.info(
                    "[HornBEM] [%d/%d] completed worker chunk %d/%d",
                    completed, len(frequencies), idx + 1, len(chunks),
                )

        all_results = []
        all_iters = []
        for idx in range(len(chunks)):
            all_results.extend(chunk_results[idx])
            all_iters.extend(chunk_iters[idx])

        return all_results, all_iters


# ---------------------------------------------------------------------------
# Impedance helper that uses pre-computed DOF indices (P1 local2global path)
# ---------------------------------------------------------------------------

def calculate_throat_impedance_horn(
    grid,
    pressure_solution,
    driver_dofs: np.ndarray,
    throat_p1_dofs: np.ndarray,
    throat_element_areas: np.ndarray,
) -> complex:
    """
    Impedance using P1 local2global DOF indices directly.

    This avoids both the 'coefficients index == vertex index' assumption
    and the need to evaluate on element centres.

    Args:
        grid: bempp Grid (not used for vertices — areas come from throat_element_areas)
        pressure_solution: bempp GridFunction with P1 coefficients.
        driver_dofs: Element indices of driven (throat) surface.
        throat_p1_dofs: shape (num_throat, 3) — P1 global DOF indices per throat element.
        throat_element_areas: shape (num_throat,) — triangle areas in m².

    Returns:
        complex: Specific acoustic impedance Z_s = <p> / u_n [Pa·s/m]
    """
    if len(driver_dofs) == 0:
        return complex(0.0, 0.0)

    # Extract P1 pressure coefficients at throat element DOFs
    coeffs = np.asarray(pressure_solution.coefficients)
    # throat_p1_dofs: (num_throat, 3) → average per element → (num_throat,)
    p_at_dofs = coeffs[throat_p1_dofs]       # (num_throat, 3)
    p_avg = np.mean(p_at_dofs, axis=1)       # (num_throat,)

    S_throat = np.sum(throat_element_areas)
    if S_throat == 0.0:
        return complex(0.0, 0.0)

    total_force = np.sum(p_avg * throat_element_areas)  # F = ∫p dA [N]
    Z_specific = total_force / S_throat                  # Z_s = F / (u·S) [Pa·s/m]
    return complex(np.real(Z_specific), np.imag(Z_specific))


# ---------------------------------------------------------------------------
# Public entry-point: solve_optimized (API-facing, same contract as before)
# ---------------------------------------------------------------------------

def solve_optimized(
    mesh,
    frequency_range: List[float],
    num_frequencies: int,
    sim_type: str,
    polar_config: Optional[Dict] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    stage_callback: Optional[Callable[[str, Optional[float], Optional[str]], None]] = None,
    verbose: bool = True,
    mesh_validation_mode: str = "warn",
    use_burton_miller: bool = True,
    bem_precision: str = "single",
    frequency_spacing: str = "linear",
    device_mode: str = "auto",
    enable_warmup: bool = False,
    cancellation_callback: Optional[Callable[[], None]] = None,
    workers: int = 1,
    quadrature_regular: Optional[int] = None,
    workgroup_size_multiple: Optional[int] = None,
    assembly_backend: Optional[str] = None,
) -> Dict:
    """
    Run HornBEMSolver frequency sweep and return the standard API result dict.

    This function is a drop-in replacement for the previous solve_optimized().
    The result dictionary structure is unchanged.
    """
    start_time = time.time()
    mesh_validation_mode = normalize_mesh_validation_mode(mesh_validation_mode)
    requested_bem_precision = _normalize_bem_precision(bem_precision)
    effective_bem_precision = "single"
    if requested_bem_precision != effective_bem_precision:
        logger.info(
            "[HornBEM] Ignoring compatibility bem_precision=%s; active runtime uses %s precision.",
            requested_bem_precision,
            effective_bem_precision,
        )
    if bool(enable_warmup):
        logger.info(
            "[HornBEM] Ignoring compatibility enable_warmup=%s; active runtime does not run warm-up.",
            bool(enable_warmup),
        )
    _configure_bempp_precision(effective_bem_precision)

    # Apply bempp global parameter overrides (these run inside the subprocess).
    if quadrature_regular is not None:
        qr = max(1, min(10, int(quadrature_regular)))
        bempp_api.GLOBAL_PARAMETERS.quadrature.regular = qr
        logger.info("[HornBEM] quadrature.regular = %d (default: 4)", qr)
    if workgroup_size_multiple is not None:
        wg = max(1, min(8, int(workgroup_size_multiple)))
        bempp_api.GLOBAL_PARAMETERS.assembly.dense.workgroup_size_multiple = wg
        logger.info("[HornBEM] workgroup_size_multiple = %d (default: 2)", wg)
    if assembly_backend is not None:
        ab = str(assembly_backend).strip().lower()
        if ab in ("opencl", "numba"):
            bempp_api.DEFAULT_DEVICE_INTERFACE = ab
            logger.info("[HornBEM] DEFAULT_DEVICE_INTERFACE = '%s'", ab)

    if isinstance(mesh, dict):
        grid = mesh["grid"]
        original_tags = mesh.get("original_surface_tags")
        unit_detection = mesh.get("unit_detection", {})
        mesh_metadata = mesh.get("mesh_metadata", {})
        # Physical tags for HornBEMSolver come from surface_tags in the mesh dict
        physical_tags = mesh.get("surface_tags", original_tags)
    else:
        grid = mesh
        original_tags = getattr(grid, "domain_indices", None)
        unit_detection = {}
        mesh_metadata = {}
        physical_tags = original_tags

    c = 343.0
    rho = 1.21
    effective_backend = str(assembly_backend or "").strip().lower()
    if effective_backend == "numba":
        boundary_interface = "numba"
        potential_interface = "numba"
    else:
        boundary_interface = boundary_device_interface(device_mode)
        potential_interface = potential_device_interface(device_mode)
    observation_request_m = _resolve_observation_distance_m(polar_config, default=2.0)

    num_frequencies = int(num_frequencies)
    if frequency_spacing == "log":
        frequencies = np.logspace(
            np.log10(frequency_range[0]), np.log10(frequency_range[1]), num_frequencies
        )
    else:
        frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)

    if stage_callback:
        stage_callback("setup", 0.0, "Preparing HornBEM solver")

    if cancellation_callback:
        cancellation_callback()

    # ------------------------------------------------------------------
    # Mesh validation
    # ------------------------------------------------------------------
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
                mesh_stats, (frequency_range[0], frequency_range[1]), c,
                elements_per_wavelength=6.0,
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
                warning_text = (
                    " | ".join(mesh_validation["warnings"])
                    or "mesh/frequency safety validation failed"
                )
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

    observation_origin = (
        str(polar_config.get("observation_origin", "mouth")).strip().lower()
        if isinstance(polar_config, dict)
        else "mouth"
    )
    observation_frame = infer_observation_frame(
        grid,
        observation_origin=observation_origin,
    )
    observation_info = resolve_safe_observation_distance(
        grid, observation_request_m, observation_frame
    )
    observation_distance_m = float(observation_info["effective_distance_m"])
    logger.info(
        "[HornBEM] observation: distance=%.3f m (requested=%.3f m, adjusted=%s), "
        "origin=%s, tag_distribution={1: %d, 2: %d, other: %d}",
        observation_distance_m,
        observation_request_m,
        observation_info.get("adjusted", False),
        observation_origin,
        int(np.count_nonzero(physical_tags == 1)) if physical_tags is not None else -1,
        int(np.count_nonzero(physical_tags == 2)) if physical_tags is not None else -1,
        int(np.count_nonzero((physical_tags != 1) & (physical_tags != 2))) if physical_tags is not None else -1,
    )
    effective_polar_config = dict(polar_config) if isinstance(polar_config, dict) else {}
    effective_polar_config["observation_origin"] = observation_origin
    effective_polar_config["distance"] = observation_distance_m

    requested_plane_specs = normalize_directivity_planes(effective_polar_config)
    requested_plane_ids = [str(spec["id"]) for spec in requested_plane_specs]

    results = {
        "frequencies": frequencies.tolist(),
        "directivity": {plane_id: [] for plane_id in requested_plane_ids},
        "spl_on_axis": {"frequencies": frequencies.tolist(), "spl": []},
        "impedance": {"frequencies": frequencies.tolist(), "real": [], "imaginary": []},
        "di": {"frequencies": frequencies.tolist(), "di": []},
        "metadata": {
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
            "observation": observation_info,
            "directivity": build_directivity_metadata(
                effective_polar_config, observation_info
            ),
        },
    }
    if observation_info["adjusted"]:
        results["metadata"]["warnings"].append(
            {
                "stage": "setup",
                "code": "observation_distance_adjusted",
                "detail": (
                    "Requested observation distance "
                    f"{observation_info['requested_distance_m']:.3f} m was inside or too close to the model. "
                    f"Using {observation_distance_m:.3f} m to keep the observer ahead of the baffle."
                ),
            }
        )
        results["metadata"]["warning_count"] = len(results["metadata"]["warnings"])

    if cancellation_callback:
        cancellation_callback()

    # ------------------------------------------------------------------
    # Build HornBEMSolver instance
    # ------------------------------------------------------------------
    if physical_tags is None:
        # Fallback: assume all elements tagged as throat (degenerate but safe)
        physical_tags = np.full(grid.elements.shape[1], 2, dtype=np.int32)

    solver = HornBEMSolver(
        grid=grid,
        physical_tags=physical_tags,
        sound_speed=c,
        rho=rho,
        tag_throat=2,
        boundary_interface=boundary_interface,
        potential_interface=potential_interface,
        bem_precision=effective_bem_precision,
        use_burton_miller=use_burton_miller,
    )

    # ------------------------------------------------------------------
    # Frequency sweep (single-process or parallel)
    # ------------------------------------------------------------------
    freq_start_time = time.time()
    success_count = 0
    solutions: List[Optional[tuple]] = []
    gmres_iterations: List[Optional[int]] = []
    opencl_safe_retry_consumed = False
    device_metadata = results.get("metadata", {}).get("device_interface")

    if stage_callback:
        stage_callback("setup", 1.0, "HornBEM solver initialized")

    for i, freq in enumerate(frequencies):
        if cancellation_callback:
            cancellation_callback()
        if progress_callback:
            progress_callback(i / len(frequencies))
        if stage_callback:
            stage_callback(
                "frequency_solve",
                (i / len(frequencies)) if len(frequencies) > 0 else 1.0,
                f"Solving frequency {i + 1}/{len(frequencies)}",
            )

        if verbose:
            logger.info("[HornBEM] Solving %d/%d: %.1f Hz", i + 1, len(frequencies), freq)

        try:
            iter_start = time.time()
            spl, impedance, di, solution, iter_count = solver._solve_single_frequency(
                freq,
                observation_frame=observation_frame,
                observation_distance_m=observation_distance_m,
            )
            iter_time = time.time() - iter_start
            success_count += 1

            if verbose:
                logger.info(" -> %.1f dB, DI=%.1f dB, iters=%s (%.2fs)", spl, di, iter_count, iter_time)

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
                        device_metadata["runtime_profile"] = str(
                            safe_profile.get("profile") or "safe_cpu"
                        )
                    logger.warning(
                        "[HornBEM] OpenCL runtime error at %.1f Hz; retrying with safe CPU profile.", freq
                    )
                    try:
                        iter_start = time.time()
                        spl, impedance, di, solution, iter_count = solver._solve_single_frequency(
                            freq,
                            observation_frame=observation_frame,
                            observation_distance_m=observation_distance_m,
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
                            if safe_profile.get("detail"):
                                device_metadata["runtime_retry_detail"] = str(safe_profile["detail"])
                        if verbose:
                            logger.info(
                                " -> %.1f dB, DI=%.1f dB, iters=%s (%.2fs)",
                                spl, di, iter_count, iter_time,
                            )
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
                    "code": "opencl_runtime_unavailable",
                    "detail": (
                        f"OpenCL buffer allocation failed and no numba fallback is enabled ({exc}). "
                        "Install/enable OpenCL drivers, then retry."
                    ),
                    "original_interface": "opencl",
                }
                results["metadata"]["warnings"].append(warning)
                results["metadata"]["warning_count"] = len(results["metadata"]["warnings"])
                logger.warning("[HornBEM] OpenCL runtime error at %.1f Hz; no fallback enabled.", freq)
                if isinstance(device_metadata, dict):
                    device_metadata["runtime_selected"] = "opencl_unavailable"
                    device_metadata["runtime_fallback_reason"] = str(exc)
                    device_metadata["runtime_retry_outcome"] = "opencl_retry_failed"
                    device_metadata["interface"] = "unavailable"
                    device_metadata["selected"] = "opencl_unavailable"
                    device_metadata["fallback_reason"] = str(exc)

            logger.error("[HornBEM] Frequency solve error at %.1f Hz: %s", freq, exc)
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

    if cancellation_callback:
        cancellation_callback()

    # ------------------------------------------------------------------
    # Directivity patterns
    # ------------------------------------------------------------------
    if verbose:
        logger.info("[HornBEM] Computing directivity patterns...")
    if stage_callback:
        stage_callback(
            "directivity",
            0.0,
            "Generating requested polar maps for solved frequencies",
        )

    directivity_start = time.time()

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
                grid, filtered_freqs, c, rho, list(filtered_solutions), effective_polar_config,
                device_interface=potential_interface,
                precision=effective_bem_precision,
                observation_frame=observation_frame,
            )

            _angle_count = 37
            _angle_start = 0.0
            _angle_end = 180.0
            if effective_polar_config:
                ar = effective_polar_config.get("angle_range", [0, 180, 37])
                _angle_start, _angle_end, _angle_count = float(ar[0]), float(ar[1]), int(ar[2])

            for plane in requested_plane_ids:
                filtered_patterns = filtered_directivity.get(plane, [])
                full_patterns = [None] * len(frequencies)
                for vi, global_i in enumerate(indices):
                    if vi < len(filtered_patterns):
                        full_patterns[global_i] = filtered_patterns[vi]
                placeholder_angles = np.linspace(_angle_start, _angle_end, _angle_count)
                placeholder = [[float(a), None] for a in placeholder_angles]
                for fi in range(len(full_patterns)):
                    if full_patterns[fi] is None:
                        full_patterns[fi] = placeholder
                results["directivity"][plane] = full_patterns

        except Exception as exc:
            results["metadata"]["failures"].append(
                frequency_failure(
                    float(filtered_freqs[0]), "directivity", "directivity_failed", str(exc)
                )
            )

    directivity_time = time.time() - directivity_start
    total_time = time.time() - start_time

    results["metadata"]["failure_count"] = len(results["metadata"]["failures"])
    results["metadata"]["partial_success"] = (
        success_count > 0 and results["metadata"]["failure_count"] > 0
    )
    results["metadata"]["performance"] = {
        "total_time_seconds": total_time,
        "bem_precision": effective_bem_precision,
    }

    valid_iterations = [n for n in gmres_iterations if n is not None]
    avg_gmres = sum(valid_iterations) / len(valid_iterations) if valid_iterations else 0.0
    if verbose:
        logger.info("=" * 70)
        logger.info("SIMULATION COMPLETE")
        logger.info("=" * 70)
        logger.info("Total time: %.1fs", total_time)
        if len(frequencies) > 0:
            logger.info(
                "Frequency solve: %.1fs (%.2fs per frequency)",
                freq_solve_time, freq_solve_time / len(frequencies),
            )
        if valid_iterations:
            logger.info(
                "GMRES iterations: avg=%.1f, min=%d, max=%d",
                avg_gmres, min(valid_iterations), max(valid_iterations),
            )
        logger.info("Directivity compute: %.1fs", directivity_time)
        logger.info("=" * 70)

    if cancellation_callback:
        cancellation_callback()

    if progress_callback:
        progress_callback(1.0)
    if stage_callback:
        stage_callback("directivity", 1.0, "Requested polar map and DI aggregation complete")
        stage_callback("finalizing", 1.0, "Packaging HornBEM solver results")

    return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Subprocess worker for hard-kill cancellation support
# ---------------------------------------------------------------------------

def _serialize_mesh_for_subprocess(mesh) -> dict:
    """
    Extract picklable numpy arrays from a mesh dict (which contains a non-picklable bempp Grid).

    Returns a plain dict that can be sent to a child process and reconstructed
    via ``_reconstruct_mesh_in_subprocess``.
    """
    if isinstance(mesh, dict):
        grid = mesh["grid"]
        return {
            "vertices": np.asarray(grid.vertices),        # (3, N) float
            "elements": np.asarray(grid.elements),         # (3, M) int32
            "surface_tags": np.asarray(mesh.get("surface_tags", grid.domain_indices), dtype=np.int32),
            "unit_detection": mesh.get("unit_detection", {}),
            "mesh_metadata": mesh.get("mesh_metadata", {}),
        }
    # Bare grid object (legacy path)
    return {
        "vertices": np.asarray(mesh.vertices),
        "elements": np.asarray(mesh.elements),
        "surface_tags": np.asarray(getattr(mesh, "domain_indices", None) or np.array([], dtype=np.int32)),
        "unit_detection": {},
        "mesh_metadata": {},
    }


def _reconstruct_mesh_in_subprocess(serialized: dict) -> dict:
    """
    Rebuild the mesh dict expected by ``solve_optimized`` from serialized arrays.

    Must run inside the child process where bempp_api is available.
    """
    vertices = np.asarray(serialized["vertices"])
    elements = np.asarray(serialized["elements"], dtype=np.int32)
    surface_tags = np.asarray(serialized["surface_tags"], dtype=np.int32)
    grid = bempp_api.Grid(vertices, elements, surface_tags)
    return {
        "grid": grid,
        "surface_tags": surface_tags,
        "original_surface_tags": surface_tags,
        "unit_detection": serialized.get("unit_detection", {}),
        "mesh_metadata": serialized.get("mesh_metadata", {}),
    }


def _solve_subprocess_worker(
    serialized_mesh: dict,
    solve_kwargs: dict,
    progress_queue: "mp.Queue",
    cancel_event: "mp.Event",
):
    """
    Top-level entry point for the BEM solve child process.

    Reconstructs the mesh, runs ``solve_optimized`` with IPC-based callbacks,
    and puts the result (or error) on ``progress_queue``.

    This function must be module-level and picklable (no closures over
    non-serializable objects).
    """
    import traceback as _tb

    def _stage_cb(stage, progress=None, message=None):
        try:
            progress_queue.put_nowait({
                "type": "stage",
                "stage": str(stage),
                "progress": float(progress) if progress is not None else None,
                "message": str(message) if message is not None else None,
            })
        except Exception:
            pass

    def _progress_cb(p):
        try:
            progress_queue.put_nowait({"type": "progress", "progress": float(p)})
        except Exception:
            pass

    def _cancel_cb():
        if cancel_event.is_set():
            raise RuntimeError("__subprocess_cancelled__")

    try:
        mesh = _reconstruct_mesh_in_subprocess(serialized_mesh)
        solve_kwargs["progress_callback"] = _progress_cb
        solve_kwargs["stage_callback"] = _stage_cb
        solve_kwargs["cancellation_callback"] = _cancel_cb
        results = solve_optimized(mesh, **solve_kwargs)
        progress_queue.put({"type": "result", "data": results})
    except RuntimeError as exc:
        if "__subprocess_cancelled__" in str(exc):
            progress_queue.put({"type": "cancelled"})
        else:
            progress_queue.put({"type": "error", "message": str(exc), "traceback": _tb.format_exc()})
    except Exception as exc:
        progress_queue.put({"type": "error", "message": str(exc), "traceback": _tb.format_exc()})


def _resolve_observation_distance_m(polar_config: Optional[Dict], default: float = 2.0) -> float:
    if not isinstance(polar_config, dict):
        return float(default)
    try:
        distance = float(polar_config.get("distance", default))
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(distance) or distance <= 0:
        return float(default)
    return float(distance)
