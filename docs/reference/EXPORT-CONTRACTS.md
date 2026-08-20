# Export contract

Status: canonical current contract, verified against `server/exports/`,
`frontend/src/results/`, and `frontend/src/jobs/RunExportControl.tsx` on 2026-08-13.
The detailed original-application inventory remains in Git history at `f51a23c`.

## Provenance rule

A design-menu export uses the editor revision supplied in its request. A job-bound
geometry or config export uses the job's retained design snapshot and fails explicitly
when no recoverable snapshot exists. It never substitutes the current editor. Result
formats use the selected stored result/channel. Imported multi-drive results are
exported per declared channel unless the user has selected a specific one.

## Geometry and design formats

| Format | Current output |
|---|---|
| STEP solid | Full-domain manufacturable B-rep in millimetres, including available wall/enclosure material with an open throat. Carries the `mesh.vertical_offset` placement in every domain — it is a CAD boundary, unlike the recentred solve and preview frames. A design without material may fall back to a surface body. |
| STEP inner surface | Full-domain ruled acoustic bore in millimetres. Exposed from the design menu for users who want to thicken/loft themselves. |
| STL | Binary little-endian STL of physical-tag-1 horn-inner triangles from an authoritative densified Gmsh build. Coordinates are millimetres with solver `(x, vertical, axial)` mapped to `(x, -vertical, axial)` and winding reversed to preserve the mesher's normal side. |
| Fusion curves | Two semicolon-delimited CRLF CSVs, profiles and slices, headed in centimetres as `x_cm;y_cm;z_cm`; uses the bare uniform-ring inner surface without vertical offset. |
| Parameter config | Canonical `.cfg` text from the design-format contract, preserving compatible raw expressions and optional CAD identity. |
| Mesh artifact | The stored solver `.msh` bytes for that job; no export-time geometry rebuild. |
| `.wglink` | Identity-bearing directory bundle with STEP and manifest, allocated idempotently in the selected workspace and registered server-side. |

STEP solid is the normal CAD choice. STL and curve CSV remain explicit advanced formats,
not alternate geometry authorities.

## Result formats

| Format | Contract |
|---|---|
| Chart PNGs | Canonical HornLab response charts plus a separately rendered directivity map, using the selected theme/smoothing. |
| On-axis FRD | Tab-delimited frequency, SPL, phase triples readable by REW and VituixCAD; includes smoothing and propagation-reference notes. |
| Polar FRD set | Horizontal/vertical per-angle files under `hor`/`ver`, written into a selected workspace subdirectory; only angles with phase coverage are emitted. |
| Electrical ZMA | Per driver-modelled channel, tab-delimited frequency, magnitude in ohms, and engineering `exp(+jωt)` phase. Refused unless `impedance_units` is `ohms`; unit-drive acoustic impedance is never coerced. |
| VituixCAD project | Version-2 `.vxp` project plus every referenced per-channel on-axis FRD and electrical ZMA. Uses the solved LR4 filters, gains, and delays when the eligible driver channels exactly match a combined result. |
| Frequency CSV | Exact-key union of SPL, DI, and impedance frequency grids. Empty cells mean unavailable, never interpolated. |
| Full JSON | Timestamp, smoothing selection, and the complete stored result contract. |
| Summary text | Human summary and the same union-grid detailed rows as the frequency CSV. |
| Polar CSV | Frequency, plane, measured theta, normalized SPL. |
| Impedance CSV | Frequency plus real/imaginary `Z/(rho*c)`. |
| VACS | Legacy advanced/preferences format. Its polar block is magnitude-only and remains an explicit follow-up decision; it must not be described as phase-correct. |
| Radiation package | Deterministic `.zip` re-simulatable equivalent source for one solved job: the bundled solver mesh plus the retained complex64 boundary `p` and `q` per frequency and per *raw* channel, with a schema-versioned manifest carrying the artifact conventions, symmetry plane, array layout, and per-member SHA-256. Traces stay on the reduced mesh and consumers image-expand; no combine state is baked in. Built and verified by `wg export-package`; refused with structured issue codes unless the job is complete and its traces cover every solved frequency. |

## Naming and failure behavior

Stored-job filenames start with `<run_number>_<portable title>`. Non-portable characters
are normalized for the path only; the job label is not changed. Multiple result channels
add a safe channel suffix.

The bundle dispatcher runs selected formats sequentially and returns both successful
filenames and per-format failures. This includes on-axis FRD, polar FRD, and both
canonical PNG render requests; they do not have separate run-menu implementations.
One failure does not erase earlier successes, and a retry can target the failed format.
The Fusion curve pair is fetched completely before either browser download begins so a
half-format cannot appear successful.

Automatic export has its own format list and records status per format. It is complete
only when every selected automatic format completed; failed formats remain retryable.

## Security and integrity

Server responses set explicit media types and safe content-disposition filenames.
Workspace writes accept only normalized relative members under the selected workspace.
CAD bundles use idempotency keys, stable identities, manifest hashes, and atomic
publication. The release SPA archive is a separate distribution artifact and is verified
against its published SHA-256 before extraction.

Remaining archive/dispatcher/catalog product work is tracked in the maintainer's
workspace-local backlog, not as part of this public contract.
