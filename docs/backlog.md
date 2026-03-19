# Backlog

Last updated: March 19, 2026 (workspace generation artifact unification slice landed; dependency/runtime doctor item clarified)

This file is the active source of truth for unfinished product and engineering work.
Resolved history and superseded backlog sections moved to `docs/archive/BACKLOG_REORGANIZATION_2026-03-19.md`.
Detailed completion history from the March 11-12, 2026 cleanup phase lives in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Working Rules

### Upstream-downstream integrity

Modules must not compensate for defects that belong upstream. Each module should receive correct input and fail visibly if it does not. When downstream code contains a workaround for an upstream defect, the fix belongs in the upstream module, not in the workaround.

### Tessellation-last principle

Tessellation (mesh generation) must always be the last geometry transformation step. Never modify, clip, or transform a tessellated mesh to achieve geometric changes. Modify the upstream parametric/B-Rep geometry and re-tessellate.

### Docs and audit discipline

Keep durable decisions in `docs/architecture.md`, active unfinished work in this file, and historical execution detail in `docs/archive/`.

## Execution Lanes

- `GLM-5 suitable`: medium-to-low complexity, bounded scope, not reserved.
- `Reserved`: complex or cross-cutting work. Prefer Codex and Opus at the listed reasoning level.

## Current Baseline

Status as of March 19, 2026:

- The architecture cleanup plan is complete.
- The enclosure BEM simulation bug (`enc_depth < horn_length`) is fixed.
- Settings modal, viewer settings, folder workspace, parameter naming/hover-help, geometry diagnostics, advanced solver controls, and job feed cleanup are all shipped.
- MSH import, return-to-parametric, and filename-derived export naming are working.
- Measurement distance propagation is verified UI to solver to observation frame, with effective distance shown in View Results.
- Symmetry-solver cleanup is complete; the blocked image-source runtime path is gone from the active runtime.
- UI quality, design quality, and redesign audits are complete.
- Latest documented full-suite baseline before current working changes was green for `npm test` and `npm run test:server`.

## Active Backlog

### P1. Rebuild Output Workspace Contract and Fix Firefox Server-Folder Regression (March 19, 2026)

**Status:** COMPLETE
**Execution lane:** Reserved — Codex `high`; Opus `high`

**Description:** The current output-folder flow is still misleading and fragmented. Firefox shows a backend output folder, but manual export writes do not actually bind to that folder, so files fall through to browser download/save behavior instead of landing in the displayed directory. Workspace metadata and exported artifacts are also split across different naming roots (`job.id` vs `outputName_counter`), so there is no single understandable generation folder for users.

**Implementation notes:**

- Current contract mismatch spans `src/ui/workspace/folderWorkspace.js`, `src/ui/fileOps.js`, and `server/api/routes_misc.py`: the UI displays an absolute backend path, while `/api/export-file` only accepts repo-relative folder paths.
- Completed-task bundle exports and folder task manifests/index now align on `job.label` / base-name folders when that generation name is available, while manifest/index job identity remains stable under `id`.
- The earlier Firefox output-folder slice shipped `/api/workspace/path`, `/api/workspace/open`, and the settings-panel affordance, but it did not complete the end-to-end write contract. Treat that item as groundwork, not as the final solution.
- `docs/modules/export.md`, `docs/modules/simulation.md`, and settings copy still describe `<workspace>/<jobId>/`, which does not match the current runtime behavior.
- No tests currently cover `/api/export-file`, `/api/workspace/path`, `/api/workspace/open`, or Firefox/non-File-System-Access export routing.

**Action plan:**

- [x] Define one cross-browser workspace model: backend-managed workspace root plus optional direct-write File System Access optimization on supporting browsers
- [x] Replace the current absolute-path display + relative-path export mismatch with explicit backend workspace configuration/read/write APIs
- [x] Fix Firefox/non-`showDirectoryPicker` behavior so exports either land in the shown workspace or clearly announce that the browser download path is being used
- [x] Unify manifests, raw simulation data, mesh artifacts, and selected exports under one human-readable generation folder named from `<outputName>_<counter>`
- [x] Add a user-facing project manifest/file format plus deterministic naming rules for ATH/MWG script snapshots, raw results, mesh artifacts, and optional exported files
- [x] Update docs and add regression tests for workspace/export routing and deterministic folder naming

Progress note (March 19, 2026):
- Backend workspace export contract now writes via `/api/export-file` with optional `workspace_subdir`; Firefox/non-File-System-Access path now routes manual and bundle export writes through backend workspace root before browser fallback.
- Regression coverage added for backend workspace path/open/export routes and frontend workspace-subdirectory export routing.
- Folder task manifests/index now persist to the same generation folder naming contract used by bundle exports (legacy `job.id` folders remain readable and rebuild deduplicates by stable job id).
- Generation folders now include user-facing `waveguide.project.v1.json`, deterministic bundle artifact naming, and deterministic script snapshot artifact `script.snapshot.mwg`; regression tests cover naming/manifest behavior.
- Completion-time runtime artifacts now persist to the same generation folder contract: deterministic raw-results snapshot (`<jobLabel>_raw.results.json`) and deterministic mesh artifact mirror (`<jobLabel>_solver.mesh.msh`) are indexed in `waveguide.project.v1.json`.

### P1. Restrict Scale to Waveguide Geometry Only (March 19, 2026)

**Status:** COMPLETE
**Execution lane:** GLM-5 suitable — medium complexity, not reserved

**Description:** `scale` currently mutates enclosure dimensions as well as horn dimensions, so changing waveguide scale also changes `encDepth`, `encEdge`, enclosure margins, and downstream OCC export resolutions. It is also still treated as formula-capable in the UI, even though current intent is numeric-only scaling.

**Implementation notes:**

- Current coupling is upstream in `src/geometry/params.js`, where `SCALE_LENGTH_KEYS` includes enclosure fields plus mesh-resolution fields.
- `scale` is exposed as formula-capable via `src/config/schema.js`, and that behavior is locked in by `tests/param-panel.test.js`.
- OCC/export normalization in `src/modules/design/index.js` also multiplies solve/export resolution fields by scale; that needs an explicit decision in the same slice so preview, export, and solve paths stay aligned.

**Action plan:**

- [x] Remove enclosure-only fields (`encDepth`, `encEdge`, `encSpaceL`, `encSpaceT`, `encSpaceR`, `encSpaceB`) from upstream scale application in `src/geometry/params.js`
- [x] Decide and document whether solve/export mesh resolution fields should continue to scale with `scale`; then make `src/modules/design/index.js` match that contract explicitly
  - **Decision:** Resolution fields (throatResolution, mouthResolution, rearResolution, encFrontResolution, encBackResolution) represent mesh ELEMENT SIZE in mm, not element count. They MUST scale with geometry to maintain consistent mesh density. Scaling now happens once in `prepareOccSimulationParams` (for both simulation and export), not in `prepareGeometryParams`. Removed from `SCALE_LENGTH_KEYS` to avoid double-scaling.
- [x] Remove `scale` from the formula allowlist and keep it numeric-only in `src/config/schema.js`
- [x] Update Scale tooltip/copy to say it affects waveguide geometry only
- [x] Update/add tests in `tests/geometry-params.test.js`, `tests/design-module.test.js`, and `tests/param-panel.test.js`

### P2. Audit Dependencies and Add Cross-Platform Runtime Doctor (March 19, 2026)

**Status:** IN PROGRESS
**Execution lane:** Reserved — Codex `high`; Opus `high`

**Description:** Dependency installation pain is coming from two different sources that currently blur together: some dependencies are genuinely hard to provision across operating systems (`bempp-cl`, OpenCL runtimes, and some `gmsh` wheel combinations), while the repo also has fixable install-flow and messaging defects. Right now dependency truth is fragmented, launcher/runtime selection can drift away from the interpreter the installer populated, startup scripts still contain stale mock-solver messaging, and there is no single cross-platform preflight/doctor flow that tells users exactly what is missing, which features are blocked, and how to fix the current machine.

**Implementation notes:**

- Current dependency truth is split across `package.json`, `server/requirements.txt`, `server/requirements-gmsh.txt`, `install/install.sh`, `install/install.bat`, `scripts/start-all.js`, `server/start.sh`, and `server/solver/deps.py`.
- Initial audit candidates from current code:
  - `jszip` appears unused beyond a static script include in `index.html`
  - `trimesh` appears unused across active runtime and tests
  - `express` is only used by `scripts/dev-server.js`
  - `uvicorn[standard]` should be re-validated against actual runtime needs versus plain `uvicorn`
- `scripts/start-all.js` and `server/start.sh` still say the app/server can continue with mock-data or mock-solver behavior, which conflicts with the maintained no-mock runtime contract.
- Launchers (`launch/*.bat|*.sh|*.command`) still delegate to `npm start`, so successful startup depends on `scripts/start-all.js` selecting the same interpreter/environment that `install/install.*` populated.
- Backend runtime gating already exists for Python/gmsh/bempp-cl and parts of the UI already consume `/health`, but the current product still lacks a single early preflight/doctor surface before users hit a failing backend start, export, or solve path.
- Treat OpenCL separately from ordinary Python package installation: package install may succeed while solve performance or device availability remains broken because the host OpenCL runtime/driver stack is missing or misconfigured.

**Action plan:**

- [x] Audit every declared dependency against actual runtime/build/test usage and remove dead packages or document why they remain
- [ ] Replace simple single-purpose dependencies with local code where that meaningfully reduces install burden
- [ ] Define and enforce one interpreter-selection contract across installer, launchers, `npm start`, and backend startup so the app always prefers the environment it installed and verifies
- [ ] Normalize installer, launcher, backend, and UI messaging around the maintained no-mock-solver contract
- [ ] Add a post-install verification/preflight step that proves `fastapi`, `gmsh`, `bempp-cl`, and OpenCL detection status for the exact interpreter that will be launched
- [ ] Add a cross-platform dependency doctor command/endpoint that reports installed, missing, unsupported, and optional components with OS-specific install guidance
- [ ] Surface dependency status in the UI before backend start/export/solve actions fail, including feature impact and guidance for gmsh, bempp-cl, OpenCL runtime, and matplotlib
- [ ] Add regression tests for dependency doctor output and dependency-status rendering

Progress note (March 19, 2026):
- Removed dead dependencies proved unused across active runtime/tests: frontend `jszip` (plus stale `index.html` script include) and backend `trimesh`.
- Kept `express` because it is the current frontend dev server runtime (`scripts/dev-server.js`, `npm start:frontend`, `npm run dev`).
- Kept `uvicorn[standard]` because backend startup still runs through `uvicorn` in `server/app.py`; extras policy (plain vs standard) is deferred to the doctor/preflight/interpreter-contract slices.

### P2. Finish Single-Precision Default Alignment Across UI and Directivity Helpers (March 19, 2026)

**Status:** COMPLETE
**Execution lane:** GLM-5 suitable — low-to-medium complexity, not reserved

**Description:** The backend precision normalization already defaults to single precision, and server tests cover that behavior. The remaining gap is in UI defaults and messaging: advanced solver settings still default to `"double"`, the tooltip still recommends double as the default, and `server/solver/directivity_correct.py` still advertises `"double"` in helper signatures even though active runtime behavior now centers on single precision.

**Implementation notes:**

- `server/solver/solve_optimized.py` already defaults `_normalize_bem_precision()` to `"single"`, with coverage in `server/tests/test_solver_hardening.py`.
- Remaining stale defaults are in `src/ui/settings/simAdvancedSettings.js`, `src/ui/settings/modal.js`, and `server/solver/directivity_correct.py`.
- The backlog item is no longer a full solver-verification epic; it is now a consistency cleanup slice plus end-to-end validation for both precision modes.

**Action plan:**

- [x] Change `RECOMMENDED_DEFAULTS.bemPrecision` to `'single'` in `src/ui/settings/simAdvancedSettings.js`
- [x] Update `src/ui/settings/modal.js` copy so single precision is the recommended default and double is framed as the higher-cost fallback when needed
- [x] Change `server/solver/directivity_correct.py` function signature defaults from `"double"` to `"single"` for consistency
- [x] Verify both single and double still work end-to-end
- [x] Update/add tests where UI defaults or copy are asserted

### P2. Enrich Simulation Results Metadata and Add Fast Directivity Re-render (March 19, 2026)

**Status:** IN PROGRESS
**Execution lane:** Reserved — Codex `high`; Opus `medium-high`

**Description:** The View Results modal still omits solve timestamp and directivity-map settings, and it does not expose any post-solve directivity-map refresh path even though the chart-rendering pipeline can already redraw from cached result data without rerunning BEM.

**Implementation notes:**

- Job timestamps already exist in `server/services/job_runtime.py`, but the current result-summary plumbing does not surface them in the modal.
- The solver computes an `effective_polar_config` in `server/solver/solve_optimized.py` but does not persist the full effective directivity configuration in result metadata.
- Fast post-solve redraw is feasible only for display-only map options. Solve-time polar settings like sweep angles, enabled axes, diagonal angle, and observation distance still require a new solve because they change generated directivity data.

**Action plan:**

- [x] Remove the standalone observation-distance row outside the main solve-statistics block
- [ ] Add simulation date/time to the results summary using persisted job timestamps
- [ ] Persist and display directivity-map details used for the solve: angle range, angular step/sample count, enabled axes, diagonal angle, normalization angle, effective observation distance, and observation origin
- [ ] Extend result/job metadata plumbing so the View Results modal can read those details without reconstructing them heuristically
- [ ] Add a lightweight post-solve directivity-map re-render path for display-only options that do not require a new BEM solve
- [ ] Add/update frontend and backend tests covering results summary content and metadata persistence

## Deferred Watchpoints

### Internationalization (i18n) Infrastructure

**Status:** DEFERRED — large scope, not blocking current release
**Execution lane:** Reserved — Codex `high`; Opus `high`

- Entire frontend still uses hard-coded English strings.
- Activate this only when localization becomes a release requirement.

Action plan when activated:

- [ ] Decide on i18n approach (library vs. message-file extraction)
- [ ] Extract UI strings into a message catalog
- [ ] Implement formatting/pluralization support and regression coverage

### Replace Gmsh-Centric Export Coupling

**Status:** DEFERRED — wait for solve-mesh and export-artifact parity work
**Execution lane:** Reserved — Codex `high`; Opus `high`

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.

### Decompose `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py`

**Status:** DEFERRED — only activate when feature work makes file size a delivery bottleneck
**Execution lane:** Reserved — Codex `medium-high`; Opus `medium-high`

- Keep this deferred unless new work is slowed down by those files' size and coupling.

### Decompose `server/services/job_runtime.py`

**Status:** DEFERRED — only activate when queueing/persistence lifecycle requirements expand
**Execution lane:** GLM-5 suitable for bounded prep slices; reserve Codex/Opus for the full refactor

- Keep this deferred unless queueing, persistence, or multi-worker lifecycle requirements materially expand.
