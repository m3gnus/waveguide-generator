# Waveguide Generator - Backlog

## Priority Issues

### 🔴 CRITICAL: OSSE Enclosure Simulations Still Produce Wrong Results
**Status**: Partially fixed, still broken
**Evidence**: Sound appears to radiate from all directions, including behind the enclosure. Simulation results differ significantly when using enclosure vs thickened waveguide (which works correctly).

**Recent Fixes Applied (e1feea3)**:
- ✅ Front baffle now creates annular surface with mouth hole (not sealed disc)
- ✅ current_profile now updated to front_wire so side walls connect from outer boundary
- ✅ Parallel workers now pass observation_distance_m to solver
- ✅ from_config now preserves physical_tags (domain_indices) for proper BEM source identification
- ✅ Fixed JS viewport ?? 25 NaN bug → || 25

**Status After Fixes**: Issue PERSISTS. These fixes addressed known bugs but the core problem remains undiagnosed.

**Remaining Hypotheses**:
1. **Symmetry reduction** may be broken, causing all simulations to behave as full models (no time savings, potential accuracy loss)
2. **Tesselation architecture** may have geometry/viewport/MSH separation issues
3. **Over-engineered safety mechanisms** in MSH generation causing suboptimal results
4. **Mesh resolution** - front/back roundovers have excessive resolution vs edges
5. **Observation distance origin** - may need to be mouth plane, not throat disc
6. **BEM formulation issue** - normals inverted, Burton-Miller not helping, domain_indices still not propagating despite fixes

**Next Steps**:
- [ ] Run A/B test: full model vs symmetry-reduced model at same frequencies to verify symmetry code correctness
- [ ] Verify mesh inspection reports (diagnostic OCC output matches final BEM mesh)
- [ ] Test observation distance at near-field (0.5m) vs far-field (2m) to detect origin issues
- [ ] Check if enclosure surfaces are inverted (normals pointing inward instead of outward)
- [ ] Verify BEM source identification - confirm throat disc is correctly tagged and detected

---

## Symmetry Detection & Reduction Module
**File**: `src/geometry/symmetry.js`
**Status**: Untested in production
**Concern**: Currently DISABLED (no evidence it provides time savings) and its correctness is unverified.

**Issues**:
- No A/B benchmark comparing full model vs symmetry-reduced model
- No validation that symmetry detection produces identical results to full model
- No timing profiling to justify the complexity

**Action Items**:
- [ ] Create test benchmark: same config, solve both full and symmetry-reduced, compare SPL/DI results
- [ ] Measure time savings at different frequency ranges
- [ ] If no benefit or bugs found, DISABLE or DELETE the symmetry module
- [ ] Document symmetry requirements if keeping (specific geometry patterns, validation rules)

---

## Tesselation Architecture Review
**Files**:
- `src/geometry/engine/buildWaveguideMesh.js` (viewport tesselation)
- `server/solver/waveguide_builder.py` (OCC geometry + MSH export)
- `src/export/gmshGeoBuilder.js` (legacy JS .geo export)
- `src/app/exports.js` (routing orchestration)

**Current Issue**: Geometry data flows differently for viewport vs MSH generation. No clean separation.

**Desired Architecture**:
```
Geometry Module (source of truth)
    ↓
    ├─→ Viewport Tesselation (uses geometry data, renders to Three.js)
    └─→ MSH Generation (uses same geometry data, exports to Gmsh/OCC)
```

**Current Reality**:
- Python OCC builder duplicates geometry logic (spline construction, surface generation)
- JS viewport has separate geometry engine
- No unified geometry representation shared between paths

**Action Items**:
- [ ] Document exact geometry data flow for each pipeline
- [ ] Identify duplicated logic between viewport and MSH generators
- [ ] Consider creating shared geometry model (or clarify why duplication is necessary)
- [ ] Add comments explaining why paths diverge (if intentional)

---

## MSH Resolution Issues
**File**: `server/solver/waveguide_builder.py` (_configure_mesh_size)
**Issue**: Front/back roundover/chamfer edges have excessive resolution

**Specific Problem**:
- Edges stretching corner-to-corner (e.g., front-top-right to front-top) have too many elements
- Should use mesh resolution from closest edge parameter (e.g., enclosure front resolution)
- Currently may be using default or interpolated values

**Action Items**:
- [ ] Review mesh size field logic for rounded corners
- [ ] Verify resolution respects enc_front_resolution and enc_back_resolution parameters
- [ ] Consider simpler resolution: chamfers should use parent surface resolution, not interpolate
- [ ] Test with explicit resolution settings and verify output

---

## Observation Distance Measurement Origin
**Files**:
- `server/solver/observation.py` (infer_observation_frame)
- `server/solver/solve_optimized.py` (_solve_single_frequency, line 348)

**Current Behavior**: Measured from `origin_center` (throat disc centroid)
**Potential Issue**: May need to be measured from mouth plane for correct far-field assumptions

**Details**:
- At 2m distance: throat vs mouth origin is ~120mm difference (6% error)
- At near-field (0.5m): error becomes ~20%
- BEM far-field assumptions (inverse-square law) break if origin is wrong

**Action Items**:
- [ ] Clarify correct measurement origin: throat disc or mouth plane?
- [ ] Run test at 0.5m distance with both origins, measure DI difference
- [ ] If mouth plane is correct, update infer_observation_frame to use mouth_center instead
- [ ] Document the measurement convention in comments

---

## Safety Mechanisms in MSH Generation
**Files**: `server/solver/waveguide_builder.py`, `server/solver/simulation_runner.py`
**Concern**: Over-engineered error handling may hide bugs rather than fix them

**Potential Issues**:
- Fallback to ruled surfaces when planar surfaces fail (lines 1693-1695, 1708-1710)
- removeDuplicateNodes() as mesh cleanup band-aid
- Physical group validation tolerances

**Question**: Should MSH generation be solid from the start with no fallbacks?

**Action Items**:
- [ ] Audit fallback code paths - are they ever triggered?
- [ ] If never triggered, remove them
- [ ] If triggered, investigate root cause instead of using fallback
- [ ] Ensure mesh validation is strict, not permissive

---

## Unit Scaling & Measurement Distance
**Status**: VERIFIED WORKING (as of commit 4b6a132)

**Unit Pipeline**:
1. Waveguide builder outputs mm (ATH convention)
2. mesh.py detects units via unitScaleToMeter metadata (mm → 0.001)
3. Frontend sends polarSettings.distance (meters)
4. BEM solver expects meters - conversion is correct

**Note**: Earlier "scale bug" (commit 4b6a132) fixed unitScaleToMeter calculation when scale ≠ 1.
- Grid from builder: mm scale
- User scale: affects Mesh.L but not mesh coordinates
- unitScaleToMeter must account for both

**Confirm**: The fix in 4b6a132 is correct and complete.

---

## Frontend: Measurement Distance Parameter
**Status**: PARTIALLY WORKING
**File**: `src/ui/settings/simulationManagementSettings.js`

**Issue**: Measurement Distance (m) control exists but unclear if it properly propagates to BEM solver.

**Current Flow** (needs verification):
```
UI Control (simulationManagementSettings.js)
  → polarSettings.distance
  → polarConfig.distance
  → exportABECProject()
  → _resolve_observation_distance_m()
  → BEM solver receives observation_distance_m
```

**Action Items**:
- [ ] Trace measurement distance from UI → solver in single-process AND parallel paths
- [ ] Verify default (2.0m) is applied when field is empty
- [ ] Verify safe-distance clamping (distance > mesh extent) works correctly
- [ ] Add UI feedback showing actual distance used in solver

---

## Frontend: Enclosure .js Nullish Coalescing Bug
**File**: `src/geometry/engine/mesh/enclosure.js:296-299`
**Status**: ✅ FIXED in e1feea3

Changed from:
```javascript
const sL = (parseFloat(params.encSpaceL) ?? 25) * scale;
```

To:
```javascript
const sL = (parseFloat(params.encSpaceL) || 25) * scale;
```

**Why**: `parseFloat(undefined)` returns `NaN` (not null), so `??` doesn't trigger. Must use `||`.

---

## UI: Move Help Tooltip to Parameter Label Hover

**Status**: Not started — researched and feasible

**Current behavior**:
- Each parameter row has a `?` button (class `control-help-trigger`) in the label row
- CSS `::after` tooltip displays on hover/focus of the `?` button
- The `ƒ` formula button lives in the input wrapper area

**Proposed change**:
- Remove the `?` button entirely
- Attach `data-help-text` directly to the `<label>` element
- Move CSS tooltip (`:hover::after`) from `.control-help-trigger` to `label[data-help-text]`
- Move the `ƒ` formula button into the label row (where `?` was)

**Feasibility**: Fully feasible — pure CSS `::after` tooltip works on any element. No new tooltip infrastructure needed.

**Files to change**:
- `src/ui/helpAffordance.js` — `createLabelRow()`: attach `data-help-text` to label instead of creating `?` button
- `src/ui/paramPanel.js` — `createControlRow()`: move `formula-info-btn` into label row, remove from input wrapper
- `src/style.css` — update selector from `button.control-help-trigger` to `label[data-help-text]`, add `position: relative; cursor: help`

**Action Items**:
- [ ] Update `createLabelRow()` in `helpAffordance.js` to set `data-help-text` on the label and skip `createHelpTrigger()`
- [ ] Move `formula-info-btn` creation into the label row in `createControlRow()` (paramPanel.js)
- [ ] Update CSS: retarget tooltip `::after` to `label[data-help-text]`, tune positioning (tooltip should appear below label row, not below button)
- [ ] Verify tooltip still works on hover and `cursor: help` provides affordance

---

## Test Failures
**Current Status**:
- 46 tests total: 36 pass, 10 fail (pre-existing as of 2026-02-10)
- Enclosure-regression tests failing
- gmsh-geo-builder test failing

**Action Items**:
- [ ] Investigate test failures - are they expected (known issues) or regressions?
- [ ] Update tests to match current behavior if intentional changes were made
- [ ] Or fix code if tests reveal actual bugs

---

## Investigation Checklist for OSSE Enclosure Bug

### Verify Fixes Applied:
- [ ] Read waveguide_builder.py lines 1697-1717: confirm current_profile = front_wire is present
- [ ] Read solve_optimized.py line 105-108: confirm observation_distance_m is passed to solve_frequencies
- [ ] Read solve_optimized.py line 198: confirm physical_tags passed to bempp_api.Grid
- [ ] Read enclosure.js lines 296-299: confirm || 25 (not ?? 25)

### Diagnose Remaining Problem:
- [ ] Enable OCC mesh diagnostics and compare visual geometry before/after recent fixes
- [ ] Run solver with throatTag=2 validation to confirm source detection
- [ ] Test near-field observation (0.5m) to isolate observation distance origin issue
- [ ] Check if enclosure surfaces have correct normal orientation in final BEM mesh
- [ ] Verify domain_indices are actually present in grid in both single and parallel paths
- [ ] A/B test: full model vs symmetry-reduced to check for symmetry bugs

### Performance:
- [ ] Profile solver time for a standard config
- [ ] Measure time savings from symmetry reduction (if enabled)
- [ ] Identify bottlenecks in MSH generation

---

## Notes for Next Session
- The primary bug (front baffle sealing horn) has been fixed
- Three supporting bugs have been fixed (domain_indices, observation_distance_m in workers, ?? 25)
- **Problem: Enclosure simulations STILL produce wrong results after these fixes**
- This suggests a deeper issue unrelated to the fixed bugs
- Most likely: observation distance origin is wrong, or normals are inverted, or BEM formulation has an issue
- Next step: run diagnostic tests with OCC mesh inspection + near-field observation distance testing
