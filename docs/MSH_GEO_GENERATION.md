# How `.geo` and `.msh` Are Generated

This document describes the exact generation flows currently implemented in this repository.

## Overview: two export paths

| Path | When used | Geometry type | Entry point |
|---|---|---|---|
| **Python OCC builder** (preferred) | R-OSSE configs, when Gmsh Python API is installed | BSpline curves + OCC ThruSections | `POST /api/mesh/build` |
| **JS .geo builder** (legacy / fallback) | OSSE configs, or when Python API unavailable | Flat polyhedral triangulated surfaces | `POST /api/mesh/generate-msh` |

The Python OCC path produces better meshes because Gmsh receives parametric curved geometry
and can mesh it correctly. The JS path works but gives Gmsh flat triangles, limiting mesh quality.

---

## 1) Primary Export Path (UI: Export MSH / Export ABEC Project)

Entry points:
- `src/app/exports.js` -> `exportMSH(app)`
- `src/app/exports.js` -> `exportABECProject(app)`

Both call:
- `buildExportMeshWithGmsh(app, preparedParams, options)`

### 1.1 Parameter preparation for Gmsh export

`buildExportMeshWithGmsh` first applies `buildGmshExportParams(preparedParams)` in `src/app/exports.js`.

Current defaults:
- `segmentDivisor: 1`
- `resolutionScale: 1`
- `minAngularSegments: 20`
- `minLengthSegments: 10`

Transform rules:
- `angularSegments = round_to_multiple_of_4(max(minAngularSegments, round(baseAngular / segmentDivisor)))`
- `lengthSegments = max(minLengthSegments, round(baseLength / segmentDivisor))`
- `throatResolution *= resolutionScale`
- `mouthResolution *= resolutionScale`
- `rearResolution *= resolutionScale`
- `encFrontResolution` / `encBackResolution` are scaled if numeric lists
- `wallThickness` is clamped to positive fallback for freestanding export

With `segmentDivisor=1` and `resolutionScale=1`, user-specified values pass through to the `.geo` file as-is. The min-segment guards (20 angular, 10 length) remain as safety nets.

### 1.2 Geometry artifact generation

`buildGeometryArtifacts(...)` from `src/geometry/pipeline.js` builds:
- `mesh` (raw geometry arrays)
- `simulation` payload (canonical surface tags and metadata)

Important simulation payload details:
- `surfaceTags` are assigned by `buildSurfaceTags(...)` in `src/geometry/tags.js`
  - `1=SD1G0`, `2=SD1D1001`, optional `3=SD2G0`, `4=I1-2`
- split-plane symmetry triangles are removed for `quadrants` (`14`, `12`, `1`)
- metadata includes:
  - `ringCount`
  - `lengthSteps`
  - `fullCircle`
  - `verticalOffset`
  - `hasEnclosure`
  - `interfaceEnabled`

### 1.3 `.geo` text generation

`buildGmshGeo(preparedParams, mesh, simulation, options)` in `src/export/gmshGeoBuilder.js` generates the `.geo` text.

Exact behavior:
1. Validates geometry arrays and tag counts.
2. Converts simulation vertices to ATH coordinates with:
   - `transformVerticesToAth(...)` in `src/geometry/transforms.js`
   - mapping is `[x, z + verticalOffset, y]`.
3. Emits fixed Gmsh options (algorithm, msh version, etc.).
4. Emits all points with per-point local mesh sizes.
5. Emits curve/surface topology:
   - structured horn quads as `Ruled Surface`
   - structured source fan as `Plane Surface`
   - any remaining triangles as `Plane Surface`
6. Emits physical groups with canonical names:
   - `SD1G0`, `SD1D1001`, optionally `SD2G0`, `I1-2`
7. Emits mesh-size fields:
   - axial `MathEval` gradient
   - distance/threshold refinement fields (source/front/back/rear)
   - `Background Field = Min(...)`
8. Appends `Mesh 2;`.

Output:
- `geoText` (string)
- `geoStats` (counts and resolved resolutions)

### 1.4 `.msh` generation is Gmsh-authoritative

Client call:
- `generateMeshFromGeo(...)` in `src/solver/client.js`
- HTTP `POST` to `/api/mesh/generate-msh`

Server endpoint:
- `server/app.py` -> `generate_mesh_with_gmsh(request: GmshMeshRequest)`

Validation on server:
- `geoText` must be non-empty
- `mshVersion` must be `2.2` or `4.1`

Actual meshing implementation:
- `server/solver/gmsh_geo_mesher.py` -> `generate_msh_from_geo(...)`
- writes temp `input.geo`
- if Python Gmsh API is available:
  - opens `.geo`
  - sets `Mesh.MshFileVersion`, `Mesh.Binary`, `Mesh.SaveAll=0`
  - `gmsh.model.mesh.generate(2)`
  - writes `output.msh`
- otherwise runs system `gmsh` CLI:
  - `gmsh input.geo -2 -format msh2|msh4 -save_all 0 -o output.msh`

Response payload returned to UI:
- `msh` (text)
- `generatedBy: "gmsh"`
- `stats: { nodeCount, elementCount }`

### 1.5 What is saved by export actions

`exportMSH(app)`:
- saves only returned `.msh` text.

`exportABECProject(app)`:
- creates zip folder (`ABEC_FreeStanding` or `ABEC_InfiniteBaffle`) containing:
  - `Project.abec`
  - `solving.txt`
  - `observation.txt`
  - `<basename>.msh` (Gmsh-generated)
  - `bem_mesh.geo` (from `buildGmshGeo`)
  - `<basename>_bempp.py`
  - `Results/coords.txt`
  - `Results/static.txt`

## 2) ATH Parity Script Path

Entry point:
- `scripts/ath-parity.js` -> `runParity(...)`

For each config:
1. Parse config and prepare params.
2. Build geometry artifacts.
3. Build `.geo` via `buildGmshGeo(...)`.
4. Generate `.msh` using `generateMshWithGmsh(...)`:
   - mode `auto` by default:
     - backend API first (`http://localhost:8000` by default)
     - fallback Python Gmsh API local call
     - fallback `gmsh` CLI local call
5. Compare generated `.msh` with ATH reference `.msh`:
   - node/element counts
   - physical groups
   - per-tag triangle counts
   - vertex-cloud distance metrics

Environment controls:
- `ATH_PARITY_BACKEND_URL` (default `http://localhost:8000`)
- `ATH_PARITY_GMSH_MODE` (`auto`, `backend`, `python`, `cli`)

## 3) Related but not primary path

There is a direct mesh serializer in `src/export/msh.js` (`exportMSH(...)`), but the current UI `.msh` and ABEC bundle flow uses the Gmsh-authoritative backend path described above.

## 4) Mesh Parameter Reference (MWG Specification)

All `Mesh.*` config parameters and their role in mesh generation:

| Config Key | Type | Default | Description |
|---|---|---|---|
| `Mesh.Quadrants` | int | `1` | Portion of 3D mesh for BEM analysis: `1` = quadrant 1 only (x>=0, y>=0), `12` = quadrants 1+2 (y>=0), `14` = quadrants 1+4 (x>=0), `1234` = full mesh. Controls angle selection in `mesh/angles.js` and split-plane triangle removal in `pipeline.js`. |
| `Mesh.AngularSegments` | int | `120` | Total number of calculated profiles around the waveguide. Must be a multiple of 4 (auto-adjusted). Determines the number of vertices per ring in the horn mesh. Used by `mesh/angles.js` (snapped to multiple of 4 or 8 for symmetry). |
| `Mesh.LengthSegments` | int | `40` | Total number of axial slices along the horn length. Controls how many rings of vertices are generated. Each ring pair creates a strip of quad faces (2 triangles each). |
| `Mesh.CornerSegments` | int | `4` | Number of angular profiles reserved for the corner region of a rounded rectangle when `Morph.TargetShape=1`. Only affects morphed (rectangular) mouths. Used by `mesh/angles.js` to distribute extra angle samples in the corner arc. |
| `Mesh.ThroatSegments` | int | `0` | Number of axial slices reserved for the throat extension region (if `Throat.Ext.*` is set). Only takes effect when `throatResolution == mouthResolution` (resolution-based distribution takes priority). Used by `mesh/sliceMap.js`. |
| `Mesh.ThroatResolution` | float | `5` | Nominal BEM mesh resolution at z=0 [mm]. Dual role: (1) controls non-uniform axial slice distribution in internal mesh via `sliceMap.js` (concentrates slices near throat when smaller than `MouthResolution`), (2) sets per-point mesh sizes and MathEval background field base value in `.geo` export. |
| `Mesh.MouthResolution` | float | `8` | Nominal BEM mesh resolution at z=Length [mm]. Element sizes between throat and mouth are smoothly interpolated. Same dual role as `ThroatResolution`. |
| `Mesh.SubdomainSlices` | int[] | last slice | Indices of grid slices where subdomain interfaces are placed. Default behavior uses the last slice. Set to empty to disable (single exterior subdomain). Used by `mesh/horn.js` for vertex z-offsets at interface boundaries. |
| `Mesh.InterfaceOffset` | float[] | `0` | Forward protrusions of subdomain interfaces [mm]. Array length should match `SubdomainSlices`. Used by `mesh/enclosure.js` to create interpolated interface rings. |
| `Mesh.InterfaceDraw` | float[] | `0` | Forward-draw depths of subdomain interfaces [mm]. Array length should match `SubdomainSlices`. Added to interface offset for total z-displacement at interface slices. |
| `Mesh.InterfaceResolution` | float | — | Mesh resolution near subdomain interfaces. Parsed from config but not yet wired to .geo mesh size fields. |
| `Mesh.WallThickness` | float | `5` | Wall thickness for freestanding horns (when enclosure depth=0) [mm]. Builds a normal-offset shell and rear disc behind the throat. |
| `Mesh.RearResolution` | float | `10` | Rear wall mesh resolution for freestanding horns [mm]. Controls a Distance/Threshold refinement field for vertices near zMin in the `.geo` export. |
| `Mesh.RearShape` | int | `1` | Legacy parameter — removed from active generation. The rear is always a flat disc. Old configs containing this value are tolerated but the value is not exported. |

### Parameters not implemented

- `Mesh.ZMapPoints`: Referenced in the MWG spec for controlling distances between individual axial slices. Not implemented; axial distribution is controlled by the resolution-based or uniform mapping in `sliceMap.js`.

### TODO

- **`Mesh.InterfaceResolution`**: Parsed from config (`src/config/parser.js`) but not wired to any mesh size control. Needs research into how subdomain interfaces are treated in this project (when they are needed, when not) and then implementation of a Distance/Threshold Gmsh field for interface vertices (tag 4 / "I1-2") in `src/export/gmshGeoBuilder.js`, following the pattern used by `encFrontResolution`/`encBackResolution`. Also needs: schema entry in `src/config/schema.js`, UI entry in `src/ui/paramPanel.js`, round-trip export in `src/export/mwgConfig.js`.

---

## 5) Python OCC Builder Path (preferred for R-OSSE)

Entry point:
- `src/app/exports.js` -> `buildExportMeshFromParams(app, preparedParams, options)`

This path bypasses the JS `.geo` builder entirely. Instead of converting a triangle soup to `.geo`
text, it sends formula parameters directly to the backend and lets Python + Gmsh OCC construct the
geometry natively from BSpline wires.

### 5.1 When this path is used

`exportMSH` and `exportABECProject` in `src/app/exports.js` route to this path when:
- `preparedParams.type === 'R-OSSE'`

If the backend returns `503` (Gmsh Python API unavailable), the call automatically falls back to
`buildExportMeshWithGmsh` (the legacy JS `.geo` path, section 1 above).

For OSSE formula configs, the old JS path is always used (OSSE support in Python builder is
deferred).

### 5.2 Parameter mapping

`buildPythonBuilderPayload(preparedParams, mshVersion)` in `src/app/exports.js` maps JS camelCase
parameters to the `WaveguideParamsRequest` snake_case fields:

| JS field | Python field | Notes |
|---|---|---|
| `type` | `formula_type` | Always `"R-OSSE"` for this path |
| `R` | `R` | ATH R-OSSE expression string |
| `a` | `a` | ATH R-OSSE expression string |
| `r0` | `r0` | Throat radius [mm] |
| `a0` | `a0` | Throat half-angle [deg] |
| `k` | `k` | Flare exponent |
| `r` | `r` | Roundedness |
| `b` | `b` | Asymmetry |
| `m` | `m` | Mouth modifier |
| `q` | `q` | Profile exponent |
| `angularSegments` | `n_angular` | Sampling grid (shape fidelity) |
| `lengthSegments` | `n_length` | Sampling grid (shape fidelity) |
| `quadrants` | `quadrants` | 1, 12, 14, or 1234 |
| `throatResolution` | `throat_res` | BEM element size at z=0 [mm] |
| `mouthResolution` | `mouth_res` | BEM element size at z=Length [mm] |
| `rearResolution` | `rear_res` | Rear wall element size [mm] |
| `wallThickness` | `wall_thickness` | Freestanding wall thickness [mm] |
| `simType` | `sim_type` | 1=infinite baffle, 2=freestanding |
| — | `msh_version` | `"2.2"` (default) or `"4.1"` |

Note: `n_angular`/`n_length` control the number of **OCC profile points** (shape sampling). They
are independent from the BEM element sizes (`throat_res`, `mouth_res`). More sampling points give
Gmsh better curve information but do not directly set triangle counts.

### 5.3 Backend endpoint

`POST /api/mesh/build` in `server/app.py`

- Validates `formula_type === "R-OSSE"` (returns 422 for unsupported types)
- Validates `msh_version` is `"2.2"` or `"4.1"` (returns 422 otherwise)
- Returns `503` if Gmsh Python API is unavailable (`WAVEGUIDE_BUILDER_AVAILABLE = False`)
- Runs `build_waveguide_mesh(params)` in `asyncio.to_thread` to avoid blocking the event loop
- Returns `{ "geo": str, "msh": str, "generatedBy": "gmsh-occ", "stats": { nodeCount, elementCount } }`

### 5.4 Python OCC geometry construction

`server/solver/waveguide_builder.py` -> `build_waveguide_mesh(params: dict)`

The builder acquires `gmsh_lock` (from `gmsh_geo_mesher.py`) to prevent concurrent Gmsh API calls.

Geometry construction steps:
1. Evaluate R-OSSE expressions `R(p)` and `a(p)` at each angular position `phi`.
2. For each phi, compute the profile curve: `(t_array, R_expr, a_deg, r0, a0, k, r, b, m, q)` -> `(x_axial, y_radial)`.
3. Convert `n_length` sample points along each profile to 3D `(x, y, z)` coordinates.
4. For each adjacent pair of profile rings, build a `gmsh.model.occ.addBSpline` wire + `addThruSections` surface strip.
5. Cap the throat end with a flat disc (`addDisk`).
6. For freestanding configs (`sim_type == 2`):
   - Build outer wall surface via normal-offset points and another set of ThruSections strips.
   - Build rear wall disc.
   - Build mouth rim surface connecting inner and outer mouth rings.

### 5.5 Mesh sizing

`_configure_mesh_size(gmsh, config, inner_points, surface_groups)`:

- **MathEval gradient**: A `MathEval` background field interpolates element size from
  `throat_res` at the throat end to `mouth_res` at the mouth, as a function of the axial
  coordinate. This ensures the radial mesh density follows the horn's axial progression.
- **Restrict fields**: Each physical surface group gets a `Restrict` field that limits the
  MathEval field to that group's surfaces. Rear wall surfaces use `rear_res`.
- **Background Field**: `Min(all Restrict fields)` so element sizes never exceed the target.

This approach is fundamentally different from the JS `.geo` builder, which assigns per-point mesh
sizes and uses `Distance`/`Threshold` fields based on tagged vertex subsets.

### 5.6 Physical groups (ABEC-compatible names)

`_assign_physical_groups(gmsh, surface_groups, sim_type)`:

| ABEC name | Tag | Surfaces |
|---|---|---|
| `SD1G0` | 1 | Inner horn wall (all ThruSections strips) |
| `SD1D1001` | 2 | Throat source disc |
| `SD2G0` | 3 | Outer wall + rear disc + mouth rim (sim_type==2 only) |
| `I1-2` | 4 | Subdomain interface surfaces (deferred, not yet implemented) |

### 5.7 Output from backend

The endpoint returns both `.geo` and `.msh` text:
- `geo`: OCC-format `.geo` script (written by `gmsh.write("output.geo")` after OCC build).
  This is a serialization of the OCC model in `.geo` format — it will contain `SetFactory("OpenCASCADE")`
  and parametric surface definitions, not flat triangles.
- `msh`: Text-format `.msh` file (version 2.2 or 4.1 as requested).

The `.geo` file returned here is **not** the same as the `.geo` file produced by `gmshGeoBuilder.js`
(section 1.3). The OCC `.geo` represents the actual parametric geometry, while the JS `.geo`
represents a flat polyhedral approximation.

### 5.8 What is saved by export actions (Python OCC path)

`exportMSH(app)` (R-OSSE):
- saves the `.msh` text returned by `/api/mesh/build`.

`exportABECProject(app)` (R-OSSE):
- creates zip folder containing:
  - `Project.abec`
  - `solving.txt`
  - `observation.txt`
  - `<basename>.msh` (from `/api/mesh/build`)
  - `bem_mesh.geo` (OCC `.geo` from `/api/mesh/build`)
  - `<basename>_bempp.py`
  - `Results/coords.txt`
  - `Results/static.txt`

### 5.9 Deferred features

Not yet supported in the Python OCC builder:
- **OSSE formula**: Only R-OSSE is implemented. OSSE configs fall back to the JS path.
- **Subdomain interfaces** (`SubdomainSlices`, `InterfaceOffset`, `InterfaceDraw`): Physical group `I1-2` is not yet constructed.
- **Morph** (rectangular target shape): Not yet ported.
- **Rollback** (throat rollback): Not yet ported.
- **Guiding curves**: Not yet ported.

