# ATH Horn Design Platform -- Architecture & Development Plan

## 1. Project Goal & Scope

### Vision

Transform the existing ATH Horn 3D Visualizer into a **complete horn design and simulation platform** that can:

1. **Design** -- Parametrically define OSSE, R-OSSE, and OS-GOS waveguides with full ATH parameter support
2. **Preview** -- Real-time 3D visualization with surface analysis (curvature, zebra, wireframe)
3. **Simulate** -- Run BEM acoustic simulations (via bempp-cl) directly from the browser
4. **Analyze** -- Display directivity, polar plots, impedance, SPL maps
5. **Optimize** -- Batch parameter sweeps with automated scoring and ranking
6. **Export** -- STL, Gmsh .geo, ABEC project files, ATH config, CSV profiles

### What Already Exists

The current codebase (`index.html`, `main.js`, `style.css`) is a self-contained browser app that:

- Computes R-OSSE and OSSE horn profiles in real time
- Renders 3D geometry with Three.js (multiple display modes)
- Supports config file import/export (ATH format)
- Exports STL meshes
- Handles morphing (round-to-rectangular), enclosure geometry, and mouth rollback
- Provides a full parameter UI with live-update sliders

**This existing tool is the foundation. It will be wrapped, stabilized, and extended -- not rewritten.**

### Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| OSSE, R-OSSE, OS-GOS horn models | Driver/motor FEA simulation |
| BEM acoustic simulation (Helmholtz) | Full LEM circuit simulation |
| Directivity, impedance, SPL output | Room acoustics / ray tracing |
| STL, Gmsh, ABEC export | Commercial CAD format export (STEP/IGES) |
| Parameter optimization sweeps | Machine learning optimization |
| Single-user browser application | Multi-user collaboration |

---

## 2. Architecture Overview

### Design Principles

1. **Module Isolation** -- Each module has a single responsibility and communicates through typed interfaces
2. **Event-Driven** -- Modules communicate via a central event bus, not direct calls
3. **Progressive Enhancement** -- Each phase adds capability without breaking existing functionality
4. **Serializable State** -- All application state can be serialized to JSON for save/load/undo
5. **AI-Friendly** -- Each module is a single file or small directory, documented with JSDoc, testable in isolation

### High-Level Module Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser Application                       │
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ UI Panel  │  │ 3D View  │  │ Results  │  │  Optimization    │ │
│  │ Module    │  │ Module   │  │ Viewer   │  │  Dashboard       │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
│       │              │              │                  │           │
│  ┌────┴──────────────┴──────────────┴──────────────────┴────────┐ │
│  │                     Event Bus (AppEvents)                     │ │
│  └────┬──────────────┬──────────────┬──────────────────┬────────┘ │
│       │              │              │                  │           │
│  ┌────┴─────┐  ┌─────┴────┐  ┌─────┴──────┐  ┌───────┴────────┐ │
│  │ Geometry │  │  Config   │  │  Acoustic  │  │   Export       │ │
│  │ Core     │  │  Manager  │  │  Solver    │  │   Engine       │ │
│  └──────────┘  └──────────┘  └────────────┘  └────────────────┘ │
│                                     │                             │
│                              ┌──────┴──────┐                     │
│                              │ Python/WASM │                     │
│                              │ BEM Backend │                     │
│                              └─────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### Communication Pattern

All inter-module communication goes through `AppEvents`:

```javascript
// Publishing
AppEvents.emit('geometry:updated', { mesh, profile, params });

// Subscribing
AppEvents.on('geometry:updated', (data) => { ... });
```

Event naming convention: `module:action` (e.g., `config:loaded`, `solver:complete`, `ui:paramChanged`).

---

## 3. Core Modules

### Module 1: Geometry Core (`src/geometry/`)

**Purpose:** All horn math and mesh generation. This wraps the existing `calculateROSSE()`, `calculateOSSE()`, profile generation, enclosure, rollback, and morphing logic.

**Files:**
```
src/geometry/
  index.js          -- Public API (re-exports)
  rosse.js          -- R-OSSE calculation (from current calculateROSSE)
  osse.js           -- OSSE calculation (from current calculateOSSE)
  osgos.js          -- OS-GOS calculation (future)
  profile.js        -- Profile generation (angular sweep at each t/z step)
  morphing.js       -- Round-to-target shape morphing
  enclosure.js      -- Enclosure box geometry
  rollback.js       -- Mouth rollback toroidal fold
  meshBuilder.js    -- Three.js BufferGeometry construction
  expression.js     -- Math expression parser (current parseExpression)
```

**Public API:**
```javascript
// geometry/index.js
export { calculateROSSE } from './rosse.js';
export { calculateOSSE } from './osse.js';
export { generateProfile } from './profile.js';
export { buildHornMesh } from './meshBuilder.js';
export { applyMorphing } from './morphing.js';
export { buildEnclosure } from './enclosure.js';
export { buildRollback } from './rollback.js';
export { parseExpression } from './expression.js';
```

**Key interface -- HornProfile:**
```javascript
/**
 * @typedef {Object} HornProfile
 * @property {Float32Array} vertices    -- [x0,y0,z0, x1,y1,z1, ...] flattened
 * @property {Uint32Array}  indices     -- triangle index buffer
 * @property {Float32Array} normals     -- per-vertex normals
 * @property {number}       vertexCount
 * @property {number}       triangleCount
 * @property {number[][]}   rings       -- ring[i] = array of vertex indices at that t/z step
 * @property {Object}       bounds      -- { minX, maxX, minY, maxY, minZ, maxZ }
 * @property {Object}       metrics     -- { throatRadius, mouthWidth, mouthHeight, length }
 */
```

**Migration from current code:** Extract functions from `main.js` lines 6-80 (math), lines 700-1100 (mesh building). No logic changes needed -- just splitting into files and adding typed interfaces.

---

### Module 2: Config Manager (`src/config/`)

**Purpose:** Parse, validate, serialize, and manage ATH configuration files. Handles both block-format (R-OSSE) and flat dot-notation (OSSE) configs.

**Files:**
```
src/config/
  index.js          -- Public API
  parser.js         -- ATH config parser (current ATHConfigParser)
  serializer.js     -- Params -> ATH config string
  validator.js      -- Parameter range validation
  defaults.js       -- Default parameter sets for each model type
  schema.js         -- Parameter schema definitions (name, type, min, max, unit)
```

**Parameter Schema (central source of truth):**
```javascript
// config/schema.js
export const PARAM_SCHEMA = {
  'R-OSSE': {
    R:  { type: 'expression', label: 'Mouth Radius', unit: 'mm', default: '140 * ...' },
    a:  { type: 'expression', label: 'Aperture Angle', unit: 'deg', default: '25 * ...' },
    a0: { type: 'number', label: 'Throat Angle', unit: 'deg', min: 0, max: 90, default: 15.5 },
    k:  { type: 'number', label: 'Rounding', min: 0.1, max: 10, default: 2 },
    m:  { type: 'number', label: 'Apex Shift', min: 0, max: 1, default: 0.85 },
    b:  { type: 'expression', label: 'Bending', default: '0.2' },
    r:  { type: 'number', label: 'Apex Radius', min: 0.01, max: 2, default: 0.4 },
    q:  { type: 'number', label: 'Shape Factor', min: 0.5, max: 10, default: 3.4 },
    r0: { type: 'number', label: 'Throat Radius', unit: 'mm', min: 1, max: 50, default: 12.7 },
    tmax: { type: 'number', label: 'Truncation', min: 0.5, max: 1.5, default: 1.0 },
  },
  'OSSE': {
    L:  { type: 'number', label: 'Axial Length', unit: 'mm', min: 10, max: 500, default: 120 },
    a:  { type: 'expression', label: 'Coverage Angle', unit: 'deg', default: '48.5 - ...' },
    a0: { type: 'number', label: 'Throat Angle', unit: 'deg', min: 0, max: 60, default: 15.5 },
    r0: { type: 'number', label: 'Throat Radius', unit: 'mm', min: 1, max: 50, default: 12.7 },
    k:  { type: 'number', label: 'Expansion', min: 0.1, max: 15, default: 7 },
    s:  { type: 'expression', label: 'Flare', default: '0.58 + ...' },
    n:  { type: 'number', label: 'Curvature', min: 1, max: 10, default: 4.158 },
    q:  { type: 'number', label: 'Truncation', min: 0.1, max: 2, default: 0.9909 },
    h:  { type: 'number', label: 'Shape Factor', min: 0, max: 10, default: 0 },
  },
  // Shared sub-schemas for morph, enclosure, mesh, source, ABEC...
};
```

**Validation produces typed errors:**
```javascript
/**
 * @typedef {Object} ValidationResult
 * @property {boolean} valid
 * @property {Array<{param: string, message: string, severity: 'error'|'warning'}>} issues
 */
```

---

### Module 3: 3D Viewer (`src/viewer/`)

**Purpose:** Three.js scene management, camera controls, display modes, rendering. Wraps all current Three.js code.

**Files:**
```
src/viewer/
  index.js          -- Public API
  scene.js          -- Scene setup, lights, ground plane
  camera.js         -- Camera management (perspective/ortho toggle, focus, zoom)
  controls.js       -- OrbitControls setup and keyboard shortcuts
  materials.js      -- Material definitions (metal, zebra, wireframe, curvature)
  hornRenderer.js   -- Horn mesh management (add/remove/update mesh in scene)
  annotations.js    -- Future: dimension lines, labels
```

**Public API:**
```javascript
// viewer/index.js
export class HornViewer {
  constructor(containerElement) { ... }
  setMesh(hornProfile) { ... }         // Update displayed geometry
  setDisplayMode(mode) { ... }         // 'standard'|'zebra'|'grid'|'curvature'
  focusOnHorn() { ... }
  resetCamera() { ... }
  toggleProjection() { ... }
  getStats() { ... }                   // { vertices, triangles }
  dispose() { ... }                    // Clean up WebGL resources
}
```

---

### Module 4: Acoustic Solver Interface (`src/solver/`)

**Purpose:** Bridge between the browser app and BEM acoustic simulation. Phase 4 module.

**Architecture decision: How to run BEM from the browser**

Three options evaluated:

| Approach | Pros | Cons |
|----------|------|------|
| **A. Python backend (FastAPI + bempp-cl)** | Full bempp API, GPU acceleration, proven | Requires server, not standalone |
| **B. WASM BEM solver** | Fully client-side, no server | No mature WASM BEM library exists |
| **C. Pyodide + bempp in browser** | No server, Python in browser | bempp-cl depends on OpenCL, won't work |

**Recommendation: Approach A (Python backend) with a local-first design.**

The solver runs as a local Python process (FastAPI on localhost). The browser communicates via HTTP. For users without Python, the app degrades gracefully -- geometry and export still work, solver features show "Solver not connected."

**Files:**
```
src/solver/
  index.js          -- Public API
  client.js         -- HTTP client for solver backend
  meshExport.js     -- Convert Three.js geometry to STL/Gmsh for solver
  resultParser.js   -- Parse solver response into visualization data
  status.js         -- Connection status management

server/
  app.py            -- FastAPI application
  solver.py         -- bempp-cl Helmholtz BEM solver
  mesh_io.py        -- Mesh import/export (meshio)
  requirements.txt  -- Python dependencies
```

**Solver API (REST):**
```
POST /api/solve
  Body: { mesh: <base64 STL>, frequency_range: [f1, f2], num_frequencies: N, sim_type: 1|2 }
  Response: { job_id: "..." }

GET /api/status/{job_id}
  Response: { status: "running"|"complete"|"error", progress: 0.0-1.0 }

GET /api/results/{job_id}
  Response: {
    directivity: { horizontal: [...], vertical: [...], diagonal: [...] },
    impedance: { frequencies: [...], real: [...], imaginary: [...] },
    spl_on_axis: { frequencies: [...], spl: [...] },
    di: { frequencies: [...], di: [...] }
  }
```

**BEM Solver Integration - Phase 4 Update**

Phase 4 of the ATH Horn system now includes a complete BEM solver integration. The implementation follows the architecture pattern established in Phase 2, with:

1. **Modular BEM Interface**: The `src/solver/` module provides a clean API for BEM simulations with:
   - HTTP client (`client.js`) for communication with Python backend
   - Mesh conversion utilities (`meshExport.js`) that prepare geometries for BEM simulation with proper surface tags
   - Result parsing (`resultParser.js`) that transforms raw solver output into structured acoustic data
   - Connection status management (`status.js`) for UI feedback

2. **BEM-ready Export Pipeline**: The system now supports:
   - Gmsh .msh export with proper surface tags for boundary conditions
   - Mesh validation to ensure compatibility with BEM solvers
   - Variable mesh density control (as requested in Phase 3)

3. **Acoustic Output Generation**: The system produces:
   - On-axis frequency response
   - Polar/directivity data (horizontal, vertical, diagonal)
   - Phase response
   - Optional impedance or throat loading metrics

4. **Integration with Existing Workflow**: The BEM solver pipeline:
   - Geometry → mesh conversion → BEM solve → acoustic results
   - Supports single and multi-frequency sweeps
   - Ensures numerical stability with reasonable defaults

5. **Validation & Reference Comparison**: The system:
   - Validates simulation results against known ATH/ABEC reference simulations when available
   - Documents assumptions and limitations of the current solver setup

**Implementation Details:**

The BEM solver integration uses bempp-cl as the primary backend, which is compatible with Gmsh .msh files. The system implements proper boundary conditions:
- **Rigid (sound-hard) boundary conditions** on horn walls (Neumann BC)
- **Acoustic source** at the throat (pressure or velocity source) 
- **Open radiation condition** at the mouth / exterior domain (Robin BC)

The system maintains backward compatibility with existing functionality while providing a stable foundation for automated optimization in Phase 5.

---

### Module 5: Results Viewer (`src/results/`)

**Purpose:** Visualize acoustic simulation results. Charts, polar plots, sonograms, near-field maps.

**Files:**
```
src/results/
  index.js          -- Public API
  polarPlot.js      -- SVG polar radiation pattern (horizontal/vertical/diagonal)
  frequencyPlot.js  -- SPL vs frequency (on-axis, off-axis)
  sonogram.js       -- Directivity map (frequency vs angle, color = SPL)
  impedancePlot.js  -- Throat impedance (R + jX vs frequency)
  diPlot.js         -- Directivity Index vs frequency
  chartUtils.js     -- Shared axis, scale, color utilities
```

**Rendering approach:** SVG for 2D plots (no heavy charting library needed). Canvas for sonogram heatmaps. All plots support:
- Dark/light mode (CSS variables)
- Export as PNG/SVG
- Tooltip with values on hover
- Frequency cursor sync across all plots

---

### Module 6: Export Engine (`src/export/`)

**Purpose:** Generate all output formats from current geometry and parameters.

**Files:**
```
src/export/
  index.js          -- Public API
  stl.js            -- Binary/ASCII STL export (current Three.js STLExporter)
  athConfig.js      -- ATH config file (current exportATHConfig, improved)
  gmsh.js           -- Gmsh .geo file generation
  abecProject.js    -- ABEC project file generation
  csvProfile.js     -- Cross-section profile CSV export
  objExport.js      -- Wavefront OBJ export (future)
```

**ATH Config export produces configs that round-trip perfectly:**
```javascript
// Import a config -> change params -> export -> identical to hand-edited ATH config
const config = ATHConfigParser.parse(fileContent);
// ... modify config.params ...
const exported = exportATHConfig(config);
// `exported` is valid for ath.exe
```

**Gmsh .geo export:**
```javascript
/**
 * Generate a Gmsh geometry file from horn profile.
 * Includes: Point definitions, Line/Spline curves, Surface loops,
 * Physical surfaces (throat, horn, mouth, exterior), Mesh size fields.
 * Compatible with Gmsh 4.x and ABEC's mesh import.
 */
export function exportGmsh(hornProfile, params) { ... }
```

**ABEC Project export:**
```javascript
/**
 * Generate a complete ABEC project directory structure:
 *   Project.abec     -- Main project file
 *   BEM_Script.txt   -- Geometry/mesh/subdomain definitions
 *   Obs_H.txt        -- Horizontal polar observation
 *   Obs_V.txt        -- Vertical polar observation
 *   horn.msh         -- Gmsh mesh file
 */
export function exportABECProject(hornProfile, params) { ... }
```

---

### Module 7: UI Panel (`src/ui/`)

**Purpose:** Parameter controls, model type selection, file operations. Currently in `index.html` + scattered through `main.js`.

**Files:**
```
src/ui/
  index.js          -- UI initialization, event wiring
  paramPanel.js     -- Dynamic parameter panel generation from schema
  modelSelect.js    -- Model type switching (R-OSSE / OSSE / OS-GOS)
  fileOps.js        -- Load/save config, file picker
  exportPanel.js    -- Export buttons and settings
  displayPanel.js   -- Display mode, mesh settings
  solverPanel.js    -- Solver connection, run simulation button
  resultsPanel.js   -- Embedded results viewer controls
```

**Schema-driven UI generation:**
```javascript
// Instead of hand-coding each input row, generate from schema:
function buildParamPanel(modelType) {
  const schema = PARAM_SCHEMA[modelType];
  const container = document.getElementById('params-container');
  container.innerHTML = '';

  for (const [key, def] of Object.entries(schema)) {
    if (def.type === 'number') {
      container.appendChild(createSliderRow(key, def));
    } else if (def.type === 'expression') {
      container.appendChild(createExpressionRow(key, def));
    }
    // ... select, checkbox, etc.
  }
}
```

This eliminates the current pattern of hand-maintained HTML + JS ID references for every parameter. Adding a new parameter requires only adding it to `schema.js`.

---

## 4. Incremental Roadmap

### Phase 0: Stabilization & Module Extraction (Current -> Foundation)

**Goal:** Split monolithic `main.js` into modules without changing any behavior.

**Steps:**
1. Create `src/` directory structure
2. Extract `parseExpression()` -> `src/geometry/expression.js`
3. Extract `calculateROSSE()` -> `src/geometry/rosse.js`
4. Extract `calculateOSSE()` -> `src/geometry/osse.js`
5. Extract mesh building functions -> `src/geometry/meshBuilder.js`
6. Extract morphing logic -> `src/geometry/morphing.js`
7. Extract enclosure geometry -> `src/geometry/enclosure.js`
8. Extract rollback geometry -> `src/geometry/rollback.js`
9. Extract profile generation -> `src/geometry/profile.js`
10. Extract `ATHConfigParser` -> `src/config/parser.js`
11. Extract config serializer -> `src/config/serializer.js`
12. Extract Three.js scene setup -> `src/viewer/scene.js`
13. Extract camera/controls -> `src/viewer/camera.js`
14. Extract materials -> `src/viewer/materials.js`
15. Extract STL export -> `src/export/stl.js`
16. Create `src/events.js` (EventBus)
17. Create new `main.js` that imports all modules and wires them together
18. Verify: app behavior is identical to before

**Validation:** Visual diff -- same horn renders identically. Config round-trip: load example config -> export -> diff shows no changes.

**No build tools needed yet.** ES module imports work natively in modern browsers. The import map in `index.html` already handles Three.js.

---

### Phase 1: Config Robustness & Schema System

**Goal:** Bulletproof config handling. Schema-driven validation. Full ATH format compatibility.

**Steps:**
1. Define complete `PARAM_SCHEMA` for R-OSSE, OSSE, OS-GOS
2. Add parameter validation with typed errors
3. Add defaults system (load defaults when switching model type)
4. Add config round-trip tests (parse -> serialize -> parse -> assert equal)
5. Test against all example configs in `example scripts/`
6. Add undo/redo system (state stack)
7. Add save/load to localStorage (auto-save on change)

**Validation:** All example configs parse and re-export correctly. Schema rejects out-of-range values with helpful messages.

---

### Phase 2: Enhanced Geometry & OS-GOS

**Goal:** Complete geometry feature set matching ATH reference.

**Steps:**
1. Implement OS-GOS horn model (`src/geometry/osgos.js`)
2. Improve morphing: implement `Morph.TargetWidth`, `Morph.TargetHeight`, `Morph.Rate` curves
3. Add `Mesh.SubdomainSlices` support
4. Add `Mesh.ThroatResolution`, `Mesh.MouthResolution` for variable mesh density
5. Implement `Source.Contours` visualization (show source cap in 3D view)
6. Add cross-section profile view (2D SVG showing horn profile at any angle p)
7. Add dimension annotations in 3D view (throat diameter, mouth dimensions, length)

**Validation:** Compare generated profiles against ATH reference output CSVs.

---

### Phase 3: Export Suite

**Goal:** Generate all output formats needed for the ATH/ABEC workflow.

**Steps:**
1. Implement Gmsh .geo export with proper Physical Surface tags
2. Implement ABEC project export (BEM script, observation scripts, mesh)
3. Implement CSV profile export (matching ATH's `_profiles_throat.csv` format)
4. Add OBJ export for CAD import
5. Add batch export (all formats at once, as .zip)
6. Research BEM tools and their requirements for simulation readiness

**Validation:** Generated .geo files open correctly in Gmsh. Generated ABEC projects load in ABEC3. CSV profiles match ATH reference output.

### BEM Simulation Research & Prep

**Research Goals:**
1. Research existing BEM tools and scripts suitable for horn acoustics:
   - https://github.com/bempp/bempp-cl  
   - https://github.com/mscroggs/bempp-acoustic-tutorials  
   - https://github.com/kurtjcu/PHM-PythonHornModelling  
   - https://github.com/Any2HRTF/Mesh2HRTF  
   - https://github.com/TomMunoz3772/electroacPy  

2. Determine for each solver:
   - Required mesh file formats (STL, OBJ, MSH, VTK, etc.)  
   - Mesh definition guidelines (triangles, resolution, manifold conditions)  
   - Boundary condition setup (acoustic source at throat, solid horn walls, etc.)  
   - Solver-specific preprocessing or metadata needs

3. Update Phase 3 architecture documentation with:
   - Summary of findings for each BEM tool
   - File format compatibility matrix  
   - Boundary condition requirements
   - Next steps for integrating BEM-ready export based on findings

**Validation:** Research documentation provides clear guidance for implementing BEM-ready exports.

---

### Phase 4: Acoustic Solver Integration

**Goal:** Run BEM simulations from the browser.

**Steps:**
1. Create Python backend (`server/app.py`) with FastAPI
2. Implement bempp-cl Helmholtz solver (`server/solver.py`)
3. Implement mesh conversion pipeline (Three.js geometry -> STL -> bempp grid)
4. Add solver status polling in browser
5. Implement results viewer: polar plots, SPL curves, impedance
6. Add sonogram (directivity map) visualization
7. Add solver connection indicator in UI
8. Implement frequency-dependent mesh refinement (6 elements per wavelength)

**Validation:** Compare BEM results against known ABEC results for the same horn geometry. Directivity patterns should match within 1-2 dB.

---

### Phase 5: Optimization & Batch Processing

**Goal:** Automated parameter exploration and design ranking.

**Steps:**
1. Implement parameter sweep generator (linspace over selected params)
2. Implement batch solve queue
3. Implement acoustic quality scoring (port `rate_radimp.py` logic to JS)
4. Add optimization dashboard (sortable table of designs with scores)
5. Add parameter sensitivity visualization (which params matter most)
6. Add design comparison view (overlay two horn profiles)

**Validation:** Batch sweep of N designs completes without memory leaks. Scoring matches Python `rate_radimp.py` output.

---

## 5. Tech Stack

### Frontend (Browser)

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | JavaScript (ES2022+) | Native browser, no build step needed initially |
| 3D Engine | Three.js 0.160+ | Already in use, excellent for real-time 3D |
| Module System | ES Modules (import/export) | Native browser support, import maps for CDN |
| UI Framework | Vanilla DOM | Current approach works well for panel UI, no framework overhead |
| 2D Charts | SVG (hand-rolled) | Lightweight, dark mode via CSS vars, no dependency |
| Sonogram | Canvas 2D | Pixel-level control for heatmaps |
| State Management | EventBus + serializable state | Simple, debuggable, undo/redo friendly |
| Testing | Vitest (when build step added) | Fast, ES module native, compatible with Node |

### Backend (Local Python, Phase 4+)

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Framework | FastAPI | Async, fast, automatic API docs |
| BEM Solver | bempp-cl | Open source, Helmholtz support, GPU acceleration |
| Mesh I/O | meshio | Reads/writes STL, Gmsh, VTK, etc. |
| Math | NumPy, SciPy | Standard scientific Python |
| Task Queue | asyncio (built-in) | Simple for single-user local use |

### Build & Dev Tools (introduced gradually)

| Phase | Tool | Purpose |
|-------|------|---------|
| 0-1 | None | ES modules work without bundling |
| 2+ | Vite | Dev server with HMR, production bundling |
| 2+ | Vitest | Unit testing |
| 3+ | GitHub Actions | CI: lint, test, build |

**No build tools in Phase 0-1.** The app works by opening `index.html` with a local HTTP server. Build tools are introduced only when the module count makes unbundled loading slow.

---

## 6. Repository Structure

```
ath-horn/
├── index.html                    -- Entry point (Phase 0: unchanged)
├── style.css                     -- Global styles
├── main.js                       -- App bootstrap (Phase 0: becomes thin orchestrator)
│
├── src/
│   ├── events.js                 -- EventBus
│   ├── state.js                  -- Serializable application state
│   │
│   ├── geometry/
│   │   ├── index.js              -- Public API
│   │   ├── rosse.js              -- R-OSSE calculation
│   │   ├── osse.js               -- OSSE calculation
│   │   ├── osgos.js              -- OS-GOS calculation (Phase 2)
│   │   ├── profile.js            -- Profile generation
│   │   ├── morphing.js           -- Shape morphing
│   │   ├── enclosure.js          -- Enclosure box
│   │   ├── rollback.js           -- Mouth rollback
│   │   ├── meshBuilder.js        -- Three.js BufferGeometry
│   │   └── expression.js         -- Math expression parser
│   │
│   ├── config/
│   │   ├── index.js
│   │   ├── parser.js             -- ATH config parser
│   │   ├── serializer.js         -- Config export
│   │   ├── validator.js          -- Parameter validation
│   │   ├── defaults.js           -- Default parameter sets
│   │   └── schema.js             -- Parameter schema definitions
│   │
│   ├── viewer/
│   │   ├── index.js
│   │   ├── scene.js              -- Three.js scene setup
│   │   ├── camera.js             -- Camera management
│   │   ├── controls.js           -- OrbitControls
│   │   ├── materials.js          -- Display mode materials
│   │   ├── hornRenderer.js       -- Horn mesh in scene
│   │   └── annotations.js        -- Dimension lines (Phase 2)
│   │
│   ├── export/
│   │   ├── index.js
│   │   ├── stl.js                -- STL export
│   │   ├── athConfig.js          -- ATH config export
│   │   ├── gmsh.js               -- Gmsh .geo export (Phase 3)
│   │   ├── abecProject.js        -- ABEC project export (Phase 3)
│   │   ├── csvProfile.js         -- CSV profile export (Phase 3)
│   │   └── objExport.js          -- OBJ export (Phase 3)
│   │
│   ├── solver/                   -- Phase 4
│   │   ├── index.js
│   │   ├── client.js             -- HTTP client for Python backend
│   │   ├── meshExport.js         -- Geometry -> solver mesh format
│   │   ├── resultParser.js       -- Parse solver response
│   │   └── status.js             -- Connection management
│   │
│   ├── results/                  -- Phase 4
│   │   ├── index.js
│   │   ├── polarPlot.js          -- Polar radiation patterns
│   │   ├── frequencyPlot.js      -- SPL vs frequency
│   │   ├── sonogram.js           -- Directivity map
│   │   ├── impedancePlot.js      -- Impedance curves
│   │   ├── diPlot.js             -- Directivity Index
│   │   └── chartUtils.js         -- Shared utilities
│   │
│   └── ui/
│       ├── index.js              -- UI initialization
│       ├── paramPanel.js         -- Schema-driven parameter UI
│       ├── modelSelect.js        -- Model type switching
│       ├── fileOps.js            -- Load/save config
│       ├── exportPanel.js        -- Export controls
│       ├── displayPanel.js       -- Display mode selection
│       ├── solverPanel.js        -- Solver controls (Phase 4)
│       └── resultsPanel.js       -- Results viewer (Phase 4)
│
├── server/                       -- Phase 4
│   ├── app.py                    -- FastAPI application
│   ├── solver.py                 -- bempp-cl BEM solver
│   ├── mesh_io.py                -- Mesh conversion
│   └── requirements.txt
│
├── test/
│   ├── geometry/
│   │   ├── rosse.test.js
│   │   ├── osse.test.js
│   │   └── expression.test.js
│   ├── config/
│   │   ├── parser.test.js
│   │   └── roundtrip.test.js
│   └── fixtures/
│       ├── tritonia2.txt         -- Example OSSE config
│       └── asromain.txt          -- Example R-OSSE config
│
├── docs/
│   └── ARCHITECTURE.md           -- This document
│
├── example scripts/              -- Existing ATH example projects
├── other scripts/                -- Existing Python automation
├── reference/                    -- ATH binaries and user guide
│
├── .gitignore
├── package.json                  -- Added in Phase 2 (for Vite/Vitest)
└── vite.config.js                -- Added in Phase 2
```

---

## 7. AI Collaboration Strategy

### File Size Discipline

Every source file should be **under 300 lines**. When a file approaches this limit, split it. This ensures:
- AI assistants can read and understand any single file in one pass
- Changes are localized and reviewable
- Merge conflicts are rare

### Module Contract Pattern

Each module has an `index.js` that defines the public API. Internal files are implementation details. AI tasks should reference the public API:

```
"Add a new export format" ->
  1. Read src/export/index.js to understand the API pattern
  2. Create src/export/newFormat.js following the same pattern
  3. Add export to index.js
  4. Wire up in src/ui/exportPanel.js
```

### JSDoc for AI Context

All public functions have JSDoc with `@param`, `@returns`, and a one-line description. This gives AI assistants enough context without reading the implementation:

```javascript
/**
 * Calculate R-OSSE horn profile point at normalized position t and angle p.
 * @param {number} t - Normalized position along horn (0=throat, 1=mouth)
 * @param {number} p - Azimuthal angle in radians (0=horizontal, PI/2=vertical)
 * @param {ROSSEParams} params - Horn parameters
 * @returns {{ x: number, y: number }} Axial position and radius at (t, p)
 */
export function calculateROSSE(t, p, params) { ... }
```

### Task Sizing for AI

Ideal AI task = "modify 1-3 files, touch 20-100 lines." Structure tasks as:

| Good AI Task | Bad AI Task |
|-------------|-------------|
| "Add OS-GOS model to geometry module" | "Implement the whole solver" |
| "Fix config parser for nested blocks" | "Refactor everything to modules" |
| "Add polar plot SVG component" | "Build the results dashboard" |
| "Wire solver status to UI panel" | "Integrate bempp-cl" |

### Testing Strategy

Each module has co-located tests. Before any change, the AI should:
1. Read the existing tests
2. Run them to confirm they pass
3. Add tests for the new behavior
4. Implement the change
5. Run tests again

---

## 8. Validation & Trust

### Phase 0 Validation

| Check | Method |
|-------|--------|
| Visual identity | Screenshot before/after module extraction |
| Config round-trip | Parse all example configs, export, diff against originals |
| Mesh identity | Compare vertex/triangle counts and positions |
| No regressions | All UI interactions work: sliders, display modes, export |

### Phase 1-2 Validation

| Check | Method |
|-------|--------|
| Schema covers all ATH params | Compare schema against ATH User Guide parameter list |
| Validation catches bad input | Unit tests with edge cases |
| OS-GOS matches reference | Compare profiles against ATH-generated CSV files |
| Profile accuracy | Plot profiles and overlay with ATH reference output |

### Phase 3 Validation

| Check | Method |
|-------|--------|
| Gmsh .geo opens in Gmsh | Automated: `gmsh -check output.geo` |
| ABEC project loads | Manual: open in ABEC3, verify mesh and settings |
| CSV profiles match | Diff against ATH reference CSV output |

### Phase 4 Validation

| Check | Method |
|-------|--------|
| BEM results vs ABEC | Compare directivity plots for same horn geometry |
| Mesh quality | Element aspect ratio < 5:1, no degenerate triangles |
| Convergence | Results stable when doubling mesh density |
| Known geometry | Validate against analytical solutions (e.g., flanged pipe) |

### Acoustic Quality Metrics (from rate_radimp.py)

The existing Python rating system scores designs on:
1. **Slope Score (50%):** Flatness of real impedance component in 5-20 kHz range
2. **Deviation Score (40%):** How closely data follows the trend line
3. **Match Score (40%):** Response conformity down to 5 kHz

These will be ported to JavaScript in Phase 5 for in-browser scoring.

---

## 9. Output Requirements Summary

### What Each Phase Delivers

| Phase | Deliverable | User Can Do |
|-------|------------|-------------|
| **0** | Modular codebase, same functionality | Edit individual modules, contribute to specific features |
| **1** | Schema-driven config, validation, undo | Reliable config handling, no more silent errors |
| **2** | OS-GOS model, improved morphing, profile view | Design all ATH horn types, see cross-sections |
| **3** | Full export suite (Gmsh, ABEC, CSV, OBJ) | Feed designs directly into simulation tools |
| **4** | BEM solver, results viewer | Simulate horns without leaving the browser |
| **5** | Optimization dashboard, batch sweeps | Automatically explore design space, find optimal horns |
| **6** | UX workflows, presets, validation, server hardening | Trustworthy design tool for production use |

### Key Technical Constraints

- **No framework lock-in.** Vanilla JS + ES modules. A framework (React, Svelte) can be introduced later if needed, but the math and solver modules must remain framework-agnostic.
- **No required build step through Phase 1.** The app must work by pointing a browser at a local HTTP server serving static files.
- **Graceful degradation.** Without the Python backend, the app is still a fully functional horn designer and exporter.
- **Mobile-last.** This is a desktop engineering tool. Mobile support is not a priority.

### Coordinate System (Reference)

```
Y-axis: Axial direction (throat at Y=0, mouth at Y=L or Y=calculated)
X-axis: Horizontal (r * cos(p))
Z-axis: Vertical (r * sin(p))

Enclosure: extends in -Y direction from mouth (behind baffle)
Rollback: toroidal fold at mouth rim, curls in -Y direction

3D vertex mapping:
  vx = radius * cos(azimuthalAngle)
  vy = axialPosition
  vz = radius * sin(azimuthalAngle)
```

## 10. Phase 6 - UX, Presets, Validation & Production Readiness

### Overview

Phase 6 transforms the ATH Horn Design Platform from a powerful engine into a usable, trustworthy design tool by implementing:

1. **User-facing workflows** - Clear end-to-end design processes
2. **Presets and reproducibility** - Fast starting points and configuration management  
3. **Validation against known references** - Building trust in results
4. **Production hardening** - Server reliability for Unraid environments

### New Modules

#### Workflow Module (`src/workflow/`)
- Implements canonical workflow state machine
- Tracks current stage, inputs, outputs, and errors/warnings
- Ensures every step produces inspectable intermediate artifacts:
  - Geometry
  - Mesh  
  - Boundary tags
  - Solver config
  - Results

#### Presets Module (`src/presets/`)
- Enables fast starting points and reproducibility
- Includes ATH-style presets and known-good horn archetypes
- Supports import/export as JSON with full round-trip safety
- Presets include geometry parameters, mesh parameters, BEM settings, and default optimization objectives

#### Validation Module (`src/validation/`)
- Builds trust in solver and optimization results
- Supports ATH/ABEC reference comparisons and published horn responses
- Implements difference metrics (SPL error, phase error, DI error)
- Provides pass/fail thresholds and structured reporting

#### Server Hardening
- Job persistence for resuming interrupted simulations/optimizations
- Resource guards (max mesh size, max concurrent BEM jobs)
- Memory estimation warnings
- Structured logging for geometry, meshing, solver, and optimization

### Documentation Updates

#### New Documentation Files
- `DEPLOYMENT_UNRAID.md` - Deployment guide for Unraid environments with job persistence and resource guards
- `VALIDATION.md` - Detailed validation methodology and reference comparison strategy  
- `PRESETS.md` - Preset system documentation with examples and import/export functionality

### Integration Points

1. **Workflow Integration** - All modules now integrate into a canonical workflow state machine
2. **Preset Integration** - Users can load presets to start new designs or reproduce previous work
3. **Validation Integration** - Results are automatically validated against reference data  
4. **Server Hardening** - System ensures stable operation in Unraid environments with resource limits and structured logging

### Expected Outcome

After Phase 6 implementation, the system will be:
- **Usable**: Clear workflows and user interfaces for all design steps
- **Trustworthy**: Validation against known references builds confidence in results  
- **Reproducible**: Presets enable fast starting points and exact reproduction
- **Production-Ready**: Server hardening ensures reliable operation on Unraid systems

### Validation & Trust

| Check | Method |
|-------|--------|
| Workflow integrity | End-to-end workflow testing with all stages |
| Preset round-trip | Import/export cycles preserve all configuration data |
| Validation accuracy | Compare results against ATH/ABEC reference data |
| Server stability | Long-running tests on Unraid-like environments |
| Resource management | Memory usage and job limits enforced properly |

### Future Enhancements

1. **Advanced Workflow** - Multi-stage workflows with branching and conditional steps
2. **Enhanced Presets** - Cloud sync, preset categories, and user customization  
3. **Expanded Validation** - Dynamic threshold adjustment and additional acoustic metrics
4. **Production Monitoring** - Health checks, performance metrics, and alerting systems

### Technical Constraints for Phase 6

- **No new horn math models** - Focus on usability and trust, not new acoustic physics
- **No new BEM formulations** - Maintain existing solver integration  
- **No performance over-optimization** - Prioritize clarity and reliability
- **Backward compatibility** - All existing functionality must continue to work

### Coordinate System (Reference)

```
Y-axis: Axial direction (throat at Y=0, mouth at Y=L or Y=calculated)
X-axis: Horizontal (r * cos(p))
Z-axis: Vertical (r * sin(p))

Enclosure: extends in -Y direction from mouth (behind baffle)
Rollback: toroidal fold at mouth rim, curls in -Y direction

3D vertex mapping:
  vx = radius * cos(azimuthalAngle)
  vy = axialPosition
  vz = radius * sin(azimuthalAngle)
```

## Frontend Element Reference

This section documents the actual frontend UI elements used in the application, which were identified during E2E testing and differ slightly from initial assumptions:

| Element ID | Description | Usage |
|------------|-------------|-------|
| `#render-btn` | Button to update the horn model geometry | Triggered by user interaction or E2E tests |
| `#export-btn` | Button to export STL mesh | Triggered by user interaction or E2E tests |
| `#export-config-btn` | Button to export ATH configuration files | Triggered by user interaction |
| `#export-csv-btn` | Button to export CSV profile data | Triggered by user interaction |
| `#export-geo-btn` | Button to export Gmsh .geo mesh files | Triggered by user interaction or E2E tests |
| `#ui-panel` | Main UI panel container | Contains all parameter and control elements |
| `#canvas-container` | Container for the 3D visualization canvas | Display area for rendered horn geometry |
| `#export-prefix` | Input field for export filename prefix | Used in export operations |
| `#export-counter` | Input field for export counter | Used in export operations |
| `#load-config-btn` | Button to load configuration files | Triggered by user interaction |
| `#config-upload` | Hidden file input for config uploads | Used in configuration loading |
| `#display-mode` | Select dropdown for display modes | Changes visualization mode |
| `#param-container` | Container for parameter controls | Dynamically populated with UI elements |
| `#stats` | Display area for geometry statistics | Shows vertex and triangle counts |
| `#zoom-in`, `#zoom-out` | Zoom controls for 3D view | Controls camera zoom |
| `#camera-toggle` | Toggle between perspective and orthographic views | Switches camera projection |
| `#focus-horn` | Button to center view on horn | Resets camera position |
| `#zoom-reset` | Reset button for camera view | Returns to default zoom and view |

## 11. Phase 7 - AI-Assisted Design & Learning Layer

### Overview

Phase 7 extends the ATH Horn Design Platform beyond deterministic optimization into AI-assisted design guidance.

### New Modules

#### AI Knowledge Module (`src/ai/knowledge/`)
- Captures and structures knowledge generated by the system for learning purposes
- Implements storage of geometry parameters, mesh parameters, solver settings, objective scores, and derived metrics
- Ensures fully reproducible, versioned schema with backward compatibility

#### AI Surrogate Modeling Module (`src/ai/surrogate/`)
- Reduces reliance on expensive BEM runs through surrogate models
- Supports simple regression models (linear, polynomial) and Gaussian Process for small datasets
- Provides approximate predictions of on-axis response, DI trend, and ripple likelihood with uncertainty estimates

#### AI Optimization Guidance Module (`src/ai/optimization/`)
- Guides optimization rather than brute-forcing it
- Implements Bayesian Optimization and CMA-ES (AI-guided initialization) 
- Enables parameter importance ranking, adaptive bounds tightening, and early termination suggestions

#### AI Insights Module (`src/ai/insights/`)
- Generates human-readable design guidance and explanations
- Provides textual explanations like "Mouth flare dominates DI stability above 3 kHz"
- Offers sensitivity summaries and trade-off explanations

#### AI-Guided Preset Evolution (`src/ai/presets/`)
- Allows presets to improve over time through AI analysis
- Tracks preset performance history and suggests refinements

### Documentation Updates

#### New Documentation Files
- `AI_GUIDANCE.md` - Detailed documentation of AI-assisted design features and workflows
- `DATA_MODEL.md` - Updated data model schema for AI knowledge capture

### Integration Points

1. **Knowledge Capture** - All system outputs are captured and stored for learning
2. **Surrogate Modeling** - Reduces BEM simulation costs by predicting outcomes
3. **Optimization Guidance** - AI enhances existing optimization algorithms
4. **Insight Generation** - Provides human-readable explanations of design outcomes
5. **Preset Evolution** - AI improves preset quality over time

### Expected Outcome

After Phase 7 implementation, the system will be:
- **Learning**: System learns from previous simulations and optimizations
- **Guided**: AI suggests better parameter regions to explore
- **Accelerated**: Convergence is faster through surrogate models and AI guidance  
- **Explainable**: Human-readable design insights explain why designs are good or bad

### AI Design Principles

1. **Assist, Don't Replace** - AI must assist engineering judgment, not replace it
2. **Traceability** - All AI suggestions must be traceable to data and logic
3. **Deterministic Outputs** - AI-generated insights must be deterministic and reproducible
4. **No Black Boxes** - All models must have explanations for their outputs

### Technical Constraints for Phase 7

- **No physics replacement** - AI cannot replace physics with machine learning models
- **No opaque models** - All AI decisions must have explanations and traceability
- **No internet connectivity requirement** - AI operates entirely locally
- **No silent overwrites** - AI never silently overwrites presets or configurations

### Coordinate System (Reference)

```
Y-axis: Axial direction (throat at Y=0, mouth at Y=L or Y=calculated)
X-axis: Horizontal (r * cos(p))
Z-axis: Vertical (r * sin(p))

Enclosure: extends in -Y direction from mouth (behind baffle)
Rollback: toroidal fold at mouth rim, curls in -Y direction

3D vertex mapping:
  vx = radius * cos(azimuthalAngle)
  vy = axialPosition
  vz = radius * sin(azimuthalAngle)
```


## Event Bus Contract (Phase 0)

The event bus is a minimal synchronous pub/sub system with:
- emit(eventName, payload)
- on(eventName, handler)
- off(eventName, handler)

No external libraries.

### Phase 5: Optimization & Batch Processing

**Goal:** Automated parameter exploration and design ranking.

**Steps:**
1. Implement parameter sweep generator (linspace over selected params)
2. Implement batch solve queue
3. Implement acoustic quality scoring (port `rate_radimp.py` logic to JS)
4. Add optimization dashboard (sortable table of designs with scores)
5. Add parameter sensitivity visualization (which params matter most)
6. Add design comparison view (overlay two horn profiles)

**Architecture:**
```
src/optimization/
  index.js          -- Public API
  parameterSpace.js -- Parameter bounds and definitions  
  objectiveFunctions.js -- Acoustic quality scoring functions
  engine.js         -- Optimization loop and algorithms
  results.js        -- Result storage and management
  api.js            -- Clean API for external integration
```

**Key Features:**
- **Parameter Space Definition:** Extracts parameter bounds from `src/config/schema.js` and supports constraints and step sizes
- **Objective Functions:** Implements reusable acoustic objective functions:
  - Smooth on-axis frequency response (minimize ripple)
  - Targeted directivity control (beamwidth vs frequency) 
  - Minimized diffraction/ripple in SPL response
  - Phase smoothness or group delay consistency
  - Optional throat impedance matching (with weighted multi-objective scoring)
- **Optimization Engine:** Implements deterministic methods:
  - Grid search (for small parameter spaces)
  - Random sampling (for larger spaces) 
  - Coordinate descent (for single parameter optimization)
  - Extensible design for future algorithms (GA, Bayesian, CMA-ES)
- **Result Management:** Stores optimization results with:
  - Parameter sets (all parameter combinations)
  - Acoustic metrics (frequency response, directivity, phase, impedance)
  - Objective scores (combined and individual)
  - Reproducible runs with seeded randomization where applicable
- **API Integration:** Exposes clean API: `optimizeHorn(config, objectives, bounds, options)`

**Integration Points:**
- Existing geometry modules (OSSE, R-OSSE, OS-GOS)
- BEM solver interface (`src/solver/`)
- Configuration management system
- Export pipeline (for generating Gmsh meshes)
- Results viewer (for displaying optimization outcomes)

**Expected Outcome:**
After implementation, the system will be able to:
- Automatically explore horn design variants
- Evaluate designs using BEM-derived acoustic metrics  
- Rank and compare horn designs objectively
- Serve as a foundation for future UI-driven or AI-assisted design workflows

**Validation:** Batch sweep of N designs completes without memory leaks. Scoring matches Python `rate_radimp.py` output.
