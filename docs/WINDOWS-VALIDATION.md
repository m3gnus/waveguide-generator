# Windows validation — P6.4

**Status:** first native Windows run of v2, 2026-08-07. Covers P6.4 items 1–4
(bootstrap, serve, bempp solve, launcher and the parent-path-with-spaces case).
Item 5 — upgrade-over-v1 and rollback E2E — was not reached; see §5.

Everything below was measured on the machine described in §1. Where a check
could not be completed, it says so and why, rather than being narrowed until it
passed.

---

## 1. Machine

| | |
|---|---|
| OS | Windows 11 Pro, build 10.0.26100 |
| CPU | AMD Ryzen 7 5825U, 12 logical processors |
| Memory | 16 GB |
| GPU | Red Hat VirtIO GPU DOD (QEMU) + Microsoft Remote Display Adapter |
| Virtualisation | QEMU/KVM guest — **no hardware 3D acceleration** |
| Install path | a parent directory containing a space: `…\Hornlab - Workspace\wg2` |
| Data directory | `%APPDATA%\WaveguideGenerator2` — `db/`, `logs/`, `locks/`, `workspace/` all created as documented |

The absence of a real GPU only affects check 8. It does not affect the solver,
which is CPU BEM.

---

## 2. Prerequisite matrix

| Prerequisite | Needed? | Already present | Version found | Notes |
|---|---|---|---|---|
| CPython 3.13 | yes | yes | 3.13.3 (MSC v.1943 64-bit) | `py -3.13` resolves it; bootstrap rejects any other series |
| Git | yes | yes | 2.53.0.windows.1 | required to install the four pinned HornLab modules from Git |
| MS Visual C++ redistributable x64 | yes | **yes** | v14.51.36247.00 | `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` present in System32 |
| Node.js | build only | yes | v24.14.0, npm 11.19.0 | see §2.1 |
| OpenCL runtime | **no** | n/a | n/a | see §2.2 |

Nothing had to be installed. The redistributable was already registered, so
**the v1 trap this task was written around never fired here** — which means the
`bempp_status()` numba probe was not exercised in its failure mode on this
machine. It was exercised in its success mode and proved accurate (check 6).

### 2.1 Node was not an obstacle, but it is still required

`frontend/dist/` is gitignored and no release tag exists yet, so the SPA had to
be built locally. `npm ci` and `npm run build` both succeeded first time on
**Node 24.14.0**, not the Node 20 the plan names — vite 8 built 1263 modules in
7.58 s with no Windows-specific failures. So the Node requirement cost one
build, not debugging.

It remains a real gap for end users: without `frontend/dist/index.html` the
server will not even import, because `server/app.py` mounts it as `StaticFiles`
at module scope. Until a `v*` tag exists and `.github/workflows/release.yml`
has published an SPA archive, a Windows install from a clone needs Node.

### 2.2 bempp needs no OpenCL runtime on Windows

This was expected to be the fiddly part. It is not, because v2 never takes the
OpenCL path: `server/solver/bempp.py` pins `assembly_backend="numba"` when it
builds the `SolveConfig`. Confirmed on the installed environment:

- `pyopencl` is **not installed** and is not in `requirements-lock.txt`
- `bempp-cl 0.4.2` imports as `bempp_cl` (the top-level module was renamed from
  `bempp`; `import bempp` fails and that is correct, not a broken install)
- the solve in check 6 ran to completion with `assembly_backend: numba`,
  `device_interface.selected: bempp-cl-numba`

The only native dependency that matters is therefore the VC++ redistributable,
which numba and llvmlite need for their compiled extensions.

Installed solver stack: `bempp-cl 0.4.2`, `numba 0.66.0`, `llvmlite 0.48.0`,
`gmsh 4.15.2`, `numpy 2.4.6`, `scipy 1.17.1`.

---

## 3. The ten checks

| # | Check | Verdict |
|---|---|---|
| 1 | Bootstrap, clean clone → running server, path with a space | **pass** (after a fix) |
| 2 | Test suites | **pass** (after two test fixes) |
| 3 | Server starts and serves the UI | **pass** |
| 4 | Single-instance lock | **pass** |
| 5 | Capability detection | **pass** |
| 6 | Real bempp solve, end to end from the UI | **pass** |
| 7 | AUTO engine resolution | **pass** |
| 8 | 3D viewport | **partial** — pipeline verified, pixels not |
| 9 | Exports | **pass** |
| 10 | Shutdown and restart | **pass** for crash/restart; graceful Ctrl+C **not verified** |

### 1. Bootstrap — pass, after fixing a hard blocker

`py -3.13 scripts\bootstrap.py` **fails on every Windows machine** as written.
It installed the full locked set in 2 min 38 s and then refused its own result:

```
Successfully installed hornlab-bempp-bem-0.1.0 hornlab-metal-bem-0.1.0 …
Could not bootstrap Waveguide Generator v2: The environment was installed but
validation failed: one or more required packages are unavailable.
```

The unavailable package is **uvloop**. `uvicorn[standard]` declares it
`sys_platform != 'win32'`, so pip correctly does not install it, but
`_locked_versions()` read every line of `requirements-lock.txt` and asserted
each one was installed at that exact version. Windows could never satisfy that.

Fixed by making the lock state its platform (§4.2). After the fix bootstrap
exits 0. The re-run took 8 s because the packages were already on disk; a true
cold install on this machine is the 2 min 38 s measured above plus validation.

`scripts/bootstrap.py` needed no other Windows work — its `Scripts\python.exe`
layout handling was already correct.

The install path contained a space throughout (`Hornlab - Workspace`). No
quoting defect appeared in bootstrap, the launcher, the server, or the exports.

### 2. Test suites — pass

```
.venv\Scripts\python -m pytest server\tests -q
462 passed, 9 skipped in 47.18s
```

All 9 skips are expected and self-describing: 6 because the v1 checkout is not
beside this one, 1 empty parameter set that follows from those, and 2 native
Metal qualification tests gated behind `WG2_RUN_LIVE=1`.

Two tests **failed on Windows before being fixed** — both were defects in the
tests, not in the product (§4.4):

- `test_custom_v1_workspace_location_is_honoured` — the fixture built
  `workspace_settings.json` by interpolating a path into a JSON string. A
  Windows path's backslashes are invalid JSON escapes, so the migration read a
  corrupt file. The product behaved correctly: it raised
  `MigrationError: Could not read … Invalid \escape`.
- `test_backup_failure_stops_before_writing` — used `chmod(0o500)` to make a
  directory unwritable. On Windows `chmod` only toggles the read-only
  attribute, which directories ignore for creation, so the backup succeeded and
  the assertion never fired.

Neither assertion was weakened. The first now serialises with `json.dumps`; the
second makes the directory genuinely unwritable with a deny ACE on Windows and
keeps `chmod` on POSIX, so both platforms still exercise the real failure path.

Other suites:

```
node --test shared/js/frame.test.mjs   → tests 43, pass 43, fail 0
cd frontend && npm test                → 48 test files, 291 tests, all passed
ruff check server scripts shared       → All checks passed!  (ruff 0.16.1)
```

### 3. Server starts and serves the UI — pass

```
GET http://127.0.0.1:3100/ → 200  text/html; charset=utf-8  5867 bytes
title: Waveguide Generator 2 — tritonia_mk2.cfg
```

This required fixing the `fcntl` import first (§4.1); before that the launcher
could not start at all.

### 4. Single-instance lock — pass

Starting a second server against the same data directory:

```
exit=2 after 1.4s
Waveguide Generator v2 is already running (pid 12512, port 3100;
lock %APPDATA%\WaveguideGenerator2\locks\server.pid).
Close that instance or use it at http://127.0.0.1:3100/.
```

Fails fast, does not hang, and still reports the owner's pid and port. That last
part is not free on Windows and is the reason for the lock offset in §4.1: byte
range locks there are mandatory, so a lock taken at offset 0 would have denied
the *reader* too and degraded this message to "owner metadata is not available
yet".

Stale locks do not wedge the app: the server was hard-killed three times
(`Stop-Process -Force`, no graceful path), each time leaving a lock file naming
a dead pid, and each subsequent start acquired it and rewrote the metadata.

### 5. Capability detection — pass

`GET /api/capabilities`:

```json
{"engines": [
  {"name": "metal", "available": false,
   "reason": "Native Metal helper requires macOS.; Native helper executable not found and Swift executable not found via HORNLAB_METAL_BEM_SWIFT or PATH.",
   "version": "0.1.0", "fast_paths": []},
  {"name": "bempp", "available": true,
   "reason": "hornlab-bempp-bem is importable and its numba assembly backend loads.",
   "version": "0.1.0", "fast_paths": []}],
 "engineSelection": {"default": "auto", "resolvedDefault": "bempp",
   "full3dOrder": ["metal", "bempp", "dryrun"],
   "metalFastPath": "axisymmetric-meridian"}}
```

Metal unavailable with an accurate reason, as expected and not a bug. bempp
available. `circsym` is correctly reported unavailable too, because it depends
on Metal.

### 6. Real solve with bempp, end to end from the UI — pass

Driven from the browser: design loaded, parameters edited in the Simulation
panel, Solve pressed, results and charts read back.

| | |
|---|---|
| Design | `tritonia_mk2.cfg`, R-OSSE, freestanding, thickened wall 5 mm |
| Sweep | 500 Hz – 2 kHz, 20 frequencies, logarithmic |
| Solve mesh | **164 triangles / 103 vertices** in the solved quarter domain (656 full-domain), max edge 50.3 mm |
| Symmetry | auto → 1 quadrant (both mirror planes hold) |
| Engine | AUTO → bempp, `assembly_backend: numba`, `solver_mode: full_3d` |
| **Wall clock** | **58.5 s** (15:19:11.215 → 15:20:09.724); engine-reported `total_time_seconds` 58.03 |
| Result | `status: complete`, no error, `has_results: true` |

Results returned `frequencies` (20), `directivity`, `spl_on_axis`, `impedance`
and `di`. The Results panel populated every chart: Directivity Map (H/V/All),
Frequency Response, Directivity Index, Forward Beam Shape/Map, 3D Balloon,
Acoustic Impedance, Simulation Summary. The job row reads
`59 s · 164 el · 20 f · bempp`.

The mesh was deliberately coarsened (throat 14 mm, mouth 28 mm, surface sampling
96 × 32) so a first end-to-end run would finish quickly. At 50.3 mm max edge and
a 2 kHz ceiling this is roughly λ/3.4 — **fast, not acoustically converged.**
This check proves the Windows solve path works; it is not a physics result and
should not be used as one.

**The `bempp_status()` probe has no gap.** It reported available, and the solve
then completed. It did not report available and die inside numba, which is the
failure this task warned about.

### 7. AUTO engine resolution — pass

AUTO skips the unavailable Metal engine and lands on bempp without erroring, at
three independent layers:

- registry: `resolve_auto_engine()` → `bempp`
- HTTP: `engineSelection.resolvedDefault` → `bempp`
- UI: the toolbar button reads "Solve current design with AUTO (bempp)"
- the executed job recorded `engine: bempp` in its config summary

Re-confirmed after every restart. No defect here.

### 8. The 3D viewport — partial

**Verified.** The geometry pipeline works end to end. The viewport's own frame
stats report a displayed binary frame with per-part vertex counts:

```
LATEST DISPLAYED BINARY FRAME   REVISION 1   LOD fine   EVAL 2668.15 ms
horn.inner 24,832 · horn.outer 24,576 · wall.throat_band 512
mouth_rim 512 · source_cap 4,097 · wall.rear_cap 257
```

A WebGL 2.0 context is created successfully and three.js initialises with no
errors — the only console output in the whole session was one
`THREE.Clock: This module has been deprecated` warning.

```
WebGL 2.0 (OpenGL ES 3.0 Chromium)
vendor:   Google Inc. (Microsoft)
renderer: ANGLE (Microsoft, Microsoft Basic Render Driver (0x0000008C), Direct3D11 vs_5_0 ps_5_0, D3D11)
browser:  Chromium 148 (Electron 42) on Windows NT 10.0 Win64
```

**Not verified.** Pixels on screen, and orbiting. `Microsoft Basic Render
Driver` is WARP, the software rasteriser — this VM exposes no GPU, so even a
successful render would not represent a real user's machine. The browser pane
also never composited: `requestAnimationFrame` did not fire within 4 s, so both
a screenshot and a `gl.readPixels` histogram of the drawing buffer returned
nothing. Orbit was therefore not exercised either.

This check needs re-running on a Windows box with a real GPU before it can be
called green.

### 9. Exports — pass

All four exports were requested through the UI's Design → Export menu, then
replayed against the API to write files to disk and inspect them.

| File | Bytes | Time | Structure check |
|---|---|---|---|
| `.step` | 3,137,513 | 6.4 s | `ISO-10303-21;` … `END-ISO-10303-21;`, OpenCASCADE 7.8 header — valid |
| `.stl` | 1,862,484 | 51.7 s | **binary** STL, header `MWG Horn`, 37,248 triangles; file size == 84 + 50 × N exactly |
| profiles `.csv` | 94,793 | <1 s | `# x_cm;y_cm;z_cm`, 1,584 data rows |
| slices `.csv` | 95,630 | <1 s | 1,617 data rows |

Worth knowing: the STL export is **binary**, not ASCII. Grepping it for
`facet normal` returns zero and looks like a failure; it is not.

### 10. Shutdown and restart — pass for crash/restart; graceful path not verified

**Job recovery: pass.** A 200-frequency solve was submitted and the server
hard-killed 18 s in, leaving a genuinely stuck row:

```
before restart:  f2a63354  status=running  stage=assemble  progress=0.3
after restart:   f2a63354  status=error    stage=error     progress=0.3
                           error_message = "Server restarted during execution"
```

Not lost, not stuck "running". The two previously completed jobs were untouched
across the restart, as were their results.

**Lock release: pass.** Verified after abnormal exit — three hard kills, three
clean restarts over a lock file naming a dead pid. Also covered by
`test_instance_lock_acquire_conflict_and_release`, which does
acquire → conflict → release → re-acquire and passes on Windows.

**Not verified: the graceful Ctrl+C path.** `launch/serve.py` installs handlers
for `SIGINT` and `SIGTERM`, and the launcher tells the user "Close this window
or press Control-C to stop it". On Windows that path could not be exercised
safely: there is no `kill(pid, SIGINT)` — `os.kill` maps to `TerminateProcess` —
so the only way to deliver a real Ctrl+C is `GenerateConsoleCtrlEvent`, which
signals *every* process attached to the console, including the harness driving
this validation. The attempt was abandoned rather than risk that.

By inspection, and stated as analysis rather than measurement: `SIGTERM` cannot
be sent by another process on Windows, and neither closing the console window
(`CTRL_CLOSE_EVENT`) nor Ctrl+Break (`SIGBREAK`) is handled by `serve.py`. If
that reading is right, the graceful path is reachable only by pressing Ctrl+C in
the launcher window, and every other way of stopping the app is an abrupt exit.
The consequences of an abrupt exit are already covered above and are benign, so
this is a loose end rather than a blocker. **It needs someone at a real
keyboard to confirm.**

---

## 4. Changes made, and why

### 4.1 `server/platform/instance.py` — a portable single-instance lock

`import fcntl` at module scope made this an `ImportError` before anything ran,
and `launch/serve.py` imports `InstanceLock` from it at module scope too, so the
launcher could not start on Windows at all.

The platform split is a pair of module-level helpers, `lock_exclusive()` and
`unlock()`, so `InstanceLock` itself is unchanged apart from calling them.
`msvcrt.locking(fd, LK_NBLCK, 1)` is the Windows equivalent of
`flock(LOCK_EX | LOCK_NB)`; contention arrives as `EACCES` and is re-raised as
`BlockingIOError` so the existing `except BlockingIOError` path still means
"another instance owns this".

Two Windows-specific details were measured, not guessed:

- **`LOCK_BYTE_OFFSET = 1 << 30`.** Windows byte-range locks are mandatory and
  are taken from the current file position. A probe confirmed that locking
  offset 0 breaks the feature in both directions: the owner's own
  `os.ftruncate` in `update_port()` fails with `EACCES`, and any other process
  reading the metadata fails with `EACCES` as well — which would have reduced
  the "already running" message to its no-metadata fallback. Locking a byte
  past everything the file will ever hold avoids both. POSIX `flock` takes the
  whole file and ignores the offset, so the constant is inert there.
- **`LOCK_OPEN_FLAGS` adds `O_BINARY`.** Windows descriptors are text mode
  unless asked otherwise; a probe showed `os.write(fd, b'{"pid": 1}\n')`
  landing on disk as `…}\r\n`. The lock file now reads back byte-for-byte on
  every platform.

### 4.1a `_pid_is_running()` was a process killer on Windows

Not required to make anything start, fixed because leaving it would be a
landmine in a module being made portable.

`os.kill(pid, 0)` is not a liveness probe on Windows. CPython implements
`os.kill` there as `OpenProcess` + `TerminateProcess(handle, sig)`, so signal 0
**terminates the process being asked about**. Demonstrated directly: a spawned
process was gone immediately after `os.kill(pid, 0)` returned without raising.

The function has no call site today, which is why nothing has been damaged, but
it is monkeypatched by `test_platform_batch_e.py` and is exactly the shape
someone would wire into stale-lock handling next. It now branches to
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess` on
Windows, treating `ERROR_ACCESS_DENIED` as "alive but not ours to open".

### 4.2 `requirements-lock.txt` and `scripts/bootstrap.py` — the uvloop blocker

The lock now carries the marker that was always implied:

```
uvloop==0.22.1; sys_platform != "win32"
```

and `_locked_versions()` parses the marker while `_validate()`'s in-environment
probe evaluates it with `packaging.markers.Marker`. Marker evaluation has to
happen inside the environment under test because the interpreter running the
bootstrap has no third-party packages. `packaging` is itself in the lock, so it
is always available where the probe runs.

`gen_requirements.py` only generates `requirements-pins.txt`, so editing the
lock cannot trip the CI drift gate. `pins.json` and the pinned SHAs are
untouched.

### 4.3 `launch-wg2.bat` — new, the Windows counterpart of `launch-wg2.command`

Same shape as the macOS launcher: verify the folder, verify `frontend/dist`,
honour `WG2_PYTHON`, otherwise validate `.venv` with `bootstrap.py --check` and
bootstrap if needed, confirm FastAPI and Uvicorn import, then exec
`launch/serve.py` with all arguments forwarded.

Three of v1's four documented traps applied and were carried over:

- **Store alias.** Every `where` match is considered, not just the first, and
  anything under `\WindowsApps\` is skipped with a specific message pointing at
  the App execution aliases setting. `py -3.13` is tried first because it
  resolves the required series directly.
- **Parenthesised probes.** Every probe containing parentheses lives in a
  subroutine, never inlined into an `if (…)` block — the bug that made v1
  discard a healthy `.venv` and rebuild it on every run.
- **Paths with spaces.** Quoted throughout; exercised for real by the install
  location.

The fourth does not apply: this launcher never pulls over itself, so it needs no
copy-to-`%TEMP%` staging. `%~dp0` is genuinely the repository here, which is
why `cd /d "%REPO_DIR%"` is safe. A v2 *installer* that self-updates would need
v1's `install-and-update.bat` dance, and that is P6.2, not this change.

It also pauses only when double-clicked, using v1's `CMDCMDLINE` test, so
scripted runs are not blocked.

### 4.4 Test fixes

Described under check 2. `server/tests/test_platform_luna.py` no longer imports
`fcntl` directly; it takes the lock through the same `instance.lock_exclusive`
helper the product uses, which keeps the test meaningful on both platforms
instead of asserting a POSIX mechanism.

---

## 5. Still broken, or unverified

Ordered by how much it matters.

1. **Graceful shutdown is unverified, and probably unreachable except by
   Ctrl+C.** Check 10. Needs a human at a keyboard, or a `SIGBREAK` handler in
   `serve.py` if console-close and Ctrl+Break should also shut down cleanly.
2. **The 3D viewport was never seen.** Check 8. This VM has no GPU and the
   browser pane did not composite. Re-run on real Windows hardware.
3. **No installer.** This change ships a *launcher*. The `install.bat` /
   `install-and-update.bat` equivalent, the prerequisite checks with version
   floors, git self-update, and the documented uninstall are all P6.2 and are
   not started. v1's two installer contract suites
   (`tests/installer-contract.test.js`, `installer-env-contract.test.js`) are
   correspondingly **not ported** — v2 has no automated installer coverage.
4. **Upgrade-over-v1 and rollback E2E on Windows: not reached.** P6.4 item 5.
   No v1 checkout exists on this machine, which is also why 6 tests skip. G6
   row 5 stays "Windows pending".
5. **The VC++ failure mode was never seen.** The redistributable was already
   installed, so `bempp_status()`'s missing-DLL branch and its remediation
   message are still untested against a real clean Windows box. The success
   branch is proven accurate.
6. **No Windows CI job.** P6.3 lists it as blocked on P6.4; P6.4 is now far
   enough along to add one.
7. **`docs/TRACEABILITY-TABLE.md` is now stale.** Its P011/Q007 row says the
   instance lock is implemented with `fcntl`. It is now `fcntl` or `msvcrt`
   depending on platform. Left unedited deliberately — this task was scoped to
   add a report under `docs/`, not to modify other documents.
8. **Solve accuracy was not assessed.** Check 6 used a deliberately coarse mesh
   to prove the path. No Windows-vs-macOS numerical parity comparison was run;
   that belongs to the qualification runner.

---

## 6. What would surprise someone who has only run this on macOS

Beyond the launcher and lock work already described:

- **`os.kill(pid, 0)` kills the process.** The idiom every POSIX developer uses
  to test liveness is destructive on Windows. See §4.1a.
- **uvloop simply does not exist here.** Any check that treats a resolved
  lockfile as a flat list of must-be-installed packages will fail on Windows,
  which is exactly what bootstrap did.
- **File locks are mandatory and positional.** A POSIX `flock` is advisory and
  whole-file; the Windows equivalent denies other processes real access and
  applies only to the byte range at the current file offset. Where you lock
  changes what still works.
- **`os.open` gives you a text-mode descriptor.** `\n` becomes `\r\n` on the
  way to disk unless you pass `O_BINARY`.
- **`chmod` cannot make a directory unwritable.** It sets the read-only
  attribute, which directories ignore for creation. Tests that build a
  permission-denied condition with mode bits silently do nothing. A deny ACE
  (`icacls … /deny user:(WD,AD)`) does work and produces a real
  `PermissionError: [WinError 5]`.
- **PowerShell cannot parse this project's design JSON.** `ConvertFrom-Json` is
  case-insensitive, and an ATH design has both `R` (mouth radius) and `r`
  (throat rounding) at the same level, so it fails with *"a dictionary … contains
  the duplicated keys 'R' and 'r'"*. Anyone scripting against the API from
  PowerShell must treat the design as opaque text, or use Python. This affects
  `/api/solve` and every `/api/export/*` payload.
- **`python` on PATH may be a Microsoft Store stub.** A zero-byte alias in
  `%LOCALAPPDATA%\Microsoft\WindowsApps` that opens the Store. The launcher
  skips it; anything else calling `python` directly will not.
- **The STL export is binary.** It contains no `facet normal` text.

None of these are v2 design faults; they are the platform. They are recorded
here so the next person does not spend the same afternoon on them.
