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
from .observation import infer_observation_frame, resolve_safe_observation_distance
from .symmetry import (
    SymmetryPlane,
    build_symmetry_policy,
    create_mirror_grid,
    evaluate_symmetry_policy,
    validate_symmetry_reduction,
)


# ---------------------------------------------------------------------------
# GMRES kwargs detection
# ---------------------------------------------------------------------------

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
_VALID_BEM_PRECISIONS = {"single", "double"}


def _normalize_bem_precision(value: Optional[str]) -> str:
    raw = str(value or "double").strip().lower()
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
        bem_precision: 'single' or 'double' (default 'double').
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
        bem_precision: str = "double",
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

        # Symmetry / image-source state (populated by _assemble_image_operators)
        self.symmetry_info: Optional[Dict] = None
        self.symmetry_planes: Optional[List] = None
        self.mirror_grids: List[Tuple[np.ndarray, np.ndarray]] = []
        self.mirror_spaces: List[Tuple] = []

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
            bem_precision=cfg.get("bem_precision", "double"),
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
    # Image-source operators for symmetry reduction
    # ------------------------------------------------------------------

    def _assemble_image_operators(
        self,
        mirror_grids: List[Tuple[np.ndarray, np.ndarray]],
        symmetry_planes: List,
        symmetry_info: Dict,
    ) -> None:
        """
        Build bempp function spaces on each mirror grid for cross-grid
        image-source operators.

        The actual Helmholtz operators are wavenumber-dependent and are
        assembled per-frequency inside ``_solve_single_frequency``.

        Args:
            mirror_grids: List of (mirror_vertices, mirror_indices) tuples
                          produced by ``create_mirror_grid``.
            symmetry_planes: List of SymmetryPlane enums used.
            symmetry_info: Symmetry metadata dict from evaluate_symmetry_policy.
        """
        # Guard: verify the mesh is actually a half/quarter mesh.
        # If the B-Rep cut failed silently and we're operating on a full
        # mesh, the image source method would double the pressure (~6 dB
        # error).  Check that vertex coordinates respect the symmetry
        # plane(s).
        grid_verts = self.grid.vertices  # (3, N)
        for plane in symmetry_planes:
            if hasattr(plane, 'value'):
                pv = plane.value
            else:
                pv = str(plane)
            if pv == "yz":
                axis_vals = grid_verts[0, :]  # X coords
                axis_name = "X"
            elif pv == "xy":
                axis_vals = grid_verts[2, :]  # Z coords
                axis_name = "Z"
            else:
                continue
            n_violating = int(np.sum(axis_vals < -1e-4))
            if n_violating > 0:
                n_total = grid_verts.shape[1]
                pct = 100.0 * n_violating / n_total
                if pct > 5.0:
                    logger.error(
                        "[HornBEM] Image-source ABORTED: mesh is NOT a half mesh. "
                        "%d/%d vertices (%.1f%%) have %s < 0. "
                        "The B-Rep symmetry cut likely failed silently.",
                        n_violating, n_total, pct, axis_name,
                    )
                    self.symmetry_planes = []
                    self.symmetry_info = {}
                    self.mirror_grids = []
                    self.mirror_spaces = []
                    return
                else:
                    logger.warning(
                        "[HornBEM] %d vertices slightly past %s=0 plane "
                        "(%.2f%% — likely numerical noise, proceeding).",
                        n_violating, axis_name, pct,
                    )

        self.symmetry_planes = symmetry_planes
        self.symmetry_info = symmetry_info
        self.mirror_grids = mirror_grids
        self.mirror_spaces = []

        for mg_verts, mg_indices in mirror_grids:
            # Mirror grid — no domain_indices / surface_tags needed
            mg_grid = bempp_api.Grid(mg_verts, mg_indices)
            dp0_mirror = bempp_api.function_space(mg_grid, "DP", 0)
            p1_mirror = bempp_api.function_space(mg_grid, "P", 1)
            self.mirror_spaces.append((dp0_mirror, p1_mirror))

        logger.info(
            "[HornBEM] Image-source operators prepared: %d mirror grid(s)",
            len(self.mirror_spaces),
        )

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

            # ----------------------------------------------------------
            # Image-source contributions (cross-grid operators)
            # ----------------------------------------------------------
            if self.mirror_spaces:
                import sys
                print(f"  [DEBUG solve_optimized] Image source path triggered, mirror_spaces={len(self.mirror_spaces)}", file=sys.stderr)
                # Cross-grid operators can't be added to direct operators
                # via bempp's __add__ (different domain spaces). Assemble to
                # dense matrices and sum at the numpy level instead.
                from scipy.sparse.linalg import gmres as _scipy_gmres

                lhs_direct = 0.5 * self.lhs_identity - dlp - coupling * (-hyp)
                A = lhs_direct.weak_form().to_dense()

                # RHS: direct part
                rhs_direct_gf = (-slp - coupling * (adlp + 0.5 * self.rhs_identity)) * neumann_fun
                b = np.asarray(rhs_direct_gf.projections(self.p1_space))

                for dp0_mirror, p1_mirror in self.mirror_spaces:
                    slp_img = bempp_api.operators.boundary.helmholtz.single_layer(
                        dp0_mirror, self.p1_space, self.p1_space, k, **op_kwargs
                    )
                    dlp_img = bempp_api.operators.boundary.helmholtz.double_layer(
                        p1_mirror, self.p1_space, self.p1_space, k, **op_kwargs
                    )
                    hyp_img = bempp_api.operators.boundary.helmholtz.hypersingular(
                        p1_mirror, self.p1_space, self.p1_space, k, **op_kwargs
                    )
                    adlp_img = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(
                        dp0_mirror, self.p1_space, self.p1_space, k, **op_kwargs
                    )

                    # LHS image: same Burton-Miller form without identity jump
                    A_img = (-dlp_img + coupling * hyp_img).weak_form().to_dense()
                    A += A_img

                    # RHS image: neumann_fun on mirror grid (same coeff pattern)
                    mirror_coeffs = np.zeros(dp0_mirror.global_dof_count, dtype=_numpy_dtype_for_precision(self.bem_precision))
                    valid_dofs = self.driver_dofs[self.driver_dofs < dp0_mirror.global_dof_count]
                    mirror_coeffs[valid_dofs] = 1.0
                    neumann_mirror = bempp_api.GridFunction(
                        dp0_mirror,
                        coefficients=1j * self.rho * omega * mirror_coeffs,
                    )
                    rhs_img_gf = (-slp_img - coupling * adlp_img) * neumann_mirror
                    b += np.asarray(rhs_img_gf.projections(self.p1_space))

                # Direct scipy GMRES solve on the dense system
                x, info = _scipy_gmres(A, b, atol=1e-5, restart=100)
                if info != 0:
                    logger.warning("[HornBEM] GMRES did not converge (info=%d) at %.1f Hz", info, freq)
                p_total = bempp_api.GridFunction(self.p1_space, coefficients=x)
                iter_count = None  # scipy gmres doesn't expose iteration count in this path

                # On-axis SPL (skip the standard GMRES path below)
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

                # Add image potential contributions
                for dp0_mirror, p1_mirror in self.mirror_spaces:
                    dlp_pot_img = bempp_api.operators.potential.helmholtz.double_layer(
                        p1_mirror, obs_point, k, **pot_kwargs
                    )
                    slp_pot_img = bempp_api.operators.potential.helmholtz.single_layer(
                        dp0_mirror, obs_point, k, **pot_kwargs
                    )
                    p_mirror = bempp_api.GridFunction(
                        p1_mirror, coefficients=p_total.coefficients
                    )
                    m_coeffs = np.zeros(dp0_mirror.global_dof_count, dtype=_numpy_dtype_for_precision(self.bem_precision))
                    v_dofs = self.driver_dofs[self.driver_dofs < dp0_mirror.global_dof_count]
                    m_coeffs[v_dofs] = 1.0
                    n_mirror = bempp_api.GridFunction(
                        dp0_mirror,
                        coefficients=1j * self.rho * omega * m_coeffs,
                    )
                    pressure_on_axis = pressure_on_axis + dlp_pot_img * p_mirror - slp_pot_img * n_mirror

                p_ref = 20e-6
                p_amplitude = np.abs(pressure_on_axis[0, 0])
                spl = 20 * np.log10(p_amplitude / p_ref) if p_amplitude > 0 else 0.0

                impedance = calculate_throat_impedance_horn(
                    self.grid, p_total, self.driver_dofs,
                    self.throat_p1_dofs, self.throat_element_areas
                )
                di = estimate_di_from_ka(self.grid, k, observation_frame=frame)
                solution = (p_total, velocity_fun, self.p1_space, self.dp0_space)
                return float(spl), impedance, float(di), solution, iter_count
            else:
                # No image sources — standard Burton-Miller
                lhs = 0.5 * self.lhs_identity - dlp - coupling * (-hyp)
                rhs = (-slp - coupling * (adlp + 0.5 * self.rhs_identity)) * neumann_fun
        else:
            lhs = dlp - 0.5 * self.lhs_identity
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
# Symmetry helper (unchanged interface)
# ---------------------------------------------------------------------------

def apply_neumann_bc_on_symmetry_planes(grid, symmetry_info: Optional[Dict]) -> None:
    if symmetry_info is None or symmetry_info.get("symmetry_face_tag") is None:
        return
    logger.info("[HornBEM] Symmetry planes detected (tag %s)", symmetry_info["symmetry_face_tag"])
    logger.info("[HornBEM] Neumann BC (rigid) applied implicitly on symmetry planes")


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
    enable_symmetry: bool = True,
    symmetry_tolerance: float = 1e-3,
    verbose: bool = True,
    mesh_validation_mode: str = "warn",
    use_burton_miller: bool = True,
    bem_precision: str = "double",
    frequency_spacing: str = "linear",
    device_mode: str = "auto",
    enable_warmup: bool = True,
    cancellation_callback: Optional[Callable[[], None]] = None,
    workers: int = 1,
) -> Dict:
    """
    Run HornBEMSolver frequency sweep and return the standard API result dict.

    This function is a drop-in replacement for the previous solve_optimized().
    The result dictionary structure is unchanged.
    """
    start_time = time.time()
    mesh_validation_mode = normalize_mesh_validation_mode(mesh_validation_mode)
    bem_precision = _normalize_bem_precision(bem_precision)
    _configure_bempp_precision(bem_precision)

    if isinstance(mesh, dict):
        grid = mesh["grid"]
        throat_elements = mesh.get("throat_elements", np.array([]))
        original_vertices = mesh.get("original_vertices")
        original_indices = mesh.get("original_indices")
        original_tags = mesh.get("original_surface_tags")
        unit_detection = mesh.get("unit_detection", {})
        mesh_metadata = mesh.get("mesh_metadata", {})
        # Physical tags for HornBEMSolver come from surface_tags in the mesh dict
        physical_tags = mesh.get("surface_tags", original_tags)
    else:
        grid = mesh
        throat_elements = np.array([])
        original_vertices = getattr(grid, "vertices", None)
        original_indices = getattr(grid, "elements", None)
        original_tags = getattr(grid, "domain_indices", None)
        unit_detection = {}
        mesh_metadata = {}
        physical_tags = original_tags

    c = 343.0
    rho = 1.21
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

    # ------------------------------------------------------------------
    # Symmetry detection
    # ------------------------------------------------------------------
    symmetry_info = {"symmetry_type": "full", "reduction_factor": 1.0}
    symmetry_policy = build_symmetry_policy(
        requested=bool(enable_symmetry),
        reason="missing_original_mesh" if enable_symmetry else "disabled",
    )
    reduction_factor = 1.0
    if stage_callback:
        stage_callback("setup", 0.0, "Preparing HornBEM solver")

    if cancellation_callback:
        cancellation_callback()

    if enable_symmetry and original_vertices is not None and original_indices is not None:
        if verbose:
            logger.info("=" * 70)
            logger.info("SYMMETRY DETECTION")
            logger.info("=" * 70)
        try:
            # Extract quadrants from mesh metadata for parameter-driven
            # symmetry detection (O(1) vs O(N²) vertex matching).
            _quadrants = (mesh_metadata or {}).get(
                "requestedQuadrants",
                (mesh_metadata or {}).get("effectiveQuadrants"),
            )
            symmetry_result = evaluate_symmetry_policy(
                vertices=original_vertices,
                indices=original_indices,
                surface_tags=original_tags,
                throat_elements=throat_elements,
                enable_symmetry=enable_symmetry,
                tolerance=symmetry_tolerance,
                quadrants=_quadrants,
            )
            symmetry_policy = symmetry_result["policy"]
            symmetry_info = symmetry_result["symmetry"]

            if symmetry_policy["applied"]:
                if verbose:
                    logger.info("[HornBEM] Symmetry detected: %s", symmetry_policy["detected_symmetry_type"])

                reduced_v = symmetry_result["reduced_vertices"]
                reduced_i = symmetry_result["reduced_indices"]
                reduced_tags = symmetry_result["reduced_surface_tags"]
                grid = bempp_api.Grid(reduced_v, reduced_i, reduced_tags)
                physical_tags = reduced_tags
                reduction_factor = float(symmetry_info["reduction_factor"])
                validate_symmetry_reduction(symmetry_info, verbose=verbose)
                throat_elements = np.where(reduced_tags == 2)[0]
            elif verbose:
                if symmetry_policy["reason"] == "excitation_off_center":
                    logger.info("[HornBEM] Symmetry rejected: excitation not centered")
                else:
                    logger.info("[HornBEM] No symmetry detected - using full model")
        except Exception as exc:
            symmetry_policy = build_symmetry_policy(
                requested=True, reason="error_fallback", error=str(exc)
            )
            symmetry_info = {"symmetry_type": "full", "reduction_factor": 1.0}
            if verbose:
                logger.warning("[HornBEM] Symmetry detection failed: %s", exc, exc_info=True)

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
    _symmetry_plane_for_observation = None
    if symmetry_info.get("symmetry_planes"):
        _planes = symmetry_info["symmetry_planes"]
        if isinstance(_planes, (list, tuple)) and len(_planes) > 0:
            _symmetry_plane_for_observation = str(_planes[0])
    observation_frame = infer_observation_frame(
        grid,
        observation_origin=observation_origin,
        symmetry_plane=_symmetry_plane_for_observation,
    )
    observation_info = resolve_safe_observation_distance(
        grid, observation_request_m, observation_frame
    )
    observation_distance_m = float(observation_info["effective_distance_m"])
    effective_polar_config = dict(polar_config) if isinstance(polar_config, dict) else {}
    effective_polar_config["distance"] = observation_distance_m

    results = {
        "frequencies": frequencies.tolist(),
        "directivity": {"horizontal": [], "vertical": [], "diagonal": []},
        "spl_on_axis": {"frequencies": frequencies.tolist(), "spl": []},
        "impedance": {"frequencies": frequencies.tolist(), "real": [], "imaginary": []},
        "di": {"frequencies": frequencies.tolist(), "di": []},
        "metadata": {
            "symmetry": symmetry_info,
            "symmetry_policy": symmetry_policy,
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
        bem_precision=bem_precision,
        use_burton_miller=use_burton_miller,
    )

    # ------------------------------------------------------------------
    # Image-source operators for symmetry reduction
    # ------------------------------------------------------------------
    if symmetry_policy.get("applied") and symmetry_info.get("symmetry_planes"):
        try:
            # Convert serialized plane strings back to SymmetryPlane enums
            _plane_str_map = {p.value: p for p in SymmetryPlane}
            _sym_planes_enum = [
                _plane_str_map[s]
                for s in symmetry_info["symmetry_planes"]
                if s in _plane_str_map
            ]
            if _sym_planes_enum:
                _mirror_grids = create_mirror_grid(reduced_v, reduced_i, _sym_planes_enum)
                solver._assemble_image_operators(
                    _mirror_grids, _sym_planes_enum, symmetry_info
                )
        except Exception as exc:
            logger.warning(
                "[HornBEM] Image-source operator setup failed: %s — "
                "falling back to standard BEM (no image contribution).",
                exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Optional warm-up
    # ------------------------------------------------------------------
    warmup_time_seconds = 0.0
    if enable_warmup and len(frequencies) > 0:
        if cancellation_callback:
            cancellation_callback()
        _warmup_start = time.time()
        try:
            _k_w = 2 * np.pi * frequencies[len(frequencies) // 2] / c
            op_kw = _operator_kwargs(boundary_interface, bem_precision)
            _dlp = bempp_api.operators.boundary.helmholtz.double_layer(
                solver.p1_space, solver.p1_space, solver.p1_space, _k_w, **op_kw
            )
            if use_burton_miller:
                _hyp = bempp_api.operators.boundary.helmholtz.hypersingular(
                    solver.p1_space, solver.p1_space, solver.p1_space, _k_w, **op_kw
                )
                _coupling = 1j / _k_w
                _lhs = 0.5 * solver.lhs_identity - _dlp - _coupling * (-_hyp)
            else:
                _lhs = _dlp - 0.5 * solver.lhs_identity
            _ = _lhs.strong_form()
            warmup_time_seconds = time.time() - _warmup_start
            if verbose:
                logger.info("[HornBEM] Warm-up complete (%.2fs)", warmup_time_seconds)
        except Exception as _warmup_exc:
            warmup_time_seconds = time.time() - _warmup_start
            if verbose:
                logger.info("[HornBEM] Warm-up skipped (%s)", _warmup_exc)

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
            "Generating polar maps (horizontal/vertical/diagonal) and deriving DI from solved frequencies",
        )

    directivity_start = time.time()

    if success_count == 0:
        failure_msgs = [f.get("detail", "unknown") for f in results["metadata"]["failures"][:3]]
        raise RuntimeError(
            f"All {len(frequencies)} frequencies failed to solve. "
            f"First failure(s): {'; '.join(failure_msgs)}"
        )

    valid_solutions = [(i, sol) for i, sol in enumerate(solutions) if sol is not None]

    # ------------------------------------------------------------------
    # Re-expand reduced solution to full mesh for directivity (Option b)
    # ------------------------------------------------------------------
    directivity_grid = grid
    directivity_solutions = solutions
    directivity_frame = observation_frame
    if solver.mirror_spaces and solver.mirror_grids:
        try:
            # Build full grid by concatenating reduced + all mirror grids
            all_verts = [grid.vertices]
            all_indices = [grid.elements]
            offset = grid.vertices.shape[1]
            for mg_verts, mg_indices in solver.mirror_grids:
                all_verts.append(mg_verts)
                all_indices.append(mg_indices + offset)
                offset += mg_verts.shape[1]
            full_v = np.concatenate(all_verts, axis=1)
            full_i = np.concatenate(all_indices, axis=1)
            directivity_grid = bempp_api.Grid(full_v, full_i)
            full_p1 = bempp_api.function_space(directivity_grid, "P", 1)
            full_dp0 = bempp_api.function_space(directivity_grid, "DP", 0)
            directivity_frame = infer_observation_frame(
                directivity_grid,
                observation_origin=observation_origin,
                symmetry_plane=None,
            )

            # Re-expand each solution: replicate coefficients for each mirror section
            n_mirrors = len(solver.mirror_grids)
            directivity_solutions = []
            for sol in solutions:
                if sol is None:
                    directivity_solutions.append(None)
                    continue
                p_total_reduced, u_total_reduced, _, _ = sol
                # P1 pressure: Neumann → same coefficients on each mirror section
                p_coeffs_reduced = np.asarray(p_total_reduced.coefficients)
                p_coeffs_full = np.tile(p_coeffs_reduced, 1 + n_mirrors)
                if len(p_coeffs_full) != full_p1.global_dof_count:
                    # Fallback: pad or trim to match
                    _target = full_p1.global_dof_count
                    if len(p_coeffs_full) < _target:
                        p_coeffs_full = np.pad(p_coeffs_full, (0, _target - len(p_coeffs_full)))
                    else:
                        p_coeffs_full = p_coeffs_full[:_target]
                p_full = bempp_api.GridFunction(full_p1, coefficients=p_coeffs_full)
                # DP0 velocity: replicate
                u_coeffs_reduced = np.asarray(u_total_reduced.coefficients)
                u_coeffs_full = np.tile(u_coeffs_reduced, 1 + n_mirrors)
                if len(u_coeffs_full) != full_dp0.global_dof_count:
                    _target = full_dp0.global_dof_count
                    if len(u_coeffs_full) < _target:
                        u_coeffs_full = np.pad(u_coeffs_full, (0, _target - len(u_coeffs_full)))
                    else:
                        u_coeffs_full = u_coeffs_full[:_target]
                u_full = bempp_api.GridFunction(full_dp0, coefficients=u_coeffs_full)
                directivity_solutions.append((p_full, u_full, full_p1, full_dp0))

            if verbose:
                logger.info(
                    "[HornBEM] Re-expanded solution to full mesh for directivity: "
                    "%d vertices, %d elements",
                    full_v.shape[1], full_i.shape[1],
                )
        except Exception as exc:
            logger.warning(
                "[HornBEM] Full-mesh re-expansion failed: %s — "
                "directivity will use reduced mesh only.",
                exc, exc_info=True,
            )
            directivity_grid = grid
            directivity_solutions = solutions
            directivity_frame = observation_frame

    if len(valid_solutions) > 0:
        indices, filtered_solutions = zip(*valid_solutions)
        filtered_freqs = frequencies[list(indices)]
        # Use re-expanded solutions for directivity if available
        filtered_dir_solutions = [
            directivity_solutions[i] for i in indices
            if directivity_solutions[i] is not None
        ]
        if len(filtered_dir_solutions) != len(filtered_freqs):
            filtered_dir_solutions = list(filtered_solutions)
        try:
            filtered_directivity = calculate_directivity_patterns_correct(
                directivity_grid, filtered_freqs, c, rho, filtered_dir_solutions, effective_polar_config,
                device_interface=potential_interface,
                precision=bem_precision,
                observation_frame=directivity_frame,
            )

            _angle_count = 37
            _angle_start = 0.0
            _angle_end = 180.0
            if effective_polar_config:
                ar = effective_polar_config.get("angle_range", [0, 180, 37])
                _angle_start, _angle_end, _angle_count = float(ar[0]), float(ar[1]), int(ar[2])

            for plane in ("horizontal", "vertical", "diagonal"):
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

            # Refine DI from polar patterns
            for vi, global_i in enumerate(indices):
                spl_on_axis_val = results["spl_on_axis"]["spl"][global_i]
                if spl_on_axis_val is None:
                    continue
                h_pattern = results["directivity"]["horizontal"][global_i]
                if not h_pattern:
                    continue
                if any(pt[1] is None for pt in h_pattern):
                    continue
                try:
                    angles_deg = np.array([pt[0] for pt in h_pattern])
                    spl_norm = np.array([pt[1] for pt in h_pattern])
                    on_axis_idx = np.argmin(np.abs(angles_deg))
                    spl_abs = spl_norm - spl_norm[on_axis_idx] + spl_on_axis_val

                    theta_rad = np.deg2rad(angles_deg)
                    p_ref = 20e-6
                    p_vals = p_ref * 10 ** (spl_abs / 20)
                    intensities = p_vals ** 2
                    sin_theta = np.maximum(np.sin(theta_rad), 0.01)

                    avg_intensity = np.trapz(intensities * sin_theta, theta_rad)
                    total_weight = np.trapz(sin_theta, theta_rad)
                    if total_weight > 0 and avg_intensity > 0:
                        avg_i = avg_intensity / total_weight
                        p_on_axis = p_ref * 10 ** (spl_on_axis_val / 20)
                        di = 10 * np.log10(p_on_axis ** 2 / avg_i)
                        results["di"]["di"][global_i] = float(max(0.0, min(30.0, di)))
                except Exception:
                    pass

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
        "bem_precision": bem_precision,
    }

    if verbose:
        logger.info("=" * 70)
        logger.info("SIMULATION COMPLETE")
        logger.info("=" * 70)
        logger.info("Total time: %.1fs", total_time)
        if warmup_time_seconds > 0:
            logger.info("Warm-up: %.2fs", warmup_time_seconds)
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
        if reduction_factor > 1.0:
            logger.info("Symmetry speedup: %.1fx", reduction_factor)
        logger.info("=" * 70)

    if cancellation_callback:
        cancellation_callback()

    if progress_callback:
        progress_callback(1.0)
    if stage_callback:
        stage_callback("directivity", 1.0, "Polar map and DI aggregation complete")
        stage_callback("finalizing", 1.0, "Packaging HornBEM solver results")

    return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
