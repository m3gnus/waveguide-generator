# Future Additions

This document tracks planned or partially implemented work.

Implemented runtime behavior belongs in:
- `docs/PROJECT_DOCUMENTATION.md`
- `docs/ABEC_PARITY_CONTRACT.md`

## Open Items

### 1. Frontend solver status messaging cleanup

Current state:
- `src/solver/index.js` still logs `Using MOCK solver ... Real BEM integration pending` in `mockBEMSolver(...)`.
- Backend solve integration is already live in `BemSolver.submitSimulation(...)`.

Future addition:
- Update solver messaging so mock mode is clearly labeled as optional fallback, not primary runtime status.

### 2. OCC interface/subdomain geometry in `/api/mesh/build`

Current state:
- `subdomain_slices`, `interface_offset`, `interface_draw`, and `interface_resolution` are accepted in request payloads.
- OCC builder currently does not use those fields to generate interface/subdomain geometry.

Future addition:
- Implement OCC interface/subdomain surface generation and map the result into explicit physical groups.

### 3. Symmetry benchmark harness

Current state:
- Symmetry reduction is implemented in the optimized solver path.
- `/api/solve` now receives full-domain frontend payloads (`quadrants=1234`) and delegates half/quarter reduction to backend symmetry detection + reduction.
- Repository does not yet include a benchmark harness with committed thresholds for full vs half vs quarter domain performance/error.

Future addition:
- Add repeatable benchmark cases, runtime/error baselines, and pass/fail thresholds to CI-facing docs/tests.
- Add explicit solver-facing symmetry policy controls (for example: `auto`, `force_full`) with validation so unsupported reductions fail loudly instead of silently producing inconsistent behavior.
- Surface solver symmetry decisions and rejection reasons in UI metadata (detected type, reduction factor, centered-excitation check result) so users can verify when quarter/half acceleration is actually active.

### 4. Axisymmetric fast path (scaffold only)

Current state:
- Eligibility checks and adapter scaffold exist (`server/solver/axisymmetric.py`).
- Production solver remains 3D `bempp-cl`; axisymmetric compute path is not enabled.

Future additions:
- Implement an axisymmetric solver adapter behind a feature flag.
- Validate numerical error and runtime against the 3D baseline on canonical cases.
- Make a go/no-go decision for production enablement after benchmarks.

### 5. ABEC parity expansion (optional)

Current state:
- Required structure and semantics are enforced by `src/export/abecBundleValidator.js`.
- Golden parity coverage exists for `ABEC_FreeStanding` and `ABEC_InfiniteBaffle`.

Future additions:
- Add stricter value-range checks (not only structural checks) where ATH references are stable.
- Add additional ATH reference bundles when available.

### 6. Potential deprecation: ABEC export and Gmsh meshing stack

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

### 7. Clarify BEM mesh controls in UI/docs

Current state:
- Live BEM solve mesh is generated from the canonical frontend payload path.
- `throatResolution`/`mouthResolution` influence slice distribution in the JS geometry path.
- `encFrontResolution`/`encBackResolution`/`rearResolution` are visible in UI but do not all affect the live BEM solve mesh path in the same way.

Future additions:
- Update parameter labels/tooltips to clearly distinguish:
- controls that affect live BEM solve mesh,
- controls that are export-specific or legacy-path specific.
- Add a short “mesh-control matrix” section to `README.md` and `docs/PROJECT_DOCUMENTATION.md`.

### 8. Explicit simulation mesh mode in UI

Current state:
- Solve submission always sends canonical mesh payload to `/api/solve`.
- Optional backend Gmsh refinement exists behind `options.use_gmsh`, but mode visibility in UI is limited.

Future addition:
- Add an explicit mesh mode control/status in the simulation panel:
- canonical mesh only, or
- canonical mesh + backend Gmsh refinement.
- Show selected mode in run status/progress messaging.

### 9. Pre-submit canonical tag diagnostics

Current state:
- Tag validity is enforced in frontend and backend, and solve fails when source tag coverage is missing.
- Users do not currently get a concise pre-submit tag summary in simulation UI.

Future addition:
- Add a pre-submit diagnostics panel with tag counts (`1/2/3/4`) and a clear warning when source-tagged elements are absent.
- Include lightweight checks for common payload issues (triangle/tag length mismatch, missing boundary metadata).

### 10. Remove stale mock/pending wording in solver UX

Current state:
- Some messaging/log text still implies mock mode is primary or that real BEM integration is pending.
- Runtime behavior already uses backend BEM solver when available.

Future addition:
- Normalize simulation UI/log strings so real backend BEM is presented as default behavior and mock mode as fallback only.

### 11. Add no-Gmsh regression lane for solve path

Current state:
- `/api/solve` can run without Gmsh in default canonical-payload mode.
- Test coverage verifies tag contracts but does not explicitly enforce a “Gmsh unavailable” solve-path lane in CI.

Future addition:
- Add a server test lane/config that simulates Gmsh-unavailable runtime and verifies solve-path readiness and error behavior remain correct.
- Keep this lane required while ABEC/Gmsh deprecation decisions are pending.

### 12. BEM Solver Acceleration Roadmap

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

### 13. Remaining Architecture Audit

- [ ] Audit the JavaScript mesh engine (`buildWaveguideMesh.js`) for further simplifications now that it's decoupled from ABEC export requirements.
