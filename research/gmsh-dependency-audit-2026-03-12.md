# Gmsh Dependency Audit

Date: March 12, 2026

## Question

Should the repository keep Gmsh as a long-term dependency in the active runtime?

## Short Decision

Yes, keep Gmsh for now.

Removing it today would break shipped runtime contracts that still depend on the Python Gmsh API for OCC-authored mesh generation, persisted `.msh` solve artifacts, and optional solver-side mesh refinement.

## Audit Scope

Code and maintained docs reviewed:

- `server/solver/deps.py`
- `server/solver/waveguide_builder.py`
- `server/solver/mesh.py`
- `server/api/routes_mesh.py`
- `server/api/routes_simulation.py`
- `server/services/simulation_runner.py`
- `src/modules/export/index.js`
- `src/modules/export/useCases.js`
- `README.md`
- `server/README.md`
- `docs/PROJECT_DOCUMENTATION.md`
- `docs/modules/export.md`
- `docs/modules/backend.md`

## Current Runtime Touchpoints

1. OCC mesh export depends on Gmsh directly.
   - `POST /api/mesh/build` is the only supported runtime path for authored `.msh` export.
   - `server/api/routes_mesh.py` rejects requests when the Gmsh OCC runtime is unavailable.
   - `server/solver/waveguide_builder.py` is the live OCC builder and mesher.

2. OCC-adaptive solve meshing still depends on the same builder.
   - `server/services/simulation_runner.py` uses the OCC path to create solve meshes and persists the generated `.msh` artifact into job state.
   - Frontend completed-job mesh download still depends on that artifact existing.

3. The solver still has an optional Gmsh refinement lane.
   - `server/solver/mesh.py` supports `use_gmsh=True` refinement for canonical meshes and explicitly errors when the runtime is missing.

4. Dependency gating and setup still treat Gmsh as first-class runtime truth.
   - `server/solver/deps.py` lists `gmsh_python` as required for `/api/mesh/build`.
   - `README.md` and `server/README.md` both describe Gmsh installation as mandatory for OCC meshing.

## What Has Already Been Removed

- The legacy frontend `.msh` export helper is gone.
- The app-facing export surface no longer offers a frontend-authored `.msh` path.
- The active export/runtime contract is already narrowed to backend-authored Gmsh output.

This means the remaining dependency is not accidental legacy surface area. It is part of the active backend runtime.

## Alternatives Checked

1. JS-side export helpers
   - Current JS export helpers cover STL, profile CSV, and MWG config only.
   - They do not provide parity for authored `.msh` output or OCC surface-group generation.

2. Canonical frontend mesh payload
   - The JS canonical payload is valid for `/api/solve`, but it is not a replacement for `/api/mesh/build`.
   - It does not preserve the exported `.msh` artifact contract used by task history and mesh download.

3. Optional solver-only future
   - A no-Gmsh `/api/solve` lane exists for canonical payloads.
   - That is useful, but it does not eliminate the separate OCC export and artifact requirements.

## Decision

Keep Gmsh as an active dependency for now.

The dependency should be revisited only after all of the following are true:

- `/api/mesh/build` is no longer needed, or it has a parity replacement for `.msh` output.
- Completed-job mesh artifact download no longer depends on persisted Gmsh-authored `.msh` files.
- The optional `use_gmsh=True` solver refinement lane is either removed or replaced.
- The maintained docs and tests are updated to reflect the new contract in one change.

## Recommended Follow-up

- Do not schedule Gmsh removal as cleanup work.
- Treat Gmsh reduction/removal as a downstream architecture project that starts with export-artifact parity and task-history requirements, not with dependency pruning.
