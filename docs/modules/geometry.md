# Geometry Contract

## Scope

Primary files:

- `src/geometry/pipeline.js`
- `src/geometry/tags.js`
- `src/geometry/engine/*`
- `src/modules/geometry/index.js`

## Responsibilities

- Evaluate horn/enclosure geometry inputs.
- Build viewport mesh artifacts.
- Build the canonical simulation payload.
- Preserve deterministic surface-tag semantics.

## Canonical Payload Contract

Required shape:

- `vertices`
- `indices`
- `surfaceTags`
- `format`
- `boundaryConditions`
- `metadata`

Invariants:

- `surfaceTags.length === indices.length / 3`
- Source tag `2` must exist
- Tag mapping remains code-owned in `src/geometry/tags.js`
- Interface tag `4` is only valid when enclosure/interface geometry is actually present

## Important Runtime Notes

- Frontend simulation payload topology is full-domain and is not trimmed by `quadrants`.
- JS canonical payload is a contract/validation artifact; active simulation meshing is OCC-adaptive in the backend.
- Adaptive phi tessellation is only for full-circle horn-only render usage.
- Outer build mode is exclusive: enclosure (`encDepth > 0`) or freestanding wall shell (`encDepth == 0 && wallThickness > 0`) or bare horn.
- Enclosure generation is OSSE-only. `R-OSSE` with `encDepth > 0` is rejected.
- When `encEdge > 0`, the JS enclosure builder adds front and rear axial roundover strips in addition to the rounded/chamfered sidewall corners; `enc_edge` covers those roundover strips.
- Freestanding wall thickness is generated from the horn surface's local 3D normals. The rear `throat_return` transition continues the outer back-side slope into a back plate located `wallThickness` behind the throat plate, rather than using a straight cylindrical drop.
- When `morphTarget` is enabled and `morphWidth` / `morphHeight` are unset, target extents are derived from the current slice.

## Regression Coverage

- `tests/mesh-payload.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/enclosure-regression.test.js`
- `tests/geometry-quality.test.js`
- `tests/morph-implicit-target.test.js`
- `tests/geometry-module.test.js`
