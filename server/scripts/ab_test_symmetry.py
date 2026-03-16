#!/usr/bin/env python3
"""
A/B Test: Half-Model (quadrants=12) vs Full-Model (quadrants=1234) Symmetry.

Follows the tessellation-last principle: the half-model mesh is built by the
OCC builder with quadrants=12 (geometry cut BEFORE tessellation), not by
clipping an already-tessellated full-model mesh.

Runs the BEM solver directly (bypassing the API) with numba backend to compare
half-model vs full-model directivity results. They must match within 0.5 dB.

Usage (run from server/ directory):
    python3 scripts/ab_test_symmetry.py
    python3 scripts/ab_test_symmetry.py --frequencies 5
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("BEMPP_DEFAULT_DEVICE_INTERFACE", "numba")

import numpy as np

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
for name in ("bempp", "bempp_cl", "numba", "gmsh", "solver", "opencl"):
    logging.getLogger(name).setLevel(logging.ERROR)

DB_THRESHOLD = 0.5


def _base_params():
    """Return default R-OSSE horn params (full circle)."""
    from contracts import WaveguideParamsRequest
    return WaveguideParamsRequest(
        formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
        r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
        throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
        gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
        quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
        wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
    )


def build_mesh(symmetry_cut=None):
    """Build mesh using OCC builder, with optional B-Rep symmetry cut.

    Args:
        symmetry_cut: ``"yz"`` to cut at X=0 (keep X>=0), or ``None`` for full model.
    """
    from solver.waveguide_builder import build_waveguide_mesh
    from solver.mesh import prepare_mesh

    params = _base_params().model_dump()
    # Always build full geometry; symmetry_cut handles the B-Rep clipping.
    params["quadrants"] = 1234
    effective_q = 12 if symmetry_cut == "yz" else 1234

    t0 = time.perf_counter()
    occ_result = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=symmetry_cut)
    build_time = time.perf_counter() - t0

    canonical = occ_result["canonical_mesh"]
    mesh_metadata = {
        "units": "mm",
        "unitScaleToMeter": 0.001,
        "meshStrategy": "occ_adaptive",
        "requestedQuadrants": effective_q,
        "effectiveQuadrants": effective_q,
    }
    mesh = prepare_mesh(
        canonical["vertices"], canonical["indices"],
        surface_tags=canonical["surfaceTags"],
        mesh_metadata=mesh_metadata,
        use_gmsh=False,
    )
    return mesh, build_time


def run_solve(label, mesh, frequencies, compute_directivity=True):
    """Run BEM solve with parameter-driven symmetry."""
    from solver.solve_optimized import HornBEMSolver
    from solver.symmetry import evaluate_symmetry_policy, create_mirror_grid, SymmetryPlane
    from solver.deps import bempp_api
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance
    from solver.directivity_correct import calculate_directivity_patterns_correct

    grid = mesh["grid"]
    original_vertices = mesh["original_vertices"]
    original_indices = mesh["original_indices"]
    original_tags = mesh["original_surface_tags"]
    throat_elements = mesh["throat_elements"]
    physical_tags = mesh["surface_tags"]
    mesh_metadata = mesh.get("mesh_metadata", {})
    c, rho = 343.0, 1.21

    # Get quadrants from metadata
    quadrants_value = mesh_metadata.get(
        "requestedQuadrants", mesh_metadata.get("effectiveQuadrants", 1234)
    )

    # Symmetry detection
    t0 = time.perf_counter()
    sym_result = evaluate_symmetry_policy(
        vertices=original_vertices, indices=original_indices,
        surface_tags=original_tags, throat_elements=throat_elements,
        enable_symmetry=True, tolerance=1e-3, quadrants=quadrants_value,
    )
    sym_time = time.perf_counter() - t0

    policy = sym_result["policy"]
    applied = policy.get("applied", False)

    if applied:
        rv = sym_result["reduced_vertices"]
        ri = sym_result["reduced_indices"]
        rt = sym_result["reduced_surface_tags"]
        grid = bempp_api.Grid(rv, ri, rt)
        physical_tags = rt
        throat_elements = np.where(rt == 2)[0]

    # Solver construction
    t0 = time.perf_counter()
    solver = HornBEMSolver(
        grid=grid, physical_tags=physical_tags, sound_speed=c, rho=rho,
        tag_throat=2, boundary_interface="numba", potential_interface="numba",
        bem_precision="double", use_burton_miller=True,
    )

    # Set up image-source operators for symmetry reduction
    if applied:
        sym_info = sym_result.get("symmetry_info") or sym_result.get("symmetry", {})
        plane_str_map = {p.value: p for p in SymmetryPlane}
        sym_planes_enum = [
            plane_str_map[s] for s in (sym_info.get("symmetry_planes") or [])
            if s in plane_str_map
        ]
        if sym_planes_enum:
            mirror_grids = create_mirror_grid(rv, ri, sym_planes_enum)
            solver._assemble_image_operators(mirror_grids, sym_planes_enum, sym_info)
    construct_time = time.perf_counter() - t0

    obs_frame = infer_observation_frame(grid, observation_origin="mouth")
    obs_info = resolve_safe_observation_distance(grid, 2.0, obs_frame)
    obs_dist = float(obs_info["effective_distance_m"])

    # Solve frequencies
    solutions = []
    t0 = time.perf_counter()
    for freq in frequencies:
        try:
            spl, impedance, di, solution, iters = solver._solve_single_frequency(
                freq, observation_frame=obs_frame, observation_distance_m=obs_dist,
            )
            solutions.append((freq, spl, impedance, di, solution))
        except Exception as exc:
            print(f"  WARNING: {label} freq {freq:.0f} Hz failed: {exc}")
            solutions.append(None)
    solve_time = time.perf_counter() - t0

    # Directivity — re-expand to full mesh if image-source was used
    directivity = None
    dir_time = 0.0
    if compute_directivity:
        valid = [(i, s) for i, s in enumerate(solutions) if s is not None]
        if valid:
            idxs, filtered = zip(*valid)
            solution_tuples = [s[4] for s in filtered]
            polar_config = {"angle_range": [0, 180, 37], "norm_angle": 5.0, "distance": obs_dist}

            dir_grid = grid
            dir_frame = obs_frame
            dir_solutions = solution_tuples
            if solver.mirror_spaces and solver.mirror_grids:
                try:
                    all_v = [grid.vertices]
                    all_i = [grid.elements]
                    offset = grid.vertices.shape[1]
                    for mg_v, mg_i in solver.mirror_grids:
                        all_v.append(mg_v)
                        all_i.append(mg_i + offset)
                        offset += mg_v.shape[1]
                    full_v = np.concatenate(all_v, axis=1)
                    full_i = np.concatenate(all_i, axis=1)
                    dir_grid = bempp_api.Grid(full_v, full_i)
                    full_p1 = bempp_api.function_space(dir_grid, "P", 1)
                    full_dp0 = bempp_api.function_space(dir_grid, "DP", 0)
                    dir_frame = infer_observation_frame(dir_grid, observation_origin="mouth")
                    n_mirrors = len(solver.mirror_grids)
                    dir_solutions = []
                    for sol_tuple in solution_tuples:
                        p_t, u_t, _, _ = sol_tuple
                        p_c = np.tile(np.asarray(p_t.coefficients), 1 + n_mirrors)
                        if len(p_c) != full_p1.global_dof_count:
                            t_ = full_p1.global_dof_count
                            p_c = p_c[:t_] if len(p_c) > t_ else np.pad(p_c, (0, t_ - len(p_c)))
                        u_c = np.tile(np.asarray(u_t.coefficients), 1 + n_mirrors)
                        if len(u_c) != full_dp0.global_dof_count:
                            t_ = full_dp0.global_dof_count
                            u_c = u_c[:t_] if len(u_c) > t_ else np.pad(u_c, (0, t_ - len(u_c)))
                        dir_solutions.append((
                            bempp_api.GridFunction(full_p1, coefficients=p_c),
                            bempp_api.GridFunction(full_dp0, coefficients=u_c),
                            full_p1, full_dp0,
                        ))
                except Exception as exc:
                    print(f"  WARNING: {label} full-mesh re-expansion failed: {exc}")
                    dir_grid = grid
                    dir_frame = obs_frame
                    dir_solutions = solution_tuples

            t0 = time.perf_counter()
            try:
                directivity = calculate_directivity_patterns_correct(
                    dir_grid, frequencies[list(idxs)], c, rho, dir_solutions, polar_config,
                    device_interface="numba", precision="double", observation_frame=dir_frame,
                )
            except Exception as exc:
                print(f"  WARNING: {label} directivity failed: {exc}")
            dir_time = time.perf_counter() - t0

    return {
        "label": label,
        "quadrants": quadrants_value,
        "symmetry_applied": applied,
        "symmetry_type": policy.get("detected_symmetry_type", "none"),
        "dof_p1": solver.p1_space.global_dof_count,
        "dof_dp0": solver.dp0_space.global_dof_count,
        "timings": {"symmetry": sym_time, "construct": construct_time,
                    "solve": solve_time, "directivity": dir_time,
                    "total": sym_time + construct_time + solve_time + dir_time},
        "solutions": solutions,
        "directivity": directivity,
        "frequencies": frequencies,
    }


def compare_directivity(full, half):
    d_full = full.get("directivity")
    d_half = half.get("directivity")
    if d_full is None or d_half is None:
        return False, {"error": "Directivity missing"}

    overall_pass = True
    overall_max = 0.0
    results = {}

    for plane in sorted(set(d_full) & set(d_half)):
        fa = np.array(d_full[plane])
        ha = np.array(d_half[plane])
        if fa.shape != ha.shape:
            results[plane] = {"error": f"Shape mismatch: {fa.shape} vs {ha.shape}"}
            overall_pass = False
            continue
        diff = np.abs(fa - ha)
        mx = float(np.nanmax(diff))
        results[plane] = {
            "max_diff_dB": round(mx, 4),
            "mean_diff_dB": round(float(np.nanmean(diff)), 4),
            "p95_diff_dB": round(float(np.nanpercentile(diff, 95)), 4),
            "pass": mx <= DB_THRESHOLD,
        }
        overall_max = max(overall_max, mx)
        if mx > DB_THRESHOLD:
            overall_pass = False

    return overall_pass, {"planes": results, "max_diff_dB": round(overall_max, 4), "pass": overall_pass}


def main():
    parser = argparse.ArgumentParser(description="A/B test: symmetry half vs full model")
    parser.add_argument("--frequencies", type=int, default=5)
    parser.add_argument("--freq-range", nargs=2, type=float, default=[500.0, 4000.0])
    parser.add_argument("--skip-directivity", action="store_true")
    args = parser.parse_args()

    freqs = np.linspace(args.freq_range[0], args.freq_range[1], args.frequencies)

    print("=" * 70)
    print("A/B TEST: SYMMETRY HALF-MODEL vs FULL-MODEL")
    print("  (Geometry-first: half mesh built by OCC with quadrants=12)")
    print("=" * 70)
    print(f"Frequencies: {args.frequencies} pts, {args.freq_range[0]:.0f}–{args.freq_range[1]:.0f} Hz")
    print(f"Threshold: {DB_THRESHOLD} dB max difference")
    print()

    # Build TWO separate meshes (tessellation-last principle)
    print("Building FULL model mesh (no symmetry cut)...")
    mesh_full, build_time_full = build_mesh(symmetry_cut=None)
    print(f"  Build: {build_time_full:.2f}s, {mesh_full['original_vertices'].shape[1]} verts, "
          f"{mesh_full['original_indices'].shape[1]} tris")

    print("Building HALF model mesh (symmetry_cut='yz', B-Rep cut at X=0)...")
    mesh_half, build_time_half = build_mesh(symmetry_cut="yz")
    print(f"  Build: {build_time_half:.2f}s, {mesh_half['original_vertices'].shape[1]} verts, "
          f"{mesh_half['original_indices'].shape[1]} tris")
    print()

    print("Warming up numba JIT...")
    _ = run_solve("WARMUP", mesh_full, freqs[:1], compute_directivity=False)
    print("  Done.")
    print()

    # Full model (no symmetry)
    print("Running FULL MODEL (quadrants=1234, no symmetry reduction)...")
    full = run_solve("FULL", mesh_full, freqs, not args.skip_directivity)
    print(f"  DOF: P1={full['dof_p1']}, DP0={full['dof_dp0']}")
    print(f"  Symmetry: {full['symmetry_type']} (applied={full['symmetry_applied']})")
    print(f"  Solve: {full['timings']['solve']:.2f}s, Total: {full['timings']['total']:.2f}s")
    print()

    # Half model (built by OCC as half, image source method in BEM)
    print("Running HALF MODEL (OCC-built quadrants=12, image source BEM)...")
    half = run_solve("HALF", mesh_half, freqs, not args.skip_directivity)
    print(f"  DOF: P1={half['dof_p1']}, DP0={half['dof_dp0']}")
    print(f"  Symmetry: {half['symmetry_type']} (applied={half['symmetry_applied']})")
    print(f"  Solve: {half['timings']['solve']:.2f}s, Total: {half['timings']['total']:.2f}s")
    print()

    # Performance
    print("=" * 70)
    print("PERFORMANCE")
    print("=" * 70)
    print(f"  {'Metric':<25s} {'Full':>10s} {'Half':>10s} {'Ratio':>8s}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8}")
    print(f"  {'DOF (P1)':<25s} {full['dof_p1']:>10d} {half['dof_p1']:>10d} "
          f"{full['dof_p1']/max(half['dof_p1'],1):>7.2f}x")
    for phase in ("symmetry", "construct", "solve", "directivity", "total"):
        ft, ht = full["timings"][phase], half["timings"][phase]
        r = ft / ht if ht > 0 else float("inf")
        print(f"  {phase:<25s} {ft:>9.2f}s {ht:>9.2f}s {r:>7.2f}x")
    print()

    # On-axis SPL comparison
    print("=" * 70)
    print("ON-AXIS SPL COMPARISON")
    print("=" * 70)
    spl_diffs = []
    all_pass = True
    for i, freq in enumerate(freqs):
        fs, hs = full["solutions"][i], half["solutions"][i]
        if fs is None or hs is None:
            print(f"  {freq:7.0f} Hz: SKIPPED")
            continue
        diff = abs(fs[1] - hs[1])
        spl_diffs.append(diff)
        status = "OK" if diff <= DB_THRESHOLD else "WARN"
        if diff > DB_THRESHOLD:
            all_pass = False
        print(f"  {freq:7.0f} Hz: full={fs[1]:7.2f} dB, half={hs[1]:7.2f} dB, "
              f"diff={diff:.3f} dB [{status}]")

    if spl_diffs:
        max_spl_diff = max(spl_diffs)
        print(f"\n  Max SPL diff: {max_spl_diff:.3f} dB {'[PASS ✓]' if max_spl_diff <= DB_THRESHOLD else '[FAIL ✗]'}")
    print()

    # Directivity comparison
    if not args.skip_directivity:
        print("=" * 70)
        print("DIRECTIVITY COMPARISON")
        print("=" * 70)
        passed, comparison = compare_directivity(full, half)
        print(f"  Overall max diff: {comparison.get('max_diff_dB', '?')} dB")

        for plane, info in comparison.get("planes", {}).items():
            if "error" in info:
                print(f"  {plane}: ERROR — {info['error']}")
            else:
                status = "PASS ✓" if info["pass"] else "FAIL ✗"
                print(f"  {plane}: max={info['max_diff_dB']:.4f} dB, "
                      f"mean={info['mean_diff_dB']:.4f} dB, p95={info['p95_diff_dB']:.4f} dB [{status}]")

        print()
        if comparison.get("pass"):
            print("  *** PASS: All planes match within 0.5 dB ***")
        else:
            print("  *** FAIL: Some planes exceed 0.5 dB threshold ***")
        print()

    print("Done.")


if __name__ == "__main__":
    main()
