# Docs Agent Guide

## Responsibilities
- Keep `docs/PROJECT_DOCUMENTATION.md` aligned with current runtime behavior.
- Keep ABEC parity and contract docs synchronized with export/runtime outputs.
- Document architecture decisions and constraints without inventing unimplemented features.

## Invariants
- Do not state that `/api/mesh/build` returns `.geo` unless code changes to do that.
- Document ABEC bundles as including `bem_mesh.geo` (current required parity contract).
- Keep canonical surface-tag mapping consistent with code (`1/2/3/4`).
- Clearly separate:
  - JS geometry/payload path
  - OCC `.msh` builder path
  - legacy `.geo -> .msh` meshing endpoint

## Required Validation Before Merge
- `npm test`
- `npm run test:server`
- `npm run test:abec` for ABEC-structure or parity-doc changes
- Spot-check endpoint docs against:
  - `server/app.py`
  - `src/app/exports.js`
  - `server/solver/deps.py`

## Known Pitfalls
- Historical docs often mix legacy `.geo` behavior with current OCC `.msh` behavior.
- UI-visible exports differ from internal/export-library functions; document both explicitly.
- Avoid roadmap language in architecture docs unless clearly labeled as future work.
