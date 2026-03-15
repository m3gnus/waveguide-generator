# Backlog

Last updated: March 15, 2026

This file is the active source of truth for unfinished product and engineering work.
Detailed completion history from the March 11-12, 2026 cleanup phase lives in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Working Rules

### Upstream-downstream integrity

Modules must not compensate for defects that belong upstream. Each module should receive correct input and fail visibly if it does not. When downstream code contains a workaround for an upstream defect, the fix belongs in the upstream module, not in the workaround.

### Docs and audit discipline

Keep durable decisions in `docs/architecture.md`, active work in this file, and per-module contracts in `docs/modules/`. Put generated audits, comparisons, and experiment output under `research/`.

## Current Baseline

Status as of March 15, 2026:
- The architecture cleanup plan is complete.
- The enclosure BEM simulation bug (self-intersecting geometry when `enc_depth < horn_length`) is fixed — depth clamping applied in `_build_enclosure_box`.
- Settings modal, viewer settings, folder workspace, parameter naming/hover-help, geometry diagnostics, advanced solver controls, and job feed cleanup are all shipped.
- Active runtime docs are `README.md`, `docs/PROJECT_DOCUMENTATION.md`, `tests/TESTING.md`, `server/README.md`, and `AGENTS.md`.

## Active Backlog

### P1. MSH File Import — Viewport Display and Simulation Workflow

Add the ability to import an external `.msh` file and display it in the viewport, replacing the parametric model. This enables users to inspect meshes generated outside the app or previously exported simulation meshes.

Backend infrastructure exists: `server/solver/mesh.py` has `load_msh_for_bem()` which parses `.msh` files via meshio and extracts vertices, triangles, and physical tags. The frontend viewport (`src/app/scene.js`) already accepts raw `{vertices, indices}` via `applyMeshToScene()`. What's missing is the glue: a frontend MSH parser, a UI entry point, and an "imported mesh mode" state.

Implementation plan:
- [x] Write a frontend Gmsh 2.2 MSH parser (`src/import/mshParser.js`) that extracts vertices (Float32Array) and triangle indices (Uint32Array) from the text format. The format is well-documented in `tests/helpers/legacyMsh.js` (which writes it). The parser just reverses that: read `$Nodes` section for XYZ coordinates, read `$Elements` section for triangle connectivity (element type 2), convert 1-based node IDs to 0-based indices.
- [x] Add an "Import Mesh" button in the Project section of the actions panel (`index.html`), next to the existing "Load Config" button. Wire a hidden file input accepting `.msh` files, similar to the existing config upload pattern in `src/app/events.js`.
- [x] Add an "imported mesh mode" flag to `GlobalState` (`src/state.js`). When active, the viewport renders the imported mesh instead of the parametric model. The parametric controls remain visible but are visually dimmed or annotated to indicate they are not driving the display.
- [x] Feed imported vertices/indices into the existing `applyMeshToScene()` in `src/app/scene.js`. The Three.js rendering path already handles arbitrary `BufferGeometry` — no changes needed to the renderer itself.
- [x] Add a "Return to Parametric" button or indicator so users can exit imported-mesh mode and return to the normal parametric workflow.
- [ ] Optionally extract physical group tags from the imported MSH and color-code surface groups in the viewport (wall=grey, source=green, enclosure=blue) to help users verify mesh topology visually.
- [ ] Consider whether imported meshes should be submittable for BEM simulation directly. The backend `load_msh_for_bem()` already validates physical tags and converts to bempp grid format. This would allow users to import externally-generated meshes and run simulations on them without going through the parametric → OCC → Gmsh pipeline.

UX flow:
1. User clicks "Import Mesh" → file picker opens filtered to `.msh`
2. File is read as text in the browser (no server round-trip for display)
3. Parsed vertices/indices replace the parametric mesh in the viewport
4. A banner or indicator shows "Displaying imported mesh: filename.msh"
5. User can rotate/zoom the imported mesh with the existing OrbitControls
6. "Return to Parametric" exits the mode and restores the parametric model

Implementation notes:
- `src/import/mshParser.js` (new)
- `src/app/scene.js` (`applyMeshToScene`)
- `src/app/events.js` (file input wiring)
- `src/state.js` (imported mesh mode flag)
- `index.html` (button)
- `server/solver/mesh.py` (`load_msh_for_bem` — existing, for optional simulation path)

### P1. Symmetry Detection Not Working for OCC Meshes

Status: symmetry detection is functionally correct but effectively disabled for all real OCC meshes due to two root causes documented on March 12, 2026:

1. **Wrong symmetry planes**: `server/solver/symmetry.py` checks X=0 / Z=0 planes assuming frontend axis convention (X radial, Y axial, Z radial), but the OCC builder emits [x_radial, y_radial, z_axial]. The second quarter-symmetry plane is evaluated on the axial axis instead of the second radial axis.
2. **Tolerance too tight for unstructured meshes**: `_check_plane_symmetry()` requires one-to-one mirrored vertex matches within a very tight tolerance. Synthetic benchmark meshes pass, but unstructured OCC/Gmsh meshes always fail, so every real simulation falls back to full model.

Impact: no incorrect results (full model is correct, just slower). Quarter-symmetry would give ~4x speedup. Half-symmetry ~2x.

Action plan:
- [ ] Decide whether to disable symmetry entirely (remove dead code path) or fix it properly.
- [ ] If fixing: move symmetry detection upstream of triangle extraction; detect from OCC profile samples rather than raw mesh vertices; use axis-aware plane definitions.
- [ ] A/B test: full-model vs symmetry-reduced simulation on a known-symmetric config — results must match within 0.5 dB across all angles.
- [ ] Only re-enable after A/B test confirms both correctness and measurable speedup.
- [ ] Add committed ATH reference fixtures for reproducible regression testing.

Implementation notes:
- `server/solver/symmetry.py`
- `server/solver/solve_optimized.py` (symmetry policy evaluation)

### P1. Enclosure Mesh Resolution — Edge Over-Refinement

The front and back roundover/chamfer edges of enclosure meshes have much higher resolution than needed. The edges between the sides (stretching front-to-back) have correct resolution, but the transverse edges (e.g., front top-left to front top-right) are over-refined.

Research (March 15, 2026): confirmed the `enclosure_edges` surface group is correctly extracted in `_build_enclosure_box()` and stored in `surface_groups["enclosure_edges"]`, but `_configure_mesh_size()` never assigns an explicit mesh size field to these edges. They fall back to Gmsh's automatic sizing, which inherits from nearby horn surface resolutions (throat_res/mouth_res) rather than using appropriate enclosure-specific resolutions.

Action plan:
- [ ] Add an explicit mesh size field for `enclosure_edges` surfaces in `_configure_mesh_size()`, using the front/back enclosure resolution rather than the finer throat/mouth resolution that leaks from the inner horn field.
- [ ] Verify with a test mesh that edge element counts are proportional to the user's resolution setting.

Implementation notes:
- `server/solver/waveguide_builder.py` (`_configure_mesh_size`, enclosure field section)

### P1. Cross-Platform Installation Hardening and Supported Runtime Matrix

- Replace the current "best effort" setup story with one explicit supported matrix and one verified install lane per supported OS/architecture.
- Publish support tiers instead of treating all Windows/macOS/Linux machines as equivalent:
  - Windows 10/11 x64: supported for the core app and OCC meshing; full simulation requires a verified OpenCL ICD/runtime.
  - macOS 13+ Intel x64: supported for the core app, OCC meshing, and a verified CPU-OpenCL simulation lane.
  - macOS 13+ Apple Silicon: supported for the core app and OCC meshing; full simulation remains a separately validated managed-env lane and must not be documented as parity-complete until it is verified end to end.
  - Linux x86_64: support Ubuntu 22.04/24.04 as the primary full-runtime target; keep other distros best effort unless they are added to CI.
  - Windows ARM64 and Linux ARM64: mark full simulation unsupported until gmsh/bempp/OpenCL parity is proven.
- Narrow the installer targets to exact repo-owned versions instead of open-ended ranges:
  - Node.js: pin one current LTS major for the repo and record it in a version file (`.nvmrc`, Volta, or equivalent).
  - Python: pin one tested minor for installation guidance and environment creation instead of accepting any `3.10 - 3.14` interpreter on PATH.
- Make installation modes explicit:
  - `Core app ready`: preview plus STL/CSV/config exports.
  - `OCC mesh ready`: `/api/mesh/build` available with supported Python + gmsh runtime.
  - `Simulation ready`: `/api/solve` available with supported bempp-cl + OpenCL runtime.
- Do not end installation with a generic success banner unless the achieved readiness level is stated clearly. If bempp/OpenCL is missing, finish as `Core app ready` or `OCC mesh ready`, not "fully installed".
- Add one backend/runtime preflight command that installers and launchers both call. It should verify:
  - selected interpreter path and version
  - active `pip` target / environment root
  - `gmsh` import + version
  - `bempp-cl` import + version
  - `pyopencl.get_platforms()`
  - `/health` dependency matrix alignment once the backend starts
- Fail early on unsupported newer Python as well as too-old Python. The current installers only enforce the lower bound, which still allows an unsupported interpreter to install partially and fail later at runtime.
- Stop reintroducing interpreter drift after setup:
  - launchers should prefer the installer-created environment deterministically
  - do not silently fall back to an arbitrary system `python3` once a managed project/runtime environment exists
  - persist the chosen backend interpreter path so launchers and diagnostics use the same runtime the installer validated
- Replace optimistic fallback wording in launch/start flows with actual runtime truth. Do not imply mock solver support or a working simulation path when dependencies are absent.
- Add install smoke coverage in CI across the supported matrix so dependency regressions are caught before release.
- Consolidate install/operator docs into one canonical installation guide, then keep `README.md`, `server/README.md`, and `docs/PROJECT_DOCUMENTATION.md` in parity with that guide and with `server/solver/deps.py`.

Research notes:
- `server/solver/deps.py` is the runtime truth: Python `>=3.10,<3.15`, gmsh `>=4.11,<5.0`, bempp-cl `>=0.4,<0.5`.
- `install/install.sh` and `install/install.bat` currently only reject Python older than 3.10. They do not reject unsupported newer Python, even though the backend dependency matrix does.
- `scripts/start-all.js` and `server/start.sh` can still fall back to a system `python3`, which can bypass the environment the installer configured.

Implementation notes:
- `install/install.sh`
- `install/install.bat`
- `scripts/start-all.js`
- `server/start.sh`
- `README.md`
- `server/README.md`
- `docs/PROJECT_DOCUMENTATION.md`
- add a shared runtime-check script invoked by both installers and launchers
- add repo-owned toolchain version files / environment spec

### P2. Tessellation Architecture — Geometry vs Mesh Separation

The current architecture should follow a clean separation: a geometry workflow constructs geometric data, then hands it to (a) the viewport tessellation module and (b) the .msh generation module. Neither tessellation module should construct geometry — they only translate and tessellate.

Audit status (March 14, 2026):
- The Python OCC path (`waveguide_builder.py`) combines geometry construction and meshing in one function.
- The JS viewport path (`buildWaveguideMesh.js`) also combines geometry computation with Three.js mesh construction.
- Both paths independently compute the same horn geometry from the same parameters — no shared geometric representation.

Action plan:
- [ ] Document the current geometry → mesh boundaries within each path.
- [ ] Evaluate whether extracting a shared geometry representation (point grids + topology description) consumed by both paths is feasible without a full rewrite.
- [ ] If feasible, design the shared geometry contract and implement incrementally.

### P3. Symmetry Runtime Truth and Operator Control Follow-Through

Depends on P1 Symmetry Detection. Deferred until the symmetry detection root causes are resolved.

- [ ] Add a reproducible diagnostics lane for ATH reference configs, capturing imported params, canonical mesh topology, and resulting `metadata.symmetry_policy` / `metadata.symmetry`.
- [ ] Add regression coverage for reference cases so future geometry or solver changes cannot silently change reduction eligibility.
- [ ] Commit ATH reference fixtures or agreed repo-owned surrogate set for reproducible regression testing.
- [x] Audit the existing `Enable Symmetry` control in the Settings modal and verify visibility, persistence, and payload behavior.
- [x] Surface the requested symmetry setting and the resulting `symmetry_policy` together in user-visible job/result surfaces.
- [x] Update runtime docs to clarify that imported ATH `Mesh.Quadrants` values do not directly trim the canonical simulation payload.

### P4. Maintained Markdown Document Overhaul

- [ ] Audit the maintained Markdown documentation set, then rewrite it for readability and architecture parity without inventing behavior that the code does not ship.
  - Scope the pass to the maintained `.md` docs that describe the live system: `README.md`, `AGENTS.md`, `docs/architecture.md`, `docs/PROJECT_DOCUMENTATION.md`, `docs/modules/*.md`, `tests/TESTING.md`, `server/README.md`, and `docs/archive/README.md`.
  - Verify each document against the actual runtime entry points, layer boundaries, active backend capabilities, supported export/simulation flows, and the current test map before rewriting copy.
  - Improve scannability with clearer section ordering, tighter wording, explicit source-of-truth links, and removal of stale or duplicated explanations that drift from the real architecture.

## Completed / Resolved

### P0. Enclosure BEM Simulation — RESOLVED

Fix applied March 14, 2026: `_build_enclosure_box` now clamps `enc_depth` to at least `horn_length + 1mm` so the back wall is always behind the throat. This prevents the self-intersecting geometry that caused omnidirectional-looking directivity.

Optional follow-up (not blocking):
- A/B test: compare thickened-waveguide vs enclosure simulation on the same horn geometry.
- Consider viewport warning when enc_depth is clamped during simulation.

### P2. Safety Mechanisms in .msh Generation — RESOLVED (Keep)

Audited March 15, 2026. All three safety mechanisms are genuinely needed and do not mask upstream bugs:
- `removeDuplicateNodes()`: handles Gmsh-level mesh deduplication from OCC seam duplicates. Standard Gmsh API usage.
- `_orient_and_validate_canonical_mesh()`: essential final mesh integrity check (vertex welding, degenerate triangle removal, watertightness validation, consistent orientation). Independent of the enclosure depth fix.
- `SurfaceFilling` fallbacks: defensive code for throat disc and rear cap when `addPlaneSurface` fails on malformed wire loops. Rarely triggered but legitimate robustness.

No action needed. These are appropriate safety mechanisms, not upstream workarounds.

### Completed Items (March 11-15, 2026)

The following items are fully shipped:
- P1. Remove Stale Local-Only Jobs From the Backend Feed
- P1. OCC Mesh Diagnostics Must Reflect The Backend Solve Mesh
- P1. Parameter Inventory, Naming, Hover Help, and Ordering
- P1. Settings Panel Completeness and Information Architecture
- P2. Folder Workspace Discoverability and Export Routing
- P2. Geometry Diagnostics Instead of Numeric BEM Tag Diagnostics
- P2. Advanced Solver Controls and BEM Precision Scope
- P3. Simulation Job Feed Source-Badge Cleanup

Detailed completion history in `docs/archive/BACKLOG_EXECUTION_LOG_2026-03-12.md`.

## Deferred Watchpoints

- The Gmsh export stack remains part of the active runtime until solve-mesh and export-artifact parity exists without it.
- Internal decomposition of `server/solver/solve_optimized.py` and `server/solver/waveguide_builder.py` stays deferred unless new feature work makes those files a delivery bottleneck.
- Internal decomposition of `server/services/job_runtime.py` stays deferred unless queueing, persistence, or multi-worker lifecycle requirements expand materially.

Re-open the backlog when:
- a new product or runtime requirement lands
- a deferred watchpoint becomes an active delivery bottleneck
- a regression or documentation drift needs tracked follow-through across multiple slices
