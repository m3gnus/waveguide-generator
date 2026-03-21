# Backlog Reorganization Archive

Created: March 19, 2026

This document stores the resolved, shipped, superseded, and audit-history sections that were removed from `docs/backlog.md` during the March 19, 2026 backlog cleanup. `docs/backlog.md` now contains active unfinished work only.

## Completed on March 19, 2026

### P1. Rebuild Output Workspace Contract and Fix Firefox Server-Folder Regression

- Unified workspace/export writes around backend-managed workspace APIs plus optional direct-write File System Access behavior on supporting browsers.
- Moved manifests, script snapshots, raw results, mesh artifacts, and selected exports into deterministic generation folders with `waveguide.project.v1.json` as the user-facing artifact index.
- Added regression coverage for backend workspace path/open/export routes and frontend workspace-subdirectory export routing.

### P1. Restrict Scale to Waveguide Geometry Only

- Removed enclosure-only fields from upstream `scale` application in the JS geometry pipeline.
- Kept OCC/export mesh resolution fields scaling with geometry as explicit element-size semantics in `prepareOccSimulationParams(...)`.
- Removed formula support for `scale`, updated UI copy, and added regression coverage for geometry/design/param-panel behavior.

### P2. Audit Dependencies and Add Cross-Platform Runtime Doctor

- Removed dead dependencies (`jszip`, `trimesh`, `express`) and replaced the frontend dev server dependency with built-in Node code.
- Enforced a shared backend interpreter-selection contract across install/setup, `npm start`, and backend startup, including repo marker `.waveguide/backend-python.path`.
- Added backend preflight/doctor commands, `/health` doctor payload exposure, frontend dependency-status rendering, and blocked-feature guidance for gmsh, bempp-cl, OpenCL runtime, and matplotlib.

### P2. Finish Single-Precision Default Alignment Across UI and Directivity Helpers

- Changed recommended BEM precision defaults and UI guidance to single precision.
- Aligned `server/solver/directivity_correct.py` helper defaults with the maintained single-precision runtime contract.
- Added coverage for UI defaults/copy and preserved both single and double precision lanes.

### P2. Enrich Simulation Results Metadata and Add Fast Directivity Re-render

- Persisted solve timestamps plus effective directivity-map metadata in results so the View Results modal can render solve-time settings directly from saved output.
- Added a fast post-solve directivity heatmap re-render path through `/api/render-directivity` for display-only reference-level changes.
- Added frontend/backend regression coverage for metadata persistence, results summary content, and re-render behavior.

## Completed on March 18, 2026

### P3. Document Symmetry Solver Investigation

- Wrote `docs/symmetry-investigation.md` covering the image-source experiments, measured error, root cause, and future paths.

### P2. Remove All Symmetry Solver Code

- Removed the inactive symmetry runtime path from backend, frontend, tests, scripts, and backlog baseline references.

### P2. BEM Single Precision Solver Failure

- Fixed precision-aware complex dtypes in the solver path so single precision works correctly.
- Added regression coverage and changed active backend precision normalization to default to single.

### P4. UI Redesign Polish

- Shifted the accent hue away from the earlier purple-blue palette.
- Reduced status-glow intensity.
- Added button `:active` feedback.

### P4. UI Redesign Anti-Pattern Audit

- Audit completed with anti-pattern score `2/10`.

## Completed / Resolved Milestones Moved Out of the Active Backlog

### March 18, 2026

- P3. Settings Modal: replace `?` help buttons with label hover tooltips
- P2. Design Quality: distinctive visual identity and hierarchy

### March 17, 2026

- P2. UI Quality Audit: accessibility and theming fixes
- P3. UI Quality Audit: medium-severity fixes
- P4. UI Quality Audit: low-severity fixes
- P3. Replace symmetry policy with solve statistics in results view
- P3. Remove symmetry text from job list entries
- P2. Restore missing load/export buttons in job list
- P1. Duplicate function definitions in `scene.js`
- P2. Observation distance measurement origin
- P2. Solver settings audit
- P2. OpenCL GPU support
- P2. Measurement distance
- P2. Tessellation architecture
- P3. Remove simulation jobs refresh button
- P3. Symmetry runtime truth
- P4. Maintained markdown overhaul

### March 14-15, 2026

- P0. Enclosure BEM simulation
- P2. Safety mechanisms in `.msh` generation

### March 11-15, 2026 backlog execution log items

- P1. Remove stale local-only jobs from the backend feed
- P1. OCC mesh diagnostics must reflect the backend solve mesh
- P1. Parameter inventory, naming, hover help, and ordering
- P1. Settings panel completeness and information architecture
- P1. MSH file import: viewport display and simulation workflow
- P1. Return to parametric: viewport blank + MSH import naming
- P1. Enclosure mesh resolution: edge over-refinement
- P2. Help tooltip: move from button to label hover
- P2. Folder workspace discoverability and export routing
- P2. Geometry diagnostics instead of numeric BEM tag diagnostics
- P2. Advanced solver controls and BEM precision scope
- P3. Directivity map section: add expand/collapse
- P3. Pre-existing test failures: 12 failures fixed
- P3. Simulation job feed source-badge cleanup

Detailed execution history for those March 11-15 slices remains in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Shipped Milestones That Now Have Broader Follow-Up Work

### P2. Firefox Output Folder

- Shipped:
  - `GET /api/workspace/path`
  - `POST /api/workspace/open`
  - Firefox settings-panel path display and "Open in Finder" affordance
- Follow-up still open:
  - End-to-end export/write contract is still inconsistent and is now tracked by active backlog item `P1. Rebuild Output Workspace Contract and Fix Firefox Server-Folder Regression`

### P1. Symmetry Performance

- Investigation complete.
- Runtime path first became disabled due to the bempp-cl limitation and was later fully removed from the active codebase.

## Deferred Watchpoints Archived on March 21, 2026

The following 4 watchpoint items were moved to archive as they remain valid future work but are not in the current critical path:

### Replace Gmsh-Centric Export Coupling

**Status:** DEFERRED — wait for solve-mesh and export-artifact parity work
**Execution lane:** Reserved — Codex `high`; Opus `high`

The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.

### Internationalization (i18n) Infrastructure

**Status:** DEFERRED — large scope, not blocking current release
**Execution lane:** Reserved — Codex `high`; Opus `high`

Entire frontend still uses hard-coded English strings. Activate this only when localization becomes a release requirement.

Action plan when activated:
- [ ] Decide on i18n approach (library vs. message-file extraction)
- [ ] Extract UI strings into a message catalog
- [ ] Implement formatting/pluralization support and regression coverage

### Decompose `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py`

**Status:** DEFERRED — only activate when feature work makes file size a delivery bottleneck
**Execution lane:** Reserved — Codex `medium-high`; Opus `medium-high`

Keep this deferred unless new work is slowed down by those files' size and coupling.

### Decompose `server/services/job_runtime.py`

**Status:** DEFERRED — only activate when queueing/persistence lifecycle requirements expand
**Execution lane:** GLM-5 suitable for bounded prep slices; reserve Codex/Opus for the full refactor

Keep this deferred unless queueing, persistence, or multi-worker lifecycle requirements materially expand.

## Deferred Notes Removed from the Active File

These notes were trimmed or folded into newer backlog items during the cleanup:

- Cross-platform installation hardening: superseded by active item `P2. Audit Dependencies and Add Cross-Platform Runtime Doctor`
- Generic UI audit scorecards and positive-findings blocks: kept here as historical context, not active work

## Historical Audit Snapshot

### Audit Summary (March 18, 2026)

| Metric | Count |
| --- | --- |
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |
| Total | 0 |

### Overall Quality Snapshot

- Overall quality score: `A (96/100)`
- Accessibility: `A- (90/100)`
- Performance: `A- (88/100)`
- Theming: `A (92/100)`
- Responsive: `B+ (88/100)`
- Code quality: `A (92/100)`
- Design identity: `A- (90/100)`

### Positive findings captured from that audit

- Strong OKLCH design-token organization
- Proper dark mode via `prefers-color-scheme`
- Reduced-motion support present
- Semantic HTML with ARIA support
- Accessible progress, toast, and skip-link affordances
- Three.js render optimization with `needsRender`
- Distinctive typography and non-generic empty/loading/error states
