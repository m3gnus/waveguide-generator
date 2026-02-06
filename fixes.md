# MWG Cleanup and Improvement Plan

This document is both a task list and implementation guide. Each section is one
discrete unit of work. They are ordered so that earlier steps do not depend on
later ones --- work them top to bottom.

---

## Phase 1 -- Remove dead code and unused features

> **Completed:** 1.1 Delete stale docs, 1.2 Remove Mouth Rollback,
> 1.3 Remove Enclosure Plan, 1.4 Remove LF Source B

### 1.5 Remove legacy (non-CAD) MSH export path

The system has two .msh export paths:

1. **Legacy:** `exportHornToMSH()` and `exportHornToMSHWithBoundaries()` in
   `src/export/msh.js` -- generates .msh from raw triangulated vertices/indices
   produced by `meshBuilder.js`.
2. **CAD:** `exportHornToMSHFromCAD()` in `src/export/msh.js` -- generates .msh
   from tessellated STEP/B-Rep geometry via OpenCascade.

Only the CAD path (2) should be kept. The .msh file must always be generated
from the STEP geometry.

**What to change:**

- In `src/export/msh.js`: remove `exportHornToMSH()` and
  `exportHornToMSHWithBoundaries()`. Keep `exportHornToMSHFromCAD()` and rename
  it to something clearer (e.g. `exportMSH()`).
- In `src/export/index.js`: update re-exports.
- In `src/app/exports.js`: update MSH export handler to use the CAD path
  exclusively. Currently line 118 calls `exportHornToMSHWithBoundaries()` --
  replace with the CAD-based function.
- In `scripts/ath-compare.js`: update or remove references to the legacy path.

**Suggestion:** The legacy `meshBuilder.js` mesh is still needed for the 3D
viewer (Three.js). Only the .msh *export* path should be consolidated to use
CAD. The viewer can continue rendering from the fast triangulated mesh.

---

### 1.6 Investigate and resolve z_values / zMapPoints

**Research findings:**

`z_values.txt` contains 916 floating-point values representing explicit axial
(Z) slice positions for mesh generation, ranging from -6.148 to 94.77. These are
loaded via the `zMapPoints` parameter in the schema (an expression field that
accepts comma-separated values).

In `meshBuilder.js`, the `buildSliceMap()` function checks if `zMapPoints` is
provided. If it is, those explicit positions are used instead of the
auto-computed resolution-based grading. This was used for ATH-compatible mesh
topology matching.

**Decision needed:** Since we are consolidating to the CAD/STEP mesh path,
`zMapPoints` is only relevant for the legacy mesh builder (used by the viewer).
The question is whether the viewer mesh needs exact slice positions or whether
resolution-based grading is sufficient.

**Suggestion:** Remove `z_values.txt` from the repo. Keep the `zMapPoints`
schema field for now as a power-user option (it allows advanced users to specify
exact slice distributions). If it is never used in practice, it can be removed
in a later cleanup pass. Add a tooltip clarifying its purpose.

---

## Phase 2 -- Fix and clarify existing features

> **Completed:** 2.1 Enable Export STEP button, 2.2 Clarify Wall Thickness (tooltip added)

### 2.3 Investigate viewing .msh in the viewer

**Research findings:**

The current viewer (`src/viewer/`) uses Three.js `BufferGeometry` built from
vertices/indices arrays. It renders the triangulated mesh from `meshBuilder.js`.

A .msh file (Gmsh MSH 2.2 format) is a text-based mesh with nodes, elements,
and physical groups. It can be parsed and displayed in Three.js, but the viewer
would need:

1. A .msh parser (read nodes + triangle elements from MSH format).
2. Conversion to Three.js BufferGeometry.
3. Ability to render physical group boundaries as colored overlays.

**Suggestion:** This is feasible but non-trivial. A simpler approach: since the
CAD path already tessellates the STEP geometry, use that tessellation directly
for the viewer. This would show the actual mesh that gets exported, rather than
the legacy meshBuilder output. Add a toggle between:

- **Surface view** (current -- fast meshBuilder triangulation)
- **Mesh view** (CAD tessellation -- shows the actual export mesh with
  boundary groups highlighted)

This avoids needing a .msh parser entirely. The CAD tessellator
(`src/cad/tessellator.js`) already produces vertices/indices that Three.js can
consume.

---

### 2.4 Move Offset to Directivity Map section and decouple from geometry

**Research findings:**

`verticalOffset` (schema key in MESH section) currently does two things:

1. Offsets the 3D model geometry in the Z axis (`meshBuilder.js:414`).
2. Is used as part of the export coordinate system (`msh.js:60-62`).

The request is to make Offset only affect the measurement/simulation position,
not the 3D model.

**What to change:**

- Move `verticalOffset` from the MESH schema section to a new field in the
  Simulation tab's DIRECTIVITY MAP section in `index.html` (near lines 120-136).
- Rename to something like "Measurement Offset" for clarity.
- In `meshBuilder.js:349-414`: remove the `verticalOffset` application to vertex
  positions. The viewer mesh should always be centered.
- In `src/export/msh.js` and `src/export/abecProject.js`: keep the offset for
  simulation coordinate transforms only.
- In `src/app/params.js`: the `applyVerticalOffset` flag already exists. Use
  this to ensure the offset only applies to exports/simulation, never to the
  viewer mesh.

---

## Phase 3 -- UI reorganization

> **Completed:** 3.1 Move Scale into R-OSSE and OSSE subwindows

### 3.2 Create unified Mesh Density box

All mesh resolution controls should be grouped into a single "Mesh Density"
section.

**Target layout:**

```
Mesh Density
├── Angular Segs      (currently in MESH)
├── Length Segs        (currently in MESH)
├── Corner Segs        (currently in MESH)
├── ─────────────────
├── Throat Resolution  (currently in MESH)
├── Mouth Resolution   (currently in MESH)
├── Front Resolution   (currently in ENCLOSURE)
├── Back Resolution    (currently in ENCLOSURE)
├── Rear Resolution    (currently in MESH)
```

**What to change:**

- In `schema.js`: move `encFrontResolution` and `encBackResolution` from the
  ENCLOSURE section into the MESH section (or create a dedicated MESH_DENSITY
  section).
- In `paramPanel.js`: update the rendering to group all these fields under a
  single collapsible "Mesh Density" `<details>` element. A horizontal separator
  should appear between the segment counts and the resolution fields.
- Remove `throatSegments` if it is redundant with `throatResolution` (verify
  this first).

---

### 3.3 Split Map Angle Range into three fields

Currently `polar-angle-range` in `index.html:123` is a single text input
accepting comma-separated values like `"0,180,37"`.

**What to change:**

- Replace the single text input with three numeric inputs:
  - **Start Angle (deg)** -- default 0
  - **End Angle (deg)** -- default 180
  - **Step (deg)** -- default 5
- In `src/ui/simulation/actions.js:17-21`: update the parsing to read from three
  separate input fields instead of splitting a comma string.
- The step field means "measure every N degrees". So start=0, end=180, step=5
  produces measurements at 0, 5, 10, ..., 175, 180 (37 points).
- In `src/export/abecProject.js:166`: update `angleRange` generation to compute
  `PolarRange` from the three fields.

**Note:** The current format `"0,180,37"` uses a *count* (37 points), while the
new format uses a *step* (5 degrees). The conversion is:
`count = floor((end - start) / step) + 1`. Make sure downstream consumers
receive the correct format (ABEC expects start, end, count).

---

## Phase 4 -- New features

### 4.1 Add output folder selection

Currently each export opens the browser's file picker individually. A persistent
output folder selection would streamline batch exports.

**What to add:**

- A "Choose Folder" button placed to the LEFT of the export counter input in
  `index.html` (near lines 54-56).
- Use the `window.showDirectoryPicker()` API (File System Access API) to let the
  user pick a folder. Store the directory handle.
- When a folder is selected, exports skip the per-file picker and write directly
  to the chosen folder using the prefix + counter naming convention.
- Show the selected folder name next to the button.
- Fallback: if the browser doesn't support `showDirectoryPicker()`, hide the
  button and keep the current per-file picker behavior.

**Files to modify:**

- `index.html`: add the button.
- `src/ui/fileOps.js`: add `selectOutputFolder()` and modify `saveFile()` to
  check for a stored directory handle.
- `src/app/exports.js`: thread the directory handle through export functions.

---

### 4.2 Add CircSym simulation mode

A circular symmetry mode for strictly axisymmetric devices. The schema already
has a partial field: `abecSimProfile` with label "CircSym Profile" (line 219).

**What to add:**

- A clear toggle or mode selector for CircSym in the ABEC/Simulation settings.
  When enabled, the simulation uses a single 2D axisymmetric profile instead of
  the full 3D mesh.
- The `abecSimProfile` field (currently just a number defaulting to -1) should
  be expanded into a proper mode:
  - Off (default, -1): full 3D simulation.
  - On (profile index): axisymmetric simulation using the specified profile.
- Update `src/export/abecProject.js` and `src/export/mwgConfig.js` to output the
  correct ABEC configuration for CircSym mode.
- Update the Simulation tab UI to show a CircSym toggle with a profile selector.

**Note:** This needs clarification on what "CircSym Profile" index means in the
ABEC context. Investigate the ABEC documentation or existing MWG configs to
determine valid values and behavior.

---

## Phase 5 -- Geometry script cleanup

### 5.1 Review and consolidate geometry scripts

Current `src/geometry/` files:

| File | Purpose | Status |
|---|---|---|
| `index.js` | Re-exports | Done |
| `common.js` | Shared utilities (evalParam, parseList, etc.) | Keep |
| `expression.js` | Math expression parser | Keep |
| `hornModels.js` | OSSE/R-OSSE profile calculations | Keep |
| `meshBuilder.js` | Triangulated mesh builder (viewer) | Keep |
| `morphing.js` | Circular-to-rectangular throat morphing | Keep |
| `rearShape.js` | Rear shape geometry (was rollback.js) | Done |
| `enclosure.js` | Rear chamber/enclosure geometry | Done (encPlan removed) |

Phase 1 removals are complete. No further consolidation needed.

---

## Summary of remaining changes by file

Quick reference for which files are touched by remaining tasks:

| File | Tasks |
|---|---|
| `index.html` | 3.3, 4.1 |
| `src/config/schema.js` | 3.2 |
| `src/geometry/meshBuilder.js` | 2.4 |
| `src/export/msh.js` | 1.5, 2.4 |
| `src/export/mwgConfig.js` | 2.4 |
| `src/export/index.js` | 1.5 |
| `src/export/abecProject.js` | 3.3, 4.2 |
| `src/app/exports.js` | 1.5, 4.1 |
| `src/app/params.js` | 2.4 |
| `src/ui/paramPanel.js` | 3.2 |
| `src/ui/fileOps.js` | 4.1 |
| `src/ui/simulation/actions.js` | 3.3 |
| `scripts/ath-compare.js` | 1.5 |
