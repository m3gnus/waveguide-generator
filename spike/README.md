# Phase 0 viewport and chart spike

> **Historical validation artifact (2026-08-03).** The adjacent v1 checkout and
> interpreter used by these commands have since been retired, so this is not a
> runnable development guide for the current application. The code and measured
> results remain as evidence for the preview transport and charting decisions.

This directory is self-contained apart from the adjacent read-only v1 checkout and its Python
virtual environment. It has no build step and makes no browser requests to the internet. The
committed `static/vendor/` files are Three.js from the v1 installation and ECharts 6.1.0 from this
spike's lockfile.

The adapter import adds `../Waveguide Generator/server` to `sys.path` and imports
`solver.mesher_adapter` directly. `server/solver/__init__.py` is empty, so this avoids
`solver_bootstrap` and its runtime side effects.

## macOS / Linux commands

Run extraction, tests, and the benchmark from the `waveguide-generator-v2` repository root:

```sh
"../Waveguide Generator/.venv/bin/python" spike/payloads/extract_payloads.py
"../Waveguide Generator/.venv/bin/python" spike/test_frame_codec.py
"../Waveguide Generator/.venv/bin/python" spike/bench_preview.py
```

The benchmark defaults to the specified 40 warm iterations for every family/LOD case. A quick
development check can use a smaller count:

```sh
"../Waveguide Generator/.venv/bin/python" spike/bench_preview.py --warm-runs 3
```

Start the server from the repository root on port 3199:

```sh
"../Waveguide Generator/.venv/bin/python" -m uvicorn ws_server:app --app-dir spike --port 3199
```

Equivalently, from inside `spike/`, the v1 checkout is two levels up:

```sh
"../../Waveguide Generator/.venv/bin/python" -m uvicorn ws_server:app --port 3199
```

Open:

- preview and 10-second sweep: <http://127.0.0.1:3199/>
- real-result ECharts probe: <http://127.0.0.1:3199/static/charts.html>
- largest persisted result JSON: <http://127.0.0.1:3199/api/results/real>

The server startup imports the adapter and runs one pre-warm call per family, logging the per-family
and total pre-warm milliseconds. The result endpoint opens
`server/data/simulations.db` through a SQLite `file:...?mode=ro` URI and returns the largest
`simulation_results.results_json` string without reserializing it.

`payloads/extract_payloads.py` also opens the database read-only. OSSE and R-OSSE come from real
jobs. The only real FREEFORM job contains the retired `corner_ratio` station field and fails the
current canonical API, so extraction falls back to the valid payload in
`server/tests/test_freeform_server.py`. There is no ICW job in the database, so ICW uses the current
defaults from `src/config/schema.js`. Every selected fixture is smoke-called before any fixture is
written.

Benchmark outputs are `results/preview-timings.json` and `results/preview-timings.md`. Cold process
time includes interpreter startup, imports, payload loading, and the first viewport call; import and
first-call evaluation are also reported individually. Frame encode time includes point-grid
tessellation and WGF0 serialization.

## Windows box: run this

Assume `waveguide-generator-v2` and `Waveguide Generator` are sibling directories. In PowerShell,
from the `waveguide-generator-v2` repository root:

```powershell
& "..\Waveguide Generator\.venv\Scripts\python.exe" spike\payloads\extract_payloads.py
& "..\Waveguide Generator\.venv\Scripts\python.exe" spike\test_frame_codec.py
& "..\Waveguide Generator\.venv\Scripts\python.exe" spike\bench_preview.py
& "..\Waveguide Generator\.venv\Scripts\python.exe" -m uvicorn ws_server:app --app-dir spike --port 3199
```

Then open <http://127.0.0.1:3199/> and <http://127.0.0.1:3199/static/charts.html>. Commit or copy the
two generated timing files to preserve the Windows latency baseline alongside the Mac result.
