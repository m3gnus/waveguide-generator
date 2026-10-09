# Shared native horn and woofer

The native adapter accepts required features native-source-contour-v1 and
native-shared-horn-woofer-v1 from the mesher's SourceAssembly/export_assembly.
It reopens the exact STEP, verifies finite canonical source and rigid faces,
their areas and actual shared edges, then uses the general imported mesher.
The final mesh must be watertight, consistently wound, forward on moving faces,
and certified within0.15mm on all moving and rigid facets. Existing22000-triangle
and host-memory admission gates remain unchanged. A restricted field refines
only the verified rigid horn-wall selector; it never becomes a source role.

POST /api/source-editor/assembly/validate accepts horn and woofer SourceDocuments,
dimensions and optional mesh_size_mm. Both documents are the contour editor's
canonical shape/drive format. Dimensions are width_mm, height_mm, depth_mm,
front_z_mm, horn_xy_mm, horn_length_mm, mouth_radius_mm, woofer_xy_mm and
aperture_radius_mm. Physical source, rim and channel identities must be distinct.
The initial scope uses parallel+Z axes and a straight circular conical horn.

POST /api/source-editor/assembly/ingest builds the exact native bundle and
publishes an immutable imported ingestion in the existing CAD-link registry.
Its geometry response is a complete imported SolveRequest.geometry; submit it
to the existing job API with engine metal. It includes exactly two authoritative
channels, explicit required features, immutable artifact/manifest hashes and
mesh sizes. It also returns source identities/frames and geometry certification.
The endpoint is restart-gated and shares the bounded source export lock.
Older installed meshers refuse this feature with503; application startup stays
usable. The editor's existing single-source UI is unchanged.

Source-qualified patch IDs are percent-encoded source/local identities, separated
by /. Source frames identify the horn throat and woofer rim in metres; the common
observation frame origin is(0,0,front_z_mm/1000). Native request admission rejects
changed weights/membership, skipped patches, stale features, conflicting anchors
or transforms. Each physical diaphragm remains one channel, including zero-weight
moving surround patches; rigid lands stay undriven. Result and persisted basis
metadata retain both physical source and common observation frames.

The existing shared multi-RHS Metal adapter receives both channel specifications
on the same mesh. Inactive source velocity is zero; every rigid scattering face
remains present. Existing complex combination applies engineering gain/delay/
polarity conjugated into the solver convention. CPU tests inspect real emitted
BCs and compare recombination with a direct weighted coupled linear-system fixture
using those BCs. The fixture verifies dispatch/phase algebra, not native acoustic
accuracy. Native runtime, installed dependency pins and acoustic convergence are
separate qualification. Neither production solver numerics nor pins change here.
