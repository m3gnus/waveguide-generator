# Sketch solver contract and Phase 0 harness

Audience: sketcher implementers and bake-off reviewers. Status: experimental adapters;
no solver selection or packaged-webview qualification has been made.

`types.ts` is the solver-neutral API: `await createSolver(name)`, `load(sketch)`,
`solve({mode, dragTargets?})`, `beginDrag(pointIds)`, `dragTo(targets)`,
`endDrag()`, `capabilities()`, `destroy()`. Lengths are mm and angles
radians; signed axis distances mean `b - a`. Points are shared by reference IDs;
arcs retain their center/start/end IDs and explicit `direction: 'ccw'`. Reserved
circle/ellipse/bspline entities return `unsupported`. Non-driving dimensions are
omitted from native equation systems; callers measure references from returned
points. `pointOnLine` and `pointLineDistance` use infinite supporting lines;
`pointOnArc` uses the finite CCW span. Tangency supports line–arc and arc–arc.

Ordinary `solve()` retains its rebuild behavior. A drag session builds during
`beginDrag()`, updates the same system with `dragTo()`, then removes goals and
rebuilds for full diagnosis in `endDrag()`. Targets name every session point once;
`load()` and ordinary `solve()` cancel a session. `totalMs` includes validation,
target updates, marshaling, native solve and readback; `solveMs` times the native
call, and `marshalMs` is the remainder. Bench `setupMs` records `beginDrag()`
separately. Failed results retain the last successful point seed and invalidate
prepared native state so the next frame can rebuild safely. There is no
continuation subdivision or branch correction.

- planegcs pushes geometry/constraints once, with fixed named sketch parameters
  referenced by temporary `coordinate_x`/`coordinate_y` constraints. Frames call
  `set_sketch_param`, `solve`, `apply_solution` and read points. The pinned C++
  binding's `solve_system` calls `GCS::System::solve(params, true, alg)`, which
  reinitializes the solver (including QR diagnosis) every time. Avoiding that cost
  needs an upstream initialize-once/move-target/solve-without-reinit entry point,
  with invalidation on topology/constraint changes and full diagnosis on release,
  as in FreeCAD's `Sketch::movePoint`. No upstream binary was changed here.
- slvs builds once, retains handles, calls `setParamValue` on dragged coordinates,
  then `solveSketch(2, false)`. It projects the cursor seed onto hard constraints;
  `WHERE_DRAGGED` adds hard pins and is not a soft goal. Its module owns one global
  sketch: interleaving instances invalidate the owner and rebuild on the next use.
  Destroying a nonowner leaves the live owner's sketch intact.
- ACS keeps one request object and updates only temporary goals and successful
  geometry seeds (points plus arc radius/angles). Every frame still serializes
  JSON and reconstructs the native Rust sketch through `acsSolveSketch`.
  Top-level `analyze:false` is honored; release uses `analyze:true`.

Native IDs map back to user constraint IDs; implicit arc rules belong to the public
arc entity. An exact endpoint-radius check catches omitted native arc-rule failures.
`destroy()` releases per-sketch state; initialized WASM modules remain cached.

Run from `frontend/`, with the installed Node 20.20.2 toolchain on PATH:

```sh
npx --no-install vitest run src/sketch/solver/
npm run build
SKETCH_BENCH=1 npm run build
```

Only the opt-in build includes `sketch-bench.html`. Open it through the normal
frontend host and click **Run bench**, or use `?autorun=1`. Filters `?solver=acs&n=60`
select both corpus variants; append `&variant=constrained` (or `underconstrained`)
to rerun one row. Invalid filter values are rejected.

Automation waits for `window.__sketchBench.done`; read `.results` or use **Download
JSON**. Each solver × size × corpus variant uses a fresh worker and a 30 s budget;
the entire run has a 300 s budget. Rows record completed frame count, every frame's
adapter/native status, validity, iterations when exposed (ACS), and timing samples.
Each completed frame is published immediately, so a slow next solve cannot hide it.
Rows also measure cold start, session setup, 300-frame p50/p95/p99/max, release
full diagnosis, branch flips/failures, and memory around 1,000 small constrained-arc
lifecycle cycles. A timeout preserves partial evidence; rows not started by the run
deadline are marked timeout. Do not compare partial rows as complete measurements.
Memory uses `performance.memory`, otherwise observed WASM buffers (capacity, not live
allocation); GC is uncontrolled. No rendering time is measured.

`contourChain(n)` has `n + 1` analytic DOF. `contourChainConstrained(n)` retains that
geometry/topology and dimensions every line length and arc radius, plus all but three
arc chord lengths. Each chord locally fixes a nonzero sweep; the remaining three
sweeps, overall heading and pole height, minus the rim/baffle equation leave four
DOF at n = 8, 60, 256. Both variants have exact initial geometry. `valid` denotes
equation feasibility, so a collapsed line can still be feasible. Redundancy bases
are not unique.

Observed capability matrix (N = native, E = exact composition, U = unsupported):

| Constraint | planegcs | slvs | ACS |
| --- | --- | --- | --- |
| coincident | N | N | N |
| horizontal (line / pair) | N | N | N |
| vertical (line / pair) | N | N | N |
| parallel | N | N | N |
| perpendicular | N | N | N |
| tangent (line–arc / arc–arc) | E¹ | N² | E¹ |
| equal (length / radius) | N | N | N |
| midpoint | E³ | N | N |
| pointOnLine | N | N | N |
| pointOnArc (finite span) | U⁴ | U⁴ | N |
| fix | E⁵ | E⁵ | E⁵ |
| distance | N | N | N |
| distanceX | E⁶ | E⁶ | N |
| distanceY | E⁶ | E⁶ | N |
| pointLineDistance | N | N | N |
| angle | N | N | N |
| radius | N | E⁷ | N |
| DOF | N⁸ | N⁸ | N |
| conflicting | E⁹ | E⁹ | E⁹ |
| redundant | N | U | N |
| partiallyRedundant | N | U | U |

1. At shared/coincident endpoints, perpendicular line/radius or parallel arc radii
   avoid singular supporting-curve tangent equations. Disjoint curves use the
   package's native tangency; planegcs uses supporting curves, ACS finite curves.
2. SolveSpace supports shared/coincident endpoint contacts; other contacts return
   `unsupported` rather than approximating tangency.
3. Point-on-line plus point-on-perpendicular-bisector.
4. planegcs's pinned `point_on_arc` accepted a point on the complementary span in
   the probe. SolveSpace's `PT_ON_CIRCLE` has no finite-span condition. Both are
   honestly unsupported; no circle approximation is substituted.
5. Coordinate equations in planegcs/ACS; coincidence with a fixed-group point in slvs.
6. planegcs parameter difference; slvs projection onto a fixed unit axis, with its
   sign converted. Both match signed `b - a` analytically.
7. Diameter set to twice the requested radius.
8. Rank on singular/redundant systems needs care. The retained failing test is slvs's
   duplicate-horizontal case: native DOF is 2, analytically 3. No rank estimate is
   invented by the adapter.
9. Native constraint IDs plus the exact common-circle invariant check for arc rules.
   SolveSpace bad handles do not distinguish conflicts from overconstraints.

Round 2 findings (2026-10-18, macOS arm64, Node 20.20.2):

- **ACS timing anomaly:** the old WKWebView n=256 row completed only frame zero,
  whose target equals the initial rim position. A Node rerun completed that frame
  in 32.746 ms with `converged`, **0 iterations**; frame one did not return before
  the isolated process was terminated at its 35 s deadline. This is a censored
  stationary sample, not faster dragging at 256. At n=60, the three-frame Node
  session reported iterations **0, 12, 11** (underconstrained), and **0, 6, 6**
  (constrained), all `converged`. The moving frames cost about 1 s despite skipping
  diagnosis. This evidence does not identify a particular internal matrix routine.
- **ACS analysis option:** on a small perturbed distance case, top-level
  `analyze:false` omitted DOF/conflict/redundancy fields; `true` returned DOF 1 and
  empty lists. Both took 4 iterations. Top-level `maxIterations:1` returned `failed`
  with 1 iteration; nesting those fields in `options` ignored them and converged
  in 4 iterations with diagnosis. Ordinary tests retain this check.
- **planegcs OOM:** the installed WASM memory section has min=max=337 pages,
  **22,085,632 bytes**, with an OOM-aborting resize stub. In a fresh module, the
  256-segment session pushed 1,798 parameters successfully, then aborted on its
  **first** native solve, before any diagnostic getters. Repeated push/clear and
  adapter diagnostic-list extraction are not prerequisites. A diagnostic-only
  in-memory copy with min=max=2,048 pages (128 MiB) solved the identical system and
  a second moved-target solve, both status 0 (about 4.045 s then 9.600 s). The
  shipped memory ceiling is the blocker; internal solve/QR workspace exceeds it.
  The stripped binary does not identify the exact allocating routine. Sessions
  do **not** avoid the OOM, since the pinned solve still reinitializes. The test
  changed neither installed files nor production loading; a larger/growable
  upstream build and a separate incremental-solve API are needed.
- **slvs duplicate-horizontal DOF:** four point coordinates and one rank-one
  equation `a.y - b.y = 0` give DOF 3, regardless of the duplicate. Native DOF 2
  remains documented by `it.fails`; the expectation was not weakened.

Indicative Node `dragTo` p50 in milliseconds, n=60, **three frames per row** including
stationary frame zero; all 18 results were `ok`. The timing probe passed in **5.58 s**.
These are short Node samples, not browser qualification or steady-state estimates.

| Solver | Underconstrained | Constrained |
| --- | ---: | ---: |
| planegcs | 136.951 | 162.715 |
| slvs | 12.796 | 12.762 |
| ACS | 1134.646 | 962.820 |

Reproduce the timing probe from `frontend/` with
`SKETCH_INVESTIGATE=timing npx --no-install vitest run src/sketch/solver/investigation.test.ts`.
The optional `gcs` probe uses independent modules, including a diagnostic memory-size
copy. The `acs` probe deliberately reaches a potentially stuck synchronous 256-size
solve and must be launched under an external process-group timeout (35 s was used).
The opt-in investigation is skipped in the normal suite. An initial combined probe
was interrupted without usable output; only the separate completed/timeout evidence
above is reported.

Verification: `npx --no-install vitest run src/sketch/solver/` passed in 785 ms:
`Test Files 3 passed | 1 skipped (4)`;
`Tests 164 passed | 1 expected fail | 11 skipped (176)`.
Ten skips are capability gaps; the eleventh is the opt-in investigation.
`npm run build` and `SKETCH_BENCH=1 npm run build` both passed (TypeScript strict
check plus Vite). Warnings: chunk sizes, and in the opt-in build Node-only package
modules externalized for browser compatibility. The opt-in output includes the
bench page, isolated worker, both WASM assets and solver chunks.
The browser benchmark, 300-frame runs, 1,000-cycle memory runs, CSP/offline behavior
and packaged WKWebView/WebView2 were not run in this round; the overseer runs those.
Normal build keeps the bench excluded. API/package findings from round 1 remain:
planegcs's AOT artifact ignores `wasmBinary` (Node needs filesystem `locateFile`),
planegcs differences/slvs projections use the opposite sign to this contract,
SolveSpace arcs need a 3D normal, and successful slvs results can omit `bad`.

Pinned licenses and source/build references (no dependency versions changed here):

- `@salusoft89/planegcs` 1.3.0: metadata **LGPL-2.0-or-later**, bundled wrapper headers
  **LGPL-2.1-or-later**. [Source](https://github.com/Salusoft89/planegcs), upstream
  README documents `npm run build:all` (Docker C++/Emscripten bindings).
- `slvs` 3.2.0: **GPL-3.0-or-later**.
  [Source](https://github.com/solvespace/solvespace), installed single-file inlined WASM.
- `acs-solver` 0.1.17: **MIT**.
  [Source](https://github.com/angelobartolome/acs), upstream README documents
  `wasm-pack build --target web --out-dir pkg` and the v0.1.17 release artifact.
