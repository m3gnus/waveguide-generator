# Backlog

Last updated: March 11, 2026

This file is the active source of truth for unfinished product and engineering work.
Superseded planning inputs were folded in from:
- `docs/archive/FUTURE_ADDITIONS_2026-03-11.md`
- `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`
- selected deferred notes in archived readiness documents

## Working Rules

### Upstream-downstream integrity

Modules must not compensate for defects that belong upstream. Each module should receive correct input and fail visibly if it does not. When downstream code contains a workaround for an upstream defect, the fix belongs in the upstream module, not in the workaround.

### Docs and audit discipline

Keep durable decisions in `docs/architecture.md`, active work in this file, and per-module contracts in `docs/modules/`. Put generated audits, comparisons, and experiment output under `research/`.

## Current Baseline

status as of date:
- March 11, 2026
- The architecture cleanup plan is complete.
- The settings modal exists, viewer settings persist, and the folder workspace manifest/index model exists.
- Active runtime docs are `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `tests/TESTING.md`, `server/README.md`, and `AGENTS.md`.
- Remaining work is now a mix of user-reported bugs, unfinished simulation-management product work carried over from earlier planning, and a smaller set of hardening/research follow-ups.

Remaining work:
- Fix real cancellation behavior for running backend solves.
- Improve simulation UX around mesh visibility, formula entry, and settings-to-runtime parity.
- Finish the remaining simulation-management roadmap slices that were carried into this backlog.
- Consolidate durable architecture/contracts into smaller maintained docs over time.
- Keep diagnostics, regression coverage, and optional engineering cleanup focused on shipped value.

## Recommended Execution Order

Work the backlog from upstream runtime truth to downstream UX:

1. Runtime job lifecycle and solve-request contract correctness.
2. Canonical solve diagnostics and regression lanes that lock those contracts in.
3. Simulation UI parity work that depends on trustworthy runtime state.
4. Folder-backed export/task-history flows that build on stable simulation metadata.
5. Ratings/filtering, docs cleanup, and optional research tracks.

## Active Backlog

### P0 Upstream Runtime And Contract Seams

- [x] Make the Stop action cancel backend work cooperatively instead of only updating UI state.
  Source: user report; `server/api/routes_simulation.py`; `server/services/simulation_runner.py`; `src/ui/simulation/controller.js`.
  Relevant: Yes. The current route marks running jobs as cancelled, but the worker thread continues through meshing/solve and only notices cancellation after the solve returns.
  Will it improve the program: Yes. It prevents wasted compute, misleading UI, and inconsistent job state.
  Research findings: `stop_simulation()` marks running jobs `cancelled` immediately, `run_simulation()` only checks cancellation after `solver.solve(...)` returns, and `stopSimulationControllerJob()` / `stopSimulation()` also flip the frontend feed to `cancelled` immediately. This is the highest-leverage runtime seam because several downstream UX states currently trust a cancellation that did not happen yet.
  Completed: March 11, 2026. Running-job stops now remain `running` with stage `cancelling` until the worker acknowledges the stop request, queued jobs still cancel immediately, the runner checks `cancellation_requested` before expensive stages and threads a cooperative cancellation callback through OCC preparation and both solver frequency loops, and the frontend now renders a truthful “stopping” state instead of faking local `cancelled` status. Backend/API, solver-hardening, and frontend controller/module regressions now cover queued cancellation, running cancellation requests, worker acknowledgement, and UI stop semantics.

- [x] Wire Simulation Basic settings all the way into `/api/solve` payloads and runtime availability messaging.
  Source: earlier simulation-management planning notes; `src/ui/settings/simBasicSettings.js`; `src/ui/simulation/jobActions.js`.
  Relevant: Yes. The settings UI exists, but `runSimulation()` still hardcodes `frequencySpacing: 'log'` and `deviceMode: 'auto'` instead of consuming the saved Simulation Basic settings consistently.
  Will it improve the program: Yes. It makes settings trustworthy and closes the gap between visible controls and actual solve behavior.
  Research findings: `src/ui/settings/simBasicSettings.js` already persists `deviceMode`, `meshValidationMode`, `frequencySpacing`, `useOptimized`, `enableSymmetry`, and `verbose`, and the modal already polls `/health` for device availability. The runtime submit path still only forwards hardcoded `frequencySpacing` / `deviceMode` from `src/ui/simulation/jobActions.js`, while `src/solver/index.js` only serializes `mesh_validation_mode`, `frequency_spacing`, and `device_mode`.
  Completed: March 11, 2026. `runSimulation()` now reads all persisted Simulation Basic settings, the `/api/solve` serializer forwards only valid runtime overrides while leaving backend defaults authoritative for invalid/unset values, the inline device-mode status derives its message from `/health` mode availability, and the request-contract/UI tests now cover `device_mode`, `mesh_validation_mode`, `frequency_spacing`, `use_optimized`, `enable_symmetry`, and `verbose`.

- [x] Add an explicit no-Gmsh regression lane for `/api/solve`.
  Source: archived future additions doc.
  Relevant: Yes. Current tests cover runtime gating and tag contracts, but not a dedicated “Gmsh unavailable while canonical `/api/solve` remains valid” lane.
  Will it improve the program: Yes. It protects a useful runtime mode from future regressions and makes the solve contract less fragile while OCC-specific work continues.
  Research findings: `server/tests/test_dependency_runtime.py` and `server/tests/test_api_validation.py` already assert OCC runtime failure paths, and `server/tests/test_mesh_validation.py` covers `use_gmsh=True` behavior. There is still no test that forces `occBuilderReady=False` while keeping solver readiness intact and proves canonical payload solves are still accepted.
  Completed: March 11, 2026. `server/tests/test_dependency_runtime.py` now locks the `solverReady=true` / `occBuilderReady=false` configuration, proves canonical `/api/solve` submission still enqueues successfully, and asserts that the OCC/Gmsh dependency branch is not consulted for canonical payloads.

- [x] Introduce a public job-runtime service surface and stop letting `server/api/routes_simulation.py` mutate job state internals directly.
  Source: architecture audit on March 11, 2026; `server/api/routes_simulation.py`; `server/services/job_runtime.py`.
  Relevant: Yes. The route layer currently imports private helpers plus mutable runtime state from `job_runtime` and writes to the DB/cache/queue directly instead of going through a service boundary.
  Will it improve the program: Yes. It restores the intended API -> services -> solver layering and makes job lifecycle behavior easier to test and evolve safely.
  Research findings: `job_runtime.py` declares ownership of the in-memory cache, queue, and scheduler, but `routes_simulation.py` still imports `_build_config_summary`, `_set_job_fields`, `_drain_scheduler_queue`, `jobs`, `job_queue`, `running_jobs`, `jobs_lock`, and `_jrt.db`. The import-boundary tests pass because they only check package-level edges, not direct access to private service internals.
  Best approach: Add explicit public service functions for create/stop/list/delete/result retrieval, migrate the route handlers to those functions, and tighten the backend boundary tests to reject direct API access to underscore-prefixed service internals.
  Completed: March 11, 2026. `server/services/job_runtime.py` now owns the public create/stop/status/result/artifact/list/delete operations, `server/api/routes_simulation.py` maps HTTP semantics onto that service surface instead of mutating DB/cache/queue internals directly, and backend regressions now cover the public submit path plus an import-boundary rule that blocks `routes_simulation.py` from importing private `job_runtime` names again.

- [x] Add pre-submit canonical tag diagnostics to the simulation UI.
  Source: archived future additions doc.
  Relevant: Yes. Contract validation exists, but users still do not get a concise pre-submit view of tag counts or missing-source problems.
  Will it improve the program: Yes. It shortens debug loops before a solve request is sent.
  Research findings: frontend preflight already rejects malformed canonical payloads in `src/solver/index.js`, backend validation rejects tag/triangle mismatches and missing tag `2`, and `prepareCanonicalSimulationMesh()` already has the data needed to count triangles per tag. The gap is purely the absence of a small read-only UI summary before submit.
  Completed: March 11, 2026. The simulation panel now renders a read-only canonical diagnostics card before submit with vertex/triangle totals, counts for tags `1/2/3/4`, and warnings for missing source coverage or tag/triangle mismatches, driven by a lightweight frontend summary helper over the canonical payload.

### P1 Simulation UX That Depends On Runtime Truth

- [x] Show simulation mesh vertex/triangle counts in the stats widget once the `.msh`/simulation mesh is built.
  Source: user request; `src/app/scene.js`; `src/modules/simulation/useCases.js`; `server/services/simulation_runner.py`.
  Relevant: Yes. The current stats widget always shows viewport tessellation counts from `renderModel()`, which diverges from the actual simulation mesh.
  Will it improve the program: Yes. It gives users the complexity they actually submitted to BEM instead of a potentially misleading viewport proxy.
  Research findings: `src/app/scene.js` always writes viewport counts into `app.stats`, while `server/services/simulation_runner.py` stores `mesh_artifact` and OCC stats but does not publish canonical vertex/triangle counts into job state. There is no shared app/panel state that can swap the stats widget from viewport counts to solve-mesh counts after OCC mesh generation succeeds.
  Completed: March 11, 2026. The backend job payload now publishes `mesh_stats` as soon as canonical/occ-adaptive solve mesh arrays exist, the simulation job cache persists those counts through polling/local job state, and the stats widget now flips from `Viewport` counts to `Simulation` counts once a live job reports solve-mesh geometry. Regression coverage now locks the backend `mesh_stats` publication, job-cache normalization, and polling-to-widget handoff.

- [x] Clarify solve-mesh versus export-mesh controls in the UI and docs.
  Source: archived future additions doc.
  Relevant: Yes. Current controls mix viewport/export/solve semantics in ways that are not obvious.
  Will it improve the program: Yes. It should reduce incorrect expectations about what affects the backend OCC mesh.
  Research findings: the runtime docs already state that the viewport mesh is not the active simulation mesh, and `throatSliceDensity` already documents that it is viewport-only. The UI still groups mixed controls under `Mesh Density`, including viewport-only tessellation, OCC enclosure resolution fields, and simulation-only mesh download behavior, so the contract is still not obvious at the point of use.
  Completed: March 11, 2026. The simulation panel now labels the mixed section as `Mesh Controls`, adds an inline note separating preview tessellation from backend solve/export meshing, renames the relevant schema labels/tooltips to `Viewport ...` versus `Solve ...`, and relabels the settings checkbox to `Auto-download solve mesh artifact (.msh)`. `README.md` and `docs/PROJECT_DOCUMENTATION.md` now include a short control matrix so viewport tessellation, solve/export mesh density, and download behavior are documented in one place.

- [x] Move the formula affordance from the section header to the relevant input fields and audit which fields should support formulas.
  Source: user request; `src/ui/paramPanel.js`; `src/config/schema.js`.
  Relevant: Yes. Formula discovery is currently global and detached from the fields that actually benefit from it.
  Will it improve the program: Yes. It should make formula support easier to discover and reduce clutter in the section header.
  Research findings: `src/ui/paramPanel.js` currently renders one section-header `ƒ` button and treats every `range`, `number`, and `expression` field as a formula-capable text input. `src/config/schema.js` already marks the true expression-oriented fields explicitly, but that set currently includes geometry-defining fields and lower-value fields such as enclosure resolutions and `sourceContours`, so an allowlist pass is still needed.
  Best approach: Treat `PARAM_SCHEMA` fields with `type: 'expression'` as the starting set, then narrow to geometry-defining fields first: OSSE/R-OSSE core profile fields, morphing/profile-transform fields, throat-extension/slot/rotation fields, and guiding-curve parameters. Keep mesh-density, source-path, and admin-style fields on an allowlist basis only if real formula use cases exist. Add a small per-row `ƒ` affordance that opens the existing formula reference panel in context.
  Completed: March 11, 2026. The schema now carries an explicit formula-field allowlist for the audited geometry-defining inputs, `src/ui/paramPanel.js` renders per-row `ƒ` buttons beside only those fields and opens the existing formula reference panel with field context, and non-audited inputs such as enclosure resolution lists, source contours, and mesh-density controls now render with normal text/number inputs instead of blanket formula UI treatment.

- [x] Gate Simulation Advanced / expert controls by backend capability and finish the remaining hardening/docs pass around them.
  Source: earlier simulation-management planning notes; `src/ui/settings/modal.js`; runtime capability checks.
  Relevant: Yes, but lower priority than the bugs and Phase 3 work above.
  Will it improve the program: Yes. It prevents dead or misleading controls while keeping the settings layout future-proof.
  Research findings: `src/ui/settings/modal.js` already contains a placeholder Simulation Advanced section, and the app already has two separate `/health` consumers: Sim Basic device-mode polling and the connection banner. There is not yet one shared capability model or any advanced-control availability flow, so this work should follow the Simulation Basic wiring rather than race it.
  Best approach: Check capability at startup and when opening Settings, keep unavailable controls in normal flow but disabled/explained instead of hidden if possible, and pair rollout with regression/docs updates rather than adding placeholder controls alone.
  Completed: March 11, 2026. `/health` now advertises a small `capabilities` payload for simulation settings, the frontend caches the latest runtime health snapshot from startup polling and reuses it when Settings opens, Sim Basic device availability and the connection banner now derive from the same capability helper, and the Simulation Advanced pane renders explicit read-only Phase 2 controls with backend-driven explanations instead of a dead placeholder. Regression coverage now locks both the backend health payload and the frontend capability-summary behavior.

### P2 Folder-Backed Completion And Export Flow

- [x] Build selected-format bundle export and idempotent auto-export on simulation completion.
  Source: `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`; current export/task code.
  Relevant: Yes. The data model for task manifests/index exists, but export orchestration across formats is still fragmented.
  Will it improve the program: Yes. It turns completed simulations into reusable artifacts without manual repetition and matches the folder-workspace design.
  Research findings: current export use cases in `src/modules/export/useCases.js` are per-format only, and completed-job export in `src/ui/simulation/jobActions.js` only records a synthetic token into `exportedFiles` after a manual export. The manifest/index model is ready for bundle bookkeeping, but there is no bundle coordinator, selected-format settings model, or once-per-completion idempotency flow.
  Best approach: Add one bundle coordinator over existing exporters, drive it from settings-selected formats, record per-task exported files, and store an idempotency marker so auto-export only runs once per completion event while preserving partial-failure reporting.
  Completed: March 11, 2026. Simulation Basic settings now persist `autoExportOnComplete` plus stable string-based export format selections, completed-task `Export` runs the full configured bundle instead of a one-off picker, bundle writes route into the task folder when a workspace is active, task manifests/index entries record real exported file tokens plus `autoExportCompletedAt`, and polling auto-exports a completed job exactly once per completion transition while still surfacing partial-format failures.

- [x] Finish completed-task source modes so folder-backed tasks and backend jobs have clear, non-mixed browsing behavior.
  Source: earlier simulation-management planning notes; current folder workspace and job-feed code.
  Relevant: Yes. Folder workspace storage exists, but the user-facing source model is still incomplete.
  Will it improve the program: Yes. It makes task history understandable at scale and aligns export folders with browsing behavior.
  Research findings: `restoreSimulationControllerJobs()` currently merges local storage, workspace index items, and remote backend jobs into one combined feed, and `renderJobList()` shows no source label or badge. That mixed-source behavior is exactly the ambiguity the original phase was trying to eliminate, so this remains the upstream seam for task-history UX.
  Best approach: Introduce an explicit source abstraction, show folder tasks when folder context is active, fall back to backend jobs otherwise, and expose the current source with a header label plus compact badge rather than mixing both sources in one list.
  Completed: March 11, 2026. The simulation feed now has explicit source modes: selecting a folder workspace loads folder task manifests/index only and skips backend job listing, backend mode still restores/polls remote jobs plus the local cache when no folder is active, the Refresh action reloads the active source instead of assuming backend polling, and the UI now labels the active source in the header plus a compact per-row badge instead of mixing sources silently.

- [x] Add task ratings plus stable sorting and filtering controls.
  Source: earlier simulation-management planning notes; existing manifest/index `rating` fields.
  Relevant: Yes. The schema already carries `rating`, but there is no complete user flow that makes it useful.
  Will it improve the program: Yes. It helps users manage larger simulation histories instead of treating every result as flat output.
  Research findings: the rating field already persists through `task.manifest.json`, task index rebuild, local job tracking, and merge logic, but there is no rating editor in `renderJobList()`. Sorting is currently hardcoded to newest-first in `src/ui/simulation/jobTracker.js`, and there are no filter controls or persisted task-list preferences.
  Best approach: Implement star editing against manifest/index persistence, default to newest-first, persist last-used sort/filter preferences, and use fast preset filters rather than freeform controls.
  Completed: March 11, 2026. The simulation jobs header now includes persisted sort and minimum-rating controls, job rows expose inline 1-5 star rating buttons, rating changes sync through controller persistence into local cache plus folder task manifests/index, and task-list rendering uses stable preference-driven sorting (`Newest`, `Highest Rated`, `Label A-Z`) with rating filtering instead of hardcoded newest-first ordering.

### P3 Docs, Hardening, And Cleanup

- [x] Create a smaller durable architecture doc and split stable per-module contracts out of large narrative docs.
  Source: user rules for this backlog; pending doc-maintenance work around trimming `docs/PROJECT_DOCUMENTATION.md`.
  Relevant: Yes. The project now has one large runtime document but no focused `docs/architecture.md` or `docs/modules/` contract set.
  Will it improve the program: Yes. It reduces doc drift and makes future maintenance cheaper.
  Research findings: the working rules in this very backlog already refer to `docs/architecture.md` and `docs/modules/`, but those paths do not exist yet. The architecture cleanup work is complete enough that this is now straightforward documentation debt rather than a blocker for product work.
  Best approach: Extract durable architecture decisions into `docs/architecture.md`, move contract details into `docs/modules/`, and keep `docs/PROJECT_DOCUMENTATION.md` as a concise runtime map until the split is complete.
  Completed: March 11, 2026. The repo now has a dedicated `docs/architecture.md` durable architecture guide plus `docs/modules/` contract docs for geometry, simulation, export, and backend boundaries, and the README/runtime documentation now point readers to those extracted references instead of relying on `docs/PROJECT_DOCUMENTATION.md` alone.

- [x] Add a maintained-doc parity audit so runtime/device-mode changes cannot leave `docs/PROJECT_DOCUMENTATION.md` and `server/README.md` describing removed fallback behavior.
  Source: architecture audit on March 11, 2026; `docs/PROJECT_DOCUMENTATION.md`; `server/README.md`; `server/contracts/__init__.py`; `server/solver/device_interface.py`.
  Relevant: Yes. The maintained docs drifted into describing `numba` and legacy fallback paths after the runtime had already narrowed to OpenCL-only device modes and explicit hard failures.
  Will it improve the program: Yes. It protects the repo’s stated “docs plus tests plus code” source-of-truth model and reduces future audit noise.
  Research findings: the March 11 audit found stale claims about `numba` mode/fallback and legacy `bempp_api` support in maintained docs even though the runtime contract and validation layer no longer exposed those paths. The repo needs a repeatable way to catch that class of drift.
  Best approach: Keep the maintained architecture docs short, derive contract tables from code where practical, and add a small regression/doc-review checklist whenever runtime enums, dependency matrices, or fallback rules change.
  Completed: March 11, 2026. README’s backend dependency matrix now states the live `bempp-cl`-only solve contract, and `tests/docs-parity.test.js` keeps the maintained docs aligned on supported device modes plus the explicit no-legacy-fallback wording across `README.md`, `docs/PROJECT_DOCUMENTATION.md`, and `server/README.md`.

- [ ] Run a structured dead-code audit on `src/` and remove utility paths with no runtime entry.
  Source: archived future additions doc.
  Relevant: Yes, but only after the higher-value UX/product work above.
  Will it improve the program: Yes. It reduces maintenance noise and future confusion.
  Research findings: recent Phase 8 cleanup already removed several deprecated frontend/export aliases, so the obvious low-hanging cleanup has mostly been harvested. What remains should be handled as a deliberate import/callsite audit with small removals, not as opportunistic drive-by deletion during product work.
  Best approach: Start with an import/callsite inventory, remove only code with no active UI/runtime/test path, and keep each cleanup slice small enough for targeted verification.

- [x] Retire the legacy frontend `.msh` export surface once tests/tooling no longer need it.
  Source: architecture audit on March 11, 2026; `src/export/index.js`; `src/export/msh.js`; `tests/geometry-artifacts.test.js`.
  Relevant: Yes, but below active runtime correctness work.
  Will it improve the program: Yes. It removes a public export path that bypasses the backend-only OCC mesh flow described by the active architecture.
  Research findings: the active runtime uses `/api/mesh/build` for authored `.msh` output, but `src/export/index.js` still publicly re-exports `exportMSH()` and `src/export/msh.js` still contains dead helper code that is only exercised by tests. That leaves legacy API surface area in place after the runtime moved on.
  Best approach: First move any remaining regression coverage to the active backend/export contract, then delete or explicitly quarantine the legacy helper so app-facing exports cannot accidentally depend on it again.
  Completed: March 11, 2026. The app-facing `src/export/index.js` surface no longer re-exports `exportMSH()`, the old frontend helper has been removed from `src/export/`, and the one remaining regression that still checks historical physical-name behavior now uses a quarantined test-only helper under `tests/helpers/legacyMsh.js`.

### P4 Research And Optional Engineering Tracks

- [ ] Add a symmetry benchmark harness and expose symmetry-policy decisions more clearly.
  Source: archived future additions doc.
  Relevant: Probably, but not urgent.
  Will it improve the program: Potentially. It would make symmetry reduction more explainable and measurable.
  Research findings: the backend already has automatic symmetry detection/reduction plus a repeatable benchmark script in `server/scripts/benchmark_solver.py`, but there is no fixture-backed harness that compares full versus half versus quarter-domain runs or captures UI-facing policy decisions. This is real research work, not a thin UI tweak.
  Best approach: Build repeatable full/half/quarter benchmark cases first, then decide whether UI controls such as `auto` versus `force_full` are justified by the data.

- [ ] Decide whether the Gmsh export stack should remain a long-term dependency.
  Source: archived future additions doc.
  Relevant: Maybe. It depends on whether remaining MSH/STL export needs can be met without OCC/Gmsh.
  Will it improve the program: Potentially, if dependency burden and setup friction drop without sacrificing export quality.
  Research findings: the current active runtime still uses `waveguide_builder.py` for `/api/mesh/build` and for adaptive solve meshing, and completed-job mesh download still depends on persisted `.msh` artifacts. That makes Gmsh removal a downstream architectural decision after current export/simulation needs are either reduced or replaced with parity.
  Best approach: Audit every remaining Gmsh touchpoint, compare against JS/export alternatives, and only plan removal if parity for the remaining export use cases is realistic.

- [ ] Consider optional internal decomposition of `solve_optimized()` and `waveguide_builder.py` if those areas need further feature work.
  Source: `docs/archive/PRODUCTION_READINESS_REPORT_2026-02-25.md` Gate C deferred notes.
  Relevant: Low right now.
  Will it improve the program: Mostly maintenance-oriented, not immediately user-visible.
  Research findings: `server/solver/solve_optimized.py` is currently 693 lines and `server/solver/waveguide_builder.py` is 2723 lines, so the maintenance concern is real. The existing backlog does not require those refactors yet, and recent work has not shown them to be the current delivery bottleneck.
  Best approach: Treat it as opportunistic refactor work only when a feature or bug fix needs deeper changes in those files; do not schedule it as standalone cleanup unless those modules become a bottleneck.

## Imported Historical Planning Work

The unfinished planning work folded into this backlog came from:
- `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`
- `docs/archive/FUTURE_ADDITIONS_2026-03-11.md`
- selected decisions preserved from earlier local planning notes before those files were removed from the repository
