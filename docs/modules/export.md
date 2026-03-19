# Export Module Contract

## Scope

**Core module files**:
- `src/modules/export/useCases.js` — local file export logic (STL, CSV, config)
- `src/modules/export/index.js` — public module interface

**UI coordination files**:
- `src/ui/simulation/exports.js` — completed-task bundle coordination
- `src/ui/fileOps.js` — file I/O helpers

## Core Responsibilities

- **Local exports**: Generate STL (binary), profile/slice CSV, and MWG config text files
- **OCC mesh export**: Orchestrate `POST /api/mesh/build` requests (parameter normalization, response handling)
- **Result bundles**: Coordinate multi-format exports for completed simulation jobs (PNG, CSV, JSON, STL, polar data, VACS, etc.)

## Runtime Contract

**OCC mesh export**:
- Uses `POST /api/mesh/build` exclusively (no `.geo` fallback)
- Backend returns `.msh` file + optional STL text (never returns `.geo`)
- Request normalized through `DesignModule.occExportParams()`

**Result bundle formats** (settings-driven string IDs):
- `png` — directivity plot image (Matplotlib server-side rendering)
- `csv` — frequency response table
- `json` — full results object
- `txt` — result summary text
- `polar_csv`, `impedance_csv` — polar/impedance data exports
- `vacs` — VACS file format
- `stl` — horn geometry STL
- `fusion_csv` — Fusion 360-compatible CSV

**Bundle execution**:
- Manual export: runs full configured bundle format set
- Auto-export: runs once per job completion → records `autoExportCompletedAt` marker

**Folder workspace behavior**:
- Backend workspace root is the canonical export target (`/api/export-file` + optional `workspace_subdir`)
- On supporting browsers, a selected folder handle is an in-browser direct-write optimization
- Bundle exports use job label/base name as subdirectory (`<workspace>/<jobLabel>/`) for both direct-write and backend-write paths
- Bundle artifact names are deterministic within each generation folder:
  - `csv` → `<jobLabel>_results.csv`
  - `json` → `<jobLabel>_results.json`
  - `txt` → `<jobLabel>_report.txt`
  - `polar_csv` → `<jobLabel>_polar.csv`
  - `impedance_csv` → `<jobLabel>_impedance.csv`
  - `vacs` → `<jobLabel>_spectrum.txt`
  - `png` → `<jobLabel>_<chartKey>.png`
  - `stl` → `<jobLabel>.stl`
  - `fusion_csv` → `<jobLabel>_profiles.csv` and `<jobLabel>_slices.csv`
- Folder task manifests/index entries now persist against the same generation folder naming contract (job identity stays in manifest/index `id`)
- Generation folders also include `waveguide.project.v1.json` as a user-facing artifact index for selected exports and script snapshots (plus simulation-owned raw-results/mesh entries written at completion)
- If direct-write or backend-write fails: app falls back to browser save/download
- Workspace contract covers manual + auto-bundles ONLY (not unrelated generated artifacts)

## Test Coverage

- `tests/export-module.test.js` — module interface
- `tests/export-gmsh-pipeline.test.js` — OCC mesh orchestration
- `tests/csv-export.test.js` — CSV export correctness
- `tests/simulation-export-bundle.test.js` — bundle coordination
- `tests/generation-artifacts.test.js` — deterministic artifact naming rules
