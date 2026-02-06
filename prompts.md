# LLM Task Prompts

Copy-paste each prompt below into a local LLM. Each one is self-contained.
Do them in order -- later prompts assume earlier ones are done.

---

## Prompt 1: Delete stale documentation files

```
You are working on the MWG project. Delete all the following files by running
git rm on each one. Then delete z_values.txt as well. Do NOT delete README.md,
server/README.md, or fixes.md.

Files to delete:

C99_FUNCTIONS_IMPLEMENTATION.md
CONSOLIDATION_SUMMARY.md
ENCLOSURE_CONNECTION_FINAL.md
ENCLOSURE_GAP_FIX.md
GEOMETRY_REDESIGN_PROPOSAL.md
GMSH_INTEGRATION_STATUS.md
SESSION_SUMMARY.md
docs/ARCHITECTURE.md
docs/ATH_CALIBRATION.md
docs/README.md
src/app/AGENTS.md
src/config/AGENTS.md
src/export/AGENTS.md
src/geometry/AGENTS.md
src/logging/AGENTS.md
src/presets/AGENTS.md
src/results/AGENTS.md
src/solver/AGENTS.md
src/ui/AGENTS.md
src/validation/AGENTS.md
src/viewer/AGENTS.md
src/workflow/AGENTS.md
test_gmsh_integration.js
test_stl_remesh.js
z_values.txt

After deleting, commit with message: "Remove stale docs, test scripts, and z_values.txt"
```

---

## Prompt 2: Remove Enclosure Plan

```
You are working on the MWG project. Remove the "Enclosure Plan" feature entirely.
It is non-functional. Here are the exact changes needed:

FILE: src/config/schema.js
- Delete this line from the ENCLOSURE section:
        encPlan: { type: 'expression', label: 'Enclosure Plan', default: '', tooltip: 'Name of a user-defined enclosure plan.' },

FILE: src/config/parser.js
- Delete this line (~line 212):
            if (encBlock._items.Plan) { p.encPlan = encBlock._items.Plan; }

FILE: src/export/mwgConfig.js
- In the enclosure export block (~lines 130-133), change:
        if (params.encPlan || params.encDepth > 0) {
            content += `Mesh.Enclosure = {\n`;
            if (params.encPlan) {
                content += `Plan = ${params.encPlan}\n`;
            } else {
                content += `Depth = ${params.encDepth}\n`;
  to:
        if (params.encDepth > 0) {
            content += `Mesh.Enclosure = {\n`;
            content += `Depth = ${params.encDepth}\n`;
  (Remove the encPlan branch entirely. Keep the encDepth path.)

FILE: src/geometry/enclosure.js
- The function buildPlanOutline() at ~line 186 reads params.encPlan. Since
  encPlan is being removed, this function will never be called with a valid
  planName. For now, leave buildPlanOutline() in place but remove the encPlan
  reference. Change line 187 from:
    let planName = params.encPlan;
  to:
    let planName = null;
  This effectively disables the function without breaking any call sites.

No other files reference encPlan. Commit with message:
"Remove non-functional Enclosure Plan feature"
```

---

## Prompt 3: Remove LF Source B

```
You are working on the MWG project. Remove all four LF Source B parameters.
They interfere with the system and should be deleted entirely.

FILE: src/config/schema.js
- Delete these 4 lines from the ENCLOSURE section:
        lfSourceBRadius: { type: 'number', label: 'LF Source B Radius', unit: 'mm', default: 0 },
        lfSourceBSpacing: { type: 'number', label: 'LF Source B Spacing', unit: 'mm', default: 0 },
        lfSourceBDrivingWeight: { type: 'number', label: 'LF Source B Driving Weight', default: 0 },
        lfSourceBSID: { type: 'number', label: 'LF Source B SID', default: 1 }

FILE: src/config/parser.js
- Delete this entire block (~lines 227-234):
        const lfSourceB = result.blocks['LFSource.B'];
        if (lfSourceB && lfSourceB._items) {
            const p = result.params;
            if (lfSourceB._items.Radius !== undefined) { p.lfSourceBRadius = lfSourceB._items.Radius; }
            if (lfSourceB._items.Spacing !== undefined) { p.lfSourceBSpacing = lfSourceB._items.Spacing; }
            if (lfSourceB._items.DrivingWeight !== undefined) { p.lfSourceBDrivingWeight = lfSourceB._items.DrivingWeight; }
            if (lfSourceB._items.SID !== undefined) { p.lfSourceBSID = lfSourceB._items.SID; }
        }

FILE: src/cad/cadEnclosure.js
- Remove the LF source cutting block (~lines 181-186). Delete these lines:
    // Cut LF source opening if defined
    const lfRadius = Number(params.lfSourceBRadius || 0);
    if (lfRadius > 0) {
        const lfSpacing = Number(params.lfSourceBSpacing || 0);
        encShape = cutLFSourceOpening(oc, encShape, lfRadius, -lfSpacing);
    }
- Also remove the cutLFSourceOpening function definition (starts at line 135)
  since nothing else calls it. If you're unsure, search the entire codebase for
  "cutLFSourceOpening" -- it's only called from this one location.

No other files use lfSourceB parameters in a meaningful way (paramPanel.js
auto-renders from the schema, so removing from schema is sufficient).

Commit with message: "Remove LF Source B parameters"
```

---

## Prompt 4: Enable STEP export button

```
You are working on the MWG project. The Export STEP button is hardcoded as
disabled in the HTML but the code already enables it dynamically when
OpenCascade loads. The hardcoded disabled attribute is unnecessary.

FILE: index.html
- Change line 44 from:
                <button id="export-step-btn" class="secondary" disabled>Export STEP</button>
  to:
                <button id="export-step-btn" class="secondary">Export STEP</button>

That's it. The button will be enabled/disabled at runtime by App.js line 165-166:
      const stepBtn = document.getElementById('export-step-btn');
      if (stepBtn) stepBtn.disabled = false;

And if CAD fails to load, App.js sets this.useCAD = false and the exportSTEP()
method checks isCADReady() before proceeding. So the button is safe to have
enabled by default -- it shows an alert if CAD isn't ready.

Commit with message: "Enable STEP export button by default"
```

---

## Prompt 5: Update Wall Thickness tooltip

```
You are working on the MWG project. The Wall Thickness parameter works but has
no tooltip explaining what it does. Add a clear tooltip.

FILE: src/config/schema.js
- Change line 126 from:
        wallThickness: { type: 'number', label: 'Wall Thickness', unit: 'mm', default: 5.0 },
  to:
        wallThickness: { type: 'number', label: 'Wall Thickness', unit: 'mm', default: 5.0, tooltip: 'Thickness of the horn wall in the STEP/CAD export. Set to 0 for a zero-thickness surface. Does not affect the 3D viewer mesh.' },

That's the only change. Commit with message:
"Add tooltip to Wall Thickness parameter"
```

---

## Prompt 6: Move Scale into R-OSSE and OSSE sections

```
You are working on the MWG project. The "Scale" parameter is currently in the
GEOMETRY section of the schema. It should appear inside both R-OSSE and OSSE
parameter sections instead, so users see it alongside the model parameters.

FILE: src/config/schema.js

Step 1: Add scale as the FIRST field in the 'R-OSSE' section. Insert it before
the R field. The R-OSSE section should start like this:

    'R-OSSE': {
        scale: {
            type: 'range',
            label: 'Scale',
            min: 0.1,
            max: 2,
            step: 0.001,
            default: 1.0,
            tooltip: 'Global scaling factor for all length dimensions. Values < 1 shrink the waveguide, > 1 enlarge it. Affects L, r0, morphCorner, and all other length parameters.'
        },
        R: { type: 'expression', label: 'R - Mouth Radius', ...

Step 2: Add the same scale definition as the FIRST field in the 'OSSE' section:

    'OSSE': {
        scale: {
            type: 'range',
            label: 'Scale',
            min: 0.1,
            max: 2,
            step: 0.001,
            default: 1.0,
            tooltip: 'Global scaling factor for all length dimensions. Values < 1 shrink the waveguide, > 1 enlarge it. Affects L, r0, morphCorner, and all other length parameters.'
        },
        L: { type: 'expression', label: 'L - Length of the Waveguide', ...

Step 3: Remove the scale field from the 'GEOMETRY' section. Delete the entire
scale block (lines 26-34):

        scale: {
            type: 'range',
            label: 'Scale',
            min: 0.1,
            max: 2,
            step: 0.001,
            default: 1.0,
            tooltip: 'Global scaling factor for all length dimensions. Values < 1 shrink the waveguide, > 1 enlarge it. Affects L, r0, morphCorner, and all other length parameters.'
        },

So the GEOMETRY section should now start with:

    'GEOMETRY': {
        throatProfile: {
            type: 'select',
            ...

IMPORTANT: The parameter key must remain "scale" (not renamed). All downstream
code reads params.scale and will continue to work because the paramPanel.js
rendering iterates over whatever keys are in the active model's schema section.

Commit with message: "Move Scale parameter into R-OSSE and OSSE sections"
```

---

## Prompt 7: Remove Mouth Rollback

```
You are working on the MWG project. Remove the "Mouth Rollback" feature
entirely. This is the most complex of the easy tasks because you must preserve
the "Rear Shape" functionality which lives in the same file.

IMPORTANT: The file src/geometry/rollback.js contains TWO functions:
  1. addRollbackGeometry() -- REMOVE this (lines 8-57)
  2. addRearShapeGeometry() -- KEEP this (lines 63-108)

Here are ALL the changes needed:

FILE: src/config/schema.js
- Delete the entire 'ROLLBACK' section (lines 139-152):
    'ROLLBACK': {
        rollback: {
            type: 'select',
            label: 'Rollback',
            options: [
                { value: false, label: 'Off' },
                { value: true, label: 'On' }
            ],
            default: false,
            tooltip: 'Add toroidal rollback fold at the mouth'
        },
        rollbackAngle: { type: 'range', label: 'Rollback Angle', unit: 'deg', min: 30, max: 270, step: 1, default: 180, tooltip: 'How far the lip curls back (degrees)' },
        rollbackStart: { type: 'range', label: 'Rollback Start', min: 0.1, max: 0.99, step: 0.01, default: 0.5, tooltip: 'Where the rollback begins (0=throat, 1=mouth)' }
    },

FILE: src/config/parser.js
- Delete the rollback normalization block (lines 193-203):
        // Normalize Rollback params (both types, flat keys)
        {
            const p = result.params;
            if (p['Scale'] !== undefined) {
                const scaleNum = Number(p['Scale']);
                p.scale = Number.isFinite(scaleNum) ? scaleNum : p['Scale'];
            }
            if (p['Rollback'] !== undefined) { p.rollback = p['Rollback'] === '1' || p['Rollback'] === 1; }
            if (p['Rollback.Angle']) { p.rollbackAngle = p['Rollback.Angle']; }
            if (p['Rollback.StartAt']) { p.rollbackStart = p['Rollback.StartAt']; }
        }
  WAIT -- this block also handles Scale parsing! You must KEEP the Scale lines
  and only remove the Rollback lines. The result should be:
        {
            const p = result.params;
            if (p['Scale'] !== undefined) {
                const scaleNum = Number(p['Scale']);
                p.scale = Number.isFinite(scaleNum) ? scaleNum : p['Scale'];
            }
        }

FILE: src/export/mwgConfig.js
- Delete the rollback config output block (lines 67-71):
        if (params.rollback) {
            content += `Rollback = 1\n`;
            content += `Rollback.Angle = ${params.rollbackAngle}\n`;
            content += `Rollback.StartAt = ${params.rollbackStart}\n`;
        }

FILE: src/geometry/meshBuilder.js
- Delete the rollback call block (~lines 420-423):
  // Add Rollback for R-OSSE
  if (params.type === 'R-OSSE' && params.rollback) {
    addRollbackGeometry(vertices, indices, params, lengthSteps, angleList, quadrantInfo.fullCircle);
  }
- Also remove the import of addRollbackGeometry at the top of the file. Search
  for "addRollbackGeometry" in the import statements and remove it.

FILE: src/geometry/rollback.js
- Delete the addRollbackGeometry function (lines 1-57, including the imports
  that are only needed by rollback: calculateROSSE, calculateOSSE, evalParam).
- KEEP addRearShapeGeometry (lines 59-108).
- After deletion, check if the remaining addRearShapeGeometry function needs any
  of the removed imports. It does NOT use calculateROSSE, calculateOSSE, or
  evalParam -- it only uses params.rearShape, params.wallThickness, vertices,
  and indices. So the imports at line 1-2 can be removed entirely.
- Rename the file from rollback.js to rearShape.js since it now only contains
  rear shape code.

FILE: src/geometry/index.js
- Change:
  export { addRollbackGeometry, addRearShapeGeometry } from './rollback.js';
  to:
  export { addRearShapeGeometry } from './rearShape.js';

FILE: src/ui/paramPanel.js
- Delete the Rollback section block (lines 163-171):
        // --- Rollback (R-OSSE primarily, but available for both) ---
        if (type === 'R-OSSE') {
            const rollSection = this.createDetailsSection('Mouth Rollback', 'rollback-details');
            const rollSchema = PARAM_SCHEMA.ROLLBACK;
            for (const [key, def] of Object.entries(rollSchema)) {
                rollSection.appendChild(this.createControlRow(key, def, state.params[key]));
            }
            this.container.appendChild(rollSection);
        }

After all changes, search the codebase for "rollback" (case-insensitive) to
verify nothing was missed. The only remaining hits should be in fixes.md.

Commit with message: "Remove Mouth Rollback feature, rename rollback.js to rearShape.js"
```
