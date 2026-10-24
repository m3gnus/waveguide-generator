# Dependency compatibility

The mesher pin `b09819ca` is an untagged interim pin pending the 0.3.6 release.
When it cuts a solid to its symmetry domain it keeps periodic (seam-closed) faces
such as a throat cylinder, which the earlier cutter could drop, and it raises
`RuntimeError` ("OCC cut lost retained portions") rather than return a cut that
lost retained geometry; WG then meshes the whole model.

It also refuses enclosures that leave less than 0.1 mm of front baffle beside the
mouth on any physical wall side. Preview and mesh construction share the
mesher's enclosure-bounds validation; solve admission calls its public
`resolve_enclosure_bounds()` with the final execution domain. The refusal
names the side and the margin to increase. Measurement-only callers use
`measure_enclosure_box_bounds()` without admitting geometry for construction.

This pin also supplies native source-contour, front-baffle woofer,
shared horn/woofer and passive phase-plug contracts, and the public
`printable_surface_tags()` that STL export uses. Its ATH import contract is
versioned: designs saved with the `ath-2026-08c-v1` stamp build exactly as
before, including bounded near-planar imported rear returns (pointwise
displacement at most the smaller of 0.0001 mm and 0.0001 times the wall
thickness). Fresh ATH imports are stamped `ath-2026-09a-v2` and follow ATH
V2026-09a: the ATH expression functions with natural `log`, NAC, current ATH
axial sampling, `Mesh.Roundover` lips, `Mesh.RearShape = 3` rears, warped
RearShape 1 rears and the corrected import defaults. STEP export of lip or
RearShape 3 imports is refused by name. Native rear construction is unchanged.
See [Native source contour ingestion](architecture/NATIVE-SOURCE-CONTOURS.md)
and [Source contour and assembly editors](architecture/source-contour-editor.md).

The Metal `7c461f7` and BEM++ `8e69106` pins provide opt-in `solve_compiled`
facades for the prescribed-source exterior-pressure subset of BEAT inputs.
They reuse WG's request compiler and the pinned BEAT contract. Native pressure
comparisons establish input-translation equivalence to independent calls to
each same backend; they do not establish numerical equivalence to BEAT.
Metal retains native quadrature and omits BEAT order overrides; BEM++ retains
native orders 4/4. Full production output requests remain refused.
See [Installed legacy BEAT input qualification](LEGACY-BEAT-INPUT-QUALIFICATION.md).

These pins do not change production routing or engine defaults. Source and
consumer checks remain distinct from installed application qualification,
Windows installed qualification, acoustic performance and manufacturing suitability.
