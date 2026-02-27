# Waveguide Generator: Project Documentation

This document describes the current implementation in this repository.
If code and docs disagree, update this file to match runtime behavior.

## 1. Scope and Entry Points

Waveguide Generator is a browser-based horn design tool with:
- Parametric OSSE / R-OSSE geometry generation
- Real-time Three.js rendering
- Canonical mesh payload generation for BEM simulation
- Backend meshing and solve APIs (FastAPI + gmsh + bempp)
- ABEC bundle export workflow

Primary entry points:
- Frontend boot: `src/main.js`
- Frontend coordinator: `src/app/App.js`
- Backend API: `server/app.py`

## 2. Runtime Architecture

### 2.1 Frontend

- `src/app/`
  - App orchestration, event wiring, scene lifecycle, export orchestration
- `src/geometry/`
  - Formula evaluation, mesh topology generation, tag assignment, canonical payload assembly
- `src/export/`
  - `.geo` builder, ABEC file generators, bundle validator, STL/CSV helpers
- `src/solver/`
  - Backend client and payload validation
- `src/ui/`
  - Parameter and simulation UI behavior
- `src/state.js`
  - Global app state, undo/redo, persistence

### 2.2 Backend

- `server/app.py`
  - FastAPI app assembly, router registration, lifecycle wiring
- `server/api/routes_simulation.py`
  - Simulation/job routes (`/api/solve`, `/api/status/{job_id}`, `/api/results/{job_id}`, `/api/jobs*`)
- `server/api/routes_mesh.py`
  - Mesh routes (`/api/mesh/build`, `/api/mesh/generate-msh`)
- `server/api/routes_misc.py`
  - Misc routes (`/`, `/health`, `/api/updates/check`, chart/directivity rendering)
- `server/services/job_runtime.py`
  - In-memory job cache, queue, scheduler loop, DB merge helpers
- `server/services/simulation_runner.py`
  - Async single-job execution and persistence flow
- `server/services/update_service.py`
  - Git-backed update status checks
- `server/solver/waveguide_builder.py`
  - OCC-based mesh construction from ATH parameters (`/api/mesh/build`)
- `server/solver/gmsh_geo_mesher.py`
  - Legacy `.geo -> .msh` backend mesher (`/api/mesh/generate-msh`)
- `server/solver/mesh.py`
  - Canonical payload integrity checks and optional gmsh refinement
- `server/solver/bem_solver.py`, `solve.py`, `solve_optimized.py`
  - BEM solve pipeline and optimized path
- `server/solver/deps.py`
  - Runtime dependency/version gating

## 3. Core Flows

### 3.1 Render flow

1. UI parameter updates mutate `GlobalState`.
2. `App` schedules render.
3. `src/app/scene.js` calls `buildGeometryArtifacts(...)`.
4. Returned mesh is rendered in Three.js.

### 3.2 Simulation flow

1. Simulation UI emits `simulation:mesh-requested`.
2. `src/app/mesh.js` builds canonical payload through `buildGeometryArtifacts(...)` and emits `simulation:mesh-ready`.
   - For `/api/solve`, frontend forces `quadrants='1234'` so backend symmetry detection/reduction is the source of truth.
3. `BemSolver.submitSimulation(...)` posts payload to `POST /api/solve` with adaptive mesh strategy:
   - `options.mesh.strategy = "occ_adaptive"`
   - `options.mesh.waveguide_params = WaveguideParamsRequest-compatible payload`
   - `device_mode = auto` (UI always delegates selection to backend policy)
   - Auto policy priority is deterministic: `opencl_gpu -> opencl_cpu -> numba`
4. Frontend polls `GET /api/status/{job_id}` and reads `GET /api/results/{job_id}` on completion.
   - Frontend also reconciles against `GET /api/jobs` to restore queued/running/history state after reload.
5. If backend solver/OCC runtime is unavailable, simulation start fails with an explicit runtime error (no mock fallback).

### 3.3 ABEC export flow

1. `exportABECProject(...)` prepares params and auto-detects symmetry (`detectGeometrySymmetry`).
2. Export requests `POST /api/mesh/build` (OCC path).
3. If OCC endpoint returns `503`, export fails and surfaces the backend error.
4. Bundle is assembled in-browser with returned `.msh` plus generated ABEC text files and `bem_mesh.geo`.

## 4. Mesh Pipelines

### 4.1 JS canonical geometry/payload pipeline

Primary files:
- `src/geometry/engine/*`
- `src/geometry/pipeline.js`
- `src/geometry/tags.js`

`buildGeometryArtifacts(...)` returns:
- `mesh` for render/export helpers
- `simulation` canonical payload with tags/BC metadata
- `export` helpers (ATH coordinate transform)

Canonical surface tags:
- `1` = wall
- `2` = source
- `3` = secondary domain
- `4` = interface

Important behavior:
- Source triangles are explicit geometry and required; payload build throws if none are tagged.
- Interface tags are only emitted when enclosure exists and `interfaceOffset` is positive.
- Symmetry-domain payloads remove triangles that lie on split planes.
- Live `/api/solve` submission forces full quadrants and delegates symmetry reduction to backend solver logic.
- Adaptive phi tessellation is restricted to full-circle horn-only render usage.
- The canonical frontend payload remains a validation/contract artifact; active simulation meshing is OCC-adaptive in backend.

### 4.2 OCC parameter-to-`.msh` pipeline (`/api/mesh/build`)

Frontend call path:
- `src/app/exports.js` -> `buildExportMeshFromParams(...)`

Backend implementation:
- `server/app.py` route `POST /api/mesh/build`
- `server/solver/waveguide_builder.py` function `build_waveguide_mesh(...)`

Frontend request normalization:
- Angular/length counts are normalized before submit:
  - `n_angular`: rounded, snapped to multiple of 4, minimum 20
  - `n_length`: rounded, minimum 10

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

### 4.3 Legacy `.geo -> .msh` pipeline (`/api/mesh/generate-msh`)

Frontend/tooling call path:
- `.geo` text is client-provided (tests/tooling/manual payloads); no runtime frontend `.geo` builder module
- consumed by legacy tooling/tests via `POST /api/mesh/generate-msh`

Backend implementation:
- `server/app.py` route `POST /api/mesh/generate-msh`
- `server/solver/gmsh_geo_mesher.py`

Behavior:
- Backend returns Gmsh-authored `.msh` with `generatedBy: "gmsh"`
- Uses gmsh Python API when available
- Falls back to gmsh CLI if Python API is unavailable but `gmsh` is on PATH

## 5. Export System

### 5.1 UI-exposed exports

Buttons wired in `src/app/events.js` currently export:
- STL (`exportSTL`)
- MWG config text (`exportMWGConfig`)
- Profile CSV (`exportProfileCSV`) — writes `_profiles.csv` and `_slices.csv`
- MSH (`exportMSH`)

ABEC export was removed in Feb 2026. The BEM solver is now fully backend-driven via `/api/solve`.

### 5.2 CSV profile/slice export

`exportProfileCSV` in `src/app/exports.js` reads the viewport horn mesh and writes two CSV files via `src/export/profiles.js`:

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

- `POST /api/mesh/generate-msh`
  - Input: `{ geoText, mshVersion, binary }`
  - Output: `{ msh, generatedBy: "gmsh", stats }`

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
| gmsh Python package | `>=4.15,<5.0` | `/api/mesh/build` |
| bempp-cl | `>=0.4,<0.5` | `/api/solve` |
| legacy `bempp_api` | `>=0.3,<0.4` | `/api/solve (legacy fallback)` |

Notes:
- `/api/mesh/generate-msh` can still operate via gmsh CLI even when Python gmsh package is absent.
- Backend solve path defaults to optimized solve mode (`use_optimized=True` in request model).
- Solver internals normalize mesh coordinates to meters before BEM assembly.
- Device policy defaults to `auto` with deterministic priority: `opencl_gpu`, then `opencl_cpu`, then `numba`.
- Startup auto benchmarking is disabled; mode resolution is based on runtime availability checks.
- Strong-form GMRES (`use_strong_form=True`) is enabled by default when the installed bempp runtime supports it (bempp-cl ≥ 0.4). Support is feature-detected once at import time.

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
- Export/ABEC: `tests/export-gmsh-pipeline.test.js`, `tests/polar-settings.test.js`
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
- Export orchestration: `src/app/exports.js`
- Polar UI/helpers: `src/ui/simulation/polarSettings.js`
- Legacy `.geo` request helper: `src/solver/client.js` (`generateMeshFromGeo`)
- FastAPI app wiring: `server/app.py`
- Simulation routes: `server/api/routes_simulation.py`
- Mesh routes: `server/api/routes_mesh.py`
- Misc routes: `server/api/routes_misc.py`
- Job runtime scheduler/state: `server/services/job_runtime.py`
- Simulation runner: `server/services/simulation_runner.py`
- OCC builder: `server/solver/waveguide_builder.py`
- Directivity render: `server/solver/directivity_plot.py`
- Legacy gmsh mesher: `server/solver/gmsh_geo_mesher.py`
- Solver dependency matrix: `server/solver/deps.py`

## 12. Recent Major Refactors (Feb 2026)

The project underwent a significant architectural cleanup to streamline mesh generation while keeping selected compatibility paths.

### 12.1 Unified Mesh Generation
- Removed legacy frontend `.geo` builders (`gmshGeoBuilder.js`).
- **Primary Path**: Export and ABEC flows use the Python OCC builder (`/api/mesh/build`).
- Legacy `.geo -> .msh` backend mesher remains for compatibility/tooling.

### 12.2 ABEC Export Modernization
- ABEC export now exclusively uses the BEM `.msh` file generated by the OCC builder.
- Removed legacy `coords.txt` and `static.txt` generation from the ABEC path.
- Updated `Project.abec` to point directly to the Gmsh `.msh` file.

### 12.3 Dead Code Elimination
- Deleted unused Node.js-only STL export scripts.
- Pruned unused imports and diagnostic functions across the core application.

### 12.4 Backend Router/Service Decomposition
- `server/app.py` was reduced to app assembly, CORS setup, router registration, and lifecycle startup.
- Route handlers now live in `server/api/routes_*.py`.
- Runtime orchestration/state and job execution moved into `server/services/*`.

---

## 13. Future Work Tracking

Planned or partial features are tracked in [docs/FUTURE_ADDITIONS.md](docs/FUTURE_ADDITIONS.md).
