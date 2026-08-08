# Solve symmetry contract

Automatic symmetry is a solve-time decision. It never edits or rewrites the ATH `Mesh.Quadrants` value in the design document. The submitted design snapshot therefore retains the imported spelling, while job and result metadata record the requested symmetry mode, the resolved mask, both plane decisions, rejection reasons, and the original design value.

## Geometry authority

The resolver passes the validated design through `server.preview.translate.design_to_mesher_config`, then through `hornlab_mesher.config_builder.build_geometry_params` and `build_point_grid`. It forces a full-circle sampling grid with 128 angular samples and 16 axial intervals. This is the same profile, morph, guiding-curve, cross-section, scale, source-boundary, and outer-wall geometry evaluation used before the OCC solve mesh is built; it does not infer symmetry from field names.

Both the inner surface and any freestanding outer surface are checked. For each axial ring, every reflected point is matched to its nearest point on the original ring's closed sampled polyline. Array position and row order are not assumed, and a reflected vertex may match the interior of a different azimuth segment. The xz check reflects `y -> -y`; the yz check reflects `x -> -x`.

The accepted distance is `max(1e-7 mm, 2e-4 * D)`, where `D` is the sampled model's bounding-box diagonal in millimetres. The relative term absorbs floating-point evaluation and modest sampling-lattice mismatch while remaining 0.02% of the model size. A miss reports both the measured maximum deviation and the tolerance. Sampling, translation, or non-finite-grid failures reject both planes and select the full domain.

## Inputs audited outside the point grid

The point grid does not contain every terminal surface, so the resolver separately audits:

- `mesh.vertical_offset`: Only a non-finite or non-scalar value rejects xz. A finite non-zero value is a rigid +y placement applied after the cut planes are built at the origin; `_solver_mesher_config` drops that placement for y-cut domains (quadrants 1 and 12), so it cannot destroy the xz reduction. It does not affect yz.
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

## Metal axisymmetric path

Symmetry-domain reduction and Metal's axisymmetric meridian path are independent decisions. After engine resolution, Metal interprets `simulation.solver_mode` as follows:

- `auto`: run the meridian path when `circsym_rejection_reasons` is empty and the installed Metal/mesher capability is ready; otherwise run full 3D and record the reasons.
- `full_3d`: always run full 3D.
- `circsym`: force the meridian path and fail with the eligibility reasons if it cannot run.

CircSym remains an internal adapter and is not an engine advertised by `/api/capabilities`. Metal advertises `axisymmetric-meridian` in its `fast_paths` capability when it is available. Result and job metadata expose `solve_path`, `axisymmetric_eligibility_reasons`, and `solve_wall_time_seconds`.
