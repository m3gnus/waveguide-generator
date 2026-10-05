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

The decoder checks frequency echoes at negotiated solver precision, because
official exterior results cast frequency to Float32 or Float64. Distinct
Float64 requests may alias at Float32: each result row maps by request order,
retaining the exact requested axis. Rounded echoes cannot distinguish reordered
rows within such an alias group; terminal counts and non-aliased echoes remain
checked. Exact Float64 duplicate requests are refused, matching HBB.
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

Optional schema tests prefer importable `beat_engine` resources, then
`WG_BEAT_ENGINE_SRC` (repository root, src directory or package directory), then
workspace checkouts. Only schema checks skip when the schema/validator or its
requested contract version is missing. Pure quadrature, axial, W4 loading and
observation parity assertions use frozen/in-memory controls and always run;
HBB private helpers are compared only in separate optional cross-checks. These
fake-result tests do not qualify numerical CPU/Metal parity or installed
startup performance, and run no Julia. No request or cancellation file is
written by this layer; staging/lifetime remain runtime responsibilities.

No existing caller, pin, requirement, runtime namespace or HBB directory changes.
The adapter imports neither HBB nor beat_engine; existing NumPy is its only
non-standard-library dependency. Callback cleanup closes the owned public
stream; it never signals a PID or assumes engine-private ownership tokens.


## Review round 1 fixes

- **P2/A — fixed:** reject legacy `xy`, bare `y` and solver-native `x` inputs.
  WG's `yz`, `xz`, `yz+xz` and full-domain names select explicit image modes.
  Keep the existing proper rotation for WG's unambiguous `xz` half.
- **P2/B — fixed:** validate uniqueness in Float64 only; keep both rows of
  `[500.0, 500.000001]` on Float32, mapping by request order through fake results,
  loading, callbacks and durable logs. No rounded-value lookup is used.
- **P2/C — fixed:** portable resource/environment/workspace discovery, isolated
  schema checks, frozen frame/force/directivity controls, and optional HBB
  cross-checks. Regressions cover discovery precedence, absence, version support
  and pure request/axial/loading assertions with optional imports blocked.
- **P2/D — fixed:** accept only Gmsh points (15), lines (1) and linear triangles
  (2); every other type raises an error naming its number. This exterior path
  has no coupled volume elements. Tests include quads, prisms and other types.
- **P3 ground clearance — fixed:** retain the existing HBB-equivalent wire
  forwarding and pre-submit clearance guard; regression coverage now exercises
  both WG parametric and imported entry points with ground enabled.
- **P3 face areas — fixed:** promote triangle coordinates to Float64 before
  subtraction, cross products and norms, regardless of solver precision.
- **P3 duplicate frequencies — fixed (clarified/tested):** exact Float64
  duplicates still raise, matching HBB `sweep.py`'s `np.unique` validation.
- **P3 multi-source performance — fixed (note only):** N independent source
  bases remain N engine ports, with fields summed by WG channel. Qualification
  must measure this cost against HBB's combined-source solves; no change here.

Files per manifest row for this round (paths relative to the repository):

- **W2 / request, mesh, observation PR:**
  `server/solver/beat_adapter/{request,mesh}.py`;
  `server/tests/beat_adapter/test_{request,observations,contract_discovery}.py`.
  `observations.py` needs no implementation change.
- **W3 / result and loading PR:** `server/solver/beat_adapter/results.py`;
  `server/tests/beat_adapter/test_{results,driver_loading}.py`.
  `driver_loading.py` needs no implementation change; it uses corrected areas.
- **W5 / sweep PR:** `server/solver/beat_adapter/results.py`;
  `server/tests/beat_adapter/test_sweep.py` and the narrow-sweep end-to-end
  regression in `server/tests/beat_adapter/test_request.py`.
- **Shared W2/W3/W5:** this `CHANGES.md`;
  `server/tests/beat_adapter/conftest.py`;
  `server/tests/beat_adapter/fixtures/{hbb_controls.json,README.md}`.

Design deviations: official JWSound remains the target. Float32 alias refusal
from the initial adapter is removed to preserve HBB narrow sweeps. The existing
rotated `xz` half remains representable, while ambiguous legacy aliases refuse.
No production adoption, engine-specific extensions, dependency changes or
numerical solves are included. Work remains uncommitted as requested.

## W8 / W9 qualification tooling (PLAN slice 8)

- **W8 / capability and conformance PR:**
  `server/solver/beat_adapter/capabilities.py`;
  `scripts/beat_conformance/{__init__,__main__,analytic,cases,recorder}.py`;
  `server/tests/beat_adapter/test_{capabilities,conformance}.py`.
  Builder calls determine support and refusal reasons. HBB's three declared
  cases retain failed-result/comparator evidence and analytic phase controls.
- **W9 / anti-vacuity PR:** `scripts/beat_conformance/recorder.py`;
  `server/tests/beat_adapter/test_conformance.py`. Required per-case frequency
  solve floors, Julia/engine identity, per-result backend/precision/convention
  and CPU/Metal device assertions; synthetic/static-only runs cannot qualify.
- **Numerical agreement PR (PLAN §5 / W10 gate preparation):**
  `scripts/beat_conformance/agreement.py`;
  `server/tests/beat_adapter/test_agreement.py`. Resonances precede norms,
  fixed named thresholds, reference 30 dB mask, nulls, normalized impedance,
  comparable DI/power and immutable mesh/settings gates use only NumPy.
- **Shared documentation:** `scripts/beat_conformance/README.md`, this file.
  Existing W2/W3/W5 implementation files are unchanged; their request/result
  contracts are exercised through the fake compiled-wire conformance tests.

Deviations: official JWSound supersedes the design's fork target. The optional
comparator is injected instead of launching HBB or hornlab-metal-bem automatically;
its absence is an explicit limitation. HBB's 0.6 degree faceted-sphere band is
retained. Numerical resonance prominence and dense step are declared per corpus;
sampled extrema require bracketing and local refinement below 0.5%. Real runner,
installed/device evidence, full migration corpus and performance measurements
remain compute-broker qualification work. No production routing, pins,
requirements, runtime directories, HBB files or blanket skip policies change.
Changes remain uncommitted as requested.
