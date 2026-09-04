# Export contract

Status: canonical current contract, verified against `server/exports/`,
`frontend/src/results/`, and `frontend/src/jobs/RunExportControl.tsx` on 2026-08-13;
export sizing reverified 2026-09-04.
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
| STL | Binary little-endian STL of physical-tag-1 horn-inner triangles from an authoritative Gmsh build on the export's own grid. Coordinates are millimetres with solver `(x, vertical, axial)` mapped to `(x, -vertical, axial)` and winding reversed to preserve the mesher's normal side. |
| Fusion curves | Two semicolon-delimited CRLF CSVs, profiles and slices, headed in centimetres as `x_cm;y_cm;z_cm`; uses the bare uniform-ring inner surface without vertical offset. |
| Parameter config | Canonical `.cfg` text from the design-format contract, preserving compatible raw expressions and optional CAD identity. |
| Mesh artifact | The stored solver `.msh` bytes for that job; no export-time geometry rebuild. |
| `.wglink` | Identity-bearing directory bundle with STEP and manifest, allocated idempotently in the selected workspace and registered server-side. |

STEP solid is the normal CAD choice. STL and curve CSV remain explicit advanced formats,
not alternate geometry authorities.

## Geometry exports size themselves

A geometry export samples the analytic surface as finely as its own **fidelity
tolerance** requires, and no finer. It reads neither the solver's millimetre mesh
resolutions nor `mesh.max_triangles`, and the design's `mesh.angular_segments`,
`mesh.length_segments` and `mesh.corner_segments` do not move it either: those remain
solver and CFG-compatibility fields. There is no user-facing export grid control, and
adding one would reintroduce the coupling this replaced. The tolerances are constants in
`server/exports/sizing.py`.

| Export | Tolerance | Meaning |
|---|---|---|
| STEP solid | 0.02 mm | Deviation of the fitted CAD surface from the analytic formula. Tighter than the print tolerance because downstream CAD operations refine against this master. |
| STL | 0.10 mm | Chord deviation of the written triangles: print resolution, at or below every common layer height and well inside a 0.4 mm nozzle. |
| STEP inner surface | 0.10 mm chord | Planned to the same chord as the STL. The written surface lands somewhat outside it — it is a *ruled* loft through control-point splines — so the number planned is a chord, not a promise about the file. |

The grid is chosen by measurement, not by formula: the analytic surface is sampled at
twice the candidate grid and each sample's distance to the cell it falls in is compared
against the tolerance, so a design whose curvature a formula would misjudge is refined
until it actually passes. Density therefore follows geometry and part size — the same
waveguide at twice the scale needs a finer grid to hold the same absolute deviation.

**The STL's triangle ceiling is a backstop, not a gate.** A design whose tolerance would
need more than 150,000 triangles is exported anyway, coarsened to the ceiling, with the
reason returned in the `X-Export-Warning` response header and logged. An export is never
refused for being large. (`mesh.max_triangles` is the solver's advisory warning
threshold; using it here turned a warning into a refusal on a mesh the export itself had
densified.)

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
| Complex pressure basis | One NPZ per imported Metal drive channel. `pressure_complex` and optional sphere pressure are lossless engineering `exp(+jωt)` phasors converted from the retained solver basis; the file tags its drive normalization, motion, source ids, and any retained tags/areas. Surface-average pressure is explicitly unavailable rather than reconstructed from result JSON. Jobs predating retention, parametric jobs, and unsupported engines refuse clearly. |
| Derived acoustics | Per-channel CSV and schema-versioned JSON sidecars joining on-axis SPL, full-sphere DI, power-response level (`SPL - DI`), de-embedded excess group delay when the phase grid is resolvable, and the retained beam-shape/beamwidth metrics. Missing values remain empty/null and are never interpolated. |
| Static HTML report | One self-contained run report across every channel, with inline CSS/SVG response and beamwidth plots, summaries, warnings, derived-data tables, and result metadata. It has no scripts or network dependencies and escapes result/user text before rendering. |
| Summary text | Human summary and the same union-grid detailed rows as the frequency CSV. |
| Polar CSV | Frequency, plane, measured theta, normalized SPL. |
| Impedance CSV | Frequency plus real/imaginary `Z/(rho*c)`. |
| Radiation-matrix CSV | Long-form engineering matrix and in-phase port reductions in Pa·s/m³. Every row is explicitly `engineering_exp_plus_jwt`; receiver/source aperture names remain attached, and the complex load is exported as real/imaginary values without display smoothing. Available only when the job retains the passive-cardioid artifact. |
| Radiation-matrix NPZ | The exact stored compressed archive. It retains aperture name/area/tag, solver-convention and engineering-convention matrices, in-phase reductions, and diagnostics; no client-side round trip or numeric conversion occurs. |
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
The permanent Workspace run archive conditionally adds both radiation-matrix formats
when the job's artifact flag is true; ordinary runs do not fail archiving over an
artifact they never produced.

The default run archive always writes full JSON, frequency CSV, derived-acoustics
sidecars, and the static HTML report. Imported Metal archives also include every
retained native drive-channel pressure basis; derived combined/cardioid channels are
not misrepresented as independently solved bases. Archive timestamps come from the
run's recorded completion time, so retrying after an interrupted metadata update
reproduces identical bytes under the archive's merge-identical policy.

## Security and integrity

Server responses set explicit media types and safe content-disposition filenames.
Workspace writes accept only normalized relative members under the selected workspace.
CAD bundles use idempotency keys, stable identities, manifest hashes, and atomic
publication. The release SPA archive is a separate distribution artifact and is verified
against its published SHA-256 before extraction.

Remaining archive/dispatcher/catalog product work is tracked in the maintainer's
workspace-local backlog, not as part of this public contract.
