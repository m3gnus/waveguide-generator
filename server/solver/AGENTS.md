# Solver Agent Guide

Scope: applies to `server/solver/*`; root-level `AGENTS.md` still defines repo-wide Definition of Done.

## Responsibilities

- Backend mesh conversion and validation (`mesh.py`).
- HornLab mesher integration (`mesher_adapter.py`, `gmsh_utils.py`).
- BEM solve orchestration and the stable solve entry point (`bem_solver.py`, `solve.py`).
- Runtime dependency gating and reporting (`deps.py`).
- Unit handling and normalization behavior used by solver paths (`units.py`).

## Invariants

- Canonical tag mapping is fixed:
  - `1 = SD1G0 (wall)`, `2 = SD1D1001 (source)`, `3 = SD2G0`, `4 = I1-2`.
- Source excitation contract must remain tag-2 driven (`tag_throat=2` with
  `driver_dofs` selected from tag-2 elements in `solve.py`).
- `prepare_mesh` must reject index out-of-range and no-source-tag payloads.
- `/api/mesh/build` supports only:
  - `formula_type in {"R-OSSE","OSSE"}`
  - `msh_version in {"2.2","4.1"}`
- `sim_type` affects solve semantics, not OCC geometry generation.
- Gmsh Python calls must stay thread-safe (`gmsh_lock`) and avoid unsafe worker-thread initialization patterns.

## Required Tests Before Merge

- For HornLab mesher adapter and Gmsh mesh-export changes:
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_api_validation.py`
- For `mesh.py` changes:
  - `server/tests/test_mesh_validation.py`
  - `server/tests/test_solver_tag_contract.py`
- For `bem_solver.py` / `solve.py` changes:
  - `server/tests/test_solver_tag_contract.py`
  - `server/tests/test_solver_hardening.py`
- For `device_interface.py` changes:
  - `server/tests/test_device_interface.py`
- For `units.py` or unit-sensitive behavior:
  - `server/tests/test_units.py`
  - `server/tests/test_observation_distance.py`
- For API contract changes touching solver integration:
  - `server/tests/test_api_validation.py`
  - `server/tests/test_dependency_runtime.py`
- Always run full server suite before merge (from repo root): `npm run test:server`

## Known Pitfalls

- Dependency support ranges in docs must match `SUPPORTED_DEPENDENCY_MATRIX`.
- HornLab mesher package and Gmsh runtime have different availability rules (Python package vs Gmsh executable/API).
- Job state is in-memory; restarts clear job history.
- Overly broad exception wrapping can hide actionable HTTP status details; preserve 422 vs 503 boundaries.
- If a new server contract test is added, update this file in the same change.
