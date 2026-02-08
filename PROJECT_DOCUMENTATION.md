# ATH Horn Project - Complete Documentation

## Table of Contents
1. [Project Overview](#project-overview)
2. [File Tree View](#file-tree-view)
3. [Module Explanations](#module-explanations)
   - Core System
   - Geometry & Math Models
   - Mesh Generation
   - CAD System
   - Simulation (BEM Solver)
   - UI Components
   - Export Systems
4. [Investigation Prompts](#investigation-prompts)

---

## Project Overview

The ATH Horn project is a **Mathematical Waveguide Generator** - a web-based tool for designing and simulating acoustic horns (waveguides). It combines:

1. **3D Visualization**: Real-time rendering of horn geometry using Three.js
2. **Parametric Design**: Adjust throat angle, coverage, rollback, and more with live updates
3. **Acoustic Simulation**: Boundary Element Method (BEM) solver for predicting sound output
4. **Export Tools**: Generate 3D models (STL), mesh files (Gmsh), and simulation configs

The project uses:
- **Frontend**: JavaScript with ES modules, Three.js 0.160
- **Backend**: Python 3.8-3.13, FastAPI, bempp-cl for BEM simulations
- **CAD**: OpenCascade for parametric STEP geometry generation

---

## File Tree View

```
ath-horn/
├── index.html                      # Main HTML entry point
├── style.css                       # Styles for UI panels
├── package.json                    # NPM dependencies and scripts
├── webpack.config.js               # Build configuration
├── setup.sh                        # One-time setup script
│
├── src/                            # Frontend source code
│   ├── main.js                     # App entry point
│   ├── state.js                    # Global application state management
│   │
│   ├── events.js                   # Event bus system for communication
│   │   └── EventBus class         # Publish/subscribe event handling
│   │
│   ├── config/                     # Configuration and parameter management
│   │   ├── index.js                # Config module exports
│   │   ├── defaults.js             # Default parameter values per model type
│   │   ├── schema.js               # Parameter definitions with validation rules
│   │   ├── parser.js               # Parse configuration files (JSON, MWG format)
│   │   └── validator.js            # Validate parameters against schema
│   │
│   ├── geometry/                   # Horn math and profile calculations
│   │   ├── index.js                # Geometry module exports
│   │   ├── common.js               # Utility functions (angle conversions, clamping)
│   │   ├── hornModels.js           # OSSE and R-OSSEhorn profile formulas
│   │   ├── meshBuilder.js          # Generate triangle meshes from profiles
│   │   ├── morphing.js             # Round rectangular mouth shaping logic
│   │   ├── enclosure.js            # Enclosure/wall geometry generation
│   │   └── rearShape.js            # Rear cap/shelf geometry
│   │
│   ├── viewer/                     # 3D visualization components
│   │   ├── index.js                # Viewer module exports
│   │   └── annotations.js          # Add text labels to 3D scene
│   │
│   ├── app/                        # Main application logic
│   │   ├── App.js                  # Application class coordinating all components
│   │   ├── events.js               # App-specific event handling
│   │   ├── scene.js                # Three.js scene setup and rendering
│   │   ├── params.js               # Prepare parameters for mesh generation
│   │   ├── mesh.js                 # Provide mesh to simulation system
│   │   ├── configImport.js         # Load .mwg config files from disk
│   │   ├── exports.js              # Export functions (STL, CSV, Gmsh)
│   │   └── logging.js              # Initialize change tracking logger
│   │
│   ├── ui/                         # User interface components
│   │   ├── paramPanel.js           # Parameters panel with sliders and inputs
│   │   ├── simulationPanel.js      # Simulation tab controls and results display
│   │   └── simulation/             # Sub-components of simulation panel
│   │       ├── index.js            # Simulation module exports
│   │       ├── actions.js          # Run/pause/cancel simulation functions
│   │       ├── charts.js           # Chart rendering (SPL, DI, impedance)
│   │       ├── connection.js       # BEM solver connection status
│   │       ├── events.js           # Simulation-related event handlers
│   │       ├── exports.js          # Export results to various formats
│   │       ├── mesh.js             # Prepare and send mesh for simulation
│   │       ├── results.js          # Display simulation results
│   │       ├── settings.js         # Sync UI with simulation settings
│   │       ├── smoothing.js        # Frequency smoothing controls and shortcuts
│   │       └── SimulationPanel.js  # Main simulation panel class
│   │
├── server/                         # Python backend (BEM solver)
│   ├── app.py                      # FastAPI web server
│   ├── requirements.txt            # Python dependencies
│   ├── start.sh                    # Start script
│   │
│   └── solver/                     # BEM simulation package
│       ├── __init__.py             # Package initialization
│       ├── bem_solver.py           # Main solver class (BEMSolver)
│       ├── deps.py                 # Dependency checks (bempp-cl availability)
│       ├── directivity.py          # Far-field directivity calculations
│       ├── directivity_correct.py  # Corrected directivity with proper integration
│       ├── mesh.py                 # Mesh preparation and Gmsh refinement
│       ├── mesh_validation.py      # Validate mesh quality for frequencies
│       ├── solve.py                # Core BEM solver (legacy)
│       ├── solve_optimized.py      # Optimized solver with symmetry detection
│       └── symmetry.py             # Automatic geometric symmetry detection

scripts/                          # Development and testing utilities
├── ath-compare.js                 # Compare MWG output against ATH reference
├── dev-server.js                  # Development server
└── gmsh-export.py                 # Gmsh mesh export pipeline

_references/                      # ATH reference data for validation
└── testconfigs/                   # Test configurations and expected outputs
```

---

## Module Explanations

### Core System

#### events.js - Event Bus System

**What it does:**
This module implements a publish/subscribe event system that allows different parts of the application to communicate without being tightly coupled.

**How it works:**
- Components can **listen** for specific events (like "state:updated")
- When something changes, code **emits** an event with data
- All listeners receive the event and can react accordingly
- Events can have middleware that processes them before reaching listeners

**Example flow:**
```
1. User changes a slider → AppState.update() called
2. AppState emits 'state:updated' event
3. App.js listens for this and calls renderModel()
4. 3D viewport updates with new geometry
```

#### state.js - Global State Management

**What it does:**
Maintains the complete application state (horn parameters, model type) in a single source of truth.

**Key features:**
- **Undo/Redo**: Stores last 50 state changes in history stack
- **Persistence**: Saves current state to localStorage automatically
- **Event emission**: Notifies listeners when state changes

**Structure:**
```javascript
{
    type: 'R-OSSE',           // Model type (OSSE or R-OSSE)
    params: { ... }           // All parameter key-value pairs
}
```

### Geometry & Math Models

#### common.js - Utility Functions

**What it does:**
Provides shared helper functions used throughout the geometry modules.

**Key functions:**
| Function | Purpose |
|----------|---------|
| `toRad(deg)` | Convert degrees to radians (multiply by π/180) |
| `toDeg(rad)` | Convert radians to degrees (multiply by 180/π) |
| `clamp(value, min, max)` | Restrict value to range [min, max] |
| `evalParam(value, p)` | If value is a function, call it; otherwise return value |

#### hornModels.js - Horn Profile Formulas

**What it does:**
Contains the mathematical formulas for generating horn profiles. This is where the actual horn shape mathematics lives.

**Two main models:**

1. **OSSE (Oblate Spheroid Enclosed)**:
   - Uses a guiding curve to determine mouth radius at each angle
   - Formula combines geometric optics with exponential taper
   - Parameters: L (length), a (mouth angle), r0 (throat radius), k, n, s

2. **R-OSSE (Round-over OSSE)**:
   - A variant with rounded throat transition
   - Uses different parameters for smoother throat profile

**How it works:**
```
For each point on the horn:
1. Calculate axial position z from normalized t
2. Calculate radius r at that position using formula
3. Apply morphing (if enabled) to round corners
4. Return {x: z, y: r} for 2D profile
5. Rotate around axis to create 3D surface
```

#### meshBuilder.js - Generate Triangle Mesh

**What it does:**
Takes the mathematical horn profiles and converts them into a triangle mesh that can be rendered or used for simulation.

**Key steps:**

1. **Slice distribution**: Decide where to place cross-sections along the horn
   - Uses uniform or user-specified resolution values

2. **Angle sampling**: Sample around the circumference
   - More points at corners for rounded rectangular throats
   - Fewer points on straight sides

3. **Vertex generation**:
   ```
   For each slice (j) and angle (i):
      - Calculate horn radius r at that position
      - Convert to 3D: x = r*cos(p), y = axial_pos, z = r*sin(p)
      - Add vertex to array
   ```

4. **Triangle indices**:
   ```
   For each quad between adjacent points:
      - Create two triangles connecting them
      - Result: triangle list for WebGL/Three.js
   ```

#### morphing.js - Round Rectangular Mouth

**What it does:**
Morphs circular throat to rectangular mouth with rounded corners.

**How the math works:**

1. **Rounded rectangle radius calculation** (`getRoundedRectRadius`):
   ```
   For each angle p:
      - If corner radius = 0: simple rectangle
      - If corner radius > 0: 
          * Calculate where corner arc starts
          * Return smaller of side or corner radius
   ```

2. **Morphing interpolation** (`applyMorphing`):
   ```
   1. Check if morphing should start (past morphFixed position)
   2. Calculate morph factor (0=none, 1=full)
      - Uses power function: factor = pow(t, morphRate)
      - Higher rate = sharper transition at mouth
   3. Interpolate between circular throat and rectangular mouth shape:
      r_final = lerp(r_circular, r_rectangular, morphFactor)
   ```

#### enclosure.js - Enclosure/Wall Geometry

**What it does:**
Adds an enclosure/wall around the horn (for OSSE models), creating a closed box with acoustic source at throat.

**Key functions:**
| Function | Purpose |
|----------|---------|
| `addEnclosureGeometry()` | Build wall mesh and add to vertices/indices |

**How it works:**
```
1. Determine enclosure depth from params
2. Generate outer wall surface following horn shape
3. Connect back to create closed volume
4. Mark different faces (mouth, throat) with tags for BEM BCs
```

#### rearShape.js - Rear Cap Geometry

**What it does:**
Adds a rear cap/shelf at the back of the horn.

### CAD System

#### cadManager.js - OpenCascade Interface

**What it does:**
Manages communication with OpenCascade (CAD kernel) running in Web Worker.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `initCADWorker()` | Start worker and load OpenCascade library |
| `buildAndTessellate()` | Build horn geometry, return triangles |
| `exportSTEP()` | Export parametric horn to STEP format |
| `terminateCADWorker()` | Clean shutdown |

**How it works:**
```
1. Create horn surface by lofting cross-sections
2. Optionally add throat source face
3. Tessellate (convert B-Rep to triangles)
4. Return vertex/indices arrays
```

### Simulation (BEM Solver)

#### bem_solver.py - Main Python Solver

**What it does:**
The entry point for acoustic simulations using Boundary Element Method.

**Key classes:**

| Class | Purpose |
|-------|---------|
| `BEMSolver` | Main solver class that interfaces with bempp-cl |

**Main methods:**

1. **prepare_mesh()**: 
   - Takes raw vertices/indices
   - Adds surface tags for boundary conditions
   - Returns BEMPP grid object

2. **solve()**:
   - Runs simulation across frequency range
   - Can use optimized solver with symmetry detection
   - Returns SPL, DI, impedance at each frequency

#### solve_optimized.py - Optimized Solver

**What it does:**
Enhanced version of the solver with performance improvements.

**Key optimizations:**

1. **Symmetry detection**: 
   - Analyzes geometry to find mirror planes
   - Reduces mesh size by 2-4x for symmetric horns
   - Significant speedup (e.g., 4× faster for full horn)

2. **Operator caching**:
   - BEM operators are expensive to compute
   - Cache them between frequencies when possible
   - Reuse for each frequency in simulation

3. **Correct far-field evaluation**:
   - Proper integration for directivity patterns
   - More accurate than older analytical approximation

#### symmetry.py - Automatic Symmetry Detection

**What it does:**
Detects if a horn is symmetric and reduces the model accordingly.

**How it works:**

1. **Geometric detection**:
   ```
   For each candidate plane (XZ, YZ):
      - Check if left/right sides match
      - Within tolerance (fraction of max dimension)
   ```

2. **Excitation check**:
   ```
   Ensure throat center is on symmetry plane
   If not, cannot use symmetry reduction
   ```

3. **Reduction**:
   ```
   Keep only half (or quarter) of mesh
   Mark symmetry faces with special tag
   BEM implicitly applies Neumann BC on these
   ```

### UI Components

#### app/App.js - Main Application Coordinator

**What it does:**
Main application class that ties everything together.

**Key responsibilities:**

| Responsibility | What it does |
|----------------|--------------|
| Initialize UI panels | ParamPanel, SimulationPanel |
| Manage 3D scene | Three.js setup and rendering loop |
| Handle file I/O | Import/export config files |
| Coordinate workflows | Mesh → Simulation → Results |

#### ui/paramPanel.js - Parameters Panel

**What it does:**
Displays all horn parameters as sliders and inputs.

**How it works:**
```
1. Read current params from GlobalState
2. For each param in schema:
   - Create slider/input element
   - Add event listener for changes
3. When user moves slider:
   - Update GlobalState with new value
   - State emits 'state:updated'
   - App renders new geometry
```

#### ui/simulation/Actions.js - Simulation Control

**What it does:**
Runs the actual simulation and manages job lifecycle.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `runSimulation()` | Submit mesh to BEM solver |
| `pollSimulationStatus()` | Check if job is complete |
| `cancelSimulation()` | Stop running job |

**Workflow:**
```
1. User clicks "Run"
2. Mesh prepared and sent to backend
3. Job ID returned immediately
4. Polling starts, checking status every second
5. When complete, results displayed in charts
```

#### ui/simulation/Charts.js - Visualization

**What it does:**Renders results as interactive charts using Chart.js.

**Chart types:**

1. **Frequency Response**: SPL (dB) vs Frequency (Hz)
2. **Directivity Index**: DI (dB) vs Frequency
3. **Impedance**: Real/Imaginary parts vs Frequency
4. **Polar Heatmap**: SPL vs Angle vs Frequency

### Export Systems

#### export/stl.js - STL Export

**What it does:**
Converts triangle mesh to STL format for 3D printing.

**Format options:**

1. **Binary STL**:
   ```
   Header (80 bytes)
   Triangle count (4 bytes)
   For each triangle:
     Normal vector (12 bytes)
     3 vertices (36 bytes)
     Attribute byte count (2 bytes)
   ```

2. **ASCII STL**:
   - Human-readable text format
   - Larger file size but editable

#### export/msh.js - Gmsh Mesh Export

**What it does:**
Exports mesh in Gmsh .msh format for BEM simulation.

**Format includes:**

1. Node coordinates (x, y, z)
2. Triangle elements (3 node indices each)
3. Physical group tags:
   - Tag 1 = throat (velocity BC)
   - Tag 2 = wall (Neumann BC)
   - Tag 4 = symmetry planes

---

## Investigation Prompts

Use these questions/prompts to explore specific parts of the code in more depth:

### Geometry & Math Models

| Question | Command/Context |
|----------|-----------------|
| How does the OSSE formula work? | `grep -n "computeOsseRadius" src/geometry/hornModels.js` |
| What's the difference between OSSE and R-OSSE? | Compare `calculateOSSE()` vs `calculateROSSE()` functions |
| Where are horn profiles mathematically defined? | Read lines 40-200 in `src/geometry/hornModels.js` |
| How does morphing round corners? | `grep -n "getRoundedRectRadius\|applyMorphing" src/geometry/morphing.js` |
| How are mesh slices distributed along horn length? | Read `buildSliceMap()` in `src/geometry/meshBuilder.js` |

### Mesh Generation

| Question | Command/Context |
|----------|-----------------|
| How are mesh slices distributed along horn length? | Read `buildSliceMap()` in `src/geometry/meshBuilder.js` |
| Why more triangles near the throat? | Throat resolution parameters control point density for accurate physics simulation |
| How does quadrants parameter work? | Search for "quadrants" and "selectAnglesForQuadrants" |
| Where are vertex coordinates calculated? | Look for `vx = r * Math.cos(p)` etc. in `buildHornMesh()` |

### CAD System

| Question | Command/Context |
|----------|-----------------|
| How does OpenCascade integration work? | Read `src/cad/cadManager.js` and `src/cad/cadBuilder.js` |
| What's the difference between mesh and CAD export? | Compare output formats: triangles vs B-Rep parametric |
| Where are cross-sections lofted? | Look for `ThruSections` in `cadBuilder.js` |

### Simulation (BEM)

| Question | Command/Context |
|----------|-----------------|
| How does symmetry detection work? | Read `detect_geometric_symmetry()` in `server/solver/symmetry.py` |
| What are the BEM operators (D, S)? | Search for "double_layer" and "single_layer" in solver files |
| Where is far-field pressure calculated? | Look for "potential" operators in `solve_optimized.py` |
| How are directivity patterns computed? | Read `calculate_directivity_patterns_correct()` |

### UI Components

| Question | Command/Context |
|----------|-----------------|
| How does parameter change trigger re-render? | Trace: slider → AppState.update() → AppEvents.emit() → renderModel() |
| Where are charts rendered? | Check `renderFrequencyResponseChart()`, etc. in `ui/simulation/charts.js` |
| How does smoothing work without re-running? | Read `smoothing.js` - applies post-processing to existing results |

### Export Systems

| Question | Command/Context |
|----------|-----------------|
| What's the structure of STL binary format? | Read comments in `export/stl.js` about buffer layout |
| How are triangle normals calculated? | Look for "edge1", "edge2" cross product in stl.js |
| Where are Gmsh physical groups defined? | Search for "physical tag" or "domain_index" |

### Testing & Validation

| Question | Command/Context |
|----------|-----------------|
| How to compare against ATH reference? | Run `node scripts/ath-compare.js` |
| Where are validation checks? | Look in `src/validation/` directory |
| What mesh quality metrics exist? | Read `server/solver/mesh_validation.py` |

### Performance & Optimization

| Question | Command/Context |
|----------|-----------------|
| How does operator caching work? | Read `CachedOperators` class in `solve_optimized.py` |
| Why is symmetry useful for BEM? | Reduces number of unknowns quadratically |
| Where is the simulation status polled? | Check `pollSimulationStatus()` in `ui/simulation/actions.js` |

---

## Quick Reference

### Key Mathematical Concepts

| Concept | Description |
|---------|-------------|
| **OSSE** | Oblate Spheroid Enclosed - horn with guiding curve determining mouth radius |
| **R-OSSE** | Round-over OSSE - variant with rounded throat transition |
| **Morphing** | Linear interpolation between circular and rectangular cross-sections |
### Important Parameter Groups

| Group | Key Parameters |
|-------|----------------|
| Geometry | L (length), a (mouth angle), r0 (throat radius), k, n, s |
| Morphing | morphTarget, morphWidth, morphHeight, morphCorner, morphRate |
| Mesh | lengthSegments, angularSegments, throatResolution, mouthResolution |

### File Formats

| Format | Purpose |
|--------|---------|
| `.mwg` | Application config (JSON with extra comments) |
| `.stl` | 3D model for printing (triangles only) |
| `.step` | Parametric CAD geometry (OpenCascade B-Rep) |
| `.msh` | Gmsh mesh (nodes + elements + tags) |

---

## Summary

This project implements a complete acoustic horn design and simulation tool with:

1. **Mathematical models** for OSSE/R-OSSE horn profiles
2. **Mesh generation** from mathematical formulas with non-uniform sampling
3. **CAD export** using OpenCascade for precise parametric geometry
4. **BEM simulation** using bempp-cl for acoustic predictions
5. **UI** with real-time visualization and result charts

The code is organized by functionality, with clear separation between:
- Geometry math (hornModels.js)
- Mesh building (meshBuilder.js)
- CAD generation (cadBuilder.js)
- Simulation (solve_optimized.py)
- UI rendering (App.js, panels)

Use the investigation prompts above to dive deeper into any specific component.