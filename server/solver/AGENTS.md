# Solver Agent Guide

Scope: applies to `server/solver/*`.

## Responsibilities

- HornLab mesher integration (`mesher_adapter.py`).
- Metal BEM solve adapter and backend status (`metal_solver.py`).
- Bempp fallback solve adapter and backend status (`bempp_solver.py`).
- Directivity Index computation from solved polar patterns (`directivity_index.py`).
- Runtime dependency gating and reporting (`deps.py`).
- Result contract helpers (`contract.py`) and chart/directivity rendering (`charts.py`, `directivity_plot.py`).
- Unit handling and normalization behavior used by solver paths (`units.py`).

## Invariants

- Canonical tag mapping is fixed:
  - `1 = SD1G0 (wall)`, `2 = SD1D1001 (source)`, `3 = SD2G0`, `4 = I1-2`.
- Source excitation contract must remain tag-2 driven; the simulation
  runner rejects canonical meshes without tag-2 elements before solve.
- `SimulationRequest.num_frequencies` must be at least 1 so empty sweeps fail
  with an API validation error before solver setup.
- `/api/mesh/build` supports only:
  - `formula_type in {"R-OSSE","OSSE"}`
  - `msh_version in {"2.2","4.1"}`
- `sim_type` affects solve semantics, not geometry generation.
- Gmsh Python calls must stay thread-safe and avoid unsafe worker-thread initialization patterns.
- `solver_backend` accepts `auto`/`metal`/`bempp`. Auto prefers Metal when
  ready and falls back to Bempp when Metal is unavailable.
- Native transverse symmetry/reduced-domain solves are supported by both
  Metal and Bempp for free-standing rigid-Neumann models. Preserve
  `quadrants=1/12/14` through HornLab mesher generation and map them to
  `native_symmetry_plane="yz+xz"/"xz"/"yz"` respectively. Bempp still rejects
  coupled infinite-baffle symmetry, Robin symmetry, and CircSym requests.

## Required Tests Before Merge

- For HornLab mesher adapter and Gmsh mesh-export changes:
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_api_validation.py`
- For `metal_solver.py` changes:
  - `server/tests/test_metal_solver_adapter.py`
  - `server/tests/test_solver_backend_selection.py`
  - `server/tests/test_solver_tag_contract.py`
- For `bempp_solver.py` changes:
  - `server/tests/test_bempp_solver.py`
  - `server/tests/test_solver_backend_selection.py`
- For changes to either backend's solve numerics (directivity, observation
  frame, symmetry expansion, assembly):
  - `server/tests/test_cross_backend_asro2_parity.py` — solves a pinned ATH
    export and compares against a stored directivity. Opt-in: needs
    `ATH_REFERENCE_ROOT`. It exercises ONE backend per run (whichever the host
    uses); set `WG_PARITY_BACKEND=bempp` or `=metal` to force the other, which
    is the only way to make it a genuine cross-backend check on one machine.
- For `deps.py` / preflight changes:
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_runtime_preflight.py`
- For `units.py` or unit-sensitive behavior:
  - `server/tests/test_units.py`
- For API contract changes touching solver integration:
  - `server/tests/test_api_validation.py`
  - `server/tests/test_dependency_runtime.py`
- For solver-readiness reporting (`device_inventory.py`, `check_solver_engine.py`):
  - `server/tests/test_device_inventory.py`
- For mesh-size limits and the solve-mesh soft warning:
  - `server/tests/test_api_validation.py` —
    `test_hornlab_mesher_publishes_mesh_stats_after_canonical_mesh_build`
    pins the published `mesh_stats` contract: `domain_multiplier`,
    `full_domain_triangle_count`, `soft_warning_full_domain_triangle_limit`
    (18,000) and `warnings`. The hard ceiling is `maxTriangles` (default
    50,000); exceeding 18,000 warns but does not refuse, so a change that
    turns the warning back into a refusal must update this test and this note.
- For chart-rendering contract changes:
  - `server/tests/test_render_routes.py`
  - `server/tests/test_charts.py`
- Always run full server suite before merge (from repo root): `npm run test:server`

## Known Pitfalls

- Dependency support ranges in docs must match `SUPPORTED_DEPENDENCY_MATRIX`.
- HornLab mesher package and Gmsh runtime have different availability rules (Python package vs Gmsh executable/API).
- Job state is in-memory; restarts clear job history.
- Overly broad exception wrapping can hide actionable HTTP status details; preserve 422 vs 503 boundaries.
- If a new server contract test is added, update this file in the same change.
