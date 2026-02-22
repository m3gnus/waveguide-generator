# Simulation Management Implementation Spec (Agent-Execution Ready)

Last updated: February 22, 2026

## 0. Purpose

This document is the implementation spec for adding robust simulation job management:
- multi-job queueing
- backend persistence
- browser session recovery

This version is intentionally deterministic so an AI coding agent can execute it without guessing.

If this document conflicts with runtime behavior, code is source of truth, and this file must be updated.

---

## 1. Current Baseline (Verified)

### Backend (`server/app.py`)
- Jobs are stored only in-memory (`jobs` dict).
- `POST /api/solve` creates a job with `status="queued"`, then immediately starts `asyncio.create_task(run_simulation(...))`.
- There is no scheduler and no max-concurrency cap.
- `POST /api/stop/{job_id}` supports queued/running cancellation.
- Results and `.msh` artifact are only in-memory and are lost on restart.

### Frontend (`src/ui/simulation/`)
- UI tracks one active job via `panel.currentJobId`.
- UI uses one polling timer via `panel.pollInterval`.
- UI stores only one result via `panel.lastResults`.
- Refresh/reload loses active simulation context.
- `src/solver/status.js` has map-based status code but is not wired into runtime.

---

## 2. Implementation Contract

### 2.1 MUST
- Keep canonical surface-tag contract unchanged: `1=wall`, `2=source`, `3=secondary`, `4=interface`.
- Keep source-tag requirement unchanged: tag `2` must exist in simulation payload.
- Keep `POST /api/solve`, `GET /api/status/{id}`, `POST /api/stop/{id}`, `GET /api/results/{id}`, `GET /api/mesh-artifact/{id}` behavior backward compatible.
- Add `GET /api/jobs` and `DELETE /api/jobs/{id}` exactly as specified below.
- Use SQLite at `server/data/simulations.db`.
- Use `limit` + `offset` pagination for `GET /api/jobs` (no cursor in v1).
- Use localStorage key `ath_simulation_jobs:v1`.

### 2.2 MUST NOT
- Must not change mesh contract semantics or ABEC parity contract.
- Must not introduce WebSocket/SSE in this implementation.
- Must not claim `/api/mesh/build` returns `.geo`.
- Must not store full simulation results in localStorage.

### 2.3 SHOULD
- Keep route handlers thin by moving persistence logic into a dedicated module.
- Add targeted tests in each phase before full regression runs.

---

## 3. Fixed Design Decisions (No Ambiguity)

1. Backend storage engine: SQLite (`sqlite3` stdlib), file `server/data/simulations.db`.
2. Persistence module path: `server/db.py`.
3. Jobs endpoint pagination: `limit` and `offset`.
4. Queue policy: FIFO.
5. Default concurrency: `max_concurrent_jobs = 1`.
6. Polling:
- when any job is `queued` or `running`: 1 second
- when all jobs are terminal: 10 seconds
- failure backoff: 1s -> 2s -> 5s -> 10s (cap 10s)
7. Frontend local index capacity: last 50 jobs.
8. Backend retention defaults:
- keep terminal jobs for 30 days
- keep at most 1000 terminal jobs

---

## 4. Job Lifecycle State Machine

States:
- `queued`
- `running`
- `complete`
- `error`
- `cancelled`

Allowed transitions:

| From | To | Trigger |
|---|---|---|
| `queued` | `running` | Scheduler starts job |
| `queued` | `cancelled` | User stops before start |
| `running` | `complete` | Solver success |
| `running` | `error` | Exception/runtime failure |
| `running` | `cancelled` | Cooperative cancel |

Rules:
- Terminal states: `complete`, `error`, `cancelled`.
- Terminal jobs cannot transition to other statuses.
- `DELETE /api/jobs/{id}` is allowed only for terminal jobs and returns `409` for `queued` or `running`.

---

## 5. API Contract (v1)

### 5.1 New Endpoint: `GET /api/jobs`

Query parameters:
- `status` optional; comma-separated subset of `queued,running,complete,error,cancelled`
- `limit` optional integer; default `50`, max `200`
- `offset` optional integer; default `0`

Response `200`:

```json
{
  "items": [
    {
      "id": "9c637d72-9fd2-4aa0-98ce-891ec9ab40cf",
      "status": "running",
      "progress": 0.42,
      "stage": "bem_solve",
      "stage_message": "Solving BEM frequencies",
      "created_at": "2026-02-22T18:20:31.305018",
      "queued_at": "2026-02-22T18:20:31.305018",
      "started_at": "2026-02-22T18:20:31.498112",
      "completed_at": null,
      "config_summary": {
        "formula_type": "R-OSSE",
        "frequency_range": [200, 20000],
        "num_frequencies": 48,
        "sim_type": "2"
      },
      "has_results": false,
      "has_mesh_artifact": true,
      "label": null,
      "error_message": null
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### 5.2 New Endpoint: `DELETE /api/jobs/{job_id}`

Success `200`:

```json
{
  "deleted": true,
  "job_id": "9c637d72-9fd2-4aa0-98ce-891ec9ab40cf"
}
```

Error behavior:
- `404` if missing job id
- `409` if job is `queued` or `running`

### 5.3 Existing Endpoints (unchanged externally)
- `POST /api/solve` -> returns `{ "job_id": "..." }`
- `GET /api/status/{job_id}` -> unchanged shape (`status`, `progress`, `stage`, `stage_message`, `message`)
- `POST /api/stop/{job_id}` -> unchanged; still supports queued/running
- `GET /api/results/{job_id}` -> unchanged externally
- `GET /api/mesh-artifact/{job_id}` -> unchanged externally

---

## 6. SQLite Schema

Use `PRAGMA user_version = 1`.

```sql
CREATE TABLE IF NOT EXISTS simulation_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('queued','running','complete','error','cancelled')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  queued_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  progress REAL NOT NULL DEFAULT 0.0,
  stage TEXT,
  stage_message TEXT,
  error_message TEXT,
  cancellation_requested INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL,
  config_summary_json TEXT NOT NULL,
  has_results INTEGER NOT NULL DEFAULT 0,
  has_mesh_artifact INTEGER NOT NULL DEFAULT 0,
  label TEXT
);

CREATE TABLE IF NOT EXISTS simulation_results (
  job_id TEXT PRIMARY KEY,
  results_json TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS simulation_artifacts (
  job_id TEXT PRIMARY KEY,
  msh_text TEXT,
  FOREIGN KEY(job_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_simulation_jobs_status_created
  ON simulation_jobs(status, created_at DESC);
```

Storage policy:
- `config_json` stores full submit payload for reproducibility.
- `config_summary_json` stores lightweight list payload for `GET /api/jobs`.
- Results and artifact payloads are split into separate tables to keep list queries fast.

---

## 7. Scheduler and Cancellation Semantics

Scheduler algorithm:
1. New job submission writes status `queued` and enqueues job id.
2. Scheduler drain loop starts jobs while `running_count < max_concurrent_jobs`.
3. On completion/error/cancel, scheduler drains queue again.
4. FIFO ordering must be preserved.

Cancellation behavior:
- If `queued`: mark `cancelled`, remove from queue.
- If `running`: mark `cancelled`, set `cancellation_requested = 1`.
- Solver remains cooperative; if a cancelled running job returns later, discard results.

Startup recovery:
- Jobs left as `running` at process start become `error` with message `Server restarted during execution`.
- Jobs left as `queued` are re-enqueued and resumed.

---

## 8. Frontend Runtime Contract

Replace single-job fields with a tracked job map:
- `panel.jobs: Map<string, JobEntry>`
- `panel.activeJobId: string | null`
- `panel.resultCache: Map<string, ResultPayload>`

`JobEntry` shape:

```js
{
  id,
  status,
  progress,
  stage,
  stageMessage,
  createdAt,
  queuedAt,
  startedAt,
  completedAt,
  configSummary,
  hasResults,
  hasMeshArtifact,
  label,
  errorMessage
}
```

LocalStorage contract:
- Key: `ath_simulation_jobs:v1`
- Value:

```json
{
  "version": 1,
  "saved_at": "2026-02-22T18:20:31.305018",
  "items": []
}
```

Reconciliation on load:
1. Read local index.
2. Fetch backend jobs (`GET /api/jobs?limit=200&offset=0`).
3. Merge by `id`; backend status wins.
4. Local running/queued jobs missing on backend become local `error` with restart/loss note.
5. Persist merged snapshot.

---

## 9. Phase Plan With Hard Gates

Do not start the next phase until the current phase gate passes.

### Phase 1: Queue + Multi-Job UI (in-memory backend)

Required files:
- `server/app.py`
- `src/ui/simulation/SimulationPanel.js`
- `src/ui/simulation/actions.js`
- `src/ui/simulation/jobTracker.js` (new)

Deliverables:
- FIFO queue with `max_concurrent_jobs=1`.
- `GET /api/jobs` endpoint backed by in-memory state.
- UI job list with per-job status/progress/cancel/view.
- Existing single-job endpoints still functional.

Gate tests:
- `node --test tests/simulation-flow.test.js`
- `node --test tests/ui-behavior.test.js`
- `cd server && python3 -m unittest tests.test_api_validation`
- `cd server && python3 -m unittest tests.test_dependency_runtime`
- `cd server && python3 -m unittest tests.test_gmsh_endpoint`
- `cd server && python3 -m unittest tests.test_occ_resolution_semantics`
- `cd server && python3 -m unittest tests.test_updates_endpoint`

### Phase 2: SQLite Persistence + Delete Endpoint

Required files:
- `server/db.py` (new)
- `server/app.py`
- `server/tests/test_job_persistence.py` (new)

Deliverables:
- SQLite-backed job/result/artifact persistence.
- Startup recovery behavior for `running` and `queued`.
- `DELETE /api/jobs/{id}` with `409` on non-terminal jobs.

Gate tests:
- `cd server && python3 -m unittest tests.test_job_persistence`
- `cd server && python3 -m unittest tests.test_api_validation`
- `cd server && python3 -m unittest tests.test_mesh_validation`
- `cd server && python3 -m unittest tests.test_solver_tag_contract`
- `cd server && python3 -m unittest tests.test_solver_hardening`
- `cd server && python3 -m unittest tests.test_dependency_runtime`
- `cd server && python3 -m unittest tests.test_gmsh_endpoint`
- `cd server && python3 -m unittest tests.test_occ_resolution_semantics`
- `cd server && python3 -m unittest tests.test_updates_endpoint`

### Phase 3: Frontend Session Recovery

Required files:
- `src/ui/simulation/jobTracker.js`
- `src/ui/simulation/SimulationPanel.js`
- `src/ui/simulation/actions.js`
- `src/ui/simulation/results.js`

Deliverables:
- localStorage job index persistence (`ath_simulation_jobs:v1`).
- deterministic local/backend reconciliation.
- results view for any completed job id.

Gate tests:
- `node --test tests/simulation-flow.test.js`
- `node --test tests/ui-behavior.test.js`
- `node --test tests/simulation-job-tracker.test.js` (new)
- `node --test tests/simulation-reconciliation.test.js` (new)

### Phase 4: Optional Product Polish

Optional features:
- user labels
- result comparison
- history export JSON
- UI control for concurrency cap

No Phase 4 work before Phases 1-3 are complete and stable.

---

## 10. Full Regression Gate (Before Merge)

From repo root:
- `npm test`
- `npm run test:server`
- `npm run test:abec`
- `npm run test:ath`

---

## 11. Risks and Mitigations

- Risk: solver not safe for true parallel jobs.
- Mitigation: keep default concurrency at `1`; treat values `>1` as advanced and optional.

- Risk: DB growth from large results.
- Mitigation: retention pruning (30 days / 1000 jobs), split heavy payload tables.

- Risk: stale local history after manual DB reset.
- Mitigation: backend remains source of truth during reconciliation.

---

## 12. Out of Scope

- WebSocket/SSE push status transport.
- Distributed queue workers.
- Preemptive solver interruption inside numerical kernels.
- Any mesh/tag contract changes.
- Any ABEC parity contract changes.

---

## 13. Definition of Done

- Users can queue, monitor, cancel, and review multiple jobs.
- Backend restart no longer loses completed history.
- Browser reload reconnects and restores job history context.
- Existing endpoints remain compatible.
- Canonical mesh/tag invariants remain intact.
- Full regression gate passes.
