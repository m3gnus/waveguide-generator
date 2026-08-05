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

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE). The pinned HornLab solver, mesher,
and plotting modules are separate AGPL repositories referenced by commit SHA in
[pins.json](pins.json).
