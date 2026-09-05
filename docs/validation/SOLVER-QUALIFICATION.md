# Solver release qualification

Run these gates once for every release wave that changes solver pins, solver
routing, symmetry, meshing, observation mapping, or cancellation. Hosted WG CI
does not own real GPU/OpenCL devices, so passing unit tests is necessary but not
sufficient. Archive the report under `docs/validation/YYYY-MM/` with the exact
WG, mesher, Metal-BEM, and BEMPP-BEM SHAs plus hardware/OS details.

## BEAT package contract

The BEAT pin `42bbfcf9ec06921eeec2f996e27b1e6873c5bc65` includes startup
cleanup, ownership-aware worker retirement and capability schema version 2.
It preserves `SubmissionClosed` and `backend_capabilities` in the public API.
A host whose runtime retirement raises still explicitly releases its event
stream; session cancellation and a later solve remain separate submissions.

Near correction is supported only on CPU. Metal, CUDA and ROCm refuse it at
configuration time; the refusal is not an implementation of the missing
accelerator correction. Regular quadrature accepts orders 1, 2 and 4 only;
complex or non-unit source amplitudes are refused. WG's adapter uses none of
these refused options. CPU double precision still serializes pressure through
Float32, so do not interpret a Float64 Python array as full-precision output.

For each packaged candidate, verify the installed distribution's PEP 610 SHA
against this pin, then test persisted BEAT startup, immediate solve,
restart/adoption of the same host and Julia child, cancel-then-solve, and an
explicit Metal-BEM selection that starts no BEAT worker. Use an isolated
worker registry and application data directory. Source-suite or wheel-level
lifecycle results do not close the installed-app and physical-platform gates.

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

**Closed for the axisymmetric infinite baffle, 2026-09-03.** An independent
ABEC3 reference now exists and agrees on absolute level, not only on pattern.
The fixture is `benchmarks/abec-g8-circsym-ib/` in the workspace root -- which
is not a git repository, so a copy is being preserved in this repo under
`docs/validation/abec-g8-circsym-ib/`; look in both. It is an ABEC3
`Dim=CircSym` project for an OS-SE waveguide (25.4 mm throat, 45 deg coverage,
120 mm long, 315.1 mm mouth) flush-mounted in an infinite baffle, generated by
ATH V2025-06, solved over 200 Hz - 20 kHz at 100 log-spaced points, with the
`.cfg` it came from, the solved `Results/Spectrum_ABEC.txt`, and `compare.py`
alongside. It is the fixture the paragraph this replaces asked for: a
demonstrable body of revolution (ABEC's whole input is one meridian polyline),
flush-mounted with a valid interior/aperture interface, and reproducible from
text.

Against `hornlab_metal_bem` (pin `368849cc`) at an element cap of 4.29 mm --
ABEC's own lambda/2 at its `MeshFrequency=40000`:

| quantity | <1 kHz | 1-4 kHz | 4-11 kHz | >11 kHz | max |
|---|---|---|---|---|---|
| absolute SPL, rms dB | 0.009 | 0.050 | 0.099 | 0.192 | 0.945 |
| pattern, rms dB | 0.005 | 0.107 | 0.230 | 0.303 | 0.894 |

The -6 dB half-angle agrees within 0.15-0.87 deg from 1 kHz to 20 kHz.
Normalized throat radiation impedance, which is independent of the observation
geometry entirely, has a median relative error of 0.0017 and a maximum of
0.0093.

Halving the element cap to 2.15 mm moves only the top band (SPL 0.192 -> 0.131,
pattern 0.303 -> 0.191) and barely touches anything below 4 kHz, so **the >11
kHz residual is our discretisation, not a formulation difference.** Quote the
converged numbers for any HF claim.

Re-running it needs no ABEC3 licence or GUI: the reference spectrum is frozen
in the fixture, so `compare.py` re-solves only our side and re-reports every
number above. Regenerating the ABEC side does need ABEC3 (F5 then F7 by hand --
it exposes no command line), and that is the only manual step.

### What this does and does not license

It validates **one path**: the CircSym/axisymmetric coupled infinite baffle,
against one external solver, on one body of revolution. Say that, and no more.

- It is **not** a validation of the 3-D engines. Full-3D Metal, quarter-domain
  Metal, BEMPP and BEAT are unvalidated against ABEC and remain qualified only
  by the analytic gates and by agreeing with each other and with CircSym.
- It is **not** a general infinite-baffle claim. One geometry, flush-mounted,
  axisymmetric, rigid-walled, no losses.
- It says nothing about free-standing radiators, ground planes, multi-body
  domains, or any Robin/impedance surface.
- Above 11 kHz the agreement is mesh-limited on our side, so an HF figure
  quoted from the shipped cap understates the engine.

The analytic piston/sphere gates and the three independent coupled formulations
remain mandatory. They are still not ABEC validation, and describing them that
way is still wrong -- what changed is that the CircSym path now has a real
external reference to cite instead.

### Feeding an ABEC mesh to our solvers

Three preparation steps are mandatory, and every one of them fails quietly. A
harness that skips any of them produces a confident, wrong verdict about our
engine.

**1. Subdivide along ABEC's own segments first.** ABEC refines its input from
`MeshFrequency`, and ATH knows that, so it emits the aperture interface as a
single element -- 157.6 mm in this fixture, 4.6 wavelengths across at 10 kHz.
Our solvers do not refine anything. Handed the file as-is the interior stays
finely meshed and **nearly correct**, with radiation impedance still landing
within a few percent, while the radiated field collapses: the -6 dB half-angle
came out **3.9 deg against ABEC's 45.6 deg at 10 kHz**, a 40 dB pattern error
produced by one element. Subdivision inserts points on the straight segments
that are already there, so the body is unchanged.

> **The failure signature: interior right, far field garbage.** A healthy
> radiation impedance is not evidence the mesh is adequate -- it is the thing
> that makes this failure survive review. Check a pattern, not an impedance.

**2. Feed ABEC's polyline from `nodes.txt`, not the analytic curve.** ABEC
subdivides its truncated cones linearly, so the surface it actually solves lies
on those segments. Resampling the underlying analytic profile instead compares
two different bodies and charges the geometric difference to the solver.

**3. Conjugate ours into ABEC's before comparing anything phase-bearing.** All
three HornLab engines use the `e^{-i omega t}` time factor, whose Green's
function is the outgoing `e^{+ikr}/4*pi*r`. ABEC documents an `e^{-jkr}`
kernel, which is the `e^{+j omega t}` time factor -- the conjugate. The two
statements name different objects and are consistent; they are not a
contradiction, and neither manual is wrong. The visible symptom is that
radiation impedance real parts agree to under 0.5% while the imaginary parts
are equal and opposite at every frequency, and that a mass-like reactance reads
negative-imaginary for us and positive for ABEC. Magnitudes, SPL and patterns
are unaffected, so a normalized-polar-only comparison will never see it -- one
more reason the absolute and impedance quantities are compared here at all.

Also match ABEC's constants where you can, and report where you cannot. ABEC
defaults to c = 343.32 m/s and rho = 1.205, and takes its polar arc from
`Offset`, which for an infinite baffle is clamped to the mouth plane -- not the
throat, which is the opposite of the free-standing ASRO reference.
`hornlab_metal_bem.SolveConfig` exposes `air_density`, and `speed_of_sound`
joins it in hornlab-metal-bem#7; `hornlab_beat_bem.SolveConfig` has had
`sound_speed` all along. In every case the default stays 343.0, so a harness
that leaves it alone runs 0.093% slow against ABEC and should say so.
`hornlab_bempp_bem` still hardcodes the value with no knob at all.

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
