# Older LOOKUP saved-design compatibility corpus

These saved JSON designs cover implicit dimensions, a morph with an axial fixed
part, source angle with scale and a reduced domain, and an 81-point table. They
remain LOOKUP designs and do not pass through a FREEFORM conversion.

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

The test always checks the resolved configuration and the save/reopen path.
It builds every artifact on every platform, but compares exact bytes only on
Darwin arm64 because native meshing is not bit-identical across platforms. It
does not prove compatibility for all possible LOOKUP designs or native-library
versions.
