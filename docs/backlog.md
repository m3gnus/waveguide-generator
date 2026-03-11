# Backlog

Last updated: March 11, 2026

This file is the active source of truth for unfinished product and engineering work.
Superseded planning inputs were folded in from:
- `docs/archive/FUTURE_ADDITIONS_2026-03-11.md`
- `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`
- `.planning/ROADMAP.md`
- `.planning/STATE.md`
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
- Remaining work is now a mix of user-reported bugs, unfinished simulation-management product work imported from old GSD plans, and a smaller set of hardening/research follow-ups.

Remaining work:
- Fix real cancellation behavior for running backend solves.
- Improve simulation UX around mesh visibility, formula entry, and settings-to-runtime parity.
- Finish the unfinished simulation-management roadmap slices that were left in `.planning/`.
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

- [ ] Make the Stop action cancel backend work cooperatively instead of only updating UI state.
  Source: user report; `server/api/routes_simulation.py`; `server/services/simulation_runner.py`; `src/ui/simulation/controller.js`.
  Relevant: Yes. The current route marks running jobs as cancelled, but the worker thread continues through meshing/solve and only notices cancellation after the solve returns.
  Will it improve the program: Yes. It prevents wasted compute, misleading UI, and inconsistent job state.
  Research findings: `stop_simulation()` marks running jobs `cancelled` immediately, `run_simulation()` only checks cancellation after `solver.solve(...)` returns, and `stopSimulationControllerJob()` / `stopSimulation()` also flip the frontend feed to `cancelled` immediately. This is the highest-leverage runtime seam because several downstream UX states currently trust a cancellation that did not happen yet.
  Best approach: Introduce a cooperative cancellation token/check that is visible to OCC meshing and BEM solve loops, check `cancellation_requested` before expensive stages and between frequency steps, and only finalize `cancelled` after the worker acknowledges the stop. Add backend tests for queued and running cancellation plus a frontend regression proving the UI waits for real cancellation semantics.

- [ ] Wire Simulation Basic settings all the way into `/api/solve` payloads and runtime availability messaging.
  Source: `.planning/ROADMAP.md` Phase 3; `.planning/phases/03-simulation-basic-payload-wiring/03-CONTEXT.md`; `src/ui/settings/simBasicSettings.js`; `src/ui/simulation/jobActions.js`.
  Relevant: Yes. The settings UI exists, but `runSimulation()` still hardcodes `frequencySpacing: 'log'` and `deviceMode: 'auto'` instead of consuming the saved Simulation Basic settings consistently.
  Will it improve the program: Yes. It makes settings trustworthy and closes the gap between visible controls and actual solve behavior.
  Research findings: `src/ui/settings/simBasicSettings.js` already persists `deviceMode`, `meshValidationMode`, `frequencySpacing`, `useOptimized`, `enableSymmetry`, and `verbose`, and the modal already polls `/health` for device availability. The runtime submit path still only forwards hardcoded `frequencySpacing` / `deviceMode` from `src/ui/simulation/jobActions.js`, while `src/solver/index.js` only serializes `mesh_validation_mode`, `frequency_spacing`, and `device_mode`.
  Best approach: Thread saved sim-basic settings through the submission builder, omit invalid/unset values so backend defaults remain authoritative, consume `/health` mode availability for inline device messaging, and add request-contract tests covering `device_mode`, `mesh_validation_mode`, `frequency_spacing`, `use_optimized`, `enable_symmetry`, and `verbose`.

- [ ] Add an explicit no-Gmsh regression lane for `/api/solve`.
  Source: archived future additions doc.
  Relevant: Yes. Current tests cover runtime gating and tag contracts, but not a dedicated “Gmsh unavailable while canonical `/api/solve` remains valid” lane.
  Will it improve the program: Yes. It protects a useful runtime mode from future regressions and makes the solve contract less fragile while OCC-specific work continues.
  Research findings: `server/tests/test_dependency_runtime.py` and `server/tests/test_api_validation.py` already assert OCC runtime failure paths, and `server/tests/test_mesh_validation.py` covers `use_gmsh=True` behavior. There is still no test that forces `occBuilderReady=False` while keeping solver readiness intact and proves canonical payload solves are still accepted.
  Best approach: Add a server test configuration that forces Gmsh-unavailable readiness and asserts the expected `/api/solve` behavior in canonical-payload mode.

- [ ] Add pre-submit canonical tag diagnostics to the simulation UI.
  Source: archived future additions doc.
  Relevant: Yes. Contract validation exists, but users still do not get a concise pre-submit view of tag counts or missing-source problems.
  Will it improve the program: Yes. It shortens debug loops before a solve request is sent.
  Research findings: frontend preflight already rejects malformed canonical payloads in `src/solver/index.js`, backend validation rejects tag/triangle mismatches and missing tag `2`, and `prepareCanonicalSimulationMesh()` already has the data needed to count triangles per tag. The gap is purely the absence of a small read-only UI summary before submit.
  Best approach: Surface counts for tags `1/2/3/4`, warn on missing source coverage or triangle/tag mismatches, and keep the checks lightweight and strictly read-only.

### P1 Simulation UX That Depends On Runtime Truth

- [ ] Show simulation mesh vertex/triangle counts in the stats widget once the `.msh`/simulation mesh is built.
  Source: user request; `src/app/scene.js`; `src/modules/simulation/useCases.js`; `server/services/simulation_runner.py`.
  Relevant: Yes. The current stats widget always shows viewport tessellation counts from `renderModel()`, which diverges from the actual simulation mesh.
  Will it improve the program: Yes. It gives users the complexity they actually submitted to BEM instead of a potentially misleading viewport proxy.
  Research findings: `src/app/scene.js` always writes viewport counts into `app.stats`, while `server/services/simulation_runner.py` stores `mesh_artifact` and OCC stats but does not publish canonical vertex/triangle counts into job state. There is no shared app/panel state that can swap the stats widget from viewport counts to solve-mesh counts after OCC mesh generation succeeds.
  Best approach: Keep viewport counts until a simulation mesh exists, then publish simulation mesh stats from the point where OCC mesh/canonical mesh construction succeeds. Persist the latest simulation mesh counts in panel/app state and update the same stats widget when `mesh_artifact` or canonical mesh preparation completes.

- [ ] Clarify solve-mesh versus export-mesh controls in the UI and docs.
  Source: archived future additions doc.
  Relevant: Yes. Current controls mix viewport/export/solve semantics in ways that are not obvious.
  Will it improve the program: Yes. It should reduce incorrect expectations about what affects the backend OCC mesh.
  Research findings: the runtime docs already state that the viewport mesh is not the active simulation mesh, and `throatSliceDensity` already documents that it is viewport-only. The UI still groups mixed controls under `Mesh Density`, including viewport-only tessellation, OCC enclosure resolution fields, and simulation-only mesh download behavior, so the contract is still not obvious at the point of use.
  Best approach: Update labels/tooltips first, then add a short mesh-controls matrix to `README.md` and `docs/PROJECT_DOCUMENTATION.md` once the UI wording is final.

- [ ] Move the formula affordance from the section header to the relevant input fields and audit which fields should support formulas.
  Source: user request; `src/ui/paramPanel.js`; `src/config/schema.js`.
  Relevant: Yes. Formula discovery is currently global and detached from the fields that actually benefit from it.
  Will it improve the program: Yes. It should make formula support easier to discover and reduce clutter in the section header.
  Research findings: `src/ui/paramPanel.js` currently renders one section-header `ƒ` button and treats every `range`, `number`, and `expression` field as a formula-capable text input. `src/config/schema.js` already marks the true expression-oriented fields explicitly, but that set currently includes geometry-defining fields and lower-value fields such as enclosure resolutions and `sourceContours`, so an allowlist pass is still needed.
  Best approach: Treat `PARAM_SCHEMA` fields with `type: 'expression'` as the starting set, then narrow to geometry-defining fields first: OSSE/R-OSSE core profile fields, morphing/profile-transform fields, throat-extension/slot/rotation fields, and guiding-curve parameters. Keep mesh-density, source-path, and admin-style fields on an allowlist basis only if real formula use cases exist. Add a small per-row `ƒ` affordance that opens the existing formula reference panel in context.

- [ ] Gate Simulation Advanced / expert controls by backend capability and finish the remaining hardening/docs pass around them.
  Source: `.planning/ROADMAP.md` Phase 8; `.planning/phases/08-advanced-controls-gating-and-hardening/08-CONTEXT.md`.
  Relevant: Yes, but lower priority than the bugs and Phase 3 work above.
  Will it improve the program: Yes. It prevents dead or misleading controls while keeping the settings layout future-proof.
  Research findings: `src/ui/settings/modal.js` already contains a placeholder Simulation Advanced section, and the app already has two separate `/health` consumers: Sim Basic device-mode polling and the connection banner. There is not yet one shared capability model or any advanced-control availability flow, so this work should follow the Simulation Basic wiring rather than race it.
  Best approach: Check capability at startup and when opening Settings, keep unavailable controls in normal flow but disabled/explained instead of hidden if possible, and pair rollout with regression/docs updates rather than adding placeholder controls alone.

### P2 Folder-Backed Completion And Export Flow

- [ ] Build selected-format bundle export and idempotent auto-export on simulation completion.
  Source: `.planning/ROADMAP.md` Phase 5; `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`.
  Relevant: Yes. The data model for task manifests/index exists, but export orchestration across formats is still fragmented.
  Will it improve the program: Yes. It turns completed simulations into reusable artifacts without manual repetition and matches the folder-workspace design.
  Research findings: current export use cases in `src/modules/export/useCases.js` are per-format only, and completed-job export in `src/ui/simulation/jobActions.js` only records a synthetic token into `exportedFiles` after a manual export. The manifest/index model is ready for bundle bookkeeping, but there is no bundle coordinator, selected-format settings model, or once-per-completion idempotency flow.
  Best approach: Add one bundle coordinator over existing exporters, drive it from settings-selected formats, record per-task exported files, and store an idempotency marker so auto-export only runs once per completion event while preserving partial-failure reporting.

- [ ] Finish completed-task source modes so folder-backed tasks and backend jobs have clear, non-mixed browsing behavior.
  Source: `.planning/ROADMAP.md` Phase 6; `.planning/phases/06-completed-task-source-modes/06-CONTEXT.md`.
  Relevant: Yes. Folder workspace storage exists, but the user-facing source model is still incomplete.
  Will it improve the program: Yes. It makes task history understandable at scale and aligns export folders with browsing behavior.
  Research findings: `restoreSimulationControllerJobs()` currently merges local storage, workspace index items, and remote backend jobs into one combined feed, and `renderJobList()` shows no source label or badge. That mixed-source behavior is exactly the ambiguity the original phase was trying to eliminate, so this remains the upstream seam for task-history UX.
  Best approach: Introduce an explicit source abstraction, show folder tasks when folder context is active, fall back to backend jobs otherwise, and expose the current source with a header label plus compact badge rather than mixing both sources in one list.

- [ ] Add task ratings plus stable sorting and filtering controls.
  Source: `.planning/ROADMAP.md` Phase 7; `.planning/phases/07-ratings-sorting-filtering/07-CONTEXT.md`; existing manifest/index `rating` fields.
  Relevant: Yes. The schema already carries `rating`, but there is no complete user flow that makes it useful.
  Will it improve the program: Yes. It helps users manage larger simulation histories instead of treating every result as flat output.
  Research findings: the rating field already persists through `task.manifest.json`, task index rebuild, local job tracking, and merge logic, but there is no rating editor in `renderJobList()`. Sorting is currently hardcoded to newest-first in `src/ui/simulation/jobTracker.js`, and there are no filter controls or persisted task-list preferences.
  Best approach: Implement star editing against manifest/index persistence, default to newest-first, persist last-used sort/filter preferences, and use fast preset filters rather than freeform controls.

### P3 Docs, Hardening, And Cleanup

- [ ] Create a smaller durable architecture doc and split stable per-module contracts out of large narrative docs.
  Source: user rules for this backlog; `.planning/STATE.md` pending todo about trimming `docs/PROJECT_DOCUMENTATION.md`.
  Relevant: Yes. The project now has one large runtime document but no focused `docs/architecture.md` or `docs/modules/` contract set.
  Will it improve the program: Yes. It reduces doc drift and makes future maintenance cheaper.
  Research findings: the working rules in this very backlog already refer to `docs/architecture.md` and `docs/modules/`, but those paths do not exist yet. The architecture cleanup work is complete enough that this is now straightforward documentation debt rather than a blocker for product work.
  Best approach: Extract durable architecture decisions into `docs/architecture.md`, move contract details into `docs/modules/`, and keep `docs/PROJECT_DOCUMENTATION.md` as a concise runtime map until the split is complete.

- [ ] Run a structured dead-code audit on `src/` and remove utility paths with no runtime entry.
  Source: archived future additions doc.
  Relevant: Yes, but only after the higher-value UX/product work above.
  Will it improve the program: Yes. It reduces maintenance noise and future confusion.
  Research findings: recent Phase 8 cleanup already removed several deprecated frontend/export aliases, so the obvious low-hanging cleanup has mostly been harvested. What remains should be handled as a deliberate import/callsite audit with small removals, not as opportunistic drive-by deletion during product work.
  Best approach: Start with an import/callsite inventory, remove only code with no active UI/runtime/test path, and keep each cleanup slice small enough for targeted verification.

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

## Imported GSD Work

The unfinished GSD planning work folded into this backlog came from:
- `.planning/ROADMAP.md` unfinished phases 3, 5, 6, 7, and 8
- `.planning/STATE.md` pending todos that were still relevant
- `.planning/phases/*/CONTEXT.md` decisions used to preserve product intent without keeping GSD as the active workflow
