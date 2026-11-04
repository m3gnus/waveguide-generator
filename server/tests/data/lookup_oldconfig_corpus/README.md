# Older LOOKUP saved-design compatibility corpus

These saved JSON designs cover implicit dimensions, a morph with an axial fixed
part, source angle with scale and a reduced domain, and an 81-point table. They
open as FREEFORM designs through the shared S4 converter.

The frozen reference is Waveguide Generator `71fce3e63ee890e9b14f1c652f43626280b00ff8`
with its 0.3.6 mesher pin `4ae1cfa69d63f2c34655f09a39a72ef953c6115e`, on
Darwin arm64. All four cases also matched mesher
`38288949147b84716eddf7b40244b1e77e58b90d` before the server changes.
The native environment used Gmsh 4.15.2, NumPy 2.4.6, SciPy 1.17.1,
meshio 5.3.5 and Pydantic 2.13.4.
Each fixture records the original input, its translated and solver configurations,
and hashes of its solve mesh and normalized manufacturable STEP. The existing
FREEFORM corpus helpers perform the real load, save, translation and builds and
normalize only the same STEP timestamp and writer counters. Each baseline mesh
and STEP was built twice before freezing; both builds agreed byte for byte.

The importer now opens these files as Custom profiles. The original resolved
configurations and artifact hashes remain historical evidence; they are not
expectations for re-solving the converted design. Stored run artifacts are
never rewritten by import.

The tests compare authored H/V curves with the original linear interpolation,
check exact straight-run reduction, the 64-anchor and 1 mm corner-spacing fit,
source-cap shape, snapped morph construction, and normal Freeform.* save/reopen.
The generated-grid comparison is sampled; it does not certify OCC wall distance.
`dense-curve.json` and `close-corners.json` add generated fit-limit inputs.
