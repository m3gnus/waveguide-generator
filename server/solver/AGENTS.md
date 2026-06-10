# Solver Agent Guide

Scope: applies to `server/solver/*`; root-level `AGENTS.md` still defines repo-wide Definition of Done.

## Responsibilities

- HornLab mesher integration (`mesher_adapter.py`).
- Metal BEM solve adapter and backend status (`metal_solver.py`); hornlab-metal-bem is the only solve backend.
- Directivity Index computation from solved polar patterns (`directivity_index.py`).
- Runtime dependency gating and reporting (`deps.py`).
- Result contract helpers (`contract.py`) and chart/directivity rendering (`charts.py`, `directivity_plot.py`).
- Unit handling and normalization behavior used by solver paths (`units.py`).

## Invariants

- Canonical tag mapping is fixed:
  - `1 = SD1G0 (wall)`, `2 = SD1D1001 (source)`, `3 = SD2G0`, `4 = I1-2`.
- Source excitation contract must remain tag-2 driven; the simulation
  runner rejects canonical meshes without tag-2 elements before solve.
- `/api/mesh/build` supports only:
  - `formula_type in {"R-OSSE","OSSE"}`
  - `msh_version in {"2.2","4.1"}`
- `sim_type` affects solve semantics, not geometry generation.
- Gmsh Python calls must stay thread-safe and avoid unsafe worker-thread initialization patterns.
- `solver_backend` accepts only `auto`/`metal`; `bempp` values must keep
  failing with an explicit removal message (the bempp engine was deleted
  2026-06-11).

## Required Tests Before Merge

- For HornLab mesher adapter and Gmsh mesh-export changes:
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_api_validation.py`
- For `metal_solver.py` changes:
  - `server/tests/test_metal_solver_adapter.py`
  - `server/tests/test_solver_backend_selection.py`
  - `server/tests/test_solver_tag_contract.py`
- For `deps.py` / preflight changes:
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_runtime_preflight.py`
- For `units.py` or unit-sensitive behavior:
  - `server/tests/test_units.py`
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
