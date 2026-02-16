# ABEC Export Parity Contract

This is the current enforced ABEC bundle contract.

Validation/enforcement:
- `src/export/abecBundleValidator.js`
- `scripts/validate-abec-bundle.js`
- `tests/abec-bundle-parity.test.js`

Reference anchor:
- `_references/testconfigs/260112aolo1/ABEC_FreeStanding`

## Required bundle structure

Every generated ABEC bundle must contain:

- `Project.abec`
- `solving.txt`
- `observation.txt`
- `<basename>.msh` (name referenced by `Project.abec`)
- `bem_mesh.geo`
- `Results/coords.txt`
- `Results/static.txt`

## Contract rules

### 1. Project wiring

`Project.abec` must provide:
- `[Solving]` with `Scriptname_Solving=solving.txt`
- `[Observation]` with `C0=observation.txt`
- `[MeshFiles]` with `C0=<basename>.msh,M1`

Referenced files must exist in the bundle.

### 2. Solving semantics

`solving.txt` must include:
- `Control_Solver`
- `MeshFile_Properties`
- `Driving "S1001"`
- mesh includes for `SD1G0` and `SD1D1001`

Mode-specific behavior:
- `ABEC_FreeStanding`: must not include `Infinite_Baffle`
- `ABEC_InfiniteBaffle`: must include `Infinite_Baffle`

### 3. Mesh physical groups

The referenced `.msh` must contain `$PhysicalNames` entries for:
- `SD1G0`
- `SD1D1001`

Any group referenced by `Mesh Include ...` in `solving.txt` must exist in mesh physical names.

### 4. Observation structure

`observation.txt` must include:
- `Driving_Values`
- `Radiation_Impedance`
- at least one `BE_Spectrum` polar block

Each `BE_Spectrum` must provide:
- `GraphHeader`
- `PolarRange`
- `Inclination`

## Notes

- ABEC bundles include `bem_mesh.geo` generated in frontend export (`buildGmshGeo`).
- `/api/mesh/build` remains `.msh`-only (plus optional `stl`) and does not return `.geo`.
- Future parity expansion items live in `docs/FUTURE_ADDITIONS.md`.
