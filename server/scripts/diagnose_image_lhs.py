#!/usr/bin/env python3
"""
Diagnostic: verify that the cross-grid image LHS matrix is correct.

Strategy: build the FULL mesh's BEM system, extract the A_12 block (interaction
between X>=0 test DOFs and X<0 trial DOFs), and compare with the cross-grid
A_image assembled on the half+mirror grids.

If A_image ≈ A_12, the cross-grid assembly is correct.
If A_image << A_12, the missing singular quadrature is causing the error.
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
    from solver.solve_optimized import _operator_kwargs
    from solver.symmetry import evaluate_symmetry_policy, create_mirror_grid, SymmetryPlane
    from solver.deps import bempp_api
    from contracts import WaveguideParamsRequest
    from scipy.sparse.linalg import gmres as scipy_gmres

    freq = 500.0
    c, rho = 343.0, 1.21
    omega = 2 * np.pi * freq
    k = omega / c
    coupling = 1j / k

    params = WaveguideParamsRequest(
        formula_type="R-OSSE", R="60", r="0.4", b="0.2", m="0.85", tmax="1.0",
        r0="12.7", a0="15.5", k="2.0", q="3.4", throat_profile=1,
        throat_ext_angle=0.0, throat_ext_length=0.0, slot_length=0.0, rot=0.0,
        gcurve_type=0, morph_target=0, n_angular=60, n_length=15,
        quadrants=1234, throat_res=8.0, mouth_res=20.0, rear_res=40.0,
        wall_thickness=6.0, enc_depth=0.0, source_shape=2, source_radius=-1.0,
    ).model_dump()
    params["quadrants"] = 1234

    # Build FULL mesh
    occ = build_waveguide_mesh(params, include_canonical=True, symmetry_cut=None)
    cf = occ["canonical_mesh"]
    mesh_full = prepare_mesh(cf["vertices"], cf["indices"], surface_tags=cf["surfaceTags"],
                             mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001,
                                            "meshStrategy": "occ_adaptive",
                                            "requestedQuadrants": 1234, "effectiveQuadrants": 1234},
                             use_gmsh=False)

    grid_full = mesh_full["grid"]
    p1_full = bempp_api.function_space(grid_full, "P", 1)
    dp0_full = bempp_api.function_space(grid_full, "DP", 0)
    verts_full = grid_full.vertices  # (3, N)
    n_p1 = p1_full.global_dof_count

    op_kw = _operator_kwargs("numba", "double")

    # Assemble FULL operators
    print("Assembling FULL operators...")
    dlp_f = bempp_api.operators.boundary.helmholtz.double_layer(p1_full, p1_full, p1_full, k, **op_kw)
    hyp_f = bempp_api.operators.boundary.helmholtz.hypersingular(p1_full, p1_full, p1_full, k, **op_kw)
    id_f = bempp_api.operators.boundary.sparse.identity(p1_full, p1_full, p1_full)

    A_full = (0.5 * id_f - dlp_f + coupling * hyp_f).weak_form().to_dense()
    print(f"A_full: shape={A_full.shape}, norm={np.linalg.norm(A_full):.6e}")

    # Partition P1 DOFs by X coordinate
    p1_x = verts_full[0, :n_p1]  # P1 DOFs map to vertices 0..n_p1-1
    tol = -1e-6
    dofs_right = np.where(p1_x >= tol)[0]  # X >= 0
    dofs_left = np.where(p1_x < tol)[0]    # X < 0
    print(f"\nFull mesh P1 DOFs: {n_p1} (right={len(dofs_right)}, left={len(dofs_left)})")

    # Extract blocks
    A_11 = A_full[np.ix_(dofs_right, dofs_right)]  # test=right, trial=right
    A_12 = A_full[np.ix_(dofs_right, dofs_left)]   # test=right, trial=left

    print(f"A_11 (right-right): shape={A_11.shape}, norm={np.linalg.norm(A_11):.6e}")
    print(f"A_12 (right-left):  shape={A_12.shape}, norm={np.linalg.norm(A_12):.6e}")
    print(f"Ratio A_11/A_12: {np.linalg.norm(A_11)/np.linalg.norm(A_12):.2f}")

    # Now build the HALF mesh + MIRROR and assemble cross-grid A_image
    print("\nBuilding half mesh + mirror...")
    occ_half = build_waveguide_mesh(params, include_canonical=True, symmetry_cut="yz")
    ch = occ_half["canonical_mesh"]
    mesh_half = prepare_mesh(ch["vertices"], ch["indices"], surface_tags=ch["surfaceTags"],
                             mesh_metadata={"units": "mm", "unitScaleToMeter": 0.001,
                                            "meshStrategy": "occ_adaptive",
                                            "requestedQuadrants": 12, "effectiveQuadrants": 12},
                             use_gmsh=False)

    sym = evaluate_symmetry_policy(
        vertices=mesh_half["original_vertices"], indices=mesh_half["original_indices"],
        surface_tags=mesh_half["original_surface_tags"], throat_elements=mesh_half["throat_elements"],
        enable_symmetry=True, tolerance=1e-3, quadrants=12)
    rv, ri, rt = sym["reduced_vertices"], sym["reduced_indices"], sym["reduced_surface_tags"]
    grid_half = bempp_api.Grid(rv, ri, rt)
    p1_half = bempp_api.function_space(grid_half, "P", 1)

    mg = create_mirror_grid(rv, ri, [SymmetryPlane.YZ])
    mv, mi = mg[0]
    grid_mirror = bempp_api.Grid(mv, mi)
    p1_mirror = bempp_api.function_space(grid_mirror, "P", 1)

    # Assemble cross-grid image operators
    print("Assembling cross-grid image operators...")
    dlp_img = bempp_api.operators.boundary.helmholtz.double_layer(p1_mirror, p1_half, p1_half, k, **op_kw)
    hyp_img = bempp_api.operators.boundary.helmholtz.hypersingular(p1_mirror, p1_half, p1_half, k, **op_kw)
    A_image_cross = (-dlp_img + coupling * hyp_img).weak_form().to_dense()

    print(f"A_image (cross-grid): shape={A_image_cross.shape}, norm={np.linalg.norm(A_image_cross):.6e}")

    # Also assemble direct operators on the half mesh for reference
    dlp_h = bempp_api.operators.boundary.helmholtz.double_layer(p1_half, p1_half, p1_half, k, **op_kw)
    hyp_h = bempp_api.operators.boundary.helmholtz.hypersingular(p1_half, p1_half, p1_half, k, **op_kw)
    id_h = bempp_api.operators.boundary.sparse.identity(p1_half, p1_half, p1_half)
    A_direct_half = (0.5 * id_h - dlp_h + coupling * hyp_h).weak_form().to_dense()
    print(f"A_direct (half):     shape={A_direct_half.shape}, norm={np.linalg.norm(A_direct_half):.6e}")

    # Compare
    print(f"\n--- COMPARISON ---")
    print(f"Full mesh A_12 norm (reference): {np.linalg.norm(A_12):.6e}")
    print(f"Cross-grid A_image norm:         {np.linalg.norm(A_image_cross):.6e}")
    print(f"Full mesh A_11 norm:             {np.linalg.norm(A_11):.6e}")
    print(f"Half mesh A_direct norm:         {np.linalg.norm(A_direct_half):.6e}")

    # The cross-grid A_image should be similar to A_12 from the full mesh
    # Note: different mesh sizes (180x172 vs 155x155), so we compare norms per-element
    a12_per = np.linalg.norm(A_12) / np.sqrt(A_12.shape[0] * A_12.shape[1])
    img_per = np.linalg.norm(A_image_cross) / np.sqrt(A_image_cross.shape[0] * A_image_cross.shape[1])
    print(f"\nPer-element norms:")
    print(f"  A_12 per element: {a12_per:.6e}")
    print(f"  A_image per elem: {img_per:.6e}")
    print(f"  Ratio: {a12_per / img_per:.2f}" if img_per > 0 else "  A_image is zero!")

    # Ratio of off-diagonal to diagonal blocks on the full mesh
    a11_per = np.linalg.norm(A_11) / np.sqrt(A_11.shape[0] * A_11.shape[1])
    print(f"  A_11 per element: {a11_per:.6e}")
    print(f"  Full mesh A_11/A_12 ratio (per-element): {a11_per / a12_per:.2f}")

    # Check if the issue is that A_12 is actually significant
    print(f"\n--- WHAT SHOULD THE SYMMETRY CORRECTION BE? ---")
    print(f"If A_12 has norm {np.linalg.norm(A_12):.4e} compared to A_11 norm {np.linalg.norm(A_11):.4e},")
    ratio = np.linalg.norm(A_12) / np.linalg.norm(A_11)
    print(f"then the image correction is {ratio*100:.1f}% of the self-interaction.")
    print(f"Cross-grid A_image is {np.linalg.norm(A_image_cross)/np.linalg.norm(A_direct_half)*100:.1f}% of A_direct_half.")
    print(f"If these match, the cross-grid assembly is correct.")
    print(f"If A_image << A_12, the singular quadrature gap is the cause.")


if __name__ == "__main__":
    main()
