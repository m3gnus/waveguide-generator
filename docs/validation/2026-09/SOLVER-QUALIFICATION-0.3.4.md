# Solver release qualification, 0.3.4 wave

Run per `docs/validation/SOLVER-QUALIFICATION.md`, 2026-09-29. Machine-readable
results: `solver-qualification-0.3.4-summary.json` (gates, totals, stop latency)
and `solver-qualification-0.3.4-same-mesh.json` (every row of the same-mesh and
ingest-level script).

## Headline

**All gates that can run on this machine PASSED at the final revisions.** The
rest are NOT RUN for missing hardware or a missing release candidate, listed
below.

Resolved during qualification: the first pass at bempp-bem 57b1260 (WG base
9396111d) FAILED one test,
`tests/test_infinite_baffle.py::test_bempp_coupled_ib_matches_portable_circsym_absolute_field`
(`AttributeError: ... no attribute 'solve_circsym_frequencies'`), a stale
comparison against the CircSym solver that metal-bem 0.2.0 removed, and the
installed wheel omitted its `validation` subpackage so
`test_native_symmetry_validation.py` could not be collected. Both were fixed at
the source: bempp-bem 5c13ac45 (every subpackage ships; tests retargeted off
CircSym), adopted by WG commit 6f122917. The whole qualification was then rerun
at that commit; the results below are from the rerun. No code or threshold was
changed by this qualification.

## Revisions

| Repository | SHA | Worktree state |
|---|---|---|
| waveguide-generator | 6f122917ed790445054a9dca51a64825e3eeabae | clean worktree (branch docs/solver-qualification-034 rebased on it, report files only), frontend built, no code changes |
| hornlab-waveguide-mesher | 873b0fda082707391da8252ce630985566d14002 | installed from this SHA (PEP 610 record); source checkout clean; SHA is an ancestor of local HEAD |
| hornlab-metal-bem | 5765b21ee3e651e192305caef348f7fb4b74e536 | same |
| hornlab-bempp-bem | 5c13ac4526d4ade287696f6e19aa29434eaff095 | same |
| hornlab-beat-bem | 74da18cdbb12844729a154005a26110511864aa0 | same |

Module tests ran from a `git archive` export of each pin, against the installed
distributions in a fresh venv (not any checkout's editable install).

## Machine and environment

- Apple M1 Max, 64 GiB RAM, macOS 26.6.2 (25G83), arm64.
- GPU: Apple M1 Max; OpenCL: Apple platform with a single GPU device and no CPU
  device. Metal and BEAT-Metal usable.
- Python 3.13.1, fresh venv built as CI does: `requirements-lock.txt`, then
  `--no-deps requirements-pins.txt`, then `requirements-dev.txt`.
- numpy 2.4.6, scipy 1.17.1, numba 0.66.0, pyopencl 2026.1.2, gmsh 4.15.2,
  bempp-cl 0.4.2, pytest 9.0.3, Node 20.20.2 for the frontend build.
- Package versions: hornlab-metal-bem 0.2.0, hornlab-waveguide-mesher 0.2.3,
  hornlab-bempp-bem 0.1.1 (5c13ac45), hornlab-beat-bem 0.1.0, hornlab-plots 0.1.2,
  hornlab-sim 0.1.1. The Metal native helper is the release build made by the
  wheel install.
- Engine detection: metal, beat-metal, beat-cpu available; bempp uses the numba
  fallback; beat-cuda and beat-rocm unavailable (no NVIDIA or ROCm hardware).
- The machine was shared: another session ran its own pytest during part of the
  WG server suite, so wall times are upper bounds.

## Gate results

| Gate | Result | Totals / evidence |
|---|---|---|
| WG live Metal full pipeline (`WG2_RUN_LIVE=1`, `test_engines_metal_live.py -m live`) | PASSED | 1 passed |
| WG full server suite (`pytest server/tests`, unpiped, from the fresh venv) | PASSED | 4954 passed, 45 skipped, 0 failed, 1043.7 s (first pass at 9396111d: 4947 passed, 45 skipped) |
| Same-mesh and ingest-level qualification (`scripts/qualify_imported_same_mesh.py`, Metal and BEAT-CPU, analytic ceilings, real CAD returns through WG's pipeline, fresh-data-dir import/solve/store/reopen) | PASSED | exit 0, about 2 min, rerun at 6f122917 with identical outcome to the first pass; 60 rows: 58 pass, 0 fail, 2 informational (coarse-vs-fine density rows carry no verdict by design); worst analytic L2 error metal 5.61e-03 (ceiling 8.5e-03), beat-cpu 3.36e-03; observed order metal 1.62, beat-cpu 2.04; horn reference-vs-fine metal 2.00e-02, beat-cpu 3.50e-03 (ceiling 3.0e-02) |
| metal-bem module suite at pin (includes the analytic pulsating-sphere gates, Burton-Miller, coupled infinite baffle, native symmetry, complex-k, speed-of-sound config) | PASSED | 624 passed, 0 skipped, 140 s |
| beat-bem module suite at pin (session lifecycle, submission ownership, worker persistence, sweep and result contracts, conformance) | PASSED | 292 passed, 0 skipped, 39 s |
| mesher module suite at pin | PASSED | 1740 passed, 35 skipped, 252 s |
| mesher ATH parity, `HORNLAB_ATH_PARITY=required`, local reference archive | PASSED | 33 passed, 12 s |
| bempp-bem module suite at pin 5c13ac45, including `test_native_symmetry_validation.py`; `hornlab_bempp_bem.validation` imports from site-packages | PASSED | 476 passed, 20 skipped, 0 failed, 42 s (first pass at 57b1260: 1 failed, see Headline) |
| BEMPP on OpenCL | NOT RUN | Apple Silicon has no CPU OpenCL device, which is the one bempp-cl assembles on; WG falls back to numba, which is not a qualified route. BEMPP is WG's CPU engine and has no GPU assembly path. The BEMPP OpenCL tests skip for this reason. |
| BEAT-CUDA | NOT RUN | No NVIDIA hardware |
| BEAT-ROCm | NOT RUN | No ROCm hardware |
| Windows and Linux owned runners | NOT RUN | Hardware not available here |
| Installed-payload gates (`qualify_installed_cpu.py`, `qualify_installed_quit.py`; persisted BEAT startup, restart/adoption, cancel-then-solve and explicit-Metal-starts-no-BEAT-worker against an installed candidate) | NOT RUN | No packaged release candidate exists yet. Source-level equivalents ran and passed (beat-bem lifecycle, ownership and persistence suites; WG server suite), but they do not close the installed-app gate. |

The removed-CircSym (axisymmetric) sections of the qualification document
are historical and were not run.

## Skips, with reasons

- WG server suite, 45 skipped: Windows-only tests (job objects, cmd.exe and
  PowerShell, security descriptors, gmsh PATH guard); the v1 simulation database
  or checkout is absent; `beat_engine` (official BEAT) is not installed; the
  live Onshape test needs five configured document ids; no waitable
  directory-change notification on this platform; X11/Wayland-only display test;
  and the live Metal test, skipped in that run only because `WG2_RUN_LIVE` was
  unset (it passed in its own run above).
- mesher, 35 skipped: the ATH reference parity tests when `ATH_REFERENCE_ROOT` is
  unset (the required-mode run above executed all 33 of them), and the
  Windows-only native-environment guards.
- bempp-bem, 20 skipped: 15 need an OpenCL CPU device or any OpenCL device,
  which this machine does not have; 5 need the external ASRO68 mesh and
  validation-artifact variables, which are unset.

## Stop and cancellation

Measured in the first pass at 9396111d; the pins that moved afterwards (bempp-bem only) do not touch the Metal or BEAT paths. Method: a real full-3D solve (OSSE horn, 48 x 24 segments, quarter domain, 40
frequencies 500 Hz - 12 kHz) was submitted through the durable `JobRuntime`. Once
the job reported stage `solve` with progress above zero, one second passed and
`JobRuntime.stop()` was called. Latency is the time from that call to the job
reaching a terminal status, polled every 10 ms, measured with a monotonic clock.
A two-frequency solve was then submitted on the same runtime to prove
cancel-then-solve. Single measurement per engine.

| Engine | Stage at stop | `stop()` call | Stop to terminal status | Final status | Next solve |
|---|---|---|---|---|---|
| metal | solve, 35% | 1 ms | 0.138 s | cancelled | complete (1.4 s) |
| beat-cpu | solve, 36% | 1 ms | 6.31 s | cancelled | complete (16.7 s) |
| beat-metal | solve, 36% | 1 ms | 1.35 s | cancelled | complete (18.9 s) |
| bempp, numba fallback (informational) | solve, 35% | 1 ms | 0.081 s | cancelled | complete (42.5 s) |

Maximum observed response latency: 6.31 s (beat-cpu); every other engine under
1.4 s. The document states no numeric latency bound, so none is judged here. The
BEAT worker is cancelled at its next checkpoint rather than killed, which is
what the beat-cpu figure reflects at this mesh size. Explicit Metal selection
starting no BEAT worker was not separately measured in this run.

Also checked at source level: the WG job-cancel tests and the beat-bem cancel,
lifecycle and ownership tests all passed within the suites above.

## Housekeeping

The Fusion add-in marker file was unchanged before and after the run (shasum
42f050882e33fbeb14765f4b72efab7d85065b3a). No process left running: the BEAT
worker hosts started by these runs were terminated at the end.
