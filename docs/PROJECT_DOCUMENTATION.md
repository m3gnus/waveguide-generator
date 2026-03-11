# Waveguide Generator: Project Documentation

This document describes the current implementation in this repository.
If code and docs disagree, update this file to match runtime behavior.

Active cleanup roadmap:
- `docs/ARCHITECTURE_CLEANUP_PLAN.md`

Canonical contract freeze:
- `docs/CANONICAL_CONTRACT.md`

## 1. Scope and Entry Points

Waveguide Generator is a browser-based horn design tool with:
- Parametric OSSE / R-OSSE geometry generation
- Real-time Three.js rendering
- Canonical mesh payload generation for BEM simulation
- Backend meshing and solve APIs (FastAPI + gmsh + bempp)
- STL / profile CSV / MWG config export workflows

Primary entry points:
- Frontend boot: `src/main.js`
- Frontend coordinator: `src/app/App.js`
- Backend API: `server/app.py`

## 2. Runtime Architecture

### 2.1 Frontend

- `src/app/`
  - App orchestration, event wiring, scene lifecycle
- `src/geometry/`
  - Formula evaluation, mesh topology generation, tag assignment, canonical payload assembly
- `src/modules/`
  - Staged facades for design prep, geometry, export, simulation, and UI coordination
  - `DesignModule` is the app-facing boundary for state/type -> prepared parameter normalization
  - `DesignModule` also owns OCC request normalization helpers used by `ExportModule` and `SimulationModule`
  - `GeometryModule` prepares geometry-shape definitions only (no tessellation or payload assembly)
- `src/export/`
  - Active STL/profile/config export helpers plus OCC mesh-build orchestration support
- `src/solver/`
  - Backend client and payload validation
- `src/ui/`
  - Parameter and simulation UI behavior
- `src/state.js`
  - Global app state, undo/redo, persistence

### 2.2 Backend

- `server/app.py`
  - FastAPI app assembly, router registration, lifecycle wiring
- `server/contracts/`
  - Shared Pydantic request/response contracts consumed by routes, services, and backend tests
- `server/api/routes_simulation.py`
  - Simulation/job routes (`/api/solve`, `/api/status/{job_id}`, `/api/results/{job_id}`, `/api/jobs*`)
- `server/api/routes_mesh.py`
  - Mesh routes (`/api/mesh/build`)
- `server/api/routes_misc.py`
  - Misc routes (`/`, `/health`, `/api/updates/check`, chart/directivity rendering)
- `server/services/job_runtime.py`
  - In-memory job cache, queue, scheduler loop, DB merge helpers
- `server/services/solver_runtime.py`
  - Service-layer adapter for solver availability flags, dependency status, OCC builder access, and device metadata
- `server/services/simulation_runner.py`
  - Async single-job execution and persistence flow
- `server/services/simulation_validation.py`
  - Domain validation helpers for `/api/solve` request semantics and OCC shell requirements
- `server/services/update_service.py`
  - Git-backed update status checks
- `server/solver/waveguide_builder.py`
  - OCC-based mesh construction from ATH parameters (`/api/mesh/build`)
- `server/solver/mesh.py`
  - Canonical payload integrity checks and optional gmsh refinement
- `server/solver/bem_solver.py`, `solve.py`, `solve_optimized.py`
  - BEM solve pipeline and optimized path
- `server/solver/deps.py`
  - Runtime dependency/version gating

### 2.3 Overview Diagram

```mermaid
flowchart LR
  App["src/app<br/>assembly + event wiring"] --> Modules["src/modules/*<br/>public frontend boundaries"]
  Modules --> Design["DesignModule<br/>state/import normalization"]
  Modules --> Geometry["Geometry internals<br/>shape + canonical payload"]
  Modules --> SolverClient["src/solver<br/>backend client + preflight validation"]
  Modules --> UiRuntime["src/ui<br/>panels + coordinators"]
  Geometry --> Viewer["src/viewer + Three.js"]
  SolverClient --> Api["server/api<br/>HTTP boundary"]
  Api --> Services["server/services<br/>validation + job orchestration"]
  Services --> SolverDomain["server/solver<br/>OCC meshing + BEM solve"]
  SolverDomain --> MeshBuild["/api/mesh/build"]
  SolverDomain --> Solve["/api/solve"]
```

## 3. Core Flows

### 3.1 Render flow

1. UI parameter updates mutate `GlobalState`.
2. `App` schedules render.
3. `src/app/scene.js` resolves prepared design inputs via `DesignModule`, gets a geometry shape from `GeometryModule`, then tessellates it for viewport rendering.
4. Returned mesh is rendered in Three.js.

### 3.2 Simulation flow

1. Simulation UI emits `simulation:mesh-requested`.
2. `src/app/mesh.js` resolves prepared design inputs via `DesignModule`, and `SimulationModule` builds canonical payload from those inputs before emitting `simulation:mesh-ready`.
   - For OCC-adaptive `/api/solve`, frontend may send `waveguide_params.quadrants`; the backend submission boundary builds a queued full-domain OCC request with `quadrants=1234`.
3. `BemSolver.submitSimulation(...)` posts payload to `POST /api/solve` with adaptive mesh strategy:
   - `options.mesh.strategy = "occ_adaptive"`
   - `options.mesh.waveguide_params = WaveguideParamsRequest-compatible payload`
   - `device_mode = auto` (UI always delegates selection to backend policy)
   - Auto policy priority is deterministic: `opencl_gpu -> opencl_cpu -> numba`
4. Frontend polls `GET /api/status/{job_id}` and reads `GET /api/results/{job_id}` on completion.
   - Frontend also reconciles against `GET /api/jobs` to restore queued/running/history state after reload.
5. If backend solver/OCC runtime is unavailable, simulation start fails with an explicit runtime error (no mock fallback).

### 3.3 Export flow

1. Local file exports (`exportSTL`, `exportMWGConfig`, `exportProfileCSV`) run through `src/modules/export/useCases.js`.
2. OCC-backed mesh export uses `prepareExportArtifacts(...)`, which normalizes export params through `DesignModule` and requests `POST /api/mesh/build`.
3. If `/api/mesh/build` returns `503`, the export path fails explicitly and does not fall back to a legacy frontend mesher.
4. ABEC bundle generation is not part of the active runtime; remaining ABEC compatibility is limited to config/result text conventions used by import/export helpers.

## 4. Mesh Pipelines

### 4.1 JS canonical geometry/payload pipeline

Primary files:
- `src/geometry/engine/*`
- `src/geometry/pipeline.js`
- `src/geometry/tags.js`

`buildGeometryArtifacts(...)` returns:
- `geometry` shape definition used as tessellation input
- `mesh` for render/export helpers
- `simulation` canonical payload with tags/BC metadata
- `export` helpers (ATH coordinate transform)

Canonical surface tags:
- `1` = wall
- `2` = source
- `3` = secondary domain (reserved in JS canonical runtime)
- `4` = interface (reserved in JS canonical runtime)

Important behavior:
- Source triangles are explicit geometry and required; payload build throws if none are tagged.
- JS canonical payload currently emits only tags `1` and `2`; tag counters for `3`/`4` remain zero in runtime tests.
- Simulation payload topology is full-domain and does not trim by `quadrants`.
- OCC-adaptive `/api/solve` builds a full-domain queued OCC request with `quadrants=1234` at the submission boundary instead of mutating the caller-owned request in place.
- `/api/solve` rejects mesh payloads that do not already contain source tag `2`, instead of waiting for solver-side mesh preparation to fail.
- The OCC runner passes canonical mesh `surfaceTags` through unchanged; later stages validate contracts rather than collapsing non-source tags into `1`.
- Adaptive phi tessellation is restricted to full-circle horn-only render usage.
- The canonical frontend payload remains a validation/contract artifact; active simulation meshing is OCC-adaptive in backend.

### 4.2 OCC parameter-to-`.msh` pipeline (`/api/mesh/build`)

Frontend call path:
- `src/modules/export/useCases.js` -> `prepareExportArtifacts(...)`

Backend implementation:
- `server/api/routes_mesh.py` route `POST /api/mesh/build`
- `server/solver/waveguide_builder.py` function `build_waveguide_mesh(...)`

Frontend request normalization:
- OCC request normalization is owned by `DesignModule`:
  - `DesignModule.output.occSimulationParams(...)` normalizes simulation OCC inputs (min/rounded segment counts, canonical quadrants, mesh-resolution defaults).
  - `DesignModule.output.occExportParams(...)` adds export-specific OCC normalization (angular snapping to multiples of 4 and scaled/coarse export resolutions).
  - `buildWaveguidePayload(...)` maps already-normalized OCC fields to request schema and enforces payload shape for required OCC fields.

Response shape:
```json
{
  "msh": "...",
  "generatedBy": "gmsh-occ",
  "stats": { "nodeCount": 0, "elementCount": 0 },
  "stl": "...optional..."
}
```

Validation/gating:
- `formula_type` must be `"R-OSSE"` or `"OSSE"` (`422` otherwise)
- `msh_version` must be `"2.2"` or `"4.1"` (`422` otherwise)
- Returns `503` when Python/gmsh runtime matrix is unsupported or OCC builder unavailable

OCC geometry logic:
- `enc_depth > 0`: enclosure geometry generated
- `enc_depth == 0` and `wall_thickness > 0`: freestanding wall shell generated
- both zero: bare horn
- `sim_type` does not control geometry generation in OCC builder
- `subdomain_slices` / `interface_*` fields are accepted in request payload but are not currently used to create OCC interface geometry

OCC mesh-resolution semantics:
- `throat_res`: nominal element size at throat plane.
- `mouth_res`: nominal element size at mouth plane.
- Horn surfaces use smooth axial interpolation `throat_res -> mouth_res`.
- `rear_res`: rear-wall size for freestanding thickened horns (no enclosure).
- `enc_front_resolution` / `enc_back_resolution`:
  comma list (`q1,q2,q3,q4`) or scalar broadcast for enclosure front/back baffle corners.
  Quadrant mapping: `Q1(+x,+y)`, `Q2(-x,+y)`, `Q3(-x,-y)`, `Q4(+x,-y)`.

Physical groups written by OCC builder:
- tag 1: `SD1G0`
- tag 2: `SD1D1001`
- tag 3: `SD2G0` (when exterior surfaces exist)

## 5. Export System

### 5.1 UI-exposed exports

Active runtime export surfaces:
- App-level exports: STL (`exportSTL`), MWG config text (`exportMWGConfig`), and profile/slice CSV (`exportProfileCSV`)
- Simulation-result exports: VACS spectrum text plus waveguide STL helpers in `src/ui/simulation/exports.js`
- Completed-job mesh download: `.msh` artifact fetch via `src/ui/simulation/meshDownload.js` when backend jobs persist mesh artifacts

ABEC bundle export is removed from the active runtime. The live solver path is fully backend-driven via `/api/solve`.

### 5.2 CSV profile/slice export

`exportProfileCSV` in `src/modules/export/useCases.js` reads the viewport horn mesh and writes two CSV files via `src/export/profiles.js`:

- **`_profiles.csv`**: For each angular position (fixed phi), lists all points from throat to mouth along the horn axis. Sections separated by blank lines.
- **`_slices.csv`**: For each axial position (fixed z), lists all points around the circumference (closing back to phi=0). Sections separated by blank lines.

Format: `x;y;z` (semicolon-delimited, no header), scaled by 0.1 (matching ATH `GridExport` convention). Coordinates are x=r·cos(phi), y=r·sin(phi), z=axial.

The mesh builder normalizes `angularSegments` to the nearest compatible multiple (via `normalizeAngularSegments` in `src/geometry/engine/mesh/angles.js`). The export must use this normalized ring count as the vertex stride, not the raw config value.

Regression coverage: `tests/csv-export.test.js`

### 5.3 Internal/library export utilities

Additional export utilities exist in `src/export/*` (tests/tooling/legacy helpers), including `.geo` and direct `.msh` builders. The OCC runtime export flow remains backend-meshed and Gmsh-authored.

## 6. Backend API Contract

Base URL: `http://localhost:8000`

- `GET /`
  - Basic service metadata and solver availability flag

- `GET /health`
  - Health status + dependency matrix/runtime payload from `deps.py`
  - Includes `deviceInterface` metadata for current device policy resolution:
    - `requested_mode`, `selected_mode`
    - `interface` (`opencl` or `numba`)
    - `device_type` (`cpu` or `gpu`)
    - `device_name`
    - `fallback_reason`
    - `available_modes`
    - `mode_availability` (per-mode `available` + `reason`)
    - `opencl_diagnostics` (base/platform/cpu/gpu OpenCL detection details)

- `GET /api/updates/check`
  - Git remote/update check against `origin`

- `POST /api/mesh/build`
  - Input: `WaveguideParamsRequest` payload (ATH-style params)
  - Output: `{ msh, generatedBy: "gmsh-occ", stats, stl? }`
  - Note: does not return `.geo`

- `POST /api/solve`
  - Validates mesh array lengths and `surfaceTags` triangle parity
  - Validates `sim_type == "2"` (infinite-baffle path currently deferred)
  - Supports adaptive OCC simulation meshing through `options.mesh.strategy="occ_adaptive"`
    with required `options.mesh.waveguide_params`
  - Supports `polar_config.enabled_axes` (`horizontal|vertical|diagonal`, at least one required)
    and `polar_config.inclination` (diagonal plane angle)
  - Supports `mesh_validation_mode` (`strict`, `warn`, `off`)
  - Supports `device_mode` (`auto`, `opencl_cpu`, `opencl_gpu`, `numba`)
  - Creates async job and returns `{ job_id }`
  - Backend schedules jobs FIFO with `max_concurrent_jobs=1` by default

- `GET /api/jobs`
  - Lists jobs with optional `status` filter and `limit`/`offset` pagination
  - Returns compact job metadata, status/progress/stage timestamps, and `has_results`/`has_mesh_artifact`

- `DELETE /api/jobs/{job_id}`
  - Deletes terminal jobs (`complete|error|cancelled`)
  - Returns `409` for active jobs (`queued|running`)

- `POST /api/stop/{job_id}`
  - Cancels queued/running job

- `GET /api/status/{job_id}`
  - Returns status/progress

- `GET /api/results/{job_id}`
  - Returns results for completed jobs

## 7. Solver Runtime and Dependency Matrix

Runtime-gated matrix in `server/solver/deps.py`:

| Component | Supported range | Required for |
|---|---|---|
| Python | `>=3.10,<3.15` | backend runtime |
| gmsh Python package | `>=4.11,<5.0` | `/api/mesh/build` |
| bempp-cl | `>=0.4,<0.5` | `/api/solve` |
| legacy `bempp_api` | `>=0.3,<0.4` | `/api/solve (legacy fallback)` |

Notes:
- Backend solve path defaults to optimized solve mode (`use_optimized=True` in request model).
- Solver internals normalize mesh coordinates to meters before BEM assembly.
- Device policy defaults to `auto` with deterministic priority: `opencl_gpu`, then `opencl_cpu`, then `numba`.
- Startup auto benchmarking is disabled; mode resolution is based on runtime availability checks.
- Strong-form GMRES (`use_strong_form=True`) is enabled by default when the installed bempp runtime supports it (bempp-cl ≥ 0.4). Support is feature-detected once at import time.
- The runtime prefers `bempp-cl`; the legacy `bempp_api` entry only documents the compatibility lane still encoded in `server/solver/deps.py`.

### 7.1 Solver performance metadata

Every `/api/solve` result includes `metadata.performance`:

| Field | Type | Description |
|---|---|---|
| `total_time_seconds` | float | Wall time for full solve |
| `frequency_solve_time` | float | Time spent in frequency loop |
| `directivity_compute_time` | float | Time for directivity post-processing |
| `time_per_frequency` | float | Average per-frequency solve time |
| `warmup_time_seconds` | float | Warm-up pass duration (0 if skipped) |
| `gmres_iterations_per_frequency` | list[int\|null] | GMRES iteration count per frequency; `null` for failed frequencies |
| `avg_gmres_iterations` | float | Mean iteration count across successful frequencies |
| `gmres_strong_form_supported` | bool | Whether strong-form preconditioner was active for this run |
| `reduction_speedup` | float | Symmetry reduction factor applied (1.0 = no reduction) |

## 8. Canonical Mesh Payload Contract

Normative reference:
- `docs/CANONICAL_CONTRACT.md`

Frontend payload shape sent to `/api/solve`:

```json
{
  "vertices": [0.0, 0.0, 0.0],
  "indices": [0, 1, 2],
  "surfaceTags": [2],
  "format": "msh",
  "boundaryConditions": {
    "throat": { "type": "velocity", "surfaceTag": 2, "value": 1.0 },
    "wall": { "type": "neumann", "surfaceTag": 1, "value": 0.0 },
    "mouth": { "type": "robin", "surfaceTag": 1, "impedance": "spherical" }
  },
  "metadata": {
    "units": "mm",
    "unitScaleToMeter": 0.001
  }
}
```

Optional directivity payload for `/api/solve`:

```json
{
  "polar_config": {
    "angle_range": [0, 180, 37],
    "norm_angle": 5,
    "distance": 2,
    "inclination": 45,
    "enabled_axes": ["horizontal", "vertical", "diagonal"]
  }
}
```

Optional device selection payload for `/api/solve`:

```json
{
  "device_mode": "auto"
}
```

Validation points:
- Frontend: `src/solver/index.js` (`validateCanonicalMeshPayload`)
- Backend request validation: `server/api/routes_simulation.py`
- Backend mesh integrity checks: `server/solver/mesh.py`
- Backend results surface failures in `metadata.failures`, `metadata.failure_count`, and `metadata.partial_success`

## 9. Testing and Verification

Canonical inventory and test-location map: `tests/TESTING.md`.

Primary commands:
- `npm test`
- `npm run test:server`
- `npm run build`

High-signal test suites:
- Geometry/tagging: `tests/mesh-payload.test.js`, `tests/geometry-artifacts.test.js`, `tests/enclosure-regression.test.js`
- Export/OCC pipeline: `tests/export-gmsh-pipeline.test.js`, `tests/polar-settings.test.js`
- Backend contracts: `server/tests/test_dependency_runtime.py`, `server/tests/test_api_validation.py`, `server/tests/test_solver_tag_contract.py`, `server/tests/test_directivity_plot.py`

## 10. Operational Notes and Constraints

- Frontend dev server: `http://localhost:3000` (`scripts/dev-server.js`)
- Backend API server: `http://localhost:8000` (`server/app.py`)
- Combined startup script: `npm start` (`scripts/start-all.js`)
- Backend jobs are in-memory; restarting backend clears job history.
- gmsh Python API calls are guarded for thread-safety and main-thread constraints.

### OpenCL / pyopencl setup

`pyopencl` is required for `bempp-cl` GPU/CPU-OpenCL acceleration. Fully automatic cross-platform driver install is not supported (vendor/admin/reboot constraints). Install manually:

- **macOS (Apple Silicon)**: `./scripts/setup-opencl-backend.sh` — installs `pocl` CPU runtime.
- **Windows**: Install vendor drivers (NVIDIA/AMD/Intel). Intel provides a standalone "CPU Runtime for OpenCL Applications" for CPU-only use.
- **Linux**: `apt install pocl-opencl-icd` (CPU) or vendor-specific ICDs.

If OpenCL is unavailable the backend falls back to `numba`; fallback reason is surfaced in `/health` under `deviceInterface.fallback_reason`.

## 11. Key File Map

- App orchestration: `src/app/App.js`
- Scene/render path: `src/app/scene.js`
- Simulation mesh provider: `src/app/mesh.js`
- Geometry artifacts/payload: `src/geometry/pipeline.js`
- Surface tag rules: `src/geometry/tags.js`
- Export use cases: `src/modules/export/useCases.js`
- Simulation panel controller/store: `src/ui/simulation/controller.js`
- Simulation job orchestration helpers: `src/ui/simulation/jobOrchestration.js`
- Polar UI/helpers: `src/ui/simulation/polarSettings.js`
- FastAPI app wiring: `server/app.py`
- Simulation routes: `server/api/routes_simulation.py`
- Mesh routes: `server/api/routes_mesh.py`
- Misc routes: `server/api/routes_misc.py`
- Job runtime scheduler/state: `server/services/job_runtime.py`
- Simulation runner: `server/services/simulation_runner.py`
- OCC builder: `server/solver/waveguide_builder.py`
- Directivity render: `server/solver/directivity_plot.py`
- Solver dependency matrix: `server/solver/deps.py`

## 12. Recent Major Refactors (Feb-Mar 2026)

### 12.1 OCC Meshing Consolidation
- Removed legacy frontend `.geo` mesh-build fallbacks from active runtime flows.
- `POST /api/mesh/build` is the only supported runtime path for OCC-authored `.msh` export artifacts.

### 12.2 Frontend Boundary Cleanup
- App assembly now routes render, export, simulation, and panel setup through module entry points.
- Deprecated solver/export/geometry alias entry points were removed during Phase 8 cleanup.

### 12.3 Export Surface Simplification
- Active runtime exports are STL, MWG config text, profile/slice CSV, simulation-result text exports, and persisted simulation mesh downloads.
- ABEC bundle export is no longer part of the shipped runtime.

### 12.4 Backend Router/Service Decomposition
- `server/app.py` was reduced to app assembly, CORS setup, router registration, and lifecycle startup.
- Route handlers now live in `server/api/routes_*.py`.
- Runtime orchestration/state, solver-runtime adapters, and request validation now live in `server/services/*`.

---

## 13. Future Work Tracking

Planned or partial features are tracked in [docs/FUTURE_ADDITIONS.md](docs/FUTURE_ADDITIONS.md).
