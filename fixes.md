# ATH Rebuild Fixes (Updated)

Last updated: 2026-02-08

This document tracks what is still left to do after the contract-first rebuild pass.

---

## Completed in this pass

1. Removed CAD/OpenCascade and STEP runtime path.
2. Added canonical mesh payload contract end-to-end:
   `mesh = { vertices, indices, surfaceTags, format, boundaryConditions, metadata }`.
3. Locked canonical tag mapping in frontend/backend:
   `1=walls`, `2=source`, `3=secondary`, `4=interface`.
4. Added backend request validation for malformed mesh payloads.
5. Aligned solver spaces to source tag `2` and fail-fast on missing source elements.
6. Centralized mm<->m conversion helpers in backend solver.
7. Added CircSym UI/config/export behavior:
   `abecSimProfile=-1` => 3D, `>=0` => CircSym.
8. Switched ABEC export to ZIP bundle with folder structure and fixed internal filenames.
9. Added BEMPP starter script generation and wired GEO export to full GEO writer.
10. Fixed CSV profile closure and 1/10 scaling.
11. Added frontend and backend regression tests for key contract behaviors.

---

## Remaining release blockers (P0)

### P0.1 ATH parity is still failing across reference configs

Current result from:
`node scripts/ath-compare.js _references/testconfigs /tmp/ath-generated`

Observed:
1. GEO mismatch on many configs (axis/offset and spline structure differences).
2. STL mismatch on many configs (mesh topology/count differences).
3. MSH mismatch on many configs (physical group count/content differences).

Required work:
1. Define exact parity targets per artifact:
   - strict byte parity or semantic parity thresholds.
2. Add deterministic parity checkpoints for:
   - profile samples,
   - mesh counts,
   - physical tag counts.
3. Iterate export/mesh transforms until reference gates pass for a minimum representative set:
   - at least 2 OSSE + 2 R-OSSE configs first,
   - then full `_references/testconfigs` sweep.

Files:
1. `scripts/ath-compare.js`
2. `src/export/msh.js`
3. `src/export/profiles.js`
4. `src/geometry/meshBuilder.js`
5. `src/geometry/enclosure.js`
6. `src/app/params.js`

---

### P0.2 MSH physical-group parity and rear-closure behavior need hard validation

Current risk:
1. Canonical tags are now enforced, but reference `.msh` files still differ in PhysicalNames count and distribution.
2. Freestanding back-wall behavior was moved to canonical payload generation and needs reference confirmation.

Required work:
1. Validate tag distribution per config against reference intent:
   - source region,
   - rigid wall region,
   - optional enclosure/interface regions.
2. Confirm back-wall generation rules for:
   - freestanding (`encDepth=0`) with `wallThickness > 0`,
   - enclosure mode (`encDepth>0`) with no duplicate rear closure.
3. Add regression tests for physical group counts and source-tag coverage.

Files:
1. `src/simulation/payload.js`
2. `src/export/msh.js`
3. `server/solver/mesh.py`
4. `tests/mesh-payload.test.js`

---

### P0.3 ABEC/BEMPP export needs reference-level validation, not just feature presence

Current status:
1. ZIP structure and extra files are implemented.
2. GEO + starter script exports are implemented.
3. Semantic parity vs reference ABEC outputs is not yet validated end-to-end.

Required work:
1. Compare generated ABEC ZIP contents against `_references/testconfigs/*/ABEC_*`.
2. Confirm `solving.txt` values for:
   - `Dim=3D` vs `Dim=CircSym`,
   - symmetry fields,
   - mesh alias and script references.
3. Confirm `observation.txt` polar block semantics against reference templates.
4. Run real Gmsh parse/mesh generation on exported GEO for representative cases.
5. Execute a BEMPP tutorial workflow using one exported mesh/script pair.

Files:
1. `src/app/exports.js`
2. `src/export/abecProject.js`
3. `src/export/msh.js`
4. `src/export/bempp.js`
5. `_references/beminfo/*`

---

### P0.4 End-to-end simulation API/UI flow needs integrated runtime verification

Current status:
1. Contract validation and unit tests are in place.
2. Full browser-to-backend runtime verification with real solver has not been completed in this pass.

Required work:
1. Verify full flow from UI:
   - `/health`
   - `/api/solve`
   - `/api/status/{id}`
   - `/api/results/{id}`
   - `/api/stop/{id}`
2. Verify payload shape from UI is exactly canonical in live runs.
3. Verify smoothing operates post-solve without resubmitting jobs.
4. Capture one successful run artifact set for regression reference.

Files:
1. `src/ui/simulation/actions.js`
2. `src/ui/simulation/mesh.js`
3. `src/solver/index.js`
4. `server/app.py`
5. `server/solver/*`

---

## High-priority follow-up (P1)

### P1.1 Expand automated regression coverage

Add tests for:
1. ABEC ZIP folder/file names and internal file text checks.
2. GEO output `Save "<mesh>.msh"` consistency.
3. Parser round-trip for additional ATH fixtures.
4. Backend validation edge-cases:
   - indices out of range,
   - zero source tags,
   - malformed boundary condition objects.

Files:
1. `tests/*.test.js`
2. `server/tests/*.py`

---

### P1.2 Remove or align stale legacy solver-side helper code

Current risk:
1. `src/solver/bemMeshGenerator.js` still exists with old tag assumptions and is not part of canonical flow.

Required work:
1. Either delete unused file(s) or refactor to canonical tag contract.
2. Add import-smoke test to prevent accidental usage of stale path.

Files:
1. `src/solver/bemMeshGenerator.js`
2. `src/ui/simulation/mesh.js`
3. `tests/` (new smoke test)

---

## Validation commands (current baseline)

1. Frontend build:
   `npm run build`
2. Frontend tests:
   `npm test`
3. Backend tests:
   `npm run test:server`
4. Parity smoke (currently failing, must be improved):
   `node scripts/ath-compare.js _references/testconfigs /tmp/ath-generated`

---

## Current findings snapshot (2026-02-08)

This snapshot records the latest verified status after the recent P0 implementation pass.

### Verified green

1. `npm run build` passes.
2. `npm test` passes (15 tests).
3. `npm run test:server` passes (6 tests).
4. `python3 scripts/validate-geo.py _references/testconfigs /tmp/ath-generated` passes for representative set:
   - `0414je3`
   - `250729solanaS2`
   - `0416ro1`
   - `260112aolo1`
5. Historical (script removed during legacy cleanup on 2026-02-08):
   - `bash scripts/runtime-api-smoke.sh` previously passed
   - covered `/health`, `/api/solve`, `/api/status/{id}`, `/api/results/{id}`, `/api/stop/{id}` success/cancel paths
   - wrote runtime artifact to `_references/runtime_smoke/latest.json`

### Still failing (release blockers)

1. `node scripts/ath-compare.js _references/testconfigs /tmp/ath-generated` still fails across all 9 configs.
2. `node scripts/abec-compare.js _references/testconfigs /tmp/ath-generated-abec` still fails across all 9 configs.

### High-confidence findings from failure analysis

1. Current parity comparators are still effectively topology-strict for core geometry:
   - GEO currently fails immediately on point-count mismatch.
   - STL currently fails immediately on triangle-count mismatch.
   - MSH currently fails immediately on node/element-count mismatch.
   This is incompatible with the intended semantic+deterministic parity gate when generator topology differs.
2. Generated GEO/mesh coordinate frame is still materially offset vs ATH references in multiple fixtures (large bbox/axis deltas observed), indicating remaining transform/alignment gaps.
3. ABEC interface-mode detection is too narrow for reference intent:
   - At least `0416ro1` reference `solving.txt` expects interface sections (`SD2G0`/`I1-2`) while generated output does not currently enable interface mode.
4. `scripts/abec-compare.js` has a prep inconsistency:
   - it does not apply the same `Scale` length normalization path used elsewhere, which can skew downstream ABEC text parity (including offsets/dimensions) for scaled fixtures.
5. Infinite-baffle offset handling was patched to use geometry-derived axial max in export path, but full ABEC parity has not yet been re-baselined after all related changes.

### Immediate next actions to clear P0

1. Align comparator policy with semantic gates:
   - GEO: compare entity semantics/tolerances without hard point-count equality.
   - STL: compare shape metrics (bbox/centroid/tolerances) without hard triangle-count equality.
   - MSH: compare physical names/tag presence and deterministic tag distribution policy; avoid hard node/element equality as blocker.
2. Fix interface-enable rule to match reference behavior (especially enclosed free-standing cases like `0416ro1`).
3. Unify param preparation in `scripts/abec-compare.js` with app/export prep (including `Scale` handling and shared defaults).
4. Re-run full validation matrix after each fix:
   - build/tests/server tests
   - ATH compare full sweep
   - ABEC compare full sweep
   - runtime smoke

---

## Definition of done for this fixes list

All items in **P0** are complete when:
1. canonical payload/tag/unit contracts remain enforced,
2. ABEC/BEMPP exports validate against references,
3. end-to-end simulation flow is verified in live runtime,
4. ATH comparison is green for agreed reference set (initial subset + full sweep).
