# Phase 2 polish, Batch S — viewport look + shell fit-and-finish

Close the recorded polish debts now that batch N guarantees orientation. Design reference remains `../wg-v2-ui-sketches/opus-sketch.html` — the goal is the sketch's hero look on the REAL renderer.

**Path discipline: create/modify ONLY `frontend/src/viewport/**`, `frontend/src/shell/Workspace.tsx`, `frontend/src/shell/StatusBar.tsx` (if present), theme/token CSS under `frontend/src/styles/**`, and viewport-related tests. Do NOT touch TopBar.tsx (batch R owns its file-menu wiring), src/design, src/jobs, src/results, src/api (except reading types).**

## Deliverables

1. **FrontSide switch:** the mesher now ships a per-surface orientation contract (`orientation`, `windingChecked` metadata; normals validated against winding — see mesher commit 03ab3bd). Switch smooth/flat materials from DoubleSide to FrontSide; keep DoubleSide ONLY for x-ray (interior visibility is the point there). Verify no surface vanishes in any display mode across OSSE/R-OSSE/ICW/FREEFORM (drive the family dropdown via a story-like test page or document the manual matrix for the overseer).
2. **The sketch's hero look:** clay material toward the sketch's light blue-grey horn (tokens exist: use the accent-tinted scheme from opus-sketch's viewport, not pure white), enclosure slightly darker/warmer per sketch, hemisphere + key + rim lighting tuned so the throat cavity reads with depth on BOTH themes (light-mode scene background #f2ede4 warm per tokens), grid/floor shadow suggestion optional. Judge against the sketch screenshot-side-by-side; state your deltas.
3. **Client-latency readout bug:** the viewport chrome shows absurd client ms (measured 49807 ms after reconnects) — `frameStartedAt` handling across reconnect/re-render is broken; fix the measurement to time request→paint per frame (ignore frames whose request timestamp predates the socket epoch), cap display at 999 ms with a '>' indicator.
4. **Dockview default layout pinning (known issue from batch G):** seed the default layout via `api.fromJSON` with explicit sizes (Parameters 300, Jobs 320, Results 340 high, Viewport flex) instead of addPanel+setSize; keep persistence + reset behavior; verify at 1440×900 and 1280×720 (no horizontal page scroll).
5. **Empty/edge states:** viewport 'waiting' overlay styled per sketch (it's a plain box today); WS-disconnected banner state (server killed → reconnect badge, not a dead panel).
6. Tests: material-mode matrix (FrontSide/DoubleSide per mode), latency formatter (reconnect case), layout JSON seed (shape snapshot), plus keep the whole vitest suite green; `npm run build` green.

## Rules
- No npm installs. Keep the demand-rendering discipline (no idle GPU work) — lighting changes must not introduce per-frame animation.
- Final message: before/after notes per item, the manual verification matrix for the overseer (modes × families × themes), test counts.
