# Geometry Module Contract

## Scope

**Primary implementation files**:
- `src/geometry/pipeline.js` — main payload assembly pipeline
- `src/geometry/tags.js` — surface-tag assignment rules (source of truth)
- `src/geometry/engine/*` — topology generation, enclosure/horn building
- `src/modules/geometry/index.js` — public module interface

## Core Responsibilities

- **Formula evaluation**: Apply parameter values to geometry equations
- **Mesh topology**: Generate viewport and simulation meshes with deterministic structure
- **Payload assembly**: Build canonical simulation payloads with correct tag semantics
- **Quality checks**: Validate mesh invariants and surface-tag consistency
- **Prepared-parameter contract**: `prepareGeometryParams(...)` runs once at the design boundary; module paths consume prepared params via prepared-only geometry entrypoints so `scale` is not applied twice

## Canonical Payload Contract

**Required fields**:
```javascript
{
  vertices,              // flat array [x, y, z, x, y, z, ...]
  indices,              // flat array [i0, i1, i2, i0, i1, i2, ...]
  surfaceTags,          // one integer per triangle
  format,               // "msh"
  boundaryConditions,   // BC object
  metadata              // units, unitScaleToMeter, identityTriangleCounts
}
```

**Critical invariants** (enforced by validation):
- `surfaceTags.length === indices.length / 3` (one tag per triangle)
- At least one source-tagged triangle (`2`) must exist
- Tag values are code-owned in `src/geometry/tags.js` (`1`=wall, `2`=source, `3`=secondary, `4`=interface)
- Interface tag (`4`) is only emitted when enclosure/interface geometry is actually present
- `identityTriangleCounts` maps geometry face names to triangle counts (e.g., `throat_disc`, `inner_wall`); derived from face group ranges (triangle-indexed, not vertex-indexed)

## Important Runtime Behaviors

**Mesh topology**:
- Frontend payload is **always full-domain** (not trimmed by `quadrants`); OCC-adaptive meshing happens server-side
- JS canonical payload is a **validation/contract artifact** only; actual simulation meshes are OCC-generated in the backend
- Viewport rendering may internally duplicate `throat_disc` vertices for normal generation (crisp shading) without changing the canonical payload

**Geometry construction**:
- **Enclosure-geometry logic is exclusive**: either enclosure (`encDepth > 0`), or freestanding shell (`encDepth == 0 && wallThickness > 0`), or bare horn (no outer geometry)
- Enclosure generation is **OSSE-only**; `R-OSSE` with `encDepth > 0` is rejected
- When `encEdge > 0`, enclosure builder adds front/rear axial roundover strips in addition to sidewall corners
- Freestanding wall thickness follows horn surface normals; rear `throat_return` transition slopes into the back plate (not a straight cylinder)
- `scale` affects horn geometry only; enclosure depth, edge radius, and enclosure clearances remain absolute millimeter values

**Tessellation**:
- Adaptive phi-tessellation is **viewport-only** (full-circle horn rendering); not used for simulation
- Morphing derives implicit target extents from the current slice when `morphTarget` is enabled but `morphWidth`/`morphHeight` are unset

## Regression Coverage

- `tests/mesh-payload.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/enclosure-regression.test.js`
- `tests/geometry-quality.test.js`
- `tests/morph-implicit-target.test.js`
- `tests/geometry-module.test.js`
