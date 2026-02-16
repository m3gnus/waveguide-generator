# Export Agent Guide

## Responsibilities
- Build export artifacts (`.geo`, `.msh` request payloads, ABEC text files, CSV, STL helpers).
- Orchestrate backend meshing routes from `src/app/exports.js`.
- Enforce ABEC bundle structure and validation (`abecBundleValidator`).

## Invariants
- `/api/mesh/build` is `.msh`-first (`generatedBy: "gmsh-occ"`); do not expect `.geo` in its response.
- ABEC export uses `/api/mesh/build` only; a `503` is surfaced as an export failure (no JS `.geo` fallback).
- ABEC export must include:
  - `Project.abec`, `solving.txt`, `observation.txt`
  - `<basename>.msh`
  - `bem_mesh.geo`
  - `Results/coords.txt`, `Results/static.txt`
- `.msh` used for solve/export remains Gmsh-authored (backend API/CLI), not direct frontend triangle serialization.
- ABEC export applies symmetry auto-detection (`detectGeometrySymmetry`) before meshing.

## Required Tests Before Merge
- `tests/export-gmsh-pipeline.test.js`
- `tests/gmsh-geo-builder.test.js`
- `tests/abec-bundle-parity.test.js`
- `tests/abec-circsym.test.js`
- `tests/csv-export.test.js`

## Known Pitfalls
- Do not let `Project.abec` mesh references drift from actual zip entry names.
- `allowDefaultPolars` behavior differs for imported infinite-baffle configs; preserve this branch logic.
- `geoText` is mandatory in ABEC bundles even when `.msh` comes from OCC.
- Backend health checks run before meshing; keep error messages actionable for local startup failures.
