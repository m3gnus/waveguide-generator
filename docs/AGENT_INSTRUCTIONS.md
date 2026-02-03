# Agent Instructions - MWG - Mathematical Waveguide Generator Design Platform

This document provides clear instructions for AI agents (like Qwen Code 2.5) working on this project.

## Project Overview

The MWG - Mathematical Waveguide Generator Design Platform is a browser-based application for designing and simulating acoustic horn waveguides. It combines:
- **Real-time 3D visualization** (Three.js)
- **Parametric horn design** (OSSE, R-OSSE models)
- **BEM acoustic simulation** (Python backend with bempp-cl)
- **Export capabilities** (STL, Gmsh, MWG config, CSV)

## Quick Start

### Running the Application

1. **Start the development server:**
```bash
npm run dev
```
The app will be available at `http://localhost:3000`

2. **Start the BEM solver backend (optional):**
```bash
cd server
python app.py
```
The backend will run on `http://localhost:8000`

**Note:** The app works without the Python backend - geometry and export features remain functional. BEM simulation requires the backend.

### Running Tests

```bash
# Unit tests
npm test

# Unit tests
npm test
```

## Project Structure

```
├── index.html              # Main HTML entry point
├── style.css               # Global styles with CSS variables
├── src/
│   ├── main.js            # Application bootstrap
│   ├── events.js          # Event bus for module communication
│   ├── state.js           # Global state management
│   ├── geometry/          # Horn geometry calculations
│   │   ├── index.js       # Public API
│   │   ├── hornModels.js  # OSSE, R-OSSE implementations
│   │   ├── meshBuilder.js # Three.js mesh generation
│   │   ├── morphing.js    # Shape morphing
│   │   ├── enclosure.js   # Enclosure geometry
│   │   └── rollback.js    # Mouth rollback
│   ├── config/            # Configuration management
│   │   ├── index.js       # Public API
│   │   └── parser.js      # MWG config file parser
│   ├── viewer/            # 3D visualization
│   │   ├── index.js       # Public API
│   │   └── annotations.js # Dimension annotations
│   ├── export/            # Export functionality
│   │   ├── index.js       # Public API
│   │   ├── stl.js         # STL export
│   │   ├── mwgConfig.js   # MWG config export
│   │   ├── csv.js         # CSV profile export
│   │   └── msh.js         # Gmsh export
│   ├── solver/            # BEM solver interface
│   │   ├── index.js       # Public API
│   │   ├── client.js      # HTTP client for backend
│   │   ├── meshExport.js  # Mesh conversion
│   │   ├── bemMeshGenerator.js # BEM mesh generation
│   │   └── resultParser.js # Result parsing
│   ├── optimization/      # Parameter optimization
│   │   ├── index.js       # Public API
│   │   ├── parameterSpace.js # Parameter bounds
│   │   ├── objectiveFunctions.js # Scoring functions
│   │   ├── engine.js      # Optimization algorithms
│   │   └── api.js         # External API
│   ├── workflow/          # Workflow state machine
│   │   └── index.js       # Design workflow stages
│   ├── presets/           # Preset management
│   │   └── index.js       # Save/load presets
│   ├── validation/        # Result validation
│   │   └── index.js       # Reference comparison
│   ├── logging/           # Change tracking
│   │   └── index.js       # Agent/user action logging
│   ├── ai/                # AI-assisted design (STUBS)
│   │   ├── index.js       # Module entry
│   │   ├── knowledge/     # Design knowledge storage
│   │   ├── surrogate/     # Surrogate models
│   │   ├── optimization/  # Bayesian optimization
│   │   └── insights/      # Design insights
│   └── ui/                # User interface
│       ├── paramPanel.js  # Parameter controls
│       ├── simulationPanel.js # Simulation interface
│       └── fileOps.js     # File operations
├── server/                # Python BEM backend
│   ├── app.py            # FastAPI application
│   ├── solver/           # bempp-cl solver package
│   ├── requirements.txt  # Python dependencies
│   └── README.md         # Backend setup instructions
└── tests/
    └── unit/             # Unit tests
```

## Key Modules

### Geometry Module (`src/geometry/`)
Handles all horn mathematics and mesh generation.

**Key functions:**
- `buildHornMesh(params)` - Generate horn mesh from parameters
- `parseExpression(expr)` - Evaluate mathematical expressions

### Config Module (`src/config/`)
Parses and validates MWG configuration files.

**Key functions:**
- `MWGConfigParser.parse(content)` - Parse MWG config file
- `generateMWGConfigContent(params)` - Export MWG config

### Viewer Module (`src/viewer/`)
Manages Three.js scene and rendering.

**Key functions:**
- `createScene()` - Initialize Three.js scene
- `createPerspectiveCamera(aspect)` - Create camera

### Solver Module (`src/solver/`)
Interfaces with Python BEM backend.

**Key classes:**
- `BemSolver` - HTTP client for backend communication
- `SimulationPanel` - UI for simulation controls

### UI Module (`src/ui/`)
User interface components.

**Key classes:**
- `ParamPanel` - Parameter controls
- `SimulationPanel` - Simulation interface

## Event System

Modules communicate via the event bus (`src/events.js`):

```javascript
import { AppEvents } from './events.js';

// Emit event
AppEvents.emit('geometry:updated', { mesh, params });

// Listen for event
AppEvents.on('geometry:updated', (data) => {
    console.log('Geometry updated:', data);
});
```

**Key events:**
- `state:updated` - Global state changed
- `geometry:updated` - Horn geometry regenerated
- `simulation:mesh-requested` - Simulation needs mesh data
- `simulation:mesh-ready` - Mesh data available

## State Management

Global state is managed through `GlobalState` (`src/state.js`):

```javascript
import { GlobalState } from './state.js';

// Get current state
const state = GlobalState.get();

// Update state
GlobalState.update({ r0: 15.0 }, 'R-OSSE');

// Undo/Redo
GlobalState.undo();
GlobalState.redo();
```

## Adding New Features

### Adding a New Horn Model

1. Add model implementation to `src/geometry/hornModels.js`
2. Update `buildHornMesh()` in `src/geometry/meshBuilder.js`
3. Add parameter schema to `src/config/schema.js` (if exists)
4. Update UI in `src/ui/paramPanel.js`

### Adding a New Export Format

1. Create new file in `src/export/` (e.g., `newFormat.js`)
2. Implement export function
3. Export from `src/export/index.js`
4. Add button in `index.html`
5. Wire up in `src/main.js`

### Adding a New Visualization Mode

1. Add shader/material to `src/viewer/index.js`
2. Add option to display mode dropdown in `index.html`
3. Handle in `renderModel()` in `src/main.js`

## BEM Simulation Setup

### Installing Python Backend

```bash
cd server

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install bempp-cl
pip install git+https://github.com/bempp/bempp-cl.git

# Run server
python app.py
```

### Testing Backend Connection

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "solver": "bempp-cl",
  "timestamp": "2026-01-30T20:00:00"
}
```

## Common Tasks

### Debugging

1. **Check browser console** - Most errors appear here
2. **Check network tab** - For API communication issues
3. **Check Python logs** - For backend errors

### Testing Changes

1. Make changes to source files
2. Refresh browser (dev server has live reload)
3. Run tests: `npm test`

### Building for Production

```bash
npm run build
```

Output will be in `dist/` directory.

## Architecture Principles

1. **Module Isolation** - Each module has single responsibility
2. **Event-Driven** - Modules communicate via event bus
3. **Serializable State** - All state can be saved/loaded
4. **Progressive Enhancement** - App works without backend
5. **AI-Friendly** - Small files (<300 lines), clear APIs

## Important Files

- `ARCHITECTURE.md` - Detailed architecture documentation
- `AI_GUIDANCE.md` - AI collaboration guidelines
- `README.md` - Project overview
- `server/README.md` - Backend setup instructions

## Troubleshooting

### "Module not found" errors
- Check import paths are correct
- Ensure file exists in expected location
- Check for typos in filenames

### Three.js rendering issues
- Check WebGL is supported in browser
- Verify mesh has valid vertices and indices
- Check normals are computed

### BEM simulation not working
- Verify Python backend is running
- Check CORS is enabled
- Ensure bempp-cl is installed correctly

### State not updating
- Check `GlobalState.update()` is called
- Verify event listeners are attached
- Check for JavaScript errors in console

## Best Practices

1. **Keep files small** - Under 300 lines when possible
2. **Use JSDoc comments** - Document public functions
3. **Test changes** - Run unit and E2E tests
4. **Follow naming conventions** - camelCase for JS, kebab-case for CSS
5. **Use event bus** - Don't directly call between modules
6. **Handle errors gracefully** - Show user-friendly messages

## Getting Help

- Read `ARCHITECTURE.md` for detailed design
- Check `AI_GUIDANCE.md` for AI collaboration tips
- Review existing code for patterns
- Test incrementally - don't make large changes at once

## Version Information

- **Node.js**: 14+ required
- **Python**: 3.8+ required (for backend)
- **Three.js**: 0.160.0
- **bempp-cl**: 0.2.3 (install from git)

## Current Implementation Status

**Version:** 1.0.0-alpha-7.5

| Phase | Status | Description |
|-------|--------|-------------|
| 0-3 | ✅ Complete | Core modules, config, geometry, export |
| 4 | ⚠️ 70% | BEM solver (code complete, needs validation) |
| 5 | ✅ Complete | Optimization engine |
| 6 | ✅ Complete | Workflow, presets, validation framework |
| 7 | ⚠️ 20% | AI modules (stubs only) |

**What's Working:**
- ✅ Geometry visualization (OSSE, R-OSSE, morphing, enclosure, rollback)
- ✅ Parameter controls with schema validation
- ✅ Export (MWG config, Gmsh .msh, CSV profiles)
- ✅ BEM simulation UI and Python backend
- ✅ Optimization engine (grid, random, coordinate descent)
- ✅ Workflow state machine
- ✅ Preset save/load
- ✅ Validation framework
- ✅ Change logging

**Needs Work:**
- ⚠️ BEM solver validation against ABEC references
- ⚠️ Results visualization module (polar plots, sonograms)
- ⚠️ AI modules (currently stubs)

**Priority Tasks:**
1. Validate BEM results against known ABEC data
2. Implement results visualization (`src/results/`)
3. Implement AI modules with real GP/Bayesian optimization
