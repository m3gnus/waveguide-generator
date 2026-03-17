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
- When active: manual exports → selected folder root; bundles → `<workspace>/<jobId>/`
- If folder write fails: app clears workspace selection, falls back to browser save/download
- Workspace contract covers manual + auto-bundles ONLY (not unrelated generated artifacts)

## Test Coverage

- `tests/export-module.test.js` — module interface
- `tests/export-gmsh-pipeline.test.js` — OCC mesh orchestration
- `tests/csv-export.test.js` — CSV export correctness
- `tests/simulation-export-bundle.test.js` — bundle coordination
