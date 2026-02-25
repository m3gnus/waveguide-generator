# Waveguide Generator

## What This Is

Waveguide Generator is a browser-based acoustic waveguide design tool with a FastAPI backend for meshing and BEM simulation. It lets users parameterize horn geometry, inspect meshes in a Three.js viewport, run backend simulations, and export engineering artifacts. The current milestone focuses on improving usability and operations via a unified Settings experience and folder-centric simulation management.

## Core Value

A user can reliably run, manage, and reuse waveguide simulations with clear controls and traceable outputs.

## Requirements

### Validated

- ✓ User can parametrize OSSE/R-OSSE waveguide geometry and render it interactively in-browser — existing
- ✓ User can generate canonical simulation payloads with enforced surface-tag contract (`1/2/3/4`) — existing
- ✓ User can submit async backend BEM simulations and retrieve status/results from API jobs — existing
- ✓ User can run OCC-backed mesh build flow via `/api/mesh/build` and legacy `.geo -> .msh` flow via `/api/mesh/generate-msh` — existing
- ✓ User can export key artifacts (STL, MSH, config/profile outputs) from the frontend — existing

### Active

- [ ] Replace the update-check entry behavior with a Settings modal that includes Viewer Controls, Simulation Basic, Simulation Advanced, and System sections.
- [ ] Implement settings persistence with recommended defaults and section/global reset actions.
- [ ] Wire Simulation Basic settings into `/api/solve` payloads using current backend contract fields.
- [ ] Implement folder-centric simulation task management with task subfolders, task manifests, and folder index.
- [ ] Add settings-driven auto-export bundles and task-level full-bundle export behavior.
- [ ] Add completed-task folder loading mode, rating persistence (1-5 stars), and sorting/filtering controls.
- [ ] Add graceful fallback behavior for unsupported folder APIs/permission failures with partial-failure export reporting.

### Out of Scope

- FUTURE_ADDITIONS tracks not selected for this milestone (pre-submit tag diagnostics, symmetry benchmark harness/policy visibility, mesh-control docs clarity, no-gmsh regression lane) — deferred by scope decision
- Mandatory new backend API/schema changes for simulation management v1 — explicitly deferred in plan
- Cross-device rating synchronization and migration tooling for old folder structures — deferred to later milestones
- Expert assembly controls (FMM/quadrature/assembler tuning UI) — deferred behind later advanced phases

## Context

- The repository is brownfield with established frontend (`src/`) and backend (`server/`) stacks and a strict simulation payload contract.
- Canonical architecture and runtime behavior are documented in `docs/PROJECT_DOCUMENTATION.md`.
- Current planning inputs for this milestone come from:
  - `docs/BEM_SETTINGS_MENU_PLAN.md`
  - `docs/SIMULATION_MANAGEMENT_PLAN.md`
  - `docs/FUTURE_ADDITIONS.md`
- A codebase map is available in `.planning/codebase/` and should be used as supporting context for planning decisions.

## Constraints

- **Contract compatibility**: Preserve canonical surface-tag invariants and `/api/solve` request/validation behavior — prevents solver regressions.
- **Runtime stability**: Keep current fallback/error semantics for unsupported device/runtime/export conditions — existing users rely on current behavior.
- **Brownfield continuity**: Integrate new settings and task management without breaking existing simulation/job workflows — reduces migration risk.
- **Test discipline**: Run targeted + full JS/server suites before merge for contract-critical areas — required by repository guardrails.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Treat Settings + Simulation Management as one milestone scope | Both plans are tightly connected through settings persistence and simulation UX operations | — Pending |
| Use docs as initialization source of truth (`BEM_SETTINGS_MENU_PLAN`, `SIMULATION_MANAGEMENT_PLAN`, `FUTURE_ADDITIONS`) | User explicitly provided docs-driven direction and brownfield context is already mapped | — Pending |
| Defer non-selected FUTURE_ADDITIONS items to later milestones | Maintain focused scope and reduce cross-cutting risk during this milestone | — Pending |

---
*Last updated: 2026-02-25 after project initialization*
