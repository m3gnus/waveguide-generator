# Review slices

All paths below are relative to the WG repository root. The target is official
`JWSound/BEAT_Engine` (`beat-engine`, import `beat_engine`), superseding the
design's fork references. Changes are additive and intentionally uncommitted.

- **W2 / observation PR:** `server/solver/beat_adapter/observations.py`;
  `server/tests/beat_adapter/test_observations.py`;
  `server/tests/beat_adapter/fixtures/{hbb_observations.json,README.md}`.
  Ordered arbitrary exterior points retain HBB polar-angle precision, diagonal
  inclination and theta-major sphere ordering. Explicit metre-frame vectors
  apply to cuts and sphere together; exact Float64 sphere axes feed DI/balloon.
- **W3 / result PR:** `server/solver/beat_adapter/results.py` (decoder,
  `result_outputs`, `parse_frequency`, conversion and native-result properties);
  `server/tests/beat_adapter/test_results.py`.
  Negative-time phasors, unit acceleration, reduced physical source area times
  real symmetry copies, absolute SPL, per-plane normalized directivity and
  untouched P1-node/DP0-face trace ordering. No legacy impedance wire scaling
  is applied to official mechanical impedance.
- **W5 / sweep PR:** `server/solver/beat_adapter/results.py` (`map_sweep`);
  `server/tests/beat_adapter/test_sweep.py`.
  Requested execution order and Float64 frequencies survive decoding; strict
  terminal counts, prefix-only cancellation, callback cancellation and stream
  closure on all exits. Missing/extra/reordered results and events after a
  terminal fail. Session staging and worker retirement remain runtime-owned.
- **Shared scaffolding:** `server/solver/beat_adapter/__init__.py`, this file;
  `server/tests/beat_adapter/{__init__,conftest}.py`. Wire fixtures are in memory.

## Boundaries and deviations

The W2 fixture is an independent scalar Python transcription of the inspected
HBB Julia formulas, documented beside the fixture; no Julia was run. HBB's
pure Python frame/request and result helpers supply additional comparisons.
This is contract coverage, not same-mesh CPU/Metal numerical qualification.

Unlike HBB's broad frequency echo tolerance, the decoder compares at negotiated
solver precision, because official exterior results cast frequency to Float32
or Float64. Requests that alias at that precision are refused so reordered rows
cannot hide inside a tolerance. The durable axis stays exactly as requested.
Cancelled results are marked even after the final row; `is_partial` follows
HBB's count-based definition and is false when every row arrived.

**W4 remains a later slice.** `parse_frequency` retains mechanical
`radiation_impedance` separately and refuses to treat generalized non-normal
force as historical source-average pressure. W4 must integrate boundary P1
pressure over the tagged source triangles with their physical areas, in the
compiled node/face order, and supply `mean_pressure_velocity` for curved,
tilted, rear-facing or signed axial drives. Surface traces are already scaled
by `1/(-i*omega)`; undo that scale before deriving a velocity-basis mean.
Multi-port selection/synthesis and electrical driver loading are not implemented.

No existing caller, pin, requirement, runtime namespace or HBB directory changes.
The adapter imports neither HBB nor beat_engine; existing NumPy is its only
non-standard-library dependency. Callback cleanup closes the owned public
stream; it never signals a PID or assumes engine-private ownership tokens.
