# Waveguide Generator

Interactive 3D design and BEM simulation for acoustic waveguides — a
from-scratch rebuild of the Waveguide Generator application on a TypeScript/React
frontend and a FastAPI backend, with the mesher as the single geometry authority.
This is the second-generation rewrite, historically called v2, and replaces the
original application.

Design and contract documents live in [docs/](docs/); the historical traceability
table mapping original-application behavior to its current owner is
[docs/TRACEABILITY-TABLE.md](docs/TRACEABILITY-TABLE.md).

## Install

Clone the repository — do not download a ZIP, because the installer updates
itself with Git and the four pinned HornLab modules are installed from Git too.
Then run the installer for your platform:

| | |
|---|---|
| macOS | double-click `installers/macos/install-wg.command` |
| Windows | double-click `installers\windows\install-and-update.bat` |
| Linux | `bash installers/linux/install.sh` |

It fast-forwards the checkout, downloads that version's prebuilt interface from
the GitHub release and **refuses to extract it unless it matches the published
SHA-256**, creates `.venv` with CPython 3.13 and the locked dependency set,
checks that a solve can actually run, and starts the app. Running it again is
cheap: an unchanged install re-verifies in about a second and contacts no index.

Prerequisites, all reported with the command that installs them: CPython 3.13
(exactly — the dependency set is locked against one series), Git 2.20+, the
Microsoft Visual C++ Redistributable on Windows, and the Xcode Command Line
Tools on Apple Silicon for the Metal solver.

Useful flags: `--tag vX.Y.Z` installs a specific release, `--skip-spa` leaves
`frontend/dist` alone while you are working on the interface, `--no-launch`
stops before starting the app, and `--force` rebuilds the environment.

To check the solve backends at any time without a full install:

```
.venv/bin/python scripts/check_backends.py
```

### Uninstall

```
bash installers/macos/uninstall.sh         # macOS: .venv and frontend/dist
bash installers/linux/uninstall.sh         # Linux: same options
installers\windows\uninstall.bat            # Windows: same options
# Add --data to also remove designs, job history, meshes, and logs.
```

Neither touches the checkout itself — delete the folder yourself when you are
done with it.

## Launch

The launchers open a compact status window with separate backend and frontend
lamps, the local URL, an **Open in browser** button, and a **Quit** button. Quit
or close the window to stop the complete server process tree.

| | |
|---|---|
| macOS | open `launchers/macos/Waveguide Generator.app` |
| Windows | double-click `launchers\windows\launch-wg.bat` |
| Linux | `./launchers/linux/launch-wg.sh` |

The macOS app is deliberately unsigned. The first time, Control-click (or
right-click) **Waveguide Generator.app**, choose **Open**, then confirm **Open**.
After that, normal double-clicks work. If macOS still blocks it, open
**System Settings → Privacy & Security** and choose **Open Anyway** for the app.

The repository root intentionally has no duplicate install or launch scripts;
use the platform folders above. On first launch the entry creates `.venv` with
CPython 3.13 and installs the locked dependencies.

For the original plain-terminal behavior, append `--no-gui`:

```
./launchers/macos/launch-wg.command --no-gui
./launchers/linux/launch-wg.sh --no-gui
launchers\windows\launch-wg.bat --no-gui
```

The launcher uses the first available port from 3100 through 3109. Advanced
server flags such as `--port`, `--no-browser`, and `--data-dir` can be appended.
The committed app icon is reproducible with
`python launchers/macos/generate_icon.py` on macOS; the generator uses only the
standard library and validates the resulting ICNS container with `iconutil`.

### Application updates

The version in the top-left corner checks GitHub's latest published full
release after the interface opens. When a newer, complete release is available
it turns amber and says **update available**; click it for the exact local
installer command and release details. The same action is available from the
command palette as **Application update**.

WG caches successful checks, retries incomplete releases quickly, and keeps the
last known result when the network is unavailable. It also inspects the local
checkout without changing it: modified, development, detached, and non-Git
installs are explained instead of being handed a command that would silently do
the wrong thing. Close Waveguide Generator before running a copied update
command so the installer can acquire the application data lock.

### Original-app run migration

On launch, Waveguide Generator looks for the original application's v1 run
database in an upgraded checkout and in sibling checkout folders. When it finds
one, it automatically merges its runs, results, mesh artifacts, and saved
workspace into the current data directory before the server starts. The v1
database is opened read-only, the current data is backed up first, existing
current-version runs win on an ID collision, and content hashes are verified
before startup continues. A completion marker makes later launches no-ops;
additional v1 runs are picked up if the source database changes.

For a v1 checkout stored somewhere else, set `WG1_ROOT` to that checkout before
launching. The manual dry-run, reporting, and rollback interface remains
available:

```
.venv/bin/python scripts/migrate_v1.py --v1-root "/path/to/v1 checkout" --dry-run
.venv/bin/python scripts/migrate_v1.py --rollback "/path/to/migration backup"
```

## Run the server directly (dev)

```
python3.13 scripts/bootstrap.py
.venv/bin/python launch/serve.py --port 3100
```

The bootstrap is idempotent: unchanged, valid environments do not contact the
package index. Run `.venv/bin/python scripts/bootstrap.py --check` to validate
without installing, or use `--force` to reinstall the locked dependency set.

Flags: `--no-browser`, `--data-dir` (or `WG2_DATA_DIR`); `WG2_ENABLE_DRYRUN=1` exposes the dry-run engine (dev/test only).

## Test commands

Python: `.venv/bin/python -m pytest server/tests -q`

JS frame codec (explicit file path — directory mode trips the node runner): `node --test shared/js/frame.test.mjs`

Frontend: `cd frontend && npm ci && npm test && npm run build`

Real solves are never run in hosted CI; Metal and bempp parity run on owned
qualification hardware, and their archived reports back the release gates.

## Releasing

Versions are plain `MAJOR.MINOR.PATCH`, starting at **2.0.0**. The original
application remains on the separate 1.x line, so the two never collide.

The version lives in `shared/version.json` — `/health` and the FastAPI metadata
read it at runtime, and Vite injects it into the SPA as `__WG2_VERSION__` at
build time. npm keeps two further copies in `frontend/package.json` and
`frontend/package-lock.json`, and the macOS app has two bundle-version keys, so
move all of them with one command rather than by hand:

```bash
python scripts/bump_version.py patch
```

`major` and `minor` do the obvious thing, `--set X.Y.Z` sets an exact version,
and `--check` proves every copy agrees. CI runs `--check` on every push; so does
`server/tests/test_version_consistency.py`.

Then commit, and tag:

```bash
git tag v2.0.1 && git push origin v2.0.1
```

The tag fires `.github/workflows/release.yml`, which **refuses to build when the
tag disagrees with `shared/version.json`** — a build that misreports itself is
worse than a failed release. It then attaches the prebuilt SPA as
`waveguide-generator-v2-spa-<version>.tar.gz` with a `.sha256` beside it, so
installing Waveguide Generator needs no Node runtime.

Pre-release and build-metadata suffixes are deliberately unsupported: the tag is
built as `v` + this string, and the installer and update check compare it.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE). The pinned HornLab solver, mesher,
and plotting modules are separate AGPL repositories referenced by commit SHA in
[pins.json](pins.json).
