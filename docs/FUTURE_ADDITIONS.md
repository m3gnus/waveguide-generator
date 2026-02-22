# Future Additions

This document tracks planned or partially implemented work.

Implemented runtime behavior belongs in:
- `docs/PROJECT_DOCUMENTATION.md`
- `docs/ABEC_PARITY_CONTRACT.md`

## Critical Evaluation & Research Items (High Priority)

### Documentation & Agent Stewardship
- **Status (Implemented Feb 2026)**: `AGENTS.md` now references the **BEM Solver Acceleration Roadmap** (FMM/OpenCL) for future automated refactors.
- **Product Docs**: Conduct periodic audits of `PROJECT_DOCUMENTATION.md` to ensure it matches recent unified-mesh refactors (Feb 2026).

## Open Items

### 1. Enhanced Simulation Management and Persistence

Current state:
- `stopSimulation` in `src/ui/simulation/actions.js` attempts to hit `/api/stop/${jobId}` but multi-simulation logic is missing.
- Simulations are lost if the window is closed; no persistent tracking exists.

Future additions:
- **Multiple Simulations**: Add support for queuing simulations (sequential or concurrent) with limit settings.
- **Persistence Layer**: Implement a "Simulation Manager" service that reads/writes job metadata to persistent storage (local or backend DB).
- **Session Recovery**: Allow users to retrieve and view results for both ongoing and past simulations even after a page reload.

### 1. UI Simplification and Cleanup

Current state:
- Simulation results are partially displayed in the left panel, creating clutter.
- UI elements for unsupported features are still visible. Like circsym and interface. 

Future additions:
- **Result Panel Refactor**: Remove the simulation result view from the left panel; transition to a dedicated workspace or modal.
- **Condense Layout**: Audit `SimulationPanel.js` and `ParamPanel.js` to tighten spacing and improve information density.

### 1. Deprecate and Remove Unsupported Features

Current state:
- `circsym` and interface functions (`interface_offset`, `interface_draw`) exist in `src/solver/waveguidePayload.js` and UI schemas but are not officially supported.
- ABEC project export is legacy and no longer required.

Future additions:
- **Delete Features**: Scrub the codebase for `circsym`, `interface_offset`, and `interface_draw`.
- **Remove ABEC Export**: Delete `src/export/abecProject.js` and remove the "Export ABEC" button from the UI.

### 1. Code Sanitization and Dead Code Removal

Current state:
- The codebase contains fallback paths like `mockBEMSolver` and several utility functions that aren't hooked up to the UI.

Future additions:
- **Delete Fallbacks**: Remove mock solvers and "pending" placeholders once real BEM integration is fully stabilized.
- **Code Audit**: Systematically remove dead code and old features. Scan for functions in the `src/` directory that lack UI representation.

### 1. Frontend solver status messaging cleanup

- **Status (Implemented Feb 2026)**:
- `src/solver/index.js` mock warning now states backend-unavailable fallback behavior (non-physics/debug-only).
- `src/ui/simulation/results.js` fallback panel text now labels mock output as preview-only and backend BEM as default runtime.
- Backend solve integration remains the primary path in `BemSolver.submitSimulation(...)`.

### 1. OCC interface/subdomain geometry in `/api/mesh/build`

Current state:
- `subdomain_slices`, `interface_offset`, `interface_draw`, and `interface_resolution` are accepted in request payloads.
- OCC builder currently does not use those fields to generate interface/subdomain geometry.

Future addition:
- Implement OCC interface/subdomain surface generation and map the result into explicit physical groups.

### 1. Symmetry benchmark harness

Current state:
- Symmetry reduction is implemented in the optimized solver path.
- `/api/solve` now receives full-domain frontend payloads (`quadrants=1234`) and delegates half/quarter reduction to backend symmetry detection + reduction.
- Repository does not yet include a benchmark harness with committed thresholds for full vs half vs quarter domain performance/error.

Future addition:
- Add repeatable benchmark cases, runtime/error baselines, and pass/fail thresholds to CI-facing docs/tests.
- Add explicit solver-facing symmetry policy controls (for example: `auto`, `force_full`) with validation so unsupported reductions fail loudly instead of silently producing inconsistent behavior.
- Surface solver symmetry decisions and rejection reasons in UI metadata (detected type, reduction factor, centered-excitation check result) so users can verify when quarter/half acceleration is actually active.

### 1. ABEC parity expansion (optional)

Current state:
- Required structure and semantics are enforced by `src/export/abecBundleValidator.js`.
- Golden parity validation is enforced via `npm run test:abec`.
- Obsolete JS suites `tests/abec-bundle-parity.test.js` and `tests/abec-circsym.test.js` were removed.

Future additions:
- Add stricter value-range checks (not only structural checks) where ATH references are stable.
- Add additional ATH reference bundles when available.

### 1. Potential deprecation: ABEC export and Gmsh meshing stack

Current state:
- ABEC export is a supported user-facing workflow and currently depends on `POST /api/mesh/build`.
- `/api/mesh/build` and `/api/mesh/generate-msh` are Gmsh-backed meshing paths.
- BEM solve (`/api/solve`) can run from canonical frontend mesh payloads without requiring Gmsh.

Implementation plan (go/no-go):

1. Discovery and impact audit
- Inventory all ABEC and Gmsh touchpoints in frontend, backend, install scripts, docs, and tests.
- Add temporary runtime instrumentation to measure real usage of:
- `exportABECProject` and ABEC bundle generation.
- `/api/mesh/build` and `/api/mesh/generate-msh`.
- `/api/solve` with `use_gmsh=true`.
- Define a decision window and explicit go/no-go thresholds (for example: low usage for N releases).

2. Prepare opt-out controls before removal
- Add feature flags to disable ABEC export and Gmsh meshing paths without deleting code.
- Hide or disable ABEC UI actions when the ABEC flag is off.
- Return clear `410/503` style API errors with migration guidance when Gmsh endpoints are disabled.
- Keep `/api/solve` working through canonical payloads as the baseline path.

3. Migration and parity safeguards
- Ensure remaining exports and simulation flows do not rely on ABEC artifacts (`bem_mesh.geo`, ABEC text files).
- Add/expand tests proving BEM simulation works end-to-end without ABEC or Gmsh endpoints.
- Add compatibility notes for users who still need ABEC:
- Pin to last ABEC-capable version, or
- Maintain a separate optional plugin/package for ABEC + Gmsh tooling.

4. Removal phase (only after go decision)
- Frontend:
- Remove ABEC export UI/actions and unused ABEC export modules.
- Remove frontend calls to `/api/mesh/build` and `/api/mesh/generate-msh` for production flows.
- Backend:
- Remove `/api/mesh/build` and `/api/mesh/generate-msh` routes and related builders.
- Remove Gmsh runtime checks and optional solve refinement path if policy is "no Gmsh anywhere".
- Packaging/runtime:
- Drop `gmsh` from dependency matrix, requirements, install/startup scripts, and health payload.
- Clean up CI jobs and tests that only validate ABEC/Gmsh behavior.

5. Documentation and contract cleanup
- Update `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `server/README.md`, and API docs to remove ABEC/Gmsh claims.
- Archive `docs/ABEC_PARITY_CONTRACT.md` as historical, or replace with a short deprecation notice.
- Update AGENTS guidance to reflect the new supported surface area.

6. Acceptance criteria
- `npm test` and `npm run test:server` pass with ABEC/Gmsh removed or fully disabled.
- No UI element references ABEC export.
- No backend route exposes Gmsh meshing APIs in the final removal state.
- BEM solver support matrix and health endpoint output match the new runtime.

7. Rollback strategy
- Keep removal as a sequence of small commits (flags -> disable default -> code deletion).
- Tag the last ABEC+Gmsh-supported release for users with legacy workflows.
- If regressions appear, re-enable by feature flag first; avoid reintroducing deleted code in emergency patches.

### 1. Clarify BEM mesh controls in UI/docs

Current state:
- Live simulation submissions request backend OCC-adaptive meshing (`options.mesh.strategy="occ_adaptive"` with `waveguide_params`).
- Frontend still sends canonical mesh arrays as contract/validation payload, but solve-time meshing is backend-owned in the adaptive path.
- `throatResolution`/`mouthResolution`/`rearResolution`/`encFrontResolution`/`encBackResolution` are not all surfaced to users with clear "solve mesh vs export mesh" impact semantics.

Future additions:
- Update parameter labels/tooltips to clearly distinguish:
- controls that affect live BEM solve mesh,
- controls that are export-specific or legacy-path specific.
- Add a short “mesh-control matrix” section to `README.md` and `docs/PROJECT_DOCUMENTATION.md`.

### 1. Explicit simulation mesh mode in UI

Current state:
- Simulation panel currently submits adaptive OCC solve requests by default (`options.mesh.strategy="occ_adaptive"`).
- Backend still supports non-default/legacy mesh paths, but mesh strategy selection is not exposed as a first-class UI control.

Future addition:
- Add an explicit mesh mode control/status in the simulation panel:
- canonical mesh only, or
- canonical mesh + backend Gmsh refinement.
- Show selected mode in run status/progress messaging.

### 1. Pre-submit canonical tag diagnostics

Current state:
- Tag validity is enforced in frontend and backend, and solve fails when source tag coverage is missing.
- Users do not currently get a concise pre-submit tag summary in simulation UI.

Future addition:
- Add a pre-submit diagnostics panel with tag counts (`1/2/3/4`) and a clear warning when source-tagged elements are absent.
- Include lightweight checks for common payload issues (triangle/tag length mismatch, missing boundary metadata).

### 1. Remove stale mock/pending wording in solver UX

- **Status (Implemented Feb 2026)**:
- Simulation UI/log wording now presents backend BEM as default behavior and mock output as fallback only.

### 1. Add no-Gmsh regression lane for solve path

Current state:
- `/api/solve` can run without Gmsh in default canonical-payload mode.
- Test coverage verifies tag contracts but does not explicitly enforce a “Gmsh unavailable” solve-path lane in CI.

Future addition:
- Add a server test lane/config that simulates Gmsh-unavailable runtime and verifies solve-path readiness and error behavior remain correct.
- Keep this lane required while ABEC/Gmsh deprecation decisions are pending.

### 1. BEM Solver Acceleration Roadmap

This section defines the execution strategy for accelerating the backend BEM solve path (`/api/solve`).

#### Strategy (Ranked by Impact)
1. **Assembler Policy**: Add `auto|dense|fmm` policy with dense fallback.
2. **Matrix-Free Iterative**: Leverage FMM-backed operators.
3. **Device Policy**: Harden `auto|opencl_cpu|opencl_gpu|numba` selection for predictable behavior.
4. **CUDA Optimization**: Deferred until post-FMM benchmarks.

#### Execution Phases
- **Phase 0: Baseline & Harness**: Create reproducible benchmark harness and record current performance.
- **Phase 1: FMM Integration**: Add assembler policy plumbing and deterministic fallback.
- **Phase 2: Matrix-Free Policy**: Finalize `auto` threshold selection and capture iteration telemetry.
- **Phase 3: Device Policy Hardening**: Implement explicit device selection and OpenCL recovery.
  - **Status (Partially implemented Feb 2026)**:
    - Added explicit modes `auto|opencl_cpu|opencl_gpu|numba`.
    - `auto` selection now uses deterministic priority (`opencl_gpu -> opencl_cpu -> numba`) with no startup benchmark.
    - Added runtime fallback metadata to `/health` and result metadata.
  - **Remaining**:
    - Expand hardware-class guidance for OpenCL GPU setup (especially Windows/Linux driver onboarding).
    - Add regression/performance baselines per hardware class.

### 1. Remaining Architecture Audit

- [x] Audit the JavaScript mesh engine (`buildWaveguideMesh.js`) for further simplifications now that it's decoupled from ABEC export requirements.
  - **Status (Audited Feb 22, 2026)**:
  - Current UI runtime mesh callers are viewport render, STL export, and simulation payload generation (`src/app/scene.js`, `src/app/exports.js`, `src/app/mesh.js`).
  - `adaptivePhi` is effectively an STL-only path in current UI behavior; viewport and simulation explicitly disable it.
  - `buildWaveguideMesh` still carries some legacy flexibility that can be simplified without changing mesh contracts:
  - Replace spread-based max-index checks (`Math.max(...indices)`) with a linear scan helper to avoid large-array argument limits.
  - Collapse rarely-used `collectGroups`/`groupInfo` option branching if external callers are no longer depending on group suppression.
  - Move adaptive-phi branch into a dedicated helper (or dedicated export-only wrapper) to reduce branching in the canonical horn build path.
  - Remove stale ABEC wording in adaptive-phi comments to reflect current caller semantics.
