# Phase 0 spike — measured results (Mac, 2026-08-03)

Machine: Magnus's Mac (Apple Silicon, macOS 26.5.2), v1 oracle at WG `fd9224d`, mesher pin `e4933f3`, Python 3.13.1.
**Windows column: PENDING** — run per README on the Windows box and append here.

## 1. Server-side preview eval (mesher canonical viewport API, N=40 warm)

| Family | LOD | Grid | Cold process¹ | Warm p50 | Encode p50 | Frame bytes |
|---|---|---:|---:|---:|---:|---:|
| OSSE | coarse | 20×4 | ~134 ms | **0.94 ms** | 0.07 ms | 3.4 KB |
| OSSE | fine | 100×48 | ~176 ms | **43.9 ms** | 2.4 ms | 174 KB |
| R-OSSE | coarse | 8×4 | ~130 ms | **0.49 ms** | 0.07 ms | 2.9 KB |
| R-OSSE | fine | 96×48 | ~172 ms | **33.2 ms** | 4.8 ms | 335 KB |
| ICW (flat_baffle) | coarse | 8×4 | ~428 ms | **0.58 ms** | 0.04 ms | 1.5 KB |
| ICW (flat_baffle) | fine | 96×48 | ~425 ms | **4.5 ms** | 2.4 ms | 167 KB |
| FREEFORM | coarse | 20×5 | ~427 ms | **2.8 ms** | 0.07 ms | 4.1 KB |
| FREEFORM | fine | 96×49 | ~423 ms | **13.8 ms** | 2.4 ms | 171 KB |

¹ Fresh-subprocess: interpreter + imports + first call. Killed by server startup pre-warm (measured 414 ms total for all four families at boot).

### The hard case the benchmark alone would have hidden

**ICW `termination="rollback"`** (the ~1 s homotopy documented in v1's `routes_mesh.py`):

- Same-params repeat: first call 1268 ms, then **4.5 ms warm** — the mesher caches the solved profile.
- **Realistic drag (param changes every call): p50 ≈ 987 ms per tick, coarse LOD does not help (p50 ≈ 1015 ms)** — the cost is the homotopy solve, not tessellation.

Consequence (plan §4.1 already anticipates this): ICW-rollback drags run at ~1 Hz with latest-wins coalescing. UX must show pending/stale honestly. Candidate later fix (Phase 5, mesher-side): warm-start the homotopy from the previous drag tick's solution. Payload preserved as `payloads/icw-rollback.json`.

## 2. Browser end-to-end (WS + binary frames + three.js, 10 s sweeps @ 30 Hz)

| Sweep | Painted/requested | fps | Server eval p50 | Decode p50 | Upload+draw p50 | **End-to-end p50 / p95** |
|---|---|---:|---:|---:|---:|---:|
| OSSE coarse | 303/303, 0 dropped | 30.3 | 2.1 ms | 0.1 ms | 10.4 ms | **14.0 / 20.6 ms** |
| OSSE fine (continuous drag) | 185/303, 118 dropped² | 18.3 | 42.9 ms | 0.1 ms | 8.3 ms | **102.9 / 123.1 ms** |

² Latest-wins coalescing working as designed: queue stays bounded, latency stays ~1 frame behind the newest input, no backlog explosion.

**Budget verdicts (plan §4.6):**
- Drag → coarse preview ≤ 80 ms: **PASS with 4× headroom** (p95 20.6 ms).
- Idle → fine ≤ 250 ms: **PASS** (single fine request ≈ 50–60 ms end-to-end; even continuous fine-dragging holds ~110 ms).
- Binary decode is free (0.1 ms) — the codec approach is validated.

## 3. ECharts with real solve data (largest real result: 10.6 MB, 48 freqs, 3 directivity axes)

- FR overlay (20 series, log-x), directivity heatmap (angle × frequency, dB colormap), polar section with frequency slider: all render correctly from `/api/results/real`.
- Scripted 5 s hover/zoom/slider interaction: 301 frames, **frame p50 16.7 ms / p95 17.5 ms / max 17.7 ms = locked 60 fps, zero jank**.
- Caveat (recorded honestly): the persisted result has only 48 frequencies; denser sonograms untested until real v2 solves exist. **ECharts: VALIDATED for G0.**

## 4. Frozen v1 oracle

`oracle/v1-manifest.json`: WG `fd9224d` (main) + module pins (mesher `e4933f38`, metal `c89086ea`, bempp `c6f40771`, plots `ea123b05`) + venv versions (numpy 2.4.6, scipy 1.17.1, gmsh 4.15.2) + `oracle/v1-dirty-files.txt` (7 files from the parallel Windows session's backend-startup work, provenance recorded).

## 5. Implementation findings worth carrying into Phase 2

1. **three.js buffer-reuse trap (bug found & fixed in review):** when vertex count changes (LOD switch), a stale `normal` attribute survives `computeVertexNormals()` and the mesh silently stops rendering. Fix: `deleteAttribute('normal')` whenever the position buffer is replaced. The v2 viewport must size-check *every* attribute on rebuild.
2. **Demand-rendering vs capture/testability:** rendering only on frame arrival makes the canvas unverifiable at rest (`preserveDrawingBuffer:false`). v2 should use demand-rendering with explicit invalidation, plus a test hook that forces a frame.
3. Latest-wins one-pending-slot coalescing (server) + seq-tagged timing (client) worked exactly as specified — carry the pattern into the real WS protocol (§4.3).
4. Server startup pre-warm (414 ms) fully hides cold-start cliffs (which are 130–430 ms per family) — make it a permanent server feature.
5. The only persisted FREEFORM job uses obsolete `corner_ratio` fields and fails today's viewport API — real-library migration (§6.1) must classify such designs, exactly as review R2-P0.4 predicted.

## Running it

See README.md. Server: `wg-spike` entry in workspace `.claude/launch.json` (port 3199) or manually per README. Windows: follow README's Windows section, then append a Windows column to §1–§2 tables.
