# Apple Silicon performance validation

Measured 2026-08-07/08 from `windows-support` at `1791083`, before the local
changes described in the last section. The host was a 10-core Apple M1 Max with
64 GiB RAM, macOS 26.5.2, Python 3.13.1, and Node 24.13.0. These are macOS
measurements, not acceptance thresholds and not confirmations of the Windows
numbers in `WINDOWS-PERFORMANCE.md`.

## Baseline suites

- `node --test shared/js/frame.test.mjs`: 43/43 passed in 88.9 ms.
- `cd frontend && npm ci`: 2.44 s.
- `npm test`: 49 files and 298 tests passed; Vitest reported 8.8 s and the
  command took 11.0 s wall time.
- `npm run build`: Vite reported 956 ms and the command took 3.30 s wall time.
  Its existing chunk-size warnings remain.
- `python -m pytest server/tests -q`: on the first clean checkout, three tests
  that request the compiled SPA failed because `frontend/dist` did not exist
  yet. After the requested frontend build, 883 passed and 2 skipped in 23.21 s
  (23.8 s wall time), with one warning. The larger count than the Windows run
  comes from locally discoverable v1 parity fixtures. No assertion was changed
  or weakened.

## SQLite WAL and connection reuse

The benchmark made 150 real `persist_runtime_update` calls, each containing one
runtime update and one stage event, on APFS. Times are per call.

| Configuration | Mean | p95 |
| --- | ---: | ---: |
| DELETE journal, fresh connection | 0.566 ms | 0.757 ms |
| DELETE journal, reused connection | 0.420 ms | 0.518 ms |
| WAL, fresh connection | 0.932 ms | 1.326 ms |
| Product WAL path, reused connection | 0.043 ms | 0.061 ms |
| Product path plus durable log fsync | 0.145 ms | 0.198 ms |

WAL without reuse was slower here too. The measured benefit comes from the
persistent per-thread connection. After `close()`, the registry contained zero
connections and all nine captured owner/worker connections rejected further
SQL as closed. This checks the leak explicitly because macOS does not expose it
through the Windows open-handle symptom.

## Solver warmup

AUTO resolves to the packaged Metal engine on this machine. The Metal status
probe took about 46.7 ms. BEMPP is installed and its probe took about 224 ms,
but its OpenCL inventory contains the M1 Max GPU rather than the CPU device
hard-coded by the Windows warmup.

The inherited warmup returned control in 0.107 ms, then spent 5.518 s in its
background thread before failing with `OpenCL cpu device could not be
initialized`. It therefore neither skipped cleanly nor warmed the engine AUTO
would use.

Fresh-process one-frequency Metal solves showed a real first-call cost. Three
trials using the application `complex_k` formulation measured first/second
pairs of 152.4/76.0, 150.1/82.1, and 157.6/76.1 ms: gaps of 76.4, 68.1, and
81.5 ms (1.83-2.07x). The warmup now follows AUTO priority, runs a real Metal
`complex_k` solve on Apple Silicon, falls back to BEMPP elsewhere, and skips
only when neither physical engine is available. Scheduling still returned in
0.091 ms; the first measured background Metal warmup took 629.9 ms including
cold imports, status probing, and the solve.

## STL and mesh integrity

A fixed OSSE design was exported from `3ceffc2~1` and from `1791083`.

- Both binary STL files contained 12,400 triangles and were exactly 620,084
  bytes. Every byte matched.
- Export time was 221.697 ms before and 2.803 ms after: 79.1x on this host.
- On a fixed 12,564-triangle mesh, `mesh_integrity_report` was exactly equal
  before and after. Time was 292.515 ms before and 25.837 ms after: 11.3x.

## Real browser viewport

The application was exercised in the in-app browser against the real local
server. A WebGL canvas rendered the generated surface, including hard-boundary
edge mode. Browser isolation did not expose the WebGL renderer string, so this
records a real rendered Apple Silicon browser session without claiming a
specific GPU-driver identity.

- A short mouth-radius drag changed the displayed model from revision 1 to 6;
  its final fine server evaluation was 1,388 ms.
- A sustained 1.12 s drag committed revisions 6 through 122, about 104 UI/input
  revisions per second.
- During a separately sampled 2.2 s dense edge-mode drag, the displayed coarse
  mesh moved from revision 123 to revision 258 after 1.636 s. No subsequent
  frame appeared before 2.187 s. The observed displayed geometry rate in that
  sample was therefore about 0.6 frame/s, not 30 frame/s. The first new coarse
  frame itself reported 191.91 ms generation time and about 5,632 vertices per
  surface. Fine frames contained about 24,832 inner and 24,576 outer vertices
  and took roughly 1.4 s on the server.
- On 60,000 synthetic vertices in Node/V8, the old `Box3` bounds calculation
  averaged 0.2320 ms and the scalar loop averaged 0.2172 ms, a 1.07x change.
  Full current `frameToScene` averaged 0.3440 ms.
- Feature-edge extraction on a synthetic 40,000-vertex, 79,202-triangle surface
  averaged 66.76 ms (49.49 ms minimum, about 83.15 ms maximum/p95).

The `SurfaceMesh` memo change does not eliminate edge extraction for actual new
preview frames: websocket decoding creates new typed arrays for every frame, so
all three memo dependencies change. It only avoids recomputation if the same
typed arrays survive an unrelated render. This remains a real viewport gap;
the sampled bottleneck also includes server geometry generation rather than
being a GPU-only problem.

## Startup and quit

- Two consecutive `bootstrap.py --check` runs took 49 and 46 ms. The stamp
  recorded 53 distributions, proving the POSIX site-packages fast path engaged.
- The duplicate FastAPI/Uvicorn import probe in `launch-wg2.command` took 246,
  240, 241, 235, and 237 ms. It is now retained only for an explicit
  `WG2_PYTHON` override, because the repository interpreter has already passed
  the bootstrap probe.
- Three automated launches reached `/health` in 468.8, 479.4, and 476.6 ms
  (474.9 ms mean), and `/` in 479.8, 490.9, and 499.2 ms (490.0 ms mean).
- A quit sent immediately after health waited roughly 550-561 ms for background
  warmups. After warmups had settled, three SIGTERM-to-exit trials took 326.9,
  337.8, and 330.8 ms (331.8 ms mean). That is plainly slower than the 20 ms
  Windows measurement.

The quit investigation found a macOS correctness bug: every Gmsh
initialize/finalize session changed the process's native SIGTERM disposition
even though Python continued to report the application callable. The original
process died with signal exit code `-15` in 3-5 ms, bypassing application
shutdown. The launcher now owns Uvicorn's capture context and the Gmsh worker
re-arms registered handlers on the main loop after both native boundaries.
Actual SIGTERM and SIGINT runs now exit with code 0 and log application shutdown,
lock release, and log flushing. An early invalid-port path that had already
started the logging listener now flushes it as well.

## Windows-only console code

On macOS, 10,000 calls each to `disable_quick_edit`,
`install_ctrl_close_handler`, and `harden_console` (30,000 calls total) took
3.470 ms. No callback ran, no handler reference was retained, and no call
raised. The module is a genuine no-op here.

## Postprocess work completed on Apple Silicon

The remaining result-construction loops were measured on 401 frequencies, a
three-plane 181-angle directivity result, and a 37-by-72 balloon (1,068,264
floating-point cells).

| Stage | Scalar baseline | Vectorized | Change |
| --- | ---: | ---: | ---: |
| Directivity contract mapping | 97.943 ms | 29.179 ms | 3.4x |
| Finite balloon JSON conversion | 377.424 ms | 24.436 ms | 15.4x |
| Beam-shape analysis | 859.259 ms | 321.870 ms | 2.7x |

The directivity mapper now constructs the contract array by plane, finite
numeric NumPy arrays bypass the recursive element sanitizer, and all 144 beam
rays are interpolated and crossed as arrays. Differential tests retain the old
scalar algorithms as test-only oracles and cover non-finite values, shortened
native frequency axes, and a balloon with one invalid cell. For the full
benchmark fixtures, compact JSON byte lengths and SHA-256 hashes were identical
before and after for directivity, sanitized balloon data, and the beam-shape
summary.
