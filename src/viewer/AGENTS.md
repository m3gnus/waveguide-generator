# Viewer Module — AI Agent Context

## Purpose

Three.js scene management, camera controls, display modes, and 3D rendering.

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Scene setup, camera, renderer | Medium |
| `annotations.js` | Dimension lines and labels | Simple |

## Public API

```javascript
import {
  createScene,           // Initialize Three.js scene
  createPerspectiveCamera, // Create camera
  createOrthographicCamera,
  createRenderer,        // WebGL renderer
  setupLighting,         // Scene lighting
  DISPLAY_MODES          // Available display modes
} from './viewer/index.js';
```

## Display Modes

| Mode | Description |
|------|-------------|
| `standard` | Default metallic material |
| `zebra` | Stripe pattern for surface analysis |
| `grid` | Grid overlay for measurements |
| `curvature` | Color-coded curvature visualization |
| `wireframe` | Triangle edges only |

## Three.js Scene Structure

```
Scene
├── AmbientLight
├── DirectionalLight (key)
├── DirectionalLight (fill)
├── HornMesh (geometry + material)
├── EnclosureMesh (if enabled)
├── GroundPlane (optional)
└── Annotations (dimension lines)
```

## For Simple Changes

1. Add display mode → modify material in `index.js`
2. Change lighting → modify `setupLighting()`
3. Add annotation → modify `annotations.js`

## For Complex Changes

Before modifying the viewer:
1. Understand Three.js BufferGeometry
2. Check camera/controls interaction
3. Test with different horn sizes

## Camera Controls

- **Orbit**: Click + drag to rotate
- **Pan**: Right-click + drag
- **Zoom**: Scroll wheel
- **Focus**: Double-click to focus on horn

## Key DOM Elements

- `#canvas-container` — Container for WebGL canvas
- `#display-mode` — Display mode dropdown
- `#camera-toggle` — Perspective/ortho toggle
- `#zoom-in`, `#zoom-out`, `#zoom-reset` — Zoom controls

## Events

```javascript
// Listen for geometry updates
AppEvents.on('geometry:updated', ({ mesh }) => {
  updateSceneMesh(mesh);
});

// Emit render request
AppEvents.emit('viewer:render');
```

## Performance Notes

- Keep triangle count reasonable (<500k for smooth interaction)
- Dispose of old meshes to prevent memory leaks
- Use `requestAnimationFrame` for rendering loop
