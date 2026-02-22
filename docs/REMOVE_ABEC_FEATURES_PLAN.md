# Plan: Remove ABEC Export, CircSym, Interface, and Infinite Baffle

## Context
The project is migrating from ABEC to bempp for BEM solving. Several features are ABEC-specific and unsupported in bempp: CircSym mode, interface resolution, ABEC project export, and infinite baffle simulation. Removing these simplifies the codebase and UI. The BEM solving pipeline must remain intact.

## Research Completed (This Session)

The following research was performed to inform this plan:

1. **CircSym references** — found in 13 files. Used exclusively by ABEC export pipeline (abecProject.js). Not used by BEM/bempp solver. Safe to remove entirely.

2. **Interface resolution** — `interfaceResolution`, `interfaceOffset`, `interfaceDraw`, `subdomainSlices` are all parsed but never consumed by any geometry builder. Flow: config parser → waveguidePayload.js → server/app.py request model → ignored. Safe to remove all four.

3. **ABEC export pipeline** — self-contained pipeline spanning ~10 frontend files + test files. Entry point: "Export ABEC Project" button → `exportABECProject()` → generates ZIP with Project.abec, solving.txt, observation.txt, .msh. Uses `buildExportMeshFromParams()` which is SHARED with MSH export (must keep).

4. **Infinite baffle (sim_type=1)** — already disabled/deferred everywhere: HTML option is `disabled`, frontend action blocks it with error, backend /api/solve rejects it with HTTP 422. Only sim_type=2 (free-standing) works. Geometry is independent of sim_type (controlled by enc_depth/wall_thickness instead).

5. **Symmetry detection** — TWO independent implementations:
   - Frontend JS (`src/geometry/symmetry.js`) — formula-based, ABEC export only → safe to remove
   - Backend Python (`server/solver/symmetry.py`) — mesh-based, used by BEM solver → MUST KEEP

6. **Frequency params** — `abecF1`, `abecF2`, `abecNumFreq` are bound to the same DOM inputs used by BEM solver. The BEM solver reads them directly from DOM, not from state. The `abecAbscissa` and `abecMeshFrequency` params are ABEC-only. Decision: rename to generic names (`freqStart`, `freqEnd`, `numFreqs`), remove `abecAbscissa`/`abecMeshFrequency`.

7. **Polar settings** — `src/ui/simulation/polarSettings.js` is used by BOTH BEM solver and ABEC export. Must keep.

8. **GUI implications** — After removing infinite baffle, only one sim type option remains → remove the dropdown entirely. CircSym controls also removed.

---

## Implementation Steps

### Step 1: Remove ABEC Export Pipeline (Frontend)

**Delete files:**
- `src/export/abecProject.js` — all ABEC generators
- `src/export/abecBundleValidator.js` — bundle validator
- `scripts/validate-abec-bundle.js` — CLI validator
- `docs/ABEC_PARITY_CONTRACT.md` — contract doc
- `src/geometry/symmetry.js` — only used by ABEC export (backend has its own)

**Delete test files:**
- `tests/abec-bundle-parity.test.js`
- `tests/abec-circsym.test.js`
- `tests/fixtures/abec/` — entire directory

**Edit files:**
- `src/app/exports.js` — remove `exportABECProject()` function; remove imports from `abecProject.js` and `symmetry.js`; keep `buildExportMeshFromParams()` and `exportMSH()` intact
- `src/app/App.js` — remove `exportABECProject()` method and its import
- `src/app/events.js` — remove `export-abec-btn` click handler
- `index.html` — remove "Export ABEC Project" button

**Keep:** `src/ui/simulation/polarSettings.js` — used by BEM solver too

---

### Step 2: Remove CircSym

**Edit files:**
- `index.html` — remove CircSym dropdown (`#circsym-mode`) and profile input (`#circsym-profile`) + labels
- `src/config/schema.js` — remove `abecSimProfile` from ABEC section
- `src/config/index.js` — remove `ABEC.SimProfile` parsing; remove `abecSimProfile` from defaults; on import, silently ignore
- `src/export/mwgConfig.js` — remove `abecSimProfile` export line
- `src/ui/simulation/settings.js` — remove `circsymModeEl`/`circsymProfileEl` logic
- `src/ui/simulation/SimulationPanel.js` — remove `circsym-profile` binding
- `src/ui/simulation/actions.js` — remove `circSymProfile` reading

---

### Step 3: Remove All Unused Interface Params

Remove all four unused interface params: `interfaceResolution`, `interfaceOffset`, `interfaceDraw`, `subdomainSlices`.

**Edit files:**
- `src/config/index.js` — remove parsing of `Mesh.InterfaceResolution`, `Mesh.InterfaceOffset`, `Mesh.InterfaceDraw`, `Mesh.SubdomainSlices`; remove all four from defaults
- `src/solver/waveguidePayload.js` — remove `interface_resolution`, `interface_offset`, `interface_draw`, `subdomain_slices` fields from payload
- `server/app.py` — remove these four fields from request models
- `src/ui/paramPanel.js` — remove these from mesh density panel rendering (if listed in MESH_DENSITY_ORDER or similar)

---

### Step 4: Remove Infinite Baffle (sim_type=1)

**Approach:** Since only `sim_type=2` (free-standing) is supported, hardcode it and remove the dropdown.

**Edit files:**
- `index.html` — remove the "Simulation Type" dropdown (`#sim-type`) and its label entirely
- `src/config/schema.js` — remove `abecSimType` from schema
- `src/config/index.js` — when importing `ABEC.SimType`, silently ignore (always treat as 2). Remove `abecSimType` from defaults
- `src/export/mwgConfig.js` — remove `abecSimType` export line
- `src/ui/simulation/SimulationPanel.js` — remove `sim-type` binding from `simulationParamBindings`
- `src/ui/simulation/settings.js` — remove `abecSimType` special-case default-to-2 logic
- `src/ui/simulation/actions.js` — remove `simulationType` check (line that shows error for non-2); hardcode `simulationType: '2'` in config
- `src/solver/waveguidePayload.js` — hardcode `sim_type: 2` instead of reading from params
- `server/app.py` — remove `sim_type != "2"` validation in `/api/solve` (it's always 2 now); keep `sim_type` field in request model for backwards compat but default to 2

---

### Step 5: Rename ABEC-prefixed Frequency Params

The BEM solver reads frequencies from the same DOM elements (`#freq-start`, `#freq-end`, `#freq-steps`) that are bound to state keys `abecF1`, `abecF2`, `abecNumFreq`. With ABEC removed, rename for clarity. Config import still understands old `ABEC.F1` etc. from imported scripts.

- `abecF1` → `freqStart`
- `abecF2` → `freqEnd`
- `abecNumFreq` → `numFreqs`
- `abecAbscissa` → remove (BEM solver hardcodes 'log' spacing)
- `abecMeshFrequency` → remove (only used in ABEC solving file)

**Files to update:**
- `src/config/schema.js` — rename keys in schema, remove `abecAbscissa`/`abecMeshFrequency`
- `src/config/index.js` — update parsing aliases and defaults; keep `ABEC.F1` → `freqStart` mapping for old config compat
- `src/export/mwgConfig.js` — update export keys
- `src/ui/simulation/SimulationPanel.js` — update binding keys
- `src/ui/simulation/settings.js` — update key references
- `src/ui/simulation/actions.js` — update key references (if any)
- `src/presets/index.js` — update preset keys

---

### Step 6: Clean Up Remaining ABEC Schema Section

After steps 1-5, the entire `'ABEC'` section in `schema.js` should be empty or nearly empty. Remove the section header. Frequency params move to a `'Simulation'` group.

**Also remove from `src/config/index.js`:**
- All `ABEC.*` config key parsing that's no longer needed
- `abecSimType`, `abecSimProfile`, `abecAbscissa`, `abecMeshFrequency` from defaults

**Also remove from `src/export/mwgConfig.js`:**
- All `ABEC.*` export lines for removed params

---

### Step 7: Clean Up Config Import Compatibility

When importing old `.mwg` config files that contain now-removed params:
- `ABEC.SimType` → silently ignore (always free-standing)
- `ABEC.SimProfile` → silently ignore
- `Mesh.InterfaceResolution` / `Mesh.InterfaceOffset` / `Mesh.InterfaceDraw` / `Mesh.SubdomainSlices` → silently ignore
- `ABEC.F1` / `ABEC.F2` / `ABEC.NumFreq` → map to new `freqStart`/`freqEnd`/`numFreqs` keys
- Config parser should not error on unknown keys — verify this is the default behavior

---

### Step 8: Update Tests

- Remove deleted test files (step 1)
- Update `tests/config-roundtrip.test.js` — remove `abecSimProfile` assertions, update renamed param assertions
- Update `tests/export-gmsh-pipeline.test.js` — if it references ABEC params
- Run `npm test` to verify no regressions beyond pre-existing failures

---

## Verification

1. `npm test` — should pass at same level as before (36/46, minus removed ABEC tests)
2. Manual check: open app, verify simulation tab UI has no circsym/sim-type dropdowns
3. Manual check: "Export ABEC Project" button is gone
4. Manual check: "Export MSH" still works
5. Import an old config with `ABEC.SimType=1` — verify it loads without error
6. Run a BEM simulation — verify it still works (frequencies, polar config, mesh build all functional)

---

## Files Summary

### Delete entirely:
- `src/export/abecProject.js`
- `src/export/abecBundleValidator.js`
- `src/geometry/symmetry.js`
- `scripts/validate-abec-bundle.js`
- `docs/ABEC_PARITY_CONTRACT.md`
- `tests/abec-bundle-parity.test.js`
- `tests/abec-circsym.test.js`
- `tests/fixtures/abec/` (entire directory)

### Edit (major changes):
- `index.html` — remove ABEC button, sim-type dropdown, circsym controls
- `src/app/exports.js` — remove exportABECProject
- `src/app/App.js` — remove exportABECProject method
- `src/app/events.js` — remove ABEC button handler
- `src/config/schema.js` — remove ABEC section, rename freq params
- `src/config/index.js` — remove ABEC parsing, update defaults
- `src/export/mwgConfig.js` — remove ABEC export lines
- `src/ui/simulation/SimulationPanel.js` — remove circsym/sim-type bindings, rename freq keys
- `src/ui/simulation/settings.js` — remove circsym/sim-type logic
- `src/ui/simulation/actions.js` — remove sim-type validation, circsym reading
- `src/solver/waveguidePayload.js` — hardcode sim_type=2, remove interface params
- `server/app.py` — remove sim_type validation, remove interface params
- `src/presets/index.js` — update preset keys
- `tests/config-roundtrip.test.js` — update assertions

### Keep untouched:
- `src/ui/simulation/polarSettings.js` — used by BEM
- `server/solver/symmetry.py` — used by BEM solver
- `server/solver/waveguide_builder.py` — mesh builder (shared)
- `server/solver/solve_optimized.py` — BEM solver
- `src/app/exports.js` → `buildExportMeshFromParams()`, `exportMSH()` — shared
