# Sketch solver contract and Phase 0 harness

Audience: sketcher implementers and reviewers.

**Decision (2026-10-18):** the sketcher uses SolveSpace libslvs 3.2 from our own WASM build,
vendored in `frontend/vendor/slvs/` (see its `BUILD.md`). The planegcs adapter stays as the
second implementation of this contract. ACS was evaluated and removed. The drag gate is a
median frame under 8 ms and p99 under 33 ms at 24 segments, with rendering included.
Long sessions must recycle the slvs module instance, because libslvs leaks memory on each
`clearSketch` rebuild. The Phase 0 bake-off evidence is kept outside the repository.
ACS rows and columns below are historical Phase 0 measurements; the adapter no longer exists.

`types.ts` exposes lazy `createSolver(name)`, `load`, `solve`, `beginDrag`, `dragTo`,
`endDrag`, `capabilities`, and `destroy`. Lengths are mm; angles are signed radians
from the first directed line to the second. Axis distances mean `b - a`.
`pointLineDistance` is unsigned to the infinite supporting line; `pointOnArc`
requires the finite CCW span. Arcs keep explicit center/start/end identities.
Circle, ellipse and bspline are reserved and return unsupported. Reference dimensions
are omitted from equations and measured from returned points. Native diagnostic IDs
map to public constraint IDs; implicit arc rules belong to the public arc entity.

Ordinary `solve()` rebuilds. Sessions prepare once and retain successful geometry
seeds; `endDrag()` removes cursor objectives and rebuilds for full diagnosis.
Failures invalidate prepared state without poisoning the successful point seed.
`load()` and ordinary `solve()` cancel sessions. Targets name every session point
exactly once. Destroy releases per-sketch state; WASM modules remain cached.

Every result splits `totalMs` into `updateMs`, `solveMs` and `readbackMs`:

- Update includes validation, any rebuild, target updates and input serialization.
- Solve times native calls (including both attempts when slvs falls back).
- Readback includes solution application, output parsing, point/diagnostic extraction,
  invariant checks and continuation-seed updates.

`marshalMs = updateMs + readbackMs` remains for existing consumers. `setupMs` measures
`beginDrag()` separately. These timings exclude harness residuals, telemetry and render.

## Drag semantics

- **planegcs:** named x/y parameters feed temporary coordinate objectives. Frames
  update goals, call `solve`, `apply_solution`, and read back points. The pinned binding
  reinitializes solution partitions each solve. Diagnosis can remain cached; saying it
  diagnoses every frame was incorrect. Soft goals select SQP and can entail dense work.
  No package/binary changes were made.
- **slvs:** `dragged()` adds native `C_WHERE_DRAGGED` hard cursor equations. Reachable
  free-chain targets now track exactly, instead of merely projecting seeded coordinates.
  If native solve rejects pins (infeasible **or redundant**, including rim y=0 pins),
  rebuild from the last successful sketch without pins, seed the target coordinates,
  and project onto hard constraints. This fallback satisfies equations but does not
  guarantee minimum cursor movement. Handles persist while pins succeed; fallback or
  global-sketch ownership changes rebuild. Drag disables failed-constraint analysis.
- **ACS:** temporary x/y soft goals with `analyze:false`; each frame serializes a reused
  request and reconstructs the Rust sketch. Successful point and arc seeds persist.
  Full release uses `analyze:true`. Serialization/reconstruction are package costs.

## Measurements and telemetry

Run from `frontend/` using the installed Node 20 toolchain:

```sh
npx vitest run src/sketch/solver/
npm run build
```

Set `SKETCH_DRAG_OUTPUT` to an output filename on the same Vitest command to save the
Node scenario results, including every target, cursor error, residual maximum, three
interior positions and split timings. The opt-in investigation remains skipped.

The opt-in Vite input (`SKETCH_BENCH=1`) includes `sketch-bench.html`. Through the normal
frontend host, click **Run bench** or use `?autorun=1`. Optional filters:
`solver=planegcs|slvs`, `n=8|16|24|32|60|256`,
`variant=underconstrained|constrained`. No browser run was performed in round 3.
Read `window.__sketchBench` or **Download JSON** for full results.

Each solver × size × variant has a fresh worker and a **120 s** budget, within a
**900 s** total budget. Planned complete drags use **300 frames for n ≤ 32, 120 at
n=60, and 30 at n=256**, spanning the whole sinusoid. Rows retain planned/completed
counts and label incomplete distributions **partial**. Native calls remain synchronous;
the parent watchdog can terminate a stuck worker. Between-frame deadlines return full
partial samples; hard termination retains only the last progress counts and reservoir
estimates, with no full sample array. Such rows are not complete measurements.

Progress messages contain small metadata, counts and percentiles from a deterministic
fixed-size 256-frame reservoir. They never carry sample histories. The full row and
sample array are posted once on completion/error/cooperative timeout. Final returned
percentiles use all frame samples. The parent coalesces rendering to at most 4 Hz and
formats detailed histories only when the run is done. Final rows include every frame's
cursor error, residual maximum, adapter/native status and split timings; cold start,
setup, release diagnosis, branch cases and 1,000 small-arc lifecycle cycles remain.
Memory buffer size measures capacity, not live allocations or proof of no leak.

At n=8 and 24 each row also measures three independent 60-frame scenarios, using the
same underconstrained seed regardless of the main row variant:

1. **Reachable:** remove the rim-baffle lock, drag the free end along a 1 mm radial /
   0.5 mm axial sinusoid. Require every cursor error <1e-6 mm.
2. **Unreachable:** retain rim z=0, request z=5 mm and the same radial sinusoid.
   Record final cursor error, final per-equation residuals and nearest-locus error.
3. **Trajectory:** retain the baffle lock and request the same 60-frame radial path
   for every solver. Record `start(n/4)`, `start(n/2)`, `start(3n/4)` each frame and
   each point's maximum deviation from the matching planegcs trajectory. A filtered
   run without planegcs has no reference comparison; absence is not a zero deviation.

`contourChain(n)` has n+1 analytic DOF. Its mostly dimensioned variant fixes every
line length/arc radius and all but three arc chords, retaining four DOF at tested sizes.
Residuals include implicit common-circle and fixed-point checks. Branch cases actually
move: small sweeps, either side of pi, crossing a tangent line, and a 100 mm cursor jump.
The detector unwraps signed endpoint sweeps frame to frame and detects entering/leaving
0 or 2pi; crossing pi alone is not a flip. Conformance also checks the analytic sweep
of each branch path and actual endpoint movement. Winding inferred from positions is
ambiguous for unsampled angular jumps ≥pi; this does not prove arbitrary-path continuity.

## Capabilities and findings

N = native, E = adapter composition/conversion or explicitly guarded branch, U = unsupported.

| Constraint / diagnostic | planegcs | slvs | ACS |
| --- | --- | --- | --- |
| coincident, horizontal, vertical | N | N | N |
| parallel, perpendicular | N | N | N |
| tangent (line–arc / arc–arc) | E¹ | N² | E¹ |
| equal (length / radius) | N | N | N |
| midpoint | E³ | N | N |
| pointOnLine | N | N | N |
| pointOnArc (finite span) | U⁴ | U⁴ | N |
| fix | E⁵ | E⁵ | E⁵ |
| distance | N | N | N |
| distanceX, distanceY | E⁶ | E⁶ | N |
| pointLineDistance (unsigned) | N | E⁷ | N |
| angle (signed) | N | E⁸ | N with finding⁹ |
| radius | N | E¹⁰ | N |
| DOF | N | N with findings¹¹ | N |
| conflicting | E¹² | E¹² | E¹² |
| redundant | N | E¹³ | N |
| partiallyRedundant | N | U | U |

1. Shared/coincident endpoints: perpendicular line/radius or parallel radii; otherwise
   native supporting-curve tangency (planegcs) or finite-curve tangency (ACS).
2. Shared/coincident endpoints only; other SolveSpace contacts return unsupported.
3. Supporting line plus perpendicular bisector (two exact scalar equations).
4. Pinned planegcs accepts the complementary span; slvs constrains the supporting circle.
5. Coordinate equations (planegcs/ACS), fixed-group point coincidence (slvs).
6. Parameter difference (planegcs), signed unit-axis projection (slvs).
7. slvs native workplane distance is signed. Capture `(point-start) cross (end-start)`
   at **load**, multiply the requested magnitude by that sign, and retain the side
   across rebuilds and drags. Exactly on the line selects the positive native side.
   Opposite-side and reversed-line fixtures exercise all adapters.
8. slvs native angle constrains cosine. A post-solve signed-angle guard rejects the
   wrong branch with `failed` and the public constraint ID; it does not repair a mirrored
   seed. Negative-angle/reversed-line fixtures solve, mirrored seeds are explicitly rejected.
9. ACS 0.1.17 can return `ok` at a supplementary signed angle from mirrored positive
   and negative seeds. Two residual tests remain `it.fails`; native support is defective
   on those branches. No package changes or semantic approximation hide this.
10. Native diameter set to twice the radius.
11. slvs duplicate horizontal reports DOF 2 instead of 3 (existing expected failure).
    Six other "feasible but failed" cases were an adapter bug, not a solver defect:
    the adapter treated only `SLVS_RESULT_OKAY` (0) as solved, while slvs returns
    `SLVS_RESULT_REDUNDANT_OKAY` (4) for solved systems with redundant constraints.
12. Native IDs plus adapter common-circle invariant checks owned by public arc IDs.
13. With `SLVS_RESULT_REDUNDANT_OKAY`, the bad handles are reported as redundant (implied, kept), not conflicting. No partial category.

Round 3 Node cursor measurements (2026-10-18, macOS arm64, Node 20.20.2; working
changes on the brief's 891ec98f base). Each scenario completed all 60 frames with
zero failed frames and zero observed sweep flips. Residual maxima across all scenarios:
planegcs ≤8.53e-14, slvs ≤9.95e-13, ACS ≤1.12e-10 (mixed equation units).

| Solver | n | Reachable max error mm | Unreachable final error mm | Max nearest-locus error mm | Trajectory max cursor error mm |
| --- | ---: | ---: | ---: | ---: | ---: |
| planegcs | 8 | 1.54e-11 | 5 | 1.78e-27 | 2.85e-14 |
| slvs | 8 | 0 | 5.000002857 | 0.005364871 | 0.005202638 |
| ACS | 8 | 1.54e-10 | 5 | 2.66e-8 | 1.15e-10 |
| planegcs | 24 | 2.26e-10 | 5 | 4.61e-12 | 4.61e-12 |
| slvs | 24 | 0 | 5.000024867 | 0.015802609 | 0.015315359 |
| ACS | 24 | 2.28e-10 | 5 | 1.06e-9 | 1.06e-9 |

Trajectory maximum interior deviations from planegcs, in order n/4, n/2, 3n/4 (mm):

| Solver | n=8 | n=24 |
| --- | --- | --- |
| planegcs | 0, 0, 0 | 0, 0, 0 |
| slvs | 0.002366, 0.001566, 0.003546 | 0.002230, 0.002251, 0.003037 |
| ACS | 3.142227, 1.823204, 1.084802 | 2.019032, 2.017003, 1.175055 |

slvs's unreachable nearest-projection tests are `it.fails` at both sizes. Native
cursor pins fix free-end tracking, but the rim's redundant hard y pin still forces
the old projection fallback even on the trajectory path. Equal residual accuracy
does not establish equivalent drag behavior or qualify a performance winner.

Previous-round evidence (not rerun here): installed planegcs WASM has a fixed
22,085,632-byte heap and aborted on the first n=256 solve; an in-memory larger-heap
probe succeeded. ACS n=256 moved-frame solve exceeded its prior 35 s cutoff.
Neither result identifies an exact internal allocation/matrix routine. No timing
ranking, GC attribution, leak claim or packaged-webview qualification follows.

Round 3 verification with Node 20.20.2:

- `npx vitest run src/sketch/solver/`: `Test Files 4 passed | 1 skipped (5)`;
  `Tests 215 passed | 11 expected fail | 11 skipped (237)`, 13.61 s.
  Ten expected failures are new; duplicate-horizontal DOF is retained. Ten skips are
  unsupported-capability cases; one is the opt-in investigation.
- A real slvs n=8 underconstrained benchmark row completed 300 frames, all three
  60-frame scenarios and 1,000 lifecycle cycles. Its telemetry test verifies bounded
  progress and a single final full-sample message. Deadline/render-rate tests use
  simulated workers and virtual time; these are not browser performance measurements.
- `npm run build` and `SKETCH_BENCH=1 npm run build` passed. Vite warns about large
  chunks; the opt-in build also reports browser externalization of Node-only imports.
  The opt-in output contains the bench page, worker and WASM assets.
- The full 36-row browser benchmark, packaged WKWebView/WebView2, CSP/offline checks,
  and performance gates were not run. There is no new performance qualification.

Pinned package licenses (versions unchanged):

- `@salusoft89/planegcs` 1.3.0: LGPL-2.0-or-later metadata, LGPL-2.1-or-later wrapper
  headers. [Source](https://github.com/Salusoft89/planegcs); upstream `npm run build:all`
  uses Docker/C++/Emscripten. AOT Node loading needs a filesystem `locateFile` path.
- `slvs` 3.2.0-hornlab.1 (vendored): GPL-3.0-or-later, licence text in
  `vendor/slvs/COPYING.txt`. [Source](https://github.com/solvespace/solvespace) at
  v3.2, commit 27b6a080. Our build has a separate `.wasm` and a 16 MiB initial heap, and
  is stripped. The solver source is unchanged.
