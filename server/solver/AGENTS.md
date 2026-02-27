# Solver Agent Guide

Scope: applies to `server/solver/*`; root-level `AGENTS.md` still defines repo-wide Definition of Done.

## Responsibilities
- Backend mesh conversion, validation, and optional refinement (`mesh.py`).
- OCC mesh builder support (`waveguide_builder.py`).
- BEM solve orchestration and optimized solve path (`bem_solver.py`, `solve*.py`).
- Runtime dependency gating and reporting (`deps.py`).
- Unit handling and normalization behavior used by solver paths (`units.py`).

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
- For `waveguide_builder.py` and OCC meshing changes:
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_occ_resolution_semantics.py`
- For `mesh.py` changes:
  - `server/tests/test_mesh_validation.py`
  - `server/tests/test_solver_tag_contract.py`
- For `solve.py` / `solve_optimized.py` changes:
  - `server/tests/test_solver_tag_contract.py`
  - `server/tests/test_solver_hardening.py`
- For `units.py` or unit-sensitive behavior:
  - `server/tests/test_units.py`
  - `server/tests/test_observation_distance.py`
- For API contract changes touching solver integration:
  - `server/tests/test_api_validation.py`
  - `server/tests/test_dependency_runtime.py`
- Always run full server suite before merge (from repo root): `npm run test:server`

## Known Pitfalls
- Dependency support ranges in docs must match `SUPPORTED_DEPENDENCY_MATRIX`.
- OCC builder and gmsh mesher have different availability rules (Python API vs CLI fallback).
- Job state is in-memory; restarts clear job history.
- Overly broad exception wrapping can hide actionable HTTP status details; preserve 422 vs 503 boundaries.
- If a new server contract test is added, update both this file and root `AGENTS.md` mapping in the same change.
