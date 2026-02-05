# Geometry System Redesign Proposal

## Current Problems

### 1. Manual Vertex/Index Management
The current system manually creates vertices and triangulation:
- ❌ Error-prone: Easy to create gaps, overlaps, or degenerate triangles
- ❌ Hard to debug: Difficult to visualize what's happening
- ❌ Brittle: Small changes break connections
- ❌ Not watertight: Gaps at front/back enclosure connections

### 2. Missing Geometric Validation
- No guarantee meshes are watertight
- No automatic normal calculation
- No self-intersection detection
- Manual stitching of different geometries

### 3. Limited Operations
- Can't do boolean operations (union, difference, intersection)
- Can't fillet/chamfer edges automatically
- Can't validate manufacturability

## Proposed Architecture

### High-Level Flow
```
User Parameters
    ↓
Geometric Primitives (OpenCascade.js / JSCAD)
    ↓
CSG Operations (union, difference, fillet)
    ↓
Watertight Solid Model
    ↓
Export to Format:
    ├─→ STL (3D printing)
    ├─→ MSH via Gmsh (BEM simulation)  
    ├─→ STEP (CAD interchange)
    └─→ WebGL mesh (viewport preview)
```

## Technology Options

### Option 1: OpenCascade.js (RECOMMENDED)
**Pros:**
- ✅ Industry-standard CAD kernel (used in FreeCAD, Salome)
- ✅ Full parametric solid modeling
- ✅ Boolean operations, fillets, sweeps, lofts
- ✅ STEP/IGES export for professional CAD
- ✅ Well-maintained, active development
- ✅ Can integrate with Gmsh directly

**Cons:**
- ⚠️ Large library (~30MB WASM)
- ⚠️ Learning curve for API

**Use Case:**
```javascript
// Pseudo-code for horn generation
const profile = createSplineProfile(ossePoints);
const path = createSpiralPath(L, rotations);
const horn = pipe(profile, path);

const enclosure = box(width, height, depth);
const filleted = fillet(enclosure, edgeRadius);

const complete = union(horn, filleted);
exportSTL(complete);
exportGmshMesh(complete, maxElementSize);
```

### Option 2: JSCAD (OpenJSCAD)
**Pros:**
- ✅ Lightweight, pure JavaScript
- ✅ CSG operations built-in
- ✅ Easy to learn, code-based modeling
- ✅ STL export native
- ✅ Good for programmatic geometry

**Cons:**
- ⚠️ Less powerful than OpenCascade
- ⚠️ No STEP export
- ⚠️ Limited advanced operations
- ⚠️ CSG can create self-intersections

**Use Case:**
```javascript
const { cylinder, cube, union, subtract } = require('@jscad/modeling');

const horn = loft(profiles);  // Approximate with sections
const box = cube({ size: [w, h, d] });
const complete = union(horn, box);
```

### Option 3: Three.js CSG (Current + CSG)
**Pros:**
- ✅ Already using Three.js
- ✅ No new dependencies
- ✅ Can keep current rendering

**Cons:**
- ❌ Still manual vertex management
- ❌ CSG libraries not robust
- ❌ No true solid modeling
- ❌ Still would have connection issues

### Option 4: Hybrid Approach (RECOMMENDED FOR PHASED MIGRATION)
Keep current system but add:
1. **Gmsh for meshing** - Generate STL, let Gmsh handle triangulation
2. **Validation layer** - Check for watertight, self-intersections
3. **Gradual refactor** - Move to OpenCascade over time

## Recommended Implementation Plan

### Phase 1: Gmsh Integration (Immediate)
```javascript
// Generate geometry as smooth surfaces (not triangles)
const hornSurface = generateOSSESurface(params);
const enclosureSurface = generateEnclosureSurface(params);

// Export to Gmsh .geo format
const geoFile = `
  Point(1) = {0, 0, 0, lc};
  ...
  Surface(1) = {hornProfile};
  Surface(2) = {enclosureProfile};
`;

// Call Gmsh to generate mesh
gmsh.model.occ.importShapes(geoFile);
gmsh.model.occ.synchronize();
gmsh.model.mesh.generate(3);
gmsh.write("output.msh");
```

**Benefits:**
- ✅ Fixes connection issues (Gmsh handles it)
- ✅ Watertight guaranteed
- ✅ Better mesh quality
- ✅ Can control element sizes
- ✅ Works with BEM solver

### Phase 2: OpenCascade.js (Long-term)
```javascript
import { initOpenCascade } from "opencascade.js";

const oc = await initOpenCascade();

// Define horn profile curve
const curve = new oc.Geom_BSplineCurve(controlPoints);
const wire = oc.BRepBuilderAPI_MakeWire(curve);

// Sweep along path to create horn
const spine = createSpiral(L, rotations);
const horn = new oc.BRepOffsetAPI_MakePipe(wire, spine);

// Create enclosure
const box = new oc.BRepPrimAPI_MakeBox(w, h, d);
const fillet = new oc.BRepFilletAPI_MakeFillet(box);

// Boolean union
const fuse = new oc.BRepAlgoAPI_Fuse(horn, fillet);

// Export
const stl = new oc.StlAPI_Writer();
stl.Write(fuse.Shape(), "output.stl");

// Or export to Gmsh
const step = new oc.STEPControl_Writer();
step.Transfer(fuse.Shape());
step.Write("output.step");
// Then: gmsh output.step -3 -o output.msh
```

**Benefits:**
- ✅ True CAD-quality geometry
- ✅ Parametric (can rebuild on parameter change)
- ✅ Professional CAD export
- ✅ No manual vertex management
- ✅ Filleting/chamfering automatic

## Migration Strategy

### Week 1-2: Add Gmsh Backend
- Install Gmsh.jl or gmsh-wasm
- Create .geo file generator from current geometry
- Add Gmsh meshing option alongside current system
- Compare outputs

### Week 3-4: Refactor Geometry Generation
- Separate "geometric definition" from "mesh generation"
- Create abstract surface representations
- Let Gmsh handle all triangulation

### Week 5-8: OpenCascade Integration
- Add OpenCascade.js dependency
- Rewrite horn generation using sweeps/lofts
- Rewrite enclosure using primitives + booleans
- Keep Gmsh for final mesh output

## File Structure

```
src/
├── geometry/
│   ├── primitives/           # NEW: Geometric primitives
│   │   ├── horn.js          # OSSE/R-OSSE as parametric curves
│   │   ├── enclosure.js     # Box + fillets as solid
│   │   └── morphing.js      # Shape transformations
│   │
│   ├── cad/                  # NEW: CAD kernel interface
│   │   ├── opencascade.js   # OpenCascade.js wrapper
│   │   ├── operations.js    # Boolean ops, fillets
│   │   └── export.js        # STL, STEP, Gmsh export
│   │
│   ├── meshing/              # NEW: Mesh generation
│   │   ├── gmsh.js          # Gmsh integration
│   │   ├── validate.js      # Watertight check, normals
│   │   └── optimize.js      # Mesh quality, simplification
│   │
│   └── legacy/               # Current system (keep during transition)
│       └── meshBuilder.js
```

## Benefits of New Architecture

### For Development
- ✅ **Easier debugging**: Visual CAD tools can inspect geometry
- ✅ **Faster iteration**: No manual vertex wrangling
- ✅ **Better testing**: Can validate against known CAD models
- ✅ **Reusable**: Geometry primitives work for any mesh generator

### For Users
- ✅ **Higher quality**: Watertight, manifold meshes guaranteed
- ✅ **More formats**: STL, STEP, IGES, MSH, etc.
- ✅ **Better simulation**: Clean meshes → better BEM results
- ✅ **CAD integration**: Export to FreeCAD, Fusion360, etc.

### For Features
- ✅ **Easy additions**: Add chamfers, threads, mounting holes
- ✅ **Assembly**: Combine multiple horns, add waveguides
- ✅ **Optimization**: Parametric CAD enables auto-optimization
- ✅ **Manufacturing**: Can generate toolpaths, check clearances

## Immediate Next Steps

1. **Install Gmsh** (command-line or WASM)
2. **Create STL export** from current geometry
3. **Test Gmsh meshing** on current STL
4. **Compare quality** with current triangulation
5. **If successful**: Replace triangulation with Gmsh
6. **Plan OpenCascade migration** based on results

## Example: Current vs. Proposed

### Current (1,412 lines of manual vertices/indices)
```javascript
// Manual vertex creation
vertices.push(x, y, z);
vertices.push(x2, y2, z2);
// Manual triangulation
indices.push(i1, i2, i3);
indices.push(i1, i3, i4);
// Hope it's watertight...
```

### Proposed (clean geometric operations)
```javascript
// Define geometry
const horn = createHornSolid(params);
const enclosure = createEnclosureSolid(params);

// Combine
const assembly = union(horn, enclosure);

// Generate mesh
const mesh = gmsh.mesh(assembly, {
  maxElementSize: params.meshSize,
  optimize: true
});

// Guaranteed watertight ✓
```

## Conclusion

The current vertex-based approach works but is fundamentally limited. Moving to a proper CAD kernel (OpenCascade.js) with Gmsh meshing would:

- **Fix current gaps** automatically
- **Enable new features** easily
- **Improve quality** dramatically
- **Future-proof** the codebase

**Recommended approach**: Start with Gmsh integration (Phase 1) to prove the concept, then migrate to OpenCascade.js (Phase 2) for long-term maintainability.
