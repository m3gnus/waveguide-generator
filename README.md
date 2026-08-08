# Waveguide Generator v2

Interactive 3D design and BEM simulation for acoustic waveguides — a
from-scratch rebuild of the Waveguide Generator application on a TypeScript/React
frontend and a FastAPI backend, with the mesher as the single geometry authority.

**Status: beta, not yet the default.** v1 remains the supported release and lives
on the `v1` branch of this repository. v2 runs beside it on port 3100 with its
own data directory, so both can be installed at once. Cutover — installers, the
v1→v2 jobs-database migration, Windows qualification, and the beta matrix — is
tracked in [docs/P6-CUTOVER-PLAN.md](docs/P6-CUTOVER-PLAN.md).

Design and contract documents live in [docs/](docs/); the traceability table
mapping every v1 behavior to its v2 owner is
[docs/TRACEABILITY-TABLE.md](docs/TRACEABILITY-TABLE.md).

## Install

Clone the repository — do not download a ZIP, because the installer updates
itself with Git and the four pinned HornLab modules are installed from Git too.
Then run the installer for your platform:

| | |
|---|---|
| macOS | double-click `install-wg2.command`, or `bash scripts/install.sh` |
| Windows | double-click `scripts\install-and-update.bat` |
| Linux | `bash scripts/install.sh` |

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
bash scripts/uninstall.sh            # macOS/Linux: .venv and frontend/dist
bash scripts/uninstall.sh --data     # also designs, job history, meshes, logs
scripts\uninstall.bat                # Windows, same options
```

Neither touches the checkout itself, nor anything belonging to v1 — delete the
folder yourself when you are done with it.

## Launch

On macOS, double-click `launch-wg2.command` in Finder. Keep the Terminal window
open while using WG v2; closing it stops the local server. On first launch it
creates `.venv` with CPython 3.13 and installs the locked v2 dependencies. It
does not use or modify the v1 environment. Direct packages, transitive versions,
and Hornlab module commit SHAs are locked in separate requirement manifests.

The same launcher works from Terminal:

```
./launch-wg2.command
```

It opens WG v2 in the default browser and uses the first available port from
3100 through 3109. Advanced flags such as `--port`, `--no-browser`, and
`--data-dir` can be appended when launching from Terminal.

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

v2 versions are plain `MAJOR.MINOR.PATCH`, starting at **2.0.0**. v1 is a
separate line at 1.x, so the two never collide.

The version lives in `shared/version.json` — `/health` and the FastAPI metadata
read it at runtime, and Vite injects it into the SPA as `__WG2_VERSION__` at
build time. npm keeps two further copies, in `frontend/package.json` and
`frontend/package-lock.json`, so move all of them with one command rather than
by hand:

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
installing v2 needs no Node runtime.

Pre-release and build-metadata suffixes are deliberately unsupported: the tag is
built as `v` + this string, and the installer and update check compare it.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE). The pinned HornLab solver, mesher,
and plotting modules are separate AGPL repositories referenced by commit SHA in
[pins.json](pins.json).
