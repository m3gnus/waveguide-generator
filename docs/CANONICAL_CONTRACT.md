# Canonical Contract (Phase 0 Freeze)

Last updated: March 11, 2026

Purpose:
- Freeze current runtime contract before structural refactors in `docs/ARCHITECTURE_CLEANUP_PLAN.md`.
- Eliminate drift between docs, runtime code, and tests.

This document is normative for Phase 0.

## 1. Classification Layers

### 1.1 Geometry face identity

Canonical identity vocabulary to preserve during cleanup:

- Freestanding thickened horn:
  - `inner_wall`
  - `outer_wall`
  - `mouth_rim`
  - `rear_cap`
  - `throat_disc`
- Horn with enclosure:
  - `horn_wall`
  - `throat_disc`
  - `enc_front`
  - `enc_side`
  - `enc_rear`
  - `enc_edge`

Phase 0 runtime status:
- Only `throat_disc` is explicitly separated in JS runtime (`meshData.groups.source` -> source-tagged triangles).
- All other triangles are aggregated into a single wall class and are not split into explicit face identities yet.

### 1.2 Mesh sizing classes

Mesh sizing classes are meshing semantics only (not solver BC semantics):
- `horn_inner_axial`
- `horn_rear_domain`
- `throat_source_region`
- `enclosure_front`
- `enclosure_rear`
- `enclosure_edge`

Phase 0 runtime status:
- OCC meshing uses numeric resolution fields directly (`throat_res`, `mouth_res`, `rear_res`, `enc_front_resolution`, `enc_back_resolution`).
- JS runtime does not yet emit explicit mesh sizing class metadata.

### 1.3 Solver boundary classes

- `RIGID_WALL`
- `ACOUSTIC_SOURCE`
- `IMPEDANCE_APERTURE` (reserved)
- `SYMMETRY` (reserved)

Phase 0 runtime status:
- Active frontend/runtime submission classes are `RIGID_WALL` and `ACOUSTIC_SOURCE`.
- `throat_disc` maps to `ACOUSTIC_SOURCE`.
- All non-source triangles map to `RIGID_WALL`.

## 2. Numeric Tag Contract

Tag constants (shared vocabulary):
- `1` = wall (`SD1G0`)
- `2` = source (`SD1D1001`)
- `3` = secondary domain (`SD2G0`)
- `4` = interface (`I1-2`)

Phase 0 runtime behavior by pipeline:
- JS canonical simulation payload (`src/geometry/pipeline.js`, `src/geometry/tags.js`):
  - Emits only tags `1` and `2`.
  - Does not emit tags `3` or `4`.
- OCC mesh build output (`/api/mesh/build`):
  - Emits tags `1`, `2`, and optional `3` when exterior surfaces exist.
  - Does not emit tag `4`.
- OCC-adaptive `/api/solve` path (`server/services/simulation_runner.py`):
  - Re-maps all non-source tags to `1` before solver mesh preparation.

Required invariants:
- `surfaceTags.length === indices.length / 3`
- At least one source-tagged triangle (`2`) must exist before solve submission.

## 3. Frontend Payload Decision (Phase 0)

Decision:
- Frontend canonical payload remains numeric-tag-first (`vertices`, `indices`, `surfaceTags`) and does not yet include explicit face identity metadata.

Deferred to later phases:
- Emitting explicit geometry face identities alongside numeric tags.
- Explicit identity -> sizing class -> solver class mapping tables in runtime payloads.

## 4. Authoritative Normalization Spec (Current Runtime)

### 4.1 Angular segments

- Geometry mesh generation (`src/geometry/engine/buildWaveguideMesh.js` + `src/geometry/engine/mesh/angles.js`):
  - Round to integer.
  - Minimum 4.
  - If not divisible by 4, snap up to the nearest multiple of 8 for ring construction.
- Waveguide OCC payload (`src/solver/waveguidePayload.js`):
  - `n_angular = max(20, round(angularSegments))`.
  - No additional divisibility snapping in this function.
- OCC export module pre-normalization (`src/modules/export/index.js`):
  - Angular segments are pre-snapped to multiples of 4 with minimum 20 before calling `buildWaveguidePayload(...)`.

### 4.2 Length segments

- Geometry mesh generation:
  - Round to integer.
  - Minimum 1 for internal tessellation (`lengthSegments`).
- Waveguide OCC payload:
  - `n_length = max(10, round(lengthSegments))`.
- OCC export module pre-normalization:
  - Minimum 10 before payload build.

### 4.3 Quadrants

- Frontend OCC payload builder accepts canonical values `1`, `12`, `14`, `1234`; otherwise attempts numeric coercion; fallback `1234`.
- `/api/solve` OCC-adaptive route validates request and coerces OCC build quadrants to full-domain `1234`.
- Simulation runner enforces full-domain `1234` again before OCC build.

### 4.4 Enclosure resolution fields

- Frontend OCC payload fields:
  - `enc_front_resolution`, `enc_back_resolution` are string fields.
  - Defaults: `"25,25,25,25"` and `"40,40,40,40"`.
  - Values are forwarded as strings by `buildWaveguidePayload(...)`.
- OCC export module applies scale-aware numeric-string transformation for these fields before payload build.

### 4.5 Unit metadata

- Canonical frontend simulation payload metadata:
  - `units: "mm"`
  - `unitScaleToMeter: 0.001`
- OCC-adaptive solve path enriches mesh metadata with the same unit contract.

## 5. Contract Lock Tests

Primary tests that lock this contract:
- `tests/mesh-payload.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/waveguide-payload.test.js`
- `server/tests/test_api_validation.py`

