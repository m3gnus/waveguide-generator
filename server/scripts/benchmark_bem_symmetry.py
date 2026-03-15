#!/usr/bin/env python3
"""
BEM Symmetry Performance Benchmark.

Profiles full-model (symmetry disabled) vs symmetry-reduced BEM solve,
breaking down time by phase: geometry build, mesh prep, symmetry detection,
BEM solver construction, frequency solve, directivity post-processing.

Verifies that symmetry-reduced geometry produces a proportionally smaller
BEM matrix (fewer DOF) and measures actual speedup.

Strategy:
  Both runs use the same OCC-built full-domain mesh (quadrants=1234).
  - "FULL" run:    enable_symmetry=False -> solves the entire mesh
  - "REDUCED" run: enable_symmetry=True  -> detects symmetry and halves/quarters mesh

This mirrors what the real solver does inside solve_optimized().

Usage (run from server/ directory):
    python3 scripts/benchmark_bem_symmetry.py
    python3 scripts/benchmark_bem_symmetry.py --frequencies 3
    python3 scripts/benchmark_bem_symmetry.py --skip-directivity
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure server/ is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Force numba backend to avoid OpenCL kernel issues in standalone scripts
os.environ.setdefault("BEMPP_DEFAULT_DEVICE_INTERFACE", "numba")

import numpy as np

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
# Suppress noisy logs from libraries during benchmark
for name in ("bempp", "numba", "gmsh", "solver", "opencl"):
    logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_conical_params() -> dict:
    """
    Return a simple axisymmetric conical horn config (R-OSSE, quadrants=1234).
    No guiding curves, no morph, no slot — guaranteed symmetric.
    Uses WaveguideParamsRequest to get proper defaults for all fields.
    """
    from contracts import WaveguideParamsRequest

    req = WaveguideParamsRequest(
        formula_type="R-OSSE",
        R="60",
        r="0.4",
        b="0.2",
        m="0.85",
        tmax="1.0",
        r0="12.7",
        a0="15.5",
        k="2.0",
        q="3.4",
        throat_profile=1,
        throat_ext_angle=0.0,
        throat_ext_length=0.0,
        slot_length=0.0,
        rot=0.0,
        gcurve_type=0,
        morph_target=0,
        n_angular=60,
        n_length=15,
        quadrants=1234,
        throat_res=8.0,
        mouth_res=20.0,
        rear_res=40.0,
        wall_thickness=6.0,
        enc_depth=0.0,
        source_shape=2,
        source_radius=-1.0,
    )
    return req.model_dump()


class PhaseTimer:
    """Context manager that records elapsed time for a named phase."""

    def __init__(self, name: str, results: Dict[str, float]):
        self.name = name
        self.results = results

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.results[self.name] = time.perf_counter() - self.start
        return False


def _format_time(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def build_mesh_once(
    num_frequencies: int,
    frequency_range: Tuple[float, float],
) -> Dict[str, Any]:
    """Build the OCC mesh once and return shared data for both runs."""

    from solver.waveguide_builder import build_waveguide_mesh
    from solver.mesh import prepare_mesh

    params = _simple_conical_params()

    t0 = time.perf_counter()
    occ_result = build_waveguide_mesh(params, include_canonical=True)
    occ_build_time = time.perf_counter() - t0

    canonical = occ_result.get("canonical_mesh", {})
    raw_vertices = canonical["vertices"]
    raw_indices = canonical["indices"]
    raw_tags = canonical["surfaceTags"]

    t0 = time.perf_counter()
    mesh_metadata = {
        "units": "mm",
        "unitScaleToMeter": 0.001,
        "meshStrategy": "occ_adaptive",
    }
    mesh = prepare_mesh(
        raw_vertices,
        raw_indices,
        surface_tags=raw_tags,
        mesh_metadata=mesh_metadata,
        use_gmsh=False,
    )
    prepare_time = time.perf_counter() - t0

    return {
        "mesh": mesh,
        "occ_build_time": occ_build_time,
        "prepare_time": prepare_time,
        "occ_stats": occ_result.get("stats", {}),
    }


def run_solve_case(
    label: str,
    mesh_data: Dict[str, Any],
    enable_symmetry: bool,
    num_frequencies: int = 3,
    frequency_range: Tuple[float, float] = (500.0, 2000.0),
    skip_directivity: bool = False,
) -> Dict[str, Any]:
    """Run a BEM solve case (with or without symmetry) and return timing + DOF info."""

    from solver.solve_optimized import (
        HornBEMSolver,
        apply_neumann_bc_on_symmetry_planes,
    )
    from solver.symmetry import (
        evaluate_symmetry_policy,
        validate_symmetry_reduction,
    )
    from solver.deps import bempp_api
    from solver.device_interface import (
        boundary_device_interface,
        potential_device_interface,
    )
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance
    from solver.directivity_correct import calculate_directivity_patterns_correct

    timings: Dict[str, float] = {}
    info: Dict[str, Any] = {"label": label, "enable_symmetry": enable_symmetry}

    mesh = mesh_data["mesh"]
    timings["occ_build"] = mesh_data["occ_build_time"]
    timings["prepare_mesh"] = mesh_data["prepare_time"]

    frequencies = np.linspace(frequency_range[0], frequency_range[1], num_frequencies)
    c = 343.0
    rho = 1.21

    grid = mesh["grid"]
    original_vertices = mesh["original_vertices"]
    original_indices = mesh["original_indices"]
    original_tags = mesh["original_surface_tags"]
    throat_elements = mesh["throat_elements"]
    physical_tags = mesh["surface_tags"]

    info["mesh_vertices"] = original_vertices.shape[1]
    info["mesh_triangles"] = original_indices.shape[1]

    # Phase: Symmetry detection + reduction
    with PhaseTimer("symmetry_detection", timings):
        symmetry_result = evaluate_symmetry_policy(
            vertices=original_vertices,
            indices=original_indices,
            surface_tags=original_tags,
            throat_elements=throat_elements,
            enable_symmetry=enable_symmetry,
            tolerance=1e-3,
        )

    symmetry_policy = symmetry_result["policy"]
    symmetry_info = symmetry_result["symmetry"]
    info["symmetry_detected"] = symmetry_policy.get("detected_symmetry_type", "?")
    info["symmetry_applied"] = symmetry_policy.get("applied", False)
    info["symmetry_reduction_factor"] = float(symmetry_policy.get("reduction_factor", 1.0))

    # Apply symmetry reduction if detected
    if symmetry_policy["applied"]:
        reduced_v = symmetry_result["reduced_vertices"]
        reduced_i = symmetry_result["reduced_indices"]
        reduced_tags_arr = symmetry_result["reduced_surface_tags"]

        with PhaseTimer("symmetry_grid_rebuild", timings):
            grid = bempp_api.grid_from_element_data(reduced_v, reduced_i, reduced_tags_arr)
            physical_tags = reduced_tags_arr
            throat_elements = np.where(reduced_tags_arr == 2)[0]

        info["reduced_vertices"] = reduced_v.shape[1]
        info["reduced_triangles"] = reduced_i.shape[0] if reduced_i.ndim == 2 else reduced_i.shape[1]
    else:
        timings["symmetry_grid_rebuild"] = 0.0
        info["reduced_vertices"] = info["mesh_vertices"]
        info["reduced_triangles"] = info["mesh_triangles"]

    # Phase: HornBEMSolver construction (function spaces, identity ops)
    boundary_interface = "numba"
    potential_interface = "numba"
    bem_precision = "double"

    if physical_tags is None:
        physical_tags = np.full(grid.elements.shape[1], 2, dtype=np.int32)

    with PhaseTimer("solver_construction", timings):
        solver = HornBEMSolver(
            grid=grid,
            physical_tags=physical_tags,
            sound_speed=c,
            rho=rho,
            tag_throat=2,
            boundary_interface=boundary_interface,
            potential_interface=potential_interface,
            bem_precision=bem_precision,
            use_burton_miller=True,
        )

    info["bem_dof_p1"] = solver.p1_space.global_dof_count
    info["bem_dof_dp0"] = solver.dp0_space.global_dof_count
    info["driver_elements"] = len(solver.driver_dofs)

    # Observation frame
    observation_frame = infer_observation_frame(grid, observation_origin="mouth")
    observation_info = resolve_safe_observation_distance(grid, 2.0, observation_frame)
    observation_distance_m = float(observation_info["effective_distance_m"])

    # Phase: Frequency solve loop
    solutions = []
    freq_times = []

    with PhaseTimer("frequency_solve_total", timings):
        for i, freq in enumerate(frequencies):
            t0 = time.perf_counter()
            try:
                spl, impedance, di, solution, iter_count = solver._solve_single_frequency(
                    freq,
                    observation_frame=observation_frame,
                    observation_distance_m=observation_distance_m,
                )
                elapsed = time.perf_counter() - t0
                freq_times.append(elapsed)
                solutions.append((freq, spl, impedance, di, solution))
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                freq_times.append(elapsed)
                solutions.append(None)
                print(f"  WARNING: {label} freq {freq:.0f} Hz failed: {exc}")

    info["freq_times"] = freq_times
    info["freq_avg_time"] = float(np.mean(freq_times)) if freq_times else 0.0
    info["freq_count_success"] = sum(1 for s in solutions if s is not None)

    # Phase: Directivity (optional)
    if not skip_directivity:
        valid_solutions = [(i, sol) for i, sol in enumerate(solutions) if sol is not None]
        if valid_solutions:
            sol_indices, filtered_solutions = zip(*valid_solutions)
            filtered_freqs = frequencies[list(sol_indices)]
            polar_config = {
                "angle_range": [0, 180, 37],
                "norm_angle": 5.0,
                "distance": observation_distance_m,
            }
            with PhaseTimer("directivity", timings):
                try:
                    directivity = calculate_directivity_patterns_correct(
                        grid, filtered_freqs, c, rho,
                        list(filtered_solutions), polar_config,
                        device_interface=potential_interface,
                        precision=bem_precision,
                        observation_frame=observation_frame,
                    )
                    info["directivity_planes"] = list(directivity.keys())
                except Exception as exc:
                    info["directivity_error"] = str(exc)
                    timings.setdefault("directivity", 0.0)
        else:
            timings["directivity"] = 0.0
    else:
        timings["directivity"] = 0.0

    # Compute total (excluding shared OCC build / prepare which are the same for both)
    timings["total_solver_only"] = (
        timings.get("symmetry_detection", 0)
        + timings.get("symmetry_grid_rebuild", 0)
        + timings.get("solver_construction", 0)
        + timings.get("frequency_solve_total", 0)
        + timings.get("directivity", 0)
    )
    timings["total"] = sum(v for k, v in timings.items() if k != "total_solver_only")
    info["timings"] = timings
    return info


def print_comparison(full: Dict[str, Any], reduced: Dict[str, Any]) -> None:
    """Pretty-print benchmark comparison."""
    print()
    print("=" * 78)
    print("BEM SYMMETRY PERFORMANCE BENCHMARK")
    print("=" * 78)
    print()

    for case in (full, reduced):
        label = case["label"]
        timings = case["timings"]
        sym_str = "enabled" if case["enable_symmetry"] else "disabled"
        print(f"--- {label} (symmetry {sym_str}) ---")
        print(f"  OCC build:            {_format_time(timings.get('occ_build', 0))}  (shared)")
        print(f"  Prepare mesh:         {_format_time(timings.get('prepare_mesh', 0))}  (shared)")
        print(f"  Symmetry detection:   {_format_time(timings.get('symmetry_detection', 0))}")
        if timings.get("symmetry_grid_rebuild", 0) > 0:
            print(f"  Symmetry grid rebuild:{_format_time(timings['symmetry_grid_rebuild'])}")
        print(f"  Solver construction:  {_format_time(timings.get('solver_construction', 0))}")
        print(f"  Frequency solve:      {_format_time(timings.get('frequency_solve_total', 0))}")
        if case.get("freq_times"):
            for i, ft in enumerate(case["freq_times"]):
                print(f"    freq[{i}]:             {_format_time(ft)}")
        print(f"  Directivity:          {_format_time(timings.get('directivity', 0))}")
        print(f"  TOTAL (solver only):  {_format_time(timings.get('total_solver_only', 0))}")
        print()
        print(f"  Input mesh: {case['mesh_vertices']} verts, {case['mesh_triangles']} tris")
        print(f"  Symmetry: {case['symmetry_detected']} (applied={case['symmetry_applied']}, "
              f"reduction={case['symmetry_reduction_factor']:.1f}x)")
        print(f"  After reduction: {case['reduced_vertices']} verts, {case['reduced_triangles']} tris")
        print(f"  BEM DOF: P1={case['bem_dof_p1']}, DP0={case['bem_dof_dp0']}")
        print(f"  Driver elements: {case['driver_elements']}")
        print(f"  Frequencies solved: {case['freq_count_success']}/{len(case.get('freq_times', []))}")
        print()

    # Comparison
    print("=" * 78)
    print("COMPARISON: FULL vs SYMMETRY-REDUCED")
    print("=" * 78)

    full_dof = full["bem_dof_p1"]
    red_dof = reduced["bem_dof_p1"]
    dof_ratio = full_dof / red_dof if red_dof > 0 else float("inf")

    full_tris = full["reduced_triangles"]
    red_tris = reduced["reduced_triangles"]
    tri_ratio = full_tris / red_tris if red_tris > 0 else float("inf")

    full_solver_total = full["timings"].get("total_solver_only", 0)
    red_solver_total = reduced["timings"].get("total_solver_only", 0)
    speedup = full_solver_total / red_solver_total if red_solver_total > 0 else float("inf")

    full_solve = full["timings"].get("frequency_solve_total", 0)
    red_solve = reduced["timings"].get("frequency_solve_total", 0)
    solve_speedup = full_solve / red_solve if red_solve > 0 else float("inf")

    full_construct = full["timings"].get("solver_construction", 0)
    red_construct = reduced["timings"].get("solver_construction", 0)
    construct_speedup = full_construct / red_construct if red_construct > 0 else float("inf")

    print(f"  DOF ratio (full/reduced):     {dof_ratio:.2f}x  ({full_dof} vs {red_dof})")
    print(f"  Triangle ratio:               {tri_ratio:.2f}x  ({full_tris} vs {red_tris})")
    print()
    print(f"  Solver-only time speedup:     {speedup:.2f}x  ({_format_time(full_solver_total)} vs {_format_time(red_solver_total)})")
    print(f"  Freq-solve speedup:           {solve_speedup:.2f}x  ({_format_time(full_solve)} vs {_format_time(red_solve)})")
    print(f"  Solver construction speedup:  {construct_speedup:.2f}x  ({_format_time(full_construct)} vs {_format_time(red_construct)})")
    print()

    # Phase-by-phase comparison
    print("  Phase-by-phase:")
    for phase in ("symmetry_detection", "symmetry_grid_rebuild", "solver_construction",
                   "frequency_solve_total", "directivity"):
        ft = full["timings"].get(phase, 0)
        rt = reduced["timings"].get(phase, 0)
        ratio = ft / rt if rt > 0 else float("inf")
        print(f"    {phase:30s} {_format_time(ft):>8s} vs {_format_time(rt):>8s}  ({ratio:.2f}x)")
    print()

    # BEM matrix size analysis
    # BEM has O(N^2) assembly + O(N^2) memory, GMRES is O(N^2) per iteration
    expected_speedup_assembly = dof_ratio ** 2
    print(f"  Expected assembly speedup (DOF^2): {expected_speedup_assembly:.1f}x")
    print(f"  Actual freq-solve speedup:         {solve_speedup:.2f}x")
    print()

    # Symmetry overhead analysis
    sym_overhead = (
        reduced["timings"].get("symmetry_detection", 0)
        + reduced["timings"].get("symmetry_grid_rebuild", 0)
    )
    time_saved = full_solve - red_solve
    print(f"  Symmetry overhead (detect+rebuild): {_format_time(sym_overhead)}")
    print(f"  Freq-solve time saved:              {_format_time(time_saved)}")
    if time_saved > 0:
        print(f"  Net benefit:                        {_format_time(time_saved - sym_overhead)}")
    else:
        print(f"  Net PENALTY:                        {_format_time(sym_overhead - time_saved)}")
    print()

    if speedup < 1.0:
        print("  *** WARNING: Symmetry-reduced model is SLOWER overall! ***")
        print("  Root causes to investigate:")
        print("    1. O(N*M) vertex matching in _check_plane_symmetry (symmetry.py lines 311-333)")
        print("       Each positive vertex is matched against ALL negative vertices via np.linalg.norm.")
        print("       For N vertices, this is O(N^2) — dominates for large meshes.")
        print("    2. Grid rebuild overhead: bempp.grid_from_element_data re-creates the full grid object.")
        print("    3. For small meshes, the overhead exceeds the BEM solve savings.")
        print()


def create_manually_halved_mesh(mesh_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a manually halved mesh by keeping only X >= 0 vertices/triangles.
    This bypasses the symmetry detection and simulates what a proper
    half-model mesh would look like.
    """
    from solver.mesh import prepare_mesh

    mesh = mesh_data["mesh"]
    verts = mesh["original_vertices"]  # (3, N)
    indices = mesh["original_indices"]  # (3, M)
    tags = mesh["original_surface_tags"]  # (M,)

    # Keep vertices with X >= -1e-6 (include on-plane vertices)
    keep_mask = verts[0, :] >= -1e-6
    old_to_new = np.full(verts.shape[1], -1, dtype=int)
    new_idx = 0
    for old_idx in range(verts.shape[1]):
        if keep_mask[old_idx]:
            old_to_new[old_idx] = new_idx
            new_idx += 1

    reduced_verts = verts[:, keep_mask]

    # Keep triangles where all 3 vertices are kept
    if indices.shape[0] == 3:
        indices_T = indices.T  # (M, 3)
    else:
        indices_T = indices

    kept_tris = []
    kept_tags = []
    for tri_idx in range(indices_T.shape[0]):
        tri = indices_T[tri_idx, :]
        if np.all(keep_mask[tri]):
            new_tri = [old_to_new[v] for v in tri]
            kept_tris.append(new_tri)
            if tags is not None and tri_idx < len(tags):
                kept_tags.append(tags[tri_idx])
            else:
                kept_tags.append(1)

    reduced_indices = np.array(kept_tris, dtype=int)
    reduced_tags = np.array(kept_tags, dtype=int)

    # Convert back to flat lists for prepare_mesh
    flat_verts = reduced_verts.T.ravel().tolist()
    flat_indices = reduced_indices.ravel().tolist()

    t0 = time.perf_counter()
    halved_mesh = prepare_mesh(
        flat_verts,
        flat_indices,
        surface_tags=reduced_tags.tolist(),
        mesh_metadata={"units": "m", "unitScaleToMeter": 1.0},
        use_gmsh=False,
    )
    prep_time = time.perf_counter() - t0

    return {
        "mesh": halved_mesh,
        "occ_build_time": mesh_data["occ_build_time"],
        "prepare_time": prep_time,
        "occ_stats": mesh_data["occ_stats"],
        "original_verts": verts.shape[1],
        "original_tris": indices_T.shape[0],
        "halved_verts": reduced_verts.shape[1],
        "halved_tris": len(kept_tris),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BEM symmetry performance benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--frequencies", type=int, default=3,
        help="Number of frequency points to solve",
    )
    parser.add_argument(
        "--freq-range", nargs=2, type=float, default=[500.0, 2000.0],
        metavar=("LOW", "HIGH"),
        help="Frequency range in Hz",
    )
    parser.add_argument(
        "--skip-directivity", action="store_true",
        help="Skip directivity post-processing",
    )
    args = parser.parse_args()

    freq_range = (args.freq_range[0], args.freq_range[1])

    print("Step 1: Building OCC mesh (shared between both runs)...")
    mesh_data = build_mesh_once(args.frequencies, freq_range)
    print(f"  OCC build: {_format_time(mesh_data['occ_build_time'])}")
    print(f"  Mesh prep: {_format_time(mesh_data['prepare_time'])}")
    occ_stats = mesh_data["occ_stats"]
    print(f"  Nodes: {occ_stats.get('nodeCount', '?')}, Elements: {occ_stats.get('elementCount', '?')}")
    print()

    print("Step 2: Testing symmetry detection on OCC mesh...")
    from solver.symmetry import detect_geometric_symmetry
    verts = mesh_data["mesh"]["original_vertices"]
    sym_type, sym_planes = detect_geometric_symmetry(verts, tolerance=1e-3)
    print(f"  Detected: {sym_type} (planes: {sym_planes})")
    if sym_type.value == "full":
        print()
        print("  *** KEY FINDING: Symmetry NOT detected on OCC mesh! ***")
        print("  The Gmsh free mesher does not produce mirror-symmetric vertices.")
        print("  Even though the OCC geometry is perfectly symmetric, the mesh")
        print("  vertices differ by up to several mm across the symmetry plane.")
        print("  This means the symmetry code path (evaluate_symmetry_policy)")
        print("  will never trigger for OCC-built meshes.")
        print()
        print("  The quadrants=1234 enforcement in simulation_runner.py exists")
        print("  because the OCC builder cannot produce partial-domain meshes")
        print("  (curve loop errors for quadrants != 1234).")
        print()
    print()

    print("Step 3: Solving FULL model (symmetry disabled, warmup run)...")
    # Run once to warm up numba JIT, then run again for real timing
    _ = run_solve_case(
        "WARMUP",
        mesh_data,
        enable_symmetry=False,
        num_frequencies=1,
        frequency_range=freq_range,
        skip_directivity=True,
    )
    print("  JIT warmup complete.")
    print()

    print("Step 4: Solving FULL model (symmetry disabled)...")
    full_case = run_solve_case(
        "FULL MODEL",
        mesh_data,
        enable_symmetry=False,
        num_frequencies=args.frequencies,
        frequency_range=freq_range,
        skip_directivity=args.skip_directivity,
    )
    print(f"  Done. DOF={full_case['bem_dof_p1']}, "
          f"solve time={_format_time(full_case['timings']['frequency_solve_total'])}")
    print()

    print("Step 5: Solving REDUCED model (symmetry enabled)...")
    reduced_case = run_solve_case(
        "SYMMETRY-ENABLED MODEL",
        mesh_data,
        enable_symmetry=True,
        num_frequencies=args.frequencies,
        frequency_range=freq_range,
        skip_directivity=args.skip_directivity,
    )
    print(f"  Done. DOF={reduced_case['bem_dof_p1']}, "
          f"solve time={_format_time(reduced_case['timings']['frequency_solve_total'])}")
    print()

    print_comparison(full_case, reduced_case)

    # Step 6: Test with manually halved mesh to show what SHOULD happen
    print()
    print("=" * 78)
    print("MANUAL HALF-MODEL TEST")
    print("  (Bypasses symmetry detection — manually slices mesh at X=0)")
    print("=" * 78)
    print()

    print("Step 6: Creating manually halved mesh...")
    halved_data = create_manually_halved_mesh(mesh_data)
    print(f"  Original: {halved_data['original_verts']} verts, {halved_data['original_tris']} tris")
    print(f"  Halved:   {halved_data['halved_verts']} verts, {halved_data['halved_tris']} tris")
    ratio = halved_data['original_tris'] / halved_data['halved_tris'] if halved_data['halved_tris'] > 0 else 0
    print(f"  Reduction: {ratio:.2f}x")
    print()

    print("Step 7: Solving HALVED model...")
    halved_case = run_solve_case(
        "MANUALLY HALVED MODEL",
        halved_data,
        enable_symmetry=False,
        num_frequencies=args.frequencies,
        frequency_range=freq_range,
        skip_directivity=args.skip_directivity,
    )
    print(f"  Done. DOF={halved_case['bem_dof_p1']}, "
          f"solve time={_format_time(halved_case['timings']['frequency_solve_total'])}")
    print()

    print_comparison(full_case, halved_case)


if __name__ == "__main__":
    main()
