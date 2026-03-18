# BEM Symmetry Optimization — Investigation Record

Date range: March 12–17, 2026
Status: **Closed — blocked by bempp-cl 0.4.x limitation**
Preserved for future revisit.

---

## Motivation

Many horn geometries are symmetric about one or two axes (half or quarter symmetry). Solving only the reduced domain and using the image source method to account for the mirrored contribution would cut the number of BEM degrees of freedom by 2–4×, with superlinear gains in assembly and solve time.

Measured performance potential on a representative R-OSSE horn:

| Metric | Full model | Half model (if working) |
|--------|-----------|------------------------|
| DOFs (P1) | ~700 | ~350 |
| DOF reduction | 1× | 2.96× |
| Solve time | baseline | ~2.66× faster |
| DOF reduction at 4000 elems | — | ~4× |

A manual test with the half mesh and standard operators (no image correction) showed 2.74× speedup (DOF 352→119, 12.2 s → 4.5 s), confirming the BEM math benefit is real.

---

## Approach 0 — Vertex-Based Symmetry Detection (Abandoned Early)

**Idea:** Detect geometric symmetry by O(N²) vertex matching: for each vertex on the +X side, look for its mirror in the −X set within a tolerance.

**Why it failed:** OCC free-meshing (Gmsh) does not produce mirror-symmetric vertex positions, even for parametrically symmetric geometry. The mesher places vertices independently on each patch. Vertex matching therefore always returns `FULL`, never triggering symmetry reduction.

This approach was benchmarked in commit `e660b43` and then replaced with parameter-driven detection in commit `b151eb6`. The `quadrants` parameter (derived from the geometry definition) is now the authoritative symmetry signal, bypassing vertex heuristics entirely.

---

## Approach 1 — Post-Tessellation Clipping (Abandoned)

**Idea:** Build a full OCC mesh, then clip it at the symmetry plane in post-processing: discard all triangles with vertices entirely on the −X side.

**Why it failed:** OCC free-meshing produces asymmetric vertex positions at the symmetry boundary. Vertices that should lie exactly on the X=0 plane are placed with ±1e-3 mm numerical noise. After clipping, the cut edge is ragged — some triangles are missing, and adjacent triangles share no vertices across the cut. This creates open boundaries and incorrect surface normals.

**Measured error:** ~14.8 dB BEM error from clipping artifacts.

The clipping code was removed in commit `a31c2a9`. The tessellation-last principle (documented in `docs/backlog.md`) was established: geometry cuts must happen at the B-Rep level before meshing, never as post-processing on the tessellated mesh.

---

## Approach 2 — B-Rep Symmetry Cut + Cross-Grid Image Operators (Main Investigation)

This was the primary investigation, spanning commits `eb2a4bf` through `e7b8814`.

### How it works (mathematically)

For a domain Ω symmetric about the YZ plane, the BEM boundary integral equation can be split:

```
A · u = A_self · u + A_image · u = f
```

where `A_self` is the standard operator on the half mesh and `A_image` is the contribution from the mirror half-space (the "image source"). `A_image` involves integration over the mirrored geometry with sign adjustments to enforce the Neumann boundary condition on the symmetry plane.

### Implementation

1. **B-Rep symmetry cut**: Added `_apply_symmetry_cut_yz()` to `waveguide_builder.py`. Uses OCC Boolean operations to cut the solid at X=0 before meshing. This produces a clean half mesh where all vertices have X ≥ 0. Verified by `diagnose_image_source.py`: the cut is correct (all X ≥ 0, ~2× element reduction).

2. **Mirror grid construction**: `create_mirror_grid()` in `solver/symmetry.py` flips the X coordinates and reverses triangle winding (rows 1↔2 of the index array) to maintain correct outward normals on the mirrored surface.

3. **Cross-grid image operators**: Assemble DLP and HYP operators with the mirror grid as domain space and the half mesh as test/range space:
   ```python
   dlp_img = bempp.operators.boundary.helmholtz.double_layer(p1_mirror, p1_half, p1_half, k)
   hyp_img = bempp.operators.boundary.helmholtz.hypersingular(p1_mirror, p1_half, p1_half, k)
   A_image = -dlp_img + coupling * hyp_img
   ```

### Root Cause of Failure

**bempp-cl 0.4.x only applies Duffy-transform singular quadrature when the domain and test function spaces share the same `Grid` object.**

The singular quadrature correction handles near-singular integrals between elements that share vertices or edges (coincident and adjacent elements). The `singular_assembler.py` in bempp-cl identifies these element pairs by comparing vertex indices, not vertex positions. When domain and test spaces are on different `Grid` objects — even if those grids are physically touching at a shared boundary — no elements are recognized as singular pairs, and the singular part returns a zero matrix.

**Diagnostic evidence** (from `diagnose_image_lhs.py`):

| Matrix block | Shape | Norm | Per-element norm |
|---|---|---|---|
| Full mesh A_12 (reference) | 180×172 | `X` | `a` |
| Cross-grid A_image | 155×155 | `0.44X` | `0.44a` |

The cross-grid image operator is 2.27× smaller than the reference A_12 block extracted from the full mesh. This is not numerical noise — it is a systematic underestimation caused by the missing singular quadrature at the symmetry plane boundary.

**Observable symptom:** ~8 dB SPL error at low frequencies (where near-field contributions from the symmetry plane are most significant).

---

## Approach 3 — Merged Grid (Also Investigated)

**Idea:** Rather than keeping the half mesh and mirror as separate `Grid` objects, merge their vertex and index arrays into a single combined `Grid` before handing to bempp. Then assemble standard (same-grid) operators. With a single grid, the singular quadrature should fire for elements at the seam.

**Why it also fails:** The merged grid has duplicate vertices at the symmetry plane — one copy from the half mesh side, one from the mirror. These physically identical positions have different vertex indices. bempp's singular assembler still does not recognize them as shared, because it checks index equality, not position equality. The situation is structurally identical to the cross-grid case.

Diagnostic scripts: `diagnose_merged_grid.py`, `diagnose_merged_solve.py`.

**Conclusion:** The merged-grid approach is not a workaround; it reproduces the same limitation.

---

## Summary of What Was Confirmed Working

| Component | Status | Evidence |
|---|---|---|
| B-Rep symmetry cut at X=0 | ✓ Correct | `diagnose_image_source.py` Step 1 |
| Mirror grid construction (flip X, reverse winding) | ✓ Correct | `diagnose_image_source.py` Step 2 |
| Parameter-driven symmetry detection via `quadrants` | ✓ Correct | `symmetry_benchmark.py` all cases pass |
| Geometry-first build (OCC cuts before meshing) | ✓ Correct | Mesh topology verified |
| Cross-grid image operator assembly | ✗ Defective singular quadrature | `diagnose_image_lhs.py` |
| Merged-grid approach | ✗ Same root cause | `diagnose_merged_grid.py` |

---

## Current State

Symmetry solving is **disabled**. `simulation_runner.py` forces `quadrants=1234` (full model) before passing to the BEM solver. The `enable_symmetry` parameter and all image operator code paths in `solve_optimized.py` are preserved but never reached.

The symmetry detection code (`solver/symmetry.py`) still runs correctly — it is used for reporting in job metadata, and the geometry-first path correctly identifies when a mesh was built as a half/quarter model. Only the BEM image source solve path is blocked.

---

## Future Options

### Option A — Half-Space Green's Function

Replace the free-space Green's function `G(x,y) = e^{ikr}/(4πr)` with the half-space Green's function that bakes the Neumann boundary condition into the kernel analytically:

```
G_hs(x,y) = G(x,y) + G(x,y*)
```

where `y*` is the mirror image of `y`. This eliminates the image source operator entirely — the standard BEM assembly handles symmetry implicitly, and singular quadrature operates as normal because there is only one grid.

**Difficulty:** bempp-cl 0.4.x does not expose a custom kernel interface. This would require either a fork of bempp-cl or a different BEM library.

### Option B — Wait for bempp-cl Upstream Fix

File an issue or track whether a future bempp-cl version adds cross-grid singular quadrature support (near-field corrections between elements on different grids that are geometrically adjacent). This would make Approach 2 work as designed without any changes on our side.

**Re-enable condition:** bempp-cl adds cross-grid singular quadrature, verified by `diagnose_image_lhs.py` showing A_image ≈ A_12 within 5%.

### Option C — Manual Duffy Corrections

Manually identify element pairs that are near-singular across the grid boundary (elements from the half mesh and elements from the mirror grid that share a physical edge at X=0), compute the missing singular contributions explicitly using Duffy parametric integration, and add them to the assembled matrix.

**Difficulty:** High. This requires low-level access to bempp-cl's quadrature machinery or reimplementing the Duffy transform from scratch.

### Option D — Alternative BEM Library

Evaluate other open-source BEM libraries (e.g., PyGBe, BEM++3, ExaFMM-t) that support half-space Green's functions or cross-grid near-field corrections natively.

---

## Relevant Commits

| Commit | Description |
|---|---|
| `e660b43` | Benchmark confirms vertex matching never triggers on OCC meshes |
| `b151eb6` | Replace O(N²) vertex heuristic with parameter-driven policy |
| `eb2a4bf` | Implement image source operators and B-Rep symmetry cut (WIP) |
| `c3c89fc` | Fix OCC sync before fragmenting surfaces in B-Rep cut |
| `8485a6a` | Fix B-Rep cut crash and add image source safety guard |
| `a31c2a9` | Remove post-tessellation clipping code (14.8 dB error measured) |
| `e7b8814` | Close investigation — root cause documented, safety gate re-enabled |

## Relevant Diagnostic Scripts

All under `server/scripts/`:

- `diagnose_image_source.py` — Four-step diagnostic: B-Rep cut, mirror grid, mesh validity, BEM solve comparison
- `diagnose_image_lhs.py` — Extract A_12 block from full mesh and compare to cross-grid A_image norm
- `diagnose_image_detail.py` — Detailed element-level analysis of cross-grid singular contributions
- `diagnose_merged_grid.py` — Test merged-grid approach (also blocked)
- `diagnose_merged_solve.py` — End-to-end solve with merged grid
- `diagnose_ath_symmetry.py` — ATH reference config symmetry verification
- `benchmark_bem_symmetry.py` — Timing profiler for full vs symmetry-reduced solve
- `ab_test_symmetry.py` — A/B comparison script used to first detect the 8 dB error
