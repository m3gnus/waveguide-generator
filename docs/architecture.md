# Architecture

This is the durable architecture reference for Waveguide Generator.
If this file and runtime code disagree, update the docs to match the code.

## Overview

Waveguide Generator is a browser-based horn design tool with a FastAPI backend. Three stable runtime layers:

1. **Frontend** (vanilla JS + Three.js): Geometry rendering, UI, export orchestration in `src/`
2. **Backend** (FastAPI + Python): OCC meshing, BEM solve, job scheduling in `server/`
3. **Shared contracts**: Canonical mesh payloads, API request/response shapes

**Entry points**:
- Frontend: `src/main.js` → `src/app/App.js`
- Backend: `server/app.py` (FastAPI app on port 8000)

## Layer Boundaries

**Frontend**:
- `src/app/` — bootstrap, scene lifecycle, top-level event wiring
- `src/modules/` — public boundaries for design, geometry, export, simulation, UI coordination
  - Modules normalize state and route to implementation layer
- `src/ui/` — interaction, panel rendering (calls modules, not internals)
- `src/geometry/`, `src/export/`, `src/solver/` — implementation packages (private, behind module boundaries)

**Backend**:
- `server/api/routes_*.py` — HTTP handlers (routes only, no business logic)
- `server/services/*.py` — validation, job orchestration, state management, runtime checks
- `server/solver/` — OCC mesh generation, BEM assembly/solve, mesh validation

## Core Workflows

**Render pipeline** (viewport update):
1. UI parameter changes → `GlobalState`
2. `App.requestRender()` → `DesignModule` (parameter normalization)
3. `GeometryModule` builds shape definition
4. Three.js tessellates + renders in WebGL

**Simulation pipeline** (async job):
1. UI submission → `SimulationModule` (payload + OCC adaptive params)
2. `POST /api/solve` → backend job queue
3. Frontend polls `GET /api/status/{job_id}` + `GET /api/results/{job_id}`
4. Results cached in `GlobalState` + folder manifests (if workspace active)

**Export pipeline**:
- **Local exports**: STL/CSV/config via `ExportModule.useCases.js`
- **OCC mesh export**: `POST /api/mesh/build` → `.msh` file
- **Result bundles**: Auto/manual via `src/ui/simulation/exports.js` (multi-format, workspace-aware)

## Durable Contracts

**Geometry payload**:
- Required keys: `vertices`, `indices`, `surfaceTags`, `format`, `boundaryConditions`, `metadata`
- Surface tags code-owned in `src/geometry/tags.js` (`1`=wall, `2`=source, `3`=secondary, `4`=interface)
- Source tag `2` must exist in every simulation payload

**Simulation**:
- Backend `/api/solve` is required; no mock/fallback solver supported
- History uses one source mode: folder workspace (manifests only) OR backend jobs + cache (never mixed)
- Job metadata: `rating`, `exportedFiles`, `autoExportCompletedAt`

**Export**:
- `/api/mesh/build` returns `.msh` files only (not `.geo`)
- Result bundles: multi-format export coordinated via settings IDs
- Auto-export: runs once per completion, records timestamp marker

## Documentation Roadmap

**For architecture & design decisions**: `docs/architecture.md` (this file)
**For current implementation**: `docs/PROJECT_DOCUMENTATION.md`
**For module contracts**: `docs/modules/`
**For active work**: `docs/backlog.md`
