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
- For snapshot Gmsh wheels, use:
  - `./.venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev -r server/requirements-gmsh.txt`
  - Headless Linux: `./.venv/bin/pip install --pre --extra-index-url https://gmsh.info/python-packages-dev-nox -r server/requirements-gmsh.txt`

### 1.0 macOS OpenCL CPU setup (recommended for bempp-cl OpenCL)

On Apple Silicon, bempp-cl `0.4.x` OpenCL requires a CPU OpenCL device. The helper below creates a no-space conda environment with `pocl` (Portable OpenCL CPU runtime):

From repository root:

```bash
./scripts/setup-opencl-backend.sh
```

After that, `npm start` and `server/start.sh` will automatically prefer:

```bash
$HOME/.waveguide-generator/opencl-cpu-env/bin/python
```

Cross-platform notes:
- Windows/Linux GPU OpenCL relies on vendor driver packages (NVIDIA/AMD/Intel).
- This repository does not currently provide a fully automatic cross-vendor GPU driver installer.
- Linux CPU fallback is typically `pocl-opencl-icd` from your distro packages.

### 1.1 Device mode policy (`/api/solve`)

The solver now supports explicit device mode selection:

- `auto`
- `opencl_cpu`
- `opencl_gpu`
- `numba`

Notes:
- `auto` is deterministic and fast: `opencl_gpu` if available, else `opencl_cpu`, else `numba`.
- If OpenCL modes are unavailable, runtime falls back to `numba`.
- With current bempp-cl `0.4.x`, OpenCL runtime availability still depends on a CPU OpenCL driver for safe operator assembly.

## 1.2 Supported dependency matrix (P3-1)

The backend now enforces a version matrix at runtime:

| Component | Supported range | Required for |
|---|---|---|
| Python | `>=3.10,<3.15` | backend runtime |
| gmsh Python package | `>=4.15,<5.0` | `/api/mesh/build` |
| bempp-cl | `>=0.4,<0.5` | `/api/solve` |
| legacy `bempp_api` fallback | `>=0.3,<0.4` | `/api/solve (legacy fallback)` |

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

## 3. API Endpoints

### `GET /health`

Health check and solver status.

Includes:
- dependency matrix/runtime payload under `dependencies`
- selected BEM device metadata under `deviceInterface`, including:
  - `requested_mode`
  - `selected_mode`
  - `interface` (`opencl` or `numba`)
  - `device_type` (`cpu` or `gpu`)
  - `device_name`
  - `fallback_reason`
  - `available_modes`
  - `mode_availability` (per-mode availability/reason for `auto|opencl_cpu|opencl_gpu|numba`)
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
- optimization flags (`use_optimized`, `enable_symmetry`, `verbose`)
- `mesh_validation_mode` (`strict` | `warn` | `off`, default `warn`)
- `device_mode` (`auto` | `opencl_cpu` | `opencl_gpu` | `numba`, default `auto`)

Validation behavior:

- `vertices.length` must be divisible by 3
- `indices.length` must be divisible by 3
- `surfaceTags.length` must equal triangle count (`indices.length / 3`)
- `sim_type` currently must be `"2"` (free-standing); `"1"` is deferred in hardened runtime
- malformed payloads return `422`

Runtime metadata behavior:

- If mesh unit metadata is missing, backend auto-detects scale with heuristic fallback.
- `/api/results/{job_id}` includes:
  - `metadata.failures`
  - `metadata.failure_count`
  - `metadata.partial_success`
  - `metadata.mesh_validation`
  - `metadata.unit_detection`
  - `metadata.device_interface` (selected interface/device information and fallback details)

### `GET /api/status/{job_id}`

Returns live job status payload:

- `status`: `queued` | `running` | `complete` | `error` | `cancelled`
- `progress`: normalized `0.0..1.0`
- `stage`: current pipeline stage (for example `mesh_prepare`, `bem_solve`, `directivity`, `finalizing`)
- `stage_message`: human-readable stage detail
- `message`: terminal error/cancellation message (when applicable)

### `GET /api/results/{job_id}`

Returns simulation results for completed job.

### `POST /api/stop/{job_id}`

Cancels queued/running job.

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
