# Project Review Fixes

Last updated: 2026-02-08

Scope reviewed: tracked frontend, backend, scripts, and docs.

Validation run during review:
1. `npm test` (pass: 28 tests)
2. `npm run test:server` (pass: 6 tests)
3. `npm run build` (pass, with bundle size warnings)

---

## P0 (Functional Correctness)

### P0.1 Stop endpoint does not actually stop solver work

Evidence:
1. `server/app.py:171` marks job as `cancelled`.
2. `server/app.py:234`..`server/app.py:290` continues running solve and later can set status to `complete`.
3. `src/ui/simulation/actions.js:43` immediately shows "Simulation cancelled" in UI.

Risk:
1. Cancelled jobs can continue consuming CPU and overwrite final job status/results.

Fix:
1. Add per-job cancellation tokens (for example `asyncio.Event`) stored with each job.
2. Check token before expensive phases and before writing completion.
3. Thread cancellation signal into solver loops (frequency iteration/progress callback).
4. Add backend test that proves cancelled jobs never transition to `complete`.

---

### P0.2 Backend "connected" check ignores solver availability

Evidence:
1. `src/solver/index.js:109` treats any `200 /health` as connected.
2. `server/app.py:123` returns solver state (`bempp-cl` or `unavailable`), but frontend does not use it.
3. In this environment `SOLVER_AVAILABLE` is `False`, so solve requests return `503` from `server/app.py:146`.

Risk:
1. UI reports backend connected, then run fails instead of cleanly falling back to mock behavior.

Fix:
1. Parse `/health` JSON and require solver availability for "connected" state.
2. If backend is up but solver unavailable, disable run button or explicitly switch to mock mode.
3. Add test for "backend reachable but solver unavailable" UI behavior.

---

### P0.3 Production build artifact is not part of runtime path

Evidence:
1. Build output is `dist/bundle.js` in `webpack.config.js:12`.
2. Runtime HTML still loads source directly: `index.html:204` (`src/main.js`).
3. Dev server serves repository root: `scripts/dev-server.js:8`.

Risk:
1. Build verification does not validate what users actually run.
2. Source + `node_modules` are served directly in dev runtime, making deploy path ambiguous.

Fix:
1. Define one production runtime path (bundle-based) and one dev path.
2. Serve `dist/` in production and wire HTML to built bundle.
3. Add a smoke test that serves built assets and verifies app boot.

---

## P1 (Reliability, Security, Operations)

### P1.1 CORS policy is too broad for non-local use

Evidence:
1. `server/app.py:27` uses `allow_origins=["*"]` with credentials enabled.

Fix:
1. Read allowed origins from env and default to localhost origins in development.
2. Keep wildcard off by default.

---

### P1.2 Setup scripts are inconsistent with current backend dependency path

Evidence:
1. Backend imports `bempp_cl.api` in `server/solver/deps.py:10`.
2. `server/start.sh:24` checks `import bempp.api` (different module path).
3. `setup.sh:32` uses `npm install` and `setup.sh:62` uses global `pip3`, not project `.venv`.

Fix:
1. Align dependency checks with `bempp_cl.api`.
2. Update setup script to use `npm ci` and project-local `.venv` commands.
3. Keep README and scripts aligned to one canonical setup flow.

---

### P1.3 In-memory job store has no retention policy

Evidence:
1. `server/app.py:34` stores jobs in process memory.
2. No cleanup path for finished/failed/cancelled jobs.

Fix:
1. Add TTL cleanup for completed terminal jobs.
2. Include max retained jobs and metrics logging.
3. If multi-user/long-running deployment is planned, move to Redis or database.

---

### P1.4 Gmsh refinement path should be concurrency-guarded

Evidence:
1. `server/solver/mesh.py:31` and `server/solver/mesh.py:148` initialize/finalize Gmsh per call.
2. API can schedule concurrent jobs via `asyncio.create_task` in `server/app.py:166`.

Fix:
1. Add a process-level lock around Gmsh operations or single-worker mesh refinement queue.
2. Add test coverage for concurrent solve submissions with `use_gmsh=true`.

---

## P2 (Performance, DX, Maintainability)

### P2.1 Frontend bundle is large and should be split

Evidence:
1. Build reports `bundle.js` ~625 KiB and warns about entrypoint size.

Fix:
1. Code-split simulation/chart/export modules behind tab/action boundaries.
2. Lazy-load heavy optional features.
3. Track bundle budget in CI.

---

### P2.2 Test coverage is good for contracts but thin for runtime edge cases

Gaps:
1. No cancellation race test (P0.1).
2. No health/availability UI test (P0.2).
3. No production-build smoke test (P0.3).
4. No retention/cleanup tests for job lifecycle.

Fix:
1. Add focused regression tests for each gap and gate in CI.

---

### P2.3 Documentation and runtime messaging drift

Examples:
1. `src/solver/index.js` header still states "mock/deferred" while real backend client path exists.
2. `fixes.md` previously contained stale counts (`npm test` now runs 28 tests).

Fix:
1. Refresh docs/comments to match current architecture and expected fallback behavior.
2. Keep one "source of truth" section for run/test status and update it in release checklist.
