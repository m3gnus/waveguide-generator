# Solver release qualification

Run these gates once for every release wave that changes solver pins, solver
routing, symmetry, meshing, observation mapping, or cancellation. Hosted WG CI
does not own real GPU/OpenCL devices, so passing unit tests is necessary but not
sufficient. Archive the report under `docs/validation/YYYY-MM/` with the exact
WG, mesher, Metal-BEM, and BEMPP-BEM SHAs plus hardware/OS details.

## Apple Silicon owned runner

Build the feature-current native helper, then run the end-to-end formulation and
domain comparison:

```bash
cd hornlab-metal-bem/hornlab_metal_bem/metal/native_helper
swift build -c release

cd waveguide-generator
WG2_RUN_LIVE=1 .venv/bin/python -m pytest \
  server/tests/test_engines_metal_live.py::test_explicit_axisymmetric_path_matches_explicit_full_3d \
  -m live -q
```

This single WG gate must solve and compare all three paths: Axisymmetric
meridian, full-domain 3-D Metal, and quarter-domain 3-D Metal. It pins on-axis
SPL/phase and the complete horizontal pattern. Do not drop the 90-degree sample.

Run the closed-body high-frequency gate as well. It independently compares
full-3D Metal and Axisymmetric against the analytic pulsating-sphere solution at
1 kHz and 16 kHz, including un-normalized complex pressure and integrated DI:

```bash
cd hornlab-metal-bem
python -m pytest \
  tests/test_sphere_grid_native.py::test_native_full3d_pulsating_sphere_matches_circsym_through_16khz \
  -q
```

Run the native coupled-baffle comparisons as well:

```bash
cd hornlab-metal-bem
python -m pytest \
  tests/test_native_coupled_ib_validation.py::test_native_coupled_ib_straight_circular_channel_matches_circsym \
  tests/test_native_coupled_ib_validation.py::test_native_coupled_ib_deep_circular_channel_matches_circsym \
  tests/test_metal_native.py::test_native_executable_coupled_ib_yz_xz_quadrant_matches_full \
  -q
```

## Windows owned runner

Install the release-candidate checkouts into the WG venv. Metal/Swift is not
required and must remain unavailable without affecting Axisymmetric:

```powershell
cd hornlab-metal-bem
python -m pytest tests/test_circsym.py tests/test_circsym_ib.py `
  tests/test_circsym_cross_os_golden.py `
  tests/test_native_coupled_ib_validation.py -q
python scripts/bench_circsym.py --backend cpu --json > circsym-windows-benchmark.json

cd ..\waveguide-generator
python -m pytest server/tests/test_engines_adapters.py `
  server/tests/test_symmetry.py server/tests/test_engines_registry.py -q
python scripts/check_backends.py
```

The report must record the CPU, RAM, Python/NumPy/SciPy/Numba versions, selected
assembly and field backends, total and per-frequency benchmark time, pass/skip
counts, and full tracebacks for failures. Expected skips are Metal-only tests;
portable CircSym physics and both cross-OS goldens must run, not skip.

## Cross-solver coupled infinite-baffle gate

On a host with both packages installed:

```bash
cd hornlab-bempp-bem
python -m pytest \
  tests/test_infinite_baffle.py::test_bempp_coupled_ib_solves_forward_only_and_enforces_aperture_continuity \
  tests/test_infinite_baffle.py::test_bempp_coupled_ib_matches_portable_circsym_absolute_field \
  -q
```

This gate covers the Airy pattern, aperture continuity, rear silence, and the
absolute amplitude/phase convention shared with CircSym.

## External axisymmetric reference status

There is not yet a valid independent ABEC axisymmetric infinite-baffle fixture.
Do not promote an archived result merely because its filename says circular or
infinite baffle: the previously proposed waveguide artifact contains
azimuth-dependent geometry, and the archived circular rolled-lip artifact has an
unresolved mounting-plane intersection and is diagnostic-only. A future external
gate must start from a demonstrably body-of-revolution, flush-mounted model with a
valid interior/aperture interface and retain enough source geometry and solver
metadata for independent reproduction.

Until that fixture exists, report this as an explicit external-reference gap.
The analytic piston/sphere gates and the three independent coupled formulations
(Axisymmetric, full-3D Metal, and BEMPP) remain mandatory, but they must not be
described as ABEC validation.

## Report checklist

- Exact four repository SHAs and whether each worktree was clean.
- Machine, OS/build, CPU/GPU/OpenCL device, RAM, and package versions.
- Passed/failed/skipped totals with reasons for every skip.
- Axisymmetric/full/quarter maximum SPL, phase, and pattern deltas.
- Closed-body Axisymmetric/full-3D level, phase, pattern, and DI deltas at 1 and
  16 kHz.
- Coupled-baffle CircSym/3-D and BEMPP/CircSym maximum deltas.
- CircSym benchmark JSON and compute-backend diagnostics.
- Stop/cancellation result, including observed maximum response latency.
