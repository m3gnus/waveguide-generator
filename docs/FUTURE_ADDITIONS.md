# Future Additions

Last updated: February 22, 2026 (ABEC removal complete, CSV export fixed)

This document tracks roadmap work by status.

Implemented runtime behavior belongs in:
- `docs/PROJECT_DOCUMENTATION.md`

## New Backlog (Simulation Feed UX, Feb 2026)

### Script snapshot compatibility hardening
Current state:
- Simulation tasks can store/load parameter snapshots (`Load Script`) from feed entries.
- Snapshot schema is versionless and assumes local UI field compatibility.

Next addition (lightweight approach recommended):
- Add a `schemaVersion` integer field to each snapshot on save; bump it whenever params are renamed or removed.
- On load, if the stored version doesn't match the current version, show a one-line warning in the feed entry (e.g. "Script saved with an older schema — some fields may not apply").
- No migration logic needed: unknown keys already silently no-op on load, which prevents hard failures. The warning covers the real risk (user confusion when a renamed field is silently dropped).

## Completed (Implemented)

### Documentation and agent stewardship
- **Implemented (Feb 2026)**: `AGENTS.md` now references the **BEM Solver Acceleration Roadmap** (FMM/OpenCL) for future automated refactors.

### Frontend solver UX messaging
- **Implemented (Feb 2026)**:
- `src/solver/index.js` warning text now makes clear that mock output is backend-unavailable fallback only.
- `src/ui/simulation/results.js` labels mock output as preview-only and backend BEM as default runtime.
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

## In Progress (Partially Implemented)

### BEM solver acceleration roadmap

Execution strategy for accelerating backend `/api/solve`:
1. Assembler policy: `auto|dense|fmm` with dense fallback.
2. Matrix-free iterative path using FMM-backed operators.
3. Device policy hardening for `auto|opencl_cpu|opencl_gpu|numba`.
4. CUDA optimization (deferred until post-FMM benchmarks).

Progress by phase:
- **Phase 0: Baseline and harness**: pending.
- **Phase 1: FMM integration**: pending.
- **Phase 2: Matrix-free policy**: pending.
- **Phase 3: Device policy hardening**: partially implemented (Feb 2026).

Implemented in Phase 3:
- Added explicit modes `auto|opencl_cpu|opencl_gpu|numba`.
- `auto` mode now uses deterministic priority: `opencl_gpu -> opencl_cpu -> numba`.
- Added runtime fallback metadata to `/health` and solve result metadata.

Remaining for Phase 3:
- Expand hardware-class guidance for OpenCL GPU setup (especially Windows/Linux driver onboarding).
- Add regression/performance baselines per hardware class.

## Remaining Backlog

### High priority documentation maintenance
- Conduct periodic audits of `docs/PROJECT_DOCUMENTATION.md` to keep it aligned with unified-mesh and solver runtime refactors.

### Simulation management enhancements
Current state:
- Multi-job management is implemented with backend job persistence and frontend session recovery.
- Simulation task feed supports queued/running/history workflows with manual cleanup actions.

Next additions:
- Add configurable retention/cleanup policies for historical and failed tasks.
- Add richer feed filtering/grouping controls for larger job histories.
- Add lightweight run labels/annotations to improve traceability across repeated experiments.

### UI simplification and cleanup
Current state:
- Simulation results are partially displayed in the left panel, creating clutter.

Next additions:
- Move results out of left panel into dedicated workspace/modal.
- Tighten spacing and improve information density in `SimulationPanel.js` and `ParamPanel.js`.

### OCC interface/subdomain geometry in `/api/mesh/build`
Current state:
- `subdomain_slices`, `interface_offset`, `interface_draw`, and `interface_resolution` are accepted by request schema.
- OCC builder does not yet generate interface/subdomain geometry from these fields.

Next addition:
- Implement OCC interface/subdomain surface generation and explicit physical-group mapping.

### Symmetry benchmark harness and policy visibility
Current state:
- Symmetry reduction exists in optimized solver path.
- `/api/solve` receives full-domain frontend payload (`quadrants=1234`) and backend performs symmetry detection/reduction.
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

### Explicit simulation mesh mode in UI
Current state:
- Simulation panel defaults to adaptive OCC solve requests.
- Backend supports non-default/legacy mesh paths, but no explicit UI mesh-mode selector exists.

Next addition:
- Add mesh mode control/status in simulation panel:
- canonical mesh only, or
- canonical mesh + backend Gmsh refinement.
- Display selected mode in run status/progress messages.

### Pre-submit canonical tag diagnostics
Current state:
- Tag validity is enforced in frontend/backend; solve fails when source tag coverage is missing.
- Simulation UI does not show concise pre-submit tag summary.

Next addition:
- Add pre-submit diagnostics for tag counts (`1/2/3/4`) and warnings for missing source-tagged elements.
- Add lightweight checks for triangle/tag length mismatch and missing boundary metadata.

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

## Deferred Removal Candidate

### Code sanitization and dead-code removal
Current state:
- Codebase still contains fallback paths (for example `mockBEMSolver`) and utility paths not fully wired to UI.

Next additions:
- Remove mock/pending fallback code once backend BEM integration hardening is complete.
- Run structured dead-code audit in `src/` for functions with no runtime UI path.
