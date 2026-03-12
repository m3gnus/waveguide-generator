# Backlog

Last updated: March 12, 2026

This file is the active source of truth for unfinished product and engineering work.
Detailed completion history from the March 11-12, 2026 cleanup phase now lives in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

Superseded planning inputs were folded in from:
- `docs/archive/FUTURE_ADDITIONS_2026-03-11.md`
- `docs/archive/SIMULATION_MANAGEMENT_PLAN_2026-03-11.md`
- selected decisions preserved from earlier local planning notes before those files were removed from the repository

## Working Rules

### Upstream-downstream integrity

Modules must not compensate for defects that belong upstream. Each module should receive correct input and fail visibly if it does not. When downstream code contains a workaround for an upstream defect, the fix belongs in the upstream module, not in the workaround.

### Docs and audit discipline

Keep durable decisions in `docs/architecture.md`, active work in this file, and per-module contracts in `docs/modules/`. Put generated audits, comparisons, and experiment output under `research/`.

## Current Baseline

status as of date:
- March 12, 2026
- The architecture cleanup plan is complete.
- The settings modal exists, viewer settings persist, and the folder workspace manifest/index model exists.
- Active runtime docs are `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `tests/TESTING.md`, `server/README.md`, and `AGENTS.md`.
- The backlog is re-opened for a UI/settings parity pass plus the earlier symmetry-control/runtime follow-through.

Researched UI/runtime findings:
- `src/ui/paramPanel.js` already renders most geometry/source/mesh controls from `src/config/schema.js` and applies native browser tooltips through `label.title`, but settings controls do not yet have the same per-control hover help pattern.
- Not every schema parameter is visible. `PARAM_SCHEMA.MESH` includes `throatSliceDensity` and `verticalOffset`, but the current parameter UI does not render either control.
- Simulation frequency and polar controls are visible in `index.html`, but they live outside the settings modal and do not share the same copy/help system as schema-driven parameter controls.
- The settings modal fully exposes viewer settings and the active Simulation Basic runtime overrides (`deviceMode`, `meshValidationMode`, `frequencySpacing`, `useOptimized`, `enableSymmetry`, `verbose`).
- Persisted simulation-management settings are only partially exposed in the settings modal. `autoExportOnComplete` and `selectedFormats` are visible there, while `defaultSort` and `minRatingFilter` still live only in the simulation-jobs toolbar.
- The Simulation Advanced section is placeholder-only. Backend capability metadata reports `simulationAdvanced.available = false`, and the public `/api/solve` request contract does not expose advanced GMRES/warm-up/tolerance/restart overrides yet.
- Folder workspace support exists in code, but visibility is conditional. The `Choose Folder` row in `index.html` is hidden by `src/app/events.js` when `window.showDirectoryPicker` is unavailable, so some user environments will show no folder button at all.
- Manual exports route through `src/ui/fileOps.js` and write directly to the selected folder when possible. Completed simulation task bundles route through `src/ui/simulation/workspaceTasks.js` and write into `<workspace>/<jobId>/`.
- The simulation diagnostics panel currently shows numeric canonical surface-tag counts (`1/2/3/4`) from `surfaceTags`. It does not show geometry-identity counts such as `throat_disc`, `horn_wall`, `enc_side`, or `rear_cap`.
- The current diagnostics count triangles per tag, not vertices, even though users may describe the numbers as “vertices.”
- The simulation job list renders a `Backend` badge on every row when the feed source is backend-only, which is redundant with the header/source labeling.

Remaining work:
- Keep diagnostics, regression coverage, and documentation current as new changes land.
- Convert new requirements into the smallest coherent backlog slices instead of rebuilding a long completed log here.

## Recommended Execution Order

When new work lands, continue to work the backlog from upstream runtime truth to downstream UX:

1. Lock in runtime truth where UI changes depend on it (`enableSymmetry` behavior, canonical diagnostics semantics, folder export routing).
2. Build the full parameter/settings inventory before changing labels, grouping, or visibility.
3. Rework settings and parameter information architecture, naming, and hover-help in one coordinated UI pass.
4. Clean up diagnostics/task-feed presentation once the underlying data and labels are settled.
5. Treat advanced solver controls as a separate contract-expansion track after product clarifies the requested GMRES precision behavior.

## Active Backlog

### P1. Parameter Inventory, Naming, Hover Help, and Ordering

- Build a source-of-truth inventory of every user-facing parameter, recording its owner file, current UI location, label/help text, and whether it is visible, hidden, duplicated, or placeholder-only.
- Reorganize geometry/simulation parameters into clearer groups with a predictable order instead of the current split between schema-driven sections and hard-coded simulation controls.
- Rewrite parameter titles into more understandable user-facing names while preserving current internal keys and config compatibility.
- Replace the current native-tooltip-only approach with a deliberate hover-help pattern that works consistently for parameters and settings.
- Expose currently hidden schema parameters `throatSliceDensity` and `verticalOffset`, or explicitly document them as intentionally internal if product decides they should remain hidden.
- Include simulation frequency and polar controls in the same naming/help/order pass so the whole UI reads as one system.

Implementation notes:
- `src/config/schema.js`
- `src/ui/paramPanel.js`
- `index.html`
- `src/ui/simulation/polarSettings.js`

### P1. Settings Panel Completeness and Information Architecture

- Audit all active settings sources and make the settings modal the primary discoverable home for persistent user preferences.
- Add understandable titles and hover clarifications for each visible setting.
- Move or mirror task-list preferences `defaultSort` and `minRatingFilter` into the settings modal, or intentionally keep them in the jobs toolbar with matching copy and documentation.
- Reorder modal sections so viewer behavior, simulation defaults, task export behavior, folder/workspace behavior, and system actions are grouped predictably.
- Keep the modal aligned with backend capability metadata so unsupported advanced controls are clearly separated from active controls.

Implementation notes:
- `src/ui/settings/viewerSettings.js`
- `src/ui/settings/simBasicSettings.js`
- `src/ui/settings/simulationManagementSettings.js`
- `src/ui/settings/modal.js`
- `server/services/solver_runtime.py`

### P2. Folder Workspace Discoverability and Export Routing

- Make folder selection discoverable near Settings and/or inside the settings modal without relying on users finding the current output-row placement.
- Treat “folder button not visible” as a real product gap: when folder picker support is unavailable, provide a visible fallback/explanation instead of silently hiding the control.
- Document and verify expected routing behavior when a folder workspace is active:
  - manual exports write into the selected folder root
  - completed simulation bundles write into `<workspace>/<jobId>/`
  - folder write failures currently clear the selected folder and fall back to picker/download behavior
- Decide whether “each generation goes into that folder” should cover only manual exports plus simulation bundles, or every generated artifact including config/script/history outputs.

Implementation notes:
- `index.html`
- `src/app/events.js`
- `src/ui/fileOps.js`
- `src/ui/workspace/folderWorkspace.js`
- `src/ui/simulation/workspaceTasks.js`
- `src/ui/simulation/exports.js`

### P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics

- Replace or augment the current numeric tag table with geometry-identity diagnostics such as `throat_disc`, `horn_wall`/`inner_wall`, `outer_wall`, `rear_cap`, `enc_front`, `enc_side`, and `enc_rear`.
- Decide whether the UI should report triangle counts, true vertex counts, or both. The current implementation counts triangles per numeric tag.
- Preserve numeric canonical tags as a secondary debug view if solver-contract troubleshooting still needs them.
- Ensure enclosure and freestanding-wall cases produce understandable labels and stable zero/non-zero diagnostics.

Implementation notes:
- `src/ui/simulation/jobActions.js`
- `src/modules/simulation/domain.js`
- `src/geometry/pipeline.js`
- `src/geometry/tags.js`
- `src/geometry/engine/buildWaveguideMesh.js`
- `src/geometry/engine/mesh/freestandingWall.js`
- `src/geometry/engine/mesh/enclosure.js`

### P2. Advanced Solver Controls and GMRES Precision Scope

- Define what the requested “single precision for GMRES” setting actually means before implementation starts.
- If the requirement means real advanced solver overrides, extend the backend/frontend contract so advanced settings can be exposed intentionally instead of remaining placeholder rows.
- After the contract exists, add understandable settings labels/help for advanced solver controls such as warm-up, method, tolerance, restart, max iterations, strong-form usage, Burton-Miller coupling, and symmetry tolerance.
- Add parity tests for any new backend-exposed solver settings before changing runtime behavior.

Research notes:
- Current public request contract exposes only `use_optimized`, `enable_symmetry`, `verbose`, `mesh_validation_mode`, `frequency_spacing`, and `device_mode`.
- `server/services/solver_runtime.py` advertises advanced controls only as `plannedControls`.
- The optimized solver currently hardcodes GMRES tolerance and uses the existing Bempp runtime path; there is no exposed float32/complex64 or result-precision toggle today.

Open product question:
- Clarify whether “single precision for GMRES” means a true FP32 solve path, a looser convergence/tolerance preset, or reduced precision only in stored/exported result data.

Implementation notes:
- `server/contracts/__init__.py`
- `server/services/solver_runtime.py`
- `server/solver/bem_solver.py`
- `server/solver/solve_optimized.py`
- `src/ui/settings/modal.js`

Required parity tests if this item becomes active:
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_api_validation.py`

### P3. Simulation Job Feed Source-Badge Cleanup

- Remove or reduce the redundant per-row `Backend` badge when the entire feed is already backend-only.
- Keep source labeling only where it adds information, such as header-level labeling or explicit `Folder` markers when folder-backed history is active.
- Review finished-task UI copy so the feed reads cleanly without repeating the same source label on every completed row.

Implementation notes:
- `src/ui/simulation/jobActions.js`

### P3. Symmetry Runtime Truth and Operator Control Follow-Through

- Add a reproducible diagnostics lane for the ATH reference configs already called out in the earlier symmetry investigation, capturing imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
- Add regression coverage for those reference cases so future geometry or solver changes cannot silently change reduction eligibility.
- Audit the existing `Enable Symmetry` control in the Settings modal and verify that it is visible in the live modal, persists correctly, and changes submitted `/api/solve` payloads as expected.
- Surface the requested symmetry setting and the resulting `symmetry_policy` together in user-visible job/result surfaces so users can tell whether a run kept the full model because symmetry was disabled, rejected, or successfully applied.
- Update runtime docs to clarify that imported ATH `Mesh.Quadrants` values do not directly trim the canonical simulation payload; full-model vs reduced-model behavior is determined by the solver symmetry policy.

Re-open the backlog when:
- a new product or runtime requirement lands
- a deferred watchpoint becomes an active delivery bottleneck
- a regression or documentation drift needs tracked follow-through across multiple slices

## Deferred Watchpoints

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

## Historical Notes

The detailed March 11-12, 2026 execution record, including the completed P0-P4 slices and their rationale, has been archived in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.
