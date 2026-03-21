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

### 1.0 macOS OpenCL CPU setup (investigation-only on Apple Silicon)

On Apple Silicon, the helper below creates a no-space conda environment with `pocl` (Portable OpenCL CPU runtime). This environment is still useful for reproducing the current `bempp-cl` OpenCL failure boundary, but it is not a validated `/api/solve` runtime contract today.

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
- bounded solve validation evidence (`bounded_solve_validation`, `/api/solve`)

`bounded_solve_validation` is sourced from the persisted Tritonia probe record:

- default path: `output/runtime/bounded_solve_validation.json`
- override path: `WG_BOUNDED_SOLVE_RECORD_PATH=/abs/path.json`
- refresh command (must run with solve enabled): `cd server && python3 scripts/benchmark_tritonia.py --freq 1000 --device auto --precision single --timeout 30`

Current Apple Silicon contract:

- Apple Silicon OpenCL solve is currently reported unsupported/unready for `/api/solve`.
- `./scripts/setup-opencl-backend.sh` provisions an investigation/runtime-repro environment, not a production-readiness fix for bounded solves.

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
  - bounded solve validation (`bounded_solve_validation`)
  - `matplotlib` (optional; chart render endpoints)
- Summary includes endpoint-scoped readiness:
  - `summary.solveReady` / `summary.solveIssues` (`/api/solve`)
  - `summary.meshBuildReady` / `summary.meshBuildIssues` (`/api/mesh/build`)

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

- `auto` follows a conservative supported-runtime policy: `opencl_cpu` first, then `opencl_gpu` only when both GPU and CPU OpenCL contexts are validated.
- OpenCL drivers are required. If OpenCL modes are unavailable, `/api/solve` returns an explicit runtime warning/error (no numba fallback).
- With current bempp-cl `0.4.x`, singular assembly still requires a CPU OpenCL context. GPU-only OpenCL runtimes are reported unsupported for `opencl_gpu` instead of using context aliasing fallbacks.
- On Apple Silicon, the current OpenCL solve path is intentionally reported unsupported/unready until a bounded solve is validated end-to-end; the existing `pocl` CPU setup is investigation-only.
- The solver now clamps the effective observation distance so the on-axis microphone and polar map stay outside the modeled geometry, and it records the adjustment in `results.metadata.observation`.
- Solve results also persist the effective polar-map settings in `results.metadata.directivity`, including angle range, sample count/step, enabled axes, normalized plane descriptors, normalization angle, diagonal angle, observation origin, and requested/effective observation distance.
- `results.directivity` is a plane-keyed map containing only the requested planes; callers must not assume all of `horizontal`, `vertical`, and `diagonal` are always present.

## 1.2 Supported dependency matrix

The backend now enforces a version matrix at runtime:

| Component           | Supported range | Required for      |
| ------------------- | --------------- | ----------------- |
| Python              | `>=3.10,<3.15`  | backend runtime   |
| gmsh Python package | `>=4.11,<5.0`   | `/api/mesh/build` |
| bempp-cl            | `>=0.4,<0.5`    | `/api/solve`      |

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

For a bounded Tritonia-M repro (OCC mesh build + optional 1-frequency solve + precision support matrix), run:

```bash
cd server
python3 scripts/benchmark_tritonia.py [options]
```

Or from repository root:

```bash
npm run benchmark:tritonia
```

Options:

- `--freq FLOAT` — Single frequency to solve (Hz, default: 1000)
- `--sweep` — Run a 3-frequency sweep (0.8×, 1×, 1.2×) instead of single frequency
- `--device MODE` — Device mode: `auto|opencl_gpu|opencl_cpu` (default: `auto`)
- `--precision MODE` — BEM precision: `single|double|both` (default: `single`; `both` tests single then double)
- `--json` — Output results as JSON
- `--no-solve` — Skip solve step, only test mesh preparation
- `--timeout SECONDS` — Max time per solve attempt (default: 120)

What this reports:

- mesh-prep success/failure for the Tritonia OCC preset (vertices, triangles, tag counts)
- selected runtime/device metadata for the requested mode
- per-precision (`single`, `double`) solve outcomes on the active host
- solver stage timings (elapsed, GMRES iterations, SPL value)
- unsupported precision modes surfaced explicitly
- persisted bounded-solve readiness evidence for preflight/doctor (`output/runtime/bounded_solve_validation.json`) when solve is enabled (not `--no-solve`)

Exit codes:

- `0` — All requested operations succeeded
- `1` — Mesh preparation failed
- `2` — All solve attempts failed (but mesh prep succeeded)
- `3` — Runtime unavailable (bempp/OpenCL not installed)

## 3. API Endpoints

### `GET /health`

Health check and solver status.

Includes:

- dependency matrix/runtime payload under `dependencies`
- solver readiness gate (`solverReady`) derived from doctor `summary.solveReady` (includes bounded solve validation evidence)
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
  - `selection_policy` (`supported_opencl_modes`)
  - `supported_modes` (validated concrete modes for the current runtime)
  - `available_modes`
  - `mode_availability` (per-mode `available` / `supported` / `reason` for `auto|opencl_cpu|opencl_gpu`)
  - `opencl_diagnostics` (base/runtime/cpu/gpu detection details)
- dependency doctor payload under `dependencyDoctor`, including:
  - `summary.requiredReady|requiredIssues`
  - `summary.solveReady|solveIssues`
  - `components`
  - `solveReadiness` (bounded solve validation status/detail/path)

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
  - solve results package directivity per requested plane only; use `results.metadata.directivity.enabled_axes` or `results.metadata.directivity.planes` to inspect which cuts were computed
- `verbose`
- `mesh_validation_mode` (`strict` | `warn` | `off`, default `warn`)
- `advanced_settings`:
  - `use_burton_miller` (bool, stable solver runtime override)
- Compatibility-only legacy fields still accepted by backend runtime (not exposed by the active frontend contract): `device_mode`, `use_optimized`, `advanced_settings.enable_warmup`, `advanced_settings.bem_precision`. `device_mode` must still be `auto`, `opencl_cpu`, or `opencl_gpu` when provided. `device_mode`, `use_optimized`, `advanced_settings.enable_warmup`, and `advanced_settings.bem_precision` are ignored; `/api/solve` always runs the stable solver entrypoint with the reduced public override surface and fixed numerics (single precision, no warm-up).

Validation behavior:

- `vertices.length` must be divisible by 3
- `indices.length` must be divisible by 3
- `surfaceTags.length` must equal triangle count (`indices.length / 3`)
- `sim_type` currently must be `"2"` (free-standing); `"1"` is deferred in hardened runtime
- malformed payloads return `422`
- compatibility-only `advanced_settings.bem_precision` must still be `single` or `double` when provided, even though the active `/api/solve` runtime ignores it

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
  - `metadata.performance.total_time_seconds`
  - `metadata.performance.bem_precision` (`single` for the stable optimized solve path)

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
