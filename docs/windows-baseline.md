# Windows baseline (measured)

Measured values for the Windows install and solve path. Every number here was
produced by running the documented commands on the machine described below.
Nothing is estimated or copied from a spec sheet.

## Host

Measured by real API enumeration, not marketing specifications.

| | |
|---|---|
| OS | Windows 11 Pro 10.0.26100 build 26100, x64 |
| Platform | QEMU/KVM virtual machine, `Standard PC (Q35 + ICH9)`, EDK II firmware |
| CPU | AMD Ryzen 7 5825U, 12 logical processors exposed to the guest |
| RAM | 16.00 GB |
| Python | 3.13.3 (venv at `.venv`) |
| Node | 24.14.0, npm 11.19.0 |

### Graphics and compute

**There is no GPU compute device on this host.** The Ryzen's integrated Radeon
GPU is not passed through to the VM.

| Probe | Result |
|---|---|
| Display devices | `Red Hat VirtIO GPU DOD controller` (display-only), `Microsoft Remote Display Adapter` |
| DXGI `EnumAdapters1` | 3 adapters, all `Microsoft Basic Render Driver`, VendorId `0x1414`, 0 MB dedicated |
| Hardware D3D12 adapters | **0** |
| `D3D12CreateDevice` | Succeeds at feature level `12_1`, but only against WARP (a CPU software rasterizer) |
| Vulkan `vkCreateInstance` | `VK_ERROR_INCOMPATIBLE_DRIVER (-9)`; no ICD registered |
| OpenCL | 1 platform (`Intel(R) OpenCL`), 1 device: `AMD Ryzen 7 5825U`, type **CPU**, 12 CU, fp64 supported. No GPU device. |

`D3D12CreateDevice` returns `S_FALSE` (`0x1`), not `S_OK`, when `ppDevice` is
NULL. A capability probe that tests `hr == 0` will report a false negative.

## Prerequisite that Windows does not ship

The **Microsoft Visual C++ Redistributable** is required and is not present on a
clean Windows install. Without it, numba's compiled `_typeconv` extension cannot
load, bempp-cl cannot import, and no solve can run — while the pure-Python
`hornlab_bempp_bem` wrapper still imports successfully, so naive readiness checks
report a working backend.

Verify with:

```
node scripts/run-backend-python.js server/scripts/check_solver_engine.py
```

## Install

`install\install-and-update.bat`, run from a path containing a space
(`C:\Users\...\Hornlab - Workspace\waveguide-generator`).

| Run | Result |
|---|---|
| First install, before fixes | **exit 1** at 261 s — strict preflight audited the system Python because `PYTHON_BIN` leaked from cmd.exe |
| After interpreter fix, VC++ still missing | **exit 1** at 165 s — engine check correctly names the missing `msvcp140.dll` |
| After VC++ redistributable installed | **exit 0** at 170 s, preflight READY, solve genuinely works |
| Rerun, before the venv-probe fix | **exit 0** at 195 s — rebuilt a healthy `.venv` every time |
| Rerun, after the venv-probe fix | **exit 0** at **58 s** — `.venv` reused |

Update path, verified by fast-forwarding a clone three commits behind: exit 0,
relaunch clean, no corruption. Before the fix this failed, because `git pull`
rewrote `install.bat` while cmd.exe was executing it and cmd resumed at a stale
byte offset, landing mid-URL and trying to run `ttps:` as a command.

## Test suites

| Suite | Baseline | After fixes |
|---|---|---|
| `npm test` | 2 failures (hardcoded POSIX separators) | **541 tests, 517 pass, 0 fail**, 24 skipped |
| `npm run test:server` | 265 tests, 4 failures + 3 errors | unchanged — all 7 are pre-existing and unrelated to the install path |

Pre-existing `test:server` failures, recorded for completeness:

- `test_circsym_integration` — the module imports `pytest`, which is not a
  declared dependency, so `unittest discover` cannot load it.
- `test_updates_endpoint` (4 cases) — exercise real `git` repository state.
- `test_mesh_artifact_persistence_failure_does_not_abort_simulation`.
- `test_export_file_writes_to_workspace_subdirectory` — asserts a POSIX
  separator (`jobs/horn_12`) against a `path.join` result.

## Solve baseline

Real solve through `solver.bempp_solver.solve_bempp_from_msh`.

Mesh: reference R-OSSE horn from `npm run diag:mesher:reference-horn`.

| | |
|---|---|
| Vertices (P1 DOFs) | 898 |
| Triangles | 1792 |
| Tag counts | `1` (wall) 1744, `2` (source) 48, `3` 0, `4` 0 |
| Units | m |

Backend: `hornlab-bempp-bem` / bempp-cl. Both assembly backends measured on the
identical mesh, frequencies and precision. numba was forced by stubbing
`opencl_runtime_status`, so the comparison is like-for-like.

| Backend | Cold 1 freq | Warm 1 freq (median) | Warm 5 freq | Per frequency |
|---|---|---|---|---|
| numba | 62.74 s | 2.592 s | 12.91 s | 2.582 s |
| **OpenCL CPU** | **0.78 s** | **0.758 s** | **3.56 s** | **0.711 s** |

- OpenCL-CPU is **3.63x** faster than numba on a warm 5-frequency sweep, and
  **80x** faster cold.
- Agreement between the two backends: **max |ΔSPL| = 1.433e-05 dB**.

**The Windows baseline any new backend must beat is 0.711 s/frequency.** An
earlier revision of this document recorded 2.75 s/frequency; that figure was
measuring numba, which was running only because bempp-cl's OpenCL path was
silently broken (see below). It was never the intended configuration.

OpenCL-CPU here is the Intel CPU OpenCL runtime driving the AMD CPU. It is not
GPU acceleration; there is no OpenCL GPU device on this host.

The 5-frequency sweep still costs essentially 5x the single-frequency solve on
both backends, so per-frequency setup is fully repeated and there is no
amortization across a sweep. That remains the clearest optimization target.

## cpubem reference and reproducible benchmark

`server/solver/cpubem` is a complex128 NumPy/SciPy correctness reference. It is
deliberately not registered as an application backend. Its multi-minute parity
suite is excluded from the default server test run and can be enabled with:

```powershell
$env:CPUBEM_SLOW_TESTS = "1"
npm.cmd run test:server
```

The whole-workload benchmark uses the same mesh, frequencies, source convention,
observation coordinates, and process for cpubem and Bempp:

```powershell
npm.cmd run bench:cpubem -- `
  --backends cpubem bempp-opencl `
  --frequencies 1000 `
  --repeats 3 `
  --threads 4 `
  --precision double `
  --observation-count 37 `
  --output benchmarks/cpubem/windows-reference-2026-07-30.json
```

The command records mesh identity, host/runtime versions, complex pressure,
source impedance, errors, compiler-workaround status, and phase timings. Failed
backends are retained in the JSON alongside any successful partial runs. Local
OpenCL, pytools, and Numba caches stay under `server/.benchmark-cache`.

Measured on the host above, at 1000 Hz with 37 points in each of two observation
planes:

| Backend | Repeats | Median total | Notes |
|---|---:|---:|---|
| cpubem | 3 | 16.596 s | 16.122 s median assembly; 0.324 s LU; 0.110 s field |
| Bempp OpenCL CPU | 3 | 1.553 s | cold first run 24.169 s; warm runs 1.256 s and 1.553 s |

The cpubem assembly phase consumes about **97%** of its per-frequency work. On
this full double-precision workload it is **10.7x slower** than the median Bempp
OpenCL CPU run. This is a different workload from the 0.711 s/frequency
five-frequency production baseline above, so the two absolute timings should
not be substituted for each other.

Correctness on the same run:

- complex field pressure maximum relative error: `2.258e-14`
- complex field pressure maximum phase error: `6.222e-14 rad`
- source impedance maximum relative error: `3.411e-15`

The measured result is committed at
`benchmarks/cpubem/windows-reference-2026-07-30.json`.

The same harness was also run for Bempp OpenCL CPU alone at 500, 750, 1000,
1500, and 2000 Hz. The two warm repeats took 5.789 s and 5.801 s total, or a
1.160 s/frequency median including mesh loading and 74-point field evaluation.
Individual warm frequency solves ranged from 0.985 s to 1.073 s. There is no
material frequency-to-frequency amortization; five solves still cost almost
exactly five times one warm solve. The result is committed at
`benchmarks/cpubem/windows-bempp-sweep-2026-07-30.json`.

## Optimized Bempp and native symmetry

The production implementation remains in `hornlab-bempp-bem`; creating a
second public BEM solver would duplicate mesh validation, boundary conditions,
observation handling, and result contracts. Exact source-support restriction,
phase instrumentation, and half/quarter image assembly are internal paths in
the existing package.

Current production settings use OpenCL CPU, single precision, quadrature order
4, GMRES tolerance `1e-6`, and exact restriction of the DP0 Neumann space to
the nonzero source support. Reducing regular quadrature to order 3 did not
converge, and reducing only singular quadrature produced `0.02394 dB`
normalized-directivity error, so order 4 remains the measured correctness
floor for this mesh.

Warm public-sweep timing over 500, 750, 1000, 1500, and 2000 Hz, including
field evaluation:

| Domain | Mesh | Warm seconds/frequency | Result |
|---|---:|---:|---|
| Full, optimized | 898 vertices / 1792 triangles | 0.499 | reference production path |
| Half (`quadrants=14`, `yz`) | 479 / 896 | 0.517 | roughly break-even on this small CPU case |
| Quarter (`quadrants=1`, `yz+xz`) | 260 / 458 | 0.396 | 21% faster than optimized full |

The quarter result is about 2.36x faster than the original 0.933
seconds/frequency full-domain double/LU run measured during this optimization
work. The half system needs GMRES restart 100; it converged in 43--52
iterations, while the corresponding full expanded default-restart solve
reached 5000 iterations without convergence. Larger dense meshes should benefit
more strongly from halving or quartering the tested equation rows, but that
scaling claim has not yet been benchmarked.

**These three numbers are superseded by the next section.** They were dominated
by two fixed per-call overheads that have since been removed, which is also why
half symmetry appeared not to pay off.

## Removing the fixed per-call overheads

The table above hid its own explanation: `slp_assembly_s` was 0.085--0.092 s in
*every* configuration measured -- full, half, and quarter alike -- even though
the quarter SLP has only 12 trial elements. Cost that does not move when the
problem shrinks fourfold is not arithmetic. Two independent fixed overheads
accounted for most of the per-frequency budget.

### bempp-cl rebuilds its OpenCL program on every assembly

`bempp_cl.core.opencl_kernels.get_kernel_from_operator_descriptor` calls
`build_program` unconditionally, and `build_program` re-enters the full pyopencl
path each time -- source read, cache-key hash, disk-cache lookup,
`Program.build`, and the `Program.__getattr__` kernel-introspection path -- for
roughly 20--25 ms per call on this host. There are three such calls per operator
assembly and four operator assemblies per frequency (boundary DLP and SLP plus
both far-field potentials).

None of it depends on frequency. The wavenumber reaches the kernel as a runtime
buffer argument, never as a compile-time `-D` macro, so one built program serves
an entire sweep, however long: each case needs 9 or 10 distinct programs.
Memoizing `build_program` on its full build key is implemented in
`hornlab_bempp_bem/_opencl_program_cache.py` and enabled from
`configure_opencl`. Measured on a 1792-triangle DLP assembly the fixed cost is
about 118 ms, independent of mesh size -- 56% of assembly time at 630 dofs and
over 90% below 240 dofs.

Counting the calls on a 20-frequency quarter sweep settles how much is left,
and counts are immune to CPU contention:

| | `build_program` calls | Real pyopencl builds | Time in builds |
|---|---:|---:|---:|
| Without the cache | 200 (10 per frequency) | 200 | 4.66 s, **65.8% of wall** |
| With the cache | 200 (10 per frequency) | **0** | 0.008 s, **0.2% of wall** |

Ten distinct programs serve the whole sweep and every one is built during
warm-up. This particular cost is now zero and cannot be reduced further; any
remaining assembly gain has to come from the buffer uploads, the singular
correction, or the kernel launches themselves.

The key has to carry the OpenCL **context**, not just the device type.
`set_default_cpu_device` builds a new context of the same type, and a kernel
cached under a context-free key is then launched against buffers belonging to
the new one, failing with `INVALID_MEM_OBJECT`. The cache is also thread-local:
`pyopencl.Kernel` is mutable and invoking it sets its arguments, so without the
memo every assembly built its own kernel and concurrent solves could not
collide — sharing one would have introduced that race.

### OpenBLAS threading is negative for LAPACK LU at these sizes

LAPACK's LU scales badly at waveguide matrix sizes: a 260x260 complex solve
takes 1.73 ms on one thread and 28.86 ms on twelve, and 479x479 takes 7.02 ms
against 49.07 ms. GEMM does **not** behave that way -- an 898x898 product goes
the other way, 7.7 ms on twelve threads against 42 ms on one -- so this is
specific to the panel factorization, which synchronises far more than the work
justifies at this size.

That asymmetry is why the limit is scoped rather than global. Pinning the whole
process would pay for the factorization by taxing every other array operation
in it, including the symmetry reduction's own dense product. NumPy and SciPy
each bundle their own OpenBLAS and both export a runtime thread setter
(`scipy_openblas_set_num_threads64_` and `scipy_openblas_set_num_threads`), so
`hornlab_bempp_bem/_blas_threads.py` limits threads around the dense solve
only and restores the previous count on exit. It is best-effort: an unrecognised
BLAS build or a missing symbol leaves the thread count alone rather than
failing a solve over a performance hint.

### Result

Warm public-sweep timing, same five frequencies, same meshes, same settings,
including field evaluation:

| Domain | Mesh | Without program cache | With | Speedup |
|---|---:|---:|---:|---:|
| Full | 898 / 1792 | 0.435 | 0.238 | 1.83x |
| Half (`quadrants=14`, `yz`) | 479 / 896 | 0.350 | 0.194 | 1.81x |
| Quarter (`quadrants=1`, `yz+xz`) | 260 / 458 | 0.324 | 0.116 | 2.79x |

Both columns already carry the other two unconditional changes -- scoped BLAS
threading and the singular workgroup sizing below -- so this isolates the
program cache alone. Against the original 0.508 s/frequency full-domain
baseline, quarter symmetry with everything enabled is **4.4x** faster.

The cache removes a *fixed* per-assembly cost, so its relative value falls away
as the mesh grows and arithmetic takes over:

| Triangles | Speedup from the program cache |
|---:|---:|
| 458 (reference quarter) | 2.73x |
| 1792 (reference full) | 1.62x |
| 2275 (ASRO2 quarter) | 1.21x |
| 9104 (ASRO68 full) | 1.06x |

It is therefore a large win on the mesh sizes the application actually uses and
close to free on the largest. Symmetry is the reduction that keeps scaling.

Absolute timings on this host drift by well over +-10% between processes and
depend heavily on what else is running, so compare within a row rather than
across separate runs, and treat the ratios as the result rather than the
seconds. Independent re-runs of the program-cache column alone have landed
between 1.13x and 1.61x for the full domain and between 2.31x and 2.82x for the
quarter. The committed artifact is
`benchmarks/cpubem/windows-bempp-opencl-program-cache-2026-07-30.json`;
regenerate it with `scripts/diagnostics/bench-opencl-program-cache.py`, which
records the ratios and the parity checks alongside the raw seconds.

The program cache is a pure memoization: normalized directivity, complex
pressure, and impedance are all **bitwise identical** across all three domains,
in every run. Scoping BLAS threads changes results by at most `1.8e-5 dB`,
which is floating-point reassociation only.

End to end, the whole body of work reproduces the previously pinned package
(`5c0b751`, which predates the symmetry work) to `1.1e-4 dB` normalized
directivity and `1.4e-7` relative impedance on the 9104-triangle ASRO68
reference at 100 Hz, 1 kHz, 1.5 kHz and 3 kHz, with identical GMRES iteration
counts. None of it changes the physics.

Half symmetry is no longer break-even. Once the fixed overhead is gone the
reduction scales as it should -- per-frequency DLP assembly falls 199.6 ->
104.8 -> 59.1 ms across full, half, and quarter, against an ideal 1 : 0.5 : 0.25
-- and half is 1.4x faster than full rather than marginally slower.

The remaining per-frequency budget is now genuinely dominated by arithmetic.
For quarter: DLP assembly 59.1 ms (50%), linear solve 38.6 ms (33%), SLP
assembly 11.5 ms (10%). Far-field evaluation fell from 84 ms/frequency to
2 ms/frequency, so the previously planned NumPy rewrite of the representation
formula would now recover at most 1% and is not worth doing.

A direct LU is faster than GMRES for the reduced systems once BLAS threading is
fixed (quarter 4.5 ms against 12.1 ms) but loses at full-domain size (89.6 ms
against 41.5 ms), and the sweep totals are within noise of each other. GMRES
remains the default; `solver="auto"` with `lu_threshold` is the knob if a future
mesh makes the tradeoff clearer.

**GMRES frequency continuation does not pay** and is deliberately not
implemented. Reusing the previous frequency's converged solution as `x0` was
measured over a 24-frequency 500 Hz - 8 kHz sweep at only **4.0%** fewer total
iterations on the 458-triangle quarter and **4.4%** on the 2275-triangle ASRO2
quarter. It helps at the bottom of the sweep (27 -> 23 iterations) and stops
helping as soon as the wavelength shortens, making several individual
frequencies *worse* (47 -> 48, 46 -> 51, 65 -> 66) because a guess that is
wrong in a different Krylov subspace can beat starting from zero. scipy's test
is `norm(b - A @ x) <= rtol * norm(b)`, relative to the right-hand side, so the
tolerance is not the reason. Do not re-try this without a phase-corrected
extrapolation.

Each reduced solve was compared with an independently solved, explicitly
expanded mirror mesh at 1000 Hz:

- quarter maximum normalized-directivity difference: `0.001694 dB`;
  impedance-magnitude difference: `0.000110 dB`
- half maximum normalized-directivity difference: `0.001369 dB`;
  impedance-magnitude difference: `0.002143 dB`

Artifacts:

- `benchmarks/cpubem/windows-bempp-quarter-final-2026-07-30.json`
- `benchmarks/cpubem/windows-bempp-half-final-2026-07-30.json`
- `benchmarks/cpubem/windows-bempp-quarter-geometric-mass-parity-2026-07-30.json`
- `benchmarks/cpubem/windows-bempp-half-expanded-lu-parity-2026-07-30.json`

These parity checks validate the symmetry reduction, not the absolute physical
solution of the underlying standard integral equation. Independently meshed
full expansions showed strong mesh sensitivity near 1000 Hz, consistent with
an interior-resonance/conditioning problem inherited from the standard BIE;
the present small complex-k shift did not remove it. Until a symmetry-compatible
combined-field/Burton--Miller formulation and ABEC comparison are available,
avoid treating results near such resonances as physically validated merely
because reduced/full parity passes.

## What is left in the assembly path

With the program builds at zero, three independent investigations profiled what
remains. Two agreed on most of it; where they disagreed the disagreement was
itself informative, and one shared hypothesis turned out to be wrong.

### bempp-cl's singular workgroup is smaller than it needs to be

The singular assembler fixes `WORKGROUP_SIZE_GALERKIN = 16` and splits each
element pair's quadrature points across that many work items:

```python
local_quad_points = number_of_quad_points // WORKGROUP_SIZE_GALERKIN
```

That is a **floor** division, and the kernel loop runs
`WORKGROUP_SIZE * local_quad_points` points, so any remainder is silently
discarded. The point counts per adjacency class, captured from a real assembly:

| Singular order | Point counts | 16 | 32 | 64 |
|---:|---|:--:|:--:|:--:|
| 2 | 32, 80, 96 | yes | no | no |
| 3 | 162, 405, 486 | **no** | no | no |
| 4 | 512, 1280, 1536 | yes | yes | **yes** |
| 5 | 1250, 3125, 3750 | **no** | no | no |
| 6 | 2592, 6480, 7776 | yes | no | no |

Two things follow. Stock bempp-cl **drops quadrature points at odd singular
orders** — an upstream defect worth reporting. And at order 4, this package's
default and its measured accuracy floor, every count divides 64, so a wider
workgroup integrates exactly the same points and is simply faster: **1.21x** on
the whole double-layer assembly for both the 458- and 2275-triangle quarter
meshes, at a maximum relative matrix difference of `8e-8`.

`hornlab_bempp_bem/_opencl_singular_workgroup.py` therefore picks, per assembly,
the largest of (64, 32, 16) that divides every count. Where none does it leaves
the stock size alone and warns, rather than changing which points are lost or
dropping to a workgroup of one and becoming an order of magnitude slower. At
orders 2 and 6 it selects 16 and the assembled matrix is **bitwise identical**
to stock; only order 4 changes, by summation reordering.

It is tempting to blame the dropped points for the accuracy penalty already
recorded at singular order 3. **Measurement refutes that**: integrating order 3
exactly, with a workgroup of 1 that divides all three counts, lands within
`0.006 dB` of the truncated result — both about `1.02 dB` from order 4 on the
reference quarter. The order-3 penalty is the order, not the missing points.

### Vector width depends on the trial count, not the mesh

Two benchmarks of `VECTORIZATION_MODE` appeared to contradict each other: one
found forcing `vec16` to be within noise on a 2275-triangle square assembly, the
other found 1.11x on the ASRO2 *symmetry* path. They are consistent — the
symmetry path tests 2275 elements against an expanded trial space of 9100, and
the win tracks the trial count. Re-measured directly, interleaved:

| Assembly | test x trial dofs | auto (vec8) | vec16 | speedup |
|---|---:|---:|---:|---:|
| q1 symmetry | 260 x 918 | 49.2 ms | 45.4 ms | within noise |
| ASRO2 symmetry | 1209 x 4552 | 613 ms | 561-572 ms | **1.07-1.09x** |

`SolveConfig.vectorization_mode` now exposes this, defaulting to `"auto"` —
bempp-cl's own behaviour. It is left off by default because the useful value
depends on the device's native vector width (8 on this CPU) and forcing 16 has
not been measured on hardware that is natively 16 or 4. `novec` is 3-5x slower
and `vec4` about 40% slower; neither is ever worth selecting.

### Measured but not taken

Each of these is real and was verified by at least one investigation, but every
one replaces a bempp-cl *function body* rather than wrapping it, which is a
materially higher maintenance risk than the workarounds above. They are recorded
here so the measurements are not lost:

- **`np.add.at` scatter in `assemble_dense`.** Replacing it with a precomputed
  grouped `reduceat` is 7-8x faster on the scatter itself (ASRO2: 32.4 ms ->
  4.2 ms), independently confirmed by both investigations. That is only about 4%
  of a whole frequency, needs a per-space index cache, and is not bitwise.
- **Caching the Duffy singular-rule machinery**, rebuilt per operator per
  frequency although it depends only on (grid, spaces, order). Bitwise
  identical; roughly 2% of a frequency.
- **DSATUR element colouring.** Fewer, better-balanced colour groups cut kernel
  launches (170 -> 126 at 458 triangles). Worth about 4% at 9104 triangles, but
  a naive implementation costs 5.9 s to compute at that size and needs its own
  cache to break even.
- **Frequency-invariant device buffers.** Both investigations measured this as
  worthless and one explained why: the invariant inputs total 1.46 MB at 9100
  triangles against a 166 MB frequency-dependent result buffer.
- **`assembly.dense.workgroup_size_multiple`** is a dead knob — identical launch
  counts and bitwise-identical matrices at every value. It is referenced nowhere
  in bempp-cl outside its own parameter definition.

## ASRO reference: accuracy against ABEC3

The ATH ASRO reference in `misc/ATH results 0 degree norm` provides both a
full-domain mesh (`250917asro68.msh`, 4554 vertices / 9104 triangles) and a
quarter of the *same* R-OSSE horn (`asro2.msh`, 1209 / 2275 — identical edge
statistics), plus ABEC3's own `Spectrum_ABEC.txt`: 40 log-spaced frequencies
from 100 Hz to 20 kHz, three polar planes, 37 angles, 2 m, normalised on axis,
acceleration drive.

Two setup facts had to be established first. ABEC measures the 2 m polar from
the **throat**, not the mouth: `origin="throat"` agrees to `0.055 dB` at 100 Hz
where `origin="mouth"` is out by `1.415 dB`. And `Sym=xy` in ABEC's
`solving.txt` means *"I have given you only part of the model, mirror it"* —
`Sym=x` names the plane whose **normal** is x, so `Sym=xy` is a quarter-space
reduction, not an infinite baffle. That matters, because the shipped project
pairs it with the **full** mesh (see below).

### Read the main lobe, not the worst angle

These polars are normalised on axis. The worst angle on such a polar is almost
always a deep rear null, where a fraction of a dB of absolute pressure appears
as tens of dB relative and *neither* code is converged — ABEC disagrees with
itself by up to 33 dB back there. Worst-of-all-angles is therefore close to
meaningless as an accuracy metric, and an earlier revision of this section
reported it, which made a validated solver look unvalidated.

Gating on ABEC's own normalised level > -20 dB, rms deviation, on matched
mesh pairs (identical `.msh` file solved by both codes):

| Band | ours full vs ABEC full | ours quarter vs ABEC quarter |
|---|---:|---:|
| 100 Hz - 1 kHz | **0.077 dB** | **0.079 dB** |
| 1 - 4 kHz | **0.182 dB** | **0.180 dB** |
| 4 - 11 kHz | **0.230 dB** | **0.228 dB** |
| 11 - 20 kHz | 2.185 dB | 1.893 dB |

Inside the -6 dB main lobe the two codes agree to 0.01 - 0.25 dB at every
frequency to 13 kHz, and -6 dB beamwidths agree to +0.2 - +1.7 deg. The two
matched pairs landing within 0.002 dB of each other says the residual is a
reproducible discretisation difference — ABEC uses constant elements collocated
at triangle centroids, we use P1-continuous on vertices — and not scatter.

For contrast, our own full-vs-quarter self-consistency is `0.001 dB` through
4 kHz against ABEC's `0.013 - 0.027 dB`, so the symmetry reduction is the
better-behaved of the two.

Reproduce with `benchmarks/abec-validation/compare_abec.py`; that directory also
holds both ABEC exports and both of our polar sets.

### The shipped reference project was misconfigured

`solving.txt` declares `Sym=xy` next to the full 4-quadrant mesh, so ABEC solved
four superimposed copies of the horn — a pitfall ABEC's own manual names
("if two elements occupy the same spot in space and belong to the same
sub-domain then the solver cannot yield exact results because of ambiguity").

**ATH is not at fault.** Sweeping `Mesh.Quadrants` through the same `ath.exe`
build emits `Sym=xy` for `1`, `Sym=y` for `12`, `Sym=x` for `14`, and **no
`Sym` clause** for `1234`. The shipped file is a stale leftover from an earlier
quarter-mesh run: regenerating gives a byte-identical mesh and a `solving.txt`
differing only by the absent clause.

Re-running ABEC with that one line removed puts the cost at 0.021 dB below
1 kHz, 0.052 dB at 1 - 4 kHz, 0.111 dB at 4 - 11 kHz and 0.636 dB above — small,
because the images landed back on the same bi-symmetric surface and on-axis
normalisation divides out the level error. Correcting it does tighten ABEC's own
full-vs-quarter agreement by 2 - 4x, which identifies the stale clause as the
source of ABEC's internal inconsistency.

### What the earlier divergence hunt ruled out

Before the angle-resolved comparison above existed, the 1 - 6 dB worst-angle
figures at 1.2 - 3.4 kHz looked like a real divergence and were chased hard.
All of it came back negative, and the eliminations remain useful evidence that
the solver is sound. It is **not**:

- **mesh resolution.** Uniform 1-to-4 subdivision of the same surface (2275 ->
  9100 quarter triangles, four times ABEC's own density) moves our answer by
  only 0.03 - 0.71 dB while the worst-angle gap to ABEC stays at 6 - 9 dB. Our
  solve is converged; refining does not move it toward ABEC. (That gap is now
  known to be rear-null placement — see the main-lobe table above.)
- **formulation, including the standard BIE's interior-resonance
  non-uniqueness.** This was the leading suspect: the horn is a closed shell
  (`WallThickness = 6`) with a real interior, and the standard exterior BIE is
  non-unique at that volume's interior Dirichlet eigenfrequencies. Burton-Miller
  exists to remove exactly that. Run on the full domain (it is unsupported with
  symmetry) it changes **nothing**: rms deviation 0.704 dB against standard's
  0.705, and per-frequency worst-case 6.08 vs 6.05 dB at 1321 Hz, 5.75 vs 5.97
  at 1734 Hz, 4.45 vs 4.17 at 2986 Hz -- at ten times the cost, 402 s against
  41 s. `complex_k` at the default 0.005 shift is likewise indistinguishable
  (0.677 dB); raising the shift to 0.05 makes things three times worse (2.120 dB)
  by damping real physics. Caveat: Burton-Miller failed to converge at 2275 Hz,
  so that one frequency is not a clean comparison; the other three are.
- **the symmetry reduction.** The quarter and the full domain agree with each
  other to about `0.01 dB` through 1.5 kHz (main-lobe rms is tighter still,
  `0.001 dB` through 4 kHz), while the worst-angle figure against ABEC was 6 dB.
  (They do separate from each other higher up, once neither mesh resolves the
  field.)
- **the driver velocity model.** `source_motion="axial"` (rigid piston) and
  `"normal"` (breathing cap) agree to within `0.4 dB` of each other; the cap is
  nearly flat.
- **anything introduced by this work.** See the regression check below.
- **a residual observation-origin offset.** Choosing throat over mouth fixed
  1.4 dB at 100 Hz, but our throat point is *inferred from the mesh* while ABEC
  measures from whatever its project defines, and a few-millimetre residual
  would be invisible at 100 Hz and a large fraction of a wavelength by 10 kHz --
  the same growing signature. Sliding the observation origin along the axis from
  -40 mm to +80 mm gives rms deviations of 0.798 (-20 mm), 0.742 (-10), 0.721
  (-5), **0.707 (0)**, 0.703 (+5), 0.717 (+10), 0.896 (+20). The scan is
  sensitive -- +20 mm nearly doubles the peak error to 13.1 dB -- so it would
  have found a real offset, and it does not: zero is already the minimum within
  noise. The inferred throat also sits essentially on the coordinate origin
  (0.708 rms from (0,0,0) against 0.707 from the inferred point). **Not the
  cause.**
- **a speed-of-sound mismatch.** This one fits the signature well enough to be
  worth testing: at long wavelength the pattern is omnidirectional so an error
  in `ka` does nothing, while at short wavelength the lobe angles depend on
  `ka`, so a constant relative error grows into large dB differences. Solving at
  `f * 343/c` for assumed `c` from 330 to 350 m/s and comparing against ABEC at
  `f` gives no sharp minimum: rms deviation is 0.68 dB at 340, 0.78 at 341, 0.68
  at 342, 0.71 at 343, 0.77 at 344, rising to 1.17 at 350. The scan is
  non-monotonic around 343 and shallow, which is what noise looks like rather
  than a mis-set constant. **Not confirmed.** ABEC's documented default is
  `c = 343.32 m/s`, `rho = 1.205` — a 0.09% difference in `c`, well inside the
  scatter of that scan, and `rho` cancels under normalisation.
- **a polar convention mismatch.** Tested by transforming the reference and
  re-differencing: swapping the H and V planes makes rms *worse* (8.99 -> 11.09
  dB), reading the grid as 0 - 90 deg in 2.5 deg steps far worse (17.93), and
  shifting the frequency index by ±1 worse (10.93 - 11.50). ABEC's abscissa is
  `logspace(100, 20000, 40)` to 3.8e-15 and its angle grid is stated in its own
  header as `0,5,…,180`. Fitting the off-axis phase gives apparent phase centres
  agreeing to `0.01 mm` at 510 Hz, so there is no arc-centre offset either. The
  identity mapping is correct. (It also surfaced a harmless convention
  difference: ABEC uses `e^-jkr`, Bempp `e^+ikr`, so phases are conjugated.)
- **an interior mode or non-uniqueness.** ABEC's radiation impedance rises
  smoothly, never exceeds 1, and shows no ripple — no enclosed-volume signature.
  ABEC also applies non-uniqueness compensation (Dual Surface Method) by default
  when `NUC=` is unspecified, so it was never exposed to the failure mode
  Burton-Miller was tried against.

The one thing that *is* real: `ABEC.MeshFrequency = 1000` sized this mesh for
1 kHz and nothing more. Worst element is 30.6 mm — 11.2 per wavelength at 1 kHz,
3.3 at 3.4 kHz, **0.56 at 20 kHz**. Above roughly 11 kHz the mesh has run out of
elements for *both* codes, and the cross-code figures there (1.9 - 2.2 dB) sit
alongside ABEC disagreeing with itself by 0.64 dB and us with ourselves by
0.40 dB. Nothing in that band is a reference for anything; it needs a finer
export, not a better solver.

### Where our own answer is converged, measured

The 11 kHz boundary is not inferred from the ABEC comparison. A second ATH
export of the same horn at 4.4x the density — `Mesh.AngularSegments` 50 -> 104,
`LengthSegments` 20 -> 40, `Rear/Mouth/ThroatResolution` 25/8/5 -> 5/4/3 mm,
giving 11650 quarter triangles against 2275 and a worst element of 6.95 mm
against 30.63 — moves our own answer by, main-lobe rms:

| Band | our coarse-vs-fine movement |
|---|---:|
| 100 Hz - 1 kHz | 0.017 dB |
| 1 - 4 kHz | 0.014 dB |
| 4 - 11 kHz | **0.065 dB** |
| 11 - 20 kHz | **2.198 dB** |

Per frequency the transition is sharp: 0.217 dB at 8852 Hz, 0.416 at 10140,
0.678 at 11615, 1.086 at 13305, 15.271 at 20000. So our solution is
h-converged to `0.065 dB` through 11 kHz and not converged above it,
independently of ABEC.

Two things follow. The cross-code residual below 11 kHz (0.08 - 0.23 dB) is
comfortably *larger* than our own discretisation error (0.014 - 0.065 dB), which
is what makes it attributable to the basis difference rather than to our mesh.
And our refined solve disagrees with ABEC's coarse result *more* than our coarse
solve does above 11 kHz (3.62 vs 1.89 dB) — the expected sign when moving toward
convergence and away from an unconverged reference.

The refined ATH config is `benchmarks/abec-validation/ath-fine1-config.txt`; the
mesh it generates is the one used by ABEC project `C_fine_quarter_sym`. Note
`ABEC.MeshFrequency` is deliberately left at 1000 there: it is a subdivision
trigger (`EdgeLength = 0.5*c/f` = 171.5 mm), so keeping it low guarantees ABEC
solves the elements ATH wrote instead of subdividing them, which is what keeps
both codes on an identical mesh.

What the eliminations add up to is worth stating plainly. Our solution is
**mesh-converged** (4x refinement moves it ≤0.71 dB) and
**formulation-independent** (standard, complex-k and Burton-Miller agree with
each other to ~0.03 dB rms). A converged P1 solution and a converged solution of
a *different integral equation* agreeing with each other is strong evidence that
the numerics are sound: discretization differences vanish at convergence, and
Burton-Miller shares none of the standard BIE's failure modes.

**Bempp is validated against ABEC3 on this geometry from 100 Hz to 11 kHz**, to
0.08 - 0.23 dB rms in the main lobe. Above 11 kHz neither code is resolved on
this mesh. Deep rear nulls disagree at any frequency, in both codes, and should
not be read as an accuracy figure.

### Nothing here changed the physics

The full-domain ASRO68 solve was run with the working tree and with the
previously pinned package (`5c0b751`, which predates the symmetry work
entirely) at 100 Hz, 1 kHz, 1.5 kHz and 3.4 kHz:

- maximum normalized-directivity difference: `1.1e-4 dB`
- maximum relative impedance difference: `1.4e-7`
- identical GMRES iteration counts (55, 65, 123, 113)

and the working tree was 26% faster on the same run.

### Speed against hornlab-metal-bem

ASRO2 is the case `hornlab-metal-bem` reports in its README: 40 frequencies,
3 planes x 37 angles, `yz+xz` quarter.

| Backend | Hardware | 40 frequencies | Per frequency |
|---|---|---:|---:|
| hornlab-metal-bem | Apple M-series GPU | ~2 s | ~50 ms |
| hornlab-bempp-bem, this work | Ryzen 7 5825U, OpenCL CPU | 39.4 s | 986 ms |
| hornlab-bempp-bem, before | same | 47.6 s | 1191 ms |

For scale, the full-domain ASRO68 case (9104 triangles) takes 150 s for the
same 40 frequencies, 3.75 s each — the quarter reduction is worth about 3.8x
here, considerably more than on the small reference horn, which is the scaling
the earlier work predicted but had not measured.

Metal remains roughly 20x faster, on GPU hardware this host does not have — it
is not a like-for-like comparison, and the Metal backend is unavailable on
Windows. The useful figure is the 1.21x within the Bempp column, which is the
program cache alone on a mesh large enough that fixed overhead no longer
dominates.

Artifacts:

- `benchmarks/cpubem/windows-bempp-asro2-quarter-2026-07-30.json`
- `benchmarks/cpubem/windows-bempp-asro68-full-2026-07-30.json`

The ASRO reference data is external, under
`misc/ATH results 0 degree norm/{250917asro68,asro2}/ABEC_FreeStanding/`.

### Why the parallel sweep stays off for normal sweeps

`run_sweep_parallel` was unreachable from the application: it rejected
`progress_callback`, and `bempp_solver` always sets one. Progress now travels
through a manager queue while the callback stays in the parent, so parallel
mode works and matches serial bitwise. `on_frequency_result` is still refused,
because a worker cannot cancel frequencies already running in its siblings and
silently meaning something different from serial mode would be worse than
refusing.

It is nevertheless off by default, because measurement says it should be. A
spawned worker re-imports bempp-cl and re-JITs its numba kernels before it can
solve anything -- 1.4 s import plus 3.5 s JIT on the reference host, about 4.9 s
in total -- against roughly 0.13 s per warm frequency. bempp-cl's hot kernels
are declared without `cache=True`, and forcing caching on their dispatchers
(`Dispatcher.enable_caching()` on all 47 of them) changes nothing measurable, so
the cost is per process and cannot be amortized on disk.

A worker therefore has to solve about 40 frequencies to pay for its own
start-up. Measured on the 16-frequency quarter case: 1.7 s in one warm process
against 16.8 s across two workers. `workers=0` now means *auto* and splits only
when each worker would get at least 40 frequencies; an explicit `workers > 1` is
still honoured but warns when the arithmetic says it will lose. The production
default of 40 frequencies therefore stays serial, and a 200-frequency sweep gets
five workers.

The real fix is a persistent worker pool reused across solve jobs, which would
pay the start-up once per server lifetime rather than once per solve. That is an
application-level change to the job runtime and has not been made.

### Seam validation

An adversarial review of the reduction confirmed the mathematics against
independently solved full meshes (agreement within `0.007 dB` and `1e-4`
relative impedance at asymmetric observation points, 9 kHz), but found the
geometric preconditions unguarded. `expand_symmetry_mesh` checked only that the
reduced mesh lay on the positive side of each plane, so several shapes were
accepted and silently mirrored into a wrong model. Measured on the real
`test_reference_horn_q1.msh` at 1 kHz, as relative complex-impedance error
against the healthy mesh:

| Damage | Expanded topology | Impedance error |
|---|---|---:|
| One seam displaced `1e-9 m` | 120 open edges, 2 disconnected blocks | 17.9% |
| Both seams displaced `1e-9 m` | 240 open edges, 4 disconnected blocks | 33.6% |
| Rigid 80 mm offset off the cut plane | 2 disjoint shells | 55.5% |

None of them warned. `require_closed_mesh` catches them, but it is `False` for
bare horns, which is the common case.

The seam tolerance was also unusable: `build_symmetry_context` clamped it to
`min(tolerance, 1e-9)`, a quantization bucket in whatever units the mesh
happened to carry -- one picometre for a mesh in millimetres -- and a caller
could not loosen it without hitting a hard `duplicate P1 coordinate` error.
Meanwhile `detect_reduced_symmetry_plane` used `1e-6` and still reported the
mesh as symmetric, so the two disagreed exactly where it mattered.

`expand_symmetry_mesh` now separates the two concepts. `plane_tolerance`
(default `mesh._SYMMETRY_SNAP_TOLERANCE`, `1e-6 m`) decides what lies *on* a
mirror plane; those coordinates are snapped to exactly zero before mirroring,
so a seam welds exactly instead of depending on both sides landing in the same
bucket. `tolerance` remains the deduplication bucket.

Four guards then run:

- the mesh must reach each declared plane, using only vertices the triangles
  actually reference -- an unused on-plane vertex was otherwise enough to smuggle
  an offset mesh through;
- each declared plane must carry a real **cut boundary edge**, not merely a
  touching vertex. This is what catches a seam whose vertices are lifted off
  one at a time (the affected edge just reclassifies as a rim, so the edge count
  cannot see it) and a closed body tangent to the plane at a single point, which
  mirrors into two shells joined at a point and has no open edge at all;
- snapping must not collapse an element, checked absolutely for zero area and
  repeated indices so a pre-existing degenerate triangle cannot mask a new one;
- the expanded open-edge count must match the reduced mesh's non-cut boundary.

The snapping is applied to the reduced dof coordinates as well as the expanded
grid. Without that the orbit map is built by matching unsnapped reduced
coordinates against a snapped expanded grid, and everything from `1e-8` to
`1e-6` -- most of the advertised band -- fails one call later with
`missing full dofs`. `tests/test_symmetry_context_tolerance.py` pins the whole
band through `build_symmetry_context`, not just through the expansion.

The `1e-9` displacement is now repaired rather than rejected: the expansion is
bitwise identical to the healthy mesh and impedance agrees to `7.2e-7`
relative, which is GMRES tolerance noise. The offset case raises. Rejecting an
offset model does rule out one legitimate use of the image method -- a body
wholly on the positive side standing in for a mirrored *pair* -- so
`validate_seam=False` bypasses the geometric preconditions for a caller who
means it.

Declaring a plane also used to switch the reduced-mesh detector off entirely,
which is exactly when it is most useful -- it already returns the right answer
and was simply never consulted. A quarter mesh declared as a half model is
mirrored in one axis only, leaving the other cut plane open, and nothing
downstream notices because the unmirrored cut reads as an ordinary rim.
`load_mesh` now cross-checks the declaration against the geometry and refuses a
mesh whose cut planes the declaration does not cover.

### The mesh built and the symmetry solved must agree

`mesher_adapter` read `Mesh.Quadrants` with Ath's rules -- anything
unrecognised is a quarter model -- while `result_mapping` looked the raw value
up in a dict and fell through to `None`, a full domain, for the same input.
For `quadrants` of `0`, `""`, `2`, `13`, `21`, `"1,2"`, `"x14"` and others
(11 of 23 tested values) the mesher built a quarter shell and the solver was
told it was a complete free-space model, solving a quarter of a horn as an open
sheet. The frontend sanitises this, so it only reached direct API callers and
imported Ath configurations.

Both now read the value through `server/solver/quadrants.py`, and
`server/tests/test_quadrants.py` pins them against each other and against
`hornlab_mesher.profile_common` -- the actual authority -- whenever the mesher
is installed.

### Why OpenCL was silently disabled

bempp-cl passes its kernel include directory to `clBuildProgram` unquoted:

```python
compile_options += ["-I", _INCLUDE_PATH]
```

pyopencl joins that option list on spaces, so an install path containing a
space — such as `...\Hornlab - Workspace\...` — splits the option and the build
fails with `INVALID_BUILD_OPTIONS`. pyopencl quotes its own `-I`, which is why
only bempp-cl's was affected.

The failure was invisible for a second reason: pyopencl's cache error handler
reads `os.environ["PYOPENCL_CACHE_FAILURE_FATAL"]` with no default, so on a
failed build the handler itself raised `KeyError` and destroyed the real
compiler diagnostic. The user-visible error named an environment variable and
never mentioned OpenCL.

Both are worked around in `server/solver/bempp_compat.py`. Neither changes any
numerics.

Returned result contract (unchanged): `frequencies`, `spl_on_axis`,
`directivity` (`horizontal`/`vertical`), `impedance`, `di`, `metadata`.

## Caveats

This host is a virtual machine with 12 vCPUs on an 8C/16T part, a reported
maximum clock that is not the real clock, no CPU pinning, unknown host
contention, and an RDP-attached display. **These timings are indicative only and
must not be used as published performance claims.**
