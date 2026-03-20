# MWG Horn BEM Backend

Python backend for running horn BEM simulations used by the frontend simulation tab.

- Framework: FastAPI
- Default URL: `http://localhost:8000`
- Main file: `server/app.py`

## 1. Setup (project-local `.venv`)

From repository root:

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt
./.venv/bin/pip install -r server/requirements-gmsh.txt
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

Notes:
- The root setup scripts (`SETUP-*`) automatically attempt gmsh+bempp installation with fallback handling.
- `gmsh` is mandatory for `/api/mesh/build`: setup exits if gmsh cannot be installed/imported after retries.
- `gmsh` Python wheels on default PyPI may be missing for some Linux/Python combinations.
- Dependency audit (March 19, 2026): `trimesh` was removed from backend requirements after proving it unused in active runtime/tests.
- `uvicorn[standard]` remains intentional because backend startup still runs through `uvicorn` in `server/app.py`; plain-vs-standard extras policy is handled in the runtime-doctor/preflight backlog slices.
- For snapshot Gmsh wheels, use:
  - `./.venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server/requirements-gmsh.txt`
  - Headless Linux: `./.venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev-nox -r server/requirements-gmsh.txt`

### 1.0 macOS OpenCL CPU setup (recommended for bempp-cl OpenCL)

On Apple Silicon, bempp-cl `0.4.x` OpenCL requires a CPU OpenCL device. The helper below creates a no-space conda environment with `pocl` (Portable OpenCL CPU runtime):

From repository root:

```bash
./scripts/setup-opencl-backend.sh
```

After that, the helper writes this repo marker:

```bash
.waveguide/backend-python.path
```

`npm start` and `server/start.sh` resolve backend Python with one shared order:

1. `PYTHON_BIN`
2. `WG_BACKEND_PYTHON`
3. `.waveguide/backend-python.path`
4. fallback probe across project `.venv`, OpenCL CPU env fallback, then `python3`

When step 4 is used, startup prefers the first interpreter whose runtime doctor reports all required dependencies ready; if none are ready, it falls back to the same raw order (`.venv` -> OpenCL CPU env -> `python3`).

For OpenCL CPU setup, the marker value is:

```bash
$HOME/.waveguide-generator/opencl-cpu-env/bin/python
```

### 1.0.1 Backend preflight verification

Run from repository root:

```bash
npm run preflight:backend
```

Strict mode exits non-zero when any required runtime check fails:

```bash
npm run preflight:backend:strict
```

Preflight always runs under the interpreter selected by the shared startup contract and reports required readiness for:
- `fastapi` (backend startup)
- `gmsh` (`/api/mesh/build`)
- `bempp-cl` (`/api/solve`)
- OpenCL runtime availability (`/api/solve`)

### 1.0.2 Backend dependency doctor

Run from repository root:

```bash
npm run doctor:backend
```

Machine-readable payload:

```bash
npm run doctor:backend:json
```

Strict mode exits non-zero when any **required** component is not ready:

```bash
npm run doctor:backend:strict
```

Doctor report contract:
- Stable schema (`schemaVersion`) with per-component entries under `components`
- Component status classification: `installed`, `missing`, `unsupported`
- Explicit component category: `required` vs `optional`
- Feature impact and OS-specific install guidance for:
  - `fastapi`
  - `gmsh` Python API
  - `bempp-cl`
  - OpenCL runtime
  - `matplotlib` (optional; chart render endpoints)

Cross-platform notes:
- Windows/Linux GPU OpenCL relies on vendor driver packages (NVIDIA/AMD/Intel).
- This repository does not currently provide a fully automatic cross-vendor GPU driver installer.
- Linux CPU fallback is typically `pocl-opencl-icd` from your distro packages.

### 1.1 Device mode policy (`/api/solve`)

The solver now supports explicit device mode selection:

- `auto`
- `opencl_cpu`
- `opencl_gpu`

Notes:
- `auto` is deterministic and fast: `opencl_gpu` if available, else `opencl_cpu`.
- OpenCL drivers are required. If OpenCL modes are unavailable, `/api/solve` returns an explicit runtime warning/error (no numba fallback).
- With current bempp-cl `0.4.x`, the singular assembler still asks for a CPU OpenCL context. On GPU-only runtimes that expose no CPU OpenCL device, Waveguide Generator now aliases those CPU-context lookups to the active GPU context for `opencl_gpu` mode so Apple/other GPU-only systems can still solve.
- The solver now clamps the effective observation distance so the on-axis microphone and polar map stay outside the modeled geometry, and it records the adjustment in `results.metadata.observation`.
- Solve results also persist the effective polar-map settings in `results.metadata.directivity`, including angle range, sample count/step, enabled axes, normalization angle, diagonal angle, observation origin, and requested/effective observation distance.

## 1.2 Supported dependency matrix

The backend now enforces a version matrix at runtime:

| Component | Supported range | Required for |
|---|---|---|
| Python | `>=3.10,<3.15` | backend runtime |
| gmsh Python package | `>=4.11,<5.0` | `/api/mesh/build` |
| bempp-cl | `>=0.4,<0.5` | `/api/solve` |

Notes:
- `GET /health` returns the live dependency status and matrix under `dependencies`.

## 2. Run Backend

From repository root:

```bash
./.venv/bin/python server/app.py
```

Or explicitly use the OpenCL CPU environment:

```bash
$HOME/.waveguide-generator/opencl-cpu-env/bin/python server/app.py
```

This starts Uvicorn on `0.0.0.0:8000`.

### 2.1 Headless / backend-only mode

The backend already runs headless. It is a standalone FastAPI service and does not require the browser UI, a local desktop GUI, or an X/Wayland display server to start.

Minimal backend-only workflow from repository root:

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt
./.venv/bin/pip install -r server/requirements-gmsh.txt
./.venv/bin/python server/app.py
```

Optional for `/api/solve`:

```bash
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

Notes:
- `/api/mesh/build` requires the Python `gmsh` package.
- `/api/solve` additionally requires `bempp-cl`.
- Plot rendering uses the non-interactive Matplotlib `Agg` backend, so chart/directivity endpoints do not require a display server.
- On headless Linux, prefer the Gmsh `-nox` wheel index documented above if the default wheel is unavailable.

Quick verification:

```bash
curl http://localhost:8000/health
```

### 2.2 Symmetry benchmark harness

For repeatable symmetry-policy experiments that do not require `gmsh` or `bempp-cl`, run:

```bash
cd server
python3 scripts/benchmark_symmetry.py --iterations 25
```

This uses deterministic synthetic fixtures for:
- full-domain reference
- half-domain symmetry reduction
- quarter-domain symmetry reduction
- rejected reduction when the source tag is off-center

### 2.3 Tritonia-M bounded runtime repro harness

For a bounded Tritonia-M repro (OCC mesh build + 1-frequency solve + precision support matrix), run:

```bash
cd server
python3 scripts/benchmark_solver.py --preset tritonia --json
```

What this reports:
- mesh-prep success/failure for the Tritonia OCC preset
- selected runtime/device metadata for the requested mode
- per-precision (`single`, `double`) support status on the active host
- solver stage timings from `metadata.performance` (`warmup`, frequency solve, directivity, total)

Notes:
- The preset defaults to `1000 Hz`, `1` frequency point, and linear spacing.
- You can still override device mode and frequency settings for reduced-sweep follow-up runs.
- Unsupported precision modes are surfaced explicitly as `unsupported` in the report; they are not silently downgraded.

## 3. API Endpoints

### `GET /health`

Health check and solver status.

Includes:
- dependency matrix/runtime payload under `dependencies`
- settings capability metadata under `capabilities`, including:
  - `simulationBasic.controls`
  - `simulationAdvanced.available`
  - `simulationAdvanced.controls`
  - `simulationAdvanced.reason`
  - `simulationAdvanced.plannedControls`
- selected BEM device metadata under `deviceInterface`, including:
  - `requested_mode`
  - `selected_mode`
  - `interface` (`opencl` when available)
  - `device_type` (`cpu` or `gpu`)
  - `device_name`
  - `fallback_reason`
  - `available_modes`
  - `mode_availability` (per-mode availability/reason for `auto|opencl_cpu|opencl_gpu`)
  - `opencl_diagnostics` (base/runtime/cpu/gpu detection details)

### `GET /api/updates/check`

Checks local repository state against `origin` and reports whether updates are available.

### `POST /api/solve`

Submits an async simulation job.

Required payload fields:

- `mesh.vertices` (flat xyz array)
- `mesh.indices` (flat triangle index array)
- `mesh.surfaceTags` (one tag per triangle)
- `mesh.format`
- `frequency_range`
- `num_frequencies`
- `sim_type`

Optional:

- `mesh.boundaryConditions`
- `mesh.metadata` (supports `units` and `unitScaleToMeter`)
- `polar_config`:
  - `angle_range` (`[start_deg, end_deg, num_points]`)
  - `norm_angle`
  - `distance`
  - `inclination` (diagonal plane angle)
  - `enabled_axes` (array of `horizontal|vertical|diagonal`, at least one required)
  - axis semantics:
    - `horizontal`: 0° plane
    - `vertical`: 90° plane
    - `diagonal`: `inclination` plane
- `verbose`
- `mesh_validation_mode` (`strict` | `warn` | `off`, default `warn`)
- `device_mode` (`auto` | `opencl_cpu` | `opencl_gpu`, default `auto`)
- `advanced_settings`:
  - `use_burton_miller` (bool, stable solver runtime override)
- Compatibility-only legacy fields still accepted by backend runtime (not exposed by the active frontend contract): `use_optimized`, `advanced_settings.enable_warmup`, `advanced_settings.bem_precision`. `use_optimized` is ignored; `/api/solve` always runs the stable solver entrypoint.

Validation behavior:

- `vertices.length` must be divisible by 3
- `indices.length` must be divisible by 3
- `surfaceTags.length` must equal triangle count (`indices.length / 3`)
- `sim_type` currently must be `"2"` (free-standing); `"1"` is deferred in hardened runtime
- malformed payloads return `422`
- compatibility-only `advanced_settings.bem_precision` must still be `single` or `double` when provided

Runtime metadata behavior:

- If mesh unit metadata is missing, backend auto-detects scale with heuristic fallback.
- Imported ATH `Mesh.Quadrants` does not directly reduce the canonical simulation payload or queued OCC solve request; `/api/solve` runs the full-domain mesh/OCC request in the active runtime.
- `/api/results/{job_id}` includes:
  - `metadata.failures`
  - `metadata.failure_count`
  - `metadata.partial_success`
  - `metadata.mesh_validation`
  - `metadata.unit_detection`
  - `metadata.device_interface` (selected interface/device information and fallback details)
  - `metadata.performance.bem_precision` (`single` or `double` for optimized solves)

### `GET /api/status/{job_id}`

Returns live job status payload:

- `status`: `queued` | `running` | `complete` | `error` | `cancelled`
- `progress`: normalized `0.0..1.0`
- `stage`: current pipeline stage (for example `mesh_prepare`, `bem_solve`, `cancelling`, `directivity`, `finalizing`)
- `stage_message`: human-readable stage detail
- `message`: terminal error/cancellation message (when applicable)

### `GET /api/results/{job_id}`

Returns simulation results for completed job.

### `GET /api/jobs`

Returns paginated persisted job rows.

- `mesh_stats` persists the authoritative solve-mesh diagnostics for each job:
  - `vertex_count` / `triangle_count`
  - canonical `tag_counts`
  - OCC-derived `identity_triangle_counts` sourced from the same canonical extraction the solver consumes

### `POST /api/stop/{job_id}`

Cancels queued/running job.

- Queued jobs transition directly to `cancelled`.
- Running jobs transition to stage `cancelling` while the worker checks `cancellation_requested`.
- Final `cancelled` status is only written after the worker exits a safe checkpoint.

### `DELETE /api/jobs/{job_id}`

Deletes a terminal job (`complete`, `error`, or `cancelled`). Returns `409` for active jobs (`queued` or `running`).

### `DELETE /api/jobs/clear-failed`

Deletes all jobs in `error` state.

## 4. Canonical Surface Tags

The backend expects this tag mapping:

- `1`: walls
- `2`: source
- `3`: secondary domain
- `4`: interface

The solver requires at least one source-tagged triangle (`2`).

## 5. Backend Tests

Cross-repo test inventory (frontend + backend + diagnostics): [`../tests/TESTING.md`](../tests/TESTING.md).

From repository root:

```bash
cd server && ../.venv/bin/python -m unittest discover -s tests
```

Or via npm script (uses system `python3`):

```bash
npm run test:server
```

## 6. Quick Smoke Checks

### Health

```bash
curl http://localhost:8000/health
```

### Payload validation (`422` expected)

```bash
curl -i -X POST http://localhost:8000/api/solve \
  -H 'Content-Type: application/json' \
  -d '{"mesh":{"vertices":[0,0,0,1,0,0,0,1,0],"indices":[0,1,2],"format":"msh"},"frequency_range":[100,1000],"num_frequencies":5,"sim_type":"2"}'
```

## 7. Operator Runbook

### 7.1 Log levels

- The backend uses Python `logging` with `MWG_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
- Default level is `INFO` when `MWG_LOG_LEVEL` is unset.
- Example:

```bash
MWG_LOG_LEVEL=DEBUG ./.venv/bin/python server/app.py
```

### 7.2 Health expectations

- `GET /health` should return:
  - `status: "ok"`
  - `solverReady`: `true` when BEM runtime is available
  - `occBuilderReady`: `true` when OCC gmsh runtime is available
  - `dependencies`: supported matrix + runtime status payload
  - `capabilities`: frontend settings capability payload for Simulation Basic / Advanced gating
- For production-like runs where simulation and OCC meshing must work, both `solverReady` and `occBuilderReady` should be `true`.

### 7.3 Common failure classes

- `422` validation failures:
  - malformed mesh arrays (`vertices/indices/surfaceTags`)
  - unsupported request values (`sim_type`, `msh_version`, etc.)
- `503` dependency/runtime unavailable:
  - missing or unsupported `gmsh` Python runtime for OCC mesh build
  - missing/unsupported `bempp` runtime for solve
- `404` missing resource:
  - unknown `job_id`
  - results/artifacts not present
- `500` unexpected server errors:
  - unhandled runtime exception

### 7.4 Troubleshooting workflow

1. Check health and dependency payload:

```bash
curl http://localhost:8000/health
```

2. Confirm runtime versions against supported matrix (`python`, `gmsh`, `bempp`).
3. Reproduce with a minimal request (for `422`/contract debugging use the payload-validation curl above).
4. For `503`, inspect `detail` from response and install/fix missing runtime.
5. For job-state issues, inspect:
  - `GET /api/jobs?limit=200&offset=0`
  - `GET /api/status/{job_id}`
6. Increase logging signal with `MWG_LOG_LEVEL=DEBUG` and retry.
