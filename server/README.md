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
./.venv/bin/pip install git+https://github.com/bempp/bempp-cl.git
```

Notes:
- `gmsh` Python wheels on default PyPI may be missing for some Linux/Python combinations.
- The `/api/mesh/generate-msh` endpoint also supports the system `gmsh` CLI, so install `gmsh` on PATH if the Python package cannot be installed.
- For snapshot Gmsh wheels, use:
  - `./.venv/bin/pip install -i https://gmsh.info/python-packages-dev --force-reinstall --no-cache-dir gmsh`
  - Headless Linux: `./.venv/bin/pip install -i https://gmsh.info/python-packages-dev-nox --force-reinstall --no-cache-dir gmsh`

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

## 1.1 Supported dependency matrix (P3-1)

The backend now enforces a version matrix at runtime:

| Component | Supported range | Required for |
|---|---|---|
| Python | `>=3.10,<3.14` | backend runtime |
| gmsh Python package | `>=4.10,<5.0` | `/api/mesh/build` |
| bempp-cl | `>=0.4,<0.5` | `/api/solve` |
| legacy `bempp_api` fallback | `>=0.3,<0.4` | `/api/solve (legacy fallback)` |

Notes:
- `POST /api/mesh/generate-msh` can still run with system `gmsh` CLI when Python gmsh is unavailable.
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
- `polar_config`
- optimization flags (`use_optimized`, `enable_symmetry`, `verbose`)
- `mesh_validation_mode` (`strict` | `warn` | `off`, default `warn`)

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
