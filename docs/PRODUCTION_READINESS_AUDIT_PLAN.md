# Production-Readiness Refactor Plan (Behavior-Preserving)

## Summary
- Objective: execute a full audit + refactor for clean architecture, maintainability, async/state correctness, robustness, and performance without behavior changes except bug fixes.
- Baseline observed: JS tests pass (`78/78`), server tests pass (`85/85`, `4` skipped), but runtime/test logs show production-readiness gaps (empty-mesh error path, excessive console/print noise, oversized frontend bundle, oversized orchestrator modules, dead code, duplicated solver logic).
- Delivery mode locked: phased-by-risk, strict API compatibility, JSDoc+Python typing (no TS migration), structured/quiet logging, hard performance budgets.

## Public API / Interface Changes
- External API compatibility: no endpoint removals/renames; request/response keys remain stable for `/api/solve`, `/api/mesh/build`, `/api/mesh/generate-msh`, `/api/jobs`, `/api/status/{job_id}`, `/api/results/{job_id}`.
- Internal interface additions:
  - Add typed internal service interfaces for job lifecycle and simulation execution (Python protocols/typed classes) extracted from `server/app.py`.
  - Add frontend JSDoc types (`@typedef`, `@param`, `@returns`) and `// @ts-check` to core orchestration modules.
  - Add centralized error model mapping (validation/runtime/dependency) with consistent HTTP conversion in backend.
- Bug-fix contract change (allowed): invalid/empty mesh payloads fail fast with clear `422` instead of surfacing internal reduction errors.

## Phase 0: Guardrails and Baseline Hardening
- Freeze behavior with contract-focused baseline tests before refactor sequencing.
- Lock critical invariants from AGENTS docs into explicit regression checks (surface tags `1/2/3/4`, source tag required, `/api/mesh/build` non-`.geo` semantics).
- Capture baseline metrics:
  - Frontend production build size from `package.json` build script.
  - Async polling behavior in simulation UI (active vs idle request cadence).
  - Backend request log volume and error-path behavior for malformed mesh payloads.

## Phase 1: Backend Architectural Decomposition
- Split `server/app.py` into clear modules:
  - `server/api/routes_mesh.py` for `/api/mesh/build` and `/api/mesh/generate-msh`.
  - `server/api/routes_simulation.py` for `/api/solve`, `/api/status`, `/api/results`, `/api/jobs`.
  - `server/services/job_runtime.py` for scheduler state and transitions.
  - `server/services/simulation_runner.py` for run orchestration currently in `run_simulation`.
  - `server/services/update_service.py` for git update checks.
- Preserve route signatures and payload shapes while moving logic out of FastAPI handlers.
- Replace global mutable job orchestration (`jobs`, `job_queue`, `running_jobs`, lock) with a typed runtime manager class while preserving FIFO + `max_concurrent_jobs=1` behavior.
- Standardize backend error handling:
  - Validation errors return `422` with deterministic detail text.
  - Runtime dependency unavailable remains `503`.
  - Unexpected errors map to `500` with safe message and logged traceback.
- Bug fix in `server/solver/mesh.py`:
  - Add explicit empty vertices/indices guards before `np.max/np.min`.
  - Return actionable validation error instead of internal reduction exception.
- Replace ad-hoc `print` diagnostics with structured `logging` throughout `server/app.py`, `server/solver/mesh.py`, `server/solver/solve.py`, `server/solver/solve_optimized.py`, `server/solver/directivity_correct.py`, `server/solver/device_interface.py`.
- Extract duplicated solver utilities (observation-distance resolution and source-velocity construction) into shared solver utility module used by both solve paths.

## Phase 2: Frontend State and Async Flow Refactor
- Refactor simulation orchestration in `src/ui/simulation/actions.js` into smaller modules:
  - `simulation/jobActions.js` (run/stop/redo/remove/clear).
  - `simulation/polling.js` (poll scheduler lifecycle and retry/backoff).
  - `simulation/progressUi.js` (stage/progress rendering and connection-status synchronization).
- Make timer lifecycle explicit:
  - Add start/stop/dispose for poll + connection timers.
  - Ensure no orphan timers when panel is inactive/destroyed.
- Replace hardcoded backend URL in mesh artifact fetch with solver backend config (currently hardcoded in `downloadMeshArtifact`).
- Keep existing UI behavior and text semantics unchanged except bug corrections.
- Reduce full UI rebuild churn from `src/app/App.js` + `src/ui/paramPanel.js`:
  - Preserve panel output but shift from full `createFullPanel()` on every state change to targeted updates where possible.
  - Keep live-update render semantics intact.

## Phase 3: Dead Code, Duplication, Naming, Readability
- Remove confirmed dead helpers in `src/geometry/engine/mesh/enclosure.js`: `parsePlanBlock`, `sampleArc`, `sampleEllipse`, `sampleBezier`, `sampleLine`, `buildPlanOutline` (currently unused).
- Remove or gate dev-only globals and diagnostics behind explicit dev flags:
  - `src/app/exports.js` (`window.testBackendConnection`).
  - `src/geometry/expression.js` (`window.testExpressionParser`).
  - `src/app/logging.js` global exposure cleanup strategy.
- Resolve duplicated “clear failed jobs” semantics split between solver API client and local tracker methods by clarifying ownership and names.
- Apply naming pass for ambiguous fields/functions in simulation and job runtime modules without changing external contracts.

## Phase 4: Error Handling and Validation Hardening
- Add centralized frontend API error parsing utility used by solver/export calls for consistent UI error messaging.
- Add preflight validation helpers for simulation requests before submission (frequency bounds, mesh shape parity, required tags already present).
- Ensure EventBus emit path isolates listener failures so one listener cannot break others.
- Ensure backend preserves status-code boundaries (`422` vs `503` vs `500`) and avoids broad exception masking in routed handlers.

## Phase 5: Performance and Scalability Improvements
- Frontend hard targets:
  - Reduce production bundle from `631 KiB` to `<= 500 KiB` by lazy-loading simulation-heavy modules and optional diagnostics.
  - Maintain idle polling at max one status cycle every 10s when no active jobs.
  - Avoid full parameter panel teardown/rebuild on every minor state update.
- Backend hard targets:
  - Remove per-request verbose health prints.
  - Keep scheduler responsiveness with bounded lock scope and deterministic queue draining.
  - Reduce repeated object serialization/deserialization overhead in job/cache paths where safe.
  - Keep existing single-concurrency semantics unless explicit future change requested.

## Phase 6: Type Safety and Documentation
- Frontend:
  - Add `// @ts-check` and JSDoc types to `src/state.js`, `src/app/App.js`, `src/ui/simulation/actions.js`, `src/solver/index.js`, `src/app/exports.js`.
- Backend:
  - Tighten type hints for route/service interfaces and shared solver utility layer.
  - Document module ownership and flow boundaries in `docs/PROJECT_DOCUMENTATION.md` and `server/README.md`.
- Add “production runbook” section: logging levels, expected health responses, failure classes, and troubleshooting checklist.

## Test Cases and Scenarios
- Required existing suites after each phase:
  - `npm test`
  - `npm run test:server`
- Contract-critical targeted tests per AGENTS map:
  - `tests/mesh-payload.test.js`
  - `tests/geometry-artifacts.test.js`
  - `tests/enclosure-regression.test.js`
  - `tests/export-gmsh-pipeline.test.js`
  - `tests/waveguide-payload.test.js`
  - `server/tests/test_mesh_validation.py`
  - `server/tests/test_solver_tag_contract.py`
  - `server/tests/test_solver_hardening.py`
  - `server/tests/test_api_validation.py`
  - `server/tests/test_gmsh_endpoint.py`
  - `server/tests/test_occ_resolution_semantics.py`
- New regression tests to add:
  - Backend empty-mesh request returns deterministic `422` and no internal traceback leakage.
  - Simulation polling lifecycle stops/starts correctly and does not create duplicate timers.
  - Mesh artifact download uses configured backend URL (not hardcoded localhost).
  - EventBus listener exception isolation.
  - No behavior regressions in source-tag and canonical surface-tag handling.
  - Build performance gate test/script that fails CI if bundle exceeds `500 KiB` threshold.

## Delivery Artifacts
- Refactored code in phased commits, each test-green.
- Updated architecture and operational docs.
- A final production-readiness report containing:
  - Structural improvements completed.
  - Dead code and duplication removed.
  - Async/state issues fixed.
  - Validation/error-handling upgrades.
  - Performance deltas (before/after).
  - Residual risks and follow-up recommendations.

## Assumptions and Defaults
- Strict compatibility mode is enforced for API/UI contracts.
- Behavior changes are prohibited unless fixing verified bugs.
- Canonical tag semantics (`1/2/3/4`) and source-tag requirements remain unchanged.
- `/api/mesh/build` remains OCC-based and does not return `.geo`.
- Type safety strategy is JSDoc + Python typing hardening, not full TypeScript migration.
- Logging policy is structured and quieter by default with optional debug verbosity.
