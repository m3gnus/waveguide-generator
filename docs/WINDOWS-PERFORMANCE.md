# Windows performance work — 2026-08-07

A pass over everything between double-clicking the launcher and reading a
result, on the machine described in
[WINDOWS-VALIDATION.md §1](WINDOWS-VALIDATION.md) (AMD Ryzen 7 5825U, 12
logical processors, 16 GB, QEMU/KVM guest with no hardware 3D acceleration).

Every number below was measured on that machine, before and after, with the
scripts described alongside each item. Where something was *not* done, it says
so rather than being quietly dropped.

---

## 1. Startup: 3.85 s → about 1.2 s of launcher preamble removed

| Stage | Before | After | How |
|---|---:|---:|---|
| `bootstrap.py --check` | 1,956 ms | **158 ms** | evidence stamp, §1.1 |
| `-c "import fastapi, uvicorn"` probe | 833 ms | **0 ms** | skipped unless `WG2_PYTHON` overrides, §1.2 |
| `import server.app` | 1,415 ms | **1,020 ms** | deferred meshio, §1.3 |
| cold start → `/health` 200 | 1,807 ms | 1,747 ms | |
| cold start → `index.html` | 1,934 ms | 1,899 ms | |

The launcher used to spawn **ten** processes on the happy path — each
`.venv\Scripts\python.exe` is a redirector stub, so every logical invocation is
two `CreateProcess` calls plus the antivirus tax. It now spawns four.

### 1.1 The bootstrap check answers from a stamp

`--check` ran on every single launch and always spawned two subprocesses: a
probe importing `packaging.markers` and querying `importlib.metadata` for all
49 locked distributions, and `python -m pip check`. Both exist to catch
somebody `pip install`ing into `.venv` out of band, which is not the happy path.

The stamp at `.venv/.wg2-bootstrap.json` now also records the interpreter's size
and mtime and a hash of the sorted `*.dist-info` directory names, gathered with
one `os.scandir` in about 2 ms. When all of it still matches, `_validate`
returns without spawning anything.

Three properties keep this honest, and each has a test:

- Any mismatch — a new distribution, a replaced interpreter, a stamp from an
  older bootstrap with no evidence recorded — falls through to the full check.
  Verified for real during this work: installing `ruff` into the environment
  made the next `--check` take 2,053 ms, re-validate, re-stamp, and return to
  168 ms afterwards.
- The verification that runs *immediately after installing* never takes the
  fast path (`_validate(..., allow_fast_path=False)`). That check is what
  caught Windows being unable to satisfy the uvloop lock entry, and a stamp
  written seconds earlier by the same run proves nothing.
- A successful slow-path validation records the evidence, so an environment
  installed by an earlier bootstrap becomes fast without being reinstalled.

### 1.2 The duplicate import probe is gone from the happy path

`:python_can_serve` re-imported FastAPI and Uvicorn — 833 ms and two more
processes — to answer a question the bootstrap probe already ends with and that
`launch/serve.py` answers with a real traceback anyway. It now runs only when
`WG2_PYTHON` pins a different interpreter, which is the case its error message
was written for.

### 1.3 meshio no longer loads at startup

`import server.app` pulled `meshio`, and with it `meshio._cli` and `rich`, via
two independent paths: `server/exports/api.py` → `.core`, and
`server/mesh/__init__.py` → `.builder`. Neither runs before somebody exports or
solves. `meshio` is now imported inside the two functions that parse an
artifact, and `server/mesh/__init__.py` re-exports lazily through PEP 562 so
that importing the gmsh worker no longer drags the builder in with it.

### 1.4 gmsh no longer delays the listen socket

`prewarm_gmsh_worker` was registered as a startup handler and *awaited* the
worker's first `gmsh.initialize` round trip. Uvicorn runs the whole lifespan
startup before `loop.create_server`, so that delayed the port opening rather
than merely delaying itself — and `import gmsh` is 283–350 ms in a cold
process, worse on the first launch after a reboot. It now goes through
`BackgroundWarmup` like the other two prewarms, which is the policy
`server/platform/warmup.py` already documented.

### 1.5 Smaller startup items

- `server/app.py` no longer builds a throwaway FastAPI app at import time. It
  was constructing a second preview `ThreadPoolExecutor` and `StaticFiles`
  mount that `serve.py` discarded, and resolving the data directory *before*
  `main()` could honour `--data-dir`. The name is still there for
  `uvicorn server.app:app`, now lazily via a module `__getattr__`.
- `_distribution_version` in the mesh builder is cached. It walked `sys.path`
  for `*.dist-info` twice per mesh build, including on a cache hit, because it
  feeds the cache key.

---

## 2. Interaction: the event loop stops doing avoidable work

### 2.1 WebSocket compression off

`uvicorn.Config` defaults `ws_per_message_deflate=True`, so **every** WebSocket
message was zlib-deflated synchronously on the event loop. The preview socket
carries 170–335 kB geometry frames of float32 at up to 30 Hz while a control is
being dragged: poorly compressible data, on the most latency-sensitive path in
the application, on the one thread that also has to answer every request.
`docs/WS-PROTOCOL.md` already recorded that "localhost bandwidth is free"; the
CPU was not.

### 2.2 gzip at level 1, and assets that stop revalidating

`GZipMiddleware` used Starlette's default `compresslevel=9`. Measured over the
real `frontend/dist` assets (2.45 MB), one cold page load costs **125 ms** of
event-loop CPU at level 9, 90 ms at level 6 and **36 ms** at level 1 — and
level 9 produces 0.3 % fewer bytes than level 6.

Everything under `/assets/` is content-hashed by Vite, so it is now served
`public, max-age=31536000, immutable`, and `index.html` `no-cache`. Verified
end to end, including that a conditional request still answers 304.

### 2.3 Request logging happens once, on a queue

Every request was logged twice — Uvicorn's access log propagating to the root
handlers, plus the application's own timing line — through two handlers that
each flush on every record, on the calling thread. Uvicorn's access log is off
(the application's line has the timings), and both handlers now sit behind a
`QueueHandler`/`QueueListener` so formatting and disk I/O leave the event loop.

### 2.4 Quit is bounded

`timeout_graceful_shutdown` was unset, meaning *wait forever* for connections
to drain — and the SPA holds two long-lived WebSockets, so a browser suspended
by the OS could hang the quit indefinitely. It is now 3 s, and the warmup drain
timeout dropped from 5 s to 1 s. Measured stop: **20 ms**.

---

## 3. The job store: 8.44 ms → 0.05 ms per checkpoint

`server/jobs/store.py` opened a **new** SQLite connection per operation and ran
in the default rollback-journal mode, so every write transaction created and
deleted a journal file — two NTFS metadata operations that antivirus watches
closely. A running solve commits one of these every 150 ms.

Measured over 150 transactions shaped exactly like `persist_runtime_update`:

| configuration | mean | p95 |
|---|---:|---:|
| rollback journal, connection per call (before) | 8.44 ms | 10.00 ms |
| rollback journal, connection reused | 6.69 ms | 8.49 ms |
| WAL, connection per call | 10.51 ms | 12.46 ms |
| **WAL + `synchronous=NORMAL`, connection reused (after)** | **0.05 ms** | **0.10 ms** |

Note the third row: WAL *alone is slower*, because reopening re-establishes the
shared-memory index every time. The win needs both halves.

End to end through the real store, `persist_runtime_update` went from a
measured 8.44 ms of SQLite plus the log flush, to **2.50 ms mean / 3.44 ms p95**
— the remainder is the `os.fsync` on the job log, which is load-bearing: the
durable file length is what lets a retry after a failed commit recognise its
own batch instead of writing it twice.

What that required, and what it cost:

- A `close()` that releases every connection on every thread. On Windows an
  open handle blocks deleting or replacing the file, which both the test
  fixtures and the v1 migration's rollback do. `JobRuntime.shutdown` calls it.
- A `checkpoint()` that folds the WAL back into the `.db` file, called by
  `scripts/migrate_v1.py` before it copies the database as a single file.
- Rollback now removes the `-wal`/`-shm` sidecars before restoring. Leaving
  them was not merely incomplete but dangerous: SQLite would have recovered the
  leftover WAL — containing the state being rolled *back* — on top of the
  restored database.
- `snapshot_jobs` ends its read transaction explicitly. With a long-lived
  connection an unfinished one would pin the WAL forever.

Alongside it:

- **`cancellation_state`** reads two columns for the cancellation checkpoint the
  solver runs once per frequency, from its own thread. `get_job_row` answered
  the same question with `SELECT *` and four `json.loads`, two of which hold a
  whole copy of the design. Measured 0.037 ms → **0.009 ms**, and it no longer
  parses megabytes to find two booleans.
- **`/api/results/{job_id}`** returns the stored JSON text directly. It used to
  parse the multi-megabyte blob, let FastAPI validate it against the return
  annotation, re-serialise it, and then JSON-encode it — four full walks on the
  event loop. The database already holds exactly the bytes the browser wants.
- **Indexes**: added `idx_simulation_jobs_created` (the unfiltered list and the
  WS snapshot both order by `created_at` with no status predicate, and were
  scanning plus building a temp sort); dropped `idx_job_events_id`, which
  duplicated the rowid B-tree and was never chosen by the planner.
- **Retention** asks SQLite for the overflow with `LIMIT -1 OFFSET :max`
  instead of fetching every terminal row and slicing in Python. It runs after
  every job.

---

## 4. Solve: the first solve stops being the slow one

### 4.1 Boot-time solver warmup

[WINDOWS-VALIDATION.md check 12](WINDOWS-VALIDATION.md) measured the problem:
the first bempp solve after every server start spends 17.5 s (OpenCL) to 53.8 s
(numba) inside one uninterruptible block during which **Stop cannot take
effect**, because the engine's only cancellation checkpoint is its
per-frequency progress callback and the first callback does not fire until the
first assembly finishes. The second solve is 0.7–1.0 s. That report named
boot-time warmup as one of the two fixes; this is it.

`server/solver/warmup.py` runs one real single-frequency solve on a checked-in
410-triangle mesh, in a background daemon thread, started from a `create_app`
startup handler. Measured phases in a fresh interpreter here:

| phase | |
|---|---:|
| `import bempp_cl.api` | ~1.7 s |
| first `function_space` (numba JIT) | ~12.4 s |
| four boundary-operator assemblies | ~4.4 s |
| small GMRES solve | ~10.5 s |
| potential operators | ~0.5 s |
| **total** | **26.3 s** |

`start_solver_warmup()` returns in **1 ms**. Four details are deliberate:

- **A daemon thread, not `asyncio.to_thread`.** The default executor's threads
  are joined at interpreter shutdown, so a warmup still compiling kernels when
  the user quits would hold the process open for the rest of its ~26 s. A
  daemon thread is abandoned. The measured 20 ms stop is this working.
- **Off by default in `create_app`**, on only from `launch/serve.py`, so the
  test suite never pays it. `WG2_SOLVER_WARMUP=0` disables it.
- **The solver's own log output is filtered while the warmup thread runs** —
  about a hundred lines of assembler timings and GMRES iterations, per start,
  about a mesh nobody asked to solve. Filtering is by *thread*, so a real solve
  overlapping the warmup still logs in full.
- **The warmup mesh is meshed over the full domain**, not one quadrant, purely
  so the wrapper's mirror-reduced-mesh warning does not appear in the user's
  log on every start describing a problem they do not have.

This does not make Stop *immediate*. It moves the unstoppable window off the
user's first solve. Process-isolated solves remain the only way to make Stop
immediate, and remain deferred (`docs/P6-CUTOVER-PLAN.md`).

### 4.2 The capability probe is cached

`bempp_status()` imported `bempp_cl.api`, enumerated every OpenCL platform and
its devices — loading ICD DLLs — and read distribution metadata, **at the start
of every solve**. It now caches a successful probe, following the pattern
`server/solver/metal.py` already used, and deliberately does not cache failure
so that installing an OpenCL runtime takes effect without a restart.

### 4.3 Parallel sweeps are available, and never silent

The pinned wrapper has a spawn-based parallel sweep behind `SolveConfig.workers`
that v2 was pinning to 1. It now passes the engine's own auto mode, which is
self-limiting: it splits only when each worker would get at least 40
frequencies, because a spawned worker re-imports bempp-cl and re-JITs its
kernels first. Short sweeps therefore stay in one warm process, where they are
dramatically faster.

The caveat is real and is therefore announced in the solve log and the job
metadata before the solve starts, rather than discovered by pressing Stop:
**Stop cannot cancel a frequency already running in a sibling worker process.**
`WG2_SOLVE_WORKERS=1` restores single-process behaviour.

### 4.4 Symmetry resolution is memoized

`resolve_symmetry` measured 1,152 ms first call and 57–150 ms after, and runs
on every submit *and* every committed design edit through
`POST /api/design/symmetry`. It is now memoized on a hash of the mesher
configuration, which is its complete input, in a 32-entry LRU. An edit that
does not move geometry now costs nothing.

---

## 5. Vectorised hot loops

Both were per-triangle Python loops with NumPy calls on 3-vectors *inside* them,
where the per-call dispatch overhead dominated. Both were replaced with array
operations and verified **differentially against the original implementation**,
not merely against their existing tests.

| | before | after | speed-up |
|---|---:|---:|---:|
| `mesh_integrity_report`, 15,842 triangles | 787 ms | 53 ms | **14.9×** |
| `binary_stl`, 37,248 triangles | 1,371 ms | 11 ms | **125×** |

`mesh_integrity_report` runs on the single gmsh worker thread that every other
mesh operation queues behind, and `builder.py` warns at 18,000 triangles, so the
old cost was 0.4–0.8 s of serialized time per build. The 37,248-triangle STL is
the exact export measured in the Windows validation report.

Equivalence was checked on hand-built fixtures covering every rule and their
interactions — out-of-range indices, repeated indices, duplicates by index,
duplicates by geometry only, zero-area faces with distinct indices, non-finite
corners, inconsistent winding, non-manifold edges, symmetry-plane axes — plus
60 randomised meshes for integrity and 20 for STL. The STL output is
**byte-identical**, including the degenerate-normal fallback and the winding
swap.

Two subtleties the vectorisation had to preserve exactly, both now pinned by
tests:

- The rules compose in an order. An out-of-range face is counted and
  contributes nothing else; a repeated-index face is degenerate and contributes
  nothing else; only surviving faces can be duplicates, zero-area, or
  contribute edges.
- A tuple containing NaN never equals another, so a face with a non-finite
  corner was never a *geometric* duplicate. `np.unique` collapses NaNs, so that
  has to be restored explicitly.

---

## 6. Frontend

### 6.1 The Results panel no longer rebuilds every chart on every event

A running solve streams progress, stage and log events continuously. Each one
gave the job list a new identity, which fed the dependency array of every open
chart's `useMemo` — including the heatmap branches, which never read it — and
`EChartRenderer` rebuilds with `notMerge: true`. With the default preference set
(two heatmaps plus three line charts) that is roughly 100–240 ms of main-thread
work per event.

Three changes, each independently sufficient to help:

- `jobsSocket` returns the *same* job object when a patch changes nothing, so
  the array identity survives a repeated progress value; it no longer re-sorts
  on every patch (`created_at` is immutable, so a patch cannot reorder), and
  `Date.parse` is called once per job rather than twice per comparison.
- The event cursor no longer wakes subscribers on its own. It advances on every
  event and is internal resume bookkeeping that nothing in the interface
  displays, but all four subscribers read the whole snapshot through
  `useSyncExternalStore`, which compares by identity.
- Each chart depends only on what it actually reads, and the cross-job label
  list is keyed on the labels rather than on the jobs array.

### 6.2 `contourPolylines` was quadratic

The marching-squares join rescanned the whole remaining segment set on every
join: measured 1.4 ms at 200 segments, 13 ms at 1,400 and **62 ms at 3,000** —
and it runs once per contour level, four levels per heatmap. It now joins
through an endpoint hash quantised to the same 1e-7 tolerance the original
comparison used, checking neighbouring buckets so a pair straddling a boundary
cannot be missed. Verified against the original implementation on a shuffled
800-segment set.

### 6.3 Dragging a splitter no longer writes to disk 60–120 times a second

dockview's `onDidLayoutChange` coalesces only to a microtask, so it fires once
per `pointermove` during a sash drag. The handler walked the layout tree,
`JSON.stringify`d it and wrote it to `localStorage` **synchronously**, while
dockview was relaying out and the WebGL canvas was being resized. It is now
debounced 300 ms with flushes on `beforeunload`, `visibilitychange` and
dispose — the shape `stores/autosave.ts` already used.

### 6.4 Per-frame viewport work

- `frameToScene` computed its bounds with `Box3.expandByPoint`, which costs a
  `Vector3.set` plus two component-wise `Vector3` calls per vertex where six
  comparisons will do. It runs over every vertex of every surface on every
  decoded frame.
- The edges-mode boundary extraction — a per-vertex string key, a `Map` over 3N
  edges, and a feature test per edge — was keyed on the `surface` object, which
  is freshly allocated per decoded frame, so the memo never hit and it re-ran at
  up to 30 Hz. It is now keyed on the typed arrays, which are the
  stable-by-content identity.

---

## 7. What was deliberately not done

1. **The remaining result-mapping and beam-shape loops.**
   `server/solver/result_mapping.py` builds the directivity JSON with a triple
   Python loop (~88,000 two-element lists at 401 frequencies), re-walks it to
   renormalize with an `np.interp` **per row**, and runs a recursive
   finiteness sanitizer over the ~1.07 M-float balloon grid that
   `_balloon_grid_from_result` has already proven finite.
   `server/solver/beam_shape.py` evaluates 144 rays per frequency one at a
   time. Both are the "postprocess" stage users watch at 0.85–0.99 progress and
   both are worth vectorising exactly as §5 did — with the same differential
   verification, because `docs/RESULT-CONTRACTS.md` requires byte-identical
   output. Not attempted here.
2. **Deferring numpy from module scope.** It is ~118 ms of startup, but it is
   imported by `server/preview/core.py`, which the first preview frame needs
   immediately. Deferring it would move the cost into the first drag, which is
   worse.
3. **Bundle loading waterfall.** The lazy Viewport chunk (945 kB) and
   EChartRenderer chunk (616 kB) still only begin downloading after the 708 kB
   entry has parsed and mounted. A `modulepreload` hint injected at build time
   would overlap them. Not done.
   **Done 2026-08-08** — see `MACOS-PERFORMANCE.md` "Viewport frame rate" §5.
   The Viewport chunk went from starting at 100 ms to starting at 11 ms.
4. **Dead dockview theme CSS.** About 123 kB of the 176 kB stylesheet is
   dockview's, of which roughly ten built-in colour themes are unused — the app
   themes `.dv-*` itself. Not stripped.
   **Measured and rejected 2026-08-08** — one of the ten *is* reachable
   (dockview's default `abyss`); the other nine are 64.6 kB raw, 3.5 kB gzipped,
   and unmeasurably cheap to parse. See `MACOS-PERFORMANCE.md` §6 for why doing
   it safely would mean fighting Vite's content hashing.
5. **`JobsPanel` re-render cascade.** Every design revision still re-renders the
   jobs list through a context-identity chain, and each card re-hydrates a full
   design document in its render body. Real, and untouched.
6. **`BaseHTTPMiddleware`.** The origin guard and request log are still
   `BaseHTTPMiddleware`, which wraps every request in an anyio task group and
   pumps the response body through memory object streams. Pure ASGI middleware
   would avoid that for the multi-megabyte results body.
   **Measured and rejected 2026-08-08.** The premise is wrong: the overhead is
   0.7–2.2 ms *per request regardless of body size* — a 4 MiB response costs the
   same as a small JSON one — and on the streamed `/assets/` chunks it was not
   measurable next to gzip. A whole session makes about twenty HTTP requests.
7. **Process-isolated solves.** Explicitly deferred by the cutover plan, and
   still the only thing that would make Stop immediate rather than prompt.

## 8. Verification

```
.venv\Scripts\python -m pytest server\tests -q   → 496 passed, 7 skipped
.venv\Scripts\ruff check server scripts shared   → All checks passed!
node --test shared\js\frame.test.mjs             → 43 pass, 0 fail
cd frontend && npm test                          → 49 files, 298 tests, all passed
cd frontend && npm run build                     → clean
```

`launch/` reports 5 pre-existing `E402`s from its `sys.path` bootstrap and is
not in the documented lint scope; that is unchanged by this work.

Beyond the suites, the whole path was exercised against a running server:
cold start, gzipped `index.html`, the 945 kB chunk served `immutable`, a 304 on
revalidation, `/api/capabilities` resolving to bempp, all three warmups
completing off the startup path, and a 20 ms stop.
