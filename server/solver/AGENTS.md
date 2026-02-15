# Solver Agent Guide

## Responsibilities
- Backend mesh conversion, validation, and optional refinement (`mesh.py`).
- Gmsh meshing endpoints and OCC mesh builder support (`gmsh_geo_mesher.py`, `waveguide_builder.py`).
- BEM solve orchestration and optimized solve path (`bem_solver.py`, `solve*.py`).
- Runtime dependency gating and reporting (`deps.py`).

## Invariants
- Canonical tag mapping is fixed:
  - `1 = SD1G0 (wall)`, `2 = SD1D1001 (source)`, `3 = SD2G0`, `4 = I1-2`.
- Source boundary space selection must remain `segments=[2]` in solver paths.
- `prepare_mesh` must reject index out-of-range and no-source-tag payloads.
- `/api/mesh/build` supports only:
  - `formula_type in {"R-OSSE","OSSE"}`
  - `msh_version in {"2.2","4.1"}`
- `sim_type` affects solve semantics, not OCC geometry generation.
- Gmsh Python calls must stay thread-safe (`gmsh_lock`) and avoid unsafe worker-thread initialization patterns.

## Required Tests Before Merge
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_api_validation.py`
- `server/tests/test_gmsh_endpoint.py`
- `server/tests/test_gmsh_geo_mesher.py`
- `server/tests/test_mesh_validation.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_units.py`

## Known Pitfalls
- Dependency support ranges in docs must match `SUPPORTED_DEPENDENCY_MATRIX`.
- OCC builder and gmsh mesher have different availability rules (Python API vs CLI fallback).
- Job state is in-memory; restarts clear job history.
- Overly broad exception wrapping can hide actionable HTTP status details; preserve 422 vs 503 boundaries.
