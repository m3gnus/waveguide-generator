# Native source contour ingestion v1

The producer API and canonical model are documented in the mesher's
`docs/source-contours.md`. This coordinated additive contract uses
`server.cadlink.native_source.ingest_native_source` with `CadLinkStore`, a data
directory and `ImportedMeshSizes`. It publishes a normal immutable ingestion
ID usable by the existing imported solve job route. No managed CAD-return
manifest or `linked_throat` disc is fabricated.

`source.json` is native authoring authority. Required version 1 and feature
`native-source-contour-v1` negotiate the semantics. Exact STEP/MSH member
hashes bind temporary ADVANCED_FACE selectors; canonical line/arc geometry,
finite branch, patch areas and horn/housing coverage are checked on reopened
STEP before general imported meshing. Snapshot copying is reverified before
the isolated worker runs; invalid publications allocate no ingestion record.

Curved moving patches retain a curvature-derived size cap; the native path
uses a 0.02 mm chord target for moving arcs and 0.03 mm for the housing size
cap, alongside the
general importer's supported 0.1 mm sagitta setting. The record distinguishes requested and effective
mesh sizes and hashes the effective density separately from geometry and
excitation. Every persisted moving and rigid facet must pass a finite-meridian
1-Lipschitz certificate within 0.15 mm, with closed, consistent winding and the
producer's triangle budget. Uncertified meshes are refused before publication.

The ingestion's `native_source.channel` authors one physical source/channel
with moving patch IDs, exact `patch_weights`, `physical_source_id` and motion.
The imported request repeats that contract and declares the required feature;
contradictions and skips are refused at planning, submission and direct Metal
entry. Source IDs at this boundary are patch IDs. Rigid patches have no drive
assignment and remain in the common scattering geometry. Zero-weight moving
patches remain tagged sources.

Normal motion has a constant face multiplier. Axial v1 motion uses explicitly
aligned +Z per-source axes under the consumer's existing per-source-axis-v2
contract, independent of the observation frame or triangulation sign votes.
Metal source dictionaries retain actual literal patch weights. Result/channel
basis metadata includes physical grouping, patch weights and excitation hash.
Other engines, arbitrary axes, lumped drivers, folded sources, UI ingestion and
standalone linked source transport remain outside this slice.

Consumer qualification uses actual native STEP ingestion, immutable persisted
MSH, job admission and production dispatch. The acoustic solve is intercepted;
the pinned BEM module's CPU profile evaluator is checked per face against an
independent projection oracle. This proves transport/BC behavior, without
claiming acoustic accuracy or installed/pin qualification. Old readers reject
the new request fields and required-feature vocabulary. The lander must install
the producer revision before moving the consumer dependency pin.
