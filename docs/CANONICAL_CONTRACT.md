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

Phase 3 runtime status:
- All triangles are emitted from the JS geometry engine into explicit subsets (`inner_wall`, `outer_wall`, `mouth_rim`, `rear_cap`, `horn_wall`, `throat_disc`, `enc_front`, `enc_side`, `enc_rear`, `enc_edge`).
- `src/geometry/tags.js` provides deterministic mapping from these identities to mesh sizing classes and solver boundary classes.

### 1.2 Mesh sizing classes

Mesh sizing classes are meshing semantics only (not solver BC semantics):
- `horn_inner_axial`
- `horn_rear_domain`
- `throat_source_region`
- `enclosure_front`
- `enclosure_rear`
- `enclosure_edge`

Phase 3 runtime status:
- JS runtime maps geometry identities to logical `MESH_SIZING_CLASS` constants internally via `tags.js`.
- OCC meshing uses numeric resolution fields directly (`throat_res`, `mouth_res`, `rear_res`, `enc_front_resolution`, `enc_back_resolution`).

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

## 3. Frontend Payload Decision (Phase 3)

Decision:
- Frontend canonical payload remains numeric-tag-first (`vertices`, `indices`, `surfaceTags`) for downstream generic simulation pipelines.
- Face identities are exposed through `meshData.groups` and mapped via `src/geometry/tags.js`.
- OCC meshing consumes raw prepared parameters only; it does not consume a frontend-generated mesh contract.

## 4. Authoritative Normalization Spec (Current Runtime)

### 4.1 Angular segments

- Geometry mesh generation (`src/geometry/engine/buildWaveguideMesh.js` + `src/geometry/engine/mesh/angles.js`):
  - Round to integer.
  - Minimum 4.
  - If not divisible by 4, snap up to the nearest multiple of 8 for ring construction.
- OCC simulation request normalization (`src/modules/design/index.js`):
  - `prepareOccSimulationParams(...)` sets `angularSegments = max(20, round(value))`.
- OCC export request normalization (`src/modules/design/index.js`):
  - `prepareOccExportParams(...)` snaps angular segments to multiples of 4 (minimum 20).
- Waveguide OCC payload mapping (`src/solver/waveguidePayload.js`):
  - Expects already-normalized integer `angularSegments` and maps it to `n_angular`.
  - Throws on missing/invalid OCC-required fields instead of normalizing them locally.

### 4.2 Length segments

- Geometry mesh generation:
  - Round to integer.
  - Minimum 1 for internal tessellation (`lengthSegments`).
- OCC simulation/export request normalization (`src/modules/design/index.js`):
  - `prepareOccSimulationParams(...)` and `prepareOccExportParams(...)` set `lengthSegments = max(10, round(value))`.
- Waveguide OCC payload mapping (`src/solver/waveguidePayload.js`):
  - Expects already-normalized integer `lengthSegments` and maps it to `n_length`.
  - Throws on missing/invalid OCC-required fields instead of normalizing them locally.

### 4.3 Quadrants

- Frontend OCC request normalization (`src/modules/design/index.js`) accepts canonical values `1`, `12`, `14`, `1234`; otherwise attempts numeric coercion; fallback `1234`.
- Waveguide OCC payload mapping (`src/solver/waveguidePayload.js`) expects normalized integer `quadrants` and maps it directly.
- `/api/solve` validates the OCC-adaptive request and builds the queued solve request with full-domain `quadrants=1234` at the submission boundary.
- Simulation runner enforces full-domain `1234` again before OCC build.

### 4.4 Enclosure resolution fields

- Frontend OCC payload fields:
  - `enc_front_resolution`, `enc_back_resolution` are string fields.
  - Defaults: `"25,25,25,25"` and `"40,40,40,40"`.
  - Defaults and export scaling are applied by `prepareOccSimulationParams(...)` / `prepareOccExportParams(...)`.
  - Values are forwarded as strings by `buildWaveguidePayload(...)`, which requires the fields to be present.

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
