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
- Architecture and runtime behavior: [docs/PROJECT_DOCUMENTATION.md](file:///Users/magnus/IM%20Dropbox/Magnus%20Andersen/DOCS/code/260127%20-%20Waveguide%20Generator/docs/PROJECT_DOCUMENTATION.md)
- ABEC bundle contract/parity: [docs/ABEC_PARITY_CONTRACT.md](file:///Users/magnus/IM%20Dropbox/Magnus%20Andersen/DOCS/code/260127%20-%20Waveguide%20Generator/docs/ABEC_PARITY_CONTRACT.md)
- Roadmap/backlog context: [docs/FUTURE_ADDITIONS.md](file:///Users/magnus/IM%20Dropbox/Magnus%20Andersen/DOCS/code/260127%20-%20Waveguide%20Generator/docs/FUTURE_ADDITIONS.md)
  - Includes **BEM Solver Acceleration Roadmap** (FMM, Device Policy, OpenCL).
- Backend operational details: [server/README.md](file:///Users/magnus/IM%20Dropbox/Magnus%20Andersen/DOCS/code/260127%20-%20Waveguide%20Generator/server/README.md)

## Coding and Testing Guardrails
- **Invariants**:
  - Keep canonical surface-tag mapping consistent with code (`1/2/3/4`).
  - Source tag (`2`) must be present in every simulation payload.
  - Interface tags (`4`) only applied when enclosure exists and `interfaceOffset` > 0.
  - Do not state that `/api/mesh/build` returns `.geo` unless code changes to do so.
  - Document ABEC bundles as including `bem_mesh.geo` (current required parity contract).
- Keep module edits local; do not rewrite unrelated stacks opportunistically.
- Run relevant tests for every behavior change; run full suites before merge:
  - `npm test`
  - `npm run test:server`

## Module-Specific Guidance

### Geometry (`src/geometry/`)
- **Responsibilities**: Param preparation, mesh topology generation, canonical payload assembly.
- **Invariants**: Group ranges are triangle indices. `surfaceTags.length` must equal triangle count.
- **Pitfalls**: `interfaceOffset` might be a list. Avoid scalar-only assumptions.

### Export (`src/export/`)
- **Responsibilities**: Build export artifacts, orchestrate backend meshing, enforce ABEC parity.
- **Invariants**: ABEC export uses `/api/mesh/build` only (no JS `.geo` fallback).
- **Pitfalls**: Keep `Project.abec` mesh references in sync with zip entries.

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

## Definition of Done
- JS tests pass: `npm test`.
- Server tests pass: `npm run test:server`.
- No docs claim `/api/mesh/build` returns `.geo` unless code does so.
- ABEC bundle output validates against the parity contract/checklist.
- Solver support matrix in docs matches `server/solver/deps.py`.

## Multi-Agent Handoff Rules
- Start from this root `AGENTS.md`.
- Avoid cross-module edits unless required by a contract break.
- If a contract break is unavoidable, update both sides and tests in one change.
