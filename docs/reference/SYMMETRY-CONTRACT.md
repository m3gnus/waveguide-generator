# Solve symmetry contract

Automatic symmetry is a solve-time decision. It never edits or rewrites the ATH `Mesh.Quadrants` value in the design document. The submitted design snapshot therefore retains the imported spelling, while job and result metadata record the requested symmetry mode, the resolved mask, both plane decisions, rejection reasons, and the original design value.

## Geometry authority

The resolver passes the validated design through `server.preview.translate.design_to_mesher_config`, then through `hornlab_mesher.config_builder.build_geometry_params` and `build_point_grid`. It forces a full-circle sampling grid with 128 angular samples and 16 axial intervals. This is the same profile, morph, guiding-curve, cross-section, scale, source-boundary, and outer-wall geometry evaluation used before the OCC solve mesh is built; it does not infer symmetry from field names.

Both the inner surface and any freestanding outer surface are checked. For each axial ring, every reflected point is matched to its nearest point on the original ring's closed sampled polyline. Array position and row order are not assumed, and a reflected vertex may match the interior of a different azimuth segment. The xz check reflects `y -> -y`; the yz check reflects `x -> -x`.

The accepted distance is `max(1e-7 mm, 2e-4 * D)`, where `D` is the sampled model's bounding-box diagonal in millimetres. The relative term absorbs floating-point evaluation and modest sampling-lattice mismatch while remaining 0.02% of the model size. A miss reports both the measured maximum deviation and the tolerance. Sampling, translation, or non-finite-grid failures reject both planes and select the full domain.

## Inputs audited outside the point grid

The point grid does not contain every terminal surface, so the resolver separately audits:

- `mesh.vertical_offset`: Only a non-finite or non-scalar value rejects xz. A finite non-zero value is a rigid +y placement applied after the cut planes are built at the origin; `_solver_mesher_config` drops that placement in every domain, so the solve mesh is always in the recentred origin frame and the placement cannot destroy the xz reduction. It does not affect yz. Only the CAD exports (solid STEP and the CAD-link bundle) apply it.
- Active enclosure spacing: yz requires `space_l == space_r`; xz requires `space_t == space_b`. Omitted values use the mesher's 25 mm defaults. Non-scalar values reject the affected plane. Enclosure values are ignored when enclosure depth is inactive or infinite-baffle mode takes precedence.
- Source geometry: the supported flat-disc and rounded-cap sources are axis-centred constructions derived from the sampled throat ring. `source.radius` and `source.curvature` only change the axisymmetric cap profile, so they introduce no additional lateral asymmetry. Unsupported `source.contours` already fails the authoritative translation path; the resolver conservatively rejects both planes with that failure.
- Infinite baffle: the coupled baffle and aperture are constructed from the sampled mouth and centred source rings and expose no independent left/right or top/bottom offsets. Their plane symmetry therefore follows the sampled surface. `mesh.vertical_offset` follows the same finite-scalar rule and rigid-placement treatment in this mode.
- Profile/formula geometry, morph target and dimensions, OSSE guiding curves and rotation, cross-section parameters, global rotation/scale, throat extension, and slot geometry are covered by the sampled surface itself.

## The rigid ground plane subtracts a plane

A ground plane (`SolveOptions.ground_plane`) is an infinite, perfectly rigid
reflecting surface through the origin, and the model is translated to stand
above it. It is named by the single axis it bounds -- `x`, `y`, or `z`, with
the fluid in `axis >= 0` -- never by an axis-pair token, because `xy` already
means legacy bi-symmetry here and x-and-y mirrors in BEAT, and it means the
z = 0 plane in `hornlab-bempp-bem`. WG's frame runs the waveguide axis along z
with y vertical, so `y` is the floor.

Standing the model off the surface is what removes the matching mirror plane: a
reduced mesh is cut *on* its plane and must touch it, while a model above a
ground plane must not reach it, and no mesh can do both. So a ground plane on
`y` subtracts xz, one on `x` subtracts yz, and one on `z` subtracts only the
legacy `xy` bi-symmetry that is never resolved anyway.

The subtraction is applied to the resolved `SymmetryResolution` rather than
inside `resolve_symmetry`, whose result is memoized on the design alone and is
also served to `POST /api/design/symmetry`, which knows nothing about solve
options. `auto` therefore degrades quarter to `half_yz` on a floor, and a
forced conflicting mode fails naming the ground plane among its reasons --
instead of the solver rejecting a reduced mesh that has already been built.

A ground plane and a coupled infinite baffle are two different half-space
boundaries and are refused together.

## Resolution and precedence

`auto` chooses quarter (`1`) when both planes hold, upper half (`12`) for xz only, right half (`14`) for yz only, and full (`1234`) when neither holds. A requested `full`, `half_xz`, `half_yz`, or `quarter` maps directly to those same masks. Explicit reduced modes are honoured only after validation; if a required plane is absent, submission fails with the plane name and all its rejection reasons. Explicit `full` always remains valid.

The resolved mask overrides `design.mesh.quadrants` only in the private request copy passed to mesh construction and the solver. The persisted request and design snapshot keep the original design value.

## Cost

On the development Mac in this batch, warm median time over seven calls with the default family designs was measured as follows (endpoint scheduling and JSON transport excluded):

| Family | Median resolver time |
| --- | ---: |
| R-OSSE | 92 ms |
| OSSE | 80 ms |
| ICW | 74 ms |
| FREEFORM | 747 ms |

FREEFORM is slower because its authoritative continuous cross-section reconstruction is substantially more expensive even at this coarse grid. Callers should debounce live edit requests and discard stale responses by design revision, just as they do for preview work.

## Axisymmetric formulation planner

Symmetry-domain reduction, formulation, and execution backend are independent
decisions. The solve planner considers the machine-local `solver_mode` before it
chooses a full-3D backend:

- `auto`: legacy wire spelling for Full 3D. It never selects Axisymmetric.
- `full_3d`: always use Metal, BEAT, or BEMPP full 3D.
- `circsym`: force the axisymmetric formulation and fail with the eligibility
  reasons if it cannot run. `circsym` remains the compatibility wire spelling;
  the product label is **Axisymmetric (meridian)**.

`axisym` is advertised independently by `/api/capabilities` and runs on CPU on
all supported operating systems, with optional Metal acceleration where present.
The backend selector therefore chooses the Full 3D implementation, not the
axisymmetric implementation. The meridian is refined from the highest requested
frequency, unlike a fixed Full 3D mesh. A rigid ground plane is never eligible
because the meridian formulation has no ground-image boundary; an explicit
Axisymmetric request is refused with that reason rather than changing formulation.

`Simulation.SolverMode` in legacy design text is a
machine setting, not a portable one, so it is never read from a design and
design export never writes it. A file that states one still opens: the value is
dropped during import and the drop is reported as migration
`006_machine_solver_mode_not_portable`, which the file-open report and
`wg validate --json` both carry. Any spelling is treated the same way, including
one that is not a valid mode, because no spelling of it was going to be
honoured. Opening a file is not editing it, so an untouched save returns the
author's bytes unchanged and the line survives there; the first real edit
serializes canonically and removes it.

Result/job symmetry metadata records `solver_plan` with the chosen
formulation, engine, reason, and eligibility reasons. Explicit Axisymmetric plans
also include `cost_evidence`: deterministic counts from the frequency-refined
meridian (unknowns, azimuthal quadrature work, matrix memory, and a revolved
full-3D triangle scale for the requested symmetry domain). These are transparent
complexity comparisons rather than machine-specific wall-clock promises.

## CAD returns that arrive already cut

A CAD return may state that the exported bodies **are** the reduced domain. The
statement is `assembly.domain` in `wgreturn.json`, gated by the
`reduced-domain-v1` entry in `required_features` so a reader that does not
understand it refuses the bundle rather than solving a half as a whole model:

```json
"domain": {
  "kind": "half",
  "cut_planes": ["y0"],
  "declared_by": "cad-author",
  "evidence": {"y0": {"min_mm": 0.0, "max_mm": 173.2, "tolerance_mm": 0.05}}
}
```

`kind` is `full`, `half`, or `quarter` and must agree with `cut_planes`, which
may name only `x0` and `y0` — the planes `imported_symmetry_from_cut_planes`
can express. `evidence` records what the writer measured on the bodies it
exported, per declared plane, and must still support the claim: geometry on the
positive side, none beyond tolerance on the negative side. The counterpart
writer and its own validation are `hornlab-fusion-addin`
`fusion-addins/WGLink/wglink_return.py` and `wglink_send.plan_domain`.

The declaration is not believed on its own. Ingestion keeps two sets of planes
apart: `symmetry.cut_planes` is what *this* preparation removed, and
`symmetry.domain_planes` is what the solver mirrors — declared planes plus cut
ones. Only the first predicts a halved source area, because only the first
halved a face here. `verify_symmetry_cut` then re-reads every domain plane from
the meshed boundary with a detector that knows nothing about what was declared,
and separates three ways a claim fails: the boundary spans both sides of the
plane, it lies on the negative side, or the plane is capped rather than open.

Two readers must never disagree about which planes those are, so
`server/solver/imported.imported_domain_planes` is the single resolver and both
the submit gate (`server/jobs/runtime.py`) and the Metal entry
(`server/solver/metal.py`) go through it. A reduced domain also cannot be
combined with a rigid ground plane on the same plane: a mirror plane is touched
and a floor is stood clear of, and an imported mesh cannot be re-cut the way
`restrict_for_ground_plane` re-cuts a parametric one, so the pair is refused as
`imported_symmetry_ground_plane_conflict` with the plane named.

A rejected **auto-cut** falls back to the full domain, because the whole model
is in the STEP. A rejected **declared** domain is refused: the other half is not
in the file, so meshing what arrived and solving it whole answers a different
question. `symmetryMode: "full"` is refused for the same reason.

`coordinate_system.export_frame` names which component's own frame
`assembly.step` is written in: `root-component`, or
`selected-occurrence-component` when the return was scoped to one occurrence.
Fusion's STEP export takes a Component and writes it in the component's own
coordinates, so the export scope decides the file's frame, and every coordinate
in the manifest — `assembly.bbox_mm`, each `instances[].assembly_from_link`,
the domain evidence — is in that frame. Absent means `root-component`, which is
what every bundle written before the member exported.

### The automatic domain (M1c-auto)

A writer that leaves the domain to WG requires `domain-automatic-v1` and writes

```json
"domain": {"kind": "automatic"}
```

with nothing else in it. The feature and the automatic kind are paired both
ways; a declared `full`/`half`/`quarter` never carries the feature. WG
advertises the feature as `"automaticDomain": 1` in `wg-capabilities.json`
(`server/cadlink/fusion_delivery.py`), and a writer uses it only then: a WG
without it refuses the return as an unknown required feature, so a new writer
is refused visibly by an old WG and never read as a declaration. An absent
domain keeps its meaning — a full model that WG may cut on its own validated
mirror test — so an older writer is unchanged. For the frame, `automatic` is
not a declared domain: every axis stays available (`solver_frame.allowed_axes`).

Under the feature, and only under it, the writer may record the cuts the CAD
timeline shows, read during the explicit export and never in the background:

```json
"cut_provenance": [
  {
    "body_object_id": "<a scope.included[].object_id>",
    "feature": {"kind": "split-body" | "extrude-cut" | "other", "name": "Split Body 3"},
    "tool": {"kind": "origin-plane" | "construction-plane", "origin_plane": "YZ" | "XZ" | "XY"},
    "plane": "x0" | "y0" | "z0",
    "kept_side": "positive" | "negative",
    "export_frame": "root-component" | "selected-occurrence-component"
  }
]
```

One entry per recorded cut of one exported body. `plane` is the cut plane in the
exported frame and must be the one `origin_plane` names (YZ is `x0`, XZ `y0`,
XY `z0`); a `construction-plane` tool is recorded only when it is coincident
with that origin plane (an offset of zero). `kept_side` is the side of `plane`
the body kept. `export_frame` must be the frame `assembly.step` is written in.
The reader checks the schema and that the body is an included one; everything
else is WG's revalidation.

WG decides with one detector (`server/cadlink/domain_interpretation.py`) that
reads the solve mesh, never the display mesh, and separates observations (per
CAD plane: which side the geometry is on, a free rim on the plane, faces lying
in it, sources meeting or bisected by it, open edges elsewhere) from
conclusions:

| Geometry | With no evidence | With evidence that revalidates |
|---|---|---|
| Whole model across a plane | today's mirror test and auto-cut, unchanged | not applicable there (the evidence is set aside) |
| Positive-side open rim on x0/y0, a source on the plane, nothing else open or capped (`candidate`) | **solved as shown**, "looks cut at x = 0" | mirrored, exactly as the same planes declared by hand; the other plane is still cut where it validates |
| A rim that is not a clean candidate, or a face on the plane that bisects a source (`ambiguous`) | solved as shown | mirrored when the plane is open, positive-side, uncapped and the only opening; otherwise refused |
| Negative side, capped cut, rim off the plane, z0 | solved as shown | refused, with the remedy in words (e.g. "keep the x ≥ 0 side and leave the cut open") |

Evidence, in precedence order: a declaration (the declared path above); the
user's own reading ("Change" on the model card,
`PUT /api/cadlink/domain-interpretation`), remembered per project; this
return's own `cut_provenance`; an earlier provenance-backed reading of the same
project. A previous "solved as shown" is never evidence. Evidence of this very
snapshot (its provenance, a Change made on it) that does not revalidate refuses
the preparation; evidence reused from the project that does not revalidate is
set aside and the model is solved as shown. Before any mirror WG revalidates:
the provenance belongs to an exported body and this frame; each plane is
open over its whole section on the positive side with no cap and no other
opening; the mesher's declared-domain verification (open section, leaks,
winding) passes; the sources resolved by their own faces; and, at every
submission, the excitation is invariant under the reflection
(`excitation_problem`). A reduced reading is solved only as modelled (+Z) until
M1d.

The reading is recorded on the ingestion record (`domain_interpretation`),
enters the mesh cache key whenever it changes what is solved, and is part of a
preparation's identity, so a changed reading is a new preparation and no
approval carries to it. It replaced the blocking `undeclared-reduced-domain`
finding: the non-blocking `domain-solved-as-shown` and
`interpreted-reduced-domain` findings record which reading was solved.
