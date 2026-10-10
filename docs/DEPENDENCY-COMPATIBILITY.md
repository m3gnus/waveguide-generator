# Dependency compatibility

The mesher pin `a515334c` supplies native source-contour, front-baffle woofer,
shared horn/woofer and passive phase-plug contracts. It also preserves saved
ATH geometry interpretation and permits bounded near-planar imported rear
returns, with pointwise displacement at most the smaller of 0.0001 mm and
0.0001 times the wall thickness. Incompatible accepted rear sampling planes
and larger warps remain refused; native rear construction is unchanged.
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
