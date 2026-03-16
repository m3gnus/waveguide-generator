#!/usr/bin/env python3
"""
Diagnostic script for image source BEM symmetry method.

Tests each component in isolation:
  1. B-Rep symmetry cut: does it produce a proper half mesh?
  2. Image source operators: do they work on a known-good half mesh?
  3. Full pipeline: half mesh + image source → matches full model?

Run from server/ directory:
    python3 scripts/diagnose_image_source.py
    python3 scripts/diagnose_image_source.py --skip-solve  (mesh diagnostics only)
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

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
for name in ("bempp", "bempp_cl", "numba", "gmsh", "solver", "opencl"):
    logging.getLogger(name).setLevel(logging.WARNING)


def _base_params():
    from contracts import WaveguideParamsRequest
    return WaveguideParamsRequest(
        formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
        r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
        throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
        gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
        quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
        wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
    )


# -----------------------------------------------------------------------
# STEP 1: B-Rep symmetry cut diagnostics
# -----------------------------------------------------------------------
def diagnose_brep_cut():
    """Test whether _apply_symmetry_cut_yz produces a valid half mesh."""
    from solver.waveguide_builder import build_waveguide_mesh

    print("=" * 70)
    print("STEP 1: B-Rep SYMMETRY CUT DIAGNOSTICS")
    print("=" * 70)

    params = _base_params().model_dump()
    params["quadrants"] = 1234

    # Build full mesh
    print("\n  Building FULL mesh (no symmetry cut)...")
    t0 = time.perf_counter()
    full_result = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=None)
    full_time = time.perf_counter() - t0
    full_mesh = full_result["canonical_mesh"]
    full_verts = np.array(full_mesh["vertices"]).reshape(-1, 3)
    full_tris = np.array(full_mesh["indices"]).reshape(-1, 3)
    print(f"    Vertices: {len(full_verts)}, Triangles: {len(full_tris)}, Time: {full_time:.2f}s")

    # Analyse symmetry of full mesh
    x_coords = full_verts[:, 0]
    x_min, x_max = x_coords.min(), x_coords.max()
    n_positive = np.sum(x_coords > 1e-6)
    n_negative = np.sum(x_coords < -1e-6)
    n_on_plane = np.sum(np.abs(x_coords) <= 1e-6)
    print(f"    X range: [{x_min:.4f}, {x_max:.4f}]")
    print(f"    Vertices X>0: {n_positive}, X<0: {n_negative}, X≈0: {n_on_plane}")
    print(f"    Asymmetry ratio: {abs(n_positive - n_negative) / max(n_positive, 1):.4f} "
          f"(0 = perfectly symmetric)")

    # Build half mesh with B-Rep cut
    print("\n  Building HALF mesh (symmetry_cut='yz')...")
    t0 = time.perf_counter()
    try:
        half_result = build_waveguide_mesh(params, include_canonical=True, symmetry_cut="yz")
        half_time = time.perf_counter() - t0
    except Exception as exc:
        print(f"    FAILED: {exc}")
        print("\n    *** B-Rep cut failed. Cannot proceed with image source testing. ***")
        return None, None

    half_mesh = half_result["canonical_mesh"]
    half_verts = np.array(half_mesh["vertices"]).reshape(-1, 3)
    half_tris = np.array(half_mesh["indices"]).reshape(-1, 3)
    print(f"    Vertices: {len(half_verts)}, Triangles: {len(half_tris)}, Time: {half_time:.2f}s")

    # Validate half mesh
    hx = half_verts[:, 0]
    hx_min = hx.min()
    n_violating = np.sum(hx < -1e-6)
    reduction = len(full_tris) / max(len(half_tris), 1)

    print(f"    X min: {hx_min:.6f}")
    print(f"    Vertices violating X>=0: {n_violating}")
    print(f"    Element reduction ratio: {reduction:.2f}x (expect ~2.0x)")

    if n_violating > 0:
        print(f"\n    *** FAIL: {n_violating} vertices have X < 0. B-Rep cut is incomplete. ***")
        cut_ok = False
    elif reduction < 1.5:
        print(f"\n    *** WARN: Only {reduction:.2f}x reduction. Cut may not be effective. ***")
        cut_ok = False
    else:
        print(f"\n    *** PASS: Half mesh is valid (all X >= 0, {reduction:.2f}x reduction). ***")
        cut_ok = True

    # Check surface tags
    full_tags = set(full_mesh.get("surfaceTags", []))
    half_tags = set(half_mesh.get("surfaceTags", []))
    print(f"\n    Full mesh tag values: {sorted(full_tags)}")
    print(f"    Half mesh tag values: {sorted(half_tags)}")
    if half_tags == full_tags:
        print("    Tags match ✓")
    else:
        print(f"    *** WARN: Tag mismatch. Missing: {full_tags - half_tags}, Extra: {half_tags - full_tags} ***")

    return (full_result, full_mesh), (half_result, half_mesh) if cut_ok else (None, None)


# -----------------------------------------------------------------------
# STEP 2: Mirror grid diagnostics
# -----------------------------------------------------------------------
def diagnose_mirror_grid(half_mesh):
    """Test that create_mirror_grid produces correct mirrored geometry."""
    from solver.symmetry import create_mirror_grid, SymmetryPlane

    print("\n" + "=" * 70)
    print("STEP 2: MIRROR GRID DIAGNOSTICS")
    print("=" * 70)

    half_verts = np.array(half_mesh["vertices"]).reshape(-1, 3).T  # (3, N)
    half_indices = np.array(half_mesh["indices"]).reshape(-1, 3).T  # (3, M)

    mirror_grids = create_mirror_grid(half_verts, half_indices, [SymmetryPlane.YZ])

    if not mirror_grids:
        print("  *** FAIL: create_mirror_grid returned empty list ***")
        return None

    mg_verts, mg_indices = mirror_grids[0]
    print(f"  Mirror grid: {mg_verts.shape[1]} vertices, {mg_indices.shape[1]} triangles")

    # Check X coordinates are flipped
    orig_x = half_verts[0, :]
    mirror_x = mg_verts[0, :]
    x_flip_ok = np.allclose(orig_x, -mirror_x)
    print(f"  X-coordinate flip: {'OK ✓' if x_flip_ok else 'FAIL ✗'}")

    # Check Y, Z are preserved
    yz_ok = np.allclose(half_verts[1:, :], mg_verts[1:, :])
    print(f"  Y,Z coordinates preserved: {'OK ✓' if yz_ok else 'FAIL ✗'}")

    # Check winding reversal
    orig_i = half_indices.copy()
    mir_i = mg_indices.copy()
    winding_reversed = np.allclose(orig_i[1, :], mir_i[2, :]) and np.allclose(orig_i[2, :], mir_i[1, :])
    print(f"  Winding reversed (rows 1↔2): {'OK ✓' if winding_reversed else 'FAIL ✗'}")

    # Check that mirror vertices are all X <= 0
    mx_max = mg_verts[0, :].max()
    mirror_side_ok = mx_max <= 1e-6
    print(f"  All mirror verts X<=0: {'OK ✓' if mirror_side_ok else f'FAIL ✗ (max X = {mx_max:.6f})'}")

    all_ok = x_flip_ok and yz_ok and winding_reversed and mirror_side_ok
    print(f"\n  *** {'PASS' if all_ok else 'FAIL'}: Mirror grid {'is' if all_ok else 'is NOT'} correct ***")
    return mirror_grids


# -----------------------------------------------------------------------
# STEP 3: Mesh validation guard
# -----------------------------------------------------------------------
def diagnose_mesh_validity_for_image_source(half_mesh, symmetry_plane="yz"):
    """Verify mesh is actually a half mesh before image source operators."""
    print("\n" + "=" * 70)
    print("STEP 3: MESH VALIDITY FOR IMAGE SOURCE")
    print("=" * 70)

    verts = np.array(half_mesh["vertices"]).reshape(-1, 3)

    if symmetry_plane == "yz":
        coords = verts[:, 0]  # X coordinates
        axis_name = "X"
    elif symmetry_plane == "xy":
        coords = verts[:, 2]  # Z coordinates
        axis_name = "Z"
    else:
        print(f"  Unknown symmetry plane: {symmetry_plane}")
        return False

    n_violating = np.sum(coords < -1e-6)
    n_total = len(coords)
    pct_violating = 100.0 * n_violating / n_total if n_total > 0 else 0

    print(f"  Total vertices: {n_total}")
    print(f"  Vertices with {axis_name} < 0: {n_violating} ({pct_violating:.1f}%)")

    if n_violating > 0 and pct_violating > 5:
        print(f"\n  *** FAIL: Mesh is NOT a half mesh. {pct_violating:.1f}% vertices on wrong side. ***")
        print(f"  *** Image source operators will produce ~6+ dB errors on a full mesh. ***")
        return False
    elif n_violating > 0:
        print(f"\n  *** WARN: {n_violating} vertices slightly past symmetry plane (numerical noise?). ***")
        return True
    else:
        print(f"\n  *** PASS: Mesh is a valid half mesh (all {axis_name} >= 0). ***")
        return True


# -----------------------------------------------------------------------
# STEP 4: BEM solve comparison
# -----------------------------------------------------------------------
def diagnose_solve(full_data, half_data, frequencies):
    """Run BEM solve on full and half meshes, compare results."""
    from solver.solve_optimized import HornBEMSolver
    from solver.symmetry import create_mirror_grid, SymmetryPlane, evaluate_symmetry_policy
    from solver.deps import bempp_api
    from solver.mesh import prepare_mesh
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance

    print("\n" + "=" * 70)
    print("STEP 4: BEM SOLVE COMPARISON")
    print("=" * 70)

    c, rho = 343.0, 1.21

    def _prepare(canonical_mesh, quadrants_val):
        mm = {
            "units": "mm", "unitScaleToMeter": 0.001,
            "meshStrategy": "occ_adaptive",
            "requestedQuadrants": quadrants_val,
            "effectiveQuadrants": quadrants_val,
        }
        return prepare_mesh(
            canonical_mesh["vertices"], canonical_mesh["indices"],
            surface_tags=canonical_mesh["surfaceTags"],
            mesh_metadata=mm, use_gmsh=False,
        )

    # Prepare full mesh (no symmetry)
    full_mesh = _prepare(full_data[1], 1234)
    full_grid = full_mesh["grid"]
    print(f"\n  Full mesh: P1 DOFs = {bempp_api.function_space(full_grid, 'P', 1).global_dof_count}, "
          f"DP0 DOFs = {bempp_api.function_space(full_grid, 'DP', 0).global_dof_count}")

    # Prepare half mesh (with symmetry)
    half_mesh = _prepare(half_data[1], 12)
    half_grid = half_mesh["grid"]
    print(f"  Half mesh: P1 DOFs = {bempp_api.function_space(half_grid, 'P', 1).global_dof_count}, "
          f"DP0 DOFs = {bempp_api.function_space(half_grid, 'DP', 0).global_dof_count}")

    # --- Full model solve (reference) ---
    print("\n  Solving FULL model (reference)...")
    full_solver = HornBEMSolver(
        grid=full_grid, physical_tags=full_mesh["surface_tags"],
        sound_speed=c, rho=rho, tag_throat=2,
        boundary_interface="numba", potential_interface="numba",
        bem_precision="double", use_burton_miller=True,
    )
    full_frame = infer_observation_frame(full_grid, observation_origin="mouth")
    full_obs = resolve_safe_observation_distance(full_grid, 2.0, full_frame)
    full_dist = float(full_obs["effective_distance_m"])

    full_results = []
    for freq in frequencies:
        t0 = time.perf_counter()
        spl, imp, di, sol, iters = full_solver._solve_single_frequency(
            freq, observation_frame=full_frame, observation_distance_m=full_dist,
        )
        dt = time.perf_counter() - t0
        full_results.append((freq, spl, dt))
        print(f"    {freq:7.0f} Hz: SPL = {spl:7.2f} dB ({dt:.2f}s)")

    # --- Half model solve (image source) ---
    print("\n  Solving HALF model (image source)...")

    # Run symmetry evaluation
    sym_result = evaluate_symmetry_policy(
        vertices=half_mesh["original_vertices"],
        indices=half_mesh["original_indices"],
        surface_tags=half_mesh["original_surface_tags"],
        throat_elements=half_mesh["throat_elements"],
        enable_symmetry=True, tolerance=1e-3, quadrants=12,
    )
    policy = sym_result["policy"]
    applied = policy.get("applied", False)
    print(f"    Symmetry applied: {applied}, type: {policy.get('detected_symmetry_type', '?')}")

    if applied:
        rv = sym_result["reduced_vertices"]
        ri = sym_result["reduced_indices"]
        rt = sym_result["reduced_surface_tags"]
        half_grid_sym = bempp_api.Grid(rv, ri, rt)
        half_tags_sym = rt
    else:
        half_grid_sym = half_grid
        half_tags_sym = half_mesh["surface_tags"]
        print("    WARNING: Symmetry not applied — results will be different from reference.")

    half_solver = HornBEMSolver(
        grid=half_grid_sym, physical_tags=half_tags_sym,
        sound_speed=c, rho=rho, tag_throat=2,
        boundary_interface="numba", potential_interface="numba",
        bem_precision="double", use_burton_miller=True,
    )

    # Set up image source operators
    if applied:
        sym_info = sym_result.get("symmetry_info") or sym_result.get("symmetry", {})
        planes = [SymmetryPlane.YZ]
        mirror_grids = create_mirror_grid(rv, ri, planes)
        half_solver._assemble_image_operators(mirror_grids, planes, sym_info)
        print(f"    Mirror grids: {len(mirror_grids)}")
        for i, (mgv, mgi) in enumerate(mirror_grids):
            print(f"      Grid {i}: {mgv.shape[1]} verts, {mgi.shape[1]} tris, "
                  f"X range [{mgv[0,:].min():.4f}, {mgv[0,:].max():.4f}]")

    # For half model with image source, use the FULL model's observation frame.
    # The half mesh has its mouth center at X>0, but the observation point for
    # image source BEM should be at X=0 (the symmetry plane) to match the full model.
    half_frame = infer_observation_frame(half_grid_sym, observation_origin="mouth")
    half_obs = resolve_safe_observation_distance(half_grid_sym, 2.0, half_frame)
    half_dist = float(half_obs["effective_distance_m"])

    if applied:
        half_frame = full_frame.copy()
        half_dist = full_dist

    # Check observation frames match
    print(f"\n    Full obs origin: {full_frame['origin_center']}")
    print(f"    Half obs origin: {half_frame['origin_center']}")
    print(f"    Full obs axis:   {full_frame['axis']}")
    print(f"    Half obs axis:   {half_frame['axis']}")
    print(f"    Full obs dist:   {full_dist:.4f} m")
    print(f"    Half obs dist:   {half_dist:.4f} m")

    half_results = []
    for freq in frequencies:
        t0 = time.perf_counter()
        try:
            spl, imp, di, sol, iters = half_solver._solve_single_frequency(
                freq, observation_frame=half_frame, observation_distance_m=half_dist,
            )
            dt = time.perf_counter() - t0
            half_results.append((freq, spl, dt))
            print(f"    {freq:7.0f} Hz: SPL = {spl:7.2f} dB ({dt:.2f}s)")
        except Exception as exc:
            print(f"    {freq:7.0f} Hz: FAILED — {exc}")
            half_results.append((freq, None, 0))

    # --- Compare ---
    print("\n  " + "-" * 50)
    print("  COMPARISON")
    print("  " + "-" * 50)
    max_diff = 0
    all_pass = True
    for (f1, spl1, _), (f2, spl2, _) in zip(full_results, half_results):
        if spl2 is None:
            print(f"  {f1:7.0f} Hz: SKIPPED (half solve failed)")
            all_pass = False
            continue
        diff = abs(spl1 - spl2)
        max_diff = max(max_diff, diff)
        status = "OK" if diff <= 0.5 else "FAIL"
        if diff > 0.5:
            all_pass = False
        print(f"  {f1:7.0f} Hz: full={spl1:7.2f}, half={spl2:7.2f}, diff={diff:.3f} dB [{status}]")

    print(f"\n  Max SPL difference: {max_diff:.3f} dB")

    if max_diff <= 0.5:
        print("  *** PASS: Image source method matches full model within 0.5 dB ***")
    elif max_diff <= 3.0:
        print("  *** MARGINAL: Differences suggest minor operator issues ***")
    elif max_diff <= 8.0:
        print("  *** FAIL: ~6 dB error suggests pressure doubling (full mesh used as half?) ***")
    else:
        print("  *** FAIL: Large errors suggest fundamental operator assembly issues ***")

    # Diagnostic: check if the half mesh observation point is on the symmetry plane
    obs_xyz = half_frame["origin_center"] + half_frame["axis"] * half_dist
    print(f"\n  Observation point: ({obs_xyz[0]:.6f}, {obs_xyz[1]:.6f}, {obs_xyz[2]:.6f})")
    print(f"  Obs point X coordinate: {obs_xyz[0]:.6f} (should be ≈0 for on-axis)")

    return all_pass


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Diagnose image source BEM symmetry")
    parser.add_argument("--skip-solve", action="store_true", help="Skip BEM solve (mesh diagnostics only)")
    parser.add_argument("--frequencies", type=int, default=3, help="Number of test frequencies")
    parser.add_argument("--freq-range", nargs=2, type=float, default=[500.0, 2000.0])
    args = parser.parse_args()

    print("=" * 70)
    print("IMAGE SOURCE BEM SYMMETRY DIAGNOSTIC")
    print("=" * 70)
    print()

    # Step 1: B-Rep cut
    full_data, half_data = diagnose_brep_cut()

    if half_data is None:
        print("\n*** B-Rep cut failed. Remaining steps cannot proceed. ***")
        print("*** Fix _apply_symmetry_cut_yz() in waveguide_builder.py first. ***")
        sys.exit(1)

    # Step 2: Mirror grid
    mirror_grids = diagnose_mirror_grid(half_data[1])

    # Step 3: Mesh validity
    mesh_valid = diagnose_mesh_validity_for_image_source(half_data[1])

    if args.skip_solve:
        print("\n*** Skipping BEM solve (--skip-solve). ***")
        sys.exit(0)

    # Step 4: BEM solve
    freqs = np.linspace(args.freq_range[0], args.freq_range[1], args.frequencies)
    solve_ok = diagnose_solve(full_data, half_data, freqs)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  B-Rep cut:      {'PASS ✓' if half_data else 'FAIL ✗'}")
    print(f"  Mirror grid:    {'PASS ✓' if mirror_grids else 'FAIL ✗'}")
    print(f"  Mesh validity:  {'PASS ✓' if mesh_valid else 'FAIL ✗'}")
    print(f"  BEM solve:      {'PASS ✓' if solve_ok else 'FAIL ✗'}")
    print()

    sys.exit(0 if solve_ok else 1)


if __name__ == "__main__":
    main()
