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

## Request-side review slices (slices 6–7)

- **W2 / request and frame PR:** `server/solver/beat_adapter/{request,mesh}.py`;
  `server/tests/beat_adapter/test_request.py`; request/schema/mesh fixtures in
  `server/tests/beat_adapter/conftest.py`. Parametric contexts and imported
  records retain engine IDs, source IDs/tags, anchor axes and channel order.
  Packed system-v1 meshes keep node rows, triangle rows and winding. Normal
  sources use contract v1; PR #18 rigid translation uses contract v2 with
  explicit signed axes. Independent source bases are summed by WG channel ID.
  CPU passes wavelength/p90/q1=0/q2=2/base-q4/s4; Metal retains fixed q4/s4.
  Adaptive callers supply each acquired batch verbatim, including unordered
  frequencies; default execution uses WG's existing live frequency plan.
- **W3–W4 / loading and channel-result PR:**
  `server/solver/beat_adapter/{driver_loading,results}.py`;
  `server/tests/beat_adapter/test_driver_loading.py`; shared `conftest.py`.
  Tagged P1 triangle pressure is area averaged before unit-acceleration
  conversion, without signed axis projections. Pressure-only outputs support
  loading when field traces are disabled. Generalized engine impedances stay
  available by port; independent fields, sphere and traces sum by channel.
  Fixtures check normal/axial flat, curved, tilted and rear sources, real
  symmetry copies and fictitious ground images, acoustic loading and the
  existing electrical driver calculation against historical pressure loading.
- **W5 / sweep extension PR:** `server/solver/beat_adapter/results.py`;
  sweep-loading coverage in `server/tests/beat_adapter/test_driver_loading.py`.
  Single-port sweeps accept compiled loading topology and retain generalized
  impedance separately, including a cancelled prefix and stream closure.
- **Shared documentation:** this `CHANGES.md`.

Request-side boundaries: official ground is Y=0. WG uses a common proper
rotation for x/z ground or a y-only half, applying it to mesh, observations
and motion directions while retaining original frame/IDs. Ground combined
with reduced symmetry is explicitly refused because official BEAT exposes
only one image-transform mode. Infinite-baffle/FEM, passive-cardioid modelling,
CUDA/ROCm, and production caller adoption are outside this exterior adapter.

Requests always include all constructed independent source ports: the official
exterior impedance output iterates every component. Its per-component values
are self-loading diagonals, so a multi-source channel's scalar generalized
impedance remains `None`; cross-force terms cannot be inferred by summing
those diagonals. Historical channel pressure loading includes all cross
pressure on its tagged surfaces through the retained boundary-pressure bases.

Schema tests load the official checkout's standard-library system-v1 validator
and JSON schema without importing beat_engine. If that checkout predates
PR #18, they use the read-only `/private/tmp/beat-batch1-candidate`; if neither
has the schema with contract v2, tests skip with an explicit reason. These
fake-result tests do not qualify numerical CPU/Metal parity or installed
startup performance, and run no Julia. No request or cancellation file is
written by this layer; staging/lifetime remain runtime responsibilities.

No existing caller, pin, requirement, runtime namespace or HBB directory changes.
The adapter imports neither HBB nor beat_engine; existing NumPy is its only
non-standard-library dependency. Callback cleanup closes the owned public
stream; it never signals a PID or assumes engine-private ownership tokens.
