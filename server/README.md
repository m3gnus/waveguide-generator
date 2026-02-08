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

## 2. Run Backend

From repository root:

```bash
./.venv/bin/python server/app.py
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
- `mesh.metadata`
- `polar_config`
- optimization flags (`use_optimized`, `enable_symmetry`, `verbose`)

Validation behavior:

- `vertices.length` must be divisible by 3
- `indices.length` must be divisible by 3
- `surfaceTags.length` must equal triangle count (`indices.length / 3`)
- malformed payloads return `422`

### `GET /api/status/{job_id}`

Returns job status and progress.

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
