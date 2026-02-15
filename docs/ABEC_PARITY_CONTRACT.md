# ABEC Export Parity Contract (Phase 2)

## Reference Anchor

Primary ATH reference bundle:

- `_references/testconfigs/260112aolo1/ABEC_FreeStanding`

This folder defines the baseline structure and semantic contract for ABEC export parity.

## Required Bundle Structure

Every generated ABEC bundle must contain:

- `Project.abec`
- `solving.txt`
- `observation.txt`
- `<basename>.msh` (name referenced by `Project.abec`)
- `bem_mesh.geo`
- `Results/coords.txt`
- `Results/static.txt`

## Contract Rules

### 1. Project wiring

`Project.abec` must provide:

- `[Solving]` with `Scriptname_Solving=solving.txt`
- `[Observation]` with `C0=observation.txt`
- `[MeshFiles]` with `C0=<basename>.msh,M1`

Referenced files must exist in the bundle.

### 2. Solving file semantics

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

## `bem_mesh.geo` Policy (Decision)

Policy: **include `bem_mesh.geo` in every ABEC bundle**.

Rationale:

- Matches ATH folder expectations and improves parity tooling.
- Provides a deterministic debug artifact even when runtime meshing uses OCC (`/api/mesh/build`).
- Does not alter `.msh` authority: solver/export still use Gmsh-authored `.msh` as source of truth.

Implementation note:

- `bem_mesh.geo` is generated in frontend export (`buildGmshGeo`) from prepared params.
- `/api/mesh/build` remains `.msh`-only (plus optional `stl`) and does not return `.geo`.

## Automation / Validation Hooks

- Validator module: `src/export/abecBundleValidator.js`
- CLI validator: `scripts/validate-abec-bundle.js`
- Regression tests: `tests/abec-bundle-parity.test.js`
- Golden fixtures:
  - `tests/fixtures/abec/ABEC_FreeStanding`
  - `tests/fixtures/abec/ABEC_InfiniteBaffle`

