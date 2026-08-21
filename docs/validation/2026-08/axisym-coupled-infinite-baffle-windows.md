# Windows Axisymmetric/CircSym and coupled infinite-baffle validation

Date: 2026-08-21  
Host: Windows 11 Pro 24H2, build 26100.1742  
Artifact root: `C:\Users\Docker\Desktop\Hornlab - Workspace\validation-artifacts\2026-08-21-axisym-coupled-ib`

## Executive verdict

| Area | Verdict | Evidence and qualification |
|---|---|---|
| Axisymmetric on Windows | **Pass** | Portable CPU CircSym is available without Metal, selects the Numba path, passes its targeted and complete corrected suites, completes the 24-frequency benchmark in 4.49–4.68 s warm, cancels in 73 ms, and completes the saved-design 121-frequency smoothness sweep without warnings or isolated spikes. |
| BEMPP coupled infinite baffle on Windows | **Pass for HornLab's internal gate; external-reference validation incomplete** | Five focused tests pass. At 1 kHz, BEMPP/CircSym differ by 0.0119 dB in the forward pattern, 0.455 dB on-axis, and 0.175 degrees in phase; both rear samples are exactly zero and aperture continuity residual is `3.92e-16`. No ABEC executable exists on this host, so the requested independent ABEC acceptance cells were not run. |
| AUTO workflow reliability | **Pass** | Real registry/planner/server checks select Axisym for eligible free-standing and infinite-baffle round models, retain explicit BEMPP full-3D, reject forced Axisym for formula-valued mesh controls clearly, and do not allow imported/CAD requests to silently lose an explicit Axisym choice. Backend and UI-focused capability tests agree. |
| Saved design, 400 Hz–10 kHz | **Numerically credible in Axisym; external physical corroboration incomplete** | The raw 121 x 181 result has no >=1 dB isolated log-frequency neighbor spike, no warning/failure, minimum dense-solve rcond `1.159e-3`, and 6.011 elements/wavelength at 10 kHz. A coarse saved BEMPP mesh produces a false rear-dominant response; refinement restores the forward lobe, proving mesh sensitivity, but remaining two-frequency deltas are above the requested parity targets. No ABEC output was available to determine which residual is closer to an independent solution. |

The session therefore proves the Windows architecture, planner, cancellation, and HornLab coupled-IB implementation. It does **not** claim completion of the external ATH/ABEC comparison matrix.

## Source control and instructions

No `GIT-WORKFLOW.md` exists in the accessible workspace tree. All applicable `AGENTS.md` files were read before Git actions. Exact clean validation worktrees were created because the original BEMPP checkout contained unrelated user changes.

| Repository | Required branch | Validated HEAD | Initial state |
|---|---|---|---|
| hornlab-metal-bem | `feature/portable-circsym` | `56a9c035f75039c5fb685287ec778f8d6ae1bb24` | clean |
| hornlab-bempp-bem | `feature/coupled-infinite-baffle` | `5e67e1973af2e9500fba6929c31cdeb608c28700` | clean |
| waveguide-generator | `feature/solver-planner` | `b791a46634fe4efa1694945f51f81288935fc029` | clean |

The venv imports the two solver packages from those exact editable worktree paths; `direct_url.json` and `git rev-parse` were both checked. The exact solver-planner mesher pin is `a7cfba26bd0121f680d119ba62b99470ff53b7e0`, and hornlab-sim is `f47e70566f153930df5bf4e5bb0ed1186733b18d`.

One provenance caveat is retained: Waveguide Generator result JSON reports the branch's static dependency-catalog SHAs (`585e...` for metal-bem and `1e7f...` for BEMPP), not the editable feature-branch SHAs actually imported for this validation. Import paths and Git HEADs, rather than that catalog field, are authoritative here.

## Machine and package environment

| Item | Value |
|---|---|
| CPU | AMD Ryzen 7 5825U with Radeon Graphics; 12 logical CPUs visible |
| RAM | 17,174,896,640 bytes (15.99 GiB) |
| Python | 3.13.3, Waveguide Generator `.venv` |
| NumPy / SciPy / Numba | 2.4.6 / 1.18.0 / 0.66.0 |
| bempp-cl / PyOpenCL | 0.4.2 / 2026.1.2 |
| meshio / gmsh / pytest | 5.3.5 / 4.15.2 / 9.1.1 |
| Numba | 12 threads, OpenMP threading layer, JIT enabled |
| OpenCL platform | Intel(R) OpenCL, OpenCL 3.0 Windows |
| OpenCL device | AMD Ryzen 7 5825U with Radeon Graphics, CPU device, 12 compute units, driver `2025.20.10.0.23_160000` |
| Metal / Swift | unavailable, as expected |

All test and application runs used a short writable `NUMBA_CACHE_DIR`. `PYOPENCL_NO_CACHE=1` avoided stale/inaccessible default cache locks. A workspace-local long cache path hit Windows `MAX_PATH`; moving it to the short writable directory resolved that environment issue.

## Test results

### hornlab-metal-bem

| Run | Passed | Failed | Skipped | Time |
|---|---:|---:|---:|---:|
| Required five-file targeted suite, exact feature HEAD | 57 | 0 | 7 | 31.55 s |
| Complete suite, exact feature HEAD | 504 | 1 | 74 | 28.96 s |
| Complete suite after isolated correction | 506 | 0 | 74 | 36.05 s |

The seven targeted skips were two unavailable legacy runtime-C cases and five native Swift/Metal cases. Portable CircSym and both cross-OS goldens ran; none skipped merely because Metal was unavailable.

The complete baseline suite isolated one genuine defect: retained Robin surface traces were computed through NumPy complex arithmetic, whose rounding did not match the componentwise Float32 Swift implementation. The fix uses the Swift operation order for real and imaginary components and adds a regression that fails on the old code.

- Local branch: `fix/windows-robin-trace-float32`
- Local commit: `9564b98` (`Match Swift rounding in retained Robin traces`)
- Files: `hornlab_metal_bem/field_traces.py`, `tests/test_field_traces.py`
- Independent review: no correctness findings; 500 randomized cases were bit-exact; 160 relevant tests passed and four native-only tests skipped. The optional retained-trace path measured about 11% slower in a stress spot-check, with no material normal-solve impact.

The commit is local only. It was not pushed, merged, or submitted as a PR.

### hornlab-bempp-bem

| Run | Passed | Failed | Errors | Skipped | Time |
|---|---:|---:|---:|---:|---:|
| Required `tests/test_infinite_baffle.py` | 5 | 0 | 0 | 0 | 71.27 s |
| Complete suite, original temp path containing spaces | 355 | 0 | 4 | 10 | 877.59 s |
| Four affected setup cases, short no-space temp path | 4 | 0 | 0 | 0 | 1492.46 s |

The four complete-suite errors occurred before test execution because upstream bempp-cl invokes gmsh with an unquoted shell string and the selected temporary path contained `Hornlab - Workspace`. The exact four cases pass with a no-space `TEMP`/`TMP`. Combined functional coverage is 359 passed and 10 skipped, although there is not one clean complete-suite invocation. This is an environment/upstream command-quoting issue, not a coupled-IB numerical failure.

The focused suite covers canonical aperture tagging, invalid tags, interface/frame orientation, forward-only field evaluation, exact rear silence, aperture continuity, the analytic baffled-piston Airy pattern, and absolute BEMPP/CircSym amplitude and phase/sign.

### waveguide-generator and frontend

| Run | Result |
|---|---|
| Required four server modules after exact dependency pins | 64 passed in 5.16 s |
| Imported/CAD explicit-Axisym refusal checks | 2 passed |
| Focused frontend capability/planner tests | 13 passed in 24.84 s |
| Frontend production build | passed |
| `scripts/check_backends.py` | Axisym ready; BEMPP OpenCL CPU ready; Metal and BEAT unavailable with exact reasons |

An initial local environment contained mesher commit `60301d...` instead of the solver-planner pin. That unrelated drift rejected an otherwise valid refined meridian. Restoring exact pin `a7cfba...` made the final 64-test suite pass. The pinned mesher already contains the relevant outer-meridian tolerance regression, so no mesher code change was made.

`npm ci` reported one high-severity issue in the locked dependency tree. No audit fix or dependency mutation was made during validation.

## CircSym benchmark

Command: `python scripts/bench_circsym.py --backend cpu --cpu-remainder auto --json`

Workload: canonical free-standing fixture, 24 logarithmic frequencies from 400 Hz to 16 kHz, 37 angles, 72 meridian segments, nominal 6 mm target edge.

| Run | Wall | Assembly | Dense solve | Field | Native total |
|---|---:|---:|---:|---:|---:|
| Process 1, first JIT with empty cache | 8.80 s | 4.73 s | 0.012 s | 3.59 s | 8.34 s |
| Process 1, same-process warm | 4.49 s | 1.10 s | 0.012 s | 3.37 s | 4.49 s |
| Process 2, disk-cache startup | 6.21 s | 1.74 s | 0.013 s | 3.75 s | 5.51 s |
| Process 2, same-process warm | 4.68 s | 1.22 s | 0.013 s | 3.44 s | 4.68 s |

Assembly and field backends are both `cpu`. The selected implementation is `circsym_numba_dp0_m0`; CPU remainder policy is `auto`, selected `numba`. The legacy C remainder reports its honest Windows reason: it requires a POSIX compiler and pthreads. Line quadrature is 16, singular line quadrature is 24, and azimuthal quadrature rises from 64 to 148 points across the sweep. Measured peak working set is 163.40 MiB. The per-frequency JSON records include assembly, dense solve, field, rcond, quadrature, impedance, and backend diagnostics.

This is a performance workload, not a valid 16 kHz physics reference: the actual maximum segment is 5.949 mm, so the six-elements/wavelength ceiling is about 9.61 kHz. Diagnostics fall from 144.14 elements/wavelength at 400 Hz to 3.60 at 16 kHz.

## Real registry, planner, and application checks

The real engine registry advertises Axisym as available with continuous-axisymmetric symmetry, free-standing and infinite-baffle mountings, DI sphere support, and intra-frequency cancellation. Metal is independently unavailable and does not suppress Axisym. BEMPP is available through the OpenCL CPU device.

| Request | Result |
|---|---|
| AUTO, eligible round free-standing | Axisym; 79 meridian segments; max azimuth quadrature 94; estimated full-3D equivalent 3,071 triangles; 38.87x fewer dense unknowns and 1,511x less matrix memory |
| AUTO, eligible round infinite baffle | Axisym; 66 meridian segments; max quadrature 91; validated aperture formulation |
| Explicit full-3D free-standing | BEMPP |
| Explicit full-3D infinite baffle | BEMPP full domain with reason: validated coupled-IB formulation |
| AUTO, formula-valued mouth resolution | BEMPP with explicit Axisym ineligibility reasons |
| Forced Axisym, formula-valued resolution | clear `ValueError`; no crash or silent fallback |
| Imported/CAD, forced Axisym | explicit refusal; request is not silently discarded |

The actual HTTP server returned 200 for `/health`, `/api/capabilities`, and `/`; its log is retained. Infinite baffle is represented as a mounting/radiation condition within Axisym and BEMPP capabilities, not as a hardware backend.

## Cancellation

| Engine/case | Stop point | Stop call | Stop to terminal | Final state | Orphans |
|---|---|---:|---:|---|---|
| Axisym, 80 points 400–16 kHz | frequency 70/80 | 0.823 ms | 73.03 ms | cancelled | none |
| BEMPP serial/OpenCL, 16 kHz | assembly configuration with worker PID 14428 active | 0.799 ms | 63.54 ms | cancelled | none; worker reference cleared |

Both meet the 0.5 s target. BEMPP terminates the current worker rather than waiting for the sweep.

## Coupled infinite-baffle numerical gate

The focused 1 kHz canonical shallow round channel gives:

| Metric | Result | Requested target |
|---|---:|---:|
| Maximum forward pattern delta, CircSym vs BEMPP | 0.01194 dB | <=0.5 dB |
| Absolute on-axis level delta | 0.45546 dB | <=0.5 dB |
| Wrapped phase delta | 0.17549 degrees | <=5 degrees |
| Aperture pressure-continuity residual | `3.916e-16` | numerical continuity |
| Rear field | exactly zero in both | exactly zero/floor |

No per-frequency scaling or phase fitting was used.

## Fresh ATH/ABEC construction audit

ATH V2026-08c was found at `boundary-lab\ath\ath202608.exe`. It generated two new, complete ABEC project trees from simple round OSSE designs. The exported meshes were regenerated with gmsh 4.15.2 at 3 mm settings after an initial 4 mm mesh missed the 10 kHz six-elements/wavelength requirement.

| Project | Nodes | Triangles | Maximum edge | Six-element ceiling | Audit |
|---|---:|---:|---:|---:|---|
| A, free-standing | 32,837 | 64,776 | 4.264 mm | 13.41 kHz | Interior SD1, exterior SD2, `I1-2` interface at z=120 mm |
| B, infinite baffle | 20,353 | 40,128 | 4.264 mm | 13.41 kHz | Interior SD1, `I1-2` at z=120 mm, `Infinite_Baffle Subdomain=2; Position=z offset=120.000mm` |

Both are true bodies of revolution with centered round sources and no p-dependent coverage, morphing, images, or double-horn construction. The interface has zero z-span, radius 0–123.326 mm, consistent -Z normals, and is flush with the baffle plane. The infinite-baffle observation file declares `Distance=2m` and `Offset=130mm`.

Source convention must be taken from the exported ABEC project, not inferred from the ATH input key: ATH input contains `Source.Velocity=1`, while `solving.txt` exports `DrvType=Acceleration; Value=1.0`. A future HornLab comparison must use the exported unit-acceleration convention and authoritative ABEC observation offset.

### External-reference gap

No ABEC executable or installation was found under the user profile or Program Files. ATH creates projects and meshes but does not produce ABEC acoustic results. Consequently, these requested cells are **not run**:

- ABEC vs CircSym/BEMPP unnormalized complex pressure
- ABEC absolute SPL, phase, polar, DI, and impedance deltas
- ABEC coupled-IB rear-field comparison

No archived file or HornLab/BEMPP result was relabeled as ABEC, no arbitrary phase alignment was applied, and every external-reference tolerance remains unclaimed.

## Saved-design smoothness and convergence investigation

The latest completed user configuration labelled `Phase1 Spaces1` was loaded from the real application database. It is a round R-OSSE design (R=140 mm, a=25 degrees, r0=12.7 mm) with a centered normal-velocity source. Only the frequency and polar sampling were expanded: 121 logarithmic frequencies over 400 Hz–10 kHz and 181 raw angles over 0–180 degrees.

AUTO selected Axisym. Runtime was 93.40 s: 10.16 s assembly, 0.070 s dense solve, and 82.77 s field evaluation. The frequency-refined meridian has 82 segments and maximum edge 5.7065 mm.

Raw checks, before plotting or interpolation:

- no solver warnings or failures;
- no isolated >=1 dB deviation from the mean of adjacent log-frequency samples in on-axis SPL, DI, or the 0, 15, 30, 45, 60, and 90 degree curves;
- maximum adjacent on-axis change 0.776 dB; maximum adjacent DI change 0.166 dB;
- minimum rcond `1.159e-3` at 702.6 Hz;
- minimum mesh density 6.011 elements/wavelength at 10 kHz;
- directivity heatmap shows continuous narrowing and rear null structure, not needle-like single-bin angular defects.

### Solver comparison and cause analysis

The unchanged saved BEMPP settings produce only 162 quarter-domain triangles (648 full equivalent), maximum edge 50.39 mm. Against Axisym over the saved 400–1000 Hz grid, maximum deltas are 24.05 dB on-axis, 88.64 degrees phase, 27.01 dB pattern, 13.94 dB DI, and 0.1719 in complex normalized impedance. Its apparent rear-dominant polar is not credible.

A two-frequency refinement to 3,308 quarter-domain triangles (13,232 full equivalent), maximum edge 8.31 mm, restores the forward lobe. This proves that the dramatic reversal/collapse is dominated by insufficient full-3D geometry/mesh resolution rather than an app observation-frame sign error. The app supplied +Z as the mouth axis to both solvers.

Residual refined deltas at 400 and 1000 Hz are still above the requested acceptance targets: maximum 1.66 dB on-axis, 7.07 degrees phase, 2.68 dB pattern, 0.689 dB DI, and 0.0554 complex normalized impedance. A preceding 1,872-triangle refinement produced similar residuals, so this report does not declare free-standing solver parity. Remaining differences may combine discretized outer geometry, rear-field null sensitivity, and formulation error; an independent ABEC result is needed to assign truth.

There is no evidence of an isolated solver/backend spike, plotting interpolation artifact, or observation-frame reversal in the Axisym 400 Hz–10 kHz result. Physical/modal structure and high-frequency rear nulls are smooth in frequency. The strongest identified artifact is the under-resolved saved BEMPP mesh.

## Failures and dispositions

| Failure/gap | Likely cause | Disposition |
|---|---|---|
| One metal-bem complete-suite failure | Float32 operation-order mismatch in optional retained Robin traces | Small isolated fix, regression, complete suite, independent review, local commit `9564b98` |
| Four BEMPP setup errors | Upstream unquoted gmsh command plus a temp path containing spaces | Exact four cases pass under a no-space temp path; no product numerical change |
| Initial WG dependency errors / later mesher rejection | Local environment not on exact branch dependency pins | Installed missing dependencies and restored exact mesher SHA; final suite passes |
| Saved BEMPP response collapses/reverses | Extremely coarse 162-triangle quarter mesh | Refined solves restore the correct forward lobe; residual parity remains open |
| ABEC comparison matrix absent | No ABEC solver executable installed | Explicitly not run; audited projects retained for execution on an ABEC-equipped host |
| Benchmark physics above ~9.61 kHz | Fixed 6 mm performance fixture under-resolves higher frequencies | Timings retained; high-frequency outputs not used as physical references |
| Result JSON dependency SHA mismatch | Static WG dependency catalog does not describe editable feature packages | Exact import paths and worktree Git SHAs recorded separately |

No tolerances or reference data were changed.

## Retained artifacts

Artifact root: `C:\Users\Docker\Desktop\Hornlab - Workspace\validation-artifacts\2026-08-21-axisym-coupled-ib`

Key files:

- `environment.json` — machine, packages, exact worktree sources, OpenCL
- `metal-*-junit.xml`, `bempp-*-junit.xml`, `waveguide-*-junit.xml` — pytest results
- `circsym-benchmark-process1.json`, `circsym-benchmark-process2.json` — cold/disk/warm benchmark runs
- `circsym-benchmark-detail.json` — full 24-frequency timing, diagnostics, and memory record
- `planner-evidence.json`, `backend-check.txt` — real registry/planner/backend evidence
- `cancellation-evidence.json` — final measured Stop runs
- `coupled-ib-parity.json` — absolute coupled-IB comparison
- `ath-abec\A-run\ath`, `ath-abec\B-run\ath` — complete fresh projects, meshes, configs, and observations
- `ath-abec\ath-project-audit.json` — interface, normal, edge, and ceiling audit
- `smoothness\hornlab-axisym-results.json` — raw 121-frequency result
- `smoothness\hornlab-bempp-current-results.json` and refined result JSON — convergence evidence
- `smoothness\hornlab-results.npz` — retained complex pressure, complex impedance, DI, rcond, and mesh-density arrays
- `smoothness\smoothness-analysis.json` — all reported maxima and spike checks
- `smoothness\smoothness-curves.png`, `smoothness\smoothness-directivity-heatmap.png`, `smoothness\parity-mesh-convergence.png` — unsmoothed plots
- `server-data\logs\server.log` — real server log

All product changes and the report remain local. Nothing was pushed or merged, and no PR was created.
