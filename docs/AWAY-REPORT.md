# Away-run final report — 2026-08-03 → 2026-08-04

You asked for: all remaining phases done; normals orientation fixed; **every** ATH parameter and WG input visible in v2; a full browser-mode verification; then a Luna-max small-chunk review fleet with all bugs fixed, followed by a Sol review with its findings fixed too. **All of it is done.** Nothing was pushed to any remote.

## The short version

**WG v2 is feature-complete against the plan, twice-reviewed, remediated, and live-verified.** 29 commits in this repo plus the mesher's `preview-api` branch and four workspace-improvement repos/commits. Final suites: **669 server / 145 frontend / 43 shared-codec / 618 mesher — all green**, plus a passing **live Metal solve** through the production pipeline and a browser re-smoke on the final build (LIVE preview, reinstated test hook, unified v2.4.1, Solve armed with AUTO→metal).

## What was built (phase completion)

- Real engines: metal / bempp / CircSym / infinite-baffle adapters, gmsh worker, result mapping per contract, capability detection with AUTO resolution — a real GPU Metal solve runs from the Solve button.
- Full jobs pipeline: ported v1 runtime (FIFO, recovery, cancellation), snapshot+cursor WS events, ratings/rerun/sort/filter/auto-export, atomic solve records.
- Complete results UX: all 11 v1 smoothing modes (golden-tested), 10 chart types, directivity maps, polars, impedance, balloon + forward-beam cards, compare overlays, resolved-polar-grid transparency.
- The full parameter surface: **all 110 ATH/WG keys** with v1 semantics, ATH **expression entry** on all 43 expression-capable fields (round-tripping to `.cfg` text), editable FREEFORM point/station tables + paste import + convert-on-switch, solve+polar option UIs.
- Files & exports: `.cfg`/`.txt` save (legacy `.mwg` opens, incl. migrations for real v1 artifacts like `= undefined`/`NaN` lines), STEP/STL/profiles per the mined contracts, 11 export formats + auto-export, MSH import, workspace endpoints, 10 chart themes via hornlab-plots.
- Viewer: orientation-contracted analytic normals (your dark-render suspicion was right — `horn.inner` faced the wall; fixed with per-triangle validation), error-bounded adaptive tessellation (~3 mm → 0.002–0.075 mm), FrontSide hero look, ortho/perspective, viewer preferences, honest fidelity metadata.

## The review protocol (your spec, executed)

1. **Luna fleet:** 20 chunks at xhigh → `docs/LUNA-TRIAGE.md` → 4 parallel fixers → **158 regression tests**. Headline catches: quadrants bitmask→v1-normalization silently solving a **quarter domain**; an instance-lock race; exports ignoring `scale` entirely.
2. **v1-inputs audit (sol xhigh):** verdict FAIL with a 179-row gap table (`docs/V1-INPUTS-AUDIT.md`) → the four-batch remediation wave closed all 60 missing rows and the 43 expression gaps.
3. **Browser walkthrough:** verified the app end-to-end live and caught two ship-blockers no suite saw — a degenerate seed morph collapsing all geometry, and an OrbitControls key-events crash killing the canvas in real browsers only.
4. **Sol final review:** *no P0*; 5 P1 integration seams + 7 P2s (`docs/SOL-FINAL-REVIEW.md`) — **all 12 fixed** (`a8afc2c`): WS gap recovery via resume, an app-lifetime JobsCoordinator (closing the Jobs panel no longer kills global job machinery), atomic solve records, expression-spelling preservation, honest fidelity aggregation, shared engine registry, undo epochs, retryable exports, one version source.

## Workspace improvements (also done while away)

Constellation lock (`hornlab-constellation`, already catching real drift) · nightly qualification runs (launchd, 03:30, reports under `runs/`) · legacy `parametric-geometry` retired with 1.6e-15 parity on the one live consumer · both bianco-era fusion bridges removed (your call) — all committed in their repos.

## Run it

```
cd waveguide-generator-v2 && WG2_ENABLE_DRYRUN=1 "../Waveguide Generator/.venv/bin/python" launch/serve.py --port 3100
```

(or the `wg2-server` entry in `.claude/launch.json`; the dev frontend is `wg2-frontend` on 3101).

## Decisions that remain yours

1. Push anything to remotes (nothing has been pushed).
2. Merge the mesher `preview-api` branch to main + re-pin consumers (the constellation nightly flags this drift on purpose).
3. GitHub remote for v2 CI (or stay local until cutover).
4. Windows: everything still deferred; the spike README has the commands when ready.
5. The BEMPP phase-metadata chip runs in your separate session.
6. G2-gate call: dark stays default; the parchment light theme is one click away.
