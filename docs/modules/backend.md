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
- `server/solver/waveguide_builder.py` — OCC geometry + Gmsh mesh generation
- `server/solver/mesh.py` — mesh validation, tag contract checks
- `server/solver/solve*.py` — BEM assembly and solve execution
- `server/solver/deps.py` — dependency matrix (Python, gmsh, bempp versions)

## Core Responsibilities

- **HTTP routes**: Expose `/api/solve`, `/api/mesh/build`, `/api/status/{job_id}`, `/api/jobs`, `/api/results/{job_id}`, health/update checks
- **Request validation**: Enforce mesh array lengths, source tag presence, dependency availability
- **OCC meshing**: Build horn/enclosure geometry and generate Gmsh `.msh` files
- **BEM solve**: Assemble BEM operators and solve the linear system (standard or optimized path)
- **Job orchestration**: Queue jobs FIFO, track state, persist results
- **Device management**: Auto-detect OpenCL device availability, apply user device selection

## Operational Details

See [`server/README.md`](../server/README.md) for:
- Setup and dependency installation
- API endpoint reference and payload contracts
- Health checks and troubleshooting
- Device mode policy and OpenCL setup
