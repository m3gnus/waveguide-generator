# Future Additions

Last updated: February 22, 2026

This document tracks roadmap work by status.

Implemented runtime behavior belongs in:
- `docs/PROJECT_DOCUMENTATION.md`

## New Backlog (Simulation Feed UX, Feb 2026)

### Failed-task retention policy (decision pending)
Current state:
- UI now supports manual `Clear Failed` cleanup in the simulation task feed.
- Failed tasks are not auto-deleted on app/backend restart.

Next addition:
- Decide and implement lifecycle policy for failed tasks:
- auto-clear on reboot, or
- retain until explicit user cleanup.
- If auto-clear is chosen, add a user-facing toggle and document default behavior.

### Script snapshot compatibility hardening
Current state:
- Simulation tasks can store/load parameter snapshots (`Load Script`) from feed entries.
- Snapshot schema is versionless and assumes local UI field compatibility.

Next addition:
- Add schema versioning/migration guards for stored task scripts to avoid breakage across UI/config updates.
- Add explicit stale-script warnings when fields no longer map cleanly to current controls.

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
- Remove stale ABEC wording in adaptive-phi comments.

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

### Simulation management and persistence
Current state:
- `stopSimulation` in `src/ui/simulation/actions.js` calls `/api/stop/${jobId}`, but full multi-simulation management is not implemented.
- Simulation tracking is not persisted across browser reload/close.

Next additions:
- Add queued simulation support (sequential or concurrent) with configurable limits.
- Implement a simulation manager persistence layer (browser storage and/or backend DB).
- Add session recovery for ongoing and historical jobs/results.

### UI simplification and cleanup
Current state:
- Simulation results are partially displayed in the left panel, creating clutter.
- UI still exposes unsupported controls (for example `circsym`, interface).

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

### ABEC parity expansion (optional)
Current state:
- Required structure/semantics are enforced by `src/export/abecBundleValidator.js`.
- Golden parity validation is covered by `tests/export-gmsh-pipeline.test.js`.
- Obsolete suites `tests/abec-bundle-parity.test.js` and `tests/abec-circsym.test.js` are removed.

Potential additions:
- Add stricter value-range checks where ATH references are stable.
- Add additional ATH reference bundles when available.

### Potential deprecation: ABEC export and Gmsh meshing stack
Current state:
- ABEC export remains supported and depends on `POST /api/mesh/build`.
- `/api/mesh/build` and `/api/mesh/generate-msh` are Gmsh-backed meshing paths.
- `/api/solve` can run from canonical frontend mesh payloads without requiring Gmsh.

Decision framework:
1. Discovery and impact audit.
- Inventory ABEC/Gmsh touchpoints in frontend, backend, docs, tests, and install scripts.
- Add temporary instrumentation to measure usage of ABEC export, `/api/mesh/build`, `/api/mesh/generate-msh`, and `/api/solve` with `use_gmsh=true`.
- Define go/no-go threshold window (for example low usage over N releases).
2. Prepare opt-out controls before removal.
- Add feature flags for ABEC export and Gmsh meshing paths.
- Hide ABEC UI actions when flag is off.
- Return clear `410/503`-style API responses with migration guidance when endpoints are disabled.
- Keep canonical `/api/solve` path as baseline.
3. Migration and parity safeguards.
- Ensure remaining workflows do not require ABEC artifacts (`bem_mesh.geo`, ABEC text files).
- Add/expand end-to-end tests proving simulation without ABEC/Gmsh endpoints.
- Provide compatibility guidance for ABEC-dependent users (pin last supported release or separate plugin/package).
4. Removal phase (only after go decision).
- Frontend: remove ABEC export UI/actions and unused ABEC modules; remove production calls to meshing endpoints.
- Backend: remove `/api/mesh/build` and `/api/mesh/generate-msh` routes/builders; remove Gmsh runtime checks if policy is no-Gmsh.
- Packaging/runtime: remove `gmsh` from dependency matrix, scripts, requirements, and health payload.
- CI: remove ABEC/Gmsh-only jobs/tests.
5. Documentation and contract cleanup.
- Update `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `server/README.md`, and API docs.
- Update AGENTS guidance to supported runtime surface.
6. Acceptance criteria.
- `npm test` and `npm run test:server` pass with ABEC/Gmsh removed or fully disabled.
- UI contains no ABEC export controls.
- Backend exposes no Gmsh meshing routes in final removal state.
- Solver support matrix and health output match runtime reality.
7. Rollback strategy.
- Use staged commits (`flags -> default-off -> deletion`).
- Tag last ABEC+Gmsh-capable release.
- Prefer feature-flag re-enable for regressions instead of emergency code resurrection.

## Deferred Removal Candidate

### Deprecated/unsupported frontend feature cleanup
Current state:
- `circsym` and interface functions (`interface_offset`, `interface_draw`) exist in `src/solver/waveguidePayload.js` and UI schema, but are not officially supported.
- ABEC export deprecation has been proposed but is not approved as of February 22, 2026.

Next additions:
- Remove unsupported payload/UI paths for `circsym`, `interface_offset`, and `interface_draw` when compatibility window closes.
- If ABEC deprecation receives a go decision, remove `src/export/abecProject.js` and related UI controls as part of the staged deprecation plan.

### Code sanitization and dead-code removal
Current state:
- Codebase still contains fallback paths (for example `mockBEMSolver`) and utility paths not fully wired to UI.

Next additions:
- Remove mock/pending fallback code once backend BEM integration hardening is complete.
- Run structured dead-code audit in `src/` for functions with no runtime UI path.
