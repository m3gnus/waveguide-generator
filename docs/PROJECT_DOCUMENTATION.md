# Waveguide Generator: Technical Documentation

This is the primary technical source of truth and supersedes prior standalone docs for:
- OCC/legacy `.msh`/`.geo` generation details
- Geometry and meshing implementation analysis

## 1. Overview

This project is a web application for acoustic horn design and simulation. It combines:

- Parametric horn geometry generation (OSSE/R-OSSE)
- Real-time Three.js rendering
- A canonical mesh payload contract shared by export and simulation paths
- A Python FastAPI backend for BEM job execution

Primary entry points:

- Frontend app boot: `src/main.js`
- Frontend coordinator: `src/app/App.js`
- Backend API: `server/app.py`

## 2. Runtime Architecture

### 2.1 Frontend layers

- `src/app/`
  - App orchestration (`App.js`)
  - Scene/render lifecycle (`scene.js`)
  - Config import/export adapters (`configImport.js`, `exports.js`, `mesh.js`)
- `src/ui/`
  - Parameter and simulation UI behavior
  - File operations and user feedback
- `src/geometry/`
  - Horn formulas, mesh generation, surface tags, transforms, and canonical payload assembly
- `src/export/`
  - File-format writers (MSH, GEO, CSV, STL, ABEC) and ABEC bundle validation
- `src/solver/`
  - Backend client and mesh payload validation on the frontend side
- `src/state.js`
  - App state, history (undo/redo), and persistence to localStorage

### 2.2 Backend layers

- `server/app.py`
  - FastAPI application
  - Request models and endpoint handlers
  - Job queue/status/result memory store
- `server/solver/`
  - BEM solver adapter and frequency-domain solve logic
  - Mesh preparation and optional Gmsh refinement
  - Directivity and supporting utilities

### 2.3 Backend dependency matrix (P3-1)

Runtime support is explicitly version-gated in `server/solver/deps.py`:

| Component | Supported range | Required for |
|---|---|---|
| Python | `>=3.10,<3.14` | backend runtime |
| gmsh Python package | `>=4.10,<5.0` | `/api/mesh/build` |
| bempp-cl | `>=0.4,<0.5` | `/api/solve` |
| legacy `bempp_api` fallback | `>=0.3,<0.4` | `/api/solve (legacy fallback)` |

Behavior:
- `GET /health` reports live dependency status under `dependencies`.
- `POST /api/mesh/build` returns `503` when Python/gmsh are outside the supported matrix.
- `POST /api/solve` returns `503` when Python/bempp are outside the supported matrix.
- `POST /api/mesh/generate-msh` can still work through system `gmsh` CLI when Python gmsh is unavailable.

## 3. Core Data Flow

End-to-end flow:

1. User changes a parameter in UI.
2. `GlobalState` updates and emits `state:updated`.
3. `App` rebuilds parameter UI and requests render.
4. `src/app/scene.js` calls `buildGeometryArtifacts(...)` from `src/geometry/pipeline.js`.
5. The same canonical geometry path is reused for:
   - Viewport mesh rendering
   - Export generation
   - Simulation mesh payload
6. Simulation run in `src/ui/simulation/actions.js` requests mesh via `simulation:mesh-requested` and submits to backend.
7. Backend processes async job (`/api/solve`) and frontend polls `/api/status/{job_id}` until `/api/results/{job_id}` is ready.

## 4. Canonical Mesh Payload Contract

The frontend sends this mesh structure to backend:

```json
{
  "vertices": [0.0, 0.0, 0.0],
  "indices": [0, 1, 2],
  "surfaceTags": [2],
  "format": "msh",
  "boundaryConditions": {
    "throat": { "type": "velocity", "surfaceTag": 2, "value": 1.0 },
    "wall": { "type": "neumann", "surfaceTag": 1, "value": 0.0 },
    "mouth": { "type": "robin", "surfaceTag": 1, "impedance": "spherical" }
  },
  "metadata": {}
}
```

Implemented validation points:

- Frontend contract validation: `src/solver/index.js` (`validateCanonicalMeshPayload`)
- Backend request validation: `server/app.py` + Pydantic models
- Backend mesh integrity checks (index bounds, tag consistency): `server/solver/mesh.py`

### 4.1 Surface tag semantics

Canonical mapping used across frontend/backend:

- `1` = walls
- `2` = source
- `3` = secondary domain
- `4` = interface

Source of truth on frontend: `src/geometry/tags.js`

## 5. Geometry and Mesh Pipeline

### 5.0 Two separate mesh pipelines

| Pipeline | Purpose | Engine | Files |
|---|---|---|---|
| **JS viewport mesh** | Real-time Three.js rendering | JS geometry engine | `src/geometry/engine/`, `src/geometry/pipeline.js` |
| **Python OCC export mesh** | BEM acoustic simulation (.msh) | Gmsh Python OCC API | `server/solver/waveguide_builder.py` |

The viewport mesh and the export mesh are **not** derived from the same geometry representation.
The viewport path uses a fast triangulated approximation; the export path uses Gmsh OCC BSplines
so that Gmsh receives real curved geometry to mesh.

### 5.1 JS viewport mesh (render path)

Key files:

- `src/geometry/engine/`: modular geometry engine
  - `profiles/`: OSSE/R-OSSE/guiding-curve math and validation
  - `mesh/`: angle/slice generation, horn shell, enclosure, source, freestanding wall shell
  - `buildWaveguideMesh.js`: top-level geometry builder
- `src/geometry/waveguide.js`: compatibility re-export facade over `src/geometry/engine/`
- `src/geometry/pipeline.js`:
  - `buildGeometryArtifacts(...)`
  - `buildCanonicalMeshPayload(...)`

Important behavior:

- A single geometry path feeds render/export/simulation to reduce drift.
- `rearShape` is removed from active geometry generation.
- `wallThickness` is applied only for freestanding horns (`encDepth = 0`):
  - normal-offset shell thickening (one wall-thickness from horn surface)
  - rear disc at `throatY - wallThickness`
- Interface/enclosure tagging is derived from mesh groups and interface offset.

### 5.2 Python OCC export mesh (acoustic simulation path)

Key file: `server/solver/waveguide_builder.py` -> `build_waveguide_mesh(params)`

- Triggered by `POST /api/mesh/build` for R-OSSE and OSSE configs.
- Uses Gmsh OCC API: one BSpline wire per phi angle (longitudinal, throat-to-mouth), then
  `addThruSections` between adjacent phi wires to form ruled surface strips.
- Longitudinal wires are correct for non-circular ATH profiles where axial length L varies with phi.
  Cross-sectional wires (one ring per t-index) would be non-planar when L(phi) is non-constant,
  producing twisted geometry — this is why longitudinal wires are used instead.
- Produces smooth curved surfaces that Gmsh can mesh correctly.
- Returns `.msh` text (plus optional `stl`), not `.geo`.
- Falls back to JS `.geo` path if Gmsh Python API is unavailable (`503`).

**Outer geometry logic** — geometry presence is controlled by enclosure/wall parameters, not `sim_type`:

| Condition | Outer geometry generated |
|---|---|
| `encDepth > 0` | Enclosure cabinet box (5 surfaces: 4 walls + back panel + front baffle rim with mouth cutout) |
| `encDepth == 0` and `wallThickness > 0` | Horn wall shell (offset outer surface + rear wall disc + mouth rim ring) |
| Both 0 | Bare horn only — no outer geometry |

`sim_type` controls the BEM radiation condition only (1 = infinite baffle, 2 = free-standing) and has
no effect on which outer geometry surfaces are generated.

**Physical surface groups:**

| Tag | Group | Description |
|---|---|---|
| SD1G0 (BEM group 1) | `horn` | Inner horn surface |
| SD1D1001 (BEM group 2) | `source` | Throat source disc |
| SD2G0 (BEM group 3) | `exterior` | Outer wall shell or enclosure surfaces (present when wallThickness>0 or encDepth>0) |


### 5.3 Mesh Parameters

All `Mesh.*` config parameters from the MWG specification:

| Config Key | Type | Default | Status |
|---|---|---|---|
| `Mesh.Quadrants` | int | `1` | Implemented. Controls BEM symmetry quadrant selection (1, 12, 14, 1234). |
| `Mesh.AngularSegments` | int | `120` | Implemented. Profiles around waveguide, must be multiple of 4. |
| `Mesh.LengthSegments` | int | `40` | Implemented. Axial slices along horn length. |
| `Mesh.CornerSegments` | int | `4` | Implemented. Corner profiles for rounded rectangle morph. |
| `Mesh.ThroatSegments` | int | `0` | Implemented. Slices for throat extension (fallback when resolutions are equal). |
| `Mesh.ThroatResolution` | float | `5` | Implemented. BEM mesh resolution at z=0 [mm]. Controls both axial slice distribution and Gmsh element sizes. |
| `Mesh.MouthResolution` | float | `8` | Implemented. BEM mesh resolution at z=Length [mm]. Interpolated with ThroatResolution. |
| `Mesh.SubdomainSlices` | int[] | last slice | Implemented. Grid slice indices for subdomain interfaces. |
| `Mesh.InterfaceOffset` | float[] | `0` | Implemented. Forward protrusions of interfaces [mm]. |
| `Mesh.InterfaceDraw` | float[] | `0` | Implemented. Forward-draw depths of interfaces [mm]. |
| `Mesh.InterfaceResolution` | float | — | Parsed from config. Not yet wired to Gmsh mesh size fields. |
| `Mesh.WallThickness` | float | `5` | Implemented. Wall thickness for freestanding horns [mm]. |
| `Mesh.RearResolution` | float | `10` | Implemented. Rear wall mesh resolution [mm]. Gmsh export only. |
| `Mesh.RearShape` | int | `1` | Legacy, removed. Always flat disc. Old configs tolerated. |
| `Mesh.ZMapPoints` | — | — | Not implemented. Axial distances set by resolution mapping. |

User-specified segment counts and resolution values pass through 1:1 to backend mesh generation (no scaling).

### 5.4 Validated geometry/meshing findings

Current validated findings from implementation and tests:

- Dual-pipeline architecture is intentional: JS viewport tessellation and Python OCC meshing are separate by design.
- Angular sampling + quadrant handling (`1`, `12`, `14`, `1234`) is implemented on both JS and Python paths.
- Enclosure stitching now uses angularly aligned perimeter generation (not legacy perimeter remeshing heuristics).
- Adaptive phi is intentionally constrained to full-circle horn-only meshes.
- Quality diagnostics exist (`validateMeshQuality`) but are currently diagnostic-first, not strict blocking.

Known implementation risks still tracked:

- Symmetry split-plane seam handling remains heuristic and can be sensitive to near-plane numerical drift.
- Simulation-grade quality policies (strict/warn/off style mesh gating) are not fully standardized across all paths.
- Cross-path parity coverage (JS tessellation invariants vs OCC outputs) still needs expansion.

## 6. Export System

Frontend export entry points live in `src/app/exports.js`.

Supported exports:

- STL: binary STL from Three.js geometry
- GEO: full Gmsh geometry + generated BEMPP starter Python script
- MSH: tagged mesh export using canonical tag rules
- ABEC: ZIP bundle with project files, mesh, `bem_mesh.geo`, and results templates
- CSV: horn profile coordinate export
- MWG config text

### Mesh export routing

For R-OSSE and OSSE configs:
- `exportMSH` and `exportABECProject` call `buildExportMeshFromParams(...)` in `src/app/exports.js`.
- Before building the payload, `detectGeometrySymmetry(preparedParams)` from `src/geometry/symmetry.js`
  is called to auto-detect the maximum valid symmetry domain and overwrite `quadrants` accordingly.
- This POSTs formula parameters to `POST /api/mesh/build`.
- Backend constructs BSpline OCC geometry and returns `.msh`.
- The ABEC bundle still includes `bem_mesh.geo`, generated by the JS geo builder for parity/debugging.
- If the backend returns `503`, falls back to the legacy JS `.geo` path.

**Symmetry auto-detection** (`src/geometry/symmetry.js`):
- Samples phi-dependent parameters (R, a for R-OSSE; a for OSSE) at 8 angles.
- Checks XZ-plane symmetry: f(φ) ≈ f(-φ) for all samples.
- Checks YZ-plane symmetry: f(φ) ≈ f(π-φ) for all samples.
- Returns the smallest valid domain: `'1'` (quarter) → `'14'` or `'12'` (half) → `'1234'` (full).
- Conservative: returns `'1234'` if a guiding curve type other than 0 is active (gcurveType ≠ 0).
- Constant expressions always yield quarter symmetry `'1'`.

Legacy / fallback path (503 fallback only):
- `exportMSH` and `exportABECProject` call `buildExportMeshWithGmsh(...)`.
- Frontend generates `.geo` via `buildGmshGeo(...)` from `src/export/gmshGeoBuilder.js`.
- The `.geo` is sent to `POST /api/mesh/generate-msh` for Gmsh meshing.
- The same generated `.geo` is bundled as `bem_mesh.geo`.

Mesh export invariant:
- `.msh` output is always Gmsh-authoritative — no direct frontend triangle-to-`.msh` serialization.
- User-specified segment counts and resolution values pass through 1:1 (no scaling).

ABEC ZIP assembly uses `JSZip` in browser and includes simulation configuration files generated by:

- `src/export/abecProject.js`
- `src/export/abecBundleValidator.js` (validation/parity contract checks)

## 7. Backend API Contract

Base URL: `http://localhost:8000`

### `GET /health`

Returns health status, solver readiness, OCC builder readiness, and dependency matrix/runtime status.

### `POST /api/solve`

- Validates mesh shape and `surfaceTags` count against triangle count.
- Creates job ID and schedules async solve task.
- Returns `{"job_id": "..."}` on success.

### `POST /api/mesh/build`

- **Preferred path for R-OSSE configs.**
- Accepts formula parameters (`WaveguideParamsRequest`) directly — no `.geo` text required.
- Backend constructs BSpline OCC geometry using Gmsh Python API.
- Returns `{ "msh": str, "generatedBy": "gmsh-occ", "stats": { nodeCount, elementCount }, "stl"?: str }`.
- Returns `503` if Gmsh Python API is unavailable.
- Returns `422` if `formula_type` is not `"R-OSSE"` or `"OSSE"`, or `msh_version` is not `"2.2"` or `"4.1"`.
- Implemented in `server/solver/waveguide_builder.py`.

### `POST /api/mesh/generate-msh`

- **Legacy / fallback path (OSSE or when Python API unavailable).**
- Generates `.msh` from submitted `.geo` text using backend gmsh.
- Returns `{ "msh": str, "generatedBy": "gmsh", "stats": { nodeCount, elementCount } }`.
- Returns `503` if gmsh is unavailable.
- Implemented in `server/solver/gmsh_geo_mesher.py`.

### `POST /api/stop/{job_id}`

- Cancels queued/running job.

### `GET /api/status/{job_id}`

- Returns status and progress.

### `GET /api/results/{job_id}`

- Returns simulation results when status is complete.

## 8. Testing and Verification

Primary automated checks:

- Frontend/unit tests: `npm test`
- ATH parity checks (when `_references/testconfigs` exists): `npm run test:ath` (strict infra mode by default)
- ATH parity infrastructure policy:
  - preflight probes and mesh smoke checks run for backend/python/cli methods before ATH comparisons.
  - failures print reproducible local fix steps for backend reachability, gmsh Python runtime, and gmsh CLI wrapper setup.
  - strict mode is enabled by default; set `ATH_PARITY_STRICT_INFRA=0` only when intentionally probing infrastructure behavior.
- ABEC bundle parity validation: `npm run test:abec <bundle-path>`
- Backend tests (project Python): `cd server && ../.venv/bin/python -m unittest discover -s tests`
- NPM backend script: `npm run test:server`
- Frontend production bundle: `npm run build`

## 9. Operational Notes

- Frontend dev server is Express static hosting on port `3000` (`scripts/dev-server.js`).
- Combined start script runs frontend + backend together (`scripts/start-all.js`).
- Backend server uses FastAPI/Uvicorn on port `8000` (`server/app.py`).

## 10. Known Constraints and Risks

- ATH parity for GEO/STL/MSH across full reference set is still incomplete.
- ABEC parity contract currently anchors to `260112aolo1` and should be expanded if new ATH references are added.
- End-to-end runtime simulation verification needs broader fixture coverage.

These are release-quality risks, not blockers for local development flow.

## 11. File Map (Key Files)

- Frontend boot: `src/main.js`
- App coordinator: `src/app/App.js`
- Global state: `src/state.js`
- Geometry pipeline: `src/geometry/pipeline.js`
- Surface tags: `src/geometry/tags.js`
- Export orchestration: `src/app/exports.js`
- ABEC validator/parity contract: `src/export/abecBundleValidator.js`, `docs/ABEC_PARITY_CONTRACT.md`
- Symmetry auto-detection: `src/geometry/symmetry.js`
- Simulation UI actions: `src/ui/simulation/actions.js`
- Solver client: `src/solver/index.js`
- Backend API: `server/app.py`
- Backend mesh handling: `server/solver/mesh.py`
- **Python OCC mesh builder**: `server/solver/waveguide_builder.py`
- JS .geo text builder (legacy): `src/export/gmshGeoBuilder.js`
- Backend tests: `server/tests/`
