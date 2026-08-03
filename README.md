# Waveguide Generator v2

In development — see ../WG-REBUILD-PLAN.md and docs/.

## Run the server (dev)

```
"../Waveguide Generator/.venv/bin/python" launch/serve.py --port 3100
```

Flags: `--no-browser`, `--data-dir` (or `WG2_DATA_DIR`); `WG2_ENABLE_DRYRUN=1` exposes the dry-run engine (dev/test only).

## Test commands

Python (uses the v1 venv): `"../Waveguide Generator/.venv/bin/python" -m pytest server/tests -q`

JS frame codec (explicit file path — directory mode trips the node runner): `node --test shared/js/frame.test.mjs`
