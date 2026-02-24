# BEM Solver Acceleration Plan

Last updated: February 24, 2026

Owner: Backend solver team

Scope:
- `server/solver/solve_optimized.py`
- `server/solver/directivity_correct.py`
- `server/solver/device_interface.py`
- `server/tests/*`

## 1. Goal

Reduce `/api/solve` wall time while preserving current solver semantics and contracts:
- Canonical surface tags stay `1/2/3/4`.
- Source tag `2` remains required.
- No physics-breaking approximations.
- Existing fallback behavior remains intact.

## 2. Current State (Audit Findings)

### Already implemented (no work needed)
- OpenCL fallback chain: GPU → CPU safe profile → numba (`device_interface.py`)
- Comprehensive device/fallback metadata: `runtime_retry_attempted`, `runtime_retry_outcome`, `runtime_selected`, `runtime_profile`, `opencl_diagnostics` (12+ fields)
- OpenCL diagnostics in `/health` endpoint
- Directivity batching: single operator call per frequency for all phi angles
- Performance metadata: `total_time_seconds`, `frequency_solve_time`, `directivity_compute_time`, `time_per_frequency`

### Verified bottlenecks
1. **Frequency solving dominates wall time**, not directivity post-processing.
2. **First-solve penalty** is real — caused by numba JIT compilation, OpenCL context/kernel compilation, and first operator assembly (buffer allocation). All one-time per process.
3. **GMRES uses weak-form by default** — strong-form (inverse mass matrix as preconditioner) reduces iteration count. Verified supported in bempp-cl 0.4.2.

### Not worth pursuing
- **FMM (Fast Multipole Method)**: Requires `exafmm-t` C++ library compiled from source. Not installed, unlikely to compile on macOS + Python 3.13. Dense assembly is appropriate for typical waveguide mesh sizes (hundreds to low thousands of elements). FMM benefits appear at N>10,000.
- **`assembler_mode` API parameter**: Building API surface for a dependency that doesn't exist. Dense is the only practical assembler.
- **`solver_warmup` API parameter**: No user will toggle this. Just always warm up — the cost (one operator assembly) is negligible relative to a multi-frequency solve.

## 3. Implementation Plan

### Step 1: Strong-form GMRES + iteration telemetry

File: `server/solver/solve_optimized.py`

**This is the highest-value change.** Verified in bempp-cl 0.4.2 source (`iterative_solvers.py`):
- `use_strong_form=True` applies the inverse mass matrix as a left preconditioner. Cached on the operator object. Fewer GMRES iterations per frequency.
- `return_iteration_count=True` returns actual scipy GMRES iteration count.

Change the GMRES call from:
```python
p_total, info = bempp_api.linalg.gmres(lhs, rhs, tol=1e-5)
```
To:
```python
p_total, info, iter_count = bempp_api.linalg.gmres(
    lhs, rhs, tol=1e-5,
    use_strong_form=True,
    return_iteration_count=True,
)
```

Add per-frequency `iter_count` to metadata as `performance.gmres_iterations_per_frequency` (list) and `performance.avg_gmres_iterations` (float).

**Space compatibility note:** The Burton-Miller composite operator's `A.range` must match `b.space`. The RHS is produced by `(-slp - coupling * ...) * neumann_fun`, which creates a GridFunction in the operator's range space. This should be compatible but must be tested with the actual mesh.

### Step 2: Weak-form fallback (cheap insurance)

If strong-form GMRES fails for a specific frequency (e.g., space compatibility issue):
1. Catch the exception.
2. Retry that frequency with `use_strong_form=False`.
3. Record warning: `gmres_strong_form_fallback`.

This ensures no regression from enabling strong-form.

### Step 3: Warm-up pass before frequency loop

File: `server/solver/solve_optimized.py`

Before the frequency sweep, front-load one-time costs:
1. Pick a representative wavenumber (e.g., midpoint of frequency range).
2. Create one set of boundary operators (DLP, SLP, HYP, ADLP).
3. Call `strong_form()` on the composite LHS operator to trigger assembly + OpenCL kernel compilation.
4. Record `warmup_time_seconds` in metadata.

If warm-up fails, log a warning and continue — the main solve will handle its own errors.

This eliminates the first-frequency latency spike (~seconds of JIT/OpenCL compilation) from the timed frequency loop.

### Step 4: Benchmark script

File: `server/scripts/benchmark_solver.py` (new)

Simple CLI tool to measure solver performance on a reference mesh:
- Args: mesh path, freq min/max, num frequencies, device mode
- Output: total time, per-frequency time, iteration counts, selected device mode, fallback events

No benchmark matrix or acceptance-criteria ceremony — just a repeatable measurement tool for before/after comparison.

## 4. Metadata Additions

Extend `results["metadata"]["performance"]`:
1. `warmup_time_seconds` (float)
2. `gmres_iterations_per_frequency` (list of int)
3. `avg_gmres_iterations` (float)

No other metadata changes needed — device/fallback tracking is already comprehensive.

## 5. Tests

Update existing tests:
1. `server/tests/test_solver_hardening.py` — verify strong-form GMRES produces valid results, verify weak-form fallback path
2. `server/tests/test_solver_tag_contract.py` — unchanged invariants still hold

New test scenarios:
1. Strong-form GMRES returns valid solution (no regression vs weak-form)
2. `return_iteration_count` produces integer > 0
3. Warm-up failure does not abort solve
4. Weak-form fallback triggers on simulated strong-form failure

Run order:
1. Targeted server tests for changed modules.
2. Full server suite: `npm run test:server`.
3. Full JS suite: `npm test`.

## 6. Risks and Mitigations

1. **Strong-form space incompatibility with Burton-Miller composite operator.**
   Mitigation: per-frequency weak-form fallback (Step 2).

2. **OpenCL driver variability across platforms.**
   Mitigation: already handled — existing fallback chain + safe profile is robust.

## 7. Definition of Done

1. Strong-form GMRES enabled by default with weak-form fallback.
2. GMRES iteration counts visible in results metadata.
3. Warm-up pass eliminates first-frequency latency spike.
4. Benchmark script committed and repeatable.
5. All existing server and JS tests pass.
