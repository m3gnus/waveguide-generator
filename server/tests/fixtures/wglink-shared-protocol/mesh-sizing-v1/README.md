# Shared synthetic returns

These are protocol and STEP-selector fixtures, generated entirely from boxes
and rectangular source patches by `scripts/generate_mesh_sizing_fixtures.py`.
No retained return data or geometry is copied. Geometry generation uses gmsh's
Open CASCADE exporter; headers contain a fixed anonymous timestamp.

- **F1** (`f1-single-source.wgreturn`): one 500 × 287 × 275 mm body, centered
  across x/y, with one HF patch and an automatic domain. The Back group covers
  the opposite face and crosses both potential symmetry cuts.
- **F2** (`f2-quarter-multi-source.wgreturn`): one synthetic 240 × 190 × 780 mm
  quarter body with declared x0/y0 cuts and LF, MF, HF and passive/port patches.
  LF, MF and port each have two disconnected faces. Back uses 40 mm.

Both carry full `mesh-sizing-v1` blocks, managed STEP appearance labels and
matching observed face areas. They exercise contract shape and selector
resolution; their triangle counts are not predictions for the original models.
WG's G1/G2 tests should mesh these generated geometries at their declared sizes.
See [the contract](../../../docs/MESH-SIZING-V1.md).

`pre-w1-protocol.py.gz` is the frozen, compressed validator from the commit in
`PROVENANCE.json`, with an uncompressed SHA-256. It is test evidence, never
imported by production. The two capability files are synthetic projections of
WG 0.3.5 / 0.3.6-old: existing delivery/features, without `meshSizing`.

Regenerate STEP/JSON with `python scripts/generate_mesh_sizing_fixtures.py` in
an environment containing gmsh. Geometry output may vary across gmsh/OCC
versions; review regenerated evidence rather than refreshing a hash blindly.
