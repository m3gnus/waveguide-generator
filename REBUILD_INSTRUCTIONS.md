# ATH Horn - Rebuild Instructions (Detailed, Line-Anchored)

This is the authoritative rebuild plan for simplifying the project while preserving real behavior.

Primary objective: reduce complexity and module sprawl without changing validated geometry, export, and simulation outcomes.

---

## 1. Rebuild Scope and Definition of Done

### 1.1 Core functionality that must be preserved

1. OSSE and R-OSSE profile generation, including expression-driven parameters.
2. Real-time 3D model updates from parameter changes.
3. Config import/export compatibility (ATH style + MWG style), including unknown block round-trip.
4. Export stack: CSV, GEO, MSH, ABEC package.
5. Simulation pipeline: frontend submit/poll/results against FastAPI backend.
6. Smoothing and result rendering in simulation UI.
7. State persistence + undo/redo.

### 1.2 What should be simplified

1. Excessive module fragmentation and duplicate adapters.
2. Legacy/dead paths that duplicate current CAD-based export flow.
3. Runtime dependence on heavyweight build steps for normal usage.
4. Inconsistent tag/unit contracts between frontend and backend.

### 1.3 Non-goals

1. New acoustic model families.
2. Feature expansion beyond current schema (except where required for parity/fixes).
3. Visual redesign.

---

## 2. Source-of-Truth Map (Line Anchors)

Use these as authoritative references during rebuild.

### 2.1 Geometry math and mesh generation

1. Expression parser: `src/geometry/expression.js:15` (core parser), `src/geometry/expression.js:148` (debug helper).
2. Shared math + quadrants: `src/geometry/common.js:5`, `src/geometry/common.js:47`.
3. Guiding curves + OSSE/R-OSSE equations:
   - `src/geometry/hornModels.js:9` (guiding curve radius)
   - `src/geometry/hornModels.js:61` (OSSE radius core)
   - `src/geometry/hornModels.js:89` (coverage-angle search)
   - `src/geometry/hornModels.js:176` (calculateOSSE)
   - `src/geometry/hornModels.js:316` (calculateROSSE)
4. Morphing:
   - `src/geometry/morphing.js:8` (rounded rectangle radius)
   - `src/geometry/morphing.js:42` (applyMorphing)
5. Mesh assembly:
   - `src/geometry/meshBuilder.js:21` (throat source)
   - `src/geometry/meshBuilder.js:99` (slice map)
   - `src/geometry/meshBuilder.js:157` (morph targets)
   - `src/geometry/meshBuilder.js:235` (quadrant angle shaping)
   - `src/geometry/meshBuilder.js:360` (buildHornMesh)
6. Enclosure + stitching:
   - `src/geometry/enclosure.js:192` (rounded outline)
   - `src/geometry/enclosure.js:280` (addEnclosureGeometry)
   - `src/geometry/enclosure.js:505` (greedy mouth-to-enclosure stitch)
7. Rear closure alternatives: `src/geometry/rearShape.js:5`.

### 2.2 Parameters, config, state

1. Param preparation + expression coercion + scale wrapping: `src/app/params.js:14`.
2. Full schema (including less-visible fields): `src/config/schema.js:1`.
3. Defaults derivation: `src/config/defaults.js:3`.
4. Config parser + legacy mappings + block handling: `src/config/parser.js:2`.
5. State persistence and history: `src/state.js:5`.
6. Config import path (typing + defaults for ATH imports): `src/app/configImport.js:5`.

### 2.3 Export pipeline

1. MSH/GEO writer:
   - `src/export/msh.js:18` (axis transform)
   - `src/export/msh.js:103` (MSH writer)
   - `src/export/msh.js:196` (full GEO generator)
   - `src/export/msh.js:374` (CAD tagged MSH export)
2. ABEC files: `src/export/abecProject.js:46`, `src/export/abecProject.js:69`, `src/export/abecProject.js:165`.
3. MWG config export: `src/export/mwgConfig.js:7`.
4. Simple CSV/GEO exports: `src/export/profiles.js:7`, `src/export/profiles.js:31`.
5. App-level export orchestration: `src/app/exports.js:17`.
6. File save/folder picker behavior: `src/ui/fileOps.js:1`.

### 2.4 Simulation frontend and backend

1. Simulation controls and polling: `src/ui/simulation/actions.js:1`.
2. Simulation mesh prep (current path issues included): `src/ui/simulation/mesh.js:1`.
3. Solver HTTP client: `src/solver/index.js:70`.
4. Result rendering and smoothing integration:
   - `src/ui/simulation/results.js:10`
   - `src/ui/simulation/charts.js:1`
   - `src/results/smoothing.js:23`
5. Backend API and job lifecycle: `server/app.py:107`, `server/app.py:128`, `server/app.py:160`, `server/app.py:185`, `server/app.py:201`, `server/app.py:223`.
6. Backend mesh/tag preparation: `server/solver/mesh.py:153`.
7. Optimized BEM solve path:
   - `server/solver/solve_optimized.py:39` (space cache)
   - `server/solver/solve_optimized.py:48` (segments selection)
   - `server/solver/solve_optimized.py:136` (BIE)
8. Correct far-field directivity path: `server/solver/directivity_correct.py:18`, `server/solver/directivity_correct.py:136`, `server/solver/directivity_correct.py:288`.

### 2.5 External reference and tutorial material to include

1. `beminfo` workflow note: `_references/beminfo/beminfo.md:7`.
2. Reference loudspeaker script:
   - `_references/beminfo/loudspeakerscript.py:35` (`segments=[2]`)
   - `_references/beminfo/loudspeakerscript.py:42` (domain filter)
   - `_references/beminfo/loudspeakerscript.py:58` (BIE form)
3. Additional tutorial/test assets:
   - `_references/beminfo/260206_lspk_bem2/lspk_bem2.py:71` (source surfaces tags)
   - `_references/beminfo/260206_lspk_bem2/lspk_bem2.py:204` (segment-space selection)

---

## 3. Non-Negotiable Invariants

### 3.1 Geometry and parser invariants

Preserve behavior exactly for:

1. Implicit multiplication handling in expressions (`2p`, `2sin(p)`, `(a)(b)`).
2. Power operator handling (`^` -> exponentiation).
3. Function aliasing and helper functions in parser.
4. OSSE throat extension + slot + main-body transitions.
5. Circular arc throat profile mode.
6. Guiding-curve driven coverage-angle search.
7. R-OSSE derived length and profile equations.
8. Morphing (rectangle/circle) with no-shrink default behavior.
9. Quadrant-specific angle generation and partial mesh stitching.

### 3.2 Config compatibility invariants

Preserve:

1. ATH flat-key import mapping and MWG block parsing.
2. Unknown block round-trip via `_blocks`.
3. Raw expressions where input was expression text.
4. Full schema fields, including:
   - `gcurveRot`, `gcurveSf*`
   - `subdomainSlices`, `interfaceOffset`, `interfaceDraw`
   - `encFrontResolution`, `encBackResolution`
   - `sourceContours`, `abecSimProfile`.

### 3.3 Units, axes, and coordinate invariants

1. Keep a single canonical internal unit policy, then explicitly convert at boundaries.
2. Current model-space mapping for viewer mesh is radial/axial:
   - `vx = r*cos(p)`, `vy = axial`, `vz = r*sin(p)` (`src/geometry/meshBuilder.js:454`).
3. ATH export transform in MSH path currently remaps XYZ (`src/export/msh.js:24`).
4. Solver/directivity currently assumes mm mesh with selected m conversions (`server/solver/directivity_correct.py:57`, `server/solver/directivity_correct.py:94`).

Rebuild requirement: document and enforce conversions once, not ad hoc.

### 3.4 Surface-tag invariants

Canonical tags must be enforced end-to-end. Recommended canonical mapping:

1. `1 = rigid/walls`
2. `2 = source (velocity BC)`
3. `3 = optional secondary domain/surface`
4. `4 = symmetry/interface`

Then align all of:

1. frontend MSH writer (`src/export/msh.js:390`)
2. frontend simulation BC metadata (`src/app/App.js:188`, `src/ui/simulation/mesh.js:79`)
3. backend mesh preparation expectations (`server/solver/mesh.py:167`)
4. backend function-space segment selection (`server/solver/solve.py:44`, `server/solver/solve_optimized.py:48`)
5. beminfo-compatible scripts (`_references/beminfo/loudspeakerscript.py:35`).

---

## 4. Formula and Contract Registry

This section is the minimum mathematical and API contract that must survive simplification.

### 4.1 Parser contract

Reference: `src/geometry/expression.js:15`.

Must support:

1. Lowercasing and function substitution.
2. `^` operator conversion.
3. Constants `pi`, `e`.
4. Helper functions (`deg`, `rad`, `fmod`, `fmin`, `fmax`, `__exp2`).
5. Safe fallback parser behavior.

### 4.2 OSSE equation contract

Reference: `src/geometry/hornModels.js:61` and `src/geometry/hornModels.js:176`.

Core radius term:

1. `rGOS = sqrt((k*r0)^2 + 2*k*r0*z*tan(a0) + z^2*tan(a)^2) + r0*(1-k)`.
2. Termination term `rTERM` piecewise in `zNorm = q*z/L`.
3. Final radius `r = rGOS + rTERM`.

Additional behavior:

1. Optional throat extension/slot before main profile.
2. Optional circular-arc throat profile mode.
3. Optional profile rotation around `[0, r0]`.

### 4.3 R-OSSE equation contract

Reference: `src/geometry/hornModels.js:316`.

Must preserve:

1. `L` derived from quadratic term in `c1/c2/c3` and mouth radius `R(p)`.
2. `xt` equation with `r`, `m`, `b`.
3. `yt` blend between GOS-like and termination expressions with `q`.

### 4.4 Morphing contract

Reference: `src/geometry/morphing.js:8`, `src/geometry/morphing.js:42`.

Must preserve:

1. Rounded rectangle radial intersection logic.
2. Morph factor based on `morphFixed` and `morphRate`.
3. Circle mode target radius `sqrt(halfWidth*halfHeight)`.
4. `morphAllowShrinkage` behavior.

### 4.5 Slice distribution contract

Reference: `src/geometry/meshBuilder.js:99`.

Must preserve both mechanisms:

1. Resolution-graded map derived from throat/mouth resolution.
2. Split-map behavior using throat extension/slot and `throatSegments`.

### 4.6 Acoustic solver contract

References:

1. `server/solver/solve.py:20`, `server/solver/solve_optimized.py:136`.
2. `server/solver/directivity_correct.py:110`.

Must preserve BIE form:

1. `(D - 0.5I)p = i*w*rho*S*u`.
2. Far-field pressure from double-layer and single-layer potentials.
3. SPL conversion reference level consistency.
4. DI calculation path from evaluated pressure field (not piston-only fallback for primary path).

---

## 5. Known Mismatches to Resolve Early (High Priority)

### 5.1 Tag convention mismatch

Evidence:

1. Frontend export marks source as tag `2` (`src/export/msh.js:393`).
2. Backend solver spaces select `segments=[1]` (`server/solver/solve.py:44`, `server/solver/solve_optimized.py:48`).
3. beminfo scripts use source segments/tag `2` (`_references/beminfo/loudspeakerscript.py:35`).

Action: pick canonical mapping (recommended above) and align all consumers.

### 5.2 Frontend-backend mesh payload mismatch

Evidence:

1. API expects `surfaceTags` (`server/app.py:47`).
2. Frontend sends `faceGroups/faceMapping` and `mshContent`, not `surfaceTags` (`src/app/App.js:181`, `src/ui/simulation/mesh.js:73`).
3. Backend passes only `request.mesh.surfaceTags` to prepare mesh (`server/app.py:253`).

Action: enforce one payload format and add validation at boundary.

### 5.3 Unit policy drift risk

Evidence:

1. Backend code mixes mm mesh assumptions with meter distances (`server/solver/solve.py:79`, `server/solver/directivity_correct.py:57`).
2. Export paths have separate axis/unit handling (`src/export/msh.js:18`, `src/export/msh.js:196`).

Action: define canonical unit and conversion points in one adapter module.

### 5.4 Simulation import path inconsistency

Evidence:

1. `src/ui/simulation/mesh.js:3` imports `../export/msh.js`.
2. Actual module location is `src/export/msh.js`.

Action: fix pathing during rebuild consolidation and add module import smoke test.

### 5.5 Export parity gaps already listed in `fixes.md`

Evidence:

1. ABEC ZIP/folder requirement: `fixes.md:80`.
2. BEMPP export requirements: `fixes.md:186`.
3. MSH back wall and physical coverage: `fixes.md:246`.
4. CSV closure + scaling: `fixes.md:266`.

Action: bake these into rebuild phases as mandatory acceptance gates.

---

## 6. Target Simplified Architecture

Keep module count low while preserving explicit adapter boundaries.

Suggested layout:

```
src/
  main.js
  state/
    state.js
    schema.js
    config-io.js
  geometry/
    expression.js
    models.js
    morphing.js
    mesh.js
  export/
    msh.js
    geo.js
    csv.js
    abec.js
  simulation/
    client.js
    payload.js
    results.js
    smoothing.js
  viewer/
    scene.js
    materials.js
server/
  app.py
  solver.py
  mesh_adapter.py
```

Rules:

1. Keep one parameter model in memory.
2. Keep boundary adapters explicit:
   - config <-> params
   - params <-> geometry
   - geometry <-> export
   - frontend mesh payload <-> backend mesh adapter
3. No silent tag/unit conversion inside random UI code.

---

## 7. Rebuild Execution Plan and Gates

## Phase 0 - Freeze baseline

1. Snapshot reference outputs for representative OSSE and R-OSSE configs from `_references/testconfigs`.
2. Store numerical checkpoints for profile samples and mesh counts.
3. Use `scripts/ath-compare.js` where applicable for parity smoke checks.

Gate:

1. Baseline artifacts committed and reproducible.

## Phase 1 - State/schema/config core

1. Implement single state container with persistence and undo/redo.
2. Port full schema and defaults.
3. Port parser + MWG exporter with `_blocks` round-trip.
4. Add tests for ATH flat-key imports and unknown block retention.

Gate:

1. load -> save -> reload is stable for sample configs.

## Phase 2 - Geometry core consolidation

1. Port parser and math utilities unchanged first.
2. Port OSSE and R-OSSE equations with parity checks.
3. Port guiding-curve search and circular-arc mode.
4. Port parameter preparation behavior including scale wrapping of function-valued parameters.

Gate:

1. Formula checkpoints match baseline within tolerance.

## Phase 3 - Mesh and enclosure core

1. Port slice mapping, angle generation, morph targets, body triangulation.
2. Port throat source generation and rear shape handling.
3. Port enclosure geometry and greedy stitching with manifold checks.
4. Keep optional group metadata output for exporters/simulation.

Gate:

1. Mesh builds for default OSSE/R-OSSE without NaNs or invalid indices.

## Phase 4 - Export system unification

1. Consolidate CSV/GEO/MSH/ABEC exporters on one canonical mesh/tag adapter.
2. Integrate `fixes.md` requirements directly:
   - CSV closure and scale correction.
   - MSH full physical coverage and freestanding back wall.
   - ABEC ZIP with expected folder/file naming.
   - BEMPP-compatible GEO/MSH pair.
3. Keep CAD-specific generation optional but adapterized.

Gate:

1. Generated GEO passes Gmsh parse and mesh generation.
2. MSH physical groups are visible and correct.
3. ABEC ZIP structure matches reference.

## Phase 5 - Simulation frontend contract

1. Define strict payload contract (`vertices`, `indices`, `surfaceTags`, metadata).
2. Remove inconsistent temporary fields or support them via explicit adapter.
3. Keep run/stop/poll/results UX and smoothing controls unchanged.
4. Preserve SVG-based chart outputs (do not introduce chart library dependency unless needed).

Gate:

1. End-to-end request payload validates against backend model before send.

## Phase 6 - Backend solver simplification

1. Consolidate solve paths after parity (legacy + optimized only until validated).
2. Enforce canonical segment/tag mapping in function spaces.
3. Keep far-field directivity path (`directivity_correct`) as primary.
4. Keep symmetry logic, but add explicit validation output for reductions.

Gate:

1. Same input mesh produces stable, non-empty source space and non-fallback outputs.

## Phase 7 - Integration hardening

1. Add regression suite:
   - config round-trip
   - geometry checkpoints
   - exporter validity checks
   - simulation API smoke flow
2. Remove dead code only after parity is proven.

Gate:

1. All acceptance checklist items pass.

---

## 8. BEMPP Workflow Requirements (Including `beminfo` Tutorials)

The rebuild must explicitly support the tutorial workflow documented in `beminfo`.

### 8.1 Mandatory workflow support

1. User edits `.geo`.
2. User runs Gmsh to produce `.msh`.
3. Python/BEMPP script imports `.msh` and solves.

Reference: `_references/beminfo/beminfo.md:7`.

### 8.2 Mandatory export behavior for BEMPP package

1. Provide a valid `.geo` and `.msh` pair with matching mesh filename.
2. Ensure physical surfaces use stable integer tags and documented meanings.
3. Include clear note on unit assumptions (recommended: SI at script boundary).
4. Include a Python starter script template with:
   - explicit `segments=[source_tag]`
   - boundary velocity callable template
   - far-field directivity sampling template.

### 8.3 Tutorial parity checks to include

1. Verify source surface selection parity with `segments=[2]` style usage from:
   - `_references/beminfo/loudspeakerscript.py:35`
   - `_references/beminfo/260206_lspk_bem2/lspk_bem2.py:204`.
2. Verify workflow against at least one tutorial mesh in `_references/beminfo/260206_lspk_bem2/`.

---

## 9. Acceptance Checklist (Release Gate)

### 9.1 Geometry and viewer

1. OSSE and R-OSSE defaults render correctly.
2. Live parameter edits update geometry immediately.
3. Display modes preserved: standard, zebra, grid/wireframe, curvature.
4. Vertex/triangle stats update correctly.

### 9.2 Config

1. ATH-style config imports correctly.
2. MWG export/reimport preserves supported fields.
3. Unknown blocks survive through `_blocks`.

### 9.3 Exports

1. CSV loops are closed and scale is correct.
2. GEO loads in Gmsh and can produce mesh.
3. MSH has correct physical groups.
4. ABEC ZIP includes expected folder structure and file naming.
5. BEMPP package aligns with tutorial workflow and tag conventions.

### 9.4 Simulation

1. `/health`, `/api/solve`, `/api/status/{id}`, `/api/results/{id}`, `/api/stop/{id}` all function from UI.
2. Payload includes explicit surface tags consumed by backend.
3. Solver produces SPL/DI/impedance/directivity without empty source segment space.
4. Smoothing applies post-solve without rerunning simulation.

---

## 10. Implementation Rules During Rebuild

1. Any tag or unit convention change must update frontend export, frontend payload, backend mesh adapter, backend solver in one atomic commit.
2. Keep conversions at boundaries only; internal core should use one unit system.
3. Prefer deleting dead code to adding compatibility shims, except for user file compatibility.
4. Keep formulas centralized and testable (no hidden duplicate implementations in UI handlers).

---

## 11. Explicit Decisions Needed Before Final Cutover

1. Canonical physical tag mapping (`1/2/3/4` meanings).
2. Canonical unit policy for BEMPP-oriented exports (recommended meters at script boundary).
3. CircSym semantics and allowed `abecSimProfile` values.
4. Whether STEP remains optional adapter or first-class runtime path.

If unresolved: preserve existing behavior and mark TODOs at adapter boundaries, not inside formulas.
