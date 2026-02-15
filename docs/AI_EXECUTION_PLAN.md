# AI Execution Plan (Waveguide Generator)

## Purpose
This plan is designed so a strong model can set direction once, and smaller/cheaper agents can execute safely in small steps without breaking core geometry, export, or solver behavior.

## Ground Truth (Validated)

1. The project currently has two separate mesh pipelines and both are intentional:
- Viewport/simulation payload mesh: `src/geometry/engine/*` + `src/geometry/pipeline.js`
- Export `.msh` mesh: `server/solver/waveguide_builder.py` via `/api/mesh/build`

2. `.geo` is no longer the primary runtime artifact for the OCC path.
- `/api/mesh/build` currently returns `msh` (+ optional `stl`), not `geo`.
- Legacy `.geo -> .msh` path still exists as fallback (`/api/mesh/generate-msh`).

3. ABEC export currently writes:
- `Project.abec`, `solving.txt`, `observation.txt`, `<name>.msh`, `Results/coords.txt`, `Results/static.txt`
- It does not currently include `bem_mesh.geo`.

4. Critical geometry/tagging mismatch exists:
- `src/geometry/engine/mesh/enclosure.js` stores enclosure group range in index-buffer units (`* 3`), while tag application expects triangle units.

5. Internal BEM stack currently targets `bempp-cl` (fallback to legacy `bempp_api`) in `server/solver/deps.py`.

## Documentation Decisions

### Decision A: `MSH_GEO_GENERATION.md`
- Do not delete immediately.
- Rewrite and narrow scope to: "OCC `.msh` generation + legacy `.geo` fallback behavior".
- Remove statements that imply `.geo` is always generated/returned by `/api/mesh/build`.

### Decision B: `PROJECT_DOCUMENTATION.md`
- Keep as the primary architecture document.
- Update all mesh/export/API sections to reflect current runtime truth.
- Split clearly into:
  - Viewport mesh pipeline
  - Export `.msh` pipeline
  - Legacy `.geo` fallback path

### Decision C: `GEOMETRY_AND_MESHING_ANALYSIS.md`
- Keep the validated parts.
- Move speculative ideas into a separate "future work" section or separate backlog doc.
- Ensure each recommendation links to a failing test or measurable metric.

## Immediate Fixes (Do Now)

These are high-leverage and currently blocking trust in geometry and exports.

### P0-1. Fix enclosure group range units
- File: `src/geometry/engine/mesh/enclosure.js`
- Change `groupInfo.enclosure` to triangle index range (remove `* 3`).
- Acceptance:
  - `tests/mesh-payload.test.js` interface tag assertions pass.
  - `tests/geometry-artifacts.test.js` secondary/interface tag assertions pass.

### P0-2. Add explicit interface group range
- File: `src/geometry/engine/mesh/enclosure.js`
- Record `groupInfo.interface` range where interface surface triangles are actually emitted.
- Acceptance:
  - `SURFACE_TAGS.INTERFACE` appears when `interfaceOffset > 0`.
  - No interface tags when `interfaceOffset == 0`.

### P0-3. Stabilize source tagging for OSSE/R-OSSE
- Files: `src/geometry/engine/buildWaveguideMesh.js`, `src/geometry/tags.js`
- Remove fragile fallback behavior where first N triangles are tagged as source when no source group exists.
- Ensure source surface is explicit and physically correct in all simulation payloads.
- Acceptance:
  - `tests/gmsh-geo-builder.test.js` source physical group expectations pass.
  - Payload always has non-empty source tags with deterministic location.

### P0-4. Repair enclosure regression metrics path
- File: `tests/enclosure-regression.test.js` and any dependent geometry metric helper
- Current failures show undefined values in enclosure analysis (`toFixed` on undefined, `NaN` maxY).
- Ensure metric extraction gracefully handles missing group/vertex sets and fails with useful assertions.
- Acceptance:
  - All `tests/enclosure-regression.test.js` cases pass for quadrants `1234`, `1`, `12`, `14`.

### P1-1. Update stale API/docs comments in code
- Files: `server/app.py`, `server/solver/waveguide_builder.py`, `src/app/exports.js`
- Remove misleading "returns .geo + .msh" comments for `/api/mesh/build`.
- Acceptance:
  - No in-code comments contradict actual return payload.

### P1-2. Clarify frontend solver status text
- File: `src/solver/index.js`
- Header currently says "MOCK SOLVER" while backend calls are implemented.
- Update wording to match current behavior (real backend path + optional mock utility).
- Acceptance:
  - New developers do not misinterpret runtime solver status.

## Staged Backlog (Document and Execute Iteratively)

## Phase 2: ABEC Export Parity

### P2-1. Define parity contract against ATH output
- Baseline reference folder:
  - `_references/testconfigs/260112aolo1/ABEC_FreeStanding`
- Compare file set, naming, config semantics, and value ranges.

### P2-2. Add ABEC export validator script
- Create script to compare generated ZIP contents against expected structure:
  - required files
  - required ABEC keys
  - mesh physical groups
  - observation block structure

### P2-3. Decide on `bem_mesh.geo` policy
- Option A: Include generated `.geo` whenever available for ATH parity.
- Option B: Keep it optional but document exactly when missing and why.
- Recommendation: include it for parity and debugging unless size/security constraints block it.

### P2-4. Regression tests for ABEC bundles
- Golden test fixtures for both:
  - `ABEC_FreeStanding`
  - `ABEC_InfiniteBaffle`
- Parse and assert key fields in `Project.abec`, `solving.txt`, `observation.txt`.

## Phase 3: Internal BEM Solver Hardening

### P3-1. Lock dependency matrix
- Define supported Python stack explicitly (Python, gmsh, bempp-cl versions).
- Remove ambiguity around package names/import paths.

### P3-2. Create solver backend interface boundary
- Formalize one adapter API:
  - `solve(mesh, freq_config, sim_config) -> normalized result`
- Implement adapter for current `bempp-cl` path.
- Keep room for future axisymmetric adapter.

### P3-3. Reintroduce mesh/frequency safety policy as configurable mode
- File: `server/solver/solve_optimized.py`
- Current validation is hard-disabled.
- Add policy modes:
  - `strict` (block invalid setups)
  - `warn` (default)
  - `off` (expert)

### P3-4. Symmetry benchmark harness
- Quantify speed and error for full vs half vs quarter domains.
- Store benchmark cases and acceptance thresholds in repo.

## Phase 4: Axisymmetric Fast Path (Research + Spike)

### P4-1. Confirm scope
- Axisymmetric solver should activate only when geometry and excitation satisfy rotational symmetry constraints.

### P4-2. Evaluate candidates
- `bempp-cl`: keep for general 3D (no native axisymmetric formulation).
- AcousticBEM/Kirkup-style axisymmetric approach: evaluate as separate solver mode.

### P4-3. Implement proof-of-concept adapter
- Minimal API-compatible axisymmetric adapter for one canonical waveguide case.
- Compare speed/accuracy against current 3D `bempp-cl` baseline.

### P4-4. Go/No-Go decision
- Promote only if accuracy and runtime targets are met.

## Multi-Agent Structure (Recommended)

Use `AGENTS.md` (not `agent.md`) because the agent tooling already resolves instruction files by this name.

### Root file
- Add `/AGENTS.md` with:
  - system overview
  - pipeline boundaries
  - source-of-truth docs
  - coding/testing guardrails
  - "do not change without parity tests" list

### Module-level files
- Add short local `AGENTS.md` files in:
  - `/src/geometry/AGENTS.md`
  - `/src/export/AGENTS.md`
  - `/server/solver/AGENTS.md`
  - `/docs/AGENTS.md`
- Keep each under ~120 lines with:
  - responsibilities
  - invariants
  - required tests before merge
  - known pitfalls

### Why this helps
- Reduces context-window waste for smaller agents.
- Prevents random cross-module edits.
- Makes handoff deterministic.

## Execution Order (Token-Efficient)

1. Fix P0 geometry/tagging defects and red tests.
2. Immediately update docs/comments that currently contradict runtime.
3. Freeze ABEC parity contract and write validators before making large export changes.
4. Stabilize and benchmark current 3D solver stack.
5. Only then run axisymmetric solver spike behind feature flag.

## Definition of Done

- JS + server tests pass.
- No docs claim `.geo` is returned by `/api/mesh/build` unless code does so.
- ABEC bundle validated against ATH reference checklist.
- Solver backend has explicit version support table and benchmark results committed.
- Agent guidance files exist at root + critical modules.

## External Research Links

- BEMPP docs: https://bempp.com/documentation/index.html
- BEM++ discourse (axisymmetric not currently available): https://bempp.discourse.group/t/is-bempp-tutorial-for-acoustics-applicable-for-axisymmetric-problems/285
- BEMPP discourse (package naming context): https://bempp.discourse.group/t/announcing-bempp-v0-4-2-and-legacy-version-0-3-4/335
- Kirkup / axisymmetric BEM background: https://www.boundary-element-method.com/acoustics/manual/chap1/sect1_3.htm
