# Export contracts mined from v1

This document treats `../Waveguide Generator` as the read-only v1 source. “Must” below records observed compatibility behavior; it does not settle the questions in the final section. The active export dispatcher exposes mesh, STEP, STL, profile CSV, and parameter-config paths (`src/modules/export/index.js:20-26`), while simulation bundles add result formats (`src/ui/simulation/exports.js:758-818`).

## Contract summary

| Path | Authoritative input/tier | Geometry and units | State source | v1 evidence |
|---|---|---|---|---|
| Browser STL | Local JS export tessellation, deliberately densified | Bare horn inner surface; model-coordinate millimetres transformed `(x,y,z) -> (x,-z,y)` | Explicit state passed by caller; job snapshot when job-bound | `src/modules/export/index.js:358-382`; `src/modules/export/useCases.js:54-67`; `src/ui/simulation/exports.js:72-89` |
| Server STEP | HornLab/OCC mesher CAD surface | Full four-quadrant acoustic inner surface; stats declare millimetres | Explicit state passed by caller; job snapshot when job-bound | `server/api/routes_mesh.py:107-181`; `server/solver/mesher_adapter.py:586-631`; `src/ui/simulation/exports.js:72-89` |
| Profile/Fusion CSV | Local JS ring tessellation | Bare horn inner surface; output centimetres, `(x,y,z) -> (x,z,y)` | Explicit state passed by caller; job snapshot when job-bound | `src/modules/export/index.js:405-460`; `src/export/profiles.js:12-51`; `src/ui/simulation/exports.js:72-89` |
| Parameter config | Serialized design parameters, not geometry | ATH/MWG-style text; no length conversion at this export layer | Explicit state passed by caller; job snapshot when job-bound | `src/modules/export/index.js:463-492`; `src/modules/export/useCases.js:113-125`; `src/ui/simulation/exports.js:72-89` |
| Mesher `.msh` build | Backend full-build mesh and exact response text | Solver mesh; backend canonical reader interprets coordinates as metres | Prepared state passed to backend | `src/modules/export/index.js:194-261`; `server/solver/mesher_adapter.py:478-497` |
| Job `.msh` artifact | Stored original artifact text | No client transformation | Stored job artifact | `server/api/routes_simulation.py:230-240` |
| Result bundle | Stored result object plus selected presentation state | Format-specific; see Result Contracts | Job result/snapshot when job-bound | `src/ui/simulation/exports.js:758-818`; `src/ui/simulation/exports.js:821-925` |

## Browser binary STL

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Source tier | Rebuilds geometry in the local JS `GeometryModule`; it does not serialize the viewport buffer or backend solve mesh. It enables adaptive-phi tessellation. | `src/modules/export/index.js:358-377` |
| Formula coverage | Local-geometry formulas only. A server-only formula fails loudly because the local engine could otherwise emit an OSSE-shaped substitute. | `src/modules/export/index.js:33-43` |
| Bodies | Includes the horn surface. Forces `encDepth=0`, `wallThickness=0`, `includeEnclosure=false`, and `omitSource=true`; therefore enclosure, wall shell, and source are excluded. | `src/modules/export/index.js:362-377` |
| Density | `lengthSegments = clamp(max(3*user,60),160)`; `angularSegments = clamp(max(2*user,100),240)` then rounded up to a multiple of four; `cornerSegments = clamp(max(2*user,4),12)`. | `src/geometry/tessellation.js:29-56` |
| Units | No scale factor is applied before writing floats, so the STL inherits local geometry units; the geometry pipeline labels those units millimetres. | `src/modules/export/index.js:378-382`; `src/geometry/pipeline.js:135-151` |
| Axes | Each vertex is mapped `(x,y,z) -> (x,-z,y)` before serialization. `verticalOffset` is disabled while preparing this export. | `src/modules/export/index.js:140-147`; `src/modules/export/useCases.js:54-61` |
| Winding/orientation | Index order is preserved through the transform. Each stored normal is recomputed as normalized `(v1-v0) × (v2-v0)`; a degenerate triangle keeps a zero normal. The active adaptive horn builder documents its order as counter-clockwise when viewed from inside the horn. | `src/export/stl.browser.js:35-60`; `src/geometry/engine/mesh/horn.js:385-425` |
| Encoding | Binary STL: 80-byte header, little-endian triangle count and `float32` records, zero attribute count. Model name is truncated to 79 characters. | `src/export/stl.browser.js:16-30`; `src/export/stl.browser.js:62-97` |
| Physical tags | None: STL records only triangle normals, vertices, and a zero attribute count. | `src/export/stl.browser.js:62-94` |
| Filename | `${baseName}.stl`; default base name is `waveguide`, and default header/model name is `MWG Horn`. | `src/modules/export/index.js:389-398`; `src/modules/export/index.js:503-539` |
| State | The use case requires a supplied state and prepares it with `applyVerticalOffset:false`. A job export must use its stored design snapshot and fails if absent; a non-job export reads the current editor state. | `src/modules/export/useCases.js:16-20`; `src/modules/export/useCases.js:54-67`; `src/ui/simulation/exports.js:72-89` |

OPEN — outward-versus-inward normals for every local surface combination need golden STL fixtures inspected by a solid validator. The builder documents viewing direction only for the adaptive horn path, not every emitted group (`src/geometry/engine/mesh/horn.js:385-425`).

## Server STEP inner surface

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Source tier | Calls `POST /api/mesh/step`; the server asks `hornlab-waveguide-mesher` for a STEP representation rather than converting browser triangles. | `src/modules/export/index.js:304-333`; `server/api/routes_mesh.py:107-181` |
| Domain and bodies | Forces all four quadrants and `step_body="inner_surface"`. It excludes enclosure, wall thickness, source rear cap, and enclosure-related surfaces. | `server/api/routes_mesh.py:109-115`; `server/api/routes_mesh.py:160-167` |
| CAD topology | Builds ring wires through B-splines and connects adjacent wires with ruled, degree-one `ThruSections`; `makeSolid=false`, so the artifact is an open bounded loft/shell rather than a closed solid. | `server/solver/mesher_adapter.py:519-578` |
| Density | First applies the same smooth-export densification as STL, then restores the caller's original `lengthSegments`; angular and corner segmentation remain densified. | `src/modules/export/index.js:292-302`; `src/geometry/tessellation.js:29-56` |
| Units | Adapter statistics label point coordinates and bounds as millimetres. | `server/solver/mesher_adapter.py:606-631` |
| Axes | OPEN — the public adapter does not state a named STEP axis mapping. Freeze it with a non-symmetric reference horn and a golden CAD coordinate/bounds fixture; do not infer it from the browser STL transform. | `server/solver/mesher_adapter.py:586-631`; `src/modules/export/index.js:140-147` |
| Orientation | OPEN — ruled loft construction and wire ordering are visible, but no stable outward-normal/orientation promise is declared. Evidence needed: face-normal and seam-orientation checks in a STEP-capable CAD kernel. | `server/solver/mesher_adapter.py:519-578` |
| Physical tags | None are exposed in the STEP response; the response contains STEP text, generator identity, and stats. | `server/api/routes_mesh.py:168-181` |
| Filename | `${baseName}.step`; picker accepts `.step` and `.stp`, but the generated suffix is `.step`. | `src/modules/export/index.js:341-350` |
| State | State is imported with `applyVerticalOffset:false`. Job-bound exports require the job design snapshot; non-job exports use the live editor. | `src/modules/export/useCases.js:74-93`; `src/ui/simulation/exports.js:72-89` |

## Profile and Fusion CSV

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Source tier | Rebuilds the local JS ring mesh with `adaptivePhi:false`; it does not consume a supplied viewport mesh even though the public wrapper retains a `_vertices` argument. | `src/modules/export/index.js:405-429`; `src/modules/export/useCases.js:128-151` |
| Formula coverage | Server-only formulas fail loudly; there is no backend-backed profile path. | `src/modules/export/index.js:33-43`; `src/modules/export/index.js:405-407` |
| Bodies | Forces enclosure and wall to zero, excludes enclosure, and omits the source. | `src/modules/export/index.js:409-425` |
| Density | Uses the prepared profile-CSV parameters, then reports `mesh.ringCount` as angular count and the prepared `lengthSegments` as axial count. Slices explicitly repeat angular vertex zero to close each ring. | `src/modules/export/index.js:426-429`; `src/export/profiles.js:34-51` |
| Format | Semicolon-delimited text, CRLF rows, blank line between profiles/slices; numeric coordinates use six decimals. | `src/export/profiles.js:1-6`; `src/export/profiles.js:12-28`; `src/export/profiles.js:34-51` |
| Units and axes | Header is centimetres; local millimetres are multiplied by `0.1`; output `(x_cm,y_cm,z_cm)` is local `(x,z,y)*0.1`. Unlike STL, the second output coordinate is not negated. | `src/export/profiles.js:12-23`; `src/export/profiles.js:34-46` |
| Winding/orientation | Profiles iterate one angular index along increasing axial index; slices iterate angular indices and repeat the first point. The CSV carries no normals or explicit winding metadata. | `src/export/profiles.js:17-26`; `src/export/profiles.js:39-50` |
| Physical tags | None. Sections are separated only by blank lines, with no profile/ring identifiers. | `src/export/profiles.js:1-6`; `src/export/profiles.js:17-26` |
| Filenames | `${baseName}_profiles.csv` and `${baseName}_slices.csv`; simulation bundles label this pair `fusion_csv`. | `src/modules/export/index.js:436-457`; `src/ui/simulation/exports.js:806-814` |
| State | Prepared from explicit state with `applyVerticalOffset:false`; job-bound exports require the stored job snapshot, otherwise the live editor is used. | `src/modules/export/useCases.js:128-142`; `src/ui/simulation/exports.js:72-89` |

## Parameter config (`.txt`, ATH/MWG-style)

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Source | Serializes `{type, ...state.params}`; this is parameter state, not sampled geometry. | `src/modules/export/useCases.js:113-125` |
| Coverage | FREEFORM is accepted. Other server-only formula types are rejected because the format would otherwise emit undefined OSSE fields. | `src/modules/export/index.js:463-471` |
| Units/axes/bodies/tags | Not independently transformed at this layer; semantics are those of the serialized config fields. No mesh bodies, winding, or physical tags exist in this artifact. | `src/modules/export/index.js:474-491` |
| Filename | `${baseName}.txt`, MIME `text/plain`; v1 calls the content MWG config even though the generated extension is `.txt`. | `src/modules/export/index.js:479-489` |
| State | Job-bound export uses the stored job snapshot and fails loudly if it is missing; a non-job export uses current editor state. | `src/ui/simulation/exports.js:72-89`; `src/ui/simulation/exports.js:761-769` |

OPEN — raw-expression round-trip equivalence and the exact `.cfg`/`.txt`/legacy `.mwg` suffix policy belong to the text grammar contract, not this export dispatcher. Freeze them with a real-library corpus plus byte/AST round trips (`src/config/index.js:214-225`).

## HornLab mesher `.msh` build

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Source tier | Backend full-build mesh from prepared design parameters. Default requested MSH version is `2.2`; the route accepts `2.2` and `4.1`. | `src/modules/export/index.js:194-209`; `server/api/routes_mesh.py:70-79` |
| Returned authority | Client validates generator identity and string content, then returns `response.msh` unchanged with server stats. | `src/modules/export/index.js:224-261` |
| Companions | For locally supported formulas, the task may also build JS geometry/payload companions. For server-only formulas those companions are deliberately `null`, because server `.msh` and stats are authoritative. | `src/modules/export/index.js:232-260` |
| Units | The backend's canonical mesh reader declares its normalized point coordinates to be metres. | `server/solver/mesher_adapter.py:478-497` |
| Axes/orientation | The exact server artifact is passed through; the export layer does not transform coordinates or reorder elements. | `src/modules/export/index.js:251-261` |
| Physical names/tags | OPEN for the writer's complete table: freeze from representative server artifacts. The browser parser retains only two-dimensional physical names and each triangle's first tag as its physical tag. | `src/import/mshParser.js:86-106`; `src/import/mshParser.js:144-200` |
| Filename | OPEN in this low-level task: it returns text and stats rather than declaring a file object. Automatic browser download uses `simulation_mesh_${jobId}.msh`. | `src/modules/export/index.js:251-261`; `src/ui/simulation/meshDownload.js:9-23` |
| State | The low-level use case receives explicit prepared parameters. Job solve artifacts are separately tied to the submitted task/snapshot. | `src/modules/export/useCases.js:36-51`; `src/ui/workspace/taskManifest.js:247-300` |

## Stored job `.msh` retrieval and browser parsing

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Retrieval | `GET /api/mesh-artifact/{job_id}` returns the stored mesh artifact as `text/plain`; no export-side rewrite is performed. | `server/api/routes_simulation.py:230-240` |
| Parser envelope | Browser parser requires a `$MeshFormat` section, exactly version `2.2`, and ASCII mode (`fileType=0`). | `src/import/mshParser.js:1-6`; `src/import/mshParser.js:71-84` |
| Nodes | Node IDs are remapped to dense indices and coordinates are stored as `Float32Array`; the parser applies no unit or axis conversion. | `src/import/mshParser.js:108-142` |
| Elements/winding | Only Gmsh type-2 triangles are retained. Other element types are skipped. Triangle node order is preserved. | `src/import/mshParser.js:144-200` |
| Names/tags | `$PhysicalNames` is optional; only dimension-2 names are recorded. The first element tag becomes the triangle physical tag. | `src/import/mshParser.js:86-106`; `src/import/mshParser.js:144-200` |
| Missing/unsupported | Missing required sections, non-2.2 versions, and binary files throw. | `src/import/mshParser.js:71-84`; `src/import/mshParser.js:108-146` |

## Result-export bundle

The bundle's result-file schemas are specified in `RESULT-CONTRACTS.md`. This section freezes orchestration shared with geometry exports.

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Format set | `mwg_config`, `step`, `png`, `csv`, `json`, `txt`, `polar_csv`, `impedance_csv`, `vacs`, `stl`, and `fusion_csv`. | `src/ui/settings/simulationManagementSettings.js:24-40` |
| Execution | Formats execute sequentially. A failure is recorded per format and does not stop remaining formats. A mixed outcome is displayed as a warning. | `src/ui/simulation/exports.js:881-919` |
| Bookkeeping | Returned file entries are stored as `formatId:fileName`; result contains exported files, failures, and selected formats. | `src/ui/simulation/exports.js:890-925` |
| State | Geometry-derived formats resolve the job snapshot; result-derived formats use `panel.lastResults`. A selected result format without results becomes a per-format failure. | `src/ui/simulation/exports.js:72-89`; `src/ui/simulation/exports.js:881-903` |

## Naming, workspace, manifest, and automatic behavior

| Contract dimension | v1 contract | Evidence |
|---|---|---|
| Manual base name | UI base name is `${prefix}_${counter}`; stored prefix/counter are schema-versioned in local storage. | `src/ui/fileOps.js:60-106`; `src/ui/fileOps.js:193-208` |
| Counter | The counter increments on the first parameter change in a pending-change group, caps at its maximum, and export writes may opt out with `incrementCounter:false`. | `src/ui/fileOps.js:210-243`; `src/ui/simulation/exports.js:91-103` |
| Job folder | Folder name is derived deterministically from job/task identity; new task labels use `YYMMDD_label` with an available counter when needed. | `src/ui/simulation/exports.js:64-70`; `src/modules/simulation/naming.js:17-52`; `src/modules/simulation/naming.js:60-97` |
| Manifest | A task directory persists `script.snapshot.mwg` and `waveguide.project.v1.json`; the manifest includes task identity, design snapshot, raw result and mesh source routes, and artifact naming data. | `src/ui/workspace/generationArtifacts.js:1-14`; `src/ui/workspace/generationArtifacts.js:135-205`; `src/ui/workspace/taskManifest.js:247-300` |
| Workspace write | The backend validates a workspace-relative subdirectory and sanitized filename and returns the native written path. | `server/api/routes_misc.py:256-319` |
| Fallback | Workspace write is attempted first. Failure falls back to the File System Access picker, then an anchor download; successful completion finalizes the counter. | `src/ui/fileOps.js:268-335` |
| Auto settings | Defaults are auto-export off, automatic mesh download off, and no selected formats; settings persist in local storage. | `src/ui/settings/simulationManagementSettings.js:42-48`; `src/ui/settings/simulationManagementSettings.js:111-162` |
| Mesh auto-download | Once per in-memory job ID, download begins as soon as an active job exposes a mesh artifact; failure removes the guard so polling may retry. | `src/ui/simulation/polling.js:111-123` |
| Auto export once | On transition to completed, v1 persists results/artifacts, runs a bundle if enabled, and then records its auto-export marker. | `src/ui/simulation/polling.js:183-227` |
| Early persistence | A mesh artifact can be persisted before solve completion; its in-memory guard is cleared on persistence failure. | `src/ui/simulation/polling.js:125-153` |

OPEN — exact crash semantics between writing some bundle files and recording the auto-export marker require an injected-crash integration test; the current sequence is non-transactional (`src/ui/simulation/polling.js:183-227`).

## v2 decisions required

1. Which revision-bound server artifact is authoritative for STL: OCC full-build geometry, a dedicated export tessellation, or another tier—and what tolerance must match the v1 browser STL (`src/modules/export/index.js:358-382`)?
2. Must every geometry export require a `designRevision`, and what explicit UX applies when a job lacks a snapshot or the live preview is stale (`src/ui/simulation/exports.js:72-89`)?
3. What canonical units and axes will all v2 geometry exports use, and which compatibility modes preserve STL `(x,-z,y)` versus profile CSV `(x,z,y)` (`src/modules/export/index.js:140-147`; `src/export/profiles.js:12-23`)?
4. What STEP face orientation, seam, axial direction, and CAD-validity fixture becomes binding, since v1 does not declare them (`server/solver/mesher_adapter.py:519-578`)?
5. Will v2 preserve STEP's asymmetric density rule—densified angular/corner sampling but caller-selected length sampling—or intentionally version the change (`src/modules/export/index.js:292-302`)?
6. Which bodies and physical groups are selectable per export, and are v1's bare-horn exclusions the default compatibility profile (`src/modules/export/index.js:362-377`; `server/api/routes_mesh.py:109-115`)?
7. Which formulas must receive server-backed STL/profile exports, replacing v1's loud local-path rejection (`src/modules/export/index.js:33-43`)?
8. Does v2 promise original Gmsh 2.2 text, add 4.1 parsing, or distinguish stored-original from canonicalized mesh downloads (`server/api/routes_mesh.py:70-79`; `src/import/mshParser.js:71-84`)?
9. What exact physical-name/tag table and element-orientation policy is frozen for `.msh` artifacts (`src/import/mshParser.js:86-106`; `src/import/mshParser.js:144-200`)?
10. Which text suffix is canonical for new saves (`.cfg`, `.txt`, or `.mwg`), and which raw expressions must round-trip byte-for-byte versus semantically (`src/modules/export/index.js:479-489`; `src/config/index.js:214-225`)?
11. Are deterministic folder/manifest writes and bundle bookkeeping transactional and idempotent, and when is the auto-export marker committed after partial failure (`src/ui/simulation/exports.js:881-925`; `src/ui/simulation/polling.js:183-227`)?
12. Must browser fallback preserve the same relative folder and manifest contract, or is a flat multi-download an accepted compatibility degradation (`src/ui/fileOps.js:268-335`)?
