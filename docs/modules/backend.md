# Backend Module Contract

## Scope

**Application & routing**:
- `server/app.py` — FastAPI app assembly, router registration, startup/shutdown
- `server/api/routes_*.py` — HTTP route handlers (simulation, mesh, misc)

**Services & domain logic**:
- `server/services/job_runtime.py` — job lifecycle, state, persistence, scheduler
- `server/services/solver_runtime.py` — runtime capability checks, device selection
- `server/services/simulation_runner.py` — single-job execution pipeline
- `server/services/simulation_validation.py` — request validation, mesh checks

**Solver & meshing**:
- `server/solver/mesher_adapter.py` — Waveguide Generator payload adapter for `hornlab-waveguide-mesher`
- `server/solver/metal_solver.py` — optional `hornlab-metal-bem` solver adapter
- `server/solver/mesh.py` — mesh validation, tag contract checks
- `server/solver/solve*.py` — BEM assembly and solve execution
- `server/solver/deps.py` — dependency matrix (Python, HornLab mesher, Metal BEM, gmsh, bempp versions)

## Core Responsibilities

- **HTTP routes**: Expose `/api/solve`, `/api/mesh/build`, `/api/mesh/step`, `/api/mesh/viewport`, `/api/status/{job_id}`, `/api/jobs`, `/api/results/{job_id}`, health/update checks
- **Request validation**: Enforce mesh array lengths, source tag presence, dependency availability
- **Backend meshing**: Build horn/enclosure `.msh` files through `hornlab-waveguide-mesher`
- **STEP surface export**: Build full-domain single-layer inner horn STEP surfaces through `hornlab-waveguide-mesher`
- **Viewport mesher geometry**: Expose HornLab mesher/Gmsh display triangles via `/api/mesh/viewport`
- **BEM solve**: Select `auto`, `bempp`, or `metal` backend, then assemble/solve through the selected runtime
- **Job orchestration**: Queue jobs FIFO, track state, persist results
- **Device management**: Auto-detect OpenCL device availability, apply user device selection

## Operational Details

See [`server/README.md`](../server/README.md) for:
- Setup and dependency installation
- API endpoint reference and payload contracts
- Health checks and troubleshooting
- Device mode policy and OpenCL setup
