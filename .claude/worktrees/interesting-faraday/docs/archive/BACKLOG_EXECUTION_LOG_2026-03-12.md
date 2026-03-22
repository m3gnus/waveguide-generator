# Backlog Execution Log

Archived: March 12, 2026

This document preserves the completed execution history that previously lived in `docs/backlog.md` before the active backlog was cleaned up into a short maintained file.

## Completed Priorities

### P0 Upstream Runtime And Contract Seams

- Make the Stop action cancel backend work cooperatively instead of only updating UI state.
- Wire Simulation Basic settings all the way into `/api/solve` payloads and runtime availability messaging.
- Add an explicit no-Gmsh regression lane for `/api/solve`.
- Introduce a public job-runtime service surface and stop letting `server/api/routes_simulation.py` mutate job state internals directly.
- Add pre-submit canonical tag diagnostics to the simulation UI.

### P1 Simulation UX That Depends On Runtime Truth

- Show simulation mesh vertex/triangle counts in the stats widget once the `.msh` or simulation mesh is built.
- Clarify solve-mesh versus export-mesh controls in the UI and docs.
- Move the formula affordance from the section header to the relevant input fields and audit which fields should support formulas.
- Gate Simulation Advanced or expert controls by backend capability and finish the remaining hardening and docs pass around them.

### P1.5 Viewport Geometry UX

- Align the viewport throat-disc appearance between OSSE and R-OSSE so R-OSSE no longer looks visually smoothed over at the source cap.

### P2 Folder-Backed Completion And Export Flow

- Build selected-format bundle export and idempotent auto-export on simulation completion.
- Finish completed-task source modes so folder-backed tasks and backend jobs have clear, non-mixed browsing behavior.
- Add task ratings plus stable sorting and filtering controls.

### P3 Docs, Hardening, And Cleanup

- Tighten frontend module boundaries and expand the boundary-test lane so `src/modules/*` remains an app-facing API layer instead of a second UI or runtime layer.
- Split `src/modules/simulation/useCases.js` into smaller frontend services with single responsibilities.
- Remove ambient frontend globals such as `window.app` and `window.__waveguideApp` in favor of explicit composition.
- Create a smaller durable architecture doc and split stable per-module contracts out of large narrative docs.
- Add a maintained-doc parity audit so runtime and device-mode changes cannot leave `docs/PROJECT_DOCUMENTATION.md` and `server/README.md` describing removed fallback behavior.
- Run a structured dead-code audit on `src/` and remove utility paths with no runtime entry.
- Retire the legacy frontend `.msh` export surface once tests or tooling no longer need it.

### P4 Research And Optional Engineering Tracks

- Add a symmetry benchmark harness and expose symmetry-policy decisions more clearly.
- Decide whether the Gmsh export stack should remain a long-term dependency.
- Defer internal decomposition of `solve_optimized()` and `waveguide_builder.py` until feature work makes it worthwhile.
- Defer internal decomposition of `server/services/job_runtime.py` until lifecycle requirements expand materially.

## Archive Notes

- Runtime behavior truth remains code plus tests.
- Active work truth remains `docs/backlog.md`.
- Historical planning inputs remain under `docs/archive/`.
- The detailed commit history for these completed slices remains available through `git log`.
