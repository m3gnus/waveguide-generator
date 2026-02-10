# MSH / GEO Generation: Python OCC Builder

This document describes how the Python OCC mesh builder constructs `.geo` and `.msh` files for
R-OSSE and OSSE waveguide types.

## Overview

Key file: `server/solver/waveguide_builder.py`

Entry point: `build_waveguide_mesh(params: dict) -> dict`

The builder uses the Gmsh Python OCC API to construct smooth BSpline surfaces, which Gmsh then
meshes with its 2D/3D meshing algorithms. The output is a tagged mesh in Gmsh `.msh` format v2.2.

## 1. Surface Construction Algorithm

### 1.1 Point Grid

`_compute_point_grids(params)` evaluates the ATH formulas at a grid of:
- `n_phi` angular positions (`phi = 0 .. 2π`, or half for symmetric quadrant export)
- `n_len` axial positions (`t = 0 .. 1`)

This yields:
- `inner_points[n_phi, n_len+1, 3]` — inner horn surface points (x, y, z)
- `outer_points[n_phi, n_len+1, 3]` — outer wall shell points, only when `encDepth == 0` and `wallThickness > 0`

### 1.2 Longitudinal Wires

`_build_surface_from_points(points, closed)` constructs the horn surface using **longitudinal wires**:

1. For each phi index `i` in `0..n_phi-1`:
   - Create one BSpline wire from `points[i, :, :]` — a curve from throat to mouth at constant phi.
   - This wire is `closed=False` (it is a profile along the horn axis, not a ring).

2. For each adjacent pair of wires `(i, i+1)`:
   - Call `addThruSections([wire_i, wire_{i+1}], makeSolid=False, makeRuled=True)`.
   - This produces one ruled surface strip between two adjacent longitudinal profiles.

3. For full 360° closed models (`closed=True`):
   - Add one final wrap-around strip between `wire[n_phi-1]` and `wire[0]`.

**Why longitudinal wires (not cross-sectional)?**

For non-circular ATH profiles where the axial length L varies with phi, cross-sections at the same
parametric `t` value have different z-positions for different phi angles. A cross-sectional BSpline
ring at constant `t` is therefore a non-planar 3D space curve. `addThruSections` on two non-planar
rings can produce a twisted, self-intersecting surface. Longitudinal wires are well-behaved curves
along the axis of the horn and always produce correct ruled strips regardless of whether L is
phi-dependent.

### 1.3 Throat and Mouth End Caps

- `_build_throat_disc(inner_points)` — flat circular/elliptical disc at the throat (source surface).
- `_build_rear_wall(inner_points, outer_points)` — annular ring at the throat connecting inner to outer.
- `_build_mouth_rim(inner_points, outer_points)` — annular ring at the mouth connecting inner to outer.

These end caps are still constructed from cross-sectional wires (one ring at z=throat or z=mouth),
which is correct because these end caps are always planar surfaces.

## 2. Outer Geometry

Outer geometry presence is controlled by enclosure/wall parameters — **not by `sim_type`**.

`sim_type` only specifies the BEM radiation condition (1 = infinite baffle, 2 = free-standing)
and has no effect on which surfaces are generated.

| Condition | What is built |
|---|---|
| `enc_depth > 0` | `_build_enclosure_box` — cabinet box with mouth cutout in front face |
| `enc_depth == 0` and `wall_thickness > 0` | Outer wall shell via `_build_surface_from_points(outer_points)` + rear wall disc + mouth rim ring |
| Both 0 | No outer geometry |

### 2.1 Enclosure Box (`_build_enclosure_box`)

When `enc_depth > 0`:

1. Compute the bounding box of the mouth profile from `inner_points[:, -1, :]`.
2. Expand by space margins (`enc_space_l/r/t/b`) to get cabinet outer extents.
3. `z_front = max(inner_points[:, -1, 2])` — front face of cabinet at the maximum mouth z-position.
4. `z_back = z_front - enc_depth` — back panel position.
5. Build a solid box: `gmsh.model.occ.addBox(x0, y0, z_back, dx, dy, enc_depth)`.
6. Build a prism from the mouth ring profile (two coplanar wires at z_front ± ε, `makeSolid=True`).
7. Cut the prism from the box using `gmsh.model.occ.cut([(3, box_tag)], [(3, prism_tag)])`.
   This creates a through-hole in the front face where the waveguide mouth exits.
8. Collect all boundary surfaces of the result and tag them as SD2G0.

If prism solid creation fails (degenerate mouth profile), falls back to the box without a hole.

**Build order:** The enclosure box boolean cut is always performed **before** any inner horn surfaces
are created. This prevents the `cut` operation from accidentally removing horn OCC entities.

### 2.2 Wall Shell

When `enc_depth == 0` and `wall_thickness > 0`:

- `_compute_point_grids` computes `outer_points` as the normal-offset of `inner_points`.
- `_build_surface_from_points(outer_points)` builds the outer wall surface (same longitudinal
  wire algorithm as the inner surface).
- `_build_rear_wall` adds the annular rear disc.
- `_build_mouth_rim` adds the annular mouth opening ring.

## 3. Physical Surface Groups

Surface group assignment is in `_assign_physical_groups`:

| Physical group | BEM tag | Description |
|---|---|---|
| `"SD1G0"` | 1 | Inner horn surface |
| `"SD1D1001"` | 2 | Throat source disc |
| `"SD2G0"` | 3 | Outer wall shell or enclosure (present when wallThickness>0 or encDepth>0) |

SD2G0 is emitted only when any exterior surface dimtags are present (non-empty `exterior_tags`).
`sim_type` no longer gates SD2G0 generation.

## 4. Mesh Size Control

`_configure_mesh_size(params)` assigns Gmsh field-based mesh size control:

- `Distance` + `Threshold` fields map element size from `throatResolution` (at throat) to
  `mouthResolution` (at mouth) using embedded geometry points.
- `rearResolution` is applied to `rear` and `enclosure` surface groups.
- All resolution values pass through 1:1 from config (no scaling).

## 5. Parameters Passed to `build_waveguide_mesh`

Key geometry parameters from `WaveguideParamsRequest` (defined in `server/app.py`):

| Parameter | Default | Description |
|---|---|---|
| `formula_type` | required | `"R-OSSE"` or `"OSSE"` |
| `wall_thickness` | 0 | Horn wall shell thickness [mm]. Outer shell built when > 0 and enc_depth == 0 |
| `enc_depth` | 0 | Enclosure cabinet depth [mm]. Box built when > 0 |
| `enc_space_l/r/t/b` | 25 | Extra space around mouth bounding box [mm] |
| `enc_edge` | 18 | Edge rounding radius [mm] (reserved for future rounded edges) |
| `sim_type` | 1 | BEM radiation condition only: 1 = infinite baffle, 2 = free-standing |
| `throat_resolution` | 5 | Gmsh element size at throat [mm] |
| `mouth_resolution` | 10 | Gmsh element size at mouth [mm] |
| `rear_resolution` | 10 | Gmsh element size at rear/enclosure surfaces [mm] |
| `angular_segments` | 80 | Number of phi sampling angles for OCC surface construction |
| `length_segments` | 20 | Number of axial sample points for OCC surface construction |

## 6. Symmetry

The frontend (`src/geometry/symmetry.js`) auto-detects the maximum valid symmetry domain before
submitting to the OCC builder:

- Samples phi-dependent parameters (R, a) at 8 discrete angles.
- Checks XZ-plane symmetry (f(φ) ≈ f(-φ)) and YZ-plane symmetry (f(φ) ≈ f(π-φ)).
- Returns `'1'` (quarter), `'14'` / `'12'` (half), or `'1234'` (full).
- Conservative: returns `'1234'` when a guiding curve is active (gcurveType ≠ 0).

The backend (`server/solver/symmetry.py`) performs an independent symmetry reduction during the
BEM solve stage (not during mesh generation).

## 7. Legacy JS .geo Path (Fallback)

When the Python API is unavailable (returns 503), the frontend falls back to the JS `.geo` builder:

- `src/export/gmshGeoBuilder.js` → `buildGmshGeo(params)`: generates Gmsh script text with
  polyhedral points, splines, and physical groups.
- The `.geo` text is POSTed to `POST /api/mesh/generate-msh` for server-side meshing.
- Parameters pass through `buildGmshExportParams()` in `src/app/exports.js` (1:1, no scaling).
- Supports the same set of `Mesh.*` parameters as the Python OCC path.

Symmetry auto-detection is **not** applied to the JS `.geo` fallback path.
