# Phase 3/4, Batch K — jobs panel + results dock, wired to the real server

Wire the shell's Jobs and Results panels to batch J's real endpoints and events. Design reference: `../wg-v2-ui-sketches/opus-sketch.html`. Contracts: `docs/WS-PROTOCOL.md` §2, batch J's REST shapes (`server/jobs/models.py`, `server/jobs/api.py` — read them), plan §4.5 (lazy results) and §4.6 budgets.

**Path discipline (concurrent agents in frontend/): create/modify ONLY `frontend/src/jobs/**`, `frontend/src/results/**`, `frontend/src/api/jobsSocket.ts`, `frontend/src/api/results.ts`, and the two host components `frontend/src/shell/JobsPanel.tsx` + `frontend/src/shell/ResultsPanel.tsx`. Do NOT touch src/design, src/stores, src/viewport, or other shell files. No npm installs (echarts is preinstalled).**

## Deliverables

1. `api/jobsSocket.ts` — `/ws/jobs` client per protocol: hello/epoch, snapshot-then-events with cursor tracking, `resume` on reconnect, gap → refetch snapshot; exposes a store-like subscription (useSyncExternalStore-compatible) of the job list + per-job live progress/stage/log-tail.
2. **Jobs panel** matching the sketch: running card (progress bar, stage message, elapsed, stop button), completed cards (duration, metrics line, ★ rating widget → PATCH metadata, Load design + Rerun buttons → POST), failed card (error hint, Retry, Open log), earlier-today list, clear-failed. Solve button in the top bar becomes ACTIVE when the dry-run engine is available (capabilities): submits the CURRENT design (read the design store via its public hook — import allowed, modification not) with default options; disabled state with reason otherwise.
3. `api/results.ts` — typed fetch of `/api/results/{id}` with an LRU cache (≤15 jobs, plan §4.6) and a compare-selection store (primary + overlays).
4. **Results dock** with real ECharts: SPL/FR panel (multi-job overlay, log-x 200 Hz–20 kHz, legend = job labels), directivity heatmap (angle × frequency from the result's H matrix, dB colormap consistent with the sketch), polar section at a slider-chosen frequency (H/V toggle), impedance panel (Re/Im or magnitude/phase toggle). Compare chips per the sketch (add/remove overlay jobs). Panels degrade gracefully with no results ("run a solve" empty state). Chart theming via the existing CSS tokens (light + dark both correct — read token values at render, re-render on theme change).
5. Tests (vitest): jobsSocket state machine vs mock WS (snapshot/cursor/resume/gap-refetch), results LRU eviction, chart data mappers (result JSON → ECharts series incl. dB conversion + angle grids), rating PATCH optimistic update.
6. `npm run build` + `npx vitest run` green.

## Rules
- ECharts: one shared init helper (canvas renderer, no per-render re-init; dispose on unmount; resize observer).
- Final message: files, test counts, exact overseer live-test script (submit N dry-run solves, what to click, what to expect).
