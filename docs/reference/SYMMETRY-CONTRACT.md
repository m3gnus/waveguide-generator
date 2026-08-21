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

- `auto`: use the platform-neutral `axisym` meridian runner when the authoritative
  mesher eligibility predicate succeeds; otherwise use the selected/AUTO full-3D
  backend and record every rejection reason.
- `full_3d`: always use Metal, BEAT, or BEMPP full 3D.
- `circsym`: force the axisymmetric formulation and fail with the eligibility
  reasons if it cannot run. `circsym` remains the compatibility wire spelling;
  the product label is **Axisymmetric (meridian)**.

`axisym` is advertised independently by `/api/capabilities` and runs on CPU on
all supported operating systems, with optional Metal acceleration where present.
The backend selector therefore chooses the *full-3D fallback*, not the
axisymmetric implementation. `Simulation.SolverMode` in legacy design text is a
machine setting: import may recognize it for compatibility, but design export
strips it. Result/job symmetry metadata records `solver_plan` with the chosen
formulation, engine, reason, and eligibility reasons. Axisymmetric AUTO plans
also include `cost_evidence`: deterministic counts from the frequency-refined
meridian (unknowns, azimuthal quadrature work, matrix memory, and a revolved
full-3D triangle scale for the requested symmetry domain). These are transparent
complexity comparisons rather than machine-specific wall-clock promises.
