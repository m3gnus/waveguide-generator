# Waveguide Generator v2

In development — see ../WG-REBUILD-PLAN.md and docs/.

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
