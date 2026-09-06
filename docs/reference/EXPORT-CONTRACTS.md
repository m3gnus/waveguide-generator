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
| STEP inner surface | 0.10 mm | Deviation of the *written* ruled loft from the analytic bore, not of the chord through its samples. The planner builds the surface it is about to write and measures that; a chord target cannot certify this export, because the loft interpolates each ring with a spline and sits about twice as far out as the chord through the same points. See the finite-sampling limits below for what the number does and does not promise. |

The grid is chosen by bounded numerical measurement, not by a curvature formula. The
analytic surface is sampled on both a twice-dense lattice and a slightly detuned lattice;
each sample's distance to the nearby candidate cells is compared against the tolerance.
The detuned probe prevents periodic `p` expressions from disappearing merely because
they share a phase with the candidate grid. This is finite sampling rather than a proof
over every possible user expression. Density therefore follows measured geometry and
part size — the same waveguide at twice the scale needs a finer grid to hold the same
absolute deviation.

### What a fidelity tolerance guarantees, and what it does not

Every number above is a maximum over a **finite** set of sample points, so the guarantee
is only as good as where the reference looks. Sampling on its own under-reads: a maximum
taken over some points cannot exceed the maximum over all of them. That does not make a
reported figure a lower bound, though, because what is measured *at* each sample point
carries approximations of its own — for the surface STEP one of them is two-sided and one
over-reads, and the table further down names all four with their directions. Sampling
under-reads; the number that comes out of sampling plus measurement is not guaranteed to
sit on either side of the truth. Four limits are known and worth stating plainly.

**A reference has to be refined where the surface is coarse, not merely refined.** A
rounded-rectangle morph samples its corner arc with three fixed intervals whatever
angular count is asked for: on the 120x80 r12 morph the arc breakpoints stay at 42.9473,
44.2581, 45.7419 and 47.0527 degrees from 147 segments up to 2352, while the straight
sides fall from 2.26 to 0.15 degrees. A reference built by multiplying the requested
count therefore never samples between them — which is exactly where the ring spline
overshoots. Measured on the written file, the grid this export chose before 0.3.2 read
0.0937 mm against such a reference and 0.1138 mm against one that subdivides the arc, so
a grid certified at 0.098 mm was 0.114 mm out at the mouth corner. The surface planner
now subdivides the arc in its reference; the reading converges by sixteen subdivisions
(0.0984 mm, 0.1104 mm, 0.1195 mm, 0.1195 mm at one, four, sixteen and thirty-two), and
the grid it accepts for that design is measured at 0.0945 mm on the written file.

**The written file is re-measured, and not by projection.** `_surface_grid_plan` reads
the loft it builds in process; a separate test writes the STEP, imports it back through
OCC, and measures the geometry that came out of the file against an independent analytic
reference. That second reading is a point-to-quad distance to the file's own
rulings rather than an OCC closest-point projection, which converges to a local minimum
on a trimmed ruled band and read 0.1699 mm for a sample 0.0929 mm from the surface it
was projecting onto.

That reading is exact against the sampled quad strip rather than against the face, and it
is worth separating the parts of it that are exact from the parts that are not, because
they do not all lean the same way. Four things are in play:

| part | direction | size, where measured |
|---|---|---|
| The straight ruling across a band | **exact** | The loft is degree one in v, so the segment between two boundary samples is on the face. Checked on the written file, not assumed: interior isoparametric points sit on their own ruling to about 1e-13 mm. |
| The reference is a finite point set | **one-sided, under-reads** | A sampled maximum is a lower bound on the true maximum over the surface. This is the limit the 5% acceptance margin below exists for. |
| A quad stands in for the spline through its rulings | **two-sided** | The quad's corners lie on the face and its interior does not, so the sign depends on which side of the local curvature the reference point falls. Measured against an independent minimisation that shares no code with it: at the point setting each qualified design's reported maximum the readings sit -0.000005, -0.000000, +0.000000 and +0.000674 mm from the true distance, and over a 32-point spread on the rounded-rectangle morph they run from 0.000116 mm below it to 0.000674 mm above. |
| The cell and band search is bounded | **one-sided, over-reads** | Missing a nearer cell can only raise the reading. Not measurable on the qualified designs, and it leans opposite to the row above. |

The two-sided term converges. Holding the reference fixed and doubling only the strip
density moved the rounded-rectangle maximum from 0.094490 mm to 0.094005 mm, which is
second order — the directly measured error at the density used was 0.000674 mm, and 4/3
of that 0.000485 mm difference predicts 0.000647 mm. A difference between two densities
is a convergence observation, not a bound on either of them; the regression test bounds
the reading instead by measuring, from the file, how far the strip chord departs from the
face at the point being checked.

Neither check is a proof over every expression a user can write. They are bounded
measurements with stated blind spots, and acceptance multiplies the reading by 1.05
before comparing it to the tolerance — 5% of the reading, which on the rounded-rectangle
grid is 0.0047 mm, or about 4.7% of the 0.10 mm target.

**Planning this export is measurement, and it costs time.** The surface planner builds
and measures the loft it is about to write, once per refinement attempt, so the cost
follows the design rather than a formula. On the rounded-rectangle morph — the most
expensive of the four qualified designs, and the one that needs the most attempts — a
plan was observed to take about 49 seconds on one development machine (Apple silicon,
Python 3.13, gmsh 4.15). That figure is a single observation on one machine and one
design, recorded so the order of magnitude is not a surprise; it is not a performance
guarantee, and no part of the export contract depends on it. Other hardware, other
designs and other gmsh builds will differ.

**The tolerance is what the search aims at, and the export says when it missed.** Each
planner refines for a bounded number of probes — six generally, sixteen for the surface
STEP, whose reading falls more slowly than the step model each refinement is sized from.
A design can run out of probes, run out of room to refine, or lose its measurement
altogether. In every one of those cases the file is still written, on the finest grid the
search reached, because refusing an export over its own sizing search would be worse than
serving it. The four designs the round-trip test qualifies arrive with four probes to
spare.

What changed is that the compromise now leaves the server. Any export whose plan is not a
grid measured to meet its tolerance returns an `X-Export-Warning` header, which the app
shows on the line that reports the successful write. Four distinct things can be
reported, and they are different claims:

| situation | what the note says |
|---|---|
| Measured, and outside the target | the deviation it was measured at, and that fine detail is smoother than the target |
| Measured inside the target, but on a grid too coarse to validate the model behind that number | that the target could not be *confirmed*, and that the reading is an estimate rather than a bound |
| No usable measurement at all | that the export fell back and its deviation is unverified, rather than known to be met |
| Coarsened to the triangle ceiling | the triangle counts before and after. When it lands on top of one of the rows above, both are reported, and the ceiling sentence does not claim the untrimmed grid would have held the target — because in that case it would not have |

A grid that was measured and did meet its tolerance reports nothing. That is the whole
point of the header: it means a compromise was made, so it must not fire on the ordinary
case.

**Scope: the CAD-link bundle is outside this.** *Send to CAD* does not go through the
geometry-export planners at all — it hands the design's own resolved geometry to the
mesher's bundle writer, so it has no fidelity plan, no reading, and nothing to warn
about. Its manifest carries no sizing note, and that is a real gap rather than an
oversight: a bundle is an identity-bearing CAD handoff sized by the design, not an export
sized to a tolerance. `test_the_cad_bundle_is_built_without_the_export_sizing_planners`
runs that path with the planners replaced by detonators, so if the bundle is ever routed
through export sizing the gap closes loudly instead of silently shipping an unreported
compromise.

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
| Polar CSV | Frequency, plane, measured theta, normalized SPL. Every plane's frame count must equal the frequency axis and every angle must be finite; a mismatch refuses the export rather than mislabeling a frame with a clamped frequency. |
| Impedance CSV | Frequency plus real/imaginary `Z/(rho*c)`. |
| Radiation-matrix CSV | Long-form engineering matrix and in-phase port reductions in Pa·s/m³. Every row is explicitly `engineering_exp_plus_jwt`; receiver/source aperture names remain attached, and the complex load is exported as real/imaginary values without display smoothing. Available only when the job retains the passive-cardioid artifact. |
| Radiation-matrix NPZ | The exact stored compressed archive. It retains aperture name/area/tag, solver-convention and engineering-convention matrices, in-phase reductions, and diagnostics; no client-side round trip or numeric conversion occurs. |
| VACS | Legacy advanced/preferences format. Complete impedance samples retain real/imaginary values (`Data_Format=Complex`); missing components refuse rather than becoming zero. Polar curves use the result frequency grid and scalar normalized magnitude (`Data_Format=Real`), with a separate angle-labelled curve per angle and no phase. Mismatched frame counts, changing angle grids and missing magnitudes refuse with a CSV alternative. This is not absolute or phase-correct pressure. Actual VACS importer qualification remains open. |
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
