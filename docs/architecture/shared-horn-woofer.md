# Shared native horn and woofer

The native adapter accepts required features native-source-contour-v1 and
native-shared-horn-woofer-v1 from the mesher's SourceAssembly/export_assembly.
It reopens the exact STEP, verifies finite canonical source and rigid faces,
their areas and actual shared edges, then uses the general imported mesher.
The final mesh must be watertight, consistently wound, forward on moving faces,
and certified within0.15mm on all moving and rigid facets. Existing22000-triangle
and host-memory admission gates remain unchanged. A restricted field refines
only the verified rigid horn-wall selector; it never becomes a source role.

The old Experimental assembly editor and its assembly authoring routes are
retired. These native artifact and imported-request contracts remain for existing
bundles and stored runs. `server.cadlink.intake.native_source` delegates shared
assembly recipes to `native_assembly.read_assembly`, which reconstructs the exact
mesher `SourceAssembly` recipe. Standalone baffle export also still needs the
mesher's `FrontBaffle`; neither module can be removed with the old editor.

Stored results replay through the existing job results API directly from their
persisted payload. Reopening an imported run uses its stored workspace and
prepared mesh through the shared imported-model workflow. It does not invoke
the retired editor or preparation helper.

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
