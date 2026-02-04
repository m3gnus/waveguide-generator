# ATH Calibration Status

This document tracks the work to calibrate MWG to produce outputs that match ATH (Acoustical Topology Horn) bitwise.

## Goal

Generate STL and MSH files from MWG that are byte-for-byte identical to ATH outputs.

## Architecture

ATH uses a two-stage pipeline:
1. Generate `mesh.geo` file (Gmsh geometry format)
2. Process through Gmsh to produce `mesh.msh` and `*.stl`

MWG must replicate this exactly:
- Generate geo files matching ATH's format
- Use same Gmsh version (4.15+) with identical settings

## Completed Work

### 1. Gmsh GEO Export (`src/export/msh.js`)
- `exportFullGeo()` function generates complete geo files
- Matches ATH format: Points, Lines, Curve Loops, Plane Surfaces
- Uses ATH's interleaved line pattern (radial, angular per point)
- Fixed mesh size at 50.0 (ATH convention)
- Curve Loops start at ID 511, Surfaces at 147

### 2. Enclosure Stitching Fix (`src/geometry/enclosure/builder.js`)
- Fixed degenerate triangles in mouth-to-baffle connection
- Proper triangulation when multiple mouth vertices map to same enclosure vertex
- Eliminates visual artifacts (edges sticking out of front baffle)

### 3. Comparison Tools (`scripts/`)
- `ath-compare.js`: Automated testing against ATH references
- `gmsh-export.py`: Gmsh processing pipeline

## Remaining Work

### 1. Coordinate Precision (In Progress)
Current GEO files have minor coordinate differences (~0.001mm):
```
Ours: Point(4)={12.311,3.118,0.000,50.0};
ATH:  Point(4)={12.311,3.119,0.000,50.0};
```

**Root cause analysis:**
ATH uses corner-aware non-uniform angular distribution. The angles computed for the throat ring differ slightly from our implementation.

**ATH's actual angles** (computed from reference geo file):
```
Point  Angle(deg)  Uniform(deg)  Diff
  1      0.000        0.000     +0.000
  2      4.738        4.500     +0.238
  3      9.476        9.000     +0.476
  4     14.217       13.500     +0.717
  ...
 10     39.751       40.500     -0.749
 11     41.747       45.000     -3.253  (corner region)
 12     43.449       49.500     -6.051
  ...
 21     90.000       90.000     +0.000
```

**Files to investigate:**
- `src/geometry/meshBuilder.js` - `buildAngleList()`, `buildQuadrantAngles()`
- The corner-aware sampling clusters more points around 45° (morphCorner region)
- Check if ATH rounds cos/sin to 6 decimal places before coordinate calculation

**Verification approach:**
1. Run `node scripts/ath-compare.js` - shows GEO diff on line 7 (Point 4)
2. Compare first ring coordinates between generated and reference geo files
3. Focus on matching the angular distribution exactly

### 2. LFSource.B Geometry (Pending)
Speaker cone cutout on front baffle for low-frequency driver:
- Cone depth = 0.4 × Radius
- Spherical cap radius = 1.5 × Spacing
- Parameters defined in `src/config/schema.js` under 'ENCLOSURE':
  - `lfSourceBRadius` - Cone radius in mm
  - `lfSourceBSpacing` - Distance from horn center in mm
  - `lfSourceBDrivingWeight` - Amplitude weight for BEM
  - `lfSourceBSID` - Source ID for BEM solver

**Implementation location:** `src/geometry/enclosure/builder.js`
- Add cone + spherical cap geometry to front baffle
- Reference ATH configs with LFSource.B: check `_references/testconfigs/` for examples

### 3. Full Test Suite (Pending)
Run comparison across all configs in `_references/testconfigs/`:
- Simple OSSE configs
- R-OSSE configs with rollback
- Configs with enclosures
- Configs with morphing/corners

## Technical Details

### ATH Coordinate System
- GEO format: X = r×cos(p), Y = r×sin(p), Z = axial
- No vertical offset in geo file (applied in bem_mesh.geo only)

### ATH Angular Sampling
ATH uses corner-aware non-uniform angular distribution:
- More points clustered around 45° (corner region)
- Fewer points on flat sides
- Based on morphing corner radius

### ATH Z-Map
Non-linear axial distribution (21 points for 20 segments):
```javascript
const ATH_ZMAP_20 = [
  0.0, 0.01319, 0.03269, 0.05965, 0.094787,
  0.139633, 0.195959, 0.263047, 0.340509, 0.427298,
  0.518751, 0.610911, 0.695737, 0.770223, 0.833534,
  0.88547, 0.925641, 0.955904, 0.977809, 0.992192, 1.0
];
```

## Running Tests

```bash
# Compare all configs
node scripts/ath-compare.js

# Process a single geo file
python scripts/gmsh-export.py _references/testconfigs/_generated/tritonia/mesh.geo
```

## Reference Files

Test configs and ATH reference outputs are in:
- `_references/testconfigs/*.txt` - ATH config files
- `_references/testconfigs/<name>/` - Reference outputs per config
  - `mesh.geo` - Gmsh geometry
  - `*.stl` - Binary STL mesh
  - `*.msh` - Gmsh mesh for BEM
