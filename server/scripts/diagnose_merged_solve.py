#!/usr/bin/env python3
"""
Diagnostic: solve on merged (half+mirror) grid as standard BEM.

Compares:
1. FULL (independent mesh, 700 elem) — reference
2. HALF+IMAGE (cross-grid image operators) — current approach
3. MERGED (half+mirror as single grid, standard BEM) — validates image method

If MERGED ≈ FULL, then the half mesh is a good discretization and the image
method SHOULD match.  If HALF+IMAGE ≠ MERGED, the cross-grid assembly is wrong.
"""
from __future__ import annotations
import os, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("BEMPP_DEFAULT_DEVICE_INTERFACE", "numba")
import numpy as np
logging.basicConfig(level=logging.WARNING)

def main():
    from solver.waveguide_builder import build_waveguide_mesh
    from solver.mesh import prepare_mesh
    from solver.solve_optimized import HornBEMSolver, _operator_kwargs
    from solver.symmetry import evaluate_symmetry_policy, create_mirror_grid, SymmetryPlane
    from solver.deps import bempp_api
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance
    from contracts import WaveguideParamsRequest
    from scipy.sparse.linalg import gmres as scipy_gmres

    freqs = [500.0, 1375.0, 2250.0, 4000.0]
    c, rho = 343.0, 1.21

    params = WaveguideParamsRequest(
        formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
        r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
        throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
        gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
        quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
        wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
    ).model_dump()
    params["quadrants"] = 1234

    # Build meshes
    print("Building meshes...")
    occ_full = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=None)
    cf = occ_full["canonical_mesh"]
    mesh_full = prepare_mesh(cf["vertices"], cf["indices"], surface_tags=cf["surfaceTags"],
                             mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001,
                                            "meshStrategy": "occ_adaptive",
                                            "requestedQuadrants": 1234, "effectiveQuadrants": 1234},
                             use_gmsh=False)

    occ_half = build_waveguide_mesh(params, include_canonical=True, symmetry_cut="yz")
    ch = occ_half["canonical_mesh"]
    mesh_half = prepare_mesh(ch["vertices"], ch["indices"], surface_tags=ch["surfaceTags"],
                             mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001,
                                            "meshStrategy": "occ_adaptive",
                                            "requestedQuadrants": 12, "effectiveQuadrants": 12},
                             use_gmsh=False)

    # Build merged grid (half + mirror, single grid)
    sym = evaluate_symmetry_policy(
        vertices=mesh_half["original_vertices"], indices=mesh_half["original_indices"],
        surface_tags=mesh_half["original_surface_tags"], throat_elements=mesh_half["throat_elements"],
        enable_symmetry=True, tolerance=1e-3, quadrants=12)
    rv, ri, rt = sym["reduced_vertices"], sym["reduced_indices"], sym["reduced_surface_tags"]
    mg = create_mirror_grid(rv, ri, [SymmetryPlane.YZ])
    mv, mi = mg[0]

    merged_v = np.concatenate([rv, mv], axis=1)
    merged_i = np.concatenate([ri, mi + rv.shape[1]], axis=1)
    merged_tags = np.concatenate([rt, rt])

    grid_full = mesh_full["grid"]
    grid_merged = bempp_api.Grid(merged_v, merged_i, merged_tags)

    print(f"  Full: {grid_full.number_of_elements} elem, {grid_full.vertices.shape[1]} verts")
    print(f"  Half: {rv.shape[1]} verts, {ri.shape[1]} tris")
    print(f"  Merged: {grid_merged.number_of_elements} elem, {grid_merged.vertices.shape[1]} verts")

    # Observation frame from full model
    obs_frame = infer_observation_frame(grid_full, observation_origin="mouth")
    obs_dist = float(resolve_safe_observation_distance(grid_full, 2.0, obs_frame)["effective_distance_m"])

    # For half model, project observation to X=0
    obs_frame_half = obs_frame.copy()
    obs_frame_half["origin_center"] = obs_frame["origin_center"].copy()
    obs_frame_half["origin_center"][0] = 0.0

    # Observation frame for merged model (recompute from merged grid)
    obs_frame_merged = infer_observation_frame(grid_merged, observation_origin="mouth")

    print(f"  Full obs origin: {obs_frame['origin_center']}")
    print(f"  Merged obs origin: {obs_frame_merged['origin_center']}")

    op_kw = _operator_kwargs("numba", "double")

    # Warm up
    print("\nWarming up numba...")
    _p1 = bempp_api.function_space(grid_full, "P", 1)
    _dp0 = bempp_api.function_space(grid_full, "DP", 0)
    _slp = bempp_api.operators.boundary.helmholtz.single_layer(_dp0, _p1, _p1, 1.0, **op_kw)
    _ = _slp.weak_form()
    print("  Done.")

    def solve_at_freq(grid, tags, freq, obs_frame_use, label):
        omega = 2 * np.pi * freq
        k = omega / c
        coupling = 1j / k

        p1 = bempp_api.function_space(grid, "P", 1)
        dp0 = bempp_api.function_space(grid, "DP", 0)
        id_p1 = bempp_api.operators.boundary.sparse.identity(p1, p1, p1)
        id_dp0 = bempp_api.operators.boundary.sparse.identity(dp0, p1, p1)

        dlp = bempp_api.operators.boundary.helmholtz.double_layer(p1, p1, p1, k, **op_kw)
        slp = bempp_api.operators.boundary.helmholtz.single_layer(dp0, p1, p1, k, **op_kw)
        hyp = bempp_api.operators.boundary.helmholtz.hypersingular(p1, p1, p1, k, **op_kw)
        adlp = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(dp0, p1, p1, k, **op_kw)

        A = (0.5 * id_p1 - dlp + coupling * hyp).weak_form().to_dense()

        throat = np.where(tags == 2)[0]
        vc = np.zeros(dp0.global_dof_count, dtype=np.complex128)
        vc[throat] = 1.0
        neumann = bempp_api.GridFunction(dp0, coefficients=1j * rho * omega * vc)
        rhs_gf = (-slp - coupling * (adlp + 0.5 * id_dp0)) * neumann
        b = np.asarray(rhs_gf.projections(p1))

        x, info = scipy_gmres(A, b, atol=1e-5, restart=100)
        p_gf = bempp_api.GridFunction(p1, coefficients=x)

        origin = obs_frame_use["origin_center"]
        obs_xyz = (origin + obs_frame_use["axis"] * obs_dist).reshape(3, 1)

        pot_kw = _operator_kwargs("numba", "double")
        dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(p1, obs_xyz, k, **pot_kw)
        slp_pot = bempp_api.operators.potential.helmholtz.single_layer(dp0, obs_xyz, k, **pot_kw)
        p_obs = dlp_pot * p_gf - slp_pot * neumann
        spl = 20 * np.log10(np.abs(p_obs[0, 0]) / 20e-6)
        return float(spl)

    print("\n" + "="*70)
    print(f"{'Freq':>8s} {'FULL':>10s} {'MERGED':>10s} {'diff':>8s}")
    print("="*70)

    for freq in freqs:
        spl_full = solve_at_freq(grid_full, mesh_full["surface_tags"], freq, obs_frame, "FULL")
        spl_merged = solve_at_freq(grid_merged, merged_tags, freq, obs_frame, "MERGED")
        diff = abs(spl_full - spl_merged)
        print(f"  {freq:7.0f} Hz: {spl_full:8.2f} dB  {spl_merged:8.2f} dB  {diff:7.3f} dB")

    print("="*70)
    print("\nIf MERGED ≈ FULL (<1 dB), the half mesh is a valid discretization")
    print("and the ~8 dB error is entirely from cross-grid operator assembly.")


if __name__ == "__main__":
    main()
