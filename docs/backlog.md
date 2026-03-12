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
- `src/ui/paramPanel.js` now renders geometry/source/mesh controls from an explicit parameter-section inventory, and both schema-driven controls and directivity controls use the shared hover-help trigger pattern. Settings modal controls still need parity with that affordance.
- Not every schema parameter is visible. `PARAM_SCHEMA.MESH` includes `throatSliceDensity` and `verticalOffset`, but the current parameter UI does not render either control.
- Simulation frequency and polar controls remain outside the settings modal, but they now share the same naming/order pass and hover-help affordance as the schema-driven parameter controls.
- The settings modal now groups persistent preferences into `Viewer`, `Simulation`, `Task Exports`, `Workspace`, and `System` sections, and every visible viewer/task-setting row uses the shared hover-help affordance.
- The Simulation section exposes the active runtime overrides (`deviceMode`, `meshValidationMode`, `frequencySpacing`, `useOptimized`, `enableSymmetry`, `verbose`) plus optimized-solver advanced overrides for `enableWarmup`, `bemPrecision`, `useBurtonMiller`, and `symmetryTolerance`.
- Backend capability metadata now reports `simulationAdvanced.controls` for the shipped advanced overrides while keeping GMRES method/restart/tolerance/max-iteration and explicit strong-form policy items in `plannedControls`.
- Folder workspace support is now visible inside the settings modal even when `window.showDirectoryPicker` is unavailable, so unsupported browsers show an explicit fallback explanation instead of no discoverable workspace entry point.
- The primary folder-selection action should live in the simulation jobs header beside `Clear Failed` and `Refresh`, with the settings modal kept as the explanatory/status surface rather than the only picker entry point.
- Manual exports route through `src/ui/fileOps.js` and write directly to the selected folder root when possible. Completed simulation task bundles route through `src/ui/simulation/workspaceTasks.js` and write into `<workspace>/<jobId>/`. If either direct-write path fails, the app clears the selected folder and falls back to the browser picker/download flow.
- The simulation diagnostics panel now reports geometry face identities such as `throat_disc`, `inner_wall`/`horn_wall`, `outer_wall`, `rear_cap`, and enclosure faces as triangle counts before submit. Canonical numeric tags (`1/2/3/4`) remain available only as a secondary debug summary.
- Those geometry diagnostics currently come from the frontend JS canonical/preview mesh before submit, not from the backend OCC/Gmsh mesh used by `occ_adaptive` solves. Inference: equal `inner_wall` / `outer_wall` counts in free-standing wall cases can be expected from the frontend tessellation even when `rear_res` should make the backend OCC wall shell coarser.
- The simulation job list no longer spends header space on a redundant `Backend Jobs` pill; folder-backed history can still surface source context through folder mode and row-level badges when that label adds information.

Remaining work:
- Keep diagnostics, regression coverage, and documentation current as new changes land.
- Convert new requirements into the smallest coherent backlog slices instead of rebuilding a long completed log here.

## Recommended Execution Order

When new work lands, continue to work the backlog from upstream runtime truth to downstream UX:

1. Lock in runtime truth where UI changes depend on it (`enableSymmetry` behavior, canonical diagnostics semantics, folder export routing).
2. Build the full parameter/settings inventory before changing labels, grouping, or visibility.
3. Rework settings and parameter information architecture, naming, and hover-help in one coordinated UI pass.
4. Clean up diagnostics/task-feed presentation once the underlying data and labels are settled.
5. Treat advanced solver controls as a separate contract-expansion track when product explicitly asks for more GMRES method/tolerance/restart/max-iteration policy beyond the shipped `bem_precision` lane.
6. Finish with a maintained Markdown-document overhaul so user-facing docs read cleanly and match the shipped architecture after the higher-risk runtime work has settled.

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

- [x] Fix the backend jobs feed so terminal jobs that exist only in the browser cache are not shown as backend-managed rows.
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

### P1. OCC Mesh Diagnostics Must Reflect The Backend Solve Mesh

- [x] Replace the current pre-submit geometry diagnostics contract with diagnostics sourced from the backend OCC mesh build used by the actual BEM job.
  - [x] Extend the backend OCC diagnostics contract so `canonical_mesh.metadata.identityTriangleCounts` is derived during Gmsh/OCC extraction and persisted on each job as `mesh_stats.identity_triangle_counts`/`mesh_stats.tag_counts` through `/api/jobs`, instead of relying on frontend preview group ranges for authoritative solve-mesh counts.
  - [x] Update the simulation UI so preview diagnostics are labeled as preview-only and post-build/backend job diagnostics become the authoritative view after the OCC mesh exists.
  - Do not present JS preview-group counts as if they describe the adaptive OCC solve mesh.
  - Show actual backend OCC triangle/vertex diagnostics after the job mesh has been built, using backend-produced data tied to the same mesh artifact/canonical extraction that the solver consumes.
  - Preserve the distinction between preview geometry and solve geometry explicitly in the UI. If pre-submit preview counts remain visible, label them as preview-only and keep backend OCC diagnostics as the authoritative post-build/job view.
  - Ensure free-standing wall cases can show different effective rear-domain density from the throat/mouth region instead of implying `inner_wall` and `outer_wall` parity when `rear_res` is coarser.
  - For enclosure cases, ensure the shown diagnostics come from the OCC build path rather than the frontend enclosure tessellation.
  - Because the persisted `.msh` physical groups currently collapse all rigid walls into tag `1`, extend the backend diagnostics contract so face-identity rows such as `inner_wall`, `outer_wall`, `rear_cap`, `enc_front`, `enc_side`, `enc_rear`, and `enc_edge` can be derived from backend OCC truth instead of frontend group ranges.

Research notes:
- `src/ui/simulation/jobActions.js` currently renders diagnostics from `summarizeCanonicalSimulationMesh(meshData)` immediately after `panel.prepareMeshForSimulation()`, which uses the frontend JS canonical mesh path.
- The adaptive solve path in `server/services/simulation_runner.py` now builds the OCC mesh with `include_canonical=True`, extracts backend canonical arrays, and persists `mesh_stats` with authoritative vertex/triangle counts, canonical tag counts, and backend-derived face-identity triangle counts alongside the `.msh` artifact.
- `server/solver/waveguide_builder.py` applies `rear_res` to free-standing outer/rear surfaces, so backend OCC density can legitimately differ from the JS preview tessellation.
- The current backend `.msh` physical-group contract still preserves only `SD1G0` (`1`) and `SD1D1001` (`2`), so authoritative face-identity diagnostics now come from the canonical OCC extraction metadata persisted with each job rather than from the `.msh` physical-group rows alone.

Implementation notes:
- `src/ui/simulation/jobActions.js`
- `src/modules/simulation/domain.js`
- `src/ui/simulation/controller.js`
- `src/ui/simulation/jobTracker.js`
- `server/services/simulation_runner.py`
- `server/solver/waveguide_builder.py`
- `server/api/routes_simulation.py`
- `server/services/job_runtime.py`
- `server/db.py`

Required regression coverage:
- `tests/simulation-module.test.js`
- `tests/simulation-controller.test.js`
- `server/tests/test_api_validation.py`
- `server/tests/test_occ_resolution_semantics.py`

### P1. Parameter Inventory, Naming, Hover Help, and Ordering

- [x] Build a source-of-truth inventory of every user-facing parameter, then use it to rework naming, hover help, and ordering into one coherent UI pass.
  - [x] Move the simulation frequency controls into the schema-driven panel with stable DOM IDs, and expose the existing `throatSliceDensity` and `verticalOffset` schema controls in the rendered mesh section.
  - [x] Make the directivity-map controls state-backed like the frequency controls, keep their canonical `ABEC.Polars:*` blocks in sync from UI state, and treat `src/ui/simulation/polarSettings.js` as the single metadata/domain source for their defaults, labels, help text, ordering, validation, and state-to-ABEC block translation.
  - [x] Finish the broader label/order cleanup across geometry and simulation sections now that frequency and directivity controls both have a single state/metadata source.
  - [x] Reorganize geometry/simulation parameters into clearer groups with a predictable order instead of the current split between schema-driven sections and hard-coded simulation controls.
  - [x] Rewrite parameter titles into more understandable user-facing names while preserving current internal keys and config compatibility.
  - [x] Replace the current native-tooltip-only approach with a deliberate hover-help pattern for the parameter and directivity UI. Settings-modal parity continues under `P1. Settings Panel Completeness and Information Architecture`.
  - [x] Expose currently hidden schema parameters `throatSliceDensity` and `verticalOffset`, or explicitly document them as intentionally internal if product decides they should remain hidden.
  - [x] Include simulation frequency and polar controls in the same naming/help/order pass so the whole UI reads as one system.

Implementation notes:
- `src/config/schema.js`
- `src/ui/paramPanel.js`
- `index.html`
- `src/ui/simulation/polarSettings.js`

### P1. Settings Panel Completeness and Information Architecture

- [x] Audit all active settings sources and make the settings modal the primary discoverable home for persistent user preferences.
  - [x] Mirror task-list preferences `defaultSort` and `minRatingFilter` into the settings modal with the same hover-help affordance used by the parameter UI.
  - [x] Add understandable titles and hover clarifications for each visible setting.
  - [x] Keep the simulation-jobs toolbar in sync with the settings-modal task-list preferences while it remains visible as a quick-access surface.
  - [x] Reorder modal sections so viewer behavior, simulation defaults, task export behavior, folder/workspace behavior, and system actions are grouped predictably.
  - [x] Keep the modal aligned with backend capability metadata so unsupported advanced controls are clearly separated from active controls.

Implementation notes:
- `src/ui/settings/viewerSettings.js`
- `src/ui/settings/simBasicSettings.js`
- `src/ui/settings/simulationManagementSettings.js`
- `src/ui/settings/modal.js`
- `server/services/solver_runtime.py`

### P2. Folder Workspace Discoverability and Export Routing

- [x] Make folder selection discoverable near Settings and/or inside the settings modal without relying on users finding the current output-row placement.
  - [x] Move the primary folder-selection action into the simulation jobs header where the redundant `Backend Jobs` pill used to sit, next to `Clear Failed` and `Refresh`.
  - [x] Treat “folder button not visible” as a real product gap: when folder picker support is unavailable, provide a visible fallback/explanation instead of silently hiding the control.
  - [x] Document and verify expected routing behavior when a folder workspace is active:
    - manual exports write into the selected folder root
    - completed simulation bundles write into `<workspace>/<jobId>/`
    - folder write failures currently clear the selected folder and fall back to picker/download behavior
  - [x] Decide that the workspace routing promise covers manual exports plus completed simulation bundles. Folder manifests/index still persist there for history, but there is no broader catch-all redirect for unrelated generated artifacts.

Research notes:
- The runtime gate is `window.showDirectoryPicker` in `src/ui/workspace/folderWorkspace.js`, so folder workspaces currently depend on the browser File System Access API instead of a repo-owned abstraction.
- MDN currently marks `showDirectoryPicker()` as limited-availability and secure-context only, which means the feature is not baseline web platform behavior across major browsers.
- Chrome’s File System Access documentation says directory picking requires a secure context (`https://` or `http://localhost`) and is implemented in Chromium-family browsers. Inference: unsupported browsers and non-secure contexts must stay on the save-picker/download fallback path until the product adopts a different workspace mechanism.
- Product wording should stay precise: without folder workspace support, manual exports and completed simulation bundles use the browser save/download path instead of workspace writes.
- Follow-up copy/product requirement: treat this as a browser-capability constraint, not a backend-jobs/settings discovery issue, and keep the primary output-folder action visible in the simulation jobs header even when it can only explain the fallback path.

Implementation notes:
- `index.html`
- `src/app/events.js`
- `src/ui/fileOps.js`
- `src/ui/workspace/folderWorkspace.js`
- `src/ui/simulation/workspaceTasks.js`
- `src/ui/simulation/exports.js`

### P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics

- [x] Replace or augment the current numeric tag table with geometry-identity diagnostics such as `throat_disc`, `horn_wall`/`inner_wall`, `outer_wall`, `rear_cap`, `enc_front`, `enc_side`, and `enc_rear`.
  - [x] Decide that the UI reports triangle counts, not vertex counts, because the geometry groups are triangle-index ranges and the canonical solver contract is triangle-tagged.
  - [x] Preserve numeric canonical tags as a secondary debug view for solver-contract troubleshooting.
  - [x] Ensure enclosure and freestanding-wall cases produce understandable labels and stable zero/non-zero diagnostics.

Implementation notes:
- `src/ui/simulation/jobActions.js`
- `src/modules/simulation/domain.js`
- `src/geometry/pipeline.js`
- `src/geometry/tags.js`
- `src/geometry/engine/buildWaveguideMesh.js`
- `src/geometry/engine/mesh/freestandingWall.js`
- `src/geometry/engine/mesh/enclosure.js`

### P2. Advanced Solver Controls and BEM Precision Scope

- [x] Define what the requested “single precision for GMRES” setting actually means before implementation starts.
  - [x] Extend the backend/frontend contract so the already-implemented advanced overrides (`enable_warmup`, `use_burton_miller`, `symmetry_tolerance`) are exposed intentionally instead of remaining placeholder rows.
  - [x] Add understandable settings labels/help for the shipped advanced overrides and keep the remaining GMRES-focused items clearly marked as planned-only.
  - [x] Add parity tests for the new backend-exposed advanced settings before changing runtime behavior.
  - [x] Clarify that the requested precision control means a true BEMPP single-precision solve lane (`bem_precision=single`) rather than a looser GMRES preset or reduced-precision export format.
  - [x] Expose `advanced_settings.bem_precision` as an optimized-solver contract override and apply it to BEMPP operator assembly/evaluation in the solve path.
  - Further GMRES method/tolerance/restart/max-iteration and explicit strong-form policy controls stay out of the public contract unless product requests them separately.

Research notes:
- Cornu’s shipped behavior uses a separate `bem_precision` control to request single or double BEMPP operator precision while keeping GMRES tolerance and method controls separate.
- This repo now mirrors that semantic boundary: `bem_precision` is a real solve-path precision override, while GMRES method/tolerance/restart/max-iteration stay planned-only.

Implementation notes:
- `server/contracts/__init__.py`
- `server/services/solver_runtime.py`
- `server/solver/bem_solver.py`
- `server/solver/solve_optimized.py`
- `src/ui/settings/modal.js`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_api_validation.py`

### P3. Simulation Job Feed Source-Badge Cleanup

- [x] Remove or reduce the redundant per-row `Backend` badge when the entire feed is already backend-only.
  - Keep source labeling only where it adds information, such as header-level labeling or explicit `Folder` markers when folder-backed history is active.
  - Review finished-task UI copy so the feed reads cleanly without repeating the same source label on every completed row.

Implementation notes:
- `src/ui/simulation/jobActions.js`

### P3. Symmetry Runtime Truth and Operator Control Follow-Through

- [ ] Add a reproducible diagnostics lane for the ATH reference configs already called out in the earlier symmetry investigation, capturing imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
  - Add regression coverage for those reference cases so future geometry or solver changes cannot silently change reduction eligibility.
  - Expand that diagnostics lane to capture both the pre-mesh OCC/profile symmetry evidence and the post-mesh OCC canonical bounds so false negatives can be localized before they reach `metadata.symmetry_policy`.
  - Root cause found on March 12, 2026: the live `occ_adaptive` solve path checks symmetry on the extracted Gmsh canonical mesh, but `server/solver/symmetry.py` assumes the frontend canonical axis convention (`X`, axial `Y`, `Z`) and only tests `X=0` / `Z=0`; the OCC builder emits `[x_radial, y_radial, z_axial]`, so the second quarter-symmetry plane is evaluated on the axial axis instead of the second radial axis.
  - Root cause found on March 12, 2026: `_check_plane_symmetry()` requires one-to-one mirrored vertex matches inside a very tight tolerance, which works for the synthetic structured benchmark meshes but not for the unstructured OCC/Gmsh canonical meshes used in production. That is why the benchmark lane passes while the real ATH references all report `reason=no_geometric_symmetry`.
  - Current blocker: the tracked repo does not yet contain the ATH reference fixtures and expected symmetry matrix needed for a reproducible regression lane. Existing ad-hoc scripts still point at missing local-only `_references/testconfigs/*.txt` files, so the next slice needs committed fixtures (or an agreed repo-owned surrogate set) before parity tests can be added safely.
  - Proposed fix: move OCC symmetry detection upstream of triangle extraction and make it axis-aware. Detect symmetry from `_compute_point_grids(...)` / pre-mesh profile samples (or equivalent OCC geometry metadata), record the detected symmetry planes in the OCC canonical metadata, and let the solve path consume that explicit symmetry description instead of re-inferring it from raw mesh vertices.
  - Guardrail: keep asymmetric enclosure spacing or other genuinely asymmetric shell geometry as a hard stop for reduction; only the symmetric horn / wall-shell cases should become eligible again.
  - Validation target: add reference expectations for the user-reported matrix (most ATH references quarter-symmetric, Tritonia family half-symmetric once the intended reference variant is confirmed) and cover both OCC wall-shell and enclosure-backed solves.
  - [x] Audit the existing `Enable Symmetry` control in the Settings modal and verify that it is visible in the live modal, persists correctly, and changes submitted `/api/solve` payloads as expected.
  - [x] Surface the requested symmetry setting and the resulting `symmetry_policy` together in user-visible job/result surfaces so users can tell whether a run kept the full model because symmetry was disabled, rejected, or successfully applied.
  - [x] Update runtime docs to clarify that imported ATH `Mesh.Quadrants` values do not directly trim the canonical simulation payload; full-model vs reduced-model behavior is determined by the solver symmetry policy.

### P4. Maintained Markdown Document Overhaul

- [ ] Audit the maintained Markdown documentation set, then rewrite it for readability and architecture parity without inventing behavior that the code does not ship.
  - Scope the pass to the maintained `.md` docs that describe the live system: `README.md`, `AGENTS.md`, `docs/architecture.md`, `docs/PROJECT_DOCUMENTATION.md`, `docs/modules/*.md`, `tests/TESTING.md`, `server/README.md`, and `docs/archive/README.md`.
  - Verify each document against the actual runtime entry points, layer boundaries, active backend capabilities, supported export/simulation flows, and the current test map before rewriting copy.
  - Improve scannability with clearer section ordering, tighter wording, explicit source-of-truth links, and removal of stale or duplicated explanations that drift from the real architecture.
  - Keep historical plan/report snapshots in `docs/archive/` archived rather than rewriting them into maintained-truth docs; only their index/links should be updated in this pass.
  - Add or extend parity checks when the overhaul changes maintained claims that should stay synchronized with code.

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
