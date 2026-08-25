# First-solve initialization on Windows — measured, then removed

**Date:** 2026-08-25. **Machine:** the one described in
[WINDOWS-VALIDATION.md](WINDOWS-VALIDATION.md) §1 — Windows 11 Pro 10.0.26100,
Ryzen 7 5825U, 12 logical processors, 16 GB, QEMU/KVM guest with no hardware 3D.
**Branch:** `fix/bempp-first-solve-warm-child`, based on
`fix/windows-suite-portability`.

One thing about the machine has changed since check 12 was written: an OpenCL
runtime is now present (`Intel(R) OpenCL`, device *AMD Ryzen 7 5825U with Radeon
Graphics*), so `bempp_status()` reports the OpenCL assembly backend rather than
falling back to numba. The numba figures below were produced by pinning
`_assembly_backend_status` to numba inside the worker; nothing else was changed
for them.

## 1. What was measured

Two harnesses, both on this machine:

* **Solver boundary.** A fresh interpreter per run — one "server start" —
  building the mesh once and then calling `solve_bempp_in_process` exactly as
  `BemppEngine.run` does. R-OSSE `R=150, r0=12.7, a=60, a0=15.5`, three
  frequencies (1/2/4 kHz). This isolates the child process and nothing else.
* **End to end.** A real uvicorn server started from `create_app`, driven over
  HTTP: `POST /api/solve`, then poll `/api/status/{id}` until the terminal
  state. Same design, same three frequencies, `engine=bempp`,
  `solver_mode=full_3d`, with 35 s of "think time" between the server becoming
  healthy and the solve being submitted.

## 2. Before

Solver boundary, first solve after start versus second:

| Backend | First solve | Block with no checkpoint in it | Second solve |
|---|---|---|---|
| OpenCL | 28.18 s | 25.43 s | 0.82 s |
| numba | 65.75 s | 61.40 s | 4.12 s |

The block is measured from the `setup` stage event to the first per-frequency
`frequency_solve` callback, which is the first checkpoint the engine offers.
Check 12 recorded 24.5/18.3 s for OpenCL and 64.3 s for numba on the same
design shape; the OpenCL figures here sit between its "first ever" and "later
starts" rows, and numba matches.

End to end, prewarm disabled (`WG2_SOLVER_WARMUP=0`), OpenCL:

| | First solve | Second solve |
|---|---|---|
| Press Solve → terminal state | 15.93 s | 0.65 s |
| Press Solve → first `frequency_solve` | 15.68 s | 0.40 s |

The end-to-end first solve is smaller than the solver-boundary one because the
runtime resolves this circular design to a quarter domain, so the mesh is
smaller than the one the boundary harness passes through.

## 3. Root causes, as confirmed rather than assumed

1. **`server/solver/warmup.py` warmed the wrong process.** Every BEMPP solve
   runs in the worker child owned by `server/solver/bempp_process.py`. The
   warmup ran in the API process. Measured directly: with the in-parent warmup
   run to completion first, it spent **24.2 s** and the first child solve still
   took **24.3 s** — the same as with no warmup at all. bempp-cl's hot numba
   kernels carry no `cache=True`, so nothing survives the process boundary
   except pyopencl's on-disk program cache, which was already populated here.
   The env var was therefore not merely off by default; on this path it did
   nothing.

2. **A cancelled solve pushed the whole cost onto the next one.** Stop at 5.0 s
   into a cold solve returned at **5.05 s** — the process boundary already
   makes Stop prompt. But the next solve then took **24.88 s** (first result at
   24.38 s), whether it was submitted immediately or 30 s later. Nothing was
   warming in between.

3. **AUTO lands on bempp here.** `detect_engines()` reports metal unavailable
   ("requires macOS") and beat unavailable ("No Julia executable was found"),
   and `resolve_auto_engine()` returns `bempp`.

Check 12 read the checkpoint-free block as *Stop latency*. On this revision it
is not: `bempp_process.py` polls the cancel callback every 50 ms in the parent
and kills the child. What the block still costs is *time to first result*.

## 4. After

Solver boundary, worker prewarmed at start, solve submitted after think time:

| Backend | First solve | First result | Second solve | Before (first solve) |
|---|---|---|---|---|
| OpenCL | 0.97 s | 0.47 s | 0.84 s | 28.18 s |
| numba | 4.21 s | 1.41 s | 4.20 s | 65.75 s |

`BemppProcessHost.prewarm()` itself returns in **11-12 ms**.

End to end, OpenCL, three frequencies:

| | Before | After |
|---|---|---|
| Press Solve → terminal state | 15.93 s | **1.13 s** |
| Press Solve → first `frequency_solve` | 15.68 s | 0.89 s |
| Second solve | 0.65 s | 0.66 s |

Stop, then solve again:

| Scenario | Before | After |
|---|---|---|
| Stop at 0.5 s, re-solve 30 s later | 24.88 s | **0.96 s** |
| Stop at 0.5 s, re-solve immediately | 24.88 s | 25.28 s |

The immediate case is unchanged by design: nothing has paid the initialization
yet, so somebody has to. The worker detects that a real solve is already queued
behind its warmup and skips the warmup rather than making that user wait out a
stand-in solve first — without that check the immediate case measured 24.9 s
against 24.5 s for no respawn at all, a small regression.

## 5. The shutdown objection

`warmup.py` kept warming opt-in because a daemon thread abandoned inside native
code cannot be stopped, so Quit could hang for the rest of the initialization
block. Warming in the child answers that, and it was measured rather than
argued. `BemppProcessHost.close()` called while the child is inside its native
warmup:

| Called | `close()` returned in | Child gone |
|---|---|---|
| 0.2 s in | 0.512 s | yes |
| 2.0 s in | 0.533 s | yes |
| 6.0 s in | 0.536 s | yes |
| 15.0 s in | 0.541 s | yes |

Bounded at `_JOIN_SECONDS` (0.5 s) plus terminate, at every point in the block.
The objection holds for the in-process path and is answered for the child one,
which is why the Metal branch stays behind `WG2_SOLVER_WARMUP=1` and the BEMPP
branch does not.

`create_app` also now closes the worker on app shutdown rather than relying on
the `atexit` hook alone, which a launcher killed with `TerminateProcess` never
runs.

## 6. Not verified on this hardware

* **Metal.** This is a Windows VM; the Metal branch was not exercised. It is
  unchanged apart from its gate.
* **BEAT / GPU.** No CUDA or ROCm device and no Julia, so `resolve_auto_engine`
  never returns `beat` here and the skip path is covered by a unit test rather
  than by a real GPU host.
* **numba end to end.** The backend is chosen by probe with no override, so the
  numba figures come from the solver-boundary harness only. The same child and
  the same code path run underneath both harnesses.
* **OpenCL "first ever".** The pyopencl on-disk program cache was already warm
  on this machine and was not cleared, so check 12's 24.5 s first-ever row has
  no counterpart here.
* **A circular design through AUTO fails on this machine** before reaching
  BEMPP at all: the planner routes it to the axisymmetric engine and the
  installed axisymmetric package raises *"Installed axisymmetric solver lacks
  intra-frequency cancellation"* (`server/solver/circsym.py:491`, the
  `should_continue` branch). That is a pinned-dependency problem predating this
  work and untouched by it; the end-to-end measurements above therefore ask for
  `engine=bempp, solver_mode=full_3d` explicitly.
