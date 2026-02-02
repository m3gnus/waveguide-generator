# Geometry Module — AI Agent Context

## Purpose

All horn mathematics and 3D mesh generation. This is the core computational module.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API exports | Simple |
| `hornModels.js` | OSSE, R-OSSE calculations | Complex math |
| `meshBuilder.js` | Three.js BufferGeometry construction | Medium |
| `morphing.js` | Round-to-rectangular shape morphing | Medium |
| `enclosure.js` | Enclosure box geometry | Simple |
| `rollback.js` | Mouth rollback toroidal fold | Medium |
| `expression.js` | Math expression parser | Medium |

## Public API

```javascript
import {
  calculateROSSE,      // R-OSSE horn profile at (t, p)
  calculateOSSE,       // OSSE horn profile at (t, p)
  buildHornMesh,       // Generate complete horn mesh
  applyMorphing,       // Apply shape morphing
  addEnclosureGeometry,// Add enclosure box
  addRollbackGeometry, // Add mouth rollback
  parseExpression      // Evaluate math expressions
} from './geometry/index.js';
```

## Coordinate System

```
Y-axis: Axial direction (throat at Y=0, mouth at Y=L)
X-axis: Horizontal (r * cos(p))
Z-axis: Vertical (r * sin(p))

3D vertex: vx = radius * cos(angle), vy = axialPosition, vz = radius * sin(angle)
```

## Key Concepts

- **t parameter**: Normalized position along horn (0=throat, 1=mouth)
- **p parameter**: Azimuthal angle in radians (0=horizontal, π/2=vertical)
- **Profile**: Array of (x, y) points defining horn shape at each t value
- **Ring**: All vertices at the same t value

## For Simple Changes

1. Parameter adjustments → modify `hornModels.js`
2. Mesh density → modify `meshBuilder.js`
3. New shape morph → modify `morphing.js`

## For Complex Changes

Before adding a new horn model:
1. Read existing models in `hornModels.js`
2. Follow the same pattern (returns { x, y } at given t, p)
3. Add export to `index.js`
4. Update `meshBuilder.js` to call new model
5. Add UI controls in `src/ui/paramPanel.js`

## Testing

```bash
npm test -- --grep "geometry"
```

## Dependencies

- **Three.js** — BufferGeometry, Vector3
- **events.js** — Emits `geometry:updated`
- **state.js** — Reads current parameters

## Common Patterns

```javascript
// Horn model function signature
function calculateXYZ(t, p, params) {
  // t: 0-1 along horn axis
  // p: 0-2π azimuthal angle
  // params: horn parameters
  return { x: axialPosition, y: radius };
}

// Mesh generation
const mesh = buildHornMesh(params);
// Returns Three.js BufferGeometry with vertices, indices, normals
```
