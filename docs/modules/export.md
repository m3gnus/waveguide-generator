# Export Contract

## Scope

Primary files:

- `src/modules/export/useCases.js`
- `src/modules/export/index.js`
- `src/ui/simulation/exports.js`
- `src/ui/fileOps.js`

## Responsibilities

- Produce local STL, profile CSV, and MWG config exports.
- Orchestrate OCC-backed mesh export through the backend.
- Build completed-task result bundles across settings-selected formats.

## Runtime Contract

- OCC-authored `.msh` export uses `POST /api/mesh/build` only.
- `/api/mesh/build` returns `.msh` plus optional STL text, not `.geo`.
- Result bundle formats are addressed by stable string IDs:
  - `png`
  - `csv`
  - `json`
  - `txt`
  - `polar_csv`
  - `impedance_csv`
  - `vacs`
  - `stl`
  - `fusion_csv`
- Manual completed-task export runs the full configured bundle.
- Auto-export runs once per completion transition and records `autoExportCompletedAt`.

## Folder Workspace Behavior

- When a folder workspace is active, completed-task bundle files write into `<workspace>/<jobId>/`.
- If folder write access is unavailable, result bundle writes fall back to the standard save/download path.

## Regression Coverage

- `tests/export-gmsh-pipeline.test.js`
- `tests/export-module.test.js`
- `tests/csv-export.test.js`
- `tests/simulation-export-bundle.test.js`
