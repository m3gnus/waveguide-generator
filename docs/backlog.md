# Backlog

Last updated: March 19, 2026 (archived completed March 19 slices; active file now contains unfinished work only)

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

No active unfinished release slices are currently tracked here.

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
