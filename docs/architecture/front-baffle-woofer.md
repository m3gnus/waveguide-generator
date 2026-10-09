# Native front-baffle woofer ingestion

The mesher's `docs/front-baffle-woofer.md` defines the canonical source and
finite rectangular baffle recipe. The shared native adapter accepts exactly
the required features `[native-source-contour-v1, native-front-baffle-woofer-v1]`
for this route. The solve request repeats both features; dropping either,
altering weights/motion, skipping moving patches or supplying a lumped driver
is refused by the existing authoritative request guard.

Native STEP selectors are verified against exact member hashes, canonical
source meridians in their translated frame, finite rigid rectangles/circular
trims and analytical areas. This includes equal-area left/right faces, so a
selector swap cannot silently move a source or rigid boundary. There are six
enclosure faces plus an optional collar. Invalid evidence allocates no record.

Imported remeshing uses curvature caps and the existing 0.1 mm surface sagitta
setting. Every persisted moving and rigid facet must pass a finite-surface
1-Lipschitz covering certificate within 0.15 mm. Requested/effective density is
recorded separately; triangle and memory admission guards retain their limits.
Full closed topology, consistent winding and forward moving normals are gates.

The record has native LF patch roles and one grouped physical source/channel.
`normalisation.source_frame` carries its translated source origin, axis +Z,
horizontal +X and vertical +Y. The common imported frame reader supports this
explicit frame; its observation origin is the source centre. There is no CAD
anchor, horn mouth or linked-throat assertion. Existing imported records retain
their frame path. Drive weights, axial axes and result/channel basis use the
native source contour contract without special acoustic solving code.

Qualification covers real STEP ingestion, persisted mesh geometry, production
job admission/dispatch and CPU normal/axial BC multipliers. This provides
geometry and transport evidence; it makes no acoustic or installed/pin claim.
The lander integrates the contour dependency first, then the woofer module,
then the consumer with a separate reviewed dependency-pin update.
