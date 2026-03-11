# Architecture

This is the durable architecture reference for Waveguide Generator.
If this file and runtime code disagree, update the docs to match the code.

## Scope

Waveguide Generator has three stable runtime layers:

1. Frontend app/runtime in `src/`
2. Backend API/services in `server/`
3. Shared mesh/request contracts carried between them

Primary entry points:

- Frontend boot: `src/main.js`
- Frontend coordinator: `src/app/App.js`
- Backend API: `server/app.py`

## Layer Boundaries

Frontend layering:

- `src/app/` owns bootstrap, scene lifecycle, and top-level orchestration.
- `src/modules/` is the app-facing boundary into design, geometry, export, simulation, and UI coordination.
- `src/ui/` owns interaction details and panel rendering, but should call module boundaries instead of geometry/export/solver internals directly.
- `src/geometry/`, `src/export/`, and `src/solver/` are implementation packages behind module boundaries.

Backend layering:

- `server/api/` owns HTTP routes only.
- `server/services/` owns validation, orchestration, persistence coordination, and runtime state.
- `server/solver/` owns OCC meshing, payload validation, and BEM solve mechanics.

## Runtime Pipelines

Render pipeline:

1. UI mutates `GlobalState`.
2. `App` resolves prepared params through `DesignModule`.
3. `GeometryModule` returns shape definitions.
4. Three.js tessellation/rendering happens from that shape output.

Simulation pipeline:

1. UI requests canonical simulation mesh from the app.
2. `SimulationModule` builds the canonical payload and OCC adaptive request data.
3. `BemSolver.submitSimulation(...)` posts to `POST /api/solve`.
4. Frontend polls job status/results and restores task history from backend jobs or folder manifests.

Export pipeline:

1. Local STL / profile CSV / MWG config exports flow through `src/modules/export/useCases.js`.
2. OCC-authored `.msh` export uses `POST /api/mesh/build` only.
3. Completed-task result exports use the simulation bundle coordinator in `src/ui/simulation/exports.js`.
4. Folder-backed workspaces write bundle artifacts into task subfolders and persist manifest/index metadata.

## Durable Contracts

Geometry contract:

- Canonical payload keys are `vertices`, `indices`, `surfaceTags`, `format`, `boundaryConditions`, and `metadata`.
- Surface tags remain code-governed in `src/geometry/tags.js`.
- Source tag `2` must be present in every simulation payload.

Simulation/task-history contract:

- Backend solve path is required for real simulation; there is no supported mock fallback.
- Folder mode and backend-job mode are explicit source modes, not a mixed feed.
- Task manifests/index entries may persist `rating`, `exportedFiles`, and `autoExportCompletedAt`.

Export contract:

- `/api/mesh/build` returns Gmsh-authored `.msh` (and optional STL text), not `.geo`.
- Result bundle selection is settings-driven through stable string IDs.
- Auto-export runs once per completion transition and records its completion marker.

## Companion Docs

- Runtime map: `docs/PROJECT_DOCUMENTATION.md`
- Module contracts: `docs/modules/README.md`
- Active backlog: `docs/backlog.md`
