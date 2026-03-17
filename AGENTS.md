# Waveguide Generator Agent Guide

## System Overview

Waveguide Generator is a parametric horn design tool with real-time Three.js rendering and BEM simulation:

- **Frontend** (vanilla JS + Three.js): browser app in `src/` — handles UI, geometry rendering, and export orchestration
- **Backend** (FastAPI + Python): solver service in `server/` — handles OCC meshing, BEM solve, and job persistence
- **Shared contract**: canonical mesh payload with `vertices`, `indices`, `surfaceTags`, boundary conditions, and metadata

Primary runtime entry points:

- Frontend boot: `src/main.js` → `src/app/App.js`
- Backend API: `server/app.py`

## Pipeline Boundaries

**1. JS geometry pipeline** (`src/geometry/*`)
- Produces viewport mesh and canonical simulation payload
- Owns surface-tag semantics (`1`=wall, `2`=source, `3`=secondary, `4`=interface)
- Tag assignment and mesh validation happen here

**2. OCC meshing pipeline** (`POST /api/mesh/build`)
- Implemented in `server/solver/waveguide_builder.py`
- Accepts parametric ATH inputs; returns Gmsh-authored `.msh` file (plus optional STL text)
- Does NOT return `.geo` files; OCC geometry is built and meshed server-side

**3. BEM solve pipeline** (`POST /api/solve`)
- Backend validates mesh arrays, source tag presence, and runtime availability
- Frontend simulation requires actual backend solve (no mock fallback supported)
- Symmetry detection and reduction applied at solve time

## Documentation Sources

**Maintained docs (source of truth for active runtime)**:
- `docs/PROJECT_DOCUMENTATION.md` — current implementation map, entry points, flows, contracts
- `docs/architecture.md` — durable system-level architecture, layer boundaries
- `docs/modules/` — per-module contracts and responsibilities
- `tests/TESTING.md` — canonical test inventory and run commands
- `server/README.md` — backend setup, API endpoints, dependency matrix, troubleshooting
- `AGENTS.md` — this file; multi-agent guidance and coding guardrails

**Reference & tracking**:
- `docs/backlog.md` — active unfinished work (reviewed regularly, not immutable)
- `docs/archive/README.md` — superseded long-form plans and historical reports
- Code + tests in `src/`, `server/`, `tests/`, `server/tests/` — ultimate runtime truth

## Coding and Testing Guardrails

**Critical invariants** (must be enforced in code and docs):
- Canonical surface-tag mapping stays code-owned in `src/geometry/tags.js` (tags: `1`=wall, `2`=source, `3`=secondary, `4`=interface)
- Source tag (`2`) must exist in every simulation payload; solver rejects meshes without it
- Interface tags (`4`) only applied when enclosure geometry AND `interfaceOffset > 0` exist
- `/api/mesh/build` returns `.msh` files ONLY; never state it returns `.geo` unless code changes

**Development discipline**:
- Keep module edits local; avoid opportunistic rewrites of unrelated code
- Update docs immediately when changing contracts or entry points
- Run targeted tests first, then full suites before any merge

**Test command sequence** (before pushing):
```bash
node --test tests/<file>.test.js        # single JS test file
cd server && python3 -m unittest tests.<module> # single Python test
npm test                               # all JS tests
npm run test:server                    # all Python tests
```

## Module-Specific Guidance

### Geometry (`src/geometry/`)
**What it owns**: Parameter normalization, mesh topology generation, canonical payload assembly
**Critical invariants**:
- `surfaceTags.length === indices.length / 3` (one tag per triangle)
- Group ranges index into triangle arrays, not vertex arrays
- `interfaceOffset` may be a scalar OR array; never assume scalar-only

**Common pitfalls**:
- Post-tessellation mesh clipping breaks BEM accuracy (OCC free-meshing is asymmetric)
- Viewport rendering and simulation payloads use different coordinate conventions (θ vs φ)

### Export (`src/export/`)
**What it owns**: STL/CSV/config file generation, OCC mesh orchestration, artifact coordination
**Critical invariants**:
- OCC mesh export path uses `POST /api/mesh/build` exclusively (no fallback to JS `.geo`)
- Backend returns `.msh` format only; never expect `.geo` files

**Common pitfalls**:
- Mixing viewport tessellation helpers with actual simulation mesh logic
- Not normalizing parameters through `DesignModule` before OCC requests

## Contract-Critical Code & Tests

**Geometry/mesh contracts** (do not change without running all listed tests):
- Files: `src/geometry/tags.js`, `src/geometry/pipeline.js`, `src/geometry/engine/mesh/enclosure.js`
- Tests: `tests/mesh-payload.test.js`, `tests/geometry-artifacts.test.js`, `tests/enclosure-regression.test.js`

**Export orchestration contracts**:
- Files: `src/app/exports.js`, `src/solver/waveguidePayload.js`, `src/modules/export/useCases.js`
- Tests: `tests/export-gmsh-pipeline.test.js`, `tests/waveguide-payload.test.js`, `tests/csv-export.test.js`

**Backend OCC builder & API**:
- Files: `server/solver/waveguide_builder.py`, `server/api/routes_mesh.py`, `server/app.py`
- Tests: `server/tests/test_dependency_runtime.py`, `server/tests/test_occ_resolution_semantics.py`, `server/tests/test_updates_endpoint.py`

**Solver & mesh validation**:
- Files: `server/solver/mesh.py`, `server/solver/solve.py`, `server/solver/solve_optimized.py`
- Tests: `server/tests/test_mesh_validation.py`, `server/tests/test_solver_tag_contract.py`, `server/tests/test_solver_hardening.py`, `server/tests/test_api_validation.py`

## Definition of Done
- JS tests pass: `npm test`.
- Server tests pass: `npm run test:server`.
- No docs claim `/api/mesh/build` returns `.geo` unless code does so.
- Solver support matrix in docs matches `server/solver/deps.py`.
- End each work session with a commit for the completed changes.
- Before ending a session, review and update documentation affected by the change so docs stay current.

## Multi-Agent Handoff Rules
- Start from this root `AGENTS.md`.
- When both root and scoped AGENTS apply, the scoped AGENTS for the edited directory takes precedence.
- Avoid cross-module edits unless required by a contract break.
- If a contract break is unavoidable, update both sides and tests in one change.
- Keep AGENTS test maps in sync when adding/removing contract-critical tests.
