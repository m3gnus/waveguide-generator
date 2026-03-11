# Architecture Cleanup Plan

## Execution Status

- Started: March 11, 2026
- Current phase: Phase 8 (in progress)
- Completed:
  - Phase 0 contract freeze (docs + contract tests aligned to runtime)
  - Phase 1 dependency boundary enforcement (frontend/backend import-boundary suites + server tests decoupled from `app.py` import shortcuts)
  - Phase 2 input normalization consolidation (DesignModule now owns OCC simulation/export normalization helpers consumed by module use cases)
  - Phase 3 make geometry the source of truth (put geometry topology, face identity, and solver-tag mapping in one place)
  - Phase 4 rebuild export and simulation as real use-case modules
  - Phase 5 untangle UI state and circular workflow logic
  - Phase 6 remove backend compatibility glue (routes now depend on contracts/services only, solver bootstrap reduced to dependency status, `/api/solve` validation moved into services)
  - Phase 7 eliminate downstream repair logic (queued OCC request construction moved to the submission boundary, source-tag invariants enforced before solve submission, runner rewrites replaced with validation)
- In progress:
  - Phase 8 delete legacy paths and rename docs to match reality

## Session Shortcut

Use `$architecture-cleanup-next` in a fresh Codex window to continue the current phase as a sequence of small slices. The skill reads this plan, checks recent commits, picks the smallest coherent remaining slice, hands each slice to a fresh Codex 5.3 subagent, and is expected to continue until the phase is complete or blocked, with tests, doc updates, and a commit per slice.

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

## Baseline Architectural Problems

This section captures the pre-cleanup snapshot that motivated the phase plan. Use the execution status and per-phase implementation notes above/below as the source of truth for what has already been resolved.

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

### Implementation Notes (Completed March 11, 2026)

- Added frontend boundary suite: `tests/architecture-boundaries.test.js`
  - Encodes dependency direction rules and blocks new cross-layer imports.
  - Tracks currently-known legacy cross-layer edges via explicit temporary exceptions.
- Added backend boundary suite: `server/tests/test_import_boundaries.py`
  - Enforces:
    - `server/tests` do not import `app.py` as a shortcut.
    - `server/api` does not import `app.py`.
    - `server/services` does not import `server/api`.
    - `server/solver` does not import `server/api` or `server/services`.
- Migrated existing server tests to true module imports:
  - `server/tests/test_api_validation.py`
  - `server/tests/test_dependency_runtime.py`
  - `server/tests/test_updates_endpoint.py`
  - `server/tests/test_job_persistence.py`

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

### Implementation Notes (Planned March 11, 2026)

#### Scope lock for this phase

In scope:

1. Make `DesignModule` the only raw-input normalization boundary for frontend runtime flows.
2. Ensure export and simulation use cases consume prepared input from `DesignModule` instead of re-normalizing values independently.
3. Keep compatibility behavior only as thin adapters during transition and schedule deletion in the same phase.

Out of scope (handled in later phases):

1. App/UI orchestration movement (`src/app` and `src/ui` structural refactor) - Phase 4 and Phase 5.
2. Geometry face identity expansion and classification mapping - Phase 3.
3. Backend contract/package re-layout - Phase 6+.

#### Prepared input contract decision for Phase 2

The prepared frontend input object remains the shared handoff type, but normalization ownership changes:

1. Raw app/state input -> normalized prepared input happens once in `DesignModule.task(...)`.
2. `SimulationModule.import(...)` and export entrypoints should accept raw input only via `DesignModule`, or explicitly require already-prepared params (`importPrepared`-style APIs).
3. `buildWaveguidePayload(...)` should map prepared values to request schema and enforce payload shape only; it should not be the source of independent defaulting rules that diverge from `DesignModule`.

#### File-level execution sequence

1. Normalize at one boundary:
   - Consolidate normalization rules used by geometry/export/solver payload preparation under `src/modules/design/index.js` and `src/geometry/params.js` (single owner path).
2. Remove export-side duplicate normalization:
   - Refactor `src/modules/export/index.js` helpers (`normalizeAngularSegments`, resolution scaling/default fallbacks, segment coercion) so they consume prepared values rather than re-deriving defaults.
3. Remove simulation-side duplicate normalization:
   - Refactor `src/modules/simulation/index.js` + `src/solver/waveguidePayload.js` interface so OCC request building uses prepared values without a second normalization policy.
4. Keep `src/app/*` behavior stable:
   - Wire callers through `DesignModule.importState(...)` / `DesignModule.task(...)` where not already done, without broader app-layer movement (reserved for Phase 4).

#### Test gates for this phase

Run in this order while implementing:

1. `node --test tests/design-module.test.js`
2. `node --test tests/geometry-params.test.js`
3. `node --test tests/waveguide-payload.test.js`
4. `node --test tests/simulation-module.test.js`
5. `node --test tests/export-module.test.js`
6. `node --test tests/app-mesh-integration.test.js`
7. `npm test`

Definition for "Phase 2 complete":

1. No normalization helpers remain duplicated across design/export/simulation paths.
2. Existing behavior covered by `tests/waveguide-payload.test.js`, `tests/export-module.test.js`, and `tests/app-mesh-integration.test.js` is preserved or intentionally updated with matching doc changes.
3. `docs/PROJECT_DOCUMENTATION.md` and `docs/CANONICAL_CONTRACT.md` are updated in the same change set if normalization behavior shifts.

### Implementation Notes (Completed March 11, 2026)

Completed in this step:

1. Added shared OCC normalization helpers to `DesignModule`:
   - `prepareOccSimulationParams(...)`
   - `prepareOccExportParams(...)`
   - `DesignModule.output.occSimulationParams(...)`
   - `DesignModule.output.occExportParams(...)`
2. Refactored `ExportModule` OCC mesh flow to consume design-layer OCC export normalization before request/payload assembly.
3. Refactored `SimulationModule` OCC-adaptive request builder to consume design-layer OCC simulation normalization.
4. Added/updated contract tests:
   - `tests/design-module.test.js` (new OCC normalization coverage)
   - `tests/export-module.test.js` (new assertion that OCC request payload uses design-layer export normalization)
5. Refactored `src/solver/waveguidePayload.js` OCC field handling to map/validate DesignModule-normalized inputs instead of running an independent OCC normalization policy.
6. Updated `tests/waveguide-payload.test.js` to assert the new boundary contract:
   - defaults/normalization are owned by `DesignModule` OCC helpers
   - `buildWaveguidePayload(...)` rejects unprepared OCC-required fields
7. Updated contract docs to reflect new ownership:
   - `docs/PROJECT_DOCUMENTATION.md`
   - `docs/CANONICAL_CONTRACT.md`

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

### Implementation Notes (Completed March 11, 2026)

Completed in this step:

1. Expanded geometry pipeline to produce explicit geometry face identities (`inner_wall`, `enc_front`, etc.) natively in `src/geometry/engine/mesh/`.
2. Created deterministic mapping layer in `src/geometry/tags.js`:
   - `FACE_IDENTITY` -> `MESH_SIZING_CLASS`
   - `FACE_IDENTITY` -> `SOLVER_BOUNDARY_CLASS`
   - `SOLVER_BOUNDARY_CLASS` -> tag values
3. Stopped using raw numeric tags as the initial intermediate meaning. The pipeline emits `groups` containing explicit semantic faces, which `buildSurfaceTags` maps deterministically.
4. Decision on OCC boundary: OCC meshing consumes raw prepared parameters only (`occExportParams`, `occSimulationParams`). The frontend geometry mesh contract is strictly for JavaScript visualization and BEM payloads. The backend boundary remains parameter-based, not an explicit geometry payload upload. Documentation has been updated to reflect this boundary.

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

### Implementation Notes (Completed, March 11, 2026)

Completed in this step:

1. Added explicit export use case `prepareExportArtifacts(...)` in `src/modules/export/useCases.js` as the Phase 4 export-artifact workflow boundary.
2. Kept `buildExportMeshFromParams(...)` as a thin compatibility alias to avoid breaking existing callers/tests during Phase 4 migration.
3. Updated export pipeline tests to validate `prepareExportArtifacts(...)` as the primary OCC export use case.
4. Updated project documentation references from removed `src/app/exports.js` paths to current module use-case paths.
5. Added `src/modules/ui/useCases.js` as the app-facing settings/workspace boundary for UI settings operations.
6. Refactored `src/app/App.js`, `src/app/events.js`, and `src/app/scene.js` to consume settings/workspace operations through `src/modules/ui/useCases.js` instead of importing `src/ui/settings/*` and `src/ui/workspace/*` directly.
7. Tightened `tests/architecture-boundaries.test.js` by removing now-resolved `src/app -> src/ui/settings|workspace` legacy exception edges.
8. Added `createAppParamPanel(...)` and `loadSimulationPanelModule(...)` use cases to `src/modules/ui/useCases.js` so app assembly no longer imports `src/ui/paramPanel.js` or `src/ui/simulationPanel.js` directly.
9. Refactored `src/app/App.js` to consume those new UI module use cases and removed the corresponding legacy app-to-ui exceptions from `tests/architecture-boundaries.test.js`.
10. Added app-facing wrappers in `src/modules/ui/useCases.js` for feedback and file-ops interactions used by app assembly flows.
11. Refactored `src/app/configImport.js`, `src/app/events.js`, and `src/app/updates.js` to consume those wrappers instead of importing `src/ui/feedback.js` and `src/ui/fileOps.js` directly.
12. Tightened `tests/architecture-boundaries.test.js` by removing now-resolved legacy exceptions for app-level feedback/file-ops imports.
13. Added `createSimulationClient()` to `src/modules/simulation/useCases.js` and refactored `src/ui/simulation/SimulationPanel.js` to consume a module API instead of importing `src/solver/index.js` directly.
14. Removed remaining temporary import-boundary exceptions (`src/app/params.js -> src/geometry/index.js`, `src/ui/simulation/SimulationPanel.js -> src/solver/index.js`) and kept behavior stable with a local `isNumericString` helper in `src/app/params.js`.
15. Moved additional simulation business logic from `src/ui/simulation/jobActions.js` into `src/modules/simulation/useCases.js`:
   - simulation config validation
   - queued-job metadata/script assembly
   - cancelled-job state construction
   - failed-job cleanup response reconciliation
   `jobActions.js` now delegates those rules through module use cases while keeping DOM/event concerns in UI files.

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

### Implementation Notes (In Progress, March 11, 2026)

Completed in this step:

1. Added `src/ui/simulation/jobOrchestration.js` as a shared UI orchestration boundary for polling/job state helpers (`setPollTimer`, `clearPollTimer`, `setActiveJob`).
2. Removed the runtime `jobActions.js` <-> `polling.js` circular dependency:
   - `jobActions.js` now imports orchestration helpers from `jobOrchestration.js`.
   - `polling.js` now reuses/re-exports orchestration helpers from `jobOrchestration.js`.
3. Kept existing external APIs stable by preserving `polling.js` helper exports for existing callers/tests.
4. Split `SimulationPanel` into a UI adapter backed by a dedicated controller/store module (`src/ui/simulation/controller.js`):
   - state initialization is now owned by `createSimulationControllerStore(...)`
   - `SimulationPanel` binds existing panel fields to controller state through explicit property delegation
5. Moved simulation job-restore orchestration out of `SimulationPanel` into `restoreSimulationControllerJobs(...)` so the panel remains focused on UI adapter concerns.
6. Added controller-level regression coverage in `tests/simulation-controller.test.js` for:
   - controller store defaults
   - adapter/controller state binding
   - restore flow behavior and polling trigger semantics
7. Removed direct `GlobalState` access from deep simulation UI workflow files:
   - `src/ui/simulation/settings.js` now reads/updates state through `src/modules/simulation/useCases.js` facade helpers.
   - `src/ui/simulation/jobActions.js` now restores scripted state through `applySimulationJobScriptState(...)` instead of mutating `GlobalState` directly.
8. Added import-boundary regression coverage to block new `src/ui/simulation/* -> src/state.js` direct imports (`tests/architecture-boundaries.test.js`).
9. Centralized simulation workspace side effects behind `src/modules/simulation/useCases.js`:
   - workspace index restore/rebuild is now owned by `readSimulationWorkspaceJobs(...)`
   - index writes are serialized through `syncSimulationWorkspaceIndex(...)`
   - task manifest writes are routed through `syncSimulationWorkspaceJobManifest(...)`
10. Removed direct `src/ui/simulation/* -> src/ui/workspace/*` imports from:
   - `src/ui/simulation/controller.js`
   - `src/ui/simulation/jobTracker.js`
   - `src/ui/simulation/jobActions.js`
   - `src/ui/simulation/polling.js`
11. Added regression coverage in `tests/simulation-module.test.js` for simulation workspace facade behavior (manifest sync, index rebuild, index write round-trip).
12. Tightened `tests/architecture-boundaries.test.js` to block new direct `src/ui/simulation/* -> src/ui/workspace/*` imports.
13. Moved `SimulationPanel` runtime assembly/disposal behind the controller layer:
   - `createSimulationPanelRuntime(...)` now owns controller store creation, adapter state binding, and UI coordinator assembly
   - `restoreSimulationPanelRuntime(...)` now mediates panel restore through the controller runtime
   - `disposeSimulationPanelRuntime(...)` now owns timer/coordinator teardown
14. Reduced `src/ui/simulation/SimulationPanel.js` to a thinner adapter that delegates lifecycle/state ownership to `src/ui/simulation/controller.js`.
15. Added controller runtime regression coverage in `tests/simulation-controller.test.js` for runtime creation, restore delegation, and dispose teardown.
16. Moved remaining controller-state mutations out of `src/ui/simulation/jobActions.js` into `src/ui/simulation/controller.js`:
   - result-cache loading (`ensureSimulationControllerJobResults(...)`)
   - queued job creation/persistence (`queueSimulationControllerJob(...)`)
   - export metadata persistence (`recordSimulationControllerExport(...)`)
   - feed removal / failed-job clearing / cancellation state (`removeSimulationControllerJob(...)`, `clearSimulationControllerJobs(...)`, `cancelSimulationControllerJob(...)`)
17. Reduced `jobActions.js` further toward a DOM/message adapter that delegates controller mutations instead of mutating job store state directly.
18. Expanded `tests/simulation-controller.test.js` to lock down those controller-level job mutation/result-loading behaviors.
19. Moved polling-time remote job reconciliation out of `src/ui/simulation/polling.js` into `src/ui/simulation/controller.js` via `reconcileSimulationControllerRemoteJobs(...)`:
   - remote backend list fetch + UI-job normalization
   - local/remote merge reconciliation (including lost-active-job error conversion)
   - active-job/current-job synchronization and persistence
   - manifest sync delegation with error callback hook
20. Reduced `polling.js` to a thinner UI timer/stage wrapper that delegates reconciliation and result-cache mutation to controller helpers.
21. Expanded `tests/simulation-controller.test.js` with remote reconciliation coverage to keep the polling/controller boundary contract stable.
22. Moved solver-backed submission/cancellation orchestration out of `src/ui/simulation/jobActions.js` into `src/ui/simulation/controller.js`:
   - `prepareSimulationControllerSubmission(...)` now owns OCC adaptive submission request preparation
   - `submitSimulationControllerJob(...)` now owns backend health checks, solver submission, and queued-job persistence
   - `stopSimulationControllerJob(...)` now owns stop-request handling while preserving local cancellation fallback
23. Reduced `jobActions.js` further to a DOM/progress adapter that reads inputs, renders UI state, and delegates submission/stop workflow through controller helpers.
24. Expanded `tests/simulation-controller.test.js` with submission/cancellation boundary coverage so the controller owns solver workflow contracts explicitly.
25. Removed the temporary `src/ui/simulation/actions.js` compatibility barrel and rewired the remaining runtime/tests to import focused submodules directly (`jobActions.js`, `polling.js`, `meshDownload.js`), leaving no documented compatibility shim in the Phase 5 simulation UI path.

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

### Implementation Notes (In Progress, March 11, 2026)

Completed in this step:

1. Removed the remaining compatibility re-exports from `server/app.py`; it now exposes only FastAPI assembly (`create_app()`, `app`, CORS/lifespan wiring, runtime banner output).
2. Added an import-boundary test that blocks `server/app.py` from reintroducing model/service/route-handler shortcut imports.
3. Updated backend architecture docs so route ownership points at `server/api/routes_*` modules instead of `server/app.py`.
4. Moved the shared request/response models into `server/contracts/` and rewired backend routes, services, and tests to import that package directly.
5. Reduced `server/models.py` to a compatibility alias so internal code no longer depends on it.
6. Removed solver-aware device-mode fallback imports from the contract definitions; contract validation now normalizes aliases locally inside `server/contracts/`.
7. Moved route-facing solver/OCC adapters into `server/services/solver_runtime.py`, so `server/api/routes_*` now import service-layer runtime APIs instead of `server/solver_bootstrap.py` or `server/solver/*`.
8. Moved `/api/solve` domain validation into `server/services/simulation_validation.py`, including OCC shell checks and adaptive solve request validation.

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

### Implementation Notes (Completed, March 11, 2026)

Completed in this step:

1. Kept `validate_submit_simulation_request()` pure: it now validates and returns normalized OCC waveguide params without mutating the caller-owned `SimulationRequest`.
2. Added an explicit submission-boundary builder in `server/services/simulation_validation.py`, so `/api/solve` queues a corrected full-domain OCC request while preserving the original request object.
3. Updated contract coverage to assert the full-domain quadrant correction happens only in the queued submission payload, not by rewriting the input request in place.
4. Added submission-time validation that rejects any solve request whose mesh payload lacks source tag `2`, so the invariant is enforced before the solver runtime starts.
5. Removed the remaining OCC runner repair logic: queued requests with non-full-domain quadrants now fail explicitly, and canonical OCC `surfaceTags` pass through to solver mesh preparation unchanged.
6. Audited the remaining backend `normalize/coerce/fallback/retry` sites: request/contract parsing stays at the boundary, while the remaining fallback and retry logic is runtime-only (OpenCL/device/runtime resilience and OCC builder internals), not semantic input repair.

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

### Implementation Notes (In Progress, March 11, 2026)

1. Removed the dead `mockBEMSolver` export from `src/solver/index.js`; the solver client now documents backend-only runtime behavior with no supported local-result fallback path.
2. Added a solver API regression test proving the public solver surface no longer exposes the removed fallback helper.
3. Updated roadmap/backlog docs so Phase 8 and future cleanup notes no longer describe `mockBEMSolver` as a remaining supported runtime branch.

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
