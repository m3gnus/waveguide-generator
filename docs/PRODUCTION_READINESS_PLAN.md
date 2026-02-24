# Production-Readiness Unified Plan (Canonical)

**Last updated:** February 24, 2026  
**Supersedes:** `docs/PRODUCTION_READINESS_AUDIT_PLAN.md`

## Goal
Deliver production readiness with behavior preservation, explicit API contract stability, and measurable quality/performance gates.

## Baseline (as of February 24, 2026)
- JS tests: `78/78` passing
- Server tests: `85/85` passing (`4` skipped)
- Frontend bundle: `631 KiB`

## Non-Negotiable Contracts
- No endpoint removals/renames.
- Request/response keys remain stable for:
  - `/api/solve`
  - `/api/mesh/build`
  - `/api/mesh/generate-msh`
  - `/api/jobs`
  - `/api/status/{job_id}`
  - `/api/results/{job_id}`
- Canonical surface tags remain `1/2/3/4`.
- Source tag `2` must exist in simulation payloads.
- Interface tag `4` only exists when enclosure is enabled and `interfaceOffset > 0`.
- `/api/mesh/build` remains OCC-based and does not return `.geo`.
- Type-safety strategy remains JSDoc + Python type hints (no TS migration).
- Behavior changes are only allowed for verified bugs.

## Comparison Outcome (What Was Kept)
From `PRODUCTION_READINESS_PLAN.md` (kept for best outcome):
- Detailed phase breakdown, priorities, and explicit bug-fix items.
- Concrete targets for timer leak fixes, localStorage validation, and bundle reduction.
- Optional-risk handling for `waveguide_builder.py` split.

From `PRODUCTION_READINESS_AUDIT_PLAN.md` (added/strengthened):
- Stronger public API compatibility language.
- Additional backend service decomposition targets:
  - `server/services/simulation_runner.py`
  - `server/services/update_service.py`
- Explicit contract-critical test inventory tied to AGENTS guardrails.

## Release Gates

### Gate A (Must pass before production)
- Empty mesh payloads fail with deterministic `422` (no traceback leakage).
- Persistence failures do not leave jobs in false-complete states.
- Polling/event listeners are disposed correctly (no orphan timers/listeners).
- Backend logs are structured (`logging`), with no broad silent failures.
- API status code boundaries are consistent (`422` vs `503` vs `500` vs `404`).

### Gate B (Should pass before production)
- Frontend bundle reduced to `<= 550 KiB` initial threshold, then `<= 500 KiB`.
- Idle polling cadence bounded to reduce network churn.
- App/service module boundaries split to reduce change risk.

### Gate C (Can land after production freeze if needed)
- Deep internal decomposition of solver internals (`solve_optimized()` split).
- `waveguide_builder.py` split (only if all contract tests remain green).

## Phase Plan

## Phase 0: Guardrails and Baseline Hardening
**Objective:** lock behavior and contracts before refactors.

### Scope
- Capture baseline metrics:
  - `npm test`
  - `npm run test:server`
  - `npm run build` bundle measurement
  - backend startup log volume
- Add lint/format baseline:
  - `.eslintrc.json` (`no-unused-vars`, `no-undef`, `consistent-return`)
  - `.prettierrc` matching current style
  - warning-only rollout, no mass auto-fix
- Add/verify contract regression tests for:
  - surface tag mapping and source-tag requirements
  - `/api/mesh/build` OCC behavior (non-`.geo`)
- Add bundle gate script: `scripts/check-bundle-size.js`

### Exit Criteria
- Existing suites pass unchanged.
- Empty-mesh regression test exists and fails with `422` before Phase 1 fix.

## Phase 1: P0 Correctness and Operational Safety
**Objective:** close production blockers first.

### Scope
- Backend bug fixes:
  - Add explicit empty vertices/indices guard in `server/solver/mesh.py`.
  - Wrap `db.store_results()` and `db.store_mesh_artifact()` with failure handling in `run_simulation` flow.
  - Fix `scheduler_loop_running` read/write race by synchronizing under lock.
- Logging hardening:
  - Replace server-side `print()` with `logging` in runtime code paths.
  - Keep `print` only in allowed CLI contexts (`benchmark_solver.py` and explicit report CLI output).
- Error semantics:
  - Missing results -> `404`.
  - Dependency unavailable -> `503`.
  - Validation problems -> `422`.
  - Unexpected failures -> `500` with safe message and logged traceback.

### Exit Criteria
- Gate A items pass.
- No silent `except Exception: pass` in runtime code.

## Phase 2: Backend Architecture Decomposition
**Objective:** reduce long-file risk while preserving behavior.

### Scope
- Split `server/app.py` into:
  - `server/api/routes_mesh.py`
  - `server/api/routes_simulation.py`
  - `server/api/routes_misc.py`
- Extract runtime/services:
  - `server/services/job_runtime.py`
  - `server/services/simulation_runner.py`
  - `server/services/update_service.py`
- Extract shared solver utilities:
  - observation distance resolution
  - source velocity construction

### Exit Criteria
- Route signatures and payloads unchanged.
- FIFO and `max_concurrent_jobs=1` behavior preserved.
- Full server test suite remains green.

## Phase 3: Frontend State and Async Flow
**Objective:** prevent leaks and stabilize simulation lifecycle.

### Scope
- Split `src/ui/simulation/actions.js` into:
  - `simulation/jobActions.js`
  - `simulation/polling.js`
  - `simulation/progressUi.js`
  - `simulation/meshDownload.js`
- Add explicit panel lifecycle management:
  - `dispose()` removes EventBus listeners and clears timers.
  - error paths always call timer cleanup.
- Replace hardcoded backend URL with shared solver base URL config.
- Cache repeated DOM lookups to reduce render churn.

### Exit Criteria
- No duplicate polling timers across panel lifecycle.
- Mesh artifact fetches use configured backend URL only.
- Simulation UX remains behavior-compatible.

## Phase 4: Error Handling and Contract Hardening
**Objective:** consistent failure surfaces frontend + backend.

### Scope
- Add centralized frontend API error parser for solver/export paths.
- Add preflight validation before `POST /api/solve`:
  - frequency bounds
  - mesh presence
  - required source/surface tags
- Isolate EventBus listener failures so one listener does not break others.
- Validate localStorage schema before hydration; fallback to defaults on mismatch.

### Exit Criteria
- User-facing errors are deterministic/actionable.
- Corrupt localStorage no longer causes app-start crashes.

## Phase 5: Cleanup and Performance
**Objective:** ship a cleaner codebase with measurable runtime improvements.

### Scope
- Remove confirmed dead code in `src/geometry/engine/mesh/enclosure.js`.
- Consolidate dev globals behind one debug object + dev guard.
- Lazy-load simulation-heavy modules via dynamic `import()`.
- Bound idle polling budget and add backoff after repeated connection failures.
- Clean backend startup/request log noise.

### Exit Criteria
- Bundle gate passes (`<= 550 KiB` first pass, `<= 500 KiB` final target).
- Idle polling remains bounded with no unnecessary request bursts.

## Phase 6: Type Safety, Docs, and Runbook
**Objective:** ensure long-term maintainability and operability.

### Scope
- Frontend `// @ts-check` + JSDoc on critical modules.
- Backend type hints tightened for route/service interfaces.
- Update `docs/PROJECT_DOCUMENTATION.md` with new boundaries.
- Add production runbook (logging levels, health expectations, failure classes, troubleshooting).

### Exit Criteria
- Annotated modules pass type checks.
- Operator can diagnose common failures from docs alone.

## Required Test Matrix
Run these after each phase as applicable:

### Core Suites
- `npm test`
- `npm run test:server`

### Contract-Critical JS Tests
- `tests/mesh-payload.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/enclosure-regression.test.js`
- `tests/export-gmsh-pipeline.test.js`
- `tests/waveguide-payload.test.js`

### Contract-Critical Server Tests
- `server/tests/test_mesh_validation.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_api_validation.py`
- `server/tests/test_gmsh_endpoint.py`
- `server/tests/test_occ_resolution_semantics.py`
- `server/tests/test_updates_endpoint.py`

### New/Strengthened Regression Tests
- Empty mesh -> deterministic `422` without traceback leakage.
- DB persistence failure -> job transitions to error state (never false-complete).
- Polling lifecycle -> cleanup on error and panel disposal.
- EventBus listener isolation.
- Mesh artifact download uses configured backend URL.
- Bundle-size gate script enforcement.

## Sequence and Parallelization
- Recommended order: Phase 0 -> Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5 -> Phase 6.
- Phase 3 and Phase 4 can run in parallel after Phase 2.
- Phase 6 can start after Phase 2 but should finalize after Phase 5.
- Defer risky optional solver-file splits until all gates are green.

## Deferred/Optional Work (Only After Gates A+B)
- Internal split of `solve_optimized()` into private helper functions.
- Controlled split of `waveguide_builder.py` if OCC parity remains stable.

## Delivery Artifacts
- Phased, test-green commits.
- Updated architecture and runbook docs.
- Final readiness report with:
  - before/after performance deltas
  - defect classes eliminated
  - residual risks and follow-up actions
