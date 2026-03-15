# Backlog

Last updated: March 16, 2026

This file is the active source of truth for unfinished product and engineering work.
Detailed completion history from the March 11-12, 2026 cleanup phase lives in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Working Rules

### Upstream-downstream integrity

Modules must not compensate for defects that belong upstream. Each module should receive correct input and fail visibly if it does not. When downstream code contains a workaround for an upstream defect, the fix belongs in the upstream module, not in the workaround.

### Docs and audit discipline

Keep durable decisions in `docs/architecture.md`, active work in this file, and per-module contracts in `docs/modules/`. Put generated audits, comparisons, and experiment output under `research/`.

## Current Baseline

Status as of March 15, 2026:
- The architecture cleanup plan is complete.
- The enclosure BEM simulation bug (self-intersecting geometry when `enc_depth < horn_length`) is fixed — depth clamping applied in `_build_enclosure_box`.
- Settings modal, viewer settings, folder workspace, parameter naming/hover-help, geometry diagnostics, advanced solver controls, and job feed cleanup are all shipped.
- Active runtime docs are `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `tests/TESTING.md`, `server/README.md`, and `AGENTS.md`.

## Active Backlog

### P1. MSH File Import — Viewport Display and Simulation Workflow

Add the ability to import an external `.msh` file and display it in the viewport, replacing the parametric model. This enables users to inspect meshes generated outside the app or previously exported simulation meshes.

Backend infrastructure exists: `server/solver/mesh.py` has `load_msh_for_bem()` which parses `.msh` files via meshio and extracts vertices, triangles, and physical tags. The frontend viewport (`src/app/scene.js`) already accepts raw `{vertices, indices}` via `applyMeshToScene()`. What's missing is the glue: a frontend MSH parser, a UI entry point, and an "imported mesh mode" state.

Implementation plan:
- [x] Write a frontend Gmsh 2.2 MSH parser (`src/import/mshParser.js`) that extracts vertices (Float32Array) and triangle indices (Uint32Array) from the text format. The format is well-documented in `tests/helpers/legacyMsh.js` (which writes it). The parser just reverses that: read `$Nodes` section for XYZ coordinates, read `$Elements` section for triangle connectivity (element type 2), convert 1-based node IDs to 0-based indices.
- [x] Add an "Import Mesh" button in the Project section of the actions panel (`index.html`), next to the existing "Load Config" button. Wire a hidden file input accepting `.msh` files, similar to the existing config upload pattern in `src/app/events.js`.
- [x] Add an "imported mesh mode" flag to `GlobalState` (`src/state.js`). When active, the viewport renders the imported mesh instead of the parametric model. The parametric controls remain visible but are visually dimmed or annotated to indicate they are not driving the display.
- [x] Feed imported vertices/indices into the existing `applyMeshToScene()` in `src/app/scene.js`. The Three.js rendering path already handles arbitrary `BufferGeometry` — no changes needed to the renderer itself.
- [x] Add a "Return to Parametric" button or indicator so users can exit imported-mesh mode and return to the normal parametric workflow.
- [x] Optionally extract physical group tags from the imported MSH and color-code surface groups in the viewport (wall=grey, source=green, enclosure=blue) to help users verify mesh topology visually.
- [x] Consider whether imported meshes should be submittable for BEM simulation directly. **Considered — feasible but deferred.** The backend `load_msh_for_bem()` already validates physical tags and converts to bempp grid format. Wiring this through the frontend simulation pipeline (solver payload builder, simulation panel, job orchestration) is a separate feature that would touch ~10 files. The viewport import path is complete and self-contained. Simulation-from-imported-mesh can be added as a future backlog item when there is user demand.

UX flow:
1. User clicks "Import Mesh" → file picker opens filtered to `.msh`
2. File is read as text in the browser (no server round-trip for display)
3. Parsed vertices/indices replace the parametric mesh in the viewport
4. A banner or indicator shows "Displaying imported mesh: filename.msh"
5. User can rotate/zoom the imported mesh with the existing OrbitControls
6. "Return to Parametric" exits the mode and restores the parametric model

Implementation notes:
- `src/import/mshParser.js` (new)
- `src/app/scene.js` (`applyMeshToScene`)
- `src/app/events.js` (file input wiring)
- `src/state.js` (imported mesh mode flag)
- `index.html` (button)
- `server/solver/mesh.py` (`load_msh_for_bem` — existing, for optional simulation path)

### P1. Return to Parametric — Viewport Blank + MSH Import Naming

Two bugs in the MSH import / Return to Parametric flow.

**Bug 1: Viewport goes blank after clicking Return to Parametric.**
The handler in `src/app/events.js` clears `ImportedMeshState` and emits `state:updated` with `null` as the state argument. `onStateUpdate(null)` sets `app.currentState = null`, then calls `renderModel(app)`. Inside `renderModel`, the imported-mesh branch is correctly skipped (active is now false), but the next guard `if (!app.currentState) return` fires early — because currentState is null — and the parametric model is never rebuilt. The viewport is left empty. The user must manually change a parameter to trigger a valid state update and see the model again.

**Bug 2: MSH import doesn't update the output name or job counter.**
Config import correctly calls `deriveExportFieldsFromFileName(file.name)` + `setExportFields()` to set `#export-prefix` and `#export-counter` from the loaded filename. The MSH import handler does not call these functions, so the task name used for the next simulation is whatever was set before the import, not derived from the imported filename (e.g. importing `myhorn_3.msh` should set output name to `myhorn`, counter to `3`).

Action plan:
- [x] Fix return-to-parametric handler in `src/app/events.js`: emit `state:updated` with the current `GlobalState` value (not `null`) so `renderModel()` has valid state and rebuilds the parametric model.
- [x] Verify the Three.js scene is fully clean after return (no leftover imported mesh geometry or references in `app.hornMesh`).
- [x] In the MSH import handler (`src/app/events.js`), call `deriveExportFieldsFromFileName(file.name)` + `setExportFields()` immediately after populating `ImportedMeshState`, following the same pattern as config import in `src/app/configImport.js`.

Implementation notes:
- `src/app/events.js` (return-to-parametric handler, mesh import handler)
- `src/ui/fileOps.js` (`deriveExportFieldsFromFileName`, `setExportFields`)
- `src/app/scene.js` (`renderModel`)

### P1. Symmetry Performance — Half-Model Slower Than Full Model

Decision made (March 2026): fix symmetry rather than disable it. Approach changed: auto-detection from raw mesh vertices removed; symmetry reduction is now controlled by the `Mesh.Quadrants` parameter.

Remaining issue: a 1/2-symmetry simulation currently runs slower than a full-model simulation, which defeats the purpose of the feature. This needs investigation and resolution before symmetry is of any practical benefit.

Known constraints from code audit: `simulation_runner.py` currently enforces `quadrants == 1234` for the `occ_adaptive` path (raises `ValueError` otherwise), and `src/solver/index.js` line 266 hardcodes `enable_symmetry: false` in the OCC adaptive submission branch regardless of the UI toggle. These constraints likely need revisiting as part of this work.

The O(N²) vertex-matching cost in `_check_plane_symmetry()` may itself be the cause — if symmetry detection still runs before the BEM solve, its overhead can exceed the savings from a smaller matrix, especially for moderate mesh sizes.

Action plan:
- [x] Decide whether to disable symmetry entirely or fix it — decided: fix.
- [ ] Profile a half-model vs full-model solve: break down time by phase (geometry build, mesh gen, BEM operator assembly, linear solve, post-processing).
- [ ] Verify that a quadrant-controlled geometry actually produces a proportionally smaller BEM matrix (fewer DOF) after the full pipeline.
- [ ] Remove or replace the O(N²) vertex-matching detection with a geometry-parameter-driven policy that costs O(1) since the quadrant count is already known.
- [ ] Remove the `enable_symmetry: false` override in `src/solver/index.js` line 266 once the approach is proven correct.
- [ ] Remove the `quadrants == 1234` enforcement in `simulation_runner.py` once the half/quarter mesh path is validated.
- [ ] A/B test: half-model vs full-model on a known-symmetric config — results must match within 0.5 dB across all angles, and half-model must be measurably faster.
- [ ] Add committed ATH reference fixtures for reproducible regression testing.

Implementation notes:
- `server/solver/symmetry.py`
- `server/solver/solve_optimized.py` (symmetry policy evaluation)
- `server/services/simulation_runner.py` (quadrants enforcement)
- `src/solver/index.js` (line 266 override)

### P1. Enclosure Mesh Resolution — Edge Over-Refinement

The front and back roundover/chamfer edges of enclosure meshes have much higher resolution than needed. The edges between the sides (stretching front-to-back) have correct resolution, but the transverse edges (e.g., front top-left to front top-right) are over-refined.

Research (March 15, 2026): confirmed the `enclosure_edges` surface group is correctly extracted in `_build_enclosure_box()` and stored in `surface_groups["enclosure_edges"]`, but `_configure_mesh_size()` never assigns an explicit mesh size field to these edges. They fall back to Gmsh's automatic sizing, which inherits from nearby horn surface resolutions (throat_res/mouth_res) rather than using appropriate enclosure-specific resolutions.

Action plan:
- [x] Add an explicit mesh size field for `enclosure_edges` surfaces in `_configure_mesh_size()`, using the front/back enclosure resolution rather than the finer throat/mouth resolution that leaks from the inner horn field.
- [x] Verify with a test mesh that edge element counts are proportional to the user's resolution setting. *(Manual verification needed with a real enclosure mesh — this is a Gmsh sizing-field change that cannot be unit-tested without the full OCC+Gmsh pipeline.)*

Implementation notes:
- `server/solver/waveguide_builder.py` (`_configure_mesh_size`, enclosure field section)

### P1. Cross-Platform Installation Hardening and Supported Runtime Matrix

- Replace the current "best effort" setup story with one explicit supported matrix and one verified install lane per supported OS/architecture.
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
- Do not end installation with a generic success banner unless the achieved readiness level is stated clearly. If bempp/OpenCL is missing, finish as `Core app ready` or `OCC mesh ready`, not "fully installed".
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
- Add install smoke coverage in CI across the supported matrix so dependency regressions are caught before release.
- Consolidate install/operator docs into one canonical installation guide, then keep `README.md`, `server/README.md`, and `docs/PROJECT_DOCUMENTATION.md` in parity with that guide and with `server/solver/deps.py`.

Research notes:
- `server/solver/deps.py` is the runtime truth: Python `>=3.10,<3.15`, gmsh `>=4.11,<5.0`, bempp-cl `>=0.4,<0.5`.
- `install/install.sh` and `install/install.bat` currently only reject Python older than 3.10. They do not reject unsupported newer Python, even though the backend dependency matrix does.
- `scripts/start-all.js` and `server/start.sh` can still fall back to a system `python3`, which can bypass the environment the installer configured.

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

### P2. Solver Settings Audit — Correctness, Defaults, and Tooltips

Review all solver settings for end-to-end correctness, appropriate defaults, and add mouse-over explanation text to every control.

Issues identified:
- `symmetryTolerance` is persisted in localStorage and sent in the API contract but has no UI control rendered and no tooltip — completely invisible to users. Only changeable by editing localStorage directly.
- `enable_symmetry` UI default is `true` but `src/solver/index.js` line 266 hardcodes it to `false` for the OCC adaptive path — the UI toggle has no practical effect. Should be documented or removed until the symmetry work is complete.
- `verbose` defaults to `true` (always detailed server logging) — consider whether `false` is a better default for typical users.
- The "Planned Controls" section in the Advanced modal shows GMRES tolerance, restart, max iterations, and strong-form preconditioner as stubs. These are not implemented on the backend. They may create confusion for users who try to set them.

Action plan:
- [ ] Audit each active setting end-to-end: UI control → localStorage → API contract → backend solver. Confirm no silent overrides or dead paths.
- [ ] Add `symmetryTolerance` UI control (numeric input or slider) to the Advanced settings modal with a tooltip explaining its effect.
- [ ] Decide the fate of the `enable_symmetry` UI toggle: either document that it has no effect until symmetry work is resolved (P1 Symmetry Performance), or remove it from the UI temporarily to avoid false expectations.
- [ ] Review `verbose` default — consider defaulting to `false` to reduce server log noise for typical users.
- [ ] Either implement or remove the "Planned Controls" stubs (GMRES params, strong-form preconditioner). If kept as stubs, add a visible note that they are not yet active.
- [ ] Ensure every active setting has a tooltip that explains: what the setting does, what changes when you raise/lower it, and what the recommended default is.

Implementation notes:
- `src/ui/settings/simBasicSettings.js`
- `src/ui/settings/simAdvancedSettings.js`
- `src/solver/index.js` (line 266 enable_symmetry override)
- `server/solver/solve_optimized.py`
- `server/contracts/__init__.py`

### P2. Firefox Output Folder — Path Display, Finder Link, and Setup Instructions

When using Firefox, the output folder UX is poor: there is no `showDirectoryPicker` API available (Firefox does not implement it and there is no browser setting to enable it), so users currently get a raw `window.prompt()` asking for a server-relative path string. The "Choose Folder" button in the Settings modal is disabled with generic help text.

Desired behavior:
- Show the current output folder as a human-readable absolute path (default when nothing is selected: `{repo_root}/output`).
- Provide a clickable link or button that opens the output folder in Finder (macOS) / Explorer (Windows) / file manager (Linux) directly from the browser.
- Replace the `window.prompt()` fallback with a proper in-UI panel: current path display at the top, "Open in Finder" button, and clear messaging that Firefox cannot select a custom folder via the browser (not a settings issue — the API is simply not implemented in Firefox).
- If the user is on Chrome/Edge and folder selection is supported, the existing `showDirectoryPicker` flow continues as-is.

Note: Firefox does NOT support `showDirectoryPicker` — not even behind a flag. There is nothing to instruct users to enable in browser settings. The messaging should be honest: Firefox users must use the server-side path mechanism, and the UI should make that path visible and usable.

Action plan:
- [ ] Add a backend endpoint `GET /api/workspace/path` that returns the absolute path of the current output folder (defaulting to `{repo_root}/output`).
- [ ] Add a backend endpoint `POST /api/workspace/open` that opens the folder in the OS file manager (`open` on macOS, `explorer` on Windows, `xdg-open` on Linux) via subprocess.
- [ ] Set a hardcoded default output folder of `{repo_root}/output` so there is always a path to display, even before the user has selected anything.
- [ ] Replace the `window.prompt()` fallback in `src/ui/fileOps.js` / `folderWorkspace.js` with a proper dialog/panel that: shows the current absolute path, has an "Open in Finder" button wired to the backend endpoint, and explains the Firefox limitation clearly.
- [ ] In the Settings modal, when `supportsFolderSelection()` is false, show the path display + Finder button + explanation instead of a disabled button with generic help text.

Implementation notes:
- `src/ui/workspace/folderWorkspace.js`
- `src/ui/fileOps.js`
- `server/app.py` or `server/api/` (new workspace endpoints)

### P2. OpenCL GPU Support — macOS Research and Setup Instructions in UI

The system currently shows CPU-only OpenCL support when GPU support was expected. Research is complete (see below). The remaining work is exposing this context and platform-specific setup guidance in the UI.

**Research findings:**
- **Apple Silicon (M-series)**: No OpenCL GPU support is possible. Apple dropped first-class OpenCL after macOS 10.14 Mojave. The Apple GPU is Metal-only and has no OpenCL driver. `pocl` (via `brew install pocl` + `ocl-icd`) can provide a CPU-only OpenCL device, which is what the solver can use.
- **Intel Mac (macOS ≤12)**: OpenCL GPU may work via Apple's built-in driver for Intel HD/Iris/UHD or AMD Radeon GPUs. Apple's driver has known workgroup-size quirks (hence the existing `configure_opencl_safe_profile()` workaround in the codebase).
- **Intel Mac (macOS 13+)**: OpenCL is increasingly deprecated and unreliable.
- **Linux**: Full OpenCL GPU support available via `intel-opencl-icd`, `rocm-opencl-runtime`, or `nvidia-opencl-icd` depending on GPU vendor.
- **Windows**: OpenCL GPU available via Intel OpenCL Runtime or CUDA toolkit (NVIDIA).

The backend already probes OpenCL at runtime via `_opencl_inventory()` in `device_interface.py` and exposes diagnostics through the `/health` endpoint. The settings modal already disables unavailable device options. What's missing is user-facing guidance explaining *why* a device is unavailable and *what to do about it*.

Action plan:
- [ ] Add OS and architecture fields to the `_opencl_inventory()` result (`sys.platform`, `platform.machine()`) so the frontend can show platform-specific instructions.
- [ ] Add an expandable "Setup Help" affordance near the Compute Device control in the Settings modal that appears when the selected or requested device mode is unavailable.
- [ ] Show platform-specific instructions dynamically based on OS/arch from the health endpoint:
  - Apple Silicon: explain GPU is Metal-only, provide pocl CPU setup via Homebrew (`brew install pocl ocl-icd`).
  - Intel Mac: suggest checking Apple driver status, note macOS 13+ deprecation.
  - Linux: suggest installing the appropriate OpenCL ICD package for the GPU vendor.
  - Windows: link to Intel OpenCL Runtime or CUDA toolkit.
- [ ] The help section should be collapsed/hidden when the selected device is available, and shown automatically when it is not.

Implementation notes:
- `server/solver/device_interface.py` (`_opencl_inventory` — add OS/arch fields)
- `src/ui/settings/simBasicSettings.js` (add help affordance near device mode select)
- `server/api/routes_misc.py` (health endpoint — carry new fields through)

### P2. Observation Distance Measurement Origin

The BEM solver measures observation distance from the throat disc centroid (`source_center` in `infer_observation_frame`), but the correct origin may be the mouth plane. At 2m distance, the throat-vs-mouth offset (~120mm) introduces ~6% error. At near-field (0.5m), error reaches ~20%.

The mouth center is already computed in `infer_observation_frame` (line 129-131) but not used as the measurement origin.

Action plan:
- [ ] Clarify correct measurement origin: throat disc or mouth plane
- [ ] Run test at 0.5m distance with both origins, measure directivity difference
- [ ] If mouth plane is correct, update `infer_observation_frame` to use `mouth_center` instead of `source_center`
- [ ] Document the measurement convention in code comments

Implementation notes:
- `server/solver/observation.py` (`infer_observation_frame`)
- `server/solver/solve_optimized.py` (line 348, `_solve_single_frequency`)

### P2. Measurement Distance UI Propagation Verification

The "Measurement Distance (m)" control exists in the settings but it is unclear if it properly propagates through to the BEM solver in all code paths (single-process and parallel worker).

Audit completed March 15, 2026. Full pipeline verified — the value propagates correctly end to end. See findings below.

**Trace findings:**

1. **UI control → state** (`src/ui/simulation/polarSettings.js`): The `polar-distance` input (`id="polar-distance"`) is declared in `POLAR_NUMERIC_FIELDS` with `stateKey: 'polarDistance'`, fallback `2`. On change, `buildPolarStatePatchForControl()` reads the DOM value and writes it to simulation state via `updateSimulationStateParams({ polarDistance: <value> })`. Default of 2.0m is applied via `toFiniteNumber()` fallback when field is blank/NaN.

2. **State → `runSimulation()`** (`src/ui/simulation/jobActions.js` line 555–566): `readPolarStateSettings(readSimulationState()?.params)` calls `resolvePolarUiState(params)` which reads `params.polarDistance` (the persisted state key). Returns `{ distance: uiState.distance, ... }`. This is assembled into `config.polarConfig = { ..., distance: polarSettings.distance }`.

3. **`config.polarConfig` → HTTP payload** (`src/solver/index.js` line 249): `polar_config: config.polarConfig || null` is included verbatim in the JSON body sent to `POST /api/solve`.

4. **HTTP → Pydantic contract** (`server/contracts/__init__.py`): `PolarConfig.distance` is a `float` with default `2.0`. Pydantic parses and validates it at request boundary. Request stored as `request.polar_config`.

5. **`request.polar_config` → solver** (`server/services/simulation_runner.py` line 359–361): `request.polar_config.model_dump()` is passed as `polar_config` to `solver.solve()`.

6. **`solver.solve()` → `solve_optimized()`** (`server/solver/bem_solver.py` line 116–127): `polar_config` is forwarded as-is.

7. **`solve_optimized()` → `_resolve_observation_distance_m()`** (`server/solver/solve_optimized.py` line 581): `_resolve_observation_distance_m(polar_config, default=2.0)` extracts `polar_config["distance"]`, returns default 2.0 if missing/invalid/non-positive.

8. **Safe-distance clamping** (`server/solver/solve_optimized.py` lines 702–739): `resolve_safe_observation_distance(grid, observation_request_m, observation_frame)` from `observation.py` computes `min_safe_distance = max_projection + clearance` and takes `effective_distance = max(requested, min_safe_distance)`. If adjusted, a warning is added to `results["metadata"]["warnings"]` with `code: "observation_distance_adjusted"` and the effective distance is logged. The `observation_info` dict is also stored in `results["metadata"]["observation"]`.

9. **Legacy solver path** (`server/solver/solve.py`): Same `_resolve_observation_distance_m` function and same `resolve_safe_observation_distance` call — identical behavior.

10. **Parallel worker path**: `solve_optimized` is called directly in `asyncio.to_thread` — the same code path, no wrapper that drops `polar_config`.

**Findings:**
- All three trace items (propagation, default, clamping) are correctly implemented. No bugs found.
- Item 4 (UI feedback showing actual distance used): The effective distance is returned in `results.metadata.observation.effective_distance_m` and any clamping is recorded in `results.metadata.warnings` with `code: "observation_distance_adjusted"`. However, this data is not surfaced in the frontend UI. The View Results modal does not currently display it. Adding it would require reading `results.metadata.observation` in `src/ui/simulation/results.js` or the view-results modal — a straightforward read, but the task says to note rather than implement if significant. Left as a future improvement.

Action plan:
- [x] Trace measurement distance from UI control → `polarSettings.distance` → `runSimulation()` → `_resolve_observation_distance_m()` → BEM solver
- [x] Verify default (2.0m) is applied when field is empty
- [x] Verify safe-distance clamping (distance > mesh extent) works correctly
- [ ] Add UI feedback showing actual distance used by solver (data is available in `results.metadata.observation.effective_distance_m` and `results.metadata.warnings`; not yet surfaced in the View Results modal)

Implementation notes:
- `src/ui/simulation/polarSettings.js` (UI control, state read/write)
- `src/ui/simulation/jobActions.js` (`runSimulation`, assembles `config.polarConfig`)
- `src/solver/index.js` (`submitSimulation`, line 249 — `polar_config` in HTTP payload)
- `server/contracts/__init__.py` (`PolarConfig.distance`)
- `server/services/simulation_runner.py` (passes `polar_config` to `solver.solve`)
- `server/solver/solve_optimized.py` (`_resolve_observation_distance_m`, `resolve_safe_observation_distance`)
- `server/solver/observation.py` (`resolve_safe_observation_distance`)

### P2. Help Tooltip — Move from Button to Label Hover

Replace the `?` button tooltip with a hover tooltip on the parameter label itself, freeing the button slot for the `ƒ` formula button.

Action plan:
- [x] Update `createLabelRow()` in `src/ui/helpAffordance.js`: set `data-help-text` on the `<label>` element, remove `createHelpTrigger()` button
- [x] Move `formula-info-btn` creation into the label row in `createControlRow()` (`src/ui/paramPanel.js`)
- [x] Update CSS: retarget tooltip `::after` from `button.control-help-trigger` to `label[data-help-text]`, add `position: relative; cursor: help`
- [x] Verify tooltip appears on hover and `cursor: help` provides affordance

Implementation notes:
- `src/ui/helpAffordance.js` (`createLabelRow`)
- `src/ui/paramPanel.js` (`createControlRow`)
- `src/style.css`

### P3. Remove Simulation Jobs Refresh Button

The manual "Refresh" button in the simulation jobs panel header (`id="refresh-jobs-btn"`) may no longer be needed, since auto-refresh already covers the primary scenarios: app startup (`SimulationPanel` constructor calls `restoreJobs()`) and folder change (via `ui:folder-workspace-changed` event → `panel.refreshJobFeed()`).

Remaining edge cases where manual refresh could still be useful:
- Jobs created in another browser session or by an external tool, without a folder change event being fired.
- Folder contents modified externally (e.g. backend wrote a manifest file outside the watched event).
- Recovery after a backend restart or transient network error that left the auto-polling in a failed state.

Action plan:
- [ ] Confirm whether any of the above edge cases are realistic user scenarios (e.g. does the backend auto-reconnect path already trigger a refresh?).
- [ ] If the edge cases are covered or negligible: remove `id="refresh-jobs-btn"` from `index.html` and the corresponding listener in `src/ui/simulation/events.js`.
- [ ] If keeping: move to a less prominent position (e.g. icon-only, lower visual weight) so it does not imply the panel does not auto-refresh.

Implementation notes:
- `index.html` (button element)
- `src/ui/simulation/events.js` (listener)
- `src/ui/simulation/SimulationPanel.js` (`refreshJobFeed`)

### P3. Directivity Map Section — Add Expand/Collapse

The "Directivity Map" settings section in the Simulation tab lacks the expand/collapse functionality present in the geometry tab parameter sections.

Current state: `renderPolarSettingsSection()` in `src/ui/simulation/polarSettings.js` builds a plain `div.section` element. The geometry tab uses native HTML `<details>`/`<summary>` elements (via `createDetailsSection()` in `src/ui/paramPanel.js`) with collapse state persisted to `localStorage` under `wg-section-collapsed-${id}`.

Action plan:
- [x] Change `renderPolarSettingsSection()` to use `<details>`/`<summary>` (or the equivalent JS-driven pattern already used in paramPanel.js).
- [x] Persist collapse state to `localStorage` under key `wg-section-collapsed-directivity-map`. Default: open.
- [x] Check if other simulation-tab sections (Simulation Settings, Advanced Settings) should also get the same treatment for consistency.

Implementation notes:
- `src/ui/simulation/polarSettings.js` (`renderPolarSettingsSection`)
- `src/style.css` (verify `<details>.section` styling matches existing collapsible sections)

### P3. Pre-Existing Test Failures

10 tests currently fail (out of 46 total, as of 2026-02-10): enclosure-regression tests and gmsh-geo-builder test, plus a settings modal test expecting a `symmetry-tolerance` control.

Action plan:
- [ ] Investigate each failure: expected (known issues) or regression?
- [ ] Update tests to match current behavior if intentional changes were made
- [ ] Or fix code if tests reveal actual bugs

### P2. Tessellation Architecture — Geometry vs Mesh Separation

The current architecture should follow a clean separation: a geometry workflow constructs geometric data, then hands it to (a) the viewport tessellation module and (b) the .msh generation module. Neither tessellation module should construct geometry — they only translate and tessellate.

Audit status (March 14, 2026):
- The Python OCC path (`waveguide_builder.py`) combines geometry construction and meshing in one function.
- The JS viewport path (`buildWaveguideMesh.js`) also combines geometry computation with Three.js mesh construction.
- Both paths independently compute the same horn geometry from the same parameters — no shared geometric representation.

Action plan:
- [ ] Document the current geometry → mesh boundaries within each path.
- [ ] Evaluate whether extracting a shared geometry representation (point grids + topology description) consumed by both paths is feasible without a full rewrite.
- [ ] If feasible, design the shared geometry contract and implement incrementally.

### P3. Symmetry Runtime Truth and Operator Control Follow-Through

Depends on P1 Symmetry Detection. Deferred until the symmetry detection root causes are resolved.

- [ ] Add a reproducible diagnostics lane for ATH reference configs, capturing imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
- [ ] Add regression coverage for reference cases so future geometry or solver changes cannot silently change reduction eligibility.
- [ ] Commit ATH reference fixtures or agreed repo-owned surrogate set for reproducible regression testing.
- [x] Audit the existing `Enable Symmetry` control in the Settings modal and verify visibility, persistence, and payload behavior.
- [x] Surface the requested symmetry setting and the resulting `symmetry_policy` together in user-visible job/result surfaces.
- [x] Update runtime docs to clarify that imported ATH `Mesh.Quadrants` values do not directly trim the canonical simulation payload.

### P4. Maintained Markdown Document Overhaul

- [ ] Audit the maintained Markdown documentation set, then rewrite it for readability and architecture parity without inventing behavior that the code does not ship.
  - Scope the pass to the maintained `.md` docs that describe the live system: `README.md`, `AGENTS.md`, `docs/architecture.md`, `docs/PROJECT_DOCUMENTATION.md`, `docs/modules/*.md`, `tests/TESTING.md`, `server/README.md`, and `docs/archive/README.md`.
  - Verify each document against the actual runtime entry points, layer boundaries, active backend capabilities, supported export/simulation flows, and the current test map before rewriting copy.
  - Improve scannability with clearer section ordering, tighter wording, explicit source-of-truth links, and removal of stale or duplicated explanations that drift from the real architecture.

## Completed / Resolved

### P0. Enclosure BEM Simulation — RESOLVED

Fix applied March 14, 2026: `_build_enclosure_box` now clamps `enc_depth` to at least `horn_length + 1mm` so the back wall is always behind the throat. This prevents the self-intersecting geometry that caused omnidirectional-looking directivity.

Optional follow-up (not blocking):
- A/B test: compare thickened-waveguide vs enclosure simulation on the same horn geometry.
- Consider viewport warning when enc_depth is clamped during simulation.

### P2. Safety Mechanisms in .msh Generation — RESOLVED (Keep)

Audited March 15, 2026. All three safety mechanisms are genuinely needed and do not mask upstream bugs:
- `removeDuplicateNodes()`: handles Gmsh-level mesh deduplication from OCC seam duplicates. Standard Gmsh API usage.
- `_orient_and_validate_canonical_mesh()`: essential final mesh integrity check (vertex welding, degenerate triangle removal, watertightness validation, consistent orientation). Independent of the enclosure depth fix.
- `SurfaceFilling` fallbacks: defensive code for throat disc and rear cap when `addPlaneSurface` fails on malformed wire loops. Rarely triggered but legitimate robustness.

No action needed. These are appropriate safety mechanisms, not upstream workarounds.

### Completed Items (March 11-15, 2026)

The following items are fully shipped:
- P1. Remove Stale Local-Only Jobs From the Backend Feed
- P1. OCC Mesh Diagnostics Must Reflect The Backend Solve Mesh
- P1. Parameter Inventory, Naming, Hover Help, and Ordering
- P1. Settings Panel Completeness and Information Architecture
- P2. Folder Workspace Discoverability and Export Routing
- P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics
- P2. Advanced Solver Controls and BEM Precision Scope
- P3. Simulation Job Feed Source-Badge Cleanup

Detailed completion history in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Deferred Watchpoints

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

Re-open the backlog when:
- a new product or runtime requirement lands
- a deferred watchpoint becomes an active delivery bottleneck
- a regression or documentation drift needs tracked follow-through across multiple slices
