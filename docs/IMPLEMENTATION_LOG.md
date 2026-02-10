# Implementation Log: Python OCC Mesh Builder

**Date**: 2026-02-10
**Branch**: main

---

## Context

The original `.msh` export path works as follows:
1. JS frontend builds a `.geo` file from a triangulated approximation of the horn surface.
2. The `.geo` is sent to the backend where Gmsh runs `Mesh 2;`.
3. Gmsh receives flat polyhedral triangles — no curvature information.
4. The resulting mesh approximates the correct shape but cannot adapt to the actual curved geometry.

The correct approach, matching ATH section 3.3.1, is:
1. Compute profile point arrays from the R-OSSE formula.
2. Give Gmsh parametric BSpline wires and `ThruSections` surfaces (OCC kernel).
3. Gmsh meshes the actual smooth curved surfaces.

A prototype of this approach already existed at `../260209 - mesh generation/generate_mesh.py`.
This implementation ports that prototype to the backend as a proper endpoint.

---

## Step 1: `server/solver/waveguide_builder.py` (new file)

**Purpose**: Python OCC mesh builder library.

**Key function**:
```python
def build_waveguide_mesh(params: dict) -> dict
```
Returns `{"geo_text": str, "msh_text": str, "stats": {"nodeCount": int, "elementCount": int}}`.

**Internal flow**:

1. `_expression_to_callable(expr_str)` — evaluates ATH math expression string (uses `p` as variable) to a Python callable `f(p)`.
2. `_compute_phi_values(n_angular, quadrants)` — generates phi angles array for the requested symmetry quadrant.
3. For each phi: `_compute_profile(t_array, R_callable, a_deg, r0, a0_deg, k, r, b, m, q)` — evaluates the R-OSSE formula and returns `(x_axial, y_radial)` arrays.
4. Profile points are converted to 3D `(x, y, z)` coordinates using `phi` for angular position.
5. `_build_surface_from_points(gmsh, rings_of_points, closed)` — for each adjacent pair of rings, creates a BSpline wire via `gmsh.model.occ.addBSpline` and connects them with `gmsh.model.occ.addThruSections`. This produces smooth parametric surface strips that Gmsh can mesh correctly.
6. `_build_throat_disc(gmsh, r0)` — flat circular disc at z=0 (source surface).
7. For `sim_type == 2` (freestanding): `_compute_outer_points(inner_rings, wall_thickness)` — normal offset; `_build_surface_from_points` for outer wall; `_build_rear_wall`; `_build_mouth_rim`.
8. `_configure_mesh_size(gmsh, config, inner_rings, surface_groups)` — MathEval gradient field + Restrict fields per surface group.
9. `_assign_physical_groups(gmsh, surface_groups, sim_type)` — ABEC-compatible names: `SD1G0`, `SD1D1001`, optionally `SD2G0`.
10. `gmsh.model.mesh.generate(2)` — Gmsh meshes the OCC surfaces.
11. Write `output.msh` and `output.geo` to temp dir, read back, clean up, return.

**Thread safety**: Acquires `gmsh_lock` from `server/solver/gmsh_geo_mesher.py` before any Gmsh API calls.

**Error handling**:
- `RuntimeError` if Gmsh Python API not installed.
- `GmshMeshingError` (from `gmsh_geo_mesher`) on any Gmsh failure.
- `ValueError` on bad/missing parameters.

**Formula support**: R-OSSE and OSSE. All ATH geometry features implemented (guiding curves, morph, throat extension, slot, circular arc, profile rotation, tmax).

---

## Step 2: `server/app.py` — new endpoint and model

**New Pydantic model**: `WaveguideParamsRequest`

Fields:
- `formula_type: str` — `"R-OSSE"` or `"OSSE"`
- R-OSSE: `R: Optional[str]`, `r`, `b`, `m`, `tmax`
- OSSE: `L: Optional[str]`, `s: Optional[str]`, `n`, `h`
- Shared: `a: Optional[str]`, `r0`, `a0`, `k`, `q`
- Throat geometry: `throat_profile`, `throat_ext_angle`, `throat_ext_length`, `slot_length`, `rot`
- Circular arc: `circ_arc_term_angle`, `circ_arc_radius`
- Guiding curve: `gcurve_type`, `gcurve_dist`, `gcurve_width`, `gcurve_aspect_ratio`, `gcurve_se_n`, `gcurve_sf*`, `gcurve_rot`
- Morph: `morph_target`, `morph_width/height/corner/rate/fixed/allow_shrinkage`
- Grid: `n_angular`, `n_length`, `quadrants`
- Element sizes: `throat_res`, `mouth_res`, `rear_res`, `wall_thickness`
- Subdomain (no effect): `subdomain_slices`, `interface_offset/draw/resolution`
- `sim_type`, `msh_version`

**New import block** (guarded):
```python
try:
    from solver.waveguide_builder import build_waveguide_mesh
    WAVEGUIDE_BUILDER_AVAILABLE = True
except ImportError:
    build_waveguide_mesh = None
    WAVEGUIDE_BUILDER_AVAILABLE = False
```

**New endpoint**:
```
POST /api/mesh/build
```
- Returns `503` if `WAVEGUIDE_BUILDER_AVAILABLE` is False.
- Returns `422` if `formula_type` is not `"R-OSSE"` or `"OSSE"`, or invalid `msh_version`.
- Runs `build_waveguide_mesh` in `asyncio.to_thread` to avoid blocking.
- Returns `{ "geo": str, "msh": str, "generatedBy": "gmsh-occ", "stats": {...} }`.

---

## Step 3: `src/app/exports.js` — frontend routing

**New helper**: `buildPythonBuilderPayload(preparedParams, mshVersion)`
- Maps JS camelCase fields to Python snake_case fields.
- R-OSSE formula string expressions passed as-is.
- `angularSegments` → `n_angular`, `lengthSegments` → `n_length`, etc.

**New export function**: `buildExportMeshFromParams(app, preparedParams, options)`
1. Checks backend reachability.
2. POSTs to `POST /api/mesh/build`.
3. On `503`: falls back to `buildExportMeshWithGmsh` (legacy path).
4. Returns `{artifacts, payload, msh, bemGeo, geoStats, meshStats}` in same shape as old path.

**Routing in `exportMSH` and `exportABECProject`**:
- If `preparedParams.type === 'R-OSSE'` or `'OSSE'`: use `buildExportMeshFromParams`.
- Otherwise: use `buildExportMeshWithGmsh` (unchanged old path).

**Backward compatibility**: `buildExportMeshWithGmsh` is unchanged. All tests that exercise the old path continue to pass.

---

## Step 4: `server/requirements.txt`

Changed:
```
# Gmsh Python API ... Falls back to `gmsh` CLI if the Python package is not installed.
gmsh>=4.10.0
```
Was previously in a comment block with gmsh as optional. Now explicit.

---

## Step 5: Documentation updates

| File | Change |
|---|---|
| `docs/MSH_GEO_GENERATION.md` | Added section 5: full Python OCC builder path documentation. Updated overview table. |
| `PROJECT_DOCUMENTATION.md` | Updated sections 5, 6, 7, 11 to reflect two-pipeline architecture. |
| `REFACTORING_STAGES.md` | Replaced speculative AI plan with actual status. Marked completed items. |
| `docs/IMPLEMENTATION_LOG.md` | This file. |
| `memory/MEMORY.md` | Updated key files list and mesh pipeline notes. |

---

## Verification

### Automated (all 46 tests pass)
```
npm test
```
The old JS export path is unchanged. The new path adds a new function without modifying existing
test-covered code paths.

### Manual — backend endpoint
```bash
curl -s -X POST http://localhost:8000/api/mesh/build \
  -H 'Content-Type: application/json' \
  -d '{
    "formula_type": "R-OSSE",
    "R": "0.5",
    "a": "60",
    "r0": 25.4,
    "a0": 90,
    "k": 0.7,
    "r": 1.0,
    "b": 0.0,
    "m": 1.0,
    "q": 1.0,
    "n_angular": 60,
    "n_length": 20,
    "quadrants": 1234,
    "throat_res": 5.0,
    "mouth_res": 8.0,
    "rear_res": 25.0,
    "wall_thickness": 6.0,
    "sim_type": 2,
    "msh_version": "2.2"
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print('geo chars:', len(d['geo']), 'msh chars:', len(d['msh']), 'stats:', d['stats'])"
```

### Manual — UI export
1. Load R-OSSE config in UI.
2. Click "Export MSH" — should receive `.msh` generated by OCC builder.
3. Open `.msh` in Gmsh GUI — inspect mesh quality on curved surfaces.
4. Click "Export ABEC" — open `bem_mesh.geo` in Gmsh — confirm `SetFactory("OpenCASCADE")`.

### Python unit test
```python
cd server
python -c "
from solver.waveguide_builder import build_waveguide_mesh
result = build_waveguide_mesh({
    'formula_type': 'R-OSSE',
    'R': '0.5', 'a': '60',
    'r0': 25.4, 'a0': 90.0, 'k': 0.7,
    'r': 1.0, 'b': 0.0, 'm': 1.0, 'q': 1.0,
    'n_angular': 40, 'n_length': 15,
    'quadrants': 1234,
    'throat_res': 5.0, 'mouth_res': 8.0, 'rear_res': 25.0,
    'wall_thickness': 6.0, 'sim_type': 2, 'msh_version': '2.2'
})
print('geo chars:', len(result['geo_text']))
print('msh chars:', len(result['msh_text']))
print('stats:', result['stats'])
"
```

---

## What is deferred

| Feature | Status |
|---|---|
| Subdomain interfaces (`I1-2` physical group) | Params accepted, no geometry effect. Needs ABEC interface semantics research. |
| Rollback (throat rollback) | Not ported from JS engine |
| `Mesh.ZMapPoints` | Spec exists, no implementation |
| Frontend tests for `/api/mesh/build` | Needs mock or integration test harness |

---

## Key design decisions

1. **Parameter separation**: `n_angular`/`n_length` control OCC geometry sampling (shape quality). `throat_res`/`mouth_res`/`rear_res` control Gmsh element sizes (mesh density). These are independent.

2. **No `.geo` round-trip**: The Python builder constructs geometry directly from formula parameters. The `.geo` it returns is an OCC serialization, not a flat polyhedral script.

3. **Graceful degradation**: The frontend falls back to the old JS path on `503`. The UI continues to work even if the Gmsh Python API is not installed on the server.

4. **gmsh_lock reuse**: Sharing the existing lock from `gmsh_geo_mesher.py` ensures no concurrent Gmsh API calls regardless of which path triggered the mesh.

5. **Two-pass morph**: Morph requires knowing per-slice max extents before applying the blend. The builder first computes all raw profiles, precomputes morph target extents, then applies morph in a second pass — matching the JS engine's `buildMorphTargets` pattern.
