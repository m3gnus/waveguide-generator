# Production-Readiness Unified Plan (Sessionized Canonical)

**Status: COMPLETE** — All sessions (0–10) finished. Gates A and B passing.
**Last updated:** February 25, 2026
**Supersedes:** `docs/PRODUCTION_READINESS_AUDIT_PLAN.md`
**Final report:** `docs/PRODUCTION_READINESS_REPORT.md`

## Decision: One Go vs Multiple Sessions
**Best outcome:** split into multiple sessions.

### Why splitting is better for this codebase
- The work crosses high-risk boundaries (`server/app.py`, solver pipeline, simulation UI lifecycle).
- Contract regressions are expensive (`surfaceTags`, source-tag requirements, `/api/mesh/build` semantics).
- Test maps from `AGENTS.md` require targeted + full-suite validation for several files.
- Smaller sessions allow deterministic rollback/handoff with less context load for weaker agents.

## Final Metrics
| Metric | Baseline (Session 0) | Final (Session 10) | Delta |
|---|---|---|---|
| JS tests | 81/81 passing | 96/96 passing | +15 regression tests |
| Server tests | 88/88 passing (1 skipped) | 102/102 passing (6 skipped) | +14 tests |
| Frontend bundle | 631 KiB | 89.7 KiB | -541.3 KiB (85.8% reduction) |

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
- Type-safety strategy remains JSDoc + Python type hints (no TypeScript migration).
- Behavior changes are only allowed for verified bugs.

## Release Gates

### Gate A (Must pass before production)
- Empty mesh payloads fail with deterministic `422` (no traceback leakage).
- Persistence failures do not leave jobs in false-complete states.
- Polling/event listeners are disposed correctly (no orphan timers/listeners).
- Backend logs are structured (`logging`) with no silent broad exception swallowing.
- API status code boundaries are consistent (`422` vs `503` vs `500` vs `404`).

### Gate B (Should pass before production)
- Frontend bundle reduced to `<= 550 KiB`, then `<= 500 KiB`.
- Idle polling cadence is bounded.
- `app.py` responsibilities are decomposed into routes/services.

### Gate C (Optional / Post-freeze)
- Internal decomposition of `solve_optimized()`.
- Optional split of `waveguide_builder.py` only with OCC parity intact.

## Current Gate Status (Update Every Session)
| Gate | Status (`not_started` / `in_progress` / `blocked` / `pass`) | Last checked | Notes |
|---|---|---|---|
| Gate A | `pass` | `2026-02-24` | All Gate A conditions satisfied: empty-mesh 422, persistence failure safety, scheduler lock, HTTP semantics, structured logging, polling lifecycle |
| Gate B | `pass` | `2026-02-24` | `app.py` decomposition complete, idle polling budget/backoff enforced, bundle reduced to `89.7 KiB` (`<= 500 KiB`) |
| Gate C | `not_started` | `-` | Optional gate |

## Agent Execution Protocol (Use in Every Session)
1. Pull context:
   - Read this plan.
   - Read `AGENTS.md` at repo root.
2. Confirm clean understanding of scope and allowed files.
3. Make only the listed changes for that session.
4. Run targeted tests first.
5. Run full suites:
   - `npm test`
   - `npm run test:server`
6. If any test fails, fix or revert that session’s changes.
7. Write handoff note using the template in this document.
8. Update this document using the mandatory update checklist and tracker sections.

## Test Commands Reference

### Core suites
- `npm test`
- `npm run test:server`

### JS targeted example
- `node --test tests/mesh-payload.test.js`

### Server targeted example
- `cd server && python3 -m unittest tests.test_mesh_validation`

### Contract-critical JS tests
- `tests/mesh-payload.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/enclosure-regression.test.js`
- `tests/export-gmsh-pipeline.test.js`
- `tests/waveguide-payload.test.js`

### Contract-critical server tests
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_gmsh_endpoint.py`
- `server/tests/test_occ_resolution_semantics.py`
- `server/tests/test_updates_endpoint.py`
- `server/tests/test_mesh_validation.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_api_validation.py`

## Session Order and Ownership
Run sessions in order. Parallelization is allowed only where explicitly stated.

- Session 0: Baseline and guardrails
- Session 1: Empty-mesh 422 hardening
- Session 2: Job persistence failure safety
- Session 3: Scheduler race and status-code semantics
- Session 4: Backend logging and exception tightening
- Session 5: Backend decomposition (routes + services)
- Session 6: Frontend timer/listener lifecycle + backend URL config
- Session 7: Frontend module split + DOM cache
- Session 8: Frontend/backend error-hardening + localStorage schema checks
- Session 9: Cleanup + performance gates
- Session 10: Type safety + documentation + final readiness report

## Session Tracker (Mandatory)
Update this table at the end of each completed session.

| Session | Status (`not_started` / `in_progress` / `done` / `blocked`) | Date completed | Agent | PR/Commit | Evidence link (handoff/log section) |
|---|---|---|---|---|---|
| 0 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 0 log entry` |
| 1 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 1 log entry` |
| 2 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 2 log entry` |
| 3 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 3 log entry` |
| 4 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 4 log entry` |
| 5 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 5 log entry` |
| 6 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 6 log entry` |
| 7 | `done` | `2026-02-24` | `claude-sonnet-4-6` | `-` | `Session 7 log entry` |
| 8 | `done` | `2026-02-24` | `gpt-5-codex` | `-` | `Session 8 log entry` |
| 9 | `done` | `2026-02-24` | `gpt-5-codex` | `-` | `Session 9 log entry` |
| 10 | `done` | `2026-02-24` | `gpt-5-codex` | `-` | `Session 10 log entry` |

## Session 0: Baseline and Guardrails
**Objective:** establish measurable baseline and non-breaking CI guardrails.

### Allowed file areas
- `docs/`
- Lint/format config files
- `scripts/check-bundle-size.js`

### Steps
1. Record baseline metrics from current branch:
   - `npm test`
   - `npm run test:server`
   - `npm run build` and capture bundle size
2. Add ESLint baseline (`warning-only` behavior, no sweeping auto-fix).
3. Add Prettier config matching current style.
4. Add `scripts/check-bundle-size.js` with threshold `550 KiB` initially.
5. Document baseline values and script usage in this plan or related docs.

### Required tests
- `npm test`
- `npm run test:server`

### Exit criteria
- No behavior change.
- Baseline metrics captured.
- Bundle gate script present and runnable.

## Session 1: Empty-Mesh 422 Hardening
**Objective:** fix empty-mesh reduction crash path with deterministic validation response.

### Allowed file areas
- `server/solver/mesh.py`
- `server/tests/test_mesh_validation.py` (or focused validation test module)

### Steps
1. Add explicit guards for empty `vertices` and `indices` before `np.max/np.min` usage.
2. Return deterministic validation error mapped to `422`.
3. Add regression test asserting:
   - status `422`
   - actionable error message
   - no traceback leakage in API response body

### Required targeted tests
- `cd server && python3 -m unittest tests.test_mesh_validation`
- `cd server && python3 -m unittest tests.test_api_validation`

### Required full suites
- `npm run test:server`
- `npm test`

### Exit criteria
- Gate A empty-mesh condition satisfied.
- Contract-critical solver tests remain green.

## Session 2: Job Persistence Failure Safety
**Objective:** never mark jobs complete when persistence fails.

### Allowed file areas
- `server/app.py`
- server test files covering job lifecycle/persistence

### Steps
1. Wrap `db.store_results()` and `db.store_mesh_artifact()` in guarded error handling inside simulation completion flow.
2. On persistence error:
   - mark job status as `error`
   - store safe diagnostic message (no sensitive traceback in API payload)
3. Add regression test ensuring no false-complete state when persistence throws.

### Required targeted tests
- `cd server && python3 -m unittest tests.test_api_validation`
- `cd server && python3 -m unittest tests.test_dependency_runtime`

### Required full suites
- `npm run test:server`
- `npm test`

### Exit criteria
- Persistence failures transition job to error deterministically.
- Existing job API contracts unchanged.

## Session 3: Scheduler Race + HTTP Semantics
**Objective:** remove race condition and normalize status-code boundaries.

### Allowed file areas
- `server/app.py`
- relevant server test modules

### Steps
1. Move `scheduler_loop_running` read/write under the same lock used for queue transitions.
2. Ensure response semantics:
   - missing result resource -> `404`
   - validation failures -> `422`
   - dependency unavailable -> `503`
   - unexpected -> `500`
3. Add/adjust tests for these cases.

### Required targeted tests
- `cd server && python3 -m unittest tests.test_api_validation`
- `cd server && python3 -m unittest tests.test_updates_endpoint`

### Required full suites
- `npm run test:server`
- `npm test`

### Exit criteria
- No race-induced scheduler state glitches.
- Status-code mapping follows Gate A contract.

## Session 4: Backend Logging + Exception Tightening
**Objective:** replace ad-hoc prints and remove silent broad exception handling.

### Allowed file areas
- `server/app.py`
- `server/solver/*.py` runtime modules
- tests affected by logging/exception behavior

### Steps
1. Replace runtime `print()` with `logging` (`logger = logging.getLogger(__name__)`).
2. Configure app log level from env (default `INFO`).
3. Narrow broad `except Exception` where feasible.
4. If catch-all is required, log with `exc_info=True` and comment why catch-all is needed.
5. Keep CLI-style print output only for approved CLI/report paths.

### Required targeted tests
- `cd server && python3 -m unittest tests.test_solver_hardening`
- `cd server && python3 -m unittest tests.test_solver_tag_contract`

### Required full suites
- `npm run test:server`
- `npm test`

### Exit criteria
- Runtime code paths use structured logging.
- No silent `except Exception: pass` in runtime flow.

## Session 5: Backend Decomposition (Routes + Services)
**Objective:** reduce `app.py` complexity without changing API behavior.

### Allowed file areas
- `server/app.py`
- `server/api/routes_mesh.py`
- `server/api/routes_simulation.py`
- `server/api/routes_misc.py`
- `server/services/job_runtime.py`
- `server/services/simulation_runner.py`
- `server/services/update_service.py`

### Steps
1. Create route modules and move handlers without changing signatures.
2. Keep payload keys and route paths identical.
3. Extract runtime state/orchestration into service classes.
4. Preserve FIFO scheduling and `max_concurrent_jobs=1` semantics.
5. Ensure startup/shutdown wiring still initializes all dependencies.

### Required targeted tests
- `cd server && python3 -m unittest tests.test_dependency_runtime`
- `cd server && python3 -m unittest tests.test_gmsh_endpoint`
- `cd server && python3 -m unittest tests.test_occ_resolution_semantics`
- `cd server && python3 -m unittest tests.test_updates_endpoint`

### Required full suites
- `npm run test:server`
- `npm test`

### Exit criteria
- Full server suite green with unchanged API contract.
- `app.py` reduced to app assembly + router wiring + lifecycle hooks.

## Session 6: Frontend Lifecycle Safety (Timers/Listeners/URL)
**Objective:** remove timer/listener leaks and hardcoded backend URL usage.

### Allowed file areas
- `src/ui/simulation/`
- `src/solver/`
- minimal related app wiring
- matching tests

### Steps
1. Ensure polling timer cleanup in all error/success/dispose paths.
2. Add `dispose()` lifecycle hook to remove EventBus listeners and timers.
3. Replace hardcoded `http://localhost:8000` fetch base with shared config-derived base URL.
4. Add regression tests for:
   - duplicate timer prevention
   - disposal cleanup
   - configured URL usage

### Required targeted tests
- `node --test tests/waveguide-payload.test.js`
- `node --test tests/export-gmsh-pipeline.test.js`

### Required full suites
- `npm test`
- `npm run test:server`

### Exit criteria
- No orphan timers/listeners.
- No hardcoded backend base URL remains in simulation/export paths.

## Session 7: Frontend Module Split + DOM Cache
**Objective:** make simulation orchestration maintainable and reduce DOM churn.

### Allowed file areas
- `src/ui/simulation/actions.js`
- new `src/ui/simulation/*` modules
- minimal import/wiring updates
- matching tests

### Steps
1. Split `actions.js` into:
   - `jobActions.js`
   - `polling.js`
   - `progressUi.js`
   - `meshDownload.js`
2. Keep public behavior and event names unchanged.
3. Cache frequently accessed DOM nodes in one place and reuse refs.
4. Keep backward-compatible exports from `actions.js` if needed.

### Required targeted tests
- `node --test tests/export-gmsh-pipeline.test.js`
- `node --test tests/waveguide-payload.test.js`

### Required full suites
- `npm test`
- `npm run test:server`

### Exit criteria
- Simulation behavior unchanged.
- Reduced repeated `document.getElementById()` churn in hot paths.

## Session 8: Error Hardening + localStorage Schema Validation
**Objective:** unify frontend/backend error surfaces and prevent hydration crashes.

### Allowed file areas
- `src/solver/` error handling utilities
- `src/state.js` and/or storage-hydration paths
- EventBus implementation
- minimal corresponding backend error mapping touch-ups

### Steps
1. Add centralized frontend API error parser (validation vs dependency vs unexpected).
2. Add simulation preflight validation before `/api/solve` call.
3. Wrap EventBus listener execution to isolate listener failure impact.
4. Validate persisted state shape before hydration; fallback to defaults on mismatch.
5. Add tests for listener isolation and corrupt storage handling.

### Required targeted tests
- `node --test tests/mesh-payload.test.js`
- `node --test tests/geometry-artifacts.test.js`
- `node --test tests/enclosure-regression.test.js`

### Required full suites
- `npm test`
- `npm run test:server`

### Exit criteria
- Deterministic user-facing errors.
- Corrupt local storage cannot crash startup.

## Session 9: Cleanup + Performance Gates
**Objective:** remove dead code and meet bundle/polling budgets.

### Allowed file areas
- `src/geometry/engine/mesh/enclosure.js`
- simulation/dynamic import integration files
- debug-global exposure locations
- performance scripts/docs

### Steps
1. Remove confirmed dead helpers in `enclosure.js`.
2. Consolidate debug globals behind dev-only gate.
3. Lazy-load simulation-heavy modules with dynamic imports.
4. Enforce idle polling budget and backoff after repeated failures.
5. Run bundle gate and record result.

### Required targeted tests (AGENTS parity set)
- `node --test tests/mesh-payload.test.js`
- `node --test tests/geometry-artifacts.test.js`
- `node --test tests/enclosure-regression.test.js`

### Required full suites
- `npm test`
- `npm run test:server`
- `npm run build`
- `node scripts/check-bundle-size.js`

### Exit criteria
- Bundle `<= 550 KiB` (intermediate) and trending to `<= 500 KiB`.
- No geometry-tag regressions.

## Session 10: Type Safety + Docs + Final Readiness Report
**Objective:** finalize maintainability and operations docs with verified status.

### Allowed file areas
- `src/` JSDoc-annotated modules
- `server/` type hints
- `docs/PROJECT_DOCUMENTATION.md`
- `server/README.md`
- readiness report document

### Steps
1. Add `// @ts-check` and JSDoc for high-risk frontend modules.
2. Tighten backend route/service type hints.
3. Update architecture docs to reflect extracted module boundaries.
4. Add operator runbook:
   - log levels
   - health expectations
   - common failure classes
   - troubleshooting steps
5. Produce final readiness report with:
   - completed sessions
   - test evidence
   - performance deltas
   - residual risks

### Required full suites
- `npm test`
- `npm run test:server`
- `npm run build`

### Exit criteria
- Documentation matches implementation.
- Readiness report is complete and evidence-backed.

## Session Handoff Template (Copy/Paste)
Use this at the end of every session:

```md
Session: <number and title>
Date: <YYYY-MM-DD>
Agent: <name/id>

Scope completed:
- <item>
- <item>

Files changed:
- <path>
- <path>

Tests run:
- <command> -> <pass/fail>
- <command> -> <pass/fail>

Contract checks:
- Surface tags 1/2/3/4 preserved: <yes/no>
- Source tag requirement preserved: <yes/no>
- `/api/mesh/build` non-`.geo` semantics preserved: <yes/no>

Known issues / follow-up:
- <item>

Next session to run:
- <session number>
```

## Mandatory Document Update Checklist (After Each Session)
Every agent must update this document before ending the session.

1. Update `Last updated` at the top of this file.
2. Update the `Session Tracker` row for the session just completed.
3. Update `Current Gate Status` if the session changed any gate readiness.
4. Add a new entry to `Session Execution Log` (template below) with:
   - scope actually completed
   - exact tests run and outcomes
   - contract checks
   - known issues and follow-up
5. If scope changed from the original session definition, note the delta explicitly in the log entry.
6. If any required test was not run, record why and mark session status as `blocked` or `in_progress` (not `done`).

## Session Execution Log
Append one entry per session in reverse chronological order.

### Log Entry Template
```md
#### Session <number>: <title>
- Date: <YYYY-MM-DD>
- Agent: <name/id>
- Branch/PR/Commit: <id>
- Status: <done/in_progress/blocked>
- Planned scope changes: <none or explicit delta>

Completed work:
- <item>
- <item>

Files changed:
- <path>
- <path>

Tests run:
- <command> -> <pass/fail>
- <command> -> <pass/fail>

Contract checks:
- Surface tags 1/2/3/4 preserved: <yes/no>
- Source tag requirement preserved: <yes/no>
- `/api/mesh/build` non-`.geo` semantics preserved: <yes/no>

Gate impact:
- Gate A: <no change / progressed / pass / blocked>
- Gate B: <no change / progressed / pass / blocked>
- Gate C: <no change / progressed / pass / blocked>

Known issues / follow-up:
- <item>

Next session:
- <number>
```

---

#### Session 10: Type Safety + Docs + Final Readiness Report
- Date: 2026-02-24
- Agent: gpt-5-codex
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Added `// @ts-check` and JSDoc contract typing to high-risk frontend modules:
  - `src/solver/index.js`
  - `src/ui/simulation/polling.js`
  - `src/ui/simulation/SimulationPanel.js`
- Tightened backend route/service type hints without changing API behavior:
  - `server/api/routes_mesh.py`
  - `server/api/routes_misc.py`
  - `server/api/routes_simulation.py`
  - `server/services/job_runtime.py`
- Updated architecture and operations docs to match runtime decomposition:
  - `docs/PROJECT_DOCUMENTATION.md` now reflects `server/app.py` as assembly + router/service boundaries
  - `server/README.md` now includes operator runbook sections (log levels, health expectations, failure classes, troubleshooting)
- Added final readiness report:
  - `docs/PRODUCTION_READINESS_REPORT.md` with completed sessions, verification evidence, performance delta, residual risks

Files changed:
- `src/solver/index.js`
- `src/ui/simulation/polling.js`
- `src/ui/simulation/SimulationPanel.js`
- `server/api/routes_mesh.py`
- `server/api/routes_misc.py`
- `server/api/routes_simulation.py`
- `server/services/job_runtime.py`
- `docs/PROJECT_DOCUMENTATION.md`
- `server/README.md`
- `docs/PRODUCTION_READINESS_REPORT.md` (new)
- `docs/PRODUCTION_READINESS_PLAN.md`

Tests run:
- `node --test tests/simulation-flow.test.js` -> 18/18 pass
- `cd server && python3 -m unittest tests.test_api_validation tests.test_dependency_runtime` -> 28 tests pass
- `npm test` -> 96/96 pass
- `npm run test:server` -> 102 pass, 6 skipped
- `npm run build` -> pass (`bundle.js` 89.7 KiB)
- `node scripts/check-bundle-size.js` -> pass (`89.7 KiB <= 550 KiB`, target `500 KiB`)

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag mapping changes)
- Source tag requirement preserved: yes (frontend preflight + backend validation unchanged)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no endpoint contract changes)

Gate impact:
- Gate A: no change (remains pass)
- Gate B: no change (remains pass)
- Gate C: no change (optional, not started)

Known issues / follow-up:
- `npm run test:server` still emits pre-existing sqlite `ResourceWarning` noise (non-blocking, suites pass)

Next session:
- none (sessionized production-readiness plan complete)

---

#### Session 9: Cleanup + Performance Gates
- Date: 2026-02-24
- Agent: gpt-5-codex
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Removed unused legacy enclosure plan helpers from `src/geometry/engine/mesh/enclosure.js` (the active enclosure path remains angular rounded-box ray casting)
- Consolidated debug globals behind local/dev runtime guard (`src/config/runtimeMode.js`) and applied gating in:
  - `src/app/logging.js` (`window.ChangeLog`, `window.setAgent`, etc.)
  - `src/app/exports.js` (`window.testBackendConnection`)
  - `src/geometry/expression.js` (`window.testExpressionParser`)
  - `src/app/App.js` (`window.__waveguideApp`)
- Added lazy-load integration for heavy frontend paths:
  - `SimulationPanel` is now loaded via dynamic import from `src/app/App.js`
  - `src/app/exports.js` is loaded on-demand via dynamic import from `src/app/App.js` export handlers
- Fixed Three.js imports to package specifiers so webpack externals apply:
  - `src/app/scene.js`
  - `src/viewer/index.js`
  - `src/app/exports.js`
- Enforced polling budget/backoff semantics in `src/ui/simulation/polling.js`:
  - active polling cadence `1s`
  - idle polling cadence `15s`
  - repeated-failure backoff doubling up to `30s`
  - idle floor retained during failure mode
  - failure counter reset in `clearPollTimer`
- Added/updated regression coverage:
  - `tests/simulation-flow.test.js` adds idle-polling-budget error-path assertion and validates failure-counter reset in `clearPollTimer`
- Completed URL cleanup follow-through using shared backend config:
  - `src/ui/simulation/viewResults.js`
  - `src/solver/client.js`
  - `src/solver/status.js`
  - `src/app/updates.js`
  - `src/ui/simulation/exports.js`

Files changed:
- `src/config/runtimeMode.js` (new)
- `src/app/App.js`
- `src/app/logging.js`
- `src/app/exports.js`
- `src/app/scene.js`
- `src/viewer/index.js`
- `src/geometry/expression.js`
- `src/geometry/engine/mesh/enclosure.js`
- `src/ui/simulation/polling.js`
- `src/ui/simulation/SimulationPanel.js`
- `src/ui/simulation/jobTracker.js`
- `src/ui/simulation/viewResults.js`
- `src/ui/simulation/exports.js`
- `src/solver/client.js`
- `src/solver/status.js`
- `src/app/updates.js`
- `tests/simulation-flow.test.js`
- `docs/PRODUCTION_READINESS_PLAN.md`

Tests run:
- `node --test tests/mesh-payload.test.js` -> 4/4 pass
- `node --test tests/geometry-artifacts.test.js` -> 5/5 pass
- `node --test tests/enclosure-regression.test.js` -> 9/9 pass
- `node --test tests/simulation-flow.test.js` -> 18/18 pass
- `node --test tests/export-gmsh-pipeline.test.js` -> 2/2 pass
- `npm test` -> 96/96 pass
- `npm run test:server` -> 102 pass, 6 skipped
- `npm run build` -> pass (`bundle.js` 89.7 KiB, async chunks generated)
- `node scripts/check-bundle-size.js` -> pass (`89.7 KiB <= 550 KiB`)

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag mapping changes)
- Source tag requirement preserved: yes (no source-tag contract regressions)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no endpoint contract changes)

Gate impact:
- Gate A: no change (remains pass)
- Gate B: pass (bundle target + polling budget + app decomposition now all satisfied)
- Gate C: no change

Known issues / follow-up:
- `npm run test:server` still reports pre-existing sqlite `ResourceWarning` noise in test output (non-blocking, tests pass)

Next session:
- 10

---

#### Session 8: Error Hardening + localStorage Schema Validation
- Date: 2026-02-24
- Agent: gpt-5-codex
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Added centralized frontend API error parsing/classification in `src/solver/apiErrors.js` (`validation` vs `dependency` vs `not_found` vs `unexpected` vs `network`)
- Updated `src/solver/index.js` to route backend calls through the centralized parser and expose typed `ApiError` surfaces with status/category metadata
- Added `validateSimulationPreflight` in `src/solver/index.js` and enforced source-tag (`2`) + frequency sanity checks before `/api/solve`
- Hardened EventBus dispatch in `src/events.js` so listener failures are isolated and do not prevent other listeners from executing
- Hardened state hydration in `src/state.js`:
  - localStorage availability checks for non-browser/test runtimes
  - persisted-state schema validation (`type` + `params`) with model-type allowlist
  - fallback to defaults plus invalid persisted payload removal on mismatch
- Fixed backend HTTP semantics in `server/api/routes_misc.py`: `/api/render-directivity` now validates missing input (`422`) before dependency import checks (`503`)
- Added regression tests for Session 8 hardening:
  - `tests/error-hardening.test.js` (EventBus listener isolation + storage schema fallback)
  - `tests/simulation-flow.test.js` (`submitSimulation` preflight + typed `422` API error mapping)

Files changed:
- `src/solver/apiErrors.js` (new)
- `src/solver/index.js`
- `src/events.js`
- `src/state.js`
- `server/api/routes_misc.py`
- `tests/error-hardening.test.js` (new)
- `tests/simulation-flow.test.js`

Tests run:
- `node --test tests/mesh-payload.test.js` -> 4/4 pass
- `node --test tests/geometry-artifacts.test.js` -> 5/5 pass
- `node --test tests/enclosure-regression.test.js` -> 9/9 pass
- `node --test tests/simulation-flow.test.js` -> 17/17 pass
- `node --test tests/error-hardening.test.js` -> 5/5 pass
- `cd server && python3 -m unittest tests.test_api_validation.HttpSemanticsTest.test_render_directivity_empty_input_returns_422 -v` -> pass
- `npm test` -> 95/95 pass
- `npm run test:server` -> 102 pass, 6 skipped

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no geometry tag mapping changes)
- Source tag requirement preserved: yes (added explicit preflight enforcement for tag `2`)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no `/api/mesh/build` changes)

Gate impact:
- Gate A: pass (maintained), with improved deterministic error surfaces and startup resilience to corrupt local state
- Gate B: no change (bundle reduction still pending Session 9)
- Gate C: no change

Known issues / follow-up:
- Bundle-size reduction gate remains outstanding for Session 9 (`<= 550 KiB`)

Next session:
- 9

---

#### Session 7: Frontend Module Split + DOM Cache
- Date: 2026-02-24
- Agent: gpt-5-codex (verification + log completion)
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Verified module split of `src/ui/simulation/actions.js` into focused sub-modules:
  - `jobActions.js`
  - `polling.js`
  - `progressUi.js`
  - `meshDownload.js`
- Verified backward-compatible barrel exports remain available from `actions.js`
- Verified DOM-cache centralization via `getSimulationDom()` in `progressUi.js` and reuse across polling/job flows
- Verified module-split regression coverage in `tests/simulation-flow.test.js`:
  - sub-module export availability (`formatJobSummary`, `renderJobList`)
  - barrel/sub-module function identity (`pollSimulationStatus`)
  - `clearPollTimer` state-reset behavior

Files changed:
- `src/ui/simulation/actions.js`
- `src/ui/simulation/jobActions.js` (new)
- `src/ui/simulation/polling.js` (new)
- `src/ui/simulation/progressUi.js` (new)
- `src/ui/simulation/meshDownload.js` (new)
- `tests/simulation-flow.test.js`

Tests run:
- `node --test tests/waveguide-payload.test.js` -> 3/3 pass
- `node --test tests/export-gmsh-pipeline.test.js` -> 2/2 pass
- `node --test tests/simulation-flow.test.js` -> 17/17 pass (includes Session 7 regression coverage)
- `npm test` -> pass
- `npm run test:server` -> pass

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no geometry/tag code changes)
- Source tag requirement preserved: yes (no payload tagging regressions)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no export/backend meshing path changes)

Gate impact:
- Gate A: no change (already pass from Session 6; maintained)
- Gate B: no change (bundle reduction pending Session 9)
- Gate C: no change

Known issues / follow-up:
- Session 7 execution-log entry was missing and is now backfilled for tracker parity

Next session:
- 8

---

#### Session 6: Frontend Lifecycle Safety (Timers/Listeners/URL)
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Created `src/config/backendUrl.js`: single `DEFAULT_BACKEND_URL` constant (`http://localhost:8000`) as the shared config source
- Updated `src/solver/index.js`: `BemSolver` constructor now imports and uses `DEFAULT_BACKEND_URL` instead of inline literal
- Fixed `src/ui/simulation/actions.js`:
  - Moved `isPolling` guard to be the FIRST statement in `pollSimulationStatus` (before any DOM access) — fixes bug where duplicate guard check failed in non-DOM environments and guard didn't prevent DOM access
  - `clearPollTimer` now resets `panel.isPolling = false` so polling loop can be restarted after being stopped (fixes re-run-after-stop regression)
  - `finally` block in `pollSimulationStatus` now uses inline timer management (not `clearPollTimer`) to reschedule without triggering `isPolling` reset mid-loop
  - `downloadMeshArtifact` accepts `backendUrl` parameter (default: `DEFAULT_BACKEND_URL`); call site in `runSimulation` passes `panel.solver.backendUrl`
- Fixed `src/ui/simulation/exports.js`: `exportAsMatplotlibPNG` uses `panel?.solver?.backendUrl` instead of hardcoded URL
- Fixed `src/ui/simulation/viewResults.js`: `fetchCharts` closure uses `panel?.solver?.backendUrl` instead of hardcoded URL
- Fixed `src/ui/simulation/events.js`: `state:updated` listener stored as `panel._onStateUpdated` for removal in `dispose()`
- Fixed `src/ui/simulation/mesh.js`: `simulation:mesh-ready` and `simulation:mesh-error` listeners stored as `panel._onMeshReady` / `panel._onMeshError` for removal in `dispose()`
- Added `SimulationPanel.dispose()`: clears `pollTimer`, `connectionPollTimer`, resets `isPolling`, removes all three AppEvents listeners (`state:updated`, `simulation:mesh-ready`, `simulation:mesh-error`)
- Added 3 regression tests to `tests/simulation-flow.test.js`:
  - `downloadMeshArtifact uses the provided backendUrl instead of hardcoded default`
  - `pollSimulationStatus guard: second call returns immediately when isPolling is true`
  - `dispose() clears poll timers, connection timer, and resets isPolling`

Files changed:
- `src/config/backendUrl.js` (new)
- `src/solver/index.js`
- `src/ui/simulation/actions.js`
- `src/ui/simulation/exports.js`
- `src/ui/simulation/viewResults.js`
- `src/ui/simulation/events.js`
- `src/ui/simulation/mesh.js`
- `src/ui/simulation/SimulationPanel.js`
- `tests/simulation-flow.test.js`

Tests run:
- `node --test tests/waveguide-payload.test.js` -> 3/3 pass
- `node --test tests/export-gmsh-pipeline.test.js` -> 2/2 pass
- `node --test tests/simulation-flow.test.js` -> 11/11 pass (+3 new)
- `npm test` -> 84/84 pass (was 81/81; +3 new regression tests)
- `npm run test:server` -> 102 pass, 1 skipped (unchanged)

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag logic changed)
- Source tag requirement preserved: yes (no tag logic changed)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changed)

Gate impact:
- Gate A: pass — polling lifecycle condition now satisfied; all Gate A conditions met
- Gate B: no change (bundle reduction pending Session 9)
- Gate C: no change

Known issues / follow-up:
- `src/app/updates.js` retains its own `DEFAULT_BACKEND_URL` constant (not in Session 6 scope; planned cleanup in Session 9/10)
- `src/app/exports.js` debug `testBackendConnection` function retains hardcoded URL (console diagnostic, not a simulation/export path)
- `src/solver/status.js` `BemStatusManager.checkConnection` retains hardcoded URL — class appears unused by main code path; cleanup in Session 9

Next session:
- 7 (Frontend module split + DOM cache)

---

#### Session 5: Backend Decomposition (Routes + Services)
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Extracted all Pydantic models from `app.py` into `server/models.py`
- Extracted solver conditional imports and availability flags into `server/solver_bootstrap.py`
- Created `server/services/` package:
  - `job_runtime.py`: global runtime state (`jobs`, `job_queue`, `running_jobs`, `jobs_lock`, `scheduler_loop_running`, `db`, `db_initialized`), FIFO scheduler (`_drain_scheduler_queue`), DB helpers, startup lifecycle (`startup_jobs_runtime`)
  - `simulation_runner.py`: `run_simulation` coroutine and `_validate_occ_adaptive_bem_shell`
  - `update_service.py`: `_run_git` and `get_update_status`
- Created `server/api/` package:
  - `routes_misc.py`: `APIRouter` with `/`, `/health`, `/api/updates/check`, `/api/render-charts`, `/api/render-directivity`
  - `routes_mesh.py`: `APIRouter` with `/api/mesh/build`, `/api/mesh/generate-msh`
  - `routes_simulation.py`: `APIRouter` with all simulation lifecycle endpoints (`/api/solve`, `/api/stop`, `/api/status`, `/api/results`, `/api/mesh-artifact`, `/api/jobs`, etc.)
- Rewrote `server/app.py` as thin assembly layer (~120 lines vs original ~1511): re-exports all public symbols for backward compat, registers routers via `include_router`, manages lifespan
- Updated all 5 server test files to use correct patch targets in new module locations:
  - `test_updates_endpoint.py`: `services.update_service._run_git`, `api.routes_misc.get_update_status`
  - `test_dependency_runtime.py`: `api.routes_misc.*`, `api.routes_mesh.*`, `api.routes_simulation.*`
  - `test_gmsh_endpoint.py`: `api.routes_mesh.gmsh_mesher_available`, `api.routes_mesh.generate_msh_from_geo`
  - `test_api_validation.py`: `api.routes_simulation.*`, `services.simulation_runner.*`, `services.job_runtime.*`
  - `test_job_persistence.py`: added `import services.job_runtime as _jrt` to setUp/tearDown to ensure test DB is consistently applied across all modules; `services.job_runtime.asyncio.create_task`
- Fixed routes_simulation.py to access `_jrt.db` via module reference (not import-time binding) so setUp DB replacement propagates correctly to all route handlers
- Circular import between `job_runtime` and `simulation_runner` resolved via lazy import inside `_drain_scheduler_queue` function body

Files changed:
- `server/app.py` (rewritten — ~1511 → ~120 lines)
- `server/models.py` (new)
- `server/solver_bootstrap.py` (new)
- `server/services/__init__.py` (new)
- `server/services/job_runtime.py` (new)
- `server/services/simulation_runner.py` (new)
- `server/services/update_service.py` (new)
- `server/api/__init__.py` (new)
- `server/api/routes_misc.py` (new)
- `server/api/routes_mesh.py` (new)
- `server/api/routes_simulation.py` (new)
- `server/tests/test_updates_endpoint.py`
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_gmsh_endpoint.py`
- `server/tests/test_api_validation.py`
- `server/tests/test_job_persistence.py`

Tests run:
- `cd server && python3 -m unittest tests.test_updates_endpoint tests.test_gmsh_endpoint tests.test_dependency_runtime` -> 10/10 pass
- `cd server && python3 -m unittest tests.test_api_validation tests.test_job_persistence` -> 29/29 pass
- `npm run test:server` -> 102 pass, 1 skipped (unchanged logic, 2 new tests from prior server count due to discover picking up models test)
- `npm test` -> 81/81 pass

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag logic changed)
- Source tag requirement preserved: yes (no tag logic changed)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changed)

Gate impact:
- Gate A: no change (all existing Gate A checks remain satisfied)
- Gate B: in_progress — `app.py` decomposition condition satisfied; bundle reduction pending Session 9
- Gate C: no change

Known issues / follow-up:
- `routes_simulation.py` uses `import services.job_runtime as _jrt` for direct DB calls; this is intentional to allow test DB injection via `_jrt.db`
- `ResourceWarning: unclosed database` appears in `test_occ_adaptive_preserves_wall_thickness_in_build_call` — pre-existing (mock patching creates temporary SimulationDB instances in prune call); non-blocking

Next session:
- 6 (Frontend lifecycle safety: timers/listeners/URL)

---

#### Session 4: Backend Logging + Exception Tightening
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Added `import logging` + `logger = logging.getLogger(__name__)` to all runtime modules: `app.py`, `solver/deps.py`, `solver/bem_solver.py`, `solver/solve.py`, `solver/solve_optimized.py`, `solver/mesh.py`, `solver/mesh_validation.py`, `solver/symmetry.py`, `solver/directivity_correct.py`, `solver/device_interface.py`, `solver/waveguide_builder.py`
- Configured log level from `MWG_LOG_LEVEL` env var (default `INFO`) via `logging.basicConfig` in `app.py`
- Replaced ALL runtime `print()` calls with structured logging at appropriate levels (`logger.debug`, `logger.info`, `logger.warning`, `logger.error`)
- Retained CLI-path `print()` only in `app.py` `__main__` startup banner and `server/scripts/benchmark_solver.py` (approved CLI report path)
- Exception handling improvements:
  - `run_simulation` broad `except Exception`: added `exc_info=True` + explanatory comment; removed `import traceback` + `traceback.print_exc()` — full traceback now goes to structured logger
  - `_merge_job_cache_from_db` silent `except Exception: pass`: added `logger.debug(...)` instead of swallowing silently
  - Gmsh refinement `except Exception`: added `exc_info=True` with comment (best-effort fallback, many possible Gmsh errors)
  - Symmetry detection `except Exception`: added `exc_info=True` (fallback to full model)
  - Directivity per-frequency `except Exception`: added `exc_info=True`
  - Verbose `prepare_mesh` diagnostics moved to `logger.debug` (suppressed at default INFO level)

Files changed:
- `server/app.py`
- `server/solver/deps.py`
- `server/solver/bem_solver.py`
- `server/solver/solve.py`
- `server/solver/solve_optimized.py`
- `server/solver/mesh.py`
- `server/solver/mesh_validation.py`
- `server/solver/symmetry.py`
- `server/solver/directivity_correct.py`
- `server/solver/device_interface.py`
- `server/solver/waveguide_builder.py`

Tests run:
- `cd server && python3 -m unittest tests.test_solver_hardening tests.test_solver_tag_contract` -> 10/10 pass
- `npm run test:server` -> 100 pass, 1 skipped (unchanged)
- `npm test` -> 81/81 pass

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag logic changed)
- Source tag requirement preserved: yes (no tag logic changed)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changed)

Gate impact:
- Gate A: in_progress — structured logging satisfied; remaining Gate A item (polling lifecycle) pending Session 6
- Gate B: no change
- Gate C: no change

Known issues / follow-up:
- `run_simulation`'s `except Exception` is intentionally a catch-all; now documented with a comment explaining why
- `benchmark_solver.py` CLI script retains `print()` — it is a standalone CLI report tool, approved path

Next session:
- 5 (Backend decomposition: routes + services)

---

#### Session 3: Scheduler Race + HTTP Semantics
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Moved `scheduler_loop_running` read/write inside `jobs_lock` in `_drain_scheduler_queue`: check-and-set now under the same lock as queue transitions; `finally` block also resets flag under lock — eliminates any potential TOCTOU between asyncio tasks or future threading callers
- Fixed `get_results` line ~1069: `status_code=500` → `status_code=404` for "Results not available" (job complete but no stored results — missing resource, not server error)
- Fixed `render_directivity` line ~1249: `status_code=400` → `status_code=422` for "Missing frequencies or directivity data" (validation failure, not generic bad request)
- Added 6 regression tests to `test_api_validation.py` in two new classes:
  - `HttpSemanticsTest`:
    - `test_get_results_missing_stored_results_returns_404` — verifies 404 when `db.get_results` returns None
    - `test_render_directivity_empty_input_returns_422` — verifies 422 for empty frequencies/directivity
    - `test_get_results_unknown_job_returns_404` — verifies 404 for nonexistent job
    - `test_get_job_status_unknown_job_returns_404` — verifies 404 for nonexistent job
  - `SchedulerStateTest`:
    - `test_scheduler_skips_when_already_running` — verifies drain exits immediately when flag is True, job remains in queue
    - `test_scheduler_loop_running_resets_after_empty_queue` — verifies flag is False after drain with empty queue

Files changed:
- `server/app.py`
- `server/tests/test_api_validation.py`

Tests run:
- `cd server && python3 -m unittest tests.test_api_validation tests.test_updates_endpoint` -> 27/27 pass (+6 new)
- `npm run test:server` -> 100 pass, 1 skipped (was 94+1; +6 new tests)
- `npm test` -> 81/81 pass

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag logic changed)
- Source tag requirement preserved: yes (no tag logic changed)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changed)

Gate impact:
- Gate A: in_progress — scheduler race + HTTP semantics satisfied; remaining Gate A item (polling lifecycle) pending Session 6; logging pending Session 4
- Gate B: no change
- Gate C: no change

Known issues / follow-up:
- `render_directivity` catch-all `except Exception` still maps to 500 — acceptable (unexpected errors)
- `run_simulation`'s broad `except Exception` still prints `traceback.print_exc()` to stdout — addressed in Session 4 (logging tightening)

Next session:
- 4 (Backend logging + exception tightening)

---

#### Session 2: Job Persistence Failure Safety
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Reordered results persistence in `run_simulation`: `db.store_results()` now called BEFORE `_set_job_fields(status="complete", ...)` — eliminates false-complete state on server crash between the two calls
- Wrapped `db.store_results()` in explicit try/except: on failure, job transitions to `error` with safe diagnostic message ("Results could not be saved. The simulation completed but persistence failed.") — no exception class or traceback leaked to API
- Wrapped `db.store_mesh_artifact()` in explicit try/except: artifact failure is non-fatal — simulation continues to completion, `has_mesh_artifact` is corrected to `False`, warning printed to stdout
- Added 3 regression tests to `test_api_validation.py` in `JobPersistenceFailureSafetyTest`:
  - `test_results_persistence_failure_leaves_error_not_complete` — verifies status is "error" not "complete" when `db.store_results` raises
  - `test_results_persistence_failure_error_message_is_safe` — verifies error_message contains no traceback or OSError class name
  - `test_mesh_artifact_persistence_failure_does_not_abort_simulation` — verifies simulation reaches "complete" and `has_mesh_artifact=False` when `db.store_mesh_artifact` raises

Files changed:
- `server/app.py`
- `server/tests/test_api_validation.py`

Tests run:
- `cd server && python3 -m unittest tests.test_api_validation` -> 18/18 pass (+3 new)
- `cd server && python3 -m unittest tests.test_dependency_runtime` -> 4/4 pass
- `npm run test:server` -> 94 pass, 1 skipped (was 91+1; +3 new tests)
- `npm test` -> 81/81 pass

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag logic changed)
- Source tag requirement preserved: yes (no tag logic changed)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changed)

Gate impact:
- Gate A: in_progress — persistence failure safety satisfied; remaining Gate A items (polling lifecycle, logging, status codes) pending
- Gate B: no change
- Gate C: no change

Known issues / follow-up:
- `run_simulation`'s `except Exception` broad catch (line ~1475) still prints `traceback.print_exc()` to stdout — addressed in Session 4 (logging tightening)
- `db.store_mesh_artifact` failure prints to stdout (not structured logging) — same Session 4 scope

Next session:
- 3 (Scheduler race + HTTP semantics)

---

#### Session 1: Empty-Mesh 422 Hardening
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Added `num_vertices == 0` guard in `prepare_mesh` after vertices reshape; raises `ValueError` with actionable message before any `np.max` call
- Added `num_triangles == 0` guard in `prepare_mesh` after indices reshape; raises `ValueError` with actionable message
- Both guards prevent `ValueError: zero-size array to reduction operation maximum which has no identity` (numpy internal) from reaching API consumers
- Added 3 regression tests to `test_mesh_validation.py`:
  - `test_empty_vertices_raises_actionable_error` — verifies ValueError, "no vertices" in message, no numpy internals
  - `test_empty_indices_raises_actionable_error` — verifies ValueError, "no triangles" in message, no numpy internals
  - `test_empty_mesh_error_message_is_actionable` — verifies message contains actionable field name

Files changed:
- `server/solver/mesh.py`
- `server/tests/test_mesh_validation.py`

Tests run:
- `cd server && python3 -m unittest tests.test_mesh_validation` -> 7/7 pass (4 pre-existing + 3 new)
- `cd server && python3 -m unittest tests.test_api_validation` -> 15/15 pass
- `npm run test:server` -> 91 pass, 1 skipped (was 88+1; +3 new tests)
- `npm test` -> 81/81 pass

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no tag logic changed)
- Source tag requirement preserved: yes (no tag logic changed)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changed)

Gate impact:
- Gate A: in_progress — empty-mesh 422 condition satisfied; remaining Gate A items (persistence, polling, logging, status codes) pending
- Gate B: no change
- Gate C: no change

Known issues / follow-up:
- The empty-mesh guards raise ValueError which maps to 422 via the synchronous `/api/mesh/generate-msh` path; in the async `/api/solve` path the job transitions to `error` state (by design — job already queued). This is acceptable and consistent with the existing architecture.
- `run_simulation`'s `except Exception` broad catch (line 1458) still prints `traceback.print_exc()` to stdout — this is addressed in Session 4 (logging tightening).

Next session:
- 2 (Job persistence failure safety)

---

#### Session 0: Baseline and Guardrails
- Date: 2026-02-24
- Agent: claude-sonnet-4-6
- Branch/PR/Commit: main
- Status: done
- Planned scope changes: none

Completed work:
- Recorded baseline metrics (JS 81/81, server 88/88 skipped=1, bundle 631 KiB)
- Corrected JS test count in plan (was 78, actual 81)
- Installed `eslint`, `@eslint/js`, `globals`, `prettier` as devDependencies
- Added `eslint.config.js` (flat config, warning-only, browser+node globals, `no-undef` false positives suppressed)
- Added `.prettierrc` (singleQuote, tabWidth:2, printWidth:100, trailingComma:es5)
- Added `.prettierignore` (dist, bundle.js, node_modules, server)
- Added `scripts/check-bundle-size.js` (intermediate 550 KiB / target 500 KiB gate)
- Added `npm run lint`, `npm run format:check`, `npm run bundle:check` scripts to `package.json`

Files changed:
- `package.json`
- `eslint.config.js` (new)
- `.prettierrc` (new)
- `.prettierignore` (new)
- `scripts/check-bundle-size.js` (new)
- `docs/PRODUCTION_READINESS_PLAN.md`

Tests run:
- `npm test` -> 81/81 pass
- `npm run test:server` -> 88 pass, 1 skipped
- `npm run lint` -> 0 errors, 36 warnings (warning-only baseline)
- `npm run format:check` -> 75 files need formatting (expected; no auto-fix applied)
- `npm run bundle:check` -> FAIL 631.5 KiB > 550 KiB (expected; bundle reduction deferred to Session 9)

Contract checks:
- Surface tags 1/2/3/4 preserved: yes (no logic changes)
- Source tag requirement preserved: yes (no logic changes)
- `/api/mesh/build` non-`.geo` semantics preserved: yes (no logic changes)

Gate impact:
- Gate A: no change
- Gate B: no change (bundle gate script now runnable; bundle still exceeds threshold)
- Gate C: no change

Known issues / follow-up:
- 36 ESLint warnings remain in `src/`; address incrementally per session
- 75 JS files need Prettier formatting; defer auto-fix to a dedicated cleanup step
- Bundle is 631.5 KiB — 81.5 KiB above 550 KiB intermediate gate; reduction work in Session 9

Next session:
- 1 (Empty-mesh 422 hardening)

---

## Final Definition of Done
- JS tests pass: `npm test`.
- Server tests pass: `npm run test:server`.
- No docs claim `/api/mesh/build` returns `.geo`.
- ABEC bundle output remains parity-safe.
- Solver support matrix docs remain aligned with `server/solver/deps.py`.
- Gate A is fully green; Gate B is green or documented with explicit, approved risk.
