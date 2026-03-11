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

## Active Backlog

### P0 Current Bugs And UX Correctness

- [ ] Make the Stop action cancel backend work cooperatively instead of only updating UI state.
  Source: user report; `server/api/routes_simulation.py`; `server/services/simulation_runner.py`; `src/ui/simulation/controller.js`.
  Relevant: Yes. The current route marks running jobs as cancelled, but the worker thread continues through meshing/solve and only notices cancellation after the solve returns.
  Will it improve the program: Yes. It prevents wasted compute, misleading UI, and inconsistent job state.
  Best approach: Introduce a cooperative cancellation token/check that is visible to OCC meshing and BEM solve loops, check `cancellation_requested` before expensive stages and between frequency steps, and only finalize `cancelled` after the worker acknowledges the stop. Add backend tests for queued and running cancellation plus a frontend regression proving the UI waits for real cancellation semantics.

- [ ] Show simulation mesh vertex/triangle counts in the stats widget once the `.msh`/simulation mesh is built.
  Source: user request; `src/app/scene.js`; `src/modules/simulation/useCases.js`; `server/services/simulation_runner.py`.
  Relevant: Yes. The current stats widget always shows viewport tessellation counts from `renderModel()`, which diverges from the actual simulation mesh.
  Will it improve the program: Yes. It gives users the complexity they actually submitted to BEM instead of a potentially misleading viewport proxy.
  Best approach: Keep viewport counts until a simulation mesh exists, then publish simulation mesh stats from the point where OCC mesh/canonical mesh construction succeeds. Persist the latest simulation mesh counts in panel/app state and update the same stats widget when `mesh_artifact` or canonical mesh preparation completes.

- [ ] Move the formula affordance from the section header to the relevant input fields and audit which fields should support formulas.
  Source: user request; `src/ui/paramPanel.js`; `src/config/schema.js`.
  Relevant: Yes. Formula discovery is currently global and detached from the fields that actually benefit from it.
  Will it improve the program: Yes. It should make formula support easier to discover and reduce clutter in the section header.
  Best approach: Treat `PARAM_SCHEMA` fields with `type: 'expression'` as the starting set, then narrow to geometry-defining fields first: OSSE/R-OSSE core profile fields, morphing/profile-transform fields, throat-extension/slot/rotation fields, and guiding-curve parameters. Keep mesh-density, source-path, and admin-style fields on an allowlist basis only if real formula use cases exist. Add a small per-row `ƒ` affordance that opens the existing formula reference panel in context.

### P1 Imported Product Work

- [ ] Wire Simulation Basic settings all the way into `/api/solve` payloads and runtime availability messaging.
  Source: `.planning/ROADMAP.md` Phase 3; `.planning/phases/03-simulation-basic-payload-wiring/03-CONTEXT.md`; `src/ui/settings/simBasicSettings.js`; `src/ui/simulation/jobActions.js`.
  Relevant: Yes. The settings UI exists, but `runSimulation()` still hardcodes `frequencySpacing: 'log'` and `deviceMode: 'auto'` instead of consuming the saved Simulation Basic settings consistently.
  Will it improve the program: Yes. It makes settings trustworthy and closes the gap between visible controls and actual solve behavior.
  Best approach: Thread saved sim-basic settings through the submission builder, omit invalid/unset values so backend defaults remain authoritative, consume `/health` mode availability for inline device messaging, and add request-contract tests covering `device_mode`, `mesh_validation_mode`, `frequency_spacing`, `use_optimized`, `enable_symmetry`, and `verbose`.

- [ ] Build selected-format bundle export and idempotent auto-export on simulation completion.
  Source: `.planning/ROADMAP.md` Phase 5; `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`.
  Relevant: Yes. The data model for task manifests/index exists, but export orchestration across formats is still fragmented.
  Will it improve the program: Yes. It turns completed simulations into reusable artifacts without manual repetition and matches the folder-workspace design.
  Best approach: Add one bundle coordinator over existing exporters, drive it from settings-selected formats, record per-task exported files, and store an idempotency marker so auto-export only runs once per completion event while preserving partial-failure reporting.

- [ ] Finish completed-task source modes so folder-backed tasks and backend jobs have clear, non-mixed browsing behavior.
  Source: `.planning/ROADMAP.md` Phase 6; `.planning/phases/06-completed-task-source-modes/06-CONTEXT.md`.
  Relevant: Yes. Folder workspace storage exists, but the user-facing source model is still incomplete.
  Will it improve the program: Yes. It makes task history understandable at scale and aligns export folders with browsing behavior.
  Best approach: Introduce an explicit source abstraction, show folder tasks when folder context is active, fall back to backend jobs otherwise, and expose the current source with a header label plus compact badge rather than mixing both sources in one list.

- [ ] Add task ratings plus stable sorting and filtering controls.
  Source: `.planning/ROADMAP.md` Phase 7; `.planning/phases/07-ratings-sorting-filtering/07-CONTEXT.md`; existing manifest/index `rating` fields.
  Relevant: Yes. The schema already carries `rating`, but there is no complete user flow that makes it useful.
  Will it improve the program: Yes. It helps users manage larger simulation histories instead of treating every result as flat output.
  Best approach: Implement star editing against manifest/index persistence, default to newest-first, persist last-used sort/filter preferences, and use fast preset filters rather than freeform controls.

- [ ] Gate Simulation Advanced / expert controls by backend capability and finish the remaining hardening/docs pass around them.
  Source: `.planning/ROADMAP.md` Phase 8; `.planning/phases/08-advanced-controls-gating-and-hardening/08-CONTEXT.md`.
  Relevant: Yes, but lower priority than the bugs and Phase 3/5/6/7 work.
  Will it improve the program: Yes. It prevents dead or misleading controls while keeping the settings layout future-proof.
  Best approach: Check capability at startup and when opening Settings, keep unavailable controls in normal flow but disabled/explained instead of hidden if possible, and pair rollout with regression/docs updates rather than adding placeholder controls alone.

### P2 Hardening, Docs, And Diagnostics

- [ ] Create a smaller durable architecture doc and split stable per-module contracts out of large narrative docs.
  Source: user rules for this backlog; `.planning/STATE.md` pending todo about trimming `docs/PROJECT_DOCUMENTATION.md`.
  Relevant: Yes. The project now has one large runtime document but no focused `docs/architecture.md` or `docs/modules/` contract set.
  Will it improve the program: Yes. It reduces doc drift and makes future maintenance cheaper.
  Best approach: Extract durable architecture decisions into `docs/architecture.md`, move contract details into `docs/modules/`, and keep `docs/PROJECT_DOCUMENTATION.md` as a concise runtime map until the split is complete.

- [ ] Add pre-submit canonical tag diagnostics to the simulation UI.
  Source: archived future additions doc.
  Relevant: Yes. Contract validation exists, but users still do not get a concise pre-submit view of tag counts or missing-source problems.
  Will it improve the program: Yes. It shortens debug loops before a solve request is sent.
  Best approach: Surface counts for tags `1/2/3/4`, warn on missing source coverage or triangle/tag mismatches, and keep the checks lightweight and strictly read-only.

- [ ] Clarify solve-mesh versus export-mesh controls in the UI and docs.
  Source: archived future additions doc.
  Relevant: Yes. Current controls mix viewport/export/solve semantics in ways that are not obvious.
  Will it improve the program: Yes. It should reduce incorrect expectations about what affects the backend OCC mesh.
  Best approach: Update labels/tooltips first, then add a short mesh-controls matrix to `README.md` and `docs/PROJECT_DOCUMENTATION.md` once the UI wording is final.

- [ ] Add an explicit no-Gmsh regression lane for `/api/solve`.
  Source: archived future additions doc.
  Relevant: Yes. Current tests cover tag contracts, but not a dedicated “Gmsh unavailable but canonical solve still valid” lane.
  Will it improve the program: Yes. It protects a useful runtime mode from future regressions.
  Best approach: Add a server test configuration that forces Gmsh-unavailable readiness and asserts the expected `/api/solve` behavior in canonical-payload mode.

- [ ] Run a structured dead-code audit on `src/` and remove utility paths with no runtime entry.
  Source: archived future additions doc.
  Relevant: Yes, but only after the higher-value UX/product work above.
  Will it improve the program: Yes. It reduces maintenance noise and future confusion.
  Best approach: Start with an import/callsite inventory, remove only code with no active UI/runtime/test path, and keep each cleanup slice small enough for targeted verification.

### P3 Research And Optional Engineering Tracks

- [ ] Add a symmetry benchmark harness and expose symmetry-policy decisions more clearly.
  Source: archived future additions doc.
  Relevant: Probably, but not urgent.
  Will it improve the program: Potentially. It would make symmetry reduction more explainable and measurable.
  Best approach: Build repeatable full/half/quarter benchmark cases first, then decide whether UI controls such as `auto` versus `force_full` are justified by the data.

- [ ] Decide whether the Gmsh export stack should remain a long-term dependency.
  Source: archived future additions doc.
  Relevant: Maybe. It depends on whether remaining MSH/STL export needs can be met without OCC/Gmsh.
  Will it improve the program: Potentially, if dependency burden and setup friction drop without sacrificing export quality.
  Best approach: Audit every remaining Gmsh touchpoint, compare against JS/export alternatives, and only plan removal if parity for the remaining export use cases is realistic.

- [ ] Consider optional internal decomposition of `solve_optimized()` and `waveguide_builder.py` if those areas need further feature work.
  Source: `docs/archive/PRODUCTION_READINESS_REPORT_2026-02-25.md` Gate C deferred notes.
  Relevant: Low right now.
  Will it improve the program: Mostly maintenance-oriented, not immediately user-visible.
  Best approach: Treat it as opportunistic refactor work only when a feature or bug fix needs deeper changes in those files; do not schedule it as standalone cleanup unless those modules become a bottleneck.

## Imported GSD Work

The unfinished GSD planning work folded into this backlog came from:
- `.planning/ROADMAP.md` unfinished phases 3, 5, 6, 7, and 8
- `.planning/STATE.md` pending todos that were still relevant
- `.planning/phases/*/CONTEXT.md` decisions used to preserve product intent without keeping GSD as the active workflow
