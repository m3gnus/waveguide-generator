#!/usr/bin/env python3
"""
Diagnostic: Compare three BEM solve approaches at a single frequency.

1. FULL: Independent full mesh (700 elements), standard BEM
2. HALF+IMAGE: Half mesh (357 elements), cross-grid image operators
3. MERGED: Half mesh + mirror merged into one grid (714 elements), standard BEM

If MERGED ≈ FULL and HALF+IMAGE ≠ MERGED, the cross-grid image implementation
has a bug.  If MERGED ≈ HALF+IMAGE ≠ FULL, the mesh discretization difference
explains the error.
"""
from __future__ import annotations
import os, sys, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("BEMPP_DEFAULT_DEVICE_INTERFACE", "numba")

import numpy as np

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

def build_mesh(symmetry_cut=None):
    from solver.waveguide_builder import build_waveguide_mesh
    from solver.mesh import prepare_mesh
    from contracts import WaveguideParamsRequest
    params = WaveguideParamsRequest(
        formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
        r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
        throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
        gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
        quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
        wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
    ).model_dump()
    params["quadrants"] = 1234
    eq = 12 if symmetry_cut == "yz" else 1234
    occ = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=symmetry_cut)
    c = occ["canonical_mesh"]
    mm = {"units": "mm", "unitScaleToMeter": 0.001, "meshStrategy": "occ_adaptive",
          "requestedQuadrants": eq, "effectiveQuadrants": eq}
    return prepare_mesh(c["vertices"], c["indices"], surface_tags=c["surfaceTags"],
                        mesh_metadata=mm, use_gmsh=False)


def solve_standard(label, mesh, freq, obs_frame=None):
    """Standard BEM solve (no image operators)."""
    from solver.solve_optimized import HornBEMSolver
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance

    grid = mesh["grid"]
    tags = mesh["surface_tags"]
    solver = HornBEMSolver(grid=grid, physical_tags=tags, sound_speed=343.0, rho=1.21,
                           tag_throat=2, boundary_interface="numba", potential_interface="numba",
                           bem_precision="double", use_burton_miller=True)
    if obs_frame is None:
        obs_frame = infer_observation_frame(grid, observation_origin="mouth")
    obs_dist = float(resolve_safe_observation_distance(grid, 2.0, obs_frame)["effective_distance_m"])
    spl, imp, di, sol, iters = solver._solve_single_frequency(
        freq, observation_frame=obs_frame, observation_distance_m=obs_dist)
    print(f"  {label}: SPL={spl:.2f} dB, DOF P1={solver.p1_space.global_dof_count}, "
          f"DP0={solver.dp0_space.global_dof_count}")
    return spl, obs_frame


def solve_image(label, mesh, freq, ref_obs_frame=None):
    """Half-model BEM solve with cross-grid image operators."""
    from solver.solve_optimized import HornBEMSolver
    from solver.symmetry import evaluate_symmetry_policy, create_mirror_grid, SymmetryPlane
    from solver.deps import bempp_api
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance

    grid = mesh["grid"]
    ov = mesh["original_vertices"]
    oi = mesh["original_indices"]
    ot = mesh["original_surface_tags"]
    te = mesh["throat_elements"]
    mm = mesh.get("mesh_metadata", {})
    qv = mm.get("requestedQuadrants", 1234)

    sym = evaluate_symmetry_policy(vertices=ov, indices=oi, surface_tags=ot,
                                   throat_elements=te, enable_symmetry=True,
                                   tolerance=1e-3, quadrants=qv)
    policy = sym["policy"]
    applied = policy.get("applied", False)
    assert applied, "Symmetry should be applied for half mesh"

    rv, ri, rt = sym["reduced_vertices"], sym["reduced_indices"], sym["reduced_surface_tags"]
    grid = bempp_api.Grid(rv, ri, rt)
    tags = rt
    te2 = np.where(rt == 2)[0]

    solver = HornBEMSolver(grid=grid, physical_tags=tags, sound_speed=343.0, rho=1.21,
                           tag_throat=2, boundary_interface="numba", potential_interface="numba",
                           bem_precision="double", use_burton_miller=True)

    si = sym.get("symmetry_info") or sym.get("symmetry", {})
    sp = si.get("symmetry_planes") or []
    pm = {p.value: p for p in SymmetryPlane}
    planes = [pm[s] for s in sp if s in pm]
    mg = create_mirror_grid(rv, ri, planes)
    solver._assemble_image_operators(mg, planes, si)

    obs_frame = infer_observation_frame(grid, observation_origin="mouth", symmetry_plane="yz")
    if ref_obs_frame is not None:
        obs_frame = ref_obs_frame.copy()
        obs_frame["origin_center"] = obs_frame["origin_center"].copy()
        obs_frame["origin_center"][0] = 0.0
    obs_dist = float(resolve_safe_observation_distance(grid, 2.0, obs_frame)["effective_distance_m"])

    spl, imp, di, sol, iters = solver._solve_single_frequency(
        freq, observation_frame=obs_frame, observation_distance_m=obs_dist)
    print(f"  {label}: SPL={spl:.2f} dB, DOF P1={solver.p1_space.global_dof_count}, "
          f"DP0={solver.dp0_space.global_dof_count}")
    return spl


def solve_block_toeplitz(label, mesh_full, freq, obs_frame=None):
    """Approach B: Assemble on full mesh, partition by symmetry, solve reduced system.

    1. Build standard operators on the full mesh (correct singular quadrature)
    2. Convert to dense matrices
    3. Identify which P1 DOFs are at X>=0 and X<0
    4. Partition: A = [[A11, A12], [A21, A22]]
    5. For Neumann symmetry: solve (A11 + A12) * p_half = b_half
    6. Evaluate pressure using the full solution (p_half replicated on both halves)
    """
    from solver.deps import bempp_api
    from solver.observation import infer_observation_frame, resolve_safe_observation_distance
    from scipy.sparse.linalg import gmres as scipy_gmres

    grid = mesh_full["grid"]
    tags = mesh_full["surface_tags"]
    c, rho = 343.0, 1.21
    omega = 2.0 * np.pi * freq
    k = omega / c

    p1 = bempp_api.function_space(grid, "P", 1)
    dp0 = bempp_api.function_space(grid, "DP", 0)

    # Identify DOF partition by X coordinate
    # P1 DOFs correspond to vertices; get vertex positions for each DOF
    n_p1 = p1.global_dof_count
    n_dp0 = dp0.global_dof_count
    verts = grid.vertices  # (3, N_verts)

    # P1: DOF i corresponds to vertex i (for P1 continuous)
    # Get X coordinate for each P1 DOF
    p1_x = np.zeros(n_p1)
    for dof in range(n_p1):
        p1_x[dof] = verts[0, dof]

    # Partition P1 DOFs: side A (X >= 0), side B (X < 0)
    # Elements at X=0 go to side A
    tol = -1e-6
    dofs_a = np.where(p1_x >= tol)[0]
    dofs_b = np.where(p1_x < tol)[0]
    print(f"  P1 DOFs: {n_p1} total, {len(dofs_a)} at X>=0, {len(dofs_b)} at X<0")

    # Similarly partition DP0 DOFs by element centroid X
    dp0_x = np.zeros(n_dp0)
    for elem in range(n_dp0):
        elem_verts = grid.elements[:, elem]
        dp0_x[elem] = np.mean(verts[0, elem_verts])
    dp0_a = np.where(dp0_x >= tol)[0]
    dp0_b = np.where(dp0_x < tol)[0]
    print(f"  DP0 DOFs: {n_dp0} total, {len(dp0_a)} at X>=0, {len(dp0_b)} at X<0")

    # Assemble operators
    op_kw = {"device_interface": "numba", "precision": "double"}
    ident_p1 = bempp_api.operators.boundary.sparse.identity(p1, p1, p1)
    ident_dp0 = bempp_api.operators.boundary.sparse.identity(dp0, p1, p1)
    dlp = bempp_api.operators.boundary.helmholtz.double_layer(p1, p1, p1, k, **op_kw)
    slp = bempp_api.operators.boundary.helmholtz.single_layer(dp0, p1, p1, k, **op_kw)
    hyp = bempp_api.operators.boundary.helmholtz.hypersingular(p1, p1, p1, k, **op_kw)
    adlp = bempp_api.operators.boundary.helmholtz.adjoint_double_layer(dp0, p1, p1, k, **op_kw)
    coupling = 1j / k

    # Full LHS and RHS as dense matrices
    A_full = (0.5 * ident_p1 - dlp + coupling * hyp).weak_form().to_dense()

    # RHS: need velocity on throat
    driver_dofs = np.where(tags == 2)[0]
    v_coeffs = np.zeros(n_dp0, dtype=np.complex128)
    v_coeffs[driver_dofs] = 1.0
    velocity_fun = bempp_api.GridFunction(dp0, coefficients=v_coeffs)
    neumann_fun = 1j * rho * omega * velocity_fun
    rhs_full_gf = (-slp - coupling * (adlp + 0.5 * ident_dp0)) * neumann_fun
    b_full = np.asarray(rhs_full_gf.projections(p1))

    # --- FULL SOLVE (standard, for comparison) ---
    x_full, info = scipy_gmres(A_full, b_full, atol=1e-5, restart=100)
    p_full_gf = bempp_api.GridFunction(p1, coefficients=x_full)

    if obs_frame is None:
        obs_frame = infer_observation_frame(grid, observation_origin="mouth")
    obs_dist = float(resolve_safe_observation_distance(grid, 2.0, obs_frame)["effective_distance_m"])
    origin = obs_frame["origin_center"]
    obs_xyz = (origin + obs_frame["axis"] * obs_dist).reshape(3, 1)

    pot_kw = {"device_interface": "numba", "precision": "double"}
    dlp_pot = bempp_api.operators.potential.helmholtz.double_layer(p1, obs_xyz, k, **pot_kw)
    slp_pot = bempp_api.operators.potential.helmholtz.single_layer(dp0, obs_xyz, k, **pot_kw)
    p_full_obs = dlp_pot * p_full_gf - slp_pot * neumann_fun
    spl_full_dense = 20 * np.log10(np.abs(p_full_obs[0, 0]) / 20e-6)
    print(f"  {label} FULL (dense): SPL={spl_full_dense:.2f} dB")

    # --- BLOCK-TOEPLITZ SOLVE ---
    # Extract blocks: A11 = A_full[dofs_a][:, dofs_a], A12 = A_full[dofs_a][:, dofs_b]
    A11 = A_full[np.ix_(dofs_a, dofs_a)]
    A12 = A_full[np.ix_(dofs_a, dofs_b)]
    b_a = b_full[dofs_a]
    b_b = b_full[dofs_b]

    # For Neumann symmetry (p_a = p_b): (A11 + A12) * p_a = b_a
    # And b_a should include both halves of the source
    A_sym = A11 + A12
    b_sym = b_a + b_b  # source on both halves

    # Wait — for symmetric excitation b_a = b_b (source is symmetric).
    # The full system: A11*p_a + A12*p_b = b_a, A21*p_a + A22*p_b = b_b
    # With p_a=p_b and A11=A22, A12=A21: (A11+A12)*p_a = b_a
    # But b_a includes only the source on the A half. We need to check.

    # Actually for symmetric excitation with b_a = b_b:
    # Row 1: A11*p + A12*p = b_a → (A11+A12)*p = b_a
    # This already accounts for the full source via the matrix symmetry.
    b_sym = b_a  # Just row A of the RHS

    x_sym, info2 = scipy_gmres(A_sym, b_sym, atol=1e-5, restart=100)

    # Reconstruct full solution: p_a = p_b = x_sym
    x_recon = np.zeros(n_p1, dtype=np.complex128)
    x_recon[dofs_a] = x_sym
    x_recon[dofs_b] = x_sym  # Wait — this requires dofs_a and dofs_b have the same size

    # Actually: dofs_b might have different size than dofs_a (asymmetric vertex distribution
    # due to vertices at X=0 being counted on side A only).
    # For reconstruction, we need to map each mirror DOF to its symmetric partner.
    # For now, just use the full solution to evaluate pressure.
    # The pressure at the observation point should come from the symmetric solution.

    # Simple: evaluate using (A11+A12)*x_sym = b_a means x_sym = pressure on side A.
    # For pressure evaluation at observation point, we need the full surface integral.
    # p(obs) = sum over ALL elements of [DLP*p - SLP*g]
    # = sum_A [DLP*p_a - SLP*g_a] + sum_B [DLP*p_b - SLP*g_b]
    # For symmetric p and g: = 2 * sum_A [DLP*p_a - SLP*g_a]
    # But this double-counts if the obs point has specific symmetry...

    # Actually, if obs is on X=0 (symmetry plane), then by symmetry the two halves
    # contribute equally. So p(obs) = 2 * p_half(obs).
    # But for a general obs point, we need the full evaluation.

    # The cleanest way: reconstruct the full coefficient vector and evaluate normally.
    # Need a DOF-to-DOF mapping between side A and side B.
    # For a symmetric mesh, DOF i at (x,y,z) on side A maps to DOF j at (-x,y,z) on side B.

    # Build the mapping
    a_to_b = np.full(len(dofs_a), -1, dtype=int)
    for i, da in enumerate(dofs_a):
        xa, ya, za = verts[0, da], verts[1, da], verts[2, da]
        # Find matching vertex at (-xa, ya, za) in dofs_b
        for j, db in enumerate(dofs_b):
            xb, yb, zb = verts[0, db], verts[1, db], verts[2, db]
            if abs(xa + xb) < 1e-4 and abs(ya - yb) < 1e-4 and abs(za - zb) < 1e-4:
                a_to_b[i] = db
                break

    matched = np.sum(a_to_b >= 0)
    print(f"  DOF mapping A→B: {matched}/{len(dofs_a)} matched")

    # Vertices at X=0 (in dofs_a) have no partner in dofs_b
    # For those, the solution is just x_sym[i]
    x_recon = np.zeros(n_p1, dtype=np.complex128)
    for i, da in enumerate(dofs_a):
        x_recon[da] = x_sym[i]
        if a_to_b[i] >= 0:
            x_recon[a_to_b[i]] = x_sym[i]  # Neumann symmetry: p_b = p_a

    p_sym_gf = bempp_api.GridFunction(p1, coefficients=x_recon)
    p_sym_obs = dlp_pot * p_sym_gf - slp_pot * neumann_fun
    spl_sym = 20 * np.log10(np.abs(p_sym_obs[0, 0]) / 20e-6)
    print(f"  {label} BLOCK-TOEPLITZ: SPL={spl_sym:.2f} dB")

    return spl_full_dense, spl_sym, obs_frame


def main():
    freq = 500.0  # worst-case frequency
    print(f"Diagnostic: comparing 3 approaches at {freq:.0f} Hz\n")

    print("Building meshes...")
    mesh_full = build_mesh(symmetry_cut=None)
    mesh_half = build_mesh(symmetry_cut="yz")
    print(f"  Full: {mesh_full['original_vertices'].shape[1]} verts, "
          f"{mesh_full['original_indices'].shape[1]} tris")
    print(f"  Half: {mesh_half['original_vertices'].shape[1]} verts, "
          f"{mesh_half['original_indices'].shape[1]} tris")

    print()

    # Warmup
    print("Warming up numba JIT...")
    solve_standard("WARMUP", mesh_full, freq)
    print()

    print("="*60)
    print(f"SOLVING AT {freq:.0f} Hz")
    print("="*60)

    # 1. Full model (standard bempp GMRES)
    spl_full, obs_full = solve_standard("FULL (standard GMRES)", mesh_full, freq)

    # 2. Half + image (cross-grid)
    spl_image = solve_image("HALF+IMAGE (cross-grid)", mesh_half, freq,
                            ref_obs_frame=obs_full)

    # 3. Block-Toeplitz (assemble on full mesh, solve symmetric system)
    spl_full_dense, spl_bt, _ = solve_block_toeplitz("BLOCK-TOEPLITZ", mesh_full, freq,
                                                      obs_frame=obs_full)

    print()
    print("="*60)
    print("COMPARISON")
    print("="*60)
    print(f"  FULL (GMRES):        {spl_full:.2f} dB")
    print(f"  FULL (dense):        {spl_full_dense:.2f} dB")
    print(f"  BLOCK-TOEPLITZ:      {spl_bt:.2f} dB  (diff from FULL: {abs(spl_full-spl_bt):.3f} dB)")
    print(f"  HALF+IMAGE:          {spl_image:.2f} dB  (diff from FULL: {abs(spl_full-spl_image):.3f} dB)")
    print()
    if abs(spl_bt - spl_full) < 0.5:
        print("  → Block-Toeplitz matches full model (correct symmetry reduction)")
        if abs(spl_image - spl_full) > 2.0:
            print("  → Cross-grid image approach has a bug — should use block-Toeplitz instead")
    else:
        print("  → Block-Toeplitz does NOT match full model — investigate DOF mapping")


if __name__ == "__main__":
    main()
