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
