# Future Additions

Last updated: February 25, 2026

This document tracks roadmap work by status.

Implemented runtime behavior belongs in:
- `docs/PROJECT_DOCUMENTATION.md`

## Backlog

### Script snapshot compatibility hardening
Current state:
- Simulation tasks can store/load parameter snapshots (`Load Script`) from feed entries.
- Snapshot schema is versionless and assumes local UI field compatibility.

Next addition (lightweight approach recommended):
- Add a `schemaVersion` integer field to each snapshot on save; bump it whenever params are renamed or removed.
- On load, if the stored version doesn't match the current version, show a one-line warning in the feed entry (e.g. "Script saved with an older schema — some fields may not apply").
- No migration logic needed: unknown keys already silently no-op on load, which prevents hard failures. The warning covers the real risk (user confusion when a renamed field is silently dropped).

### Pre-submit canonical tag diagnostics
Current state:
- Tag validity is enforced in frontend/backend; solve fails when source tag coverage is missing.
- Simulation UI does not show concise pre-submit tag summary.

Next addition:
- Add pre-submit diagnostics for tag counts (`1/2/3/4`) and warnings for missing source-tagged elements.
- Add lightweight checks for triangle/tag length mismatch and missing boundary metadata.

### Simulation management enhancements
Current state:
- Multi-job management is implemented with backend job persistence and frontend session recovery.
- Simulation task feed supports queued/running/history workflows with manual cleanup actions.

Next additions:
- Add configurable retention/cleanup policies for historical and failed tasks.
- Add richer feed filtering/grouping controls for larger job histories.
- Add lightweight run labels/annotations to improve traceability across repeated experiments.

### Symmetry benchmark harness and policy visibility
Current state:
- Symmetry reduction exists in optimized solver path.
- `quadrants` is retained for legacy/import compatibility; frontend viewport and canonical mesh generation no longer use it to trim geometry.
- `/api/solve` may still receive `waveguide_params.quadrants` from imported configs, but backend coerces OCC-adaptive solve builds to full domain before meshing.
- No committed benchmark harness with full/half/quarter thresholds.

Next additions:
- Add repeatable benchmark cases, error/runtime baselines, and pass/fail thresholds.
- Add explicit solver symmetry policy controls (for example `auto`, `force_full`) with validation.
- Surface solver symmetry decisions/rejection reasons in UI metadata (detected type, reduction factor, centered-excitation check).

### Clarify BEM mesh controls in UI and docs
Current state:
- Live solves default to backend OCC-adaptive meshing (`options.mesh.strategy="occ_adaptive"` with `waveguide_params`).
- Frontend still sends canonical mesh arrays for contract/validation.
- Resolution controls are not all clearly explained as solve-mesh vs export-mesh behavior.

Next additions:
- Update labels/tooltips to distinguish live-solve controls from export/legacy controls.
- Add a mesh-control matrix to `README.md` and `docs/PROJECT_DOCUMENTATION.md`.

### No-Gmsh regression lane for solve path
Current state:
- `/api/solve` can run without Gmsh in canonical-payload mode.
- Existing tests verify tag contracts but do not explicitly enforce a Gmsh-unavailable solve lane.

Next addition:
- Add required server test lane/config simulating Gmsh-unavailable runtime and validating solve-path readiness/error behavior.

## Decision-Dependent / Optional Tracks

### Potential deprecation: Gmsh meshing stack
Current state:
- `/api/mesh/build` and `/api/mesh/generate-msh` are Gmsh-backed meshing paths used for MSH/STL export.
- `/api/solve` can run from canonical frontend mesh payloads without requiring Gmsh.
- ABEC export pipeline has been removed (Feb 2026).

Decision framework:
- Audit remaining Gmsh touchpoints (MSH export, STL export via OCC path).
- Evaluate whether JS-based mesher can reach parity for remaining export needs.
- If Gmsh can be fully replaced, remove from dependency matrix, scripts, and requirements.

### Code sanitization and dead-code removal
Current state:
- Codebase still contains fallback paths (for example `mockBEMSolver`) and utility paths not fully wired to UI.

Next additions:
- Remove mock/pending fallback code once backend BEM integration hardening is complete.
- Run structured dead-code audit in `src/` for functions with no runtime UI path.

## Completed (Implemented)

### BEM solver acceleration
- **Implemented (Feb 2026)**: Strong-form GMRES enabled via `use_strong_form=True` (inverse mass matrix preconditioner). Reduces GMRES iteration count per frequency.
- `return_iteration_count=True` surfaces per-frequency iteration counts; both kwargs are feature-detected at import time via `inspect.signature` so the solver degrades cleanly on legacy `bempp_api` runtimes without a try/except fallback per frequency.
- Warm-up pass before frequency loop front-loads JIT/OpenCL kernel compilation costs. Controllable via `enable_warmup` parameter; benchmark CLI exposes `--no-warmup` for A/B measurement.
- Performance metadata added to every `/api/solve` result: `warmup_time_seconds`, `gmres_iterations_per_frequency`, `avg_gmres_iterations`, `gmres_strong_form_supported`.
- Benchmark script: `server/scripts/benchmark_solver.py`.

### Documentation and agent stewardship
- **Implemented (Feb 2026)**: `AGENTS.md` now references the **BEM Solver Acceleration Roadmap** (FMM/OpenCL) for future automated refactors.

### Frontend solver UX messaging
- **Implemented (Feb 2026)**:
- `src/solver/index.js` warning text now makes clear that mock output is backend-unavailable fallback only.
- `src/ui/simulation/results.js` is now a thin data-caching stub (left-panel inline charts removed).
- Simulation UI/log wording now presents backend BEM as default behavior and mock output as fallback only.
- Backend solve integration remains the primary path in `BemSolver.submitSimulation(...)`.

### Architecture audit
- **Implemented (Audited Feb 22, 2026)**: JavaScript mesh engine audit completed for `buildWaveguideMesh.js`.
- Current runtime callers are viewport render, STL export, and simulation payload generation (`src/app/scene.js`, `src/app/exports.js`, `src/app/mesh.js`).
- `adaptivePhi` is effectively STL-only in current UI behavior; viewport and simulation disable it.
- Follow-up simplification candidates (not yet implemented):
- Replace spread-based max-index checks (`Math.max(...indices)`) with a linear scan helper to avoid large-array argument limits.
- Collapse rarely-used `collectGroups`/`groupInfo` branching if external callers do not depend on group suppression.
- Move adaptive-phi branch into a dedicated helper/export-only wrapper to reduce branching in canonical horn build path.
