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
- The active execution backlog is empty. Re-open this file only when a new product/runtime requirement lands or a deferred watchpoint becomes an active bottleneck.

Remaining work:
- Keep diagnostics, regression coverage, and documentation current as new changes land.
- Convert new requirements into the smallest coherent backlog slices instead of rebuilding a long completed log here.

## Recommended Execution Order

When new work lands, continue to work the backlog from upstream runtime truth to downstream UX:

1. Runtime job lifecycle and solve-request contract correctness.
2. Canonical solve diagnostics and regression lanes that lock those contracts in.
3. Simulation UI parity work that depends on trustworthy runtime state.
4. Folder-backed export/task-history flows that build on stable simulation metadata.
5. Ratings/filtering, docs cleanup, and optional research tracks.

## Active Backlog

There are no scheduled implementation items right now.

When the backlog re-opens, queue this UX/runtime follow-up near the simulation UI slice:
- Expose the `enable_symmetry` on/off control in the settings menu so users can explicitly disable automatic symmetry reduction for troubleshooting and A/B comparisons between full-model and reduced-model solves.

Re-open the backlog when:
- a new product or runtime requirement lands
- a deferred watchpoint becomes an active delivery bottleneck
- a regression or documentation drift needs tracked follow-through across multiple slices

## Deferred Watchpoints

- Symmetry-policy controls remain read-only unless new benchmark data or user requirements justify explicit override controls.
- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

## Historical Notes

The detailed March 11-12, 2026 execution record, including the completed P0-P4 slices and their rationale, has been archived in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.
