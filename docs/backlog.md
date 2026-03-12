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

### P1. Cross-Platform Installation Hardening and Supported Runtime Matrix

- Replace the current “best effort” setup story with one explicit supported matrix and one verified install lane per supported OS/architecture.
- Publish support tiers instead of treating all Windows/macOS/Linux machines as equivalent:
  - Windows 10/11 x64: supported for the core app and OCC meshing; full simulation requires a verified OpenCL ICD/runtime.
  - macOS 13+ Intel x64: supported for the core app, OCC meshing, and a verified CPU-OpenCL simulation lane.
  - macOS 13+ Apple Silicon: supported for the core app and OCC meshing; full simulation remains a separately validated managed-env lane and must not be documented as parity-complete until it is verified end to end.
  - Linux x86_64: support Ubuntu 22.04/24.04 as the primary full-runtime target; keep other distros best effort unless they are added to CI.
  - Windows ARM64 and Linux ARM64: mark full simulation unsupported until gmsh/bempp/OpenCL parity is proven.
- Narrow the installer targets to exact repo-owned versions instead of open-ended ranges:
  - Node.js: pin one current LTS major for the repo and record it in a version file (`.nvmrc`, Volta, or equivalent).
  - Python: pin one tested minor for installation guidance and environment creation instead of accepting any `3.10 - 3.14` interpreter on PATH.
- Make installation modes explicit:
  - `Core app ready`: preview plus STL/CSV/config exports.
  - `OCC mesh ready`: `/api/mesh/build` available with supported Python + gmsh runtime.
  - `Simulation ready`: `/api/solve` available with supported bempp-cl + OpenCL runtime.
- Do not end installation with a generic success banner unless the achieved readiness level is stated clearly. If bempp/OpenCL is missing, finish as `Core app ready` or `OCC mesh ready`, not “fully installed”.
- Add one backend/runtime preflight command that installers and launchers both call. It should verify:
  - selected interpreter path and version
  - active `pip` target / environment root
  - `gmsh` import + version
  - `bempp-cl` import + version
  - `pyopencl.get_platforms()`
  - `/health` dependency matrix alignment once the backend starts
- Fail early on unsupported newer Python as well as too-old Python. The current installers only enforce the lower bound, which still allows an unsupported interpreter to install partially and fail later at runtime.
- Stop reintroducing interpreter drift after setup:
  - launchers should prefer the installer-created environment deterministically
  - do not silently fall back to an arbitrary system `python3` once a managed project/runtime environment exists
  - persist the chosen backend interpreter path so launchers and diagnostics use the same runtime the installer validated
- Replace optimistic fallback wording in launch/start flows with actual runtime truth. Do not imply mock solver support or a working simulation path when dependencies are absent.
- Add install smoke coverage in CI across the supported matrix so dependency regressions are caught before release:
  - Windows x64
  - macOS Intel or the closest available runner approximation
  - macOS Apple Silicon when runner availability allows, otherwise keep a manual verification lane until CI exists
  - Ubuntu x86_64
- Consolidate install/operator docs into one canonical installation guide, then keep `README.md`, `server/README.md`, and `docs/PROJECT_DOCUMENTATION.md` in parity with that guide and with `server/solver/deps.py`.

Research notes:
- `server/solver/deps.py` is the runtime truth: Python `>=3.10,<3.15`, gmsh `>=4.11,<5.0`, bempp-cl `>=0.4,<0.5`.
- `install/install.sh` and `install/install.bat` currently only reject Python older than 3.10. They do not reject unsupported newer Python, even though the backend dependency matrix does.
- `scripts/start-all.js` and `server/start.sh` can still fall back to a system `python3`, which can bypass the environment the installer configured.
- The official gmsh snapshot indexes currently publish wheels for `win_amd64`, `macosx_10_15_x86_64`, `macosx_12_0_arm64`, and `manylinux_x86_64`; the headless `-nox` index is Linux x86_64 only.
- pyopencl upstream documents Conda Forge as the easiest install path and states that `PLATFORM_NOT_FOUND_KHR` means pyopencl is installed but no OpenCL ICD/driver is present.
- bempp-cl upstream declares `requires-python = ">=3.8.0"` in `pyproject.toml`, while its installation guide still describes Conda as the easiest environment path and `pocl` as the default CPU OpenCL backend.
- As of March 12, 2026, Node.js lists `v24.14.0 (LTS)` on the official download page, and python.org shows active 3.12 and 3.13 release lines. The repo should choose one tested Python minor for installation instead of accepting every runtime in the supported matrix.

Implementation notes:
- `install/install.sh`
- `install/install.bat`
- `scripts/start-all.js`
- `server/start.sh`
- `README.md`
- `server/README.md`
- `docs/PROJECT_DOCUMENTATION.md`
- add a shared runtime-check script invoked by both installers and launchers
- add repo-owned toolchain version files / environment spec

Required regression coverage:
- `tests/docs-parity.test.js`
- `server/tests/test_dependency_runtime.py`
- add install/runtime smoke coverage for the supported OS matrix
- add launcher/preflight regression coverage for interpreter selection and readiness messaging

### P1. Remove Stale Local-Only Jobs From the Backend Feed

- Fix the backend jobs feed so terminal jobs that exist only in the browser cache are not shown as backend-managed rows.
- Treat `/api/jobs` as the source of truth whenever the panel is in backend mode. Keep local cached metadata only as an overlay for matching backend job IDs, not as permission to keep rendering backend-missing jobs indefinitely.
- Purge stale local-only backend jobs from the `ath_simulation_jobs:v1` cache during restore/reconcile once the backend job list has loaded, so pre-update rows disappear automatically without adding a legacy recovery UI.
- Do not add a special delete fallback for these stale rows. Once backend mode stops retaining them, the existing delete flow can remain a true backend delete for real backend jobs.

Research notes:
- `src/ui/simulation/controller.js` restores backend mode from `loadLocalIndex()` before fetching `/api/jobs`, then merges the two sources through `mergeJobs(seedItems, remote.items || [])`.
- `src/ui/simulation/jobTracker.js` currently preserves terminal local items even when their IDs are absent from the backend response; only missing active jobs are rewritten to `error`.
- `src/ui/simulation/jobActions.js` always calls `panel.solver.deleteJob(jobId)` before removing a row from the UI.
- `src/solver/index.js` maps that action directly to `DELETE /api/jobs/{jobId}`, so a local-only stale row always fails with `Delete simulation job resource not found (404): Job not found`.
- This explains the current symptom: old pre-update jobs remain visible in the backend feed but cannot be deleted because they are not present in SQLite anymore.

Implementation notes:
- `src/ui/simulation/jobTracker.js`
- `src/ui/simulation/controller.js`
- `src/ui/simulation/jobActions.js`

Required regression coverage:
- `tests/simulation-job-tracker.test.js`
- `tests/simulation-controller.test.js`

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
