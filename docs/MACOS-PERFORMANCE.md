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

---

# Viewport frame rate — 2026-08-08

Follow-up pass on the same host (10-core Apple M1 Max, macOS 26.5.2, Python
3.13.1, Node 24.13.0), aimed at the question the section above left open: why
the displayed geometry advanced about 0.6 frames per second while the interface
committed about 104 design revisions per second.

## 1. Where the frames were going: the client threw all of them away

Not the GPU, not decoding, not React. `previewSocket.onFrame` rendered a frame
only while its `designRevision` still equalled the store's, which no continuous
gesture can satisfy — the store commits a revision per `pointermove` while
building a preview takes hundreds of milliseconds, so the revision has always
moved on by the time geometry comes back.

Reproduced end to end by driving the real `PreviewProtocol` and the real mesher
with a simulated drag — revisions at 104 Hz, coarse requests at 30 Hz, and the
client's exact acceptance rule:

| design | frames the server produced | frames the client accepted |
|---|---:|---:|
| OSSE with a chamfered enclosure | 21 | **0** |
| the application's own seed R-OSSE | 7 | **0** |

Zero, not "few". In a real browser the occasional acceptance comes only from a
frame landing in a gap between pointer events, which is the measured 0.6/s.

A frame that lags the design by a few revisions is what the stale badge exists
to describe, so it is now rendered. A frame older than a *discontinuous* edit —
undo, redo, load, family switch — is refused, because that geometry answers a
design the user has rejected rather than an earlier point on the same gesture.
Those edits already carry `immediate`, so their revision is the barrier.
`WS-PROTOCOL.md` §1 carries the new rule; conformance case 2 is unchanged and
now has its own test.

After the change the same harness accepts every frame the server produces:

| design | before | after |
|---|---:|---:|
| OSSE with a chamfered enclosure | 0.00 displayed frames/s | **5.68** |
| the application's own seed R-OSSE | 0.00 displayed frames/s | **1.89** |

## 2. Feature-edge extraction, 3.3–6.6x

Edge extraction is the most expensive per-frame client work, and it now runs on
every frame rather than almost never. It built a `Map` keyed on a string per
vertex and a second keyed on a string per edge, and gave every edge a list of
freshly allocated normal tuples. Same algorithm, open-addressed typed-array
tables, per-edge normals as a chain of triangle indices; output verified
byte-identical, with the old implementation kept in the test file as a
differential oracle over hand-built and randomised meshes.

| case | before | after | |
|---|---:|---:|---:|
| 12,820-triangle coarse scene | 6.34 ms | 1.91 ms | 3.3x |
| 59,844-triangle fine scene | 36.20 ms | 6.90 ms | 5.3x |
| 79,202-triangle single surface | 61.67 ms | 9.41 ms | 6.6x |

The last row is the 66.76 ms case measured above.

## 3. One re-render per gesture instead of one per pointermove

`PreviewSocketManager.update` published a new snapshot object unconditionally,
and `onRevision` calls it on every committed mutation only to set `stale` —
which goes true on the first revision of a gesture and stays true. Every one of
those was a full re-render of Viewport, the Canvas, the Scene and every
SurfaceMesh, through `useSyncExternalStore`'s identity comparison. A 60-move
drag now notifies once.

## 4. The camera stopped stealing the user's view

The camera fit key contains the model bounds, so every frame asks for a refit,
and the fit is computed from the last *requested* direction — re-applying it
discards an orbit and snaps back to the preset. That fired about once per pause
before, because frames only landed between gestures; at the new frame rate it
would fire several times a second. An automatic refit now yields to a camera
the user has moved, while a view they ask for takes it back. Confirmed in a
real browser: orbit to a side view, drag Mouth radius 140 → 134 mm, geometry
updates, camera holds.

## 5. Load waterfall

`index.html` linked only the entry bundle, so the 946 kB Viewport chunk was not
requested until the entry had downloaded, parsed and mounted. Measured over
localhost in a real browser:

| | entry JS | Viewport chunk |
|---|---|---|
| before | 11 → 30 ms | 100 → **123 ms** |
| after (build-time `modulepreload`) | 11 → 44 ms | 11 → **51 ms** |

The chart renderer gets `prefetch`, not `modulepreload`: nothing needs it until
a solve has produced results.

## 6. Measured and deliberately not done

- **Stripping dockview's nine unreachable colour themes.** 64.6 kB of the
  176.6 kB stylesheet, 3.5 kB gzipped, and below the browser timer's resolution
  to parse. Doing it safely means doing it before Vite hashes the asset, and
  postcss-import inlines the file inside `vite:css` where no plugin hook can
  reach it; rewriting it in `generateBundle` would leave the content hash naming
  content the file no longer has, while `/assets/` is served `immutable` for a
  year. Not worth that machinery for 3.5 kB.
- **Pure-ASGI origin guard and request log.** `BaseHTTPMiddleware` costs
  0.7–2.2 ms per request, and — contrary to the reasoning in
  `WINDOWS-PERFORMANCE.md` §7.6 — that cost is *fixed*, not proportional to the
  body: a 4 MiB response and a small JSON one cost the same. On the only large
  responses the application actually serves, the streamed `/assets/` chunks, the
  difference was not measurable next to gzip's own 10 ms. A whole session makes
  about twenty HTTP requests.
- **Throttling coarse requests to the server's turnaround.** At 30 Hz against a
  255 ms build, seven of every eight requests are coalesced away. Serialising
  one costs 0.033 ms, so the client spends 1.0 ms per second of drag on requests
  that get dropped, and the server about 13 ms per second validating them. Real,
  but too small to justify the extra state.
- **Dropping curvature from fine frames** (1430 → 1372 ms, 1.04x) and **omitting
  the outer wall** (1372 → **1619 ms**, i.e. slower, and slower at coarse too).

## 7. What is now the wall

Server geometry generation. For the application's own seed design a coarse
preview is 255 ms and a fine preview 1.85 s on this host; the Windows reference
machine is roughly twice as slow. A profile of one coarse build shows 74,496
scalar calls to `hornlab_mesher.profile_formulas.calculate_rosse` and 1.71
million to `eval_param` — pure-Python evaluation inside the pinned mesher, and
about 5.5 profile evaluations for every vertex that reaches the frame.

Nothing at the v2 boundary changes that by much. Sweeping every fidelity knob
`PreviewOptionsV1` exposes, the only combination that helps materially is
chord 0.60 mm + normal step 14° + silhouette 32 + 3,000 vertices: **252 ms →
120.6 ms (2.1x)**, at an achieved chord error of 0.46 mm against today's
0.0068 mm, and with the angular resolution halved. `max_vertices` alone barely
moves it (252 → 218 ms for a quarter of the triangles), which is the tell: the
cost is the adaptive refinement search, not the output. Vectorising the profile
evaluation in the mesher is the change that would matter.

## 8. The mesher-side vectorisation — 2026-08-08

§7 named the change that would matter. This section records it, measured on the
same host. All of it is in `hornlab-waveguide-mesher`, not in this repository:
four commits on top of `8a8f383`, which is both that repository's published
`main` and the mesher pin in `server/requirements-pins.txt`. **None of it
reaches v2 until the mesher is pushed and the pin bumped.**

### What was scalar

A preview build spent about 90% of its time in the profile formulas. Every grid
node re-resolved the entire parameter set for a single point: eleven
`eval_param` lookups, two tangents and the `_rosse_length` solve, none of which
vary along the axial parameter. Four separate per-element loops sat on top of
that — the polar-to-Cartesian conversion, the index buffer, the source cap, and
a `build_point_grid` that spelled its vertices as a flat Python list of 591,360
floats that every consumer immediately parsed back into the array they came
from. FREEFORM had its own: `np.fromiter` over a generator calling the scalar
rounded-rectangle radius once per azimuth.

The four commits resolve parameters once per azimuth and evaluate whole
meridians and rings as arrays. `eval_param` calls on a coarse seed build go from
1.71 million to about 6,000. The scalar entry points are unchanged and remain
the public single-point API, which is what lets the tests use them as
differential oracles.

### Measured, best of five builds per case

| design | coarse before | after | | fine before | after | |
|---|---:|---:|---:|---:|---:|---:|
| seed R-OSSE | 181.7 ms | **29.0 ms** | 6.3x | 1264.8 ms | **126.8 ms** | 10.0x |
| R-OSSE, per-azimuth ATH formulas | 200.1 ms | **28.9 ms** | 6.9x | 1388.7 ms | **128.4 ms** | 10.8x |
| OSSE | 83.4 ms | **23.9 ms** | 3.5x | 510.4 ms | **83.4 ms** | 6.1x |
| ICW, flat baffle | 48.8 ms | **17.8 ms** | 2.7x | 212.3 ms | **63.7 ms** | 3.3x |
| FREEFORM, rounded rectangle | 68.1 ms | **34.4 ms** | 2.0x | 327.7 ms | **135.8 ms** | 2.4x |
| FREEFORM, circular | 51.9 ms | **20.7 ms** | 2.5x | 259.4 ms | **83.9 ms** | 3.1x |
| LOOKUP | 39.6 ms | **16.1 ms** | 2.5x | 200.3 ms | **67.4 ms** | 3.0x |

Vertex and triangle counts are unchanged in every row. §7 recorded 255 ms and
1.85 s for the seed design from an earlier session; re-measuring the same pin in
the same session as the "after" gives 181.7 ms and 1264.8 ms, so the ratios above
are the honest ones and §7's absolute numbers were the noisier reading.

For the seed design the server can now generate about 34 coarse frames per
second where it generated about 5.5, and a fine preview stops being a pause.
That is the geometry build rate alone, not the displayed frame rate — §1 and §2
cover the client-side work that also sits between a build and a rendered frame,
and §1's end-to-end figures were taken against the unvectorised pin.

### How it was verified

Differentially against the pinned implementation, per `RESULT-CONTRACTS.md`, not
only against the existing tests. Fourteen designs — R-OSSE plain, boxed with
both edge types, morphed to a rectangle, throat-extended, quadrant-reduced and
with per-azimuth ATH expressions; OSSE plain, with a guiding curve and with
per-azimuth expressions; ICW; two FREEFORM families; LOOKUP — each built at both
LODs with and without curvature, and every position, index, normal, curvature
and metadata array compared.

**1,692 of 1,716 arrays are byte-for-byte identical.** The 24 that move are all
one case, the R-OSSE with per-azimuth ATH expressions, and they move by at most
1.13e-12 mm. That is the one place a squaring is involved: NumPy squares by
multiplication, which is correctly rounded, while CPython's `x ** 2` goes
through the platform `pow`, which on macOS is occasionally one ulp wide of it.

The mesher suite is 1,097 passed, 0 skipped, run through `./run-ath-parity.sh
--full` so the ATH reference parity suite actually executes — plain `pytest
tests/` skips it silently and proves nothing. This repository's `server/tests`
is 1,039 passed, 2 skipped against the mesher checkout, including the
byte-for-byte frame comparison in `test_preview_ws_frame.py`.

### What is the wall now

Not the profile formulas. Instrumented per function on a coarse seed build,
share of a 36.7 ms instrumented wall:

| | ms/build | calls | share |
|---|---:|---:|---:|
| `adaptive_grid_indices` | 12.43 | 1 | 33.9% |
| — of which `_axis_interval_error` | 11.05 | 272 | 30.1% |
| `_triangle_orientation_analysis` | 6.37 | 10 | 17.4% |
| `_raw_radial_grid` | 5.63 | 1 | 15.3% |
| `analytic_grid_normals` | 3.70 | 3 | 10.1% |
| `_outer_offset_shell` | 2.44 | 1 | 6.7% |
| `resample_grid_vectors` + `_bilinear_coarse_points` | 2.50 | 2 | 6.8% |

The refinement search is now the single largest cost, as §7 predicted it would
be once the formulas were cheap — but it is its own per-interval NumPy dispatch
that is expensive, not the profile evaluation underneath it. Available and not
done: `_axis_interval_error` takes three separate reductions over its deviation
array and computes a full `np.linalg.norm` where an argmax over squared
distances would do; `resample_grid_vectors` and `_bilinear_coarse_points` are
still nested Python loops over every output node; `_triangle_orientation_analysis`
runs twice per surface, once to choose the winding and once to check it. At fine
LOD the ranking differs — `analytic_grid_normals` costs 33.6 ms and its
`_phi_derivative` allocates several full-grid copies through `np.roll`.
