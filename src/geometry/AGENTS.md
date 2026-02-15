# Geometry Agent Guide

## Responsibilities
- Own param preparation and expression handling for geometry inputs.
- Build waveguide mesh topology in `src/geometry/engine/*`.
- Produce canonical simulation payload via `buildGeometryArtifacts` / `buildCanonicalMeshPayload`.
- Maintain surface-tag assignment and boundary-condition defaults.

## Invariants
- Group ranges (`groups.*.start/end`) are triangle indices, not raw index-buffer offsets.
- Canonical `surfaceTags.length` must equal `indices.length / 3`.
- Source tag (`2`) must be present in every simulation payload.
- Interface tags (`4`) are only applied when:
  - enclosure geometry exists, and
  - `interfaceOffset` parses to a positive value.
- Split-plane triangles must be removed for reduced quadrant payloads (`1`, `12`, `14`).
- Adaptive-phi mode is only valid for full-circle horn-only geometry (no enclosure/wall).

## Required Tests Before Merge
- `tests/mesh-payload.test.js`
- `tests/geometry-artifacts.test.js`
- `tests/enclosure-regression.test.js`
- `tests/geometry-quality.test.js`
- `tests/viewport-tessellation-consistency.test.js`
- `tests/app-mesh-integration.test.js`

## Known Pitfalls
- `interfaceOffset` / `interfaceDraw` may be comma-separated lists; avoid scalar-only assumptions.
- Enclosure and freestanding-wall builders assume consistent ring topology.
- Source tagging must come from explicit source geometry; do not reintroduce positional fallback tagging.
- Coordinate transforms for export (`mapVertexToAth`) are downstream; do not bake ATH transforms into base mesh generation.
