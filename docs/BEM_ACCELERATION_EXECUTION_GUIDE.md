# BEM Acceleration Execution Guide

## Purpose
This document defines the execution instructions for accelerating the backend BEM solve path (`/api/solve`) with the highest effort-to-gain impact first, while preserving numerical reliability and current frontend/backend contracts.

## Scope
In scope:
- Backend BEM runtime performance for:
  - Frequency solve loop
  - Operator assembly and iterative solve
  - Directivity evaluation path
- Runtime mode controls and metadata reporting
- Benchmarking and acceptance thresholds
- Safe fallback behavior

Out of scope:
- Frontend UI redesign
- ABEC export parity changes
- OCC meshing feature expansion unrelated to solve performance
- Axisymmetric production path enablement (remains scaffold-only unless separately approved)

## Current Baseline (must be preserved)
- Canonical tags:
  - `1 = wall`
  - `2 = source`
  - `3 = secondary`
  - `4 = interface/symmetry`
- Source boundary space remains `segments=[2]`.
- `/api/solve` contract remains backward-compatible.
- Existing solve flow and stage reporting in `server/app.py` must continue to function.
- Symmetry reduction behavior remains backend-owned when enabled.

## High-Level Strategy (ranked)
1. Add **assembler policy** (`auto|dense|fmm`) with dense fallback.
2. Treat **matrix-free iterative** as an outcome of FMM-backed operators, not a separate backend rewrite.
3. Harden and expose **device policy** (`auto|opencl_cpu|opencl_gpu|numba`) for predictable runtime behavior.
4. Defer **CUDA-specific backend** unless post-FMM benchmarks fail target KPIs.
5. Defer custom Cython/Fortran/C++ kernel rewrite unless profiling proves Python-side overhead is a dominant bottleneck.

## Required API Additions
Add optional fields to `SimulationRequest`:
- `assembler_mode`: `"auto" | "dense" | "fmm"` (default `"auto"`)
- `device_mode`: `"auto" | "opencl_cpu" | "opencl_gpu" | "numba"` (default `"auto"`)
- `solver_tolerance`: float (bounded)
- `gmres_restart`: int (bounded)
- `gmres_maxiter`: int (bounded)

Keep defaults equivalent to current behavior when fields are omitted.

## Required Metadata Additions
Return in results metadata:
- `metadata.solver.assembler_selected`
- `metadata.solver.device_selected`
- `metadata.solver.fmm_available`
- `metadata.solver.iteration_count`
- `metadata.solver.gmres_info`
- `metadata.performance.total_time_seconds`
- `metadata.performance.frequency_solve_time`
- `metadata.performance.directivity_compute_time`
- `metadata.performance.time_per_frequency`
- `metadata.performance.reduction_speedup`

## Execution Phases

### Phase 0: Baseline and Harness
Tasks:
1. Create reproducible benchmark harness for small, medium, and large meshes.
2. Record baseline for:
   - dense+numba
   - dense+opencl (where available)
   - symmetry on/off
3. Record:
   - wall time
   - per-frequency time
   - peak memory
   - failure rate
4. Store baselines in versioned docs/test artifacts.

Acceptance:
- Baseline reports are reproducible within ±10% on same machine profile.

### Phase 1: FMM Integration with Safe Fallback
Tasks:
1. Add assembler policy plumbing through:
   - `server/app.py`
   - `server/solver/bem_solver.py`
   - `server/solver/solve.py`
   - `server/solver/solve_optimized.py`
2. Implement deterministic fallback:
   - If `fmm` requested but unavailable, return clear error or auto-fallback based on mode.
3. Add capability detection and expose in metadata/health reporting.

Acceptance:
- Existing solve tests pass unchanged.
- New tests cover policy selection and fallback paths.
- No regression in existing request payload compatibility.

### Phase 2: Matrix-Free Policy Finalization
Tasks:
1. Implement `auto` threshold selection for dense vs fmm by effective problem size.
2. Keep threshold configurable for tuning.
3. Capture iteration telemetry and convergence status.

Acceptance:
- `auto` never selects unsupported mode.
- Small cases do not regress more than 20%.
- Medium/large cases show measurable speedup on validated hardware.

### Phase 3: Device Policy Hardening
Tasks:
1. Implement explicit device selection mode.
2. Preserve runtime recovery from OpenCL buffer errors.
3. Ensure metadata clearly reports requested vs selected device and fallback reasons.

Acceptance:
- Device selection behavior is deterministic.
- Recovery/fallback tests pass.
- Health endpoint reflects device/runtime state consistently.

### Phase 4: CUDA Decision Gate (Optional)
Gate criteria:
- Enter only if Phase 1-3 fail target KPIs.
- Produce separate technical design and PoC first.
- Do not merge CUDA path without dedicated CI and parity thresholds.

## Numerical Validation Requirements
For dense vs accelerated path on reference meshes:
- SPL median absolute delta: <= 0.5 dB
- DI median absolute delta: <= 0.5 dB
- Impedance magnitude median relative error: <= 5%
- No silent failures at any requested frequency point
- Failure count and partial success metadata remain accurate

## Performance Targets
Primary target:
- >= 2x end-to-end speedup on medium/large meshes in validated environments.

Secondary targets:
- Reduced peak memory usage for large problems.
- Stable runtime with explicit fallback behavior.
- No user-visible API breakage.

## Required Test Coverage
Run and keep green:
- `server/tests/test_dependency_runtime.py`
- `server/tests/test_api_validation.py`
- `server/tests/test_mesh_validation.py`
- `server/tests/test_solver_tag_contract.py`
- `server/tests/test_solver_hardening.py`
- `server/tests/test_device_interface.py`
- `server/tests/test_reference_smoke.py` (when enabled)

Also run:
- `npm test`
- `npm run test:server`

## Risk Register
- FMM dependency packaging complexity.
- Runtime variability across OpenCL environments.
- Numerical drift between dense and accelerated formulations.
- Overfitting optimization to one hardware profile.
- Expanding solver complexity without benchmark discipline.

Mitigations:
- Policy-based fallbacks.
- Strict benchmark harness and parity thresholds.
- Feature-flag rollout behavior.
- Metadata transparency for solver mode decisions.

## Rollout Rules
1. Merge in small phases:
   - API + metadata scaffolding
   - FMM mode
   - Auto policy tuning
   - Device mode controls
2. Keep dense path available as stable fallback.
3. Document known environment constraints in `server/README.md` and `docs/PROJECT_DOCUMENTATION.md`.
4. Do not remove existing path until accelerated path proves stable by benchmark and parity criteria.

## Definition of Done
- All required tests pass.
- Baseline and post-change benchmarks published.
- Numerical parity thresholds met.
- Clear solver mode/device metadata available in results.
- Default user workflow remains backward-compatible and stable.
- Documentation updated to match runtime behavior.
