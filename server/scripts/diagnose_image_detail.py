#!/usr/bin/env python3
"""
Detailed diagnostic for the image source BEM error.

Dumps matrix norms, RHS contributions, and source strengths to identify
where the ~8 dB error comes from.
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

    freq = 500.0
    c, rho = 343.0, 1.21
    omega = 2 * np.pi * freq
    k = omega / c

    # Build half mesh
    params = WaveguideParamsRequest(
        formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
        r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
        throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
        gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
        quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
        wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
    ).model_dump()
    params["quadrants"] = 1234

    # Half mesh
    occ_half = build_waveguide_mesh(params, include_canonical=True, symmetry_cut="yz")
    ch = occ_half["canonical_mesh"]
    mesh_half = prepare_mesh(ch["vertices"], ch["indices"], surface_tags=ch["surfaceTags"],
                             mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001,
                                            "meshStrategy": "occ_adaptive",
                                            "requestedQuadrants": 12, "effectiveQuadrants": 12},
                             use_gmsh=False)

    # Full mesh
    occ_full = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=None)
    cf = occ_full["canonical_mesh"]
    mesh_full = prepare_mesh(cf["vertices"], cf["indices"], surface_tags=cf["surfaceTags"],
                             mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001,
                                            "meshStrategy": "occ_adaptive",
                                            "requestedQuadrants": 1234, "effectiveQuadrants": 1234},
                             use_gmsh=False)

    # Symmetry setup for half mesh
    sym = evaluate_symmetry_policy(
        vertices=mesh_half["original_vertices"], indices=mesh_half["original_indices"],
        surface_tags=mesh_half["original_surface_tags"], throat_elements=mesh_half["throat_elements"],
        enable_symmetry=True, tolerance=1e-3, quadrants=12)
    rv, ri, rt = sym["reduced_vertices"], sym["reduced_indices"], sym["reduced_surface_tags"]
    grid_half = bempp_api.Grid(rv, ri, rt)

    mg = create_mirror_grid(rv, ri, [SymmetryPlane.YZ])
    mv, mi = mg[0]
    grid_mirror = bempp_api.Grid(mv, mi)

    # Spaces
    p1_half = bempp_api.function_space(grid_half, "P", 1)
    dp0_half = bempp_api.function_space(grid_half, "DP", 0)
    p1_mirror = bempp_api.function_space(grid_mirror, "P", 1)
    dp0_mirror = bempp_api.function_space(grid_mirror, "DP", 0)

    print(f"Half mesh: {grid_half.number_of_elements} elements, P1={p1_half.global_dof_count}, DP0={dp0_half.global_dof_count}")
    print(f"Mirror mesh: {grid_mirror.number_of_elements} elements, P1={p1_mirror.global_dof_count}, DP0={dp0_mirror.global_dof_count}")

    # Throat elements
    throat_half = np.where(rt == 2)[0]
    throat_area_half = np.sum(grid_half.volumes[throat_half])
    throat_full_tags = mesh_full["surface_tags"]
    throat_full_elems = np.where(throat_full_tags == 2)[0]
    throat_area_full = np.sum(mesh_full["grid"].volumes[throat_full_elems])
    print(f"\nThroat elements: half={len(throat_half)}, full={len(throat_full_elems)}")
    print(f"Throat area: half={throat_area_half:.6e} m², full={throat_area_full:.6e} m²")
    print(f"Ratio: {throat_area_full/throat_area_half:.4f} (expect ~2.0)")

    # Source strength
    v_half = np.zeros(dp0_half.global_dof_count, dtype=np.complex128)
    v_half[throat_half] = 1.0
    neumann_half = bempp_api.GridFunction(dp0_half, coefficients=1j * rho * omega * v_half)

    v_mirror = np.zeros(dp0_mirror.global_dof_count, dtype=np.complex128)
    v_mirror_valid = throat_half[throat_half < dp0_mirror.global_dof_count]
    v_mirror[v_mirror_valid] = 1.0
    neumann_mirror = bempp_api.GridFunction(dp0_mirror, coefficients=1j * rho * omega * v_mirror)

    q_half = np.abs(np.sum(v_half * grid_half.volumes))
    q_mirror = np.abs(np.sum(v_mirror * grid_mirror.volumes))
    print(f"Volume velocity: half={q_half:.6e}, mirror={q_mirror:.6e}, total={q_half+q_mirror:.6e}")
    print(f"Mirror throat elements: {np.sum(v_mirror > 0)}/{dp0_mirror.global_dof_count}")

    # Operators
    op_kw = _operator_kwargs("numba", "double")

    # Direct operators
    dlp = bempp_api.operators.boundary.helmholtz.double_layer(p1_half, p1_half, p1_half, k, **op_kw)
    slp = bempp_api.operators.boundary.helmholtz.single_layer(dp0_half, p1_half, p1_half, k, **op_kw)
    hyp = bempp_api.operators.boundary.helmholtz.hypersingular(p1_half, p1_half, p1_half, k, **op_kw)
    adlp = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(dp0_half, p1_half, p1_half, k, **op_kw)
    ident_p1 = bempp_api.operators.boundary.sparse.identity(p1_half, p1_half, p1_half)
    ident_dp0 = bempp_api.operators.boundary.sparse.identity(dp0_half, p1_half, p1_half)
    coupling = 1j / k

    # Image operators
    dlp_img = bempp_api.operators.boundary.helmholtz.double_layer(p1_mirror, p1_half, p1_half, k, **op_kw)
    slp_img = bempp_api.operators.boundary.helmholtz.single_layer(dp0_mirror, p1_half, p1_half, k, **op_kw)
    hyp_img = bempp_api.operators.boundary.helmholtz.hypersingular(p1_mirror, p1_half, p1_half, k, **op_kw)
    adlp_img = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(dp0_mirror, p1_half, p1_half, k, **op_kw)

    # Assemble to dense
    A_direct = (0.5 * ident_p1 - dlp + coupling * hyp).weak_form().to_dense()
    A_image = (-dlp_img + coupling * hyp_img).weak_form().to_dense()

    print(f"\n--- LHS MATRICES ---")
    print(f"A_direct: shape={A_direct.shape}, norm={np.linalg.norm(A_direct):.6e}, "
          f"diag_mean={np.mean(np.abs(np.diag(A_direct))):.6e}")
    print(f"A_image:  shape={A_image.shape}, norm={np.linalg.norm(A_image):.6e}, "
          f"diag_mean={np.mean(np.abs(np.diag(A_image))):.6e}")
    print(f"Ratio (direct/image) norm: {np.linalg.norm(A_direct)/np.linalg.norm(A_image):.2f}")
    print(f"A_total = A_direct + A_image")
    A_total = A_direct + A_image
    print(f"A_total: norm={np.linalg.norm(A_total):.6e}")

    # RHS
    rhs_direct_gf = (-slp - coupling * (adlp + 0.5 * ident_dp0)) * neumann_half
    b_direct = np.asarray(rhs_direct_gf.projections(p1_half))
    rhs_image_gf = (-slp_img - coupling * adlp_img) * neumann_mirror
    b_image = np.asarray(rhs_image_gf.projections(p1_half))
    b_total = b_direct + b_image

    print(f"\n--- RHS VECTORS ---")
    print(f"b_direct: norm={np.linalg.norm(b_direct):.6e}")
    print(f"b_image:  norm={np.linalg.norm(b_image):.6e}")
    print(f"b_total:  norm={np.linalg.norm(b_total):.6e}")
    print(f"Ratio (direct/image): {np.linalg.norm(b_direct)/np.linalg.norm(b_image):.2f}")

    # Solve
    x_with_image, info1 = scipy_gmres(A_total, b_total, atol=1e-5, restart=100)
    x_without_image, info2 = scipy_gmres(A_direct, b_direct, atol=1e-5, restart=100)

    print(f"\n--- SOLUTIONS ---")
    print(f"x_with_image:    norm={np.linalg.norm(x_with_image):.6e}, max={np.max(np.abs(x_with_image)):.6e}")
    print(f"x_without_image: norm={np.linalg.norm(x_without_image):.6e}, max={np.max(np.abs(x_without_image)):.6e}")

    # Evaluate pressure at observation point
    obs_frame = infer_observation_frame(mesh_full["grid"], observation_origin="mouth")
    obs_dist = float(resolve_safe_observation_distance(mesh_full["grid"], 2.0, obs_frame)["effective_distance_m"])
    obs_frame_half = obs_frame.copy()
    obs_frame_half["origin_center"] = obs_frame["origin_center"].copy()
    obs_frame_half["origin_center"][0] = 0.0
    obs_xyz = (obs_frame["origin_center"] + obs_frame["axis"] * obs_dist).reshape(3, 1)
    obs_xyz_half = (obs_frame_half["origin_center"] + obs_frame_half["axis"] * obs_dist).reshape(3, 1)

    print(f"\nObs point (full):  {obs_xyz.flatten()}")
    print(f"Obs point (half):  {obs_xyz_half.flatten()}")

    pot_kw = _operator_kwargs("numba", "double")

    # Evaluate WITH image at obs_xyz_half
    p_gf = bempp_api.GridFunction(p1_half, coefficients=x_with_image)
    dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(p1_half, obs_xyz_half, k, **pot_kw)
    slp_pot = bempp_api.operators.potential.helmholtz.single_layer(dp0_half, obs_xyz_half, k, **pot_kw)
    p_direct = dlp_pot * p_gf - slp_pot * neumann_half

    dlp_pot_img = bempp_api.operators.potential.helmholtz.double_layer(p1_mirror, obs_xyz_half, k, **pot_kw)
    slp_pot_img = bempp_api.operators.potential.helmholtz.single_layer(dp0_mirror, obs_xyz_half, k, **pot_kw)
    p_mirror_gf = bempp_api.GridFunction(p1_mirror, coefficients=x_with_image)
    p_image = dlp_pot_img * p_mirror_gf - slp_pot_img * neumann_mirror

    p_total = p_direct + p_image
    spl_with_image = 20 * np.log10(np.abs(p_total[0, 0]) / 20e-6)
    spl_direct_only = 20 * np.log10(np.abs(p_direct[0, 0]) / 20e-6)
    spl_image_only = 20 * np.log10(np.abs(p_image[0, 0]) / 20e-6) if np.abs(p_image[0, 0]) > 0 else -np.inf

    print(f"\n--- PRESSURE AT OBSERVATION POINT ---")
    print(f"p_direct: {np.abs(p_direct[0,0]):.6e} ({spl_direct_only:.2f} dB)")
    print(f"p_image:  {np.abs(p_image[0,0]):.6e} ({spl_image_only:.2f} dB)")
    print(f"p_total:  {np.abs(p_total[0,0]):.6e} ({spl_with_image:.2f} dB)")
    print(f"Image/Direct pressure ratio: {np.abs(p_image[0,0])/np.abs(p_direct[0,0]):.4f}")
    print(f"Phase direct: {np.angle(p_direct[0,0]):.4f} rad")
    print(f"Phase image:  {np.angle(p_image[0,0]):.4f} rad")

    # Also evaluate WITHOUT image (half mesh only, no image operators)
    p_gf2 = bempp_api.GridFunction(p1_half, coefficients=x_without_image)
    p_no_image = dlp_pot * p_gf2 - slp_pot * neumann_half
    spl_no_image = 20 * np.log10(np.abs(p_no_image[0, 0]) / 20e-6)
    print(f"\np_no_image (half-only): {np.abs(p_no_image[0,0]):.6e} ({spl_no_image:.2f} dB)")

    # Full model for reference
    grid_full = mesh_full["grid"]
    p1_full = bempp_api.function_space(grid_full, "P", 1)
    dp0_full = bempp_api.function_space(grid_full, "DP", 0)
    dlp_f = bempp_api.operators.boundary.helmholtz.double_layer(p1_full, p1_full, p1_full, k, **op_kw)
    slp_f = bempp_api.operators.boundary.helmholtz.single_layer(dp0_full, p1_full, p1_full, k, **op_kw)
    hyp_f = bempp_api.operators.boundary.helmholtz.hypersingular(p1_full, p1_full, p1_full, k, **op_kw)
    adlp_f = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(dp0_full, p1_full, p1_full, k, **op_kw)
    id_p1_f = bempp_api.operators.boundary.sparse.identity(p1_full, p1_full, p1_full)
    id_dp0_f = bempp_api.operators.boundary.sparse.identity(dp0_full, p1_full, p1_full)

    v_full = np.zeros(dp0_full.global_dof_count, dtype=np.complex128)
    v_full[throat_full_elems] = 1.0
    neumann_full = bempp_api.GridFunction(dp0_full, coefficients=1j * rho * omega * v_full)

    A_full = (0.5 * id_p1_f - dlp_f + coupling * hyp_f).weak_form().to_dense()
    b_full = np.asarray((-slp_f - coupling * (adlp_f + 0.5 * id_dp0_f) * neumann_full).projections(p1_full))
    x_full, _ = scipy_gmres(A_full, b_full, atol=1e-5, restart=100)
    p_full_gf = bempp_api.GridFunction(p1_full, coefficients=x_full)
    dlp_pot_f = bempp_api.operators.potential.helmholtz.double_layer(p1_full, obs_xyz, k, **pot_kw)
    slp_pot_f = bempp_api.operators.potential.helmholtz.single_layer(dp0_full, obs_xyz, k, **pot_kw)
    p_full_obs = dlp_pot_f * p_full_gf - slp_pot_f * neumann_full
    spl_full = 20 * np.log10(np.abs(p_full_obs[0, 0]) / 20e-6)
    print(f"p_full:    {np.abs(p_full_obs[0,0]):.6e} ({spl_full:.2f} dB)")

    print(f"\n--- SUMMARY ---")
    print(f"Full model:                  {spl_full:.2f} dB")
    print(f"Half + image (with mirror):  {spl_with_image:.2f} dB  (diff: {abs(spl_full - spl_with_image):.3f} dB)")
    print(f"Half only (no image):        {spl_no_image:.2f} dB  (diff: {abs(spl_full - spl_no_image):.3f} dB)")
    print(f"Direct potential only:       {spl_direct_only:.2f} dB")
    print(f"Image potential only:        {spl_image_only:.2f} dB")


if __name__ == "__main__":
    main()
