# Backlog

Last updated: March 20, 2026 (added active solver-simplification slice after Tritonia-M BEM runtime investigation)

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

### P1 — Collapse the unstable BEM "optimized" path into one supported solver runtime

**Status:** OPEN
**Execution lane:** Reserved — Codex `high`; Opus `high`

- Tritonia-M shows the current optimized solver is not a trustworthy default. The ATH import and canonical mesh preparation succeed, but a 1 kHz / 1-frequency `single` solve still times out after 90 seconds even when forced to `tol=1e-3` and `use_strong_form=False`, while `double` fails immediately on Apple M1 Max OpenCL with a kernel creation error.
- The current stack exposes too much unstable behavior across API/UI/runtime boundaries: duplicate `solve.py` vs `solve_optimized.py` paths, public `useOptimized` branching, advanced settings for unsupported precision/device combinations, `workers` accepted but unused by the public sweep, OpenCL auto-selection and retry heuristics, optional warm-up, runtime-capability reporting that describes unstable knobs instead of support, and heavier-than-needed directivity post-processing.

Implementation notes:

- Backend solver/runtime: `server/solver/solve_optimized.py`, `server/solver/solve.py`, `server/solver/device_interface.py`, `server/services/solver_runtime.py`, `server/services/simulation_runner.py`, `server/contracts/__init__.py`, `server/README.md`
- Frontend/API surface: `src/solver/index.js`, `src/ui/settings/modal.js`, `src/ui/settings/simAdvancedSettings.js`, `src/ui/runtimeCapabilities.js`, `src/ui/simulation/jobActions.js`, `src/ui/simulation/results.js`
- Coverage/docs to update: `server/tests/test_solver_hardening.py`, `server/tests/test_api_validation.py`, `tests/sim-advanced-settings.test.js`, `tests/simulation-module.test.js`, `docs/PROJECT_DOCUMENTATION.md`, `docs/architecture.md`

Action plan:

- [ ] Add a bounded Tritonia-M benchmark/repro path (1-frequency and reduced sweep) that reports mesh-prep success, selected runtime/device, solver stage timings, and supported vs unsupported precision modes on the active host.
- [ ] Collapse the public BEM entrypoint to one stable solver path; remove `useOptimized` from the public contract and UI, and internalize any remaining legacy-vs-optimized differences unless a retained second path has a clear, tested user-facing contract.
- [ ] Remove or hide unsupported public knobs first: disable `double` on unsupported OpenCL GPU runtimes, stop advertising `workers` until the public sweep actually uses them, and remove or hard-disable `enableWarmup`, `bemPrecision`, and likely `deviceMode` if they remain solver-internal implementation choices rather than supported user-facing controls.
- [ ] Replace the current auto OpenCL heuristics with a conservative supported-runtime policy; keep runtime fallback only when there is a real validated fallback, not speculative GPU/CPU aliasing behavior, and update runtime-capability reporting so it reports supported vs unsupported configurations instead of exposing unstable runtime strategy.
- [ ] Simplify solver numerics to explicit, minimal defaults: no automatic strong-form enablement, no platform-specific "smart" behavior without proof, and no mixed-precision operator assembly.
- [ ] Reduce simulation runner orchestration and stage reporting to the core phases required for a stable solve, then trim result packaging and post-processing to the minimum contract the current UI actually needs; defer extra DI refinement, diagonal-plane packaging, and metadata-heavy summaries into follow-up slices if they remain valuable after the solver path is stable.
- [ ] Update the backend/frontend contract, runtime guidance, and tests to match the reduced solver surface, then rerun `npm test` and `npm run test:server`.

Add new execution slices here when a deferred watchpoint is activated or a new verified issue is opened.

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
