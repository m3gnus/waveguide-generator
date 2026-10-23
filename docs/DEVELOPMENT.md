# Development guide

Status: current contributor and agent orientation, verified 2026-08-22.

## Source-of-truth order

1. Executable code and tests are the behavior authority.
2. Documents under `reference/` state the intended stable boundary.
3. `plans/` contains accepted design gates that constrain future implementation.
   Maintainer task tracking is workspace-local and is not published with the repository.
4. `legacy/`, `history/`, and the dated folders in `validation/` are evidence, not task
   instructions. `validation/MEASUREMENT-TEMPLATE.md` is the exception: a blank form to
   copy when validating a build against a solve.

When a reference and code disagree, stop and reconcile them in the same change. Do not
implement an old `BATCH-*-BRIEF.md`: those one-use work orders were removed after the
August rebuild and remain available in Git history.

## Architecture map

- `frontend/src/stores/` owns editor, preferences, autosave, and run-export state.
- `frontend/src/api/` owns HTTP/WebSocket boundaries.
- `frontend/src/viewport/` renders binary preview frames; it does not derive geometry.
- `server/design/` parses, migrates, validates, and serializes design documents.
- `server/cli/` provides headless design and local solve-readiness commands.
- `server/preview/` translates a design into the pinned mesher preview API.
- `server/jobs/` owns durable jobs, snapshot/cursor events, retention, and recovery.
- `server/solver/` builds solver requests and maps every backend to one result contract.
- `server/contracts/` holds shared leaf types and never imports other server packages.
- `server/exports/` owns authoritative geometry and CAD-link bundle exports.
- `server/cadlink/` owns CAD identity, registries, return ingestion, and Onshape access.
- `shared/` contains the cross-language binary-frame contract and fixtures.
- `pins.json` fixes the HornLab mesher, solver, plotting, and simulation repositories.

The authoritative flow is:

```text
design text/UI -> typed design -> HornLab mesher -> preview or solver artifact
                                     |                    |
                                     +-> CAD/mesh export  +-> backend -> result contract
```

## Run archive locations

`server/workspace/archive_identity.py` owns project folder allocation and run
locations. New runs use the current Runs root until their first workspace write.
That write pins an absolute `task_metadata.archived_path`, even when the first
write is an automatic export or CAD pinning before archive completion. Later
workspace exports, archive retries, and CAD pinning resolve through
that stored path. Selecting a different WG folder affects runs with no recorded
location; it does not move existing runs. An explicit export destination chosen
by the user still receives that export's files and does not change the run location.

The writer requires the recorded Runs root to be available. If it is missing,
it reports that the run's recorded archive location is unavailable and asks the
user to reconnect the drive or folder. It neither recreates that root nor falls
back to the current root. A missing project/run child under an available root
can be recreated for a retry. Workspace write requests for a run carry `job_id`;
the server validates the logical project/run path and resolves its absolute root.
The browser never supplies an absolute archive destination.

Fusion versions live once per return state in `<model>/cad/`. Capture writes a
`.json` sidecar; `/api/cadlink/runs/archive-document` accepts only `jobId`, verifies
the captured bytes against the retained manifest, and pins the original by
renaming the model to `.pinned.f3d` and its sidecar to `.pinned.cad.json`. Pinned versions are never pruned, including
after their jobs leave history. The rename also excludes pins from older WG's
ordinary-sidecar prune enumerator. Keep mirrors `captureMode: "run"`; the legacy
`project` setting migrates to Keep, and Don't keep mirrors `"off"`.

The archive writer pins before publishing `run.json` and records the returned
model-relative path as `cad.modelFile`. A retry preserves that field's original
presence/value from the matching record, including a record displaced into
`archive/run.json` by existing collision handling. Missing or unverified models
are reported without preventing result archival. Download and Open in Fusion
share checksum verification and look in `cad/`, legacy run copies, then recorded
run locations (including the older project's `cad/`). No existing run or model
file is moved automatically.

Every allocation, alias adoption, and registered-key reuse checks portable path
validation and available on-disk ownership, including run records. Reservations
made while a drive is offline are checked again when it returns. Conflicting
legacy mirror backfills and invalid persisted registrations receive a safe,
suffixed folder for future runs, with a warning in the server log. Existing runs'
sealed metadata, absolute paths, and files stay in place. The workspace writer
and allocator share `server/workspace/portable_paths.py` as their validator.

## Names and legacy evidence

The product and GitHub repository are `Waveguide Generator` / `waveguide-generator`.
`WG2_*`, `v2-snapshot`, the historical SPA asset name, and the macOS bundle identifier
are compatibility identifiers and must not be changed as part of cosmetic renaming.
The retired application is preserved on `origin/v1`; use immutable Git objects from
that branch when legacy behavior must be rechecked. A removed sibling checkout is not
a valid runtime dependency.

## Building the frontend and running the tests

Build the frontend **before running the Python tests**, including on a fresh
clone. The server mounts `frontend/dist`, which is generated and not checked in.
From the repository root, using Node 20:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m pytest server/tests scripts/tests -v
```

On Windows use `.venv\Scripts\python.exe` for the Python command. Tests that
exercise the Tk status view require an importable Tk installation; those view
tests skip explicitly when Tk cannot load; controller tests that do not import
the view still run. A missing frontend build stops the server suite once with
the build commands, rather than failing each app-constructing test separately.

JS frame codec (explicit file path — directory mode trips the node runner): `node --test shared/js/frame.test.mjs`

Frontend unit tests: `npm --prefix frontend test`

### Python static checking

Run `.venv/bin/python -m mypy` from the repository root after installing the
locked runtime dependencies and `server/requirements-dev.txt`. CI runs this
check in the Linux server job with those same runtime dependencies installed.

The first phase checks six boundary modules: job request/response models,
imported mesh integrity, the native-work permit, safe archive names, runtime
identity, and release asset names. `mypy.ini` lists the exact scope and enables
strict checking. Imported modules supply their types; errors outside the
listed modules are deferred with `follow_imports = silent`. This does not
claim whole-backend type safety. Extend the list with small, validated changes;
do not hide existing errors with a blanket project ignore list.

Real solves are never run in hosted CI; Metal and bempp parity run on owned
qualification hardware, and their archived reports back the release gates. Use
the [solver release qualification checklist](validation/SOLVER-QUALIFICATION.md)
for the mandatory macOS, Windows, full/quarter, and cross-solver runs.

## Verification

Run checks in proportion to the change:

```bash
.venv/bin/python -m ruff check server scripts shared launch launchers
.venv/bin/lint-imports --no-cache
.venv/bin/python scripts/run_tests.py --full -q -p no:cacheprovider
node --test shared/js/frame.test.mjs
cd frontend && npm test && npm run build
```

The lint line is first because it is the cheapest and the easiest to forget: it
is the same command the **Generated-file drift** job runs, and that job has
turned `main` red for a single unused import. `ruff` is in
`server/requirements-dev.txt` for exactly this reason. `lint-imports` checks the
server layers declared in `.importlinter`, including lazy and type-checking
imports. Higher layers may import lower ones. Jobs and CAD Link share an
orchestration layer; the engine registry sits above the solver adapters it
selects. API routers stay with their owning packages, with app and CLI assembly
above them. Tests are excluded from layer coverage, but a separate contract
keeps `server/contracts/` independent of every other server package. CAD Link
contracts enforce the four lanes, keep interpretation free of persistence and
lane imports (including indirect imports), and prevent preparation from importing
job admission shims. See [Server layout](architecture/CAD-OPERATIONS.md#server-layout).

Each current upward import is an exact, commented `ignore_imports` edge. New
edges fail, and an exception whose import disappears fails too: remove it as
part of the boundary cleanup. An exception covers all symbols on that module
edge; adding another symbol on an existing edge is not detected. New server
packages must be assigned a layer. The other two checks that
job runs are worth the same habit before a release:

```bash
.venv/bin/python scripts/gen_requirements.py --check
.venv/bin/python scripts/bump_version.py --check
```

Geometry, solver, platform, or release work may also require the pinned sibling suites,
constellation checks, a real browser, or owned qualification hardware. Hosted CI is not
solver qualification; its only real solves are the two tiny ones below.

`server/tests/test_real_pipeline.py` is the one default test with nothing faked: a tiny
OSSE through `/api/solve` on the pinned mesher and BEMPP, and a committed `.wgreturn`
through ingest, backend preparation and solve (on BEMPP-OpenCL or Metal), holding the
results and the installed module commits to `pins.json`. The tests use a
300-second job timeout as a hang guard; they do not impose a 60-second performance
threshold on cold compilation or shared hosted CPUs. Hosted Ubuntu's server
job installs pinned PoCL CPU OpenCL and requires the CAD path; other hosted jobs may skip
it with the registry's reasons. Before any pin move, run this file on a capable local
qualification host in an environment installed from the pins, explicitly requiring both
paths (a missing imported engine must fail):

```bash
WG_REQUIRE_IMPORTED_PIPELINE=1 .venv/bin/python scripts/run_tests.py server/tests/test_real_pipeline.py -q
```

Hosted CI runs Linux and Windows on every push to `dev` (a diagnostic after landing,
not a landing gate), and the full three-platform matrix when dispatched or called by
a release workflow. Pushes to other branches and pull requests run nothing.

### Running targeted tests

Run a subset with the launcher, not with a hand-built `pytest` line:

In a fresh worktree, build the frontend first (`cd frontend && npm ci && npx vite build`).
Tests that mount the real app serve `frontend/dist` and fail without it; they say so in
their error message.

```bash
.venv/bin/python scripts/run_tests.py server/tests/test_design_*.py -q
.venv/bin/python scripts/run_tests.py server/tests/test_x.py::test_one -x
.venv/bin/python scripts/run_tests.py --changed main -m "not slow" -q
```

`scripts/run_tests.py` expands its globs itself and **refuses to run** when no target
is given or any target resolves to no test files. Bare `pytest` with an empty file list
runs the whole default suite, which is how a targeted run once became a 20-minute one.
Known flags (`-q`, `-x`, `--co`, `--lf`, `--ff`) pass through to pytest. Known value
options accept separate or attached values (`-k expr`, `-kEXPR`, `-p name`,
`--ignore path`, `--ignore=path`); a missing, empty or dash-prefixed value is refused.
Other long options must use `--opt=value`. Unknown bare options are refused because
the launcher cannot safely distinguish their values from targets; bare `--flag`
options must be in its known flags set. Use `--` to end launcher option parsing if
needed. Pytest always receives `--` before the resolved paths, so it cannot consume
a target as an option value. All launcher refusals exit with code 2.

`--changed [base]` uses `git diff --name-only <base>...HEAD` (default `HEAD`),
plus staged, unstaged, and untracked files. `scripts/test_map.toml` maps source
prefixes to conservative Python test globs. Changes to existing test files select
those files. Unknown paths, deleted test files, shared code, conftests, requirements,
and the test launcher/map select the full Python suite. An empty diff is refused.
This is an inner-loop aid: frontend changes still require Node 20 frontend checks,
and validation scope follows the workspace policy (§3.1), not this selector.

### Parallel suite execution and checkpoints

Development requirements are maintained in `server/requirements-dev.txt`; the
resolved versions also belong in `server/requirements-lock.txt`.
`scripts/gen_requirements.py` generates `server/requirements-pins.txt` from
`pins.json`; `scripts/hash_lock_requirements.py --write` generates
`server/requirements-hashed.txt` and `server/requirements-runtime-hashed.txt`
from the lock. Do not edit these generated files by hand. When changing
dependencies, create a fresh Python 3.13 venv and install the lock, then the pins
with `--no-deps`, then the dev requirements. Do not modify another session's venv.

`--full` selects `server/tests scripts/tests`, including all `slow` tests, and
defaults to four xdist workers. Set `WG_TEST_WORKERS` or pass `-n` to override it;
the supported range is 0–6. Explicit targeted runs remain serial unless given `-n`.
Collection-only runs omit the default worker count. Never use `-n auto` on the shared Mac.
Two broker lanes may be active together, so four workers per lane can already mean
eight workers. The suite also limits BLAS, OpenMP, and Numba pools to one thread per
process so the native libraries do not multiply that budget again.

Run heavy gates through the workspace compute broker, with an honest expected
duration, and wait for their exit status. For example, after configuring `broker`:

```bash
broker submit --lane suite --priority 2 --expected 12 \
  --requester "<session>" --purpose "<branch>: full Python gate" --cwd "$PWD" \
  --shell '.venv/bin/python scripts/run_tests.py --full --durations=30 -q -p no:cacheprovider'
broker wait <id> --timeout 540
```

For the inner loop, use targeted paths or `--changed`, optionally with
`-m "not slow"`. The marker excludes expensive geometry, real solver, and process
integration tests only when explicitly requested. It never changes the default
full gate.

Parallel execution requires `--dist=loadgroup` (the launcher adds it for `-n`).
Prefer isolation: each worker has its own import-time data/add-in sandbox and
process globals, `tmp_path` is worker-specific, and shutdown harness servers use
a test-local port-0 reservation and publish the kernel-assigned port. The
production CLI still validates ports 1–65535. The session-claim guard restores its global
after each test, and native gmsh calls remain on the owning process's worker
thread. A `serial` marker groups tests onto one worker; it does **not** pause other
workers or the other broker lane. Use it only when those tests share a resource,
not to claim an idle machine for a wall-clock assertion.

Validation scope is owned by [the workspace policy](../GIT-WORKFLOW.md) §3.1:
each routine merge requires fast checks, affected tests and a scoped review.
The full suite runs at the `dev` checkpoint and on release candidates, or earlier
for a concrete broad-impact risk. A correction reruns only failed and affected
checks; unchanged results remain valid. Serial/parallel parity is evidence for
the runner configuration, not a requirement to run both modes for every merge.
Use the launcher above for execution and the compute broker for heavy runs.

S1-full qualification on the shared ten-logical-CPU Mac used a fresh Python 3.13
environment and both complete Python suites, with slow tests included:

| Execution | Wall time | Passed / skipped | Mean CPU cores | Broker job |
| --- | ---: | ---: | ---: | --- |
| Serial (`-n0`) | 18m 39s | 5695 / 51 | 0.89 | `260929-233140-suite-827e` |
| Four workers | 5m 16s | 5695 / 51 | 3.34 | `260929-234149-suite-f480` |
| Six workers | 3m 39s | 5695 / 51 | 4.97 | `260929-233223-suite-935d` |

Wall and aggregate user/system CPU time come from `/usr/bin/time -l`; mean
cores are CPU seconds divided by wall seconds. The serial/four-worker pair used
the same frozen tree. The six-worker run differed only by an unused test constant
removed after startup. Its other broker lane was held idle. A separate
`-n1` run was submitted and rejected by broker triage as duplicating the approved
serial baseline; it was not executed.

Four workers give a measured 3.54× speedup and remain the default. Six reduced
wall time by another 31% and used 49% more mean CPU cores. Two six-worker lanes
would consume about all ten cores on average; two four-worker lanes leave
capacity for foreground work. These are individual broker runs on the shared
Mac; their logs record host load before and after execution.

`--durations=30` identified the largest export calls at about 104, 54, and 52
seconds, alongside real BEMPP, scale, and shutdown integrations. The `slow`
selection covers 21 of 5746 tests. Refresh the duration evidence when changing
expensive tests; keep all of them in the full gate.

The server suite does not need `frontend/dist` as a whole: only the tests that build the
real app (which serves the built SPA) do. Those fail with the build instruction
(`npm --prefix frontend ci && npm --prefix frontend run build`) when it is missing; the
pure tests run without it. Nothing builds the frontend for you.

### Stopping a run

Stop only your own processes, by a recorded PID or process group, or with
`broker cancel <id>` for a broker job. Never `pkill -f <pattern>` (or `killall`, or any
name-matching kill): the pattern matches other people's suites on the same machine, and
one did SIGTERM two other sessions' runs.

## Feature flags

`WG2_ENABLE_ONSHAPE=1` turns the parked Onshape adapter on. It is off in every other
run, including a git-checkout install, so export it in a development launcher to work
on the adapter. With it off the Onshape routes are not mounted, `/api/capabilities`
reports `onshape: false`, and the interface hides every Onshape control; a stored
Onshape choice reads as Fusion and is left in place.

## Building the standalone desktop apps

`scripts/build_bundle.py` creates the relocatable CPython runtime layer, an
application layer materialized from committed blobs at the packaged commit, a
platform bundle, and the install/update assets under `build/bundle/`. It installs
only `server/requirements-runtime.txt` and `server/requirements-pins.txt`, constrained
by `server/requirements-lock.txt`, never the development requirements. Untracked files,
working-tree edits, tests, caches, and checkout line-ending filters do not enter the
app layer. The macOS build
requires Apple Silicon macOS, `uv`, Swift, `codesign`, and `hdiutil`.

Verification now creates private data and Fusion AddIns directories and forces both
`WG2_DATA_DIR` and `WG2_FUSION_ADDINS_DIR` into every verification child environment,
replacing ambient overrides. Before this fix, a local build with verification enabled
on a machine with Fusion could touch the real AddIns folder during server startup.
Until you use a builder containing this fix, pass `--skip-verify` or set
`WG2_FUSION_ADDINS_DIR` to a private folder before building.

Release builds pass the checksum file beside the SPA tarball automatically:

```bash
python3 scripts/build_bundle.py \
  --spa update-spa-<version>.tar.gz \
  --output build/bundle
```

Without `--spa`, `frontend/dist` must already contain the matching verified
`.wg2-spa.json` release stamp. `--runtime-only` and `--app-only` are refused (the layer
updater was removed in 0.3.6), `--python-version` can rehearse another CPython 3.13 patch, and
`--skip-verify` skips the copied-bundle gate (backends ready, `/` and `/health`
answer, and `codesign --verify --strict` still passes after the run, which proves the
launcher's cache redirection keeps the sealed bundle unmodified). A normal local build
should leave verification enabled. The output directory must not exist or must be empty;
the builder refuses stale files instead of mixing them into the asset set:

```bash
.venv/bin/python scripts/build_bundle.py --output build/bundle
```

The Windows build must run on x86-64 Windows with `uv` and the x64 Microsoft Visual
C++ redistributable files available on `PATH`, in `System32`, or below the Visual
Studio redistributable tree. `uv python install` cannot install another host platform,
so `--platform windows` deliberately refuses on macOS/Linux instead of packaging a
host interpreter under a Windows asset name:

```powershell
python scripts/build_bundle.py --platform windows `
  --spa update-spa-<version>.tar.gz `
  --output build/bundle
```

It leaves `build/bundle/Waveguide Generator/` and archives that folder as
`Waveguide.Generator-<version>-windows-x86_64.zip`; the dotted filename is also the
name GitHub serves, while the archive root keeps the product's spaces. The runtime uses the
same pinned CPython patch as macOS and carries `vcruntime140.dll`,
`vcruntime140_1.dll`, and `msvcp140.dll` for clean machines. Bundle verification runs
the renamed `pythonw.exe` launcher with `-c`, requires `scripts/check_backends.py` to
report the bempp/numba backend ready, and requires `/` and `/health` to answer. The
bootstrap separately recognizes CPython's no-command state as the Explorer
double-click contract and enters `launchers.desktop`; `-c`, `-m`, and script
invocations remain ordinary worker/interpreter paths. Executing that no-command path
on Windows remains an open real-platform gate. The included
isolated `Waveguide Generator._pth` uses CPython's permitted `import site` hook;
`sitecustomize.py` then imports `wg_desktop_bootstrap` to distinguish a direct GUI
launch from the same executable being reused as `sys.executable` by workers. The
included `WaveguideGenerator.ico` can be selected for a shortcut; setting the icon inside
`Waveguide Generator.exe` itself requires a Windows PE resource edit and is deferred
until the executable-signing work.

### The Linux bundle

`--platform linux` builds `Waveguide.Generator-<version>-linux-x86_64.tar.gz`: the
application folder `waveguide-generator/` — `app`, `runtime`, the `waveguide-generator`
launcher, a `.desktop` template and a 512px icon — beside `install.sh`, `uninstall.sh`
and `READ ME FIRST.txt` at the archive root. That layout is the Windows one, not the
macOS one, so one rule resolves the installation root on Windows and Linux.

**What "Linux" means here is Ubuntu 24.04 LTS on x86-64**, and distributions close
enough to it. That is a narrow claim on purpose. The release job runs on `ubuntu-24.04`
rather than `ubuntu-latest` so the glibc the runtime links against cannot move under a
release without someone deciding to move it, and the interpreter is requested as
`cpython-<version>-linux-x86_64-gnu` so a build on a musl or arm64 host cannot silently
produce a runtime the asset name does not describe. There is no arm64 and no musl build.

The build must run on Linux. `uv` *can* unpack the Linux interpreter on any host — it is
a tarball — but the layer installed on top of it is compiled manylinux x86-64 wheels
plus a source build of the pinned HornLab modules, which no macOS or Windows `pip`
resolves; a cross-built bundle would carry the right interpreter and the wrong
extensions, and fail at import in a user's hands rather than at build time.

Three things differ from the other two platforms, each for a measured reason:

- **Tcl/Tk is kept, not pruned.** On Linux there is no native window, so
  `launchers.desktop` falls back to the tkinter status window — the status window *is*
  the desktop application there. Pruning Tk as macOS and Windows do would have shipped a
  bundle whose only interface cannot start, and it would have looked like a size win.
  It costs nothing else: measured 2026-09-04 on a bare `ubuntu:24.04` container with no
  X11 package installed, the pinned runtime imports `tkinter` (Tk 9.0) and reaches
  `couldn't connect to display ":99"` — a Tcl-level error, so Tk initialised and its
  X11 client code is statically linked rather than resolved from the system. The Tcl/Tk
  trees and their two shared objects are about 8 MB of a 102 MB runtime.
- **The system OpenGL and X11 libraries are not in the bundle.** The `gmsh` wheel
  dlopens them at import, and gmsh is the single geometry authority, so an install
  without them opens the interface and meshes nothing. Measured the same day on the same
  container, adding one package at a time until `import gmsh` succeeded: `libglu1-mesa
  libgl1 libgomp1 libfontconfig1 libxrender1 libxcursor1 libxft2 libxinerama1 libxi6
  libxext6`. A desktop system has all of them; the bare container had none, which is why
  it is a useful floor and not a description of a user's machine. `install.sh`
  preflights by importing gmsh with the bundled interpreter and stops before copying
  anything, printing the failing library from the real error and the apt command.
- **The install is per-user and refuses root.** The in-app updater replaces `app` and
  `runtime` inside the installation as the user and cannot elevate, so a root-owned copy
  under `/opt` would install once and refuse every update afterwards — the same reason
  the Windows installer writes to `%LOCALAPPDATA%\Programs`. `install.sh` puts the
  application in `$XDG_DATA_HOME/waveguide-generator`, the entry in
  `$XDG_DATA_HOME/applications`, the icon in `hicolor/512x512/apps`, and a symlink in
  `~/.local/bin`; `uninstall.sh` removes exactly those, takes the PATH symlink only when
  it still points into the installation being removed, and keeps the workspace unless
  `--data` is passed. Both refuse a directory with no `app/APP-MANIFEST.json` in it,
  because `rm -rf` over a path built from `--prefix` is how a home directory is lost.

Bundle verification is the same shape as the other two: `scripts/check_backends.py`
must report the bempp/numba backend ready, `/` and `/health` must answer, and then the
*shipped launcher* is started with `--no-gui` and must serve the application — the gate
that covers the script the desktop entry actually executes, which running the
interpreter directly says nothing about. It is headless by request; the status window
itself needs a display and is not covered.

The runtime id is content-addressed from the two direct requirement files, the full
constraint lock, the exact Python patch and python-build-standalone build, and the
versioned bundle recipe. Runtime ZIPs use sorted entries, fixed timestamps, and preserved
modes. All three release jobs materialize the platform-neutral app ZIP from the same
committed blobs and verified SPA, with canonical modes and stored entries, so platform
line-ending filters and zlib versions cannot change its bytes. Release CI compares each
platform's SHA-256 against the macOS one before uploading that platform's assets, and
only the macOS job uploads the shared app layer. The builder and extractor reject links, absolute paths, `..` traversal,
Windows-reserved names, case-insensitive collisions, and AppleDouble metadata. Complete
installer assets use `Waveguide.Generator-<version>-<platform>.<extension>` before their
checksum sidecars are written, matching the filenames GitHub serves.

## Standalone application updates

Updates are signed full installers; there is no layer updater from 0.3.6.
`server/updates/installer_client.py` checks releases (stable: `/releases/latest`;
beta: the bounded release list) and accepts a release only when its three installers,
`SHA256SUMS` and `SHA256SUMS.sig` are present and the manifest verifies against the
compiled Ed25519 keys (`docs/reference/INSTALLER-CLIENT-CONTRACT.md`).
`POST /api/updates/install` downloads this platform's installer under
`<data>/update-install/<version>/` and re-verifies it; the server then approves a
restart and publishes the launcher's `update.json` request. The launcher
(`launchers/full_installer.py`) consumes it, closes, and starts the installer in update
mode: Windows setup with `/VERYSILENT /WAITPID /OUTCOME /WGLOG /RELAUNCH /DIR`, macOS
`Install Waveguide Generator.command --update`, Linux `install.sh --update --prefix`,
the last two through `launchers/installer-helper.sh` copied outside the installation
(`docs/reference/INSTALLER-HANDOFF-CONTRACT.md`). Any other request kind, such as a
pre-0.3.6 layer or checkout request, is discarded and the restart latch released.

`WG2_UPDATES_API_BASE` (default `https://api.github.com`) redirects the latest-release
and release-list requests for an end-to-end rehearsal against a local fake. Production
asset URLs must identify this repository's GitHub release assets, and each redirect hop
is revalidated against the allowed GitHub release CDN
(`server/updates/release_urls.py`). A rehearsal accepts plain `http://` only at the same
literal loopback origin as the API base; redirects cannot escape it.

Installed 0.3.4 and 0.3.5 clients discover and hand off to these installers with their
own released code; `server/tests/test_frozen_installer_compatibility.py` and
`scripts/tests/test_frozen_installer_handoff.py` run that code from the release tags.

## Documentation changes

Every new document must state its audience or status. Repository plans must record an
accepted design decision, not a personal task list. Dated measurements include the
machine and tested state. Moving a contract requires updating any test or docstring that
reads it directly.

### Live design dimensions (C2)

The parametric viewport displays accepted preview metadata directly, next to the
live timing readout, alongside either parameter tab:
`previewMetadata.dimensions_mm`: `mouth_opening` is `[W, H]`,
`horn_overall` and optional `enclosure_overall` are `[W, H, D]` in millimetres.
Canonical `resolve_geometry` design controls supply the values, displayed to one
decimal place as Mouth W × H, Depth D, Overall W × H only when either differs from
the mouth by more than 0.05 mm, and Enclosure W × H × D when present. One mm unit
ends the readout. The header wraps and reserves room above banners and stats.
The sampling method is `resolved-design-geometry`; these are not measured CAD or
STEP bounds. Exported morphed mouths can differ by the mesher's fitting tolerance.
Optional `dimensions_requested_mm.mouth_opening` supplies the requested morph
target; the title tooltip shows requested and effective sizes when they differ.
Each requested axis is formatted separately: zero displays as "auto" beside an
explicit axis. Both implicit axes omit the requested pair.
The readout displays no revision text; revision and sequence checks still reject
old responses. New/Open retains the previous viewport frame while hiding its
readouts until an accepted frame belongs to the new client document identity.
Matching error-only responses end the document wait with unavailable sizes.
Ownership also tracks connection generation: backend restarts may repeat a server
epoch and reset request sequences. A replacement connection without dimension keys
clears the old readouts. Document observation while paused and document-scoped
error filtering apply only to dimension metadata. Older meshers without dimension
keys retain established errors, badges and notifications and hide the readout.
A coarse `dimensions_status: "pending"` frame retains the same document's canonical
values, dimmed with "Updating dimensions" in the title and accessible label. The
viewport's lane-selection policy prevents late coarse frames from downgrading
settled sizes. "current" displays values plainly; "unavailable" clears them.
Invalid drafts and failed edits label retained values "Last valid preview".
Only mounted parameter panels are observed for invalid drafts; observer cleanup
and reattachment follow panel lifecycle. A separate visually hidden live region
announces freshness changes, without announcing every frame's dimension digits.
Completed run summaries continue using that run's `mesh_stats.dimensions_m`.
The fine producer metadata and a coarse pending example are captured in
`shared/preview-fixtures/c2-dimensions-metadata.json` and
`shared/preview-fixtures/c2-dimensions-pending-metadata.json`.
The real-mesher server regression skips until the dependency pin supplies C2.

### Moving earlier run archives

Use **Move earlier runs…** from a CAD project’s folder menu, **Earlier runs of
this design…** from Design, or **From an earlier version · Move into …** in Run
details. Opening the tool only saves a preview. Identity matches are checked;
name-only matches and every Untitled match require selection. Changing selection,
**Gather Fusion files into cad/** (initially checked), or **Rename folder to
match …** (initially unchecked) creates another preview. **Move N runs** confirms
that saved preview; paths, counts, byte sizes, methods and old-folder cleanup are
shown before execution.

`workspace/run_moves.py` owns planning, exclusion, execution, journal recovery and
undo. Journals live in application data `moves/<id>.json`, never in Runs. Atomic,
fsynced intent records precede mutations. Exclusive same-volume renames cannot
replace a concurrent destination. Cross-volume moves stage copies, fsync and
SHA-256-verify every file across the whole move before removing any original.
Deletion touches only verified manifest members, rechecks each source and
retained destination before unlink, and can resume a partially removed tree.
Unexpected new files remain in place and are reported. Temporary copies journal
each completed, fsynced member. Cleanup verifies a safe retained source first,
then unlinks only journaled members whose current type, size and SHA-256 match;
directories are removed only when empty. Interrupted unjournaled writes and
unexpected partial contents require attention. Every recovery rechecks
destination bytes; a past verified marker never licenses later deletion.
Recovered undo uses the same final-tree, subsequent-run and link checks as
interactive undo before reverse renames, accounting only for journaled restores
and renames already landed. Folder identity also detects replaced rename roots.
Gathered deletion targets are verified again immediately before unlink.

Recovery runs before the jobs runtime starts. A pending journal keeps affected
projects locked after an error; writes answer 409 **Runs are being moved**.
Capture, pin, pruning, workspace writes and automatic-export reservations share
the exclusion. A submission for an affected project waits outside the CAD lock
until its pending move or undo finishes. At job creation (and CAD preparation
binding), location assignment is repeated under the same CAD lock that moves
hold from busy-check through reference commit. This closes the gap between
request composition and durable job creation; the queued job uses the final
registered folder. Unrelated submissions continue normally. Recovery rechecks
busy jobs and journals references for jobs added since the move journal (for
example by an earlier server version). A busy older job refuses recovery until
it is stopped; an unarchived location is never moved underneath a writer.
Existing queued/running/not-yet-archived runs in affected folders refuse a new
move. Reconnect the drive or close the file, then use **Retry recovery**. Startup displays a recovery notice. Ordinary
previews are never executed at startup.

The journal coordinates separate jobs/CAD database transactions. It updates
`task_metadata.archive.{key,folder,run,moved}`, `archived_path`, an existing
`imported_geometry.archive_stem`, and recorded `exported_files` under the moved
run. One-off export paths elsewhere remain valid. Registry aliases and the
`lineage_cad_names` / `imported_models` compatibility mirrors follow an explicit
project rename. Server `design.json` pointers follow the location and current
name; the durable jobs metadata stream updates other windows. This version has
no separate export-index file or live `project.json` writer; an opaque
`project.json` inside a run moves unchanged, and unknown project-level contents
prevent a whole-folder rename.

**Moved run.json files are byte-for-byte unchanged: no fields, including
cad.modelFile, are rewritten.** Legacy CAD locations in those immutable records
remain historical references. Fusion download/open/listing resolve through the
full return-state hash and retained-manifest verifier. Gather keeps one `.f3d`
per checksum, named `.pinned.f3d`, and one protected `.cad.json` evidence record per return state. When
states share identical document bytes, the sidecar’s `modelFile` basename points
to the shared file inside the same `cad/`; it cannot escape that folder. These
pinned records stay outside the older-WG ordinary-sidecar prune enumerator.

**Undo this move** is available for completed same-drive moves only. It refuses
changed files, occupied/linked originals and subsequent archives, restores exact
original sidecar bytes through fsynced temporary files, and rechecks restored
originals before removing gathered copies. Undo itself is journaled and
recoverable. Cross-drive originals have been removed after verification, so that
move has no undo in this tool. No source outside a known Runs root, unknown job,
symlink/junction, or not-downloaded File Provider placeholder is accepted.
