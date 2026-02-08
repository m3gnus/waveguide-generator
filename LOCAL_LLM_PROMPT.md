# Prompt: Scaffold the ATH Horn Rebuild

You are helping rebuild a web-based mathematical waveguide generator. The full rebuild plan is in `REBUILD_INSTRUCTIONS.md` in this project root. Read it first.

Your job right now is the **scaffolding and mechanical tasks** — creating the new file structure, copying existing code into the new layout, and wiring things together. You are NOT doing the complex math verification or algorithm design tasks (those are marked `[CLAUDE]` in the instructions). You ARE doing everything marked `[LOCAL LLM]`.

## Context

The existing project lives in this directory. It works, but has ~80+ source files. We are rebuilding into ~8 files with no webpack, no OpenCascade, and no custom event system. Three.js and Chart.js come from CDN.

## What to do, in order

### Step 1: Create the new file structure

Create a new directory called `rebuild/` inside the project root. All new files go there. Do not modify the existing code.

```
rebuild/
  index.html
  style.css
  src/
    main.js
    state.js
    geometry.js
    mesh.js
    viewer.js
    simulation.js
  server/
    app.py
    solver.py
    requirements.txt
```

### Step 2: `index.html`

Create `rebuild/index.html` based on the existing `index.html` layout. Keep the same UI structure (tab navigation with Geometry and Simulation tabs, viewer container, stats bar, parameter container, export buttons). Changes from the original:

- Remove the webpack script. Add an import map for Three.js and include Chart.js + JSZip from CDN:

```html
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
  }
}
</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/jszip@3/dist/jszip.min.js"></script>
<script type="module" src="src/main.js"></script>
```

- Keep all the existing buttons: Load Config, Export Config, Export CSV, Export STL, Export MSH, Export ABEC Project
- Add a new button: **Export GEO** (for Gmsh .geo export — this is the primary BEM export path)
- Remove the Export STEP button (OpenCascade is dropped)
- Keep display mode selector, live update checkbox, export prefix/counter, folder selection
- Keep the simulation tab structure (connection status, run/stop buttons, chart containers)

### Step 3: `style.css`

Copy the existing `style.css` into `rebuild/style.css` as-is. No changes needed.

### Step 4: `state.js` — State management + schema + defaults

Create `rebuild/src/state.js`. This file combines three things from the old codebase:

**A) The parameter schema** — copy the entire `PARAM_SCHEMA` object from `src/config/schema.js`. This defines all parameter groups: `R-OSSE`, `OSSE`, `GEOMETRY`, `MORPH`, `MESH`, `ENCLOSURE`, `SOURCE`, `ABEC`.

**B) The defaults function** — copy `getDefaults()` from `src/config/defaults.js`:
```js
export function getDefaults(modelType) {
    const defaults = {};
    const core = PARAM_SCHEMA[modelType];
    if (core) {
        for (const [key, def] of Object.entries(core)) {
            defaults[key] = def.default;
        }
    }
    const sharedGroups = ['GEOMETRY', 'MORPH', 'MESH', 'SOURCE', 'ABEC', 'ENCLOSURE'];
    for (const group of sharedGroups) {
        const groupSchema = PARAM_SCHEMA[group];
        if (groupSchema) {
            for (const [key, def] of Object.entries(groupSchema)) {
                defaults[key] = def.default;
            }
        }
    }
    return defaults;
}
```

**C) State class using native EventTarget** (replaces the old custom EventBus + state.js):

```js
class AppState extends EventTarget {
    constructor() {
        super();
        this.type = 'R-OSSE';
        this.params = {};
        this.history = [];
        this.historyIndex = -1;
        this.maxHistory = 50;
        this._load();
    }

    update(patch, options = {}) {
        if (!options.skipHistory) this._pushHistory();
        Object.assign(this.params, patch);
        this._save();
        this.dispatchEvent(new CustomEvent('state-updated', { detail: { type: this.type, params: this.params } }));
    }

    setType(type) {
        this._pushHistory();
        this.type = type;
        this.params = getDefaults(type);
        this._save();
        this.dispatchEvent(new CustomEvent('state-updated', { detail: { type: this.type, params: this.params } }));
    }

    loadParams(type, params) {
        this._pushHistory();
        this.type = type;
        this.params = { ...getDefaults(type), ...params };
        this._save();
        this.dispatchEvent(new CustomEvent('state-updated', { detail: { type: this.type, params: this.params } }));
    }

    undo() {
        if (this.historyIndex < this.history.length - 1) {
            this.historyIndex++;
            const prev = this.history[this.history.length - 1 - this.historyIndex];
            this.type = prev.type;
            this.params = { ...prev.params };
            this._save();
            this.dispatchEvent(new CustomEvent('state-updated', { detail: { type: this.type, params: this.params } }));
        }
    }

    redo() {
        if (this.historyIndex > 0) {
            this.historyIndex--;
            const next = this.history[this.history.length - 1 - this.historyIndex];
            this.type = next.type;
            this.params = { ...next.params };
            this._save();
            this.dispatchEvent(new CustomEvent('state-updated', { detail: { type: this.type, params: this.params } }));
        }
    }

    _pushHistory() {
        // Remove any redo states
        if (this.historyIndex > 0) {
            this.history.splice(this.history.length - this.historyIndex);
            this.historyIndex = 0;
        }
        this.history.push({ type: this.type, params: { ...this.params } });
        if (this.history.length > this.maxHistory) this.history.shift();
    }

    _save() {
        try {
            localStorage.setItem('athHornState', JSON.stringify({ type: this.type, params: this.params }));
        } catch (e) { /* quota exceeded, ignore */ }
    }

    _load() {
        try {
            const saved = localStorage.getItem('athHornState');
            if (saved) {
                const { type, params } = JSON.parse(saved);
                this.type = type || 'R-OSSE';
                this.params = { ...getDefaults(this.type), ...params };
            } else {
                this.params = getDefaults(this.type);
            }
        } catch (e) {
            this.params = getDefaults(this.type);
        }
    }
}

export const state = new AppState();
```

### Step 5: `geometry.js` — Copy all math functions

Create `rebuild/src/geometry.js`. This consolidates math from 4 existing files. Copy these functions **verbatim** (do not rewrite the math):

**From `src/geometry/common.js`** — copy the entire file:
- `EPS`, `toRad`, `toDeg`, `clamp`, `evalParam`
- `parseNumberList` (alias `parseList`)
- `isFullCircle`, `parseQuadrants`
- `cleanNumber`, `formatNumber`

**From `src/geometry/expression.js`** — copy the entire file:
- `parseExpression()` (lines 15-146)
- `window.testExpressionParser` debug helper (lines 149-159)

**From `src/geometry/hornModels.js`** — copy the entire file:
- `getGuidingCurveRadius()`
- `computeOsseRadius()`
- `computeCoverageAngleFromGuidingCurve()`
- `validateParameters()`
- `calculateOSSE()`
- `calculateROSSE()`

**From `src/geometry/morphing.js`** — copy both functions:
- `getRoundedRectRadius()`
- `applyMorphing()`

**Important change in morphing.js:** Replace `THREE.MathUtils.lerp(a, b, t)` with an inline function since geometry.js should not depend on Three.js:
```js
const lerp = (a, b, t) => a + (b - a) * t;
```

**From `src/app/params.js`** — copy `prepareParamsForMesh()`. This function transforms raw UI state into mesh-ready parameters (parses expressions, applies scale). Update its imports to reference functions from this same file instead of separate modules.

Make sure all `export` statements are correct. The key exports are:
- `parseExpression`, `evalParam`, `toRad`, `toDeg`, `clamp`, `parseNumberList`, `parseList`
- `parseQuadrants`, `isFullCircle`, `cleanNumber`, `formatNumber`, `EPS`
- `calculateOSSE`, `calculateROSSE`, `validateParameters`
- `getRoundedRectRadius`, `applyMorphing`
- `prepareParamsForMesh`

Remove all `import` statements that reference other files (since everything is now in one file). Remove the `import * as THREE from 'three'` from the morphing code.

### Step 6: `mesh.js` — Copy mesh builder + exports

Create `rebuild/src/mesh.js`. This consolidates the mesh builder and all export formats.

**From `src/geometry/meshBuilder.js`** — copy the entire file:
- `generateThroatSource()`
- `buildSliceMap()`
- `computeOsseProfileAt()`
- `buildMorphTargets()`
- `computeMouthExtents()`
- `buildQuadrantAngles()`
- `buildAngleList()`
- `selectAnglesForQuadrants()`
- `buildHornMesh()` (the main export)

**From `src/geometry/enclosure.js`** — copy the entire file:
- `sampleArc`, `sampleEllipse`, `sampleBezier`, `sampleLine`
- `generateRoundedBoxOutline()`
- `addEnclosureGeometry()`

**From `src/geometry/rearShape.js`** — copy `addRearShapeGeometry()`

**From `src/export/msh.js`** — copy:
- `transformVerticesToAth()`
- `appendThroatCap()`
- `buildMsh()`
- `exportHornToGeo()`
- `exportFullGeo()`
- `exportMSH()`

**From `src/export/csv.js`** — copy:
- `exportVerticesToCSV()`
- `exportCrossSectionProfilesCSV()`

**From `src/export/mwgConfig.js`** — copy `generateMWGConfigContent()`

**From `src/config/parser.js`** — copy the `MWGConfigParser` class with its `parse()` method

**From `src/export/abecProject.js`** — copy:
- `generateAbecProjectFile()`
- `generateAbecSolvingFile()`
- `generateAbecObservationFile()`

Update all imports to reference `geometry.js` instead of the old module paths. For example:
```js
// OLD:
import { calculateROSSE, calculateOSSE } from './hornModels.js';
import { evalParam, parseList, parseQuadrants } from './common.js';
import { applyMorphing } from './morphing.js';

// NEW:
import {
    calculateROSSE, calculateOSSE,
    evalParam, parseList, parseQuadrants,
    applyMorphing, getRoundedRectRadius,
    isFullCircle, cleanNumber, formatNumber, EPS, toRad
} from './geometry.js';
```

### Step 7: `viewer.js` — Three.js scene (skeleton)

Create `rebuild/src/viewer.js` with a skeleton. Copy the core scene setup from `src/viewer/index.js` and `src/app/scene.js`:

- Scene creation (lights, grid, axes helper)
- Material definitions (standard metallic, wireframe, zebra, curvature)
- WebGLRenderer setup
- OrbitControls
- Camera (perspective + ortho toggle)
- Render loop
- `updateModel(vertices, indices)` function that creates/updates a BufferGeometry
- `focusOnModel()` function

Export a `Viewer` class or object that `main.js` can instantiate.

### Step 8: `simulation.js` — Skeleton with stubs

Create `rebuild/src/simulation.js` with stubs for now. The complex simulation logic will be filled in later by Claude. For now, create:

```js
// Simulation panel - connection, run/stop, results display
// This file will be completed by Claude for the complex parts.

export class SimulationPanel {
    constructor(containerEl, state) {
        this.container = containerEl;
        this.state = state;
        this.jobId = null;
        this.connected = false;
        this._buildUI();
        this._checkConnection();
    }

    _buildUI() {
        // TODO: Build simulation controls (run, stop, frequency range, etc.)
        // TODO: Build chart containers for SPL, DI, impedance, polar
    }

    async _checkConnection() {
        try {
            const res = await fetch('http://localhost:8000/health');
            this.connected = res.ok;
        } catch {
            this.connected = false;
        }
        // TODO: Update connection status indicator
    }

    async runSimulation(meshData, params) {
        // TODO: Submit to POST /api/solve
        // TODO: Poll GET /api/status/{id}
        // TODO: Retrieve GET /api/results/{id}
        // TODO: Display in charts
    }

    async stopSimulation() {
        // TODO: POST /api/stop/{id}
    }
}
```

### Step 9: `main.js` — Bootstrap and wire everything together

Create `rebuild/src/main.js`. This is the entry point. It:

1. Imports `state` from `state.js`
2. Imports `Viewer` from `viewer.js`
3. Imports `buildHornMesh` and export functions from `mesh.js`
4. Imports `prepareParamsForMesh` from `geometry.js`
5. Imports `SimulationPanel` from `simulation.js`
6. Imports `PARAM_SCHEMA` from `state.js`

On DOMContentLoaded:
- Initialize the Viewer (attach to `#viewer-container`)
- Build the parameter panel from `PARAM_SCHEMA` (copy the panel generation logic from `src/ui/paramPanel.js`)
- Wire tab navigation (Geometry / Simulation)
- Wire export buttons to export functions
- Wire Load Config button to file upload + `MWGConfigParser`
- Listen for `state-updated` events and call the render pipeline:
  ```js
  state.addEventListener('state-updated', (e) => {
      if (liveUpdateEnabled) renderModel();
  });
  ```
- Wire keyboard shortcuts: Ctrl+Z (undo), Ctrl+Shift+Z (redo)

The `renderModel()` function:
```js
function renderModel() {
    const prepared = prepareParamsForMesh(state.type, state.params);
    const mesh = buildHornMesh(prepared);
    viewer.updateModel(mesh.vertices, mesh.indices);
    updateStats(mesh);
}
```

Copy the parameter panel generation from `src/ui/paramPanel.js`. The key function creates controls for each parameter type:
- `range` → `<input type="range">` + `<input type="number">`
- `number` → `<input type="number">`
- `expression` → `<input type="text">`
- `select` → `<select>` with options

Copy file operations from `src/ui/fileOps.js`:
- `saveFile()` — uses File System Access API with download fallback
- `getExportBaseName()` — prefix + zero-padded counter

### Step 10: `server/requirements.txt`

```
fastapi
uvicorn[standard]
numpy
bempp-cl
```

### Step 11: `server/app.py` — Copy and simplify

Copy `server/app.py` from the existing project. The main change: import from `solver` (single file) instead of `solver/` package. Keep all endpoints:
- `GET /` — serve static files
- `GET /health` — connection check
- `POST /api/solve` — submit simulation job
- `GET /api/status/{id}` — poll job status
- `GET /api/results/{id}` — retrieve results
- `POST /api/stop/{id}` — cancel job

### Step 12: `server/solver.py` — Stub for now

Create a stub that the solver merge (a Claude task) will fill later:

```python
"""
BEM solver wrapper for bempp-cl.
This file will be populated by merging server/solver/*.py modules.
For now, it provides the interface that app.py expects.
"""

def prepare_mesh(vertices, indices, surface_tags, params):
    """Convert raw mesh data into a bempp grid."""
    raise NotImplementedError("Solver not yet merged — see REBUILD_INSTRUCTIONS.md Phase 8")

def run_simulation(mesh, frequency_range, num_frequencies, sim_type, **kwargs):
    """Run BEM frequency sweep. Returns results dict."""
    raise NotImplementedError("Solver not yet merged — see REBUILD_INSTRUCTIONS.md Phase 8")
```

## What NOT to do

- Do NOT rewrite or simplify any mathematical formulas. Copy them verbatim.
- Do NOT remove parameters from the schema. Keep every parameter.
- Do NOT create a build system (no webpack, no rollup, no vite). Native ES modules only.
- Do NOT install npm packages. Everything comes from CDN or is plain JS.
- Do NOT touch the existing codebase. All new files go in `rebuild/`.
- Do NOT attempt the `[CLAUDE]` tasks (complex algorithm verification, solver merging, simulation pipeline). Leave stubs where noted.

## When you're done

The result should be a `rebuild/` directory that:
1. Opens in a browser and shows the UI shell
2. Has `geometry.js` with all math functions importable
3. Has `mesh.js` with the mesh builder and all export format writers importable
4. Has `state.js` with the schema, defaults, and EventTarget-based state
5. Has `viewer.js` that can render a BufferGeometry
6. Has stubs for `simulation.js` and `server/solver.py`
7. Has a working `server/app.py` that starts (even though solver is stubbed)

The parameter panel should generate controls, the viewer should show a 3D model when `renderModel()` is called, and export buttons should trigger the correct export functions (even if some produce incomplete output until the Claude tasks are done).

## Files to read from the existing codebase

Here is the priority order of source files you should read and copy from:

1. `src/geometry/common.js` — small, copy entirely into geometry.js
2. `src/geometry/expression.js` — copy entirely into geometry.js
3. `src/geometry/hornModels.js` — copy entirely into geometry.js
4. `src/geometry/morphing.js` — copy into geometry.js, replace THREE.MathUtils.lerp
5. `src/app/params.js` — copy prepareParamsForMesh into geometry.js
6. `src/geometry/meshBuilder.js` — copy entirely into mesh.js
7. `src/geometry/enclosure.js` — copy entirely into mesh.js
8. `src/geometry/rearShape.js` — copy into mesh.js
9. `src/export/msh.js` — copy into mesh.js
10. `src/export/csv.js` — copy into mesh.js
11. `src/export/mwgConfig.js` — copy into mesh.js
12. `src/config/parser.js` — copy MWGConfigParser into mesh.js
13. `src/export/abecProject.js` — copy into mesh.js
14. `src/config/schema.js` — copy PARAM_SCHEMA into state.js
15. `src/config/defaults.js` — copy getDefaults into state.js
16. `src/viewer/index.js` — reference for viewer.js
17. `src/app/scene.js` — reference for viewer.js
18. `src/ui/paramPanel.js` — reference for main.js parameter panel
19. `src/ui/fileOps.js` — copy saveFile/getExportBaseName into main.js
20. `index.html` — reference for rebuild/index.html layout
21. `style.css` — copy as-is
22. `server/app.py` — copy and simplify imports
