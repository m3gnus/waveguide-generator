# MWG Horn Design Platform -- Architecture & Development Plan

> **Last Updated:** 2026-02-02
> **Current Version:** 1.0.0-alpha-7.5

## 1. Project Goal & Scope

### Vision

Transform the existing MWG - Mathematical Waveguide Generator 3D Visualizer into a **complete horn design and simulation platform** that can:

1. **Design** -- Parametrically define OSSE and R-OSSE waveguides with full MWG parameter support
2. **Preview** -- Real-time 3D visualization with surface analysis (curvature, zebra, wireframe)
3. **Simulate** -- Run BEM acoustic simulations (via bempp-cl) directly from the browser
4. **Analyze** -- Display directivity, polar plots, impedance, SPL maps
5. **Optimize** -- Batch parameter sweeps with automated scoring and ranking
6. **Export** -- STL, Gmsh .msh, MWG config, CSV profiles

### Current Status

The platform has completed Phases 0-6 and has Phase 7 (AI) as stubs:
- ✅ Modular codebase with event-driven architecture
- ✅ Full geometry support (OSSE, R-OSSE, morphing, enclosure, rollback)
- ✅ Config parsing, validation, and schema system
- ✅ Export suite (MWG config, Gmsh .msh, CSV profiles)
- ✅ BEM solver backend (bempp-cl with mock fallback)
- ✅ Optimization engine (grid, random, coordinate descent)
- ✅ Workflow, presets, and validation framework
- ⚠️ BEM solver needs validation against ABEC references
- ⚠️ Results visualization module not yet implemented
- ⚠️ AI modules are stubs only

### Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| OSSE, R-OSSE horn models | Driver/motor FEA simulation |
| BEM acoustic simulation (Helmholtz) | Full LEM circuit simulation |
| Directivity, impedance, SPL output | Room acoustics / ray tracing |
| STL, Gmsh, CSV export | Commercial CAD format export (STEP/IGES) |
| Parameter optimization sweeps | Deep learning optimization |
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
│  │ UI Panel  │  │ 3D View  │  │Simulation│  │  Optimization    │ │
│  │ Module    │  │ Module   │  │  Panel   │  │  Engine          │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
│       │              │              │                  │           │
│  ┌────┴──────────────┴──────────────┴──────────────────┴────────┐ │
│  │                     Event Bus (AppEvents)                     │ │
│  └────┬──────────────┬──────────────┬──────────────────┬────────┘ │
│       │              │              │                  │           │
│  ┌────┴─────┐  ┌─────┴────┐  ┌─────┴──────┐  ┌───────┴────────┐ │
│  │ Geometry │  │  Config   │  │  Solver    │  │   Export       │ │
│  │ Core     │  │  Manager  │  │  Client    │  │   Engine       │ │
│  └──────────┘  └──────────┘  └────────────┘  └────────────────┘ │
│                                     │                             │
│  ┌──────────┐  ┌──────────┐  ┌─────┴──────┐  ┌────────────────┐ │
│  │ Workflow │  │ Presets  │  │ Validation │  │   AI Module    │ │
│  │ Engine   │  │ Manager  │  │ Framework  │  │   (stubs)      │ │
│  └──────────┘  └──────────┘  └────────────┘  └────────────────┘ │
│                                     │                             │
│                              ┌──────┴──────┐                     │
│                              │   Python    │                     │
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

## 3. Core Modules (Current Implementation)

### Module 1: Geometry Core (`src/geometry/`)

**Purpose:** All horn math and mesh generation.

**Actual Files:**
```
src/geometry/
  index.js          -- Public API (re-exports)
  hornModels.js     -- Combined R-OSSE and OSSE calculations
  expression.js     -- Math expression parser
  meshBuilder.js    -- Three.js BufferGeometry construction
  morphing.js       -- Round-to-target shape morphing
  enclosure.js      -- Enclosure box geometry
  rollback.js       -- Mouth rollback toroidal fold
  rearShape.js      -- Rear chamber shape generation
  AGENTS.md         -- Module documentation for AI assistants
```

**Note:** The original architecture specified separate `rosse.js` and `osse.js` files, but these were combined into `hornModels.js` during implementation.

---

### Module 2: Config Manager (`src/config/`)

**Purpose:** Parse, validate, serialize, and manage MWG configuration files.

**Actual Files:**
```
src/config/
  index.js          -- Public API
  parser.js         -- MWG config parser
  schema.js         -- Parameter schema definitions
  validator.js      -- Parameter range validation
  defaults.js       -- Default parameter sets
  AGENTS.md         -- Module documentation
```

**Note:** The `serializer.js` file was not created separately; serialization is handled within `mwgConfig.js` in the export module.

---

### Module 3: 3D Viewer (`src/viewer/`)

**Purpose:** Three.js scene management, camera controls, display modes, rendering.

**Actual Files:**
```
src/viewer/
  index.js          -- Combined viewer implementation (scene, camera, materials, controls)
  annotations.js    -- Dimension lines and labels
  AGENTS.md         -- Module documentation
```

**Note:** The viewer was simplified from the original architecture. Instead of separate files for scene.js, camera.js, controls.js, materials.js, and hornRenderer.js, most functionality is combined in `index.js` with some remaining in `src/main.js`.

---

### Module 4: Acoustic Solver Interface (`src/solver/`)

**Purpose:** Bridge between the browser app and BEM acoustic simulation.

**Actual Files:**
```
src/solver/
  index.js          -- Public API and mock solver
  client.js         -- HTTP client for solver backend
  meshExport.js     -- Convert Three.js geometry to solver format
  resultParser.js   -- Parse solver response into visualization data
  status.js         -- Connection status management
  bemMeshGenerator.js -- Generate BEM-ready meshes with surface tags

src/processing/
  smoothing.js      -- Frequency response smoothing algorithms
                       (fractional octave, variable, psychoacoustic, ERB)
```

**Backend Files:**
```
server/
  app.py            -- FastAPI application
  solver/           -- bempp-cl Helmholtz BEM solver package
  requirements.txt  -- Python dependencies (gmsh, bempp-cl prerequisites)
  start.sh          -- Server startup script
  README.md         -- Backend documentation
```

**Solver API (REST):**
```
POST /api/solve
  Body: {
    mesh,
    frequency_range,
    num_frequencies,
    sim_type,
    boundary_conditions,
    polar_config: {
      angle_range: [start, end, num_points],
      norm_angle: float,
      distance: float,
      inclination: float
    }
  }
  Response: { job_id: "..." }

GET /api/status/{job_id}
  Response: { status: "running"|"complete"|"error", progress: 0.0-1.0 }

GET /api/results/{job_id}
  Response: {
    directivity: { horizontal, vertical, diagonal },
    impedance: { frequencies, real, imaginary },
    spl_on_axis: { frequencies, spl },
    di: { frequencies, di }
  }

GET /health
  Response: { status: "ok" }
```

**ABEC.Polars Configuration:**

The solver now supports ATH/ABEC-style polar directivity configuration:
- `angle_range`: [start_deg, end_deg, num_points] for angular sweep
- `norm_angle`: Reference angle for normalization (degrees)
- `distance`: Measurement distance from horn mouth (meters)
- `inclination`: Inclination angle for measurement plane (degrees)

Reference: Ath-4.8.2-UserGuide section 4.1.5

**BEM Solver Implementation Details:**

The `server/solver/` package implements:
- Full Helmholtz BIE formulation using bempp-cl
- Gmsh mesh refinement for frequency-dependent element sizing
- Proper boundary conditions:
  - Velocity source at throat (unit normal velocity)
  - Rigid (Neumann) boundary on horn walls
  - Open radiation at mouth
- Directivity index calculation via hemisphere integration
- MockBEMSolver fallback with realistic acoustic data when bempp unavailable

---

### Module 5: Results Viewer (`src/results/`)

**Purpose:** Visualize acoustic simulation results.

**Status: NOT IMPLEMENTED**

The architecture specifies:
```
src/results/
  index.js          -- Public API
  polarPlot.js      -- SVG polar radiation pattern
  frequencyPlot.js  -- SPL vs frequency
  sonogram.js       -- Directivity map
  impedancePlot.js  -- Throat impedance curves
  diPlot.js         -- Directivity Index vs frequency
  chartUtils.js     -- Shared utilities
```

**Current State:** Basic result display is embedded in `src/ui/simulationPanel.js`. A dedicated results module with proper visualization has not been implemented yet.

---

### Module 6: Export Engine (`src/export/`)

**Purpose:** Generate all output formats from current geometry and parameters.

**Actual Files:**
```
src/export/
  index.js          -- Public API
  mwgConfig.js      -- MWG config file export
  msh.js            -- Gmsh .msh export with Physical Surface tags
  csv.js            -- CSV profile export
  profiles.js       -- Profile data generation
  AGENTS.md         -- Module documentation
```

**Note:** The original architecture specified `stl.js`, `gmsh.js` (.geo), `abecProject.js`, and `objExport.js`. The actual implementation:
- STL export remains in `src/main.js` (not extracted)
- Gmsh export uses `.msh` format (binary mesh) via `msh.js`, not `.geo` (geometry script)
- ABEC project export not implemented
- OBJ export not implemented

---

### Module 7: UI Panel (`src/ui/`)

**Purpose:** Parameter controls, model type selection, file operations, simulation interface.

**Actual Files:**
```
src/ui/
  paramPanel.js     -- Schema-driven parameter UI generation
  fileOps.js        -- Load/save config, file picker
  simulationPanel.js -- Combined solver controls, results panel, export
                       Includes:
                       - ABEC.Polars directivity configuration UI
                       - Polar heatmap visualization (2D frequency/angle maps)
                       - REW-style smoothing controls with keyboard shortcuts
                       - Real-time smoothing application (no re-simulation needed)
  AGENTS.md         -- Module documentation
```

**Features:**
- **Directivity Heatmaps:** Professional 2D color-coded SPL maps (frequency vs angle) matching industry standards (e.g., ADAM Audio)
- **Results Export:** Multiple export formats for simulation results:
  - SVG/PNG images of charts
  - CSV data files (frequency, SPL, DI, impedance)
  - JSON format (complete results with metadata and smoothing info)
  - Text reports with summary statistics
  - Exports include current smoothing settings
- **Post-Processing Smoothing:** 11 smoothing algorithms with full keyboard shortcut support:
  - Fractional octave: 1/1, 1/2, 1/3, 1/6, 1/12, 1/24, 1/48 octave
  - Variable: Frequency-dependent bandwidth (1/48 @ 100Hz to 1/3 @ 10kHz)
  - Psychoacoustic: Perception-based with cubic mean peak emphasis
  - ERB: Equivalent Rectangular Bandwidth matching ear's resolution
- **Keyboard Shortcuts:**
  - `Ctrl+Shift+1-9`: Fractional octave smoothing
  - `Ctrl+Shift+X`: Variable smoothing
  - `Ctrl+Shift+Y`: Psychoacoustic smoothing
  - `Ctrl+Shift+Z`: ERB smoothing
  - `Ctrl+0`: Remove smoothing
  - Toggle behavior: Press same shortcut again to remove

**Note:** The original architecture specified separate files for modelSelect.js, exportPanel.js, displayPanel.js, solverPanel.js, and resultsPanel.js. These were combined into fewer files, with `simulationPanel.js` handling most simulation-related UI.

---

### Module 8: Optimization Engine (`src/optimization/`)

**Purpose:** Automated parameter exploration and design ranking.

**Actual Files:**
```
src/optimization/
  index.js          -- Public API
  parameterSpace.js -- Parameter bounds and definitions
  objectiveFunctions.js -- Acoustic quality scoring
  engine.js         -- Optimization loop and algorithms
  results.js        -- Result storage and management
  api.js            -- Clean API for external integration
```

**Implemented Algorithms:**
- Grid search (exhaustive)
- Random sampling
- Coordinate descent

**Objective Functions:**
- Smooth on-axis frequency response
- Directivity control
- Impedance matching
- Multi-objective weighted scoring

---

### Module 9: Workflow Engine (`src/workflow/`)

**Purpose:** Canonical workflow state machine for design process.

**Actual Files:**
```
src/workflow/
  index.js          -- Workflow state machine implementation
```

---

### Module 10: Presets Manager (`src/presets/`)

**Purpose:** Fast starting points and configuration management.

**Actual Files:**
```
src/presets/
  index.js          -- Preset storage and management
```

---

### Module 11: Validation Framework (`src/validation/`)

**Purpose:** Reference comparison and result validation.

**Actual Files:**
```
src/validation/
  index.js          -- Validation manager and reference comparison
```

---

### Module 12: AI Module (`src/ai/`) — STUBS ONLY

**Purpose:** AI-assisted design guidance (not yet implemented).

**Actual Files:**
```
src/ai/
  index.js          -- Module entry point with status warning

  knowledge/
    index.js        -- Knowledge storage API
    schema.js       -- Design knowledge schema
    storage.js      -- Persistence layer

  surrogate/
    index.js        -- Surrogate model API
    gaussianProcess.js -- GP stub (NOT mathematically correct)
    regression.js   -- Simple regression models

  optimization/
    index.js        -- AI optimization API
    bayesianOptimizer.js -- BO stub (returns mock values)
    cmaesAdapter.js -- CMA-ES adapter stub

  insights/
    index.js        -- Insights API
    sensitivityAnalyzer.js -- Sensitivity analysis
    textGenerator.js -- Human-readable explanations
```

**Status:** All AI modules are **STUBS** that define interfaces but return mock/demo data. Real implementation requires:
1. Validated BEM solver (for training data)
2. Proper GP library with matrix inversion
3. Real acoustic metrics for insight generation

---

### Supporting Modules

**Event Bus (`src/events.js`):**
```javascript
// Minimal synchronous pub/sub
AppEvents.emit(eventName, payload)
AppEvents.on(eventName, handler)
AppEvents.off(eventName, handler)
```

**State Management (`src/state.js`):**
- Serializable application state
- Undo/redo support
- localStorage persistence

**Logging (`src/logging/`):**
- Change tracking for all AI agent and user interactions
- Session-based log grouping
- Event categorization (state, geometry, workflow, export, simulation)
- Log persistence and export capabilities
- Subscriber pattern for real-time log monitoring

---

## 4. Setup & Dependencies

### Automated Setup

The project includes a setup script that handles all dependency installation:

```bash
./setup.sh
```

This script:
1. Checks for Node.js and npm
2. Installs frontend dependencies (`npm install`)
3. Checks for Python 3 and pip3
4. Installs backend dependencies (`pip3 install -r server/requirements.txt`)
5. Attempts to install bempp-cl from GitHub
6. Falls back gracefully if bempp installation fails

### Python Dependencies (`server/requirements.txt`)

```
# Web Framework
fastapi==0.104.1
uvicorn[standard]==0.24.0
python-multipart==0.0.6

# BEM Solver prerequisites
plotly
numpy
scipy
numba
meshio>=4.0.16

# Mesh Processing
trimesh==4.0.5
gmsh>=4.10.0

# Utilities
pydantic>=2.10.0

# bempp-cl installed separately via:
# pip install git+https://github.com/bempp/bempp-cl.git
```

### Graceful Degradation

Without the Python backend or bempp-cl:
- Geometry, visualization, and export work normally
- Solver shows "not connected" status
- Mock solver provides realistic fake data for UI testing
- App remains fully functional for horn design work

---

## 5. Repository Structure (Actual)

```
mwg-horn/
├── index.html                    -- Entry point
├── style.css                     -- Global styles
├── setup.sh                      -- One-time setup script
│
├── src/
│   ├── main.js                   -- App bootstrap and orchestrator
│   ├── events.js                 -- EventBus
│   ├── state.js                  -- Serializable application state
│   │
│   ├── geometry/
│   │   ├── index.js              -- Public API
│   │   ├── hornModels.js         -- R-OSSE and OSSE calculations
│   │   ├── expression.js         -- Math expression parser
│   │   ├── meshBuilder.js        -- Three.js BufferGeometry
│   │   ├── morphing.js           -- Shape morphing
│   │   ├── enclosure.js          -- Enclosure box
│   │   ├── rollback.js           -- Mouth rollback
│   │   ├── rearShape.js          -- Rear chamber shape
│   │   └── AGENTS.md
│   │
│   ├── config/
│   │   ├── index.js
│   │   ├── parser.js             -- MWG config parser
│   │   ├── schema.js             -- Parameter schema
│   │   ├── validator.js          -- Validation
│   │   ├── defaults.js           -- Default parameters
│   │   └── AGENTS.md
│   │
│   ├── viewer/
│   │   ├── index.js              -- Combined viewer
│   │   ├── annotations.js        -- Dimension lines
│   │   └── AGENTS.md
│   │
│   ├── export/
│   │   ├── index.js
│   │   ├── mwgConfig.js          -- MWG config export
│   │   ├── msh.js                -- Gmsh .msh export
│   │   ├── csv.js                -- CSV profile export
│   │   ├── profiles.js           -- Profile generation
│   │   └── AGENTS.md
│   │
│   ├── solver/
│   │   ├── index.js              -- Public API + mock solver
│   │   ├── client.js             -- HTTP client
│   │   ├── meshExport.js         -- Mesh conversion
│   │   ├── resultParser.js       -- Parse results
│   │   ├── status.js             -- Connection status
│   │   └── bemMeshGenerator.js   -- BEM mesh generation
│   │
│   ├── processing/
│   │   └── smoothing.js          -- REW-style frequency response smoothing
│   │
│   ├── optimization/
│   │   ├── index.js
│   │   ├── parameterSpace.js
│   │   ├── objectiveFunctions.js
│   │   ├── engine.js
│   │   ├── results.js
│   │   └── api.js
│   │
│   ├── workflow/
│   │   └── index.js
│   │
│   ├── presets/
│   │   └── index.js
│   │
│   ├── validation/
│   │   └── index.js
│   │
│   ├── logging/
│   │   └── index.js
│   │
│   ├── ai/                       -- STUBS ONLY
│   │   ├── index.js
│   │   ├── knowledge/
│   │   ├── surrogate/
│   │   ├── optimization/
│   │   └── insights/
│   │
│   └── ui/
│       ├── paramPanel.js
│       ├── fileOps.js
│       └── simulationPanel.js
│
├── server/
│   ├── app.py                    -- FastAPI application
│   ├── solver/                  -- bempp-cl BEM solver package
│   ├── requirements.txt          -- Python dependencies
│   ├── start.sh                  -- Server startup
│   └── README.md
│
├── tests/
│   └── unit/                     -- Jest unit tests
│
├── plan/
│   ├── ROADMAP.md                -- Development roadmap
│   ├── STATUS.md                 -- Current status
│   └── AGENT_ROLES.md            -- AI agent guidelines
│
├── _references/                  -- Reference data and ABEC comparisons
│
├── package.json
├── package-lock.json
├── jest.config.js
├── webpack.config.js
│
├── docs/
│   ├── ARCHITECTURE.md           -- This document
│   ├── AGENT_INSTRUCTIONS.md     -- Detailed AI instructions
│   ├── AI_GUIDANCE.md            -- AI module documentation
│   └── README.md                 -- Documentation index
├── AGENTS.md                     -- Top-level AI guidance
└── README.md                     -- Project overview
```

---

## 6. Tech Stack (Actual)

### Frontend (Browser)

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | JavaScript (ES2022+) | ES modules, no transpilation |
| 3D Engine | Three.js 0.160+ | CDN via import map |
| Module System | ES Modules | Native browser support |
| UI Framework | Vanilla DOM | No framework |
| State Management | EventBus + state.js | Undo/redo support |
| Testing | Jest | Unit tests |

### Backend (Python)

| Component | Technology | Notes |
|-----------|-----------|-------|
| Framework | FastAPI | Async, automatic docs |
| BEM Solver | bempp-cl | With mock fallback |
| Mesh Processing | Gmsh, meshio, trimesh | Full mesh pipeline |
| Math | NumPy, SciPy | Scientific computing |

### Build & Dev

| Tool | Purpose |
|------|---------|
| Webpack | Production bundling |
| Jest | Unit testing |
| Express | Dev server |

---

## 7. Frontend Element Reference

| Element ID | Description |
|------------|-------------|
| `#render-btn` | Update horn geometry |
| `#export-btn` | Export STL mesh |
| `#export-config-btn` | Export MWG config |
| `#export-csv-btn` | Export CSV profiles |
| `#export-geo-btn` | Export Gmsh mesh |
| `#ui-panel` | Main UI panel container |
| `#canvas-container` | 3D visualization area |
| `#param-container` | Parameter controls |
| `#display-mode` | Display mode selector |
| `#stats` | Geometry statistics |
| `#solver-status` | BEM solver connection |
| `#run-simulation-btn` | Start BEM simulation |

---

## 8. Coordinate System

```
Y-axis: Axial direction (throat at Y=0, mouth at Y=L)
X-axis: Horizontal (r * cos(p))
Z-axis: Vertical (r * sin(p))

Enclosure: extends in -Y direction from mouth
Rollback: toroidal fold at mouth rim, curls in -Y direction

3D vertex mapping:
  vx = radius * cos(azimuthalAngle)
  vy = axialPosition
  vz = radius * sin(azimuthalAngle)
```

---

## 9. Development Phases Summary

| Phase | Status | Description |
|-------|--------|-------------|
| 0 | ✅ Complete | Module extraction from monolithic main.js |
| 1 | ✅ Complete | Config schema, validation, undo/redo |
| 2 | ✅ Complete | Enhanced geometry, morphing, annotations |
| 3 | ✅ Complete | Export suite (MWG, Gmsh, CSV) |
| 4 | ⚠️ 70% | BEM solver (code complete, needs validation) |
| 5 | ✅ Complete | Optimization engine |
| 6 | ✅ Complete | Workflow, presets, validation framework |
| 7 | ⚠️ 20% | AI module (stubs only) |

### Remaining Work

**Phase 4 Completion:**
- Validate BEM results against ABEC references
- Document mesh quality requirements
- Test with real horn geometries

**Results Visualization:**
- Implement `src/results/` module
- Polar plots, frequency response, sonograms

**Phase 7 Implementation:**
- Requires validated BEM solver first
- Implement proper GP with matrix operations
- Train surrogate models on real data

---

## 10. AI Collaboration Guidelines

### File Size Discipline

Target: **under 300 lines per file**. Split when approaching limit.

### Module Contract Pattern

Each module has `index.js` defining the public API. Internal files are implementation details.

### Task Sizing

Good: "Modify 1-3 files, touch 20-100 lines"
Bad: "Implement the whole solver"

### Documentation

- Each module has `AGENTS.md` for AI context
- JSDoc on all public functions
- Status warnings in stub implementations
