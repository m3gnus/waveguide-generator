# Architecture Cleanup Plan

## Goal

Refactor the repo into explicit modules with one-way dependencies, one source of truth per contract, and no downstream "repair" logic that silently fixes invalid upstream data.

The target state is:

- `src/app` only wires UI events to application modules.
- `src/modules/*` own use cases and are the only public frontend entry points.
- `src/geometry`, `src/export`, `src/solver`, `src/viewer`, and `src/ui` become internal implementation packages behind those module boundaries.
- `server/api` only parses HTTP and returns HTTP.
- `server/services` only orchestrate jobs and persistence.
- `server/solver` only owns meshing / solve domain logic.
- Contracts are validated once, at the boundary where they are created.
- Invalid data is rejected early instead of being normalized or patched later in the pipeline.

## Non-Negotiable Design Rules

1. No module may import another module's internals when a public module API exists.
2. No request or mesh payload may be mutated downstream to "make it work".
3. Geometry identity, mesh sizing classes, and solver boundary classes must be separate concepts.
4. Contract docs and tests must match runtime behavior before structural refactors start.
5. Temporary compatibility shims must have a removal phase. No permanent "backward compat" glue in core runtime code.

## Required Tag Model

This cleanup should adopt three layers of classification instead of one overloaded tag system.

### 1. Geometry face identity

Freestanding thickened R-OSSE / OSSE:

- `inner_wall`
- `outer_wall`
- `mouth_rim`
- `rear_cap`
- `throat_disc`

OSSE with enclosure:

- `horn_wall`
- `throat_disc`
- `enc_front`
- `enc_side`
- `enc_rear`
- `enc_edge`

### 2. Mesh sizing classes

These are for meshing semantics, not solver BC semantics.

Examples:

- `horn_inner_axial`
- `horn_rear_domain`
- `throat_source_region`
- `enclosure_front`
- `enclosure_rear`
- `enclosure_edge`

For freestanding thickened horns, preserve the logical split:

- `inner_wall` and `mouth_rim` follow axial `throat_res -> mouth_res`
- `outer_wall` and `rear_cap` follow `rear_res`
- `throat_disc` follows `throat_res`

### 3. Solver boundary classes

These are physics classes, not geometry names.

- `RIGID_WALL`
- `ACOUSTIC_SOURCE`
- `IMPEDANCE_APERTURE` for future passive resistive / foam openings
- `SYMMETRY`

`throat_disc` should remain a geometry identity. It should map to `ACOUSTIC_SOURCE` only when the active simulation setup says it is the driven boundary.

## What Is Still Architecturally Wrong

### Frontend

1. The module layer is incomplete.
   - `src/modules/export/index.js` still imports internals from `src/export`, `src/geometry`, and `src/solver`.
   - `src/modules/simulation/index.js` still imports `src/geometry/pipeline.js` and `src/solver/waveguidePayload.js`.
   - `src/modules/geometry/index.js` and `src/modules/design/index.js` are currently thin wrappers over legacy files instead of the true owners of those concerns.

2. App/UI code still reaches into lower layers directly.
   - `src/app/scene.js` imports both `GeometryModule` and `src/geometry/pipeline.js`.
   - `src/app/App.js` and `src/app/events.js` depend directly on `src/ui/*`.
   - `src/ui/simulation/SimulationPanel.js` imports solver, workspace, result, and UI code directly.

3. There is duplicated normalization logic.
   - Segment/resolution normalization exists in `src/geometry`, `src/modules/export/index.js`, and `src/solver/waveguidePayload.js`.
   - This guarantees drift because different paths can "fix" the same input differently.

4. UI workflow code has circular and compatibility structure.
   - `src/ui/simulation/jobActions.js` explicitly documents a circular reference with `polling.js`.
   - `src/ui/simulation/actions.js` is a backward-compatibility barrel.

5. The solver client still carries legacy fallback code.
   - `src/solver/index.js` still contains `mockBEMSolver`.

6. Contract drift exists around tags.
   - `docs/PROJECT_DOCUMENTATION.md` still describes broader `1/2/3/4` frontend tag semantics.
   - `src/geometry/tags.js` currently only produces wall/source tagging in runtime code.
   - Tests currently assert that secondary/interface tags are absent in the JS pipeline.

### Backend

1. `server/app.py` is still a compatibility hub.
   - It re-exports models, routes, services, runtime state, and helper functions for old imports and tests.
   - That blocks clean package boundaries and encourages patching internals through the app module.

2. Request models know too much about solver internals.
   - `server/models.py` imports solver normalization with a fallback path.
   - `server/solver_bootstrap.py` duplicates validators and availability behavior.

3. Route code still performs downstream repair.
   - `server/api/routes_simulation.py` mutates `waveguide_params["quadrants"] = 1234` for OCC adaptive solve instead of requiring the caller or use case to construct the correct request upstream.

4. There is no explicit shared contract package.
   - Frontend payload builders, backend request models, docs, and tests all define overlapping versions of the same contract.

## Execution Strategy

Do this in order. Do not begin with broad file moves. Freeze contracts first, then enforce boundaries, then relocate code.

Each phase should land as a separate PR or commit series with:

- code changes
- contract tests
- doc updates
- deletion of obsolete glue introduced by earlier phases

## Phase 0: Freeze The Contracts

### Objective

Define the exact domain vocabulary before touching structure.

### Tasks

1. Write a canonical contract doc for:
   - geometry face identities
   - mesh sizing classes
   - solver boundary classes
   - numeric physical tags used by BEM / Gmsh output
2. Decide whether the frontend canonical payload should continue exposing only solver-facing numeric tags, or whether it should also carry face identity metadata alongside them.
3. Resolve the current tag drift:
   - if JS runtime should only emit wall/source tags, update docs to say that
   - if richer tags are required, implement them in the geometry source pipeline before any export or solve stage
4. Define one authoritative normalization spec for:
   - angular segments
   - length segments
   - quadrants
   - enclosure resolution fields
   - unit metadata

### Exit Criteria

- One written contract, referenced by docs and tests
- No contradictory tag semantics across docs, runtime code, and tests

### Verification

- `node --test tests/mesh-payload.test.js`
- `node --test tests/geometry-artifacts.test.js`
- `node --test tests/waveguide-payload.test.js`
- `cd server && python3 -m unittest tests.test_api_validation`

## Phase 1: Enforce Dependency Boundaries

### Objective

Make invalid imports impossible before moving logic.

### Tasks

1. Define allowed dependency directions for frontend:
   - `app -> modules`
   - `modules -> internal domain packages`
   - `ui -> modules` or `ui -> ui-only helpers`, but not `ui -> geometry/export/solver` directly
   - `viewer -> geometry outputs only`
2. Define allowed dependency directions for backend:
   - `api -> services/contracts`
   - `services -> solver/contracts/db`
   - `solver -> solver-internal modules only`
3. Add an import-boundary check script to CI.
   - Start with a simple `rg` or AST-based test if needed.
4. Add a rule that tests must patch/import the true module they target, not `server/app.py` as a shortcut.

### Exit Criteria

- Boundary rules are encoded in tests or lint
- New cross-layer imports fail CI

### Verification

- New import-boundary test suite passes
- `npm test`
- `npm run test:server`

## Phase 2: Consolidate Frontend Input Normalization

### Objective

Create one place where raw state becomes prepared domain input.

### Tasks

1. Move all parameter normalization into the design/input module.
   - absorb logic now split across `src/geometry/params.js`, `src/modules/export/index.js`, `src/solver/waveguidePayload.js`, and config import helpers
2. Create one prepared-domain type used by geometry, export, and simulation use cases.
3. Remove duplicate helpers such as repeated segment snapping and resolution defaulting.
4. Make invalid input fail here, not later in export or solver payload builders.

### Exit Criteria

- Every frontend flow consumes the same prepared input object
- No export/simulation module re-normalizes already prepared values

### Verification

- `node --test tests/waveguide-payload.test.js`
- `node --test tests/export-module.test.js`
- `node --test tests/app-mesh-integration.test.js`

## Phase 3: Make Geometry The Source Of Truth

### Objective

Put geometry topology, face identity, and solver-tag mapping in one place.

### Tasks

1. Expand the geometry pipeline to produce explicit geometry face identities.
2. Add a deterministic mapping layer:
   - geometry face identity -> mesh sizing class
   - geometry face identity -> solver boundary class
   - solver boundary class -> numeric physical tag
3. Stop using raw numeric tags as the only intermediate meaning inside the frontend.
4. Keep `src/geometry` responsible for canonical mesh payload assembly.
5. Decide whether OCC meshing consumes:
   - raw prepared parameters only, or
   - an explicit geometry contract generated by the frontend
   and document that boundary clearly.

### Exit Criteria

- Geometry is the only place that knows how a surface should be classified
- Export and simulation paths consume geometry classifications, not re-derive them

### Verification

- `node --test tests/mesh-payload.test.js`
- `node --test tests/geometry-artifacts.test.js`
- `node --test tests/enclosure-regression.test.js`
- `node --test tests/export-gmsh-pipeline.test.js`

## Phase 4: Rebuild Export And Simulation As Real Use-Case Modules

### Objective

Turn `src/modules/*` into real application modules instead of pass-through wrappers.

### Tasks

1. Move export orchestration logic out of `src/app/exports.js` into `src/modules/export`.
2. Move simulation request-building logic out of UI code into `src/modules/simulation`.
3. Make `src/app` call module APIs only.
4. Remove direct `src/app -> src/geometry`, `src/app -> src/solver`, and `src/app -> src/ui/*settings*` imports where a module boundary should exist.
5. Replace the current mixed payload assembly with explicit use cases:
   - `prepareViewportMesh`
   - `prepareCanonicalSimulationMesh`
   - `prepareOccAdaptiveSolveRequest`
   - `prepareExportArtifacts`

### Exit Criteria

- `src/app` becomes orchestration only
- `src/modules/*` own the workflows end to end

### Verification

- `node --test tests/ui-module.test.js`
- `node --test tests/simulation-flow.test.js`
- `node --test tests/export-module.test.js`
- `npm test`

## Phase 5: Untangle UI State And Circular Workflow Logic

### Objective

Keep DOM code as an adapter layer, not a second application layer.

### Tasks

1. Split `SimulationPanel` into:
   - a UI adapter
   - a simulation controller/store
2. Remove the `jobActions.js` <-> `polling.js` cycle by introducing a single job orchestration module.
3. Stop accessing `GlobalState` directly from deep UI workflow files.
4. Centralize workspace/task-manifest side effects behind one simulation workspace service.
5. Keep `src/ui` focused on rendering controls, reading DOM inputs, and showing messages.

### Exit Criteria

- No documented circular imports remain in UI workflow code
- UI modules no longer own simulation business rules

### Verification

- `node --test tests/simulation-flow.test.js`
- `node --test tests/ui-module.test.js`
- any simulation UI regression tests added in this phase

## Phase 6: Remove Backend Compatibility Glue

### Objective

Make backend packages talk through explicit APIs instead of re-export hubs.

### Tasks

1. Stop using `server/app.py` as a compatibility surface for tests.
2. Move shared API models into a dedicated contracts package.
3. Remove solver-aware fallback imports from `server/models.py`.
4. Shrink `server/solver_bootstrap.py` so it only reports dependency availability and never owns contract logic.
5. Make `server/api/routes_*` depend on contracts and services only.
6. Move direct route validation that is really domain validation into dedicated validators or service-layer use cases.

### Exit Criteria

- `server/app.py` is thin application assembly only
- backend tests import the real route/service/solver modules they exercise

### Verification

- `cd server && python3 -m unittest tests.test_updates_endpoint`
- `cd server && python3 -m unittest tests.test_api_validation`
- `cd server && python3 -m unittest tests.test_dependency_runtime`
- `npm run test:server`

## Phase 7: Eliminate Downstream Repair Logic

### Objective

Reject invalid requests upstream instead of mutating them later.

### Tasks

1. Remove request mutation in `server/api/routes_simulation.py`.
   - If full-domain OCC solve is required, build that request correctly before submission.
2. Audit every `normalize/coerce/fallback/retry` path and classify it as:
   - valid boundary parsing
   - valid runtime resilience
   - invalid architecture glue
3. Delete the invalid architecture glue.
4. Add contract tests that prove:
   - wrong quadrants are rejected or corrected only at the designated request-construction boundary
   - source tags are present before solve submission
   - mesh tagging is not rewritten in later stages

### Exit Criteria

- No route/service/solver layer silently rewrites semantic input data
- Remaining fallback code is runtime-only, not contract-repair code

### Verification

- `cd server && python3 -m unittest tests.test_api_validation`
- `cd server && python3 -m unittest tests.test_solver_tag_contract`
- `cd server && python3 -m unittest tests.test_mesh_validation`

## Phase 8: Delete Legacy Paths And Rename Docs To Match Reality

### Objective

Finish the cleanup by removing dead branches and stale language.

### Tasks

1. Remove `mockBEMSolver` if it is no longer part of the supported runtime.
2. Remove backward-compatibility barrels that exist only to preserve old file shapes.
3. Remove deprecated aliases once their callers are gone.
4. Update docs so they describe:
   - current tag taxonomy
   - current module boundaries
   - actual `/api/mesh/build` and `/api/solve` behavior
5. Add a simple architecture overview diagram after the refactor lands.

### Exit Criteria

- No "legacy fallback", "backward compat", or documented circular import remains in runtime code without an explicit reason
- Docs describe the shipped architecture, not the pre-refactor one

### Verification

- `npm test`
- `npm run test:server`
- manual import-boundary audit

## Suggested Implementation Order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6
8. Phase 7
9. Phase 8

Do not reorder Phase 0 and Phase 1. If the contract and boundary rules are not frozen first, the rest of the refactor will become another round of implicit behavior and patch-up logic.

## Definition Of Done

The architecture cleanup is done when all of the following are true:

- Every runtime flow crosses modules through explicit APIs only.
- Geometry identity, mesh sizing, and solver BC classes are separate and documented.
- No stage rewrites semantic input created by an earlier stage.
- `server/app.py` is only app assembly.
- `src/app` is only frontend assembly.
- There are no intentional circular imports in active runtime code.
- `npm test` passes.
- `npm run test:server` passes.
- `docs/PROJECT_DOCUMENTATION.md` matches the final runtime behavior.
