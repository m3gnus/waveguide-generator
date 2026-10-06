# Review slices

All paths below are relative to the WG repository root. The target is official
`JWSound/BEAT_Engine` (`beat-engine`, import `beat_engine`), superseding the
design's fork references. Changes are additive.

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
numerical solves are included. Production callers remain unchanged.

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
sampled extrema require bracketing and local refinement below 0.5%. Real hardware,
installed/device evidence, full migration corpus and performance measurements
remain compute-broker qualification work. No production routing, pins,
requirements, runtime directories, HBB files or blanket skip policies change.
This tooling section describes commit `2e1e57b5`.


## Review round 1 fixes — qualification tooling (`2e1e57b5`)

- **P1/A — fixed:** SciPy (already in WG runtime requirements) supplies true
  topographic prominence for peaks and inverted dips. Failing controls rerun
  the old adjacent-extremum algorithm: removed and four-step-shifted rippled
  resonances pass there, then fail the new gate. Empty-extremum columns are
  reported; declared expected resonance columns fail on monotone references.
- **P1/B — fixed:** CLI selection cannot shrink the fixed CPU required set;
  Metal runs additionally require the Metal case. Missing cases are named,
  `qualified=False`, and selected successful records remain available.
  Required names also retain their canonical declarations/request hashes.
- **P2/C — fixed:** directivity must be zero at the reference angle. A +3 dB
  offset still passes the old spread criterion but fails the restored assertion.
- **P2/D — fixed:** `EngineRun` supplies launch selection only. Recorder-owned
  subprocess checks run the actual Julia binary and record version/content
  identity; an independent Metal integer kernel must dispatch and return the
  expected values. Loaded engine metadata/revision and content identity are
  independently inspected. The recorder owns the public worker and counts its
  checked terminal events, never result-array length or `real_solves` claims.
  Qualification requires observed backend/precision/device/solve-count fields.
  **Still attested:** precomputed `SolveEvidence` fields (cannot qualify),
  worker temperature, and installed `engine_revision` from VCS metadata
  (its correspondence to upstream bytes is unverified); no upstream tree is downloaded. Source revision requires a clean
  tracked engine tree. Installed classification is independently inspected.
- **P2/E — fixed:** add and require `metal_exterior_float32` for a declared
  Metal run, with the same exterior acceptance and a two-frequency solve floor.
- **P2/F — fixed:** nonempty distinct revisions; frozen backend/precision;
  optional paired record/hash/mesh/revision bindings. Forced-LU budgets derive
  from reference-record native `linear_solver` evidence, matching official
  BEAT's diagnostics. Unbound comparisons remain explicitly attested.
- **P3 static refusals — fixed:** an explicit expected refusal set must equal
  the observed set; newly accepted or newly refused scenarios fail the case.
- **P3 feature names — fixed:** declared feature names validate before probing;
  misspellings raise instead of becoming API-absence refusals.
- **P3 sphere shape — fixed:** exactly `(1, 2, 5)` pressure samples required;
  a single analytic sample can no longer pass the sphere case.
- **P3 provenance — fixed:** record override names plus SHA-256 value hashes;
  no environment value, interpreter path or prefix is copied verbatim.

Files per PR / manifest row for this fix round:

- **W8 capability/conformance:** `server/solver/beat_adapter/capabilities.py`;
  `scripts/beat_conformance/{cases,analytic}.py`;
  `server/tests/beat_adapter/test_{capabilities,conformance}.py`.
- **W9 qualification:** `scripts/beat_conformance/{__main__,recorder,verification}.py`,
  `scripts/beat_conformance/assert_metal_device.jl`;
  `server/tests/beat_adapter/test_{conformance,verification}.py`.
- **W10 agreement preparation:** `scripts/beat_conformance/agreement.py`;
  `server/tests/beat_adapter/test_agreement.py`; record hashing shared with W9.
- **Shared W2/W3/W5 documentation:** this file and
  `scripts/beat_conformance/README.md`. W2 requests, W3 directivity/analytic
  results and W5 terminal handling are exercised by the conformance controls;
  their implementation files are unchanged.

Design deviations: the real runner now returns launch settings rather than
self-attested solve evidence, so the recorder can independently observe the
engine. SciPy replaces a NumPy prominence implementation because WG already
requires it. The official JWSound target, additive scope and fixed numerical
budgets remain unchanged. Real CPU/Metal/installed/performance runs are deferred
compute-broker evidence, not claimed by fake tests. This fix round is left
uncommitted at the user's explicit request; previous rounds' stale commit-state
wording is removed.

Review round 2 (conformance), fixed directly:
- Bound agreement records must be passed real-solve records whose case backend
  and precision equal the compared settings; a synthetic, failed or
  cross-backend record is refused.
- Mesh binding now compares the two records' recorder-computed packed-mesh
  hashes with each other (the recorder never hashed raw mesh file bytes).
- Accepted: a monotone reference passes unless the corpus declares
  expected_resonance_columns; the corpus owner must declare them. A Python
  caller can still pass a canonical-named case with its own accept callable;
  the CLI cannot.


## PLAN slice 8 — first real CPU agreement runners (2026-10-06)

- **W2 / frozen-input PR:** `scripts/beat_conformance/{runners,run_agreement}.py`;
  `server/tests/beat_adapter/test_runners.py`. Identical metre mesh bytes, tags,
  winding/normals, axes, polar/sphere points, frequencies, medium, quadrature,
  precision and explicit threads; unsupported comparison domains refuse.
- **W3 / comparison-result PR:** the same runner/test files; common WG DI and
  sampled-sphere power, historical mean pressure loading, exact-pin HBB API.
- **W5 / managed-sweep/evidence PR:** `scripts/beat_conformance/{runners,recorder,
  verification}.py`; `server/tests/beat_adapter/test_{runners,verification}.py`.
  Recorder-owned managed child sessions, public negotiation, terminal evidence,
  unconditional shutdown and optional clean-source/installed byte matching.
- **Shared W2/W3/W5:** this file; `scripts/beat_conformance/README.md` and
  the 2026-10-06 results report kept with the workspace evidence (commands, broker IDs and verdicts; not in this repository).

Official JWSound remains the target. Qualification uses child mode to bound
ownership to the broker job and avoid HBB adoption/signalling. HBB's public
one-shot API preserves its own project/runtime and does no provisioning.
Installed wheel source byte matching handles missing wheel VCS metadata.
HBB revision correspondence remains metadata attested; the reviewed runner
now binds both records and uses observed LU for stricter reference budgets.
No startup/performance claim is made. Fixed agreement tolerances are unchanged.
No existing caller, pin, requirement, engine code or HBB directory changes.

Brokered CPU evidence: coarse full/quarter scans retain failed sampling gates;
quarter 2 Hz refinement passes Float64 and Float32 (1,632 frequency solves total).
HBB Float32 wire serialization exactly explains Float64 pressure residuals and
a flat peak's one-sample location shift. Full dense and narrow chamber/duct runs
remain unqualified. Combined checks: 1262 passed, 1 optional schema skip;
focused checks: 91 passed; Ruff and diff whitespace checks passed. Uncommitted.


## Agreement runner fix round (`7dce8bba`)

- **P1/A — fixed:** per-engine diagnostic observations replace shared settings
  equality. Missing HBB precision/Julia threads/phasor, normals/connectivity,
  Cartesian points, medium and singular order remain explicitly declared.
  HBB's returned parsed mesh counts/tag areas are checked and hashed; the hash
  is of exposed MeshInfo facts, not a connectivity hash. Returned polar axes
  are observed. Declarations are never listed as verified equality.
- **P2/B — fixed:** reject any non-Float32-representable frequency axis before
  launching either engine; the adapter's general narrow-sweep API is unchanged.
- **P2/C — fixed:** overall case status/qualification follows agreement;
  immutable engine snapshots use engine_status/engine_qualified. CLI regression
  pins that run_case's optional comparator is never used by run_agreement.
- **P2/D — fixed:** both ResultSets carry engine record/hash bindings; compare
  checks mesh/case/runtime identity and uses HBB's actual dense_solve_method
  for LU budgets, ahead of any conflicting linear_solver label.
- **P2/E — fixed:** reject both source checkouts, installed package/dist roots
  and resolved aliases before writes, reusing runtime paths.checked_root.
- **P2/F — fixed:** capture SHA first, enumerate immutable ls-tree paths, read
  cat-file blobs at that SHA, then refuse a changed HEAD.
- **P2/G — fixed:** wire_quantization.py is a reproducible diagnostic with a
  synthetic regression; transformed samples never enter agreement gates.
  HBB Float32 wire output caps a Float64 comparison near 6e-8.
- **P3 threads/pins/count wording — fixed:** launch from frozen input threads,
  record launch/native threads, resolve pins from the repo root, and describe
  HBB counts as API-validated rows, not terminal events.

Files per PR / manifest row for this fix round:

- **W2 / input and observation evidence:**
  `scripts/beat_conformance/{runners,run_agreement,settings}.py`;
  `server/tests/beat_adapter/test_runners.py`.
- **W3 / agreement and diagnostic results:**
  `scripts/beat_conformance/{agreement,wire_quantization}.py`;
  `server/tests/beat_adapter/test_{agreement,wire_quantization}.py`.
- **W5 / recorder and revision binding:**
  `scripts/beat_conformance/{recorder,verification}.py`;
  `server/tests/beat_adapter/test_{runners,verification}.py`.
- **Shared W2/W3/W5 documentation:** this file and
  `scripts/beat_conformance/README.md`; workspace results report remains outside
  the repository. Existing adapter/runtime implementation files are unchanged.

Design deviations: reported API gaps remain declarations instead of requiring
WG-specific BEAT behaviour. MeshInfo area sums allow 1e-12 relative arithmetic
rounding between independent Python readers; counts, mesh bytes, tags, normals
and numerical agreement thresholds are unchanged. Diagnostic-only wire
simulation explains the precision ceiling without feeding gate inputs. Official
JWSound remains the target. Changes remain additive and uncommitted as requested.


Fix-round validation: 1279 passed / 1 optional compiled-v2 schema skip;
focused runner/verification/agreement/wire/conformance tests 151 passed; Ruff
and whitespace checks passed. Broker job `261006-022851-compute-e01e` reran the
unchanged 376-frequency quarter case under the tested code: Float64 and Float32
PASS with paired record bindings and stricter actual-LU budgets, exit 0 in
9.17 minutes. Source fingerprints and all 14 case JSON hashes were verified.
The external report retains exact commands, environment, verdicts and hashes.
All requested review fixes and the refined rerun are complete; source remains
uncommitted as explicitly requested.
