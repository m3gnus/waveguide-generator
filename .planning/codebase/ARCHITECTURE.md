# Architecture

**Analysis Date:** 2026-02-25

## Pattern Overview

**Overall:** Browser frontend + FastAPI backend with strict mesh/surface-tag contract and multiple meshing pipelines.

**Key Characteristics:**
- Frontend and backend are decoupled by canonical mesh payload contract (`vertices`, `indices`, `surfaceTags`, metadata)
- Backend owns simulation runtime and OCC meshing readiness checks
- Legacy `.geo -> .msh` path exists alongside OCC parameterized builder
- Test-first guardrails enforce parity on contract-critical modules

## Layers

**UI/Orchestration Layer (frontend):**
- Purpose: Parameter handling, event wiring, render/interaction orchestration
- Contains: `src/app/*`, `src/ui/*`, `src/state.js`
- Depends on: geometry, solver client, export modules
- Used by: browser entry `src/main.js`

**Geometry/Contract Layer (frontend):**
- Purpose: Build visualization mesh + canonical simulation payload with surface tags
- Contains: `src/geometry/*`, `src/simulation/payload.js`, `src/solver/waveguidePayload.js`
- Depends on: param normalization and mesh engine internals
- Used by: viewport rendering, exports, solve request preparation

**API/Service Layer (backend):**
- Purpose: Validate requests, gate dependencies, enqueue/execute jobs, expose results
- Contains: `server/api/*`, `server/services/*`, `server/app.py`
- Depends on: solver and mesher modules in `server/solver/*`
- Used by: frontend solver and export flows

**Numerical Solver/Mesher Layer (backend):**
- Purpose: OCC mesh generation, legacy geo meshing, BEM solve and validation
- Contains: `server/solver/waveguide_builder.py`, `gmsh_geo_mesher.py`, `mesh.py`, `solve.py`, `solve_optimized.py`
- Depends on: gmsh, bempp, numpy/scipy stack
- Used by: `/api/mesh/build`, `/api/mesh/generate-msh`, `/api/solve`

## Data Flow

**Render + payload flow:**
1. UI state changes update global state
2. `App` triggers geometry artifact rebuild
3. `src/geometry/pipeline.js` emits render mesh + canonical payload
4. Viewer consumes mesh; solver/export paths consume payload

**Solve flow:**
1. Frontend submits simulation with OCC-adaptive mesh strategy
2. Backend validates runtime/dependency readiness and payload contract
3. Job runtime queues and executes simulation via solver modules
4. Frontend polls status/results endpoints until completion

**Export flow:**
1. Frontend prepares ATH params and symmetry metadata
2. Backend `/api/mesh/build` creates gmsh-authored mesh via OCC builder
3. Frontend assembles export artifacts around returned `.msh`

## Key Abstractions

**Canonical surface-tag mapping:**
- Purpose: stable boundary-condition semantics across pipelines
- Values: `1/2/3/4` (wall/source/secondary/interface)
- Enforcement: frontend payload builders + backend validation tests

**Pipeline boundary contracts:**
- OCC build endpoint for parameterized meshing (`/api/mesh/build`)
- Legacy geo mesher endpoint for `.geo` text inputs (`/api/mesh/generate-msh`)
- Solver endpoint for async simulation (`/api/solve`)

## Entry Points

**Frontend entry:**
- Location: `src/main.js`
- Triggers: browser load
- Responsibilities: boot app and wire UI/runtime

**Frontend coordinator:**
- Location: `src/app/App.js`
- Triggers: app init/events
- Responsibilities: state, scene, mesh lifecycle, export and simulation orchestration

**Backend entry:**
- Location: `server/app.py`
- Triggers: `python server/app.py` / uvicorn startup
- Responsibilities: app assembly, middleware, router registration, lifespan startup

## Error Handling

**Strategy:**
- Validate early at API and payload boundaries
- Return HTTP 422 for schema/contract violations
- Return HTTP 503 when required runtime dependencies are unavailable or unsupported

## Cross-Cutting Concerns

**Validation:**
- Strong shape/tag checks on mesh payloads and solve requests
- Dependency matrix checks via `server/solver/deps.py`

**Testing contracts:**
- Contract-critical files carry mandatory parity test sets documented in `AGENTS.md`

---

*Architecture analysis: 2026-02-25*
*Update when pipeline boundaries or contract ownership change*
