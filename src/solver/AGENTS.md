# Solver Module — AI Agent Context

## Purpose

Interface between browser app and Python BEM acoustic simulation backend.

## ⚠️ Current Status

**MOCK SOLVER — NO REAL PHYSICS**

The BEM solver currently returns fake deterministic data. Real physics requires:
- Python backend running at `localhost:8000`
- bempp-cl library installed
- Proper mesh preparation

## Files

| File | Purpose | Complexity |
|------|---------|------------|
| `index.js` | Public API, mock solver | Medium |
| `client.js` | HTTP client for backend | Simple |
| `meshExport.js` | Geometry → solver mesh | Medium |
| `resultParser.js` | Parse solver responses | Simple |
| `status.js` | Connection status management | Simple |

## Public API

```javascript
import {
  BemSolver,            // Main solver class
  mockBEMSolver,        // Mock for testing
  prepareMeshForBEM,    // Convert geometry
  parseResults          // Parse solver output
} from './solver/index.js';
```

## Backend API

```
POST /api/solve
  Body: { mesh, frequency_range, num_frequencies, sim_type }
  Response: { job_id }

GET /api/status/{job_id}
  Response: { status, progress }

GET /api/results/{job_id}
  Response: { directivity, impedance, spl_on_axis, di }
```

## Simulation Types

| Type | Description |
|------|-------------|
| `1` | Infinite baffle (horn in wall) |
| `2` | Free-standing (horn in open air) |

## For Simple Changes

1. Change backend URL → modify `client.js`
2. Add result field → modify `resultParser.js`
3. Update status logic → modify `status.js`

## For Complex Changes

Before modifying the solver interface:
1. Understand BEM boundary conditions
2. Read `server/solver.py` for backend logic
3. Test with mock data first

## Event Integration

```javascript
// Request mesh for simulation
AppEvents.emit('simulation:mesh-requested');

// Mesh is ready
AppEvents.on('simulation:mesh-ready', (meshData) => {
  solver.submitSimulation(config, meshData);
});

// Simulation complete
AppEvents.emit('simulation:complete', results);
```

## Boundary Conditions

- **Throat**: Acoustic source (pressure or velocity)
- **Horn walls**: Rigid (Neumann BC, sound-hard)
- **Mouth**: Open radiation (Robin BC)

## Python Backend Setup

```bash
cd server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Testing Connection

```bash
curl http://localhost:8000/health
# Expected: { "status": "ok", "solver": "bempp-cl" }
```
