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

Backend: `hornlab-bempp-bem` / bempp-cl, assembly backend **numba**.
`pyopencl` is not installed, so the OpenCL-CPU path was not exercised.

| Measurement | Wall time |
|---|---|
| Cold, 1 frequency (numba JIT + import) | **62.81 s** |
| Warm, 1 frequency | 2.71 / 2.77 / 2.92 s — **median 2.77 s** |
| Warm, 5 frequencies (1000–2000 Hz, log) | **13.76 s** = **2.75 s/frequency** |

The 5-frequency sweep costs essentially 5x the single-frequency solve. There is
no measurable amortization across frequencies today, so per-frequency setup is
fully repeated. That is the clearest available optimization target.

Returned result contract (unchanged): `frequencies`, `spl_on_axis`,
`directivity` (`horizontal`/`vertical`), `impedance`, `di`, `metadata`.

## Caveats

This host is a virtual machine with 12 vCPUs on an 8C/16T part, a reported
maximum clock that is not the real clock, no CPU pinning, unknown host
contention, and an RDP-attached display. **These timings are indicative only and
must not be used as published performance claims.**
