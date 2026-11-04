# Dependency compatibility

The current mesher adds the measured ATH lip/Opus compositions, throat controls,
R-OSSE truncation and sketched woofer/HF diaphragms. Geometry qualification at
`7e0bdcc` passed 9,542 tests with five Windows-only skips and required ATH parity;
the later target changes only two registered Custom profile labels. Installed
WG consumer qualification is required separately before this pin lands.

The mesher pin `1a53231aef363d91bb081eb16cb5e269a3644af5` declares version 0.2.9. It retains the released
`983f5a39` geometry contracts while reusing existing sampling and preview owners.
It includes version-gated ATH v3 controls and typed public refusals with
a shipped, checked control-label catalog. Saved v1/v2 retain their prior geometry.
It retains the ICW mouth endpoint-intersection fix from `4ae1cfa6`
in `general_horn.radii_at_z`.
`mesh_sizing.py` is unchanged, so CAD Link retains its earlier vendoring provenance.
When it cuts a solid to its symmetry domain it keeps periodic (seam-closed) faces
such as a throat cylinder, which the earlier cutter could drop, and it raises
`RuntimeError` ("OCC cut lost retained portions") rather than return a cut that
lost retained geometry; WG then meshes the whole model.

The pin adds validated triangle-surface cutting through the existing OCC
cutter's explicit `triangle_surface_mesh=True` option, with
`TriangleCutRefused` for unsafe triangle cuts. Existing CAD and FEM callers
keep the general cut path. This pin alone does not enable automatic cutting
of imported mesh files. Failed cleanup after a successful cut raises instead
of leaving temporary faces to be meshed.

Welding, unused-vertex compaction and degenerate-triangle filtering share one
owner while preserving their generated/imported caller behavior. The pin's
qualification compared 216 mesh files and 16 identical refusals with its
`0f885553` base. Offset validation keeps the same invalid-cell decisions and
first location, but distinguishes collapsed inner, outer and paired surface
normals from actual folds. WG preserves the existing refusal paths and names
the collapsed surface without attributing it to excessive wall thickness.

It also includes ATH R-OSSE morph import and early singular-coverage checks.
R-OSSE block `Rot` and top-level `arcterm`/`trunc` follow ATH as named,
ignored no-ops. Its v2 interpretation treats a constant `Scale.Z = 1` as a
no-op and refuses nonunit or azimuth-dependent axial scales. The v3 consumer
requires a v3-capable mesher pin: it supports positive constant `Scale.Z`,
top-level R-OSSE `Rot`, authored `Mesh.ThroatExtSegments` and the corrected
R-OSSE station map. A separate axial scale cannot be silently discarded by
native feature conversion. Saved v1/v2 interpretations remain unchanged.
Fresh ATH OSSE text
with `Length = 0` is refused as an unsupported source-only model. Saved and
native geometry retain their existing interpretation.

Enclosures with less than 0.1 mm of physical baffle beside any wall now
continue straight back from the fitted mouth rim as a flush enclosure. Every
previously accepted enclosure retains its original construction, including
zero margins with fractional mouth extents. The new flush path refuses a
configured woofer, sampled rings wider behind the mouth, and fitted rims that
fold across a reduced-domain cut or cross themselves. The existing native
roundover/enclosure refusal remains. Preview, mesh and solve admission share
`resolve_enclosure_bounds()` with the execution domain; measurement-only
callers retain `measure_enclosure_box_bounds()`.

STEP and mesh support the new flush construction. WGLink bundles, Fusion and
Onshape share an explicit flush refusal until fitted-mouth reconstruction is
supported there. This enabling pin ships with that consumer guard. Against
`7c58e751`, qualification retained 70 raw byte-identical artifact pairs across
16 existing saved/configuration and accepted-enclosure cases, including STEP,
mesh, preview and build/manifest metadata (fixed STEP clock only).

New flush builds also select Gmsh's existing older initial boundary recovery
to avoid duplicate throat/source triangles observed on Linux. The existing
meshing owner restores the caller's options on success and failure. Fitted
surfaces, validation, repair and every previously accepted enclosure path
remain unchanged. Qualification against `fc8d7d1c` retained the same 70 raw
identity pairs; the original failing flush case and affected checks passed
with both locked and latest Linux dependencies. Hosted Windows verification
of this correction remains separate from these source checks.

Profile queries collect their warnings within the existing build owner, so
concurrent curve queries do not change process-wide warning filters. Numeric
profile controls reject booleans, inactive endpoint block angles no longer
override an explicit handle, point angle or corner, and curve evaluation shares
the production span/boundary handling. Cubic construction, inversion, shrink
arithmetic and accepted native mesh output are retained. Historical wall
identity checks keep their fixed baselines after a rebase; missing history is
a failure rather than a skipped comparison. The existing near-vertical exact
insertion conditioning limitation remains; no tolerance or geometry fix is
implied. Source/identity and consumer checks remain separate from installed
and platform qualification.

This pin also supplies native source-contour, front-baffle woofer,
shared horn/woofer and passive phase-plug contracts, and the public
`printable_surface_tags()` that STL export uses. Its ATH import contract is
versioned: designs saved with the `ath-2026-08c-v1` stamp build exactly as
before, including bounded near-planar imported rear returns (pointwise
displacement at most the smaller of 0.0001 mm and 0.0001 times the wall
thickness). The pin supplies the `ath-2026-09a-v2` behavior retained by saved
v2 designs. WG now stamps fresh ATH imports `ath-2026-09a-v3`; this consumer
change must land together with its v3-capable mesher pin. V3 inherits the v2
ATH V2026-09a behavior: the ATH expression functions with natural `log`, NAC,
current ATH axial sampling, `Mesh.Roundover` lips, `Mesh.RearShape = 3` rears, warped
RearShape 1 rears and the corrected import defaults. STEP export of lip or
RearShape 3 imports is refused by name. Native rear construction is unchanged.
See [Native source contour ingestion](architecture/NATIVE-SOURCE-CONTOURS.md)
and [Source contour and assembly editors](architecture/source-contour-editor.md).

Current v2/v3 ATH imports also retain R-OSSE slot translation, authored throat
extension segments before the complete body map (ten by default; v3 can override
the count with `Mesh.ThroatExtSegments`), and sampled rectangular
no-shrink boundary expansion. Saved v1 and native configurations retain their
preceding construction. Consumers that replace angular, axial or corner density
must first call the native `retain_ath_import_construction()` on the authored
config and carry its transient construction through sampling. The coupled WG
consumer correction shares that handoff across exports, STEP/STL planning,
preview cache identity and symmetry probes; the data never enters saved designs
or geometry stamps. The strong-expansion OCC triangulation failure and existing
R-OSSE axial-table residuals at LengthSegments 12/30 remain separate limitations.

The Metal pin `80a2d5e` supplies a BEAT-compatible worker using the existing
request compiler and event/result decoder. WG routes its qualified single-source,
normal-motion exterior Metal solves through that interface, with the existing
explicit `complex_k` formulation and 0.005 shift. Other Metal features retain
the native interface. The selected engine remains Metal, and BEAT Metal stays
a separate choice. Existing power, surface traces, drive ports and observation
conventions are retained. Results record observed source/version/commit and
helper/shader identity; unavailable identity remains unknown.

The BEM++ pin `5d82a93` retains its opt-in prescribed-source exterior-pressure
`solve_compiled` facade and native orders 4/4. Same-backend pressure comparisons
establish input-translation equivalence; they do not establish numerical
equivalence between Metal, BEM++ and BEAT. See
[Installed legacy BEAT input qualification](LEGACY-BEAT-INPUT-QUALIFICATION.md).

Engine defaults and saved engine choices are retained. Source and consumer
checks remain distinct from installed application qualification, Windows
installed qualification, acoustic performance and manufacturing suitability.

The pin adds one native owner for optional surface-deviation sizing and sampled
observations, triangle-shape reports and a pre-build density estimate. Horn
surface deviation defaults to Off; existing CAD sizing retains its rule.
Explicit minimum sizes remain authoritative. Report failures remain unavailable
observations and do not trigger remeshing; sampled values are not hard maximum
guarantees. Full ATH/archive and saved-design identity qualified the final native
production code. A test-only correction explicitly accommodates the new
optional None field in a frozen dataclass snapshot; affected checks passed
without another full run or changes to the captured historical values. The consumer uses this shared
owner and retains its existing memory admission guard. No OCC triangulation
fix or triangle-cut limit increase is included.

The mesher pin scopes partial OCC boolean glue to the two woofer assembly
fragments and restores the caller's option on success and failure. Mac
qualification retained exact point, triangle, physical-tag and complete MSH
bytes on twelve cases, including ATH imports, reduced domains and nearby
woofers. The improvement targets woofer Build time; the ordinary horn path
retains its existing fragment behavior. The WG consumer's new-woofer default
is the ATH contour, and an actual Build with that default is qualified on
the exact installed pin. Hosted platform and installed-candidate evidence
remain separate from these source comparisons.

The plots pin `9462f4b` preserves unavailable samples as chart gaps, keeps the signed phase grid and on-axis reference, and supports explicit directivity display ranges. Partial-cap plots state their coverage. These renderer contracts are available to WG export consumers.

The BEM++ pin `5d82a93` serializes overlapping BLAS limit scopes, refuses unsupported baffle combinations, releases streamed traces and retains unavailable directivity samples as unavailable. The existing opt-in compiled facade and native quadrature orders remain.

The Metal pin `80a2d5e` rejects false GMRES success and degenerate geometry and reuses Jacobi factors. Its active tree includes only those solver changes over version 0.2.2. Native process ownership and shader aggregation changes remain excluded; persistent-helper and Metal gap features are not enabled by this pin.

Sampling reuses the existing profile cache and array evaluator, and preview
resolution reuses already prepared geometry. The retained tree excludes the
unqualified session-ownership and STEP fitting changes. No new geometry
construction, meshing tolerance or refusal relaxation is claimed.

The sim pin `bf50b3b5` combines Neumann traces with their pressures and reports
unavailable combined radiated powers as unavailable. LEM-to-BEM path loading
now honors mesh options; zero-drive impedance, zero-series termination and
Hornresp export refusals follow their existing physical owners. These are
intentional numerical/output corrections, qualified by standard-install and
native seam suites. The schema unit test uses a stand-in; its known P2 coverage
limitation remains recorded rather than being treated as real-schema coverage.

## Metal stream ownership and field geometry

Metal-BEM pin `722ed50607b66b06c493e570238f5e29eaa73725` adds stream-owned execution directories, bounded
process-group cleanup after EPERM, complete-image trace replay and immutable
field-geometry reuse. Its source tree equals the qualified aggregation/gaps
prefix `d246bc1d33a0510e273b174a94e3344586a5e9bf`. Persistent-helper work is
excluded after Windows fixture-contract failures. BM multi-source refusal occurs
before setup. Experimental BM ground remains opt-in and defaults off; BEAT
qualification is still required before enabling it.
