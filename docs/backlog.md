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

New requirement landed: symmetry behavior now needs explicit runtime hardening and user-facing controls because imported ATH reference cases do not all take the same reduction path.

Research grounding for the next slices:
- `../misc/waveguides from ATH/250917asro68.txt` (`R-OSSE`, freestanding wall shell) currently stays full-model once `wallThickness > 0`, but the same horn without the wall shell qualifies for quarter reduction.
- `../misc/waveguides from ATH/260308tritonia.txt` (`OSSE`, no enclosure) currently qualifies for quarter-domain reduction.
- `../misc/waveguides from ATH/260308Tritonia-M.txt` (`OSSE`, enclosure spacing `25,25,25,300`) currently qualifies only for half-domain reduction because the enclosure remains left/right symmetric but not top/bottom symmetric.
- The settings modal code already contains an `Enable Symmetry` control; the gap is to verify/operator-surface it reliably and to make the runtime behavior around it trustworthy.

### P1. Symmetry Runtime Truth For Reference Configs

- Add a reproducible diagnostics lane for the ATH reference configs above that captures imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
- Add regression coverage for those cases so future geometry or solver changes cannot silently change reduction eligibility:
  - `asro68` with `wallThickness = 0` should document its current quarter-domain eligibility.
  - `asro68` with the imported wall shell should document its current full-model fallback.
  - `260308tritonia` should document its current quarter-domain reduction.
  - `260308Tritonia-M` should document its current half-domain reduction and the enclosure asymmetry that prevents quarter reduction.
- After the reference expectations are locked in, decide case-by-case whether wall-shell/enclosure symmetry needs algorithm fixes or stricter eligibility rules. Prefer conservative full-model fallback over unsafe false-positive reductions.

### P2. Simulation UI And Operator Control Parity

- Audit the existing `Enable Symmetry` control in the Settings modal and verify that it is visible in the live modal, persists correctly, and changes submitted `/api/solve` payloads as expected.
- Add UI coverage for the Simulation Basic section that specifically asserts the presence and persistence of `enableSymmetry`; current modal smoke coverage only checks section labels.
- Surface the requested symmetry setting and the resulting `symmetry_policy` together in user-visible job/result surfaces so users can tell whether a run kept the full model because symmetry was disabled, rejected, or successfully applied.

### P3. Documentation And Contract Follow-Through

- Update runtime docs to clarify that imported ATH `Mesh.Quadrants` values do not directly trim the canonical simulation payload; full-model vs reduced-model behavior is determined by the solver symmetry policy.
- Document the current reference-config symmetry expectations for `asro68`, `tritonia`, and `Tritonia-M` so future investigations have a stable baseline.
- Reconcile the old “symmetry-policy controls remain read-only” watchpoint with the new requirement for explicit operator control and troubleshooting guidance.

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
