# Exterior adapter controls

Status: frozen mathematical controls for official-engine unit tests.

`exterior_observations.json` records scalar Float64 coordinates for polar cuts
and theta-major spheres. `exterior_controls.json` records frame translation,
pressure-loading normalization, directivity and analytic sphere controls.
These controls were originally recorded while HBB was supported; their origin
is historical and they do not require or execute that package. Comparison evidence
is retained in `docs/validation`. The removed rollback response snapshots and
snapshot-generation command are no longer part of the harness.

`wg_request_compatibility.json` records request compatibility controls. Tests
independently check coordinate order, phase, source area and force normalization.
