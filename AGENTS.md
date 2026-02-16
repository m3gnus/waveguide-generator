# Waveguide Generator Agent Guide

## System Overview
- Frontend: browser app (Three.js + UI + export orchestration) under `src/`.
- Backend: FastAPI service for meshing and BEM solve workflows under `server/`.
- Shared contract: canonical mesh payload (`vertices`, `indices`, `surfaceTags`, BC metadata).
- Primary runtime entry points:
  - Frontend boot: `src/main.js`
  - Frontend coordinator: `src/app/App.js`
  - Backend API: `server/app.py`

## Pipeline Boundaries
1. JS geometry pipeline (`src/geometry/*`)
- Produces viewer mesh and canonical simulation payload.
- Owns surface-tag semantics (`1/2/3/4`) and split-plane filtering for symmetry domains.

2. OCC export meshing pipeline (`/api/mesh/build`)
- Implemented in `server/solver/waveguide_builder.py`.
- Accepts ATH parameters and returns Gmsh-authored `.msh` (plus optional `stl` text).
- Does not return `.geo`.

3. Legacy `.geo -> .msh` pipeline (`/api/mesh/generate-msh`)
- `.geo` built in frontend (`src/export/gmshGeoBuilder.js`), meshed in backend.
- Uses gmsh Python API when present, otherwise gmsh CLI fallback.

4. BEM solve pipeline (`/api/solve`)
- Backend validates mesh shape and source tags before solving.
- Frontend simulation falls back to mock results only when backend is unreachable.

## Source-of-Truth Docs
- Architecture and runtime behavior: `docs/PROJECT_DOCUMENTATION.md`
- ABEC bundle contract/parity: `docs/ABEC_PARITY_CONTRACT.md`
- Roadmap/backlog context: `docs/FUTURE_ADDITIONS.md`
- Backend operational details: `server/README.md`

## Coding and Testing Guardrails
- Keep module edits local; do not rewrite unrelated stacks opportunistically.
- Preserve canonical tag contract across frontend and backend:
  - `1 = wall`, `2 = source`, `3 = secondary`, `4 = interface`.
- Never document `/api/mesh/build` as returning `.geo` unless code is changed to do so.
- Keep ABEC bundle structure aligned with parity contract (including `bem_mesh.geo`).
- Run relevant tests for every behavior change; run full suites before merge:
  - `npm test`
  - `npm run test:server`

## Do Not Change Without Parity Tests
- `src/geometry/tags.js`, `src/geometry/pipeline.js`, `src/geometry/engine/mesh/enclosure.js`
  - Required: `tests/mesh-payload.test.js`, `tests/geometry-artifacts.test.js`, `tests/enclosure-regression.test.js`
- `src/app/exports.js`, `src/export/gmshGeoBuilder.js`
  - Required: `tests/export-gmsh-pipeline.test.js`, `tests/gmsh-geo-builder.test.js`
- `src/export/abecProject.js`, `src/export/abecBundleValidator.js`
  - Required: `tests/abec-bundle-parity.test.js`, `tests/abec-circsym.test.js`
- `server/solver/waveguide_builder.py`, `server/app.py`
  - Required: `server/tests/test_dependency_runtime.py`, `server/tests/test_gmsh_endpoint.py`
- `server/solver/mesh.py`, `server/solver/solve.py`, `server/solver/solve_optimized.py`
  - Required: `server/tests/test_mesh_validation.py`, `server/tests/test_solver_tag_contract.py`, `server/tests/test_api_validation.py`

## Execution Order (Token-Efficient)
1. Fix P0 geometry/tagging defects and red tests.
2. Immediately update docs/comments that contradict runtime.
3. Freeze ABEC parity contract and validators before large export changes.
4. Stabilize/benchmark the current 3D solver stack.
5. Keep any axisymmetric spike behind feature flags.

## Definition of Done
- JS tests pass: `npm test`.
- Server tests pass: `npm run test:server`.
- No docs claim `/api/mesh/build` returns `.geo` unless code does so.
- ABEC bundle output validates against the parity contract/checklist.
- Solver support matrix in docs matches `server/solver/deps.py`.

## Multi-Agent Handoff Rules
- Start from the nearest local `AGENTS.md` in the module you edit.
- Avoid cross-module edits unless required by a contract break.
- If a contract break is unavoidable, update both sides and tests in one change.
