# Backlog

Last updated: March 15, 2026

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
- MSH import: viewport display, return-to-parametric, and filename-derived export naming are all working. Return-to-parametric viewport blank bug and MSH naming bug both fixed.
- Help tooltips moved from `?` button to label hover; `ƒ` formula button now in the label row.
- Directivity Map section now has expand/collapse matching geometry tab sections.
- Measurement distance propagation verified correct end-to-end (UI → solver → observation frame).
- Enclosure mesh edge over-refinement fixed — explicit Gmsh sizing field for enclosure edges.
- All 257 tests pass (0 failures) — 12 pre-existing failures fixed.

## Active Backlog

### P1. Symmetry Performance — Half-Model Slower Than Full Model

**BLOCKED — requires running backend with BEM solver dependencies (bempp-cl, OpenCL) to profile and A/B test. Cannot be progressed in a code-only session.**

Decision (March 2026): fix symmetry rather than disable it. Symmetry reduction controlled by the `Mesh.Quadrants` parameter (auto-detection from raw mesh vertices removed). The `enable_symmetry` UI toggle should be removed (see P2 Solver Settings Audit); `Mesh.Quadrants` is the sole control.

Remaining issue: a 1/2-symmetry simulation currently runs slower than a full-model simulation, which defeats the purpose of the feature.

Known constraints: `simulation_runner.py` enforces `quadrants == 1234` for the `occ_adaptive` path (raises `ValueError`), and `src/solver/index.js` line 266 hardcodes `enable_symmetry: false`. Both need revisiting as part of this work.

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

### P2. Observation Distance Measurement Origin

**UNBLOCKED (March 15, 2026)** — decision made: use **mouth plane** as measurement origin.

The BEM solver currently measures observation distance from the throat disc centroid (`source_center` in `infer_observation_frame`). The correct origin is the mouth plane. At 2m distance, the throat-vs-mouth offset (~120mm) introduces ~6% error. At near-field (0.5m), error reaches ~20%.

The mouth center is already computed in `infer_observation_frame` (line 129-131) but not used as the measurement origin.

Action plan:
- [x] Clarify correct measurement origin — decided: mouth plane.
- [x] Update `infer_observation_frame` to use `mouth_center` instead of `source_center` as the measurement origin.
- [ ] Run test at 0.5m distance with both origins, verify directivity improvement.
- [x] Document the measurement convention in code comments.

Implementation notes:
- `server/solver/observation.py` (`infer_observation_frame`)
- `server/solver/solve_optimized.py` (line 348, `_solve_single_frequency`)

### P2. Solver Settings Audit — Correctness, Defaults, and Tooltips

Review all solver settings for end-to-end correctness, appropriate defaults, and add mouse-over explanation text to every control.

Decision (March 15, 2026): **remove** the `enable_symmetry` UI toggle. `Mesh.Quadrants` is the sole symmetry control — the toggle is dead code (`src/solver/index.js` line 266 hardcodes it to `false`).

Issues identified:
- `enable_symmetry` toggle: remove from UI. `Mesh.Quadrants` controls symmetry reduction directly.
- `symmetryTolerance`: persisted in localStorage and sent in API contract but has no UI control. Either add a control or remove from the contract (defer until symmetry is working).
- `verbose` defaults to `true` (always detailed server logging) — consider defaulting to `false`.
- "Planned Controls" stubs (GMRES params) show in Advanced modal but are not implemented on the backend.

Action plan:
- [ ] Remove `enable_symmetry` toggle from the Settings modal UI.
- [ ] Remove the `enable_symmetry: false` hardcode in `src/solver/index.js` line 266 (no longer needed once the toggle is gone).
- [ ] Audit each remaining active setting end-to-end: UI control → localStorage → API contract → backend solver.
- [ ] Defer `symmetryTolerance` UI control until the P1 Symmetry Performance work is complete.
- [ ] Review `verbose` default — consider defaulting to `false`.
- [ ] Either implement or remove the "Planned Controls" stubs (GMRES params, strong-form preconditioner).
- [ ] Ensure every active setting has a tooltip explaining what it does, what changes when you raise/lower it, and what the recommended default is.

Implementation notes:
- `src/ui/settings/simBasicSettings.js`
- `src/ui/settings/simAdvancedSettings.js`
- `src/solver/index.js` (line 266 enable_symmetry override)
- `server/solver/solve_optimized.py`
- `server/contracts/__init__.py`

### P2. Cross-Platform Installation Hardening and Supported Runtime Matrix

**NOT STARTED — large scope touching installers, launchers, and docs across all platforms. Can be sliced into smaller pieces (e.g. preflight script first, then installer updates, then docs consolidation).**

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

### P2. Firefox Output Folder — Path Display, Finder Link, and Setup Instructions

When using Firefox, the output folder UX is poor: there is no `showDirectoryPicker` API available (Firefox does not implement it and there is no browser setting to enable it), so users currently get a raw `window.prompt()` asking for a server-relative path string. The "Choose Folder" button in the Settings modal is disabled with generic help text.

Desired behavior:
- Show the current output folder as a human-readable absolute path (default when nothing is selected: `{repo_root}/output`).
- Provide a clickable link or button that opens the output folder in Finder (macOS) / Explorer (Windows) / file manager (Linux) directly from the browser.
- Replace the `window.prompt()` fallback with a proper in-UI panel: current path display at the top, "Open in Finder" button, and clear messaging that Firefox cannot select a custom folder via the browser (not a settings issue — the API is simply not implemented in Firefox).
- If the user is on Chrome/Edge and folder selection is supported, the existing `showDirectoryPicker` flow continues as-is.

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
- **Intel Mac (macOS <=12)**: OpenCL GPU may work via Apple's built-in driver for Intel HD/Iris/UHD or AMD Radeon GPUs. Apple's driver has known workgroup-size quirks (hence the existing `configure_opencl_safe_profile()` workaround in the codebase).
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

### P2. Measurement Distance — Surface Effective Distance in UI

Audit complete (March 15, 2026) — measurement distance propagates correctly end-to-end through all code paths. One remaining task: surface the actual distance used by the solver in the View Results modal.

The backend already returns `results.metadata.observation.effective_distance_m` and records any clamping in `results.metadata.warnings` (code: `"observation_distance_adjusted"`). This data is not yet shown in the frontend.

Action plan:
- [x] Trace and verify full propagation: UI → state → HTTP → solver (all correct)
- [x] Verify default (2.0m) applied when field is empty (correct)
- [x] Verify safe-distance clamping works (correct)
- [x] Show effective distance in the View Results modal (read from `results.metadata.observation`). Display any clamping warning when the solver adjusted the distance.

Implementation notes:
- `src/ui/simulation/viewResults.js` (display effective distance + clamping warning)

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

### P3. Symmetry Runtime Truth and Operator Control Follow-Through

Depends on P1 Symmetry Performance. Deferred until the symmetry performance root causes are resolved.

- [ ] Add a reproducible diagnostics lane for ATH reference configs, capturing imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
- [ ] Add regression coverage for reference cases so future geometry or solver changes cannot silently change reduction eligibility.
- [ ] Commit ATH reference fixtures or agreed repo-owned surrogate set for reproducible regression testing.

Previously completed:
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

### P2. Safety Mechanisms in .msh Generation — RESOLVED (Keep)

Audited March 15, 2026. All three safety mechanisms are genuinely needed and do not mask upstream bugs.

### Completed Items (March 11-15, 2026)

The following items are fully shipped:
- P1. Remove Stale Local-Only Jobs From the Backend Feed
- P1. OCC Mesh Diagnostics Must Reflect The Backend Solve Mesh
- P1. Parameter Inventory, Naming, Hover Help, and Ordering
- P1. Settings Panel Completeness and Information Architecture
- P1. MSH File Import — Viewport Display and Simulation Workflow
- P1. Return to Parametric — Viewport Blank + MSH Import Naming (both bugs fixed)
- P1. Enclosure Mesh Resolution — Edge Over-Refinement (Gmsh sizing field fix)
- P2. Help Tooltip — Move from Button to Label Hover
- P2. Folder Workspace Discoverability and Export Routing
- P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics
- P2. Advanced Solver Controls and BEM Precision Scope
- P3. Directivity Map Section — Add Expand/Collapse
- P3. Pre-Existing Test Failures — 12 failures fixed (257/257 pass)
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
