# Module Contracts

This folder holds the stable per-module contracts for the active runtime.

Each module document specifies **what the module owns, its responsibilities, runtime invariants, and regression test coverage**.

## Module Documents

- **`geometry.md`** — Geometry artifacts, canonical simulation payload, surface-tag rules, mesh topology invariants
- **`simulation.md`** — Simulation job submission, result handling, symmetry decisions, task history and exports
- **`export.md`** — STL/CSV/config file exports, OCC mesh orchestration, result bundle coordination
- **`backend.md`** — FastAPI API, routes, services, OCC builder, BEM solver, dependency matrix

## Related Documentation

- `docs/PROJECT_DOCUMENTATION.md` — current implementation map, flows, and entry points (use this as the main reference)
- `docs/architecture.md` — system-level architecture, layer boundaries, and durable design decisions
- `AGENTS.md` — multi-agent coding guardrails and contract-critical code locations
