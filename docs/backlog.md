# Backlog

Last updated: March 21, 2026 (added P2 legacy mesh path cleanup item)

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

### P1 — Fix false-green `/api/solve` readiness on OpenCL CPU hosts

**Status:** OPEN
**Execution lane:** Reserved — Codex `high`

- On the current macOS/arm64 `opencl-cpu-env`, runtime preflight and `/health` report `requiredReady=true` / `selected_mode=opencl_cpu`, and Tritonia mesh prep succeeds, but a real 1 kHz solve still fails immediately: `python server/scripts/benchmark_tritonia.py --json --freq 1000 --device auto --precision single --timeout 30` exits `2` with `All 1 frequencies failed to solve`.
- The traced failure boundary is inside `bempp_cl` OpenCL dense assembly, not the app contract layer: `KeyError(2)` from `bempp_cl/core/opencl_kernels.py:get_vec_string` while assembling the single-layer operator. A forced `numba` operator path completes the same prepared problem, but that path is currently benchmark/test-only evidence and must not become the implicit supported runtime contract unless explicitly chosen.
- The current macOS setup path provisions a CPU OpenCL environment (`opencl-cpu-env` with `pocl`) and validates CPU OpenCL only; it does not establish Apple Silicon GPU-backed OpenCL as a working solver target. The backlog must treat “real GPU acceleration on Apple Silicon” as an unverified host/runtime question, not an assumed capability.

Implementation notes:

- Runtime selection / readiness reporting: `server/solver/device_interface.py`, `server/services/runtime_preflight.py`, `server/api/routes_misc.py`, `server/scripts/runtime_preflight.py`
- Solver recovery path: `server/solver/solve_optimized.py`, `server/solver/directivity_correct.py`, `server/solver/bem_solver.py`
- Repro / coverage / docs: `server/scripts/benchmark_tritonia.py`, `server/tests/test_solver_hardening.py`, `server/tests/test_runtime_preflight.py`, `server/tests/test_device_interface.py`, `server/tests/test_tritonia_benchmark.py`, `server/tests/test_dependency_runtime.py`, `server/README.md`, `docs/PROJECT_DOCUMENTATION.md`

Action plan:

- [x] Reproduce the Apple Silicon failure in code-level regression coverage by asserting the current `opencl_cpu` Tritonia solve fails with the surfaced `All 1 frequencies failed to solve. First failure(s): 2` signature under `RUN_BEM_REFERENCE=1`, which preserves the traced `KeyError(2)` boundary from the live `opencl-cpu-env` repro. (2026-03-20: added live-gated regression coverage in `server/tests/test_tritonia_benchmark.py` against the dedicated `opencl-cpu-env` interpreter path.)
- [x] Record the current bounded-solve evidence correctly: the live Tritonia repro still fails under `opencl_cpu`, while the same prepared mesh succeeds when `solve_optimized` is forced to `numba` boundary/potential operators. Keep that evidence in regression coverage, but do not treat it as the accepted runtime direction. (2026-03-20: live coverage added in `server/tests/test_tritonia_benchmark.py`.)
- [x] Decide the supported Apple Silicon runtime contract explicitly: mark Apple Silicon OpenCL solve unsupported/unready for now, and do not silently convert the production runtime to `numba` fallback unless that choice is made deliberately. (2026-03-20: `server/solver/device_interface.py` now treats Apple Silicon OpenCL modes as unsupported for `/api/solve`, `/health` and runtime doctor inherit the unready contract, and docs now describe `./scripts/setup-opencl-backend.sh` as investigation-only rather than a validated readiness path.)
- [ ] Implement readiness gating so `/health` / runtime doctor report actual validated solver readiness from a bounded solve path, not raw OpenCL import + device enumeration.
- [ ] Add a host-level validation slice for Apple Silicon GPU viability: prove whether the current `bempp-cl` + OpenCL stack can run on a real GPU-backed path on Apple Silicon, or document that the maintained runtime is CPU OpenCL only / GPU unsupported.
- [ ] Update benchmark/preflight tooling so "ready" means a bounded solve path passes on the intended supported backend, not just dependency import + device enumeration.
- [ ] Refresh docs and rerun the Tritonia repro plus `npm run test:server` and `npm test`.

### P1 — Retire active `quadrants` partial-mesh behavior from OCC solve/export

**Status:** OPEN
**Execution lane:** Reserved — Codex `medium`

- Symmetry-reduced solving is no longer part of the active runtime, but `quadrants` is still exposed as a solve/export control and still reaches the OCC builder, where it generates half- and quarter-domain meshes. That leaves the runtime contract internally inconsistent: frontend canonical mesh is full-domain-only, docs say imported quadrants are metadata only, while `/api/solve` and `/api/mesh/build` still accept and apply partial-domain values.

Implementation notes:

- Frontend/UI/config: `src/config/schema.js`, `src/ui/parameterInventory.js`, `src/modules/design/index.js`, `src/solver/waveguidePayload.js`, `src/config/index.js`, `src/export/mwgConfig.js`
- Backend OCC/runtime: `server/contracts/__init__.py`, `server/api/routes_mesh.py`, `server/services/simulation_runner.py`, `server/solver/waveguide_builder.py`
- Coverage/docs: `server/tests/test_api_validation.py`, `tests/export-module.test.js`, `tests/waveguide-payload.test.js`, `docs/modules/simulation.md`, `docs/modules/geometry.md`, `docs/PROJECT_DOCUMENTATION.md`, `server/README.md`

Action plan:

- [ ] Define the contract explicitly: active OCC solve/export paths always build full-domain meshes (`quadrants=1234`), while legacy `Mesh.Quadrants` values remain import-compatible metadata only.
- [ ] Remove or hide `quadrants` from the active solve/export UI and stop describing half/quarter BEM analysis as a supported runtime behavior.
- [ ] Canonicalize OCC request construction so `/api/solve` and `/api/mesh/build` no longer generate partial meshes from user-facing `quadrants` values.
- [ ] Decide legacy config handling: tolerate import of non-`1234` values, but export either `1234` or omit the field unless a dedicated compatibility mode is introduced.
- [ ] Replace tests and docs that currently assert non-full-domain OCC acceptance with full-domain-only contract coverage.

### P1 — Collapse the unstable BEM "optimized" path into one supported solver runtime

**Status:** COMPLETE
**Execution lane:** Reserved — Codex `high`; Opus `high`

- Tritonia-M shows the current optimized solver is not a trustworthy default. The ATH import and canonical mesh preparation succeed, but a 1 kHz / 1-frequency `single` solve still times out after 90 seconds even when forced to `tol=1e-3` and `use_strong_form=False`, while `double` fails immediately on Apple M1 Max OpenCL with a kernel creation error.
- The current stack still exposes too much unstable behavior across API/UI/runtime boundaries: unsupported precision/device combinations, `workers` accepted but unused by the public sweep, OpenCL auto-selection and retry heuristics, optional warm-up, runtime-capability reporting that describes unstable knobs instead of support, and heavier-than-needed directivity post-processing.

Implementation notes:

- Backend solver/runtime: `server/solver/solve_optimized.py`, `server/solver/bem_solver.py`, `server/solver/device_interface.py`, `server/services/solver_runtime.py`, `server/services/simulation_runner.py`, `server/contracts/__init__.py`, `server/README.md`
- Frontend/API surface: `src/solver/index.js`, `src/ui/settings/modal.js`, `src/ui/settings/simAdvancedSettings.js`, `src/ui/runtimeCapabilities.js`, `src/ui/simulation/jobActions.js`, `src/ui/simulation/results.js`
- Coverage/docs to update: `server/tests/test_solver_hardening.py`, `server/tests/test_api_validation.py`, `tests/sim-advanced-settings.test.js`, `tests/simulation-module.test.js`, `docs/PROJECT_DOCUMENTATION.md`, `docs/architecture.md`

Action plan:

- [x] Add a bounded Tritonia-M benchmark/repro path (1-frequency and reduced sweep) that reports mesh-prep success, selected runtime/device, solver stage timings, and supported vs unsupported precision modes on the active host. (2026-03-20: `server/scripts/benchmark_tritonia.py` provides a dedicated repro path: `cd server && python3 scripts/benchmark_tritonia.py [options]` or `npm run benchmark:tritonia`. Options include `--freq`, `--sweep`, `--device`, `--precision single|double|both`, `--json`, `--no-solve`, `--timeout`. Reports mesh-prep success, device/runtime metadata, per-precision solve outcomes, and unsupported mode detection. Tests in `server/tests/test_tritonia_benchmark.py`. See `tests/TESTING.md` for usage.)
- [x] Collapse the public BEM entrypoint to one stable solver path; remove `useOptimized` from the public contract and UI, and internalize any remaining legacy-vs-optimized differences unless a retained second path has a clear, tested user-facing contract. (2026-03-20: frontend/UI and public request surface no longer expose `useOptimized`; backend now always executes `solve_optimized` and treats `use_optimized` as compatibility-only ignored input. Internal legacy runtime wrappers/tests tied to `solver.solve` were retired and contract docs/tests now assert `solve_optimized` as the single supported runtime entrypoint.)
- [x] Remove or hide unsupported public knobs first: disable `double` on unsupported OpenCL GPU runtimes, stop advertising `workers` until the public sweep actually uses them, and remove or hard-disable `enableWarmup`, `bemPrecision`, and likely `deviceMode` if they remain solver-internal implementation choices rather than supported user-facing controls. (2026-03-20: the active frontend no longer exposes `enableWarmup`, `bemPrecision`, or `deviceMode`; `/health` Simulation Basic capability controls no longer advertise `device_mode`; backend `BEMSolver.solve(...)` now ignores compatibility-only `device_mode`, `advanced_settings.enable_warmup`, and `advanced_settings.bem_precision` while still accepting and validating them for older callers. The active `/api/solve` runtime now keeps only `use_burton_miller` as a supported advanced override, so unsupported precision/device combinations and unused worker controls are no longer part of the public request surface.)
- [x] Replace the current auto OpenCL heuristics with a conservative supported-runtime policy; keep runtime fallback only when there is a real validated fallback, not speculative GPU/CPU aliasing behavior, and update runtime-capability reporting so it reports supported vs unsupported configurations instead of exposing unstable runtime strategy. (2026-03-20: `server/solver/device_interface.py` now resolves `auto` with conservative supported ordering (`opencl_cpu` first, `opencl_gpu` only when both GPU and CPU OpenCL contexts are available), and removed GPU-only CPU-context aliasing. `/health` runtime metadata now carries `selection_policy`, `supported_modes`, and per-mode `supported` status. Coverage updated in `server/tests/test_device_interface.py`, `server/tests/test_runtime_preflight.py`, and `server/tests/test_dependency_runtime.py`; runtime guidance updated in `server/README.md` and `docs/PROJECT_DOCUMENTATION.md`.)
- [x] Simplify solver numerics to explicit, minimal defaults: no automatic strong-form enablement, no platform-specific "smart" behavior without proof, and no mixed-precision operator assembly. (2026-03-20: `server/solver/solve_optimized.py` now enforces a fixed active numerics policy (`single` precision, no warm-up pass) while still accepting compatibility precision/warm-up inputs without applying them; GMRES strong-form auto-enable was removed and performance metadata/docs were trimmed to the reduced numerics surface.)
- [x] Reduce simulation runner orchestration and stage reporting to the core phases required for a stable solve, then trim result packaging and post-processing to the minimum contract the current UI actually needs; defer extra DI refinement, diagonal-plane packaging, and metadata-heavy summaries into follow-up slices if they remain valuable after the solver path is stable. (2026-03-20: stage reporting was reduced to core live phases `initializing|mesh_prepare|bem_solve|finalizing`; `metadata.performance` now exposes only `total_time_seconds` and `bem_precision`, matching the active UI summary contract; directivity post-processing no longer rewrites `results.di` from polar-map refinement. Diagonal-plane solving stays supported, but the active contract is now plane-driven: the solver normalizes requested planes once, `results.directivity` emits only requested planes, `metadata.directivity` persists both `enabled_axes` and normalized `planes`, and the UI/export paths no longer assume hard-coded `horizontal|vertical|diagonal` payload keys.)
- [x] Update the backend/frontend contract, runtime guidance, and tests to match the reduced solver surface, then rerun `npm test` and `npm run test:server`. (2026-03-20: contract/runtime docs in `server/README.md`, `docs/PROJECT_DOCUMENTATION.md`, and `docs/architecture.md` now reflect the fixed numerics policy and collapsed live stage contract; regression coverage was updated in `server/tests/test_solver_hardening.py`, `server/tests/test_api_validation.py`, `tests/simulation-module.test.js`, and related frontend flow coverage. Verified locally after the accepted slices with `npm run test:server` (195 tests, 7 skipped) and `npm test` (312 tests).)

Add new execution slices here when a deferred watchpoint is activated or a new verified issue is opened.

### P2 — Remove legacy mesh paths and `refine_mesh_with_gmsh` dead code

**Status:** OPEN
**Execution lane:** GLM-5 suitable

- The legacy JS `.geo` export path (`gmshGeoBuilder.js` → `POST /api/mesh/generate-msh` → `gmsh_geo_mesher.py`) and the optional `refine_mesh_with_gmsh()` BEM refinement path are both dead code. Production files were already deleted, but `use_gmsh` plumbing, test assertions against the deleted `/api/mesh/generate-msh` endpoint, and stale doc references remain. Gmsh itself stays — it is the core meshing engine (~140 calls in `waveguide_builder.py` for BSpline geometry, mesh generation, physical groups, `.msh` export). `tests/helpers/legacyMsh.js` and `server/solver/gmsh_utils.py` also stay — both are actively used.

Implementation notes:

- Delete `refine_mesh_with_gmsh()` and remove `use_gmsh`/`target_frequency` params: `server/solver/mesh.py`, `server/solver/bem_solver.py`, `server/services/simulation_runner.py`, `server/solver/mesh_validation.py`
- Delete `test_use_gmsh_requires_gmsh_runtime`: `server/tests/test_mesh_validation.py`
- Remove legacy `/api/mesh/generate-msh` test references: `tests/export-gmsh-pipeline.test.js` (remove `generate-msh` assertions from OCC test; delete entire 503-fallback test)
- Update stale docstring referencing deleted `gmshGeoBuilder.js`: `server/solver/waveguide_builder.py` lines 12–13
- Update stale doc reference to "legacy `.geo` tooling": `docs/PROJECT_DOCUMENTATION.md` line 261

Action plan:

- [x] Delete `refine_mesh_with_gmsh()` (mesh.py lines 55–207) and remove the `use_gmsh`/`target_frequency` parameters and `if use_gmsh:` block from `prepare_mesh()`.
- [x] Remove `refine_mesh_with_gmsh` import/wrapper and `use_gmsh` param from `bem_solver.py`.
- [ ] Remove `use_gmsh` extraction logic, `target_freq` variable, and `use_gmsh=` kwargs from `simulation_runner.py`.
- [ ] Remove `use_gmsh` mention from validation message in `mesh_validation.py`.
- [x] Delete `test_use_gmsh_requires_gmsh_runtime` from `test_mesh_validation.py`.
- [ ] In `tests/export-gmsh-pipeline.test.js`: remove `generate-msh` assertion lines from the OCC endpoint test; delete the entire 503-fallback-to-`generate-msh` test.
- [ ] Update `waveguide_builder.py` docstring (lines 12–13) and `docs/PROJECT_DOCUMENTATION.md` (line 261) to remove references to deleted legacy paths.
- [ ] Verify: `npm test`, `npm run test:server`, and grep for `refine_mesh_with_gmsh`, `use_gmsh`, `generate-msh` returns zero active hits.

## Deferred Watchpoints

### Replace Gmsh-Centric Export Coupling

**Status:** DEFERRED — wait for solve-mesh and export-artifact parity work
**Execution lane:** Reserved — Codex `high`; Opus `high`

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.

### Internationalization (i18n) Infrastructure

**Status:** DEFERRED — large scope, not blocking current release
**Execution lane:** Reserved — Codex `high`; Opus `high`

- Entire frontend still uses hard-coded English strings.
- Activate this only when localization becomes a release requirement.

Action plan when activated:

- [ ] Decide on i18n approach (library vs. message-file extraction)
- [ ] Extract UI strings into a message catalog
- [ ] Implement formatting/pluralization support and regression coverage

### Decompose `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py`

**Status:** DEFERRED — only activate when feature work makes file size a delivery bottleneck
**Execution lane:** Reserved — Codex `medium-high`; Opus `medium-high`

- Keep this deferred unless new work is slowed down by those files' size and coupling.

### Decompose `server/services/job_runtime.py`

**Status:** DEFERRED — only activate when queueing/persistence lifecycle requirements expand
**Execution lane:** GLM-5 suitable for bounded prep slices; reserve Codex/Opus for the full refactor

- Keep this deferred unless queueing, persistence, or multi-worker lifecycle requirements materially expand.
