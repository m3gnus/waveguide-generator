# Review slices

## Exterior input policy (policy version 1)

`preflight.validate_exterior_physics` checks the physics WG can both send and
interpret before `official_beat.solve_compiled` imports the engine or acquires a
worker. The capability report runs this same guard against constructed requests
and explicit unsupported-wire probes. It reports representability, separately
from runtime readiness and numerical qualification.

This is **WG policy**, not a change to BEAT's validators or Boundary Lab's
request acceptance. The engine pin, source, defaults and result contracts are
unchanged. The guard does not mutate requests, rescale amplitudes, infer axes,
change quadrature, select a replacement model or invalidate readiness evidence.
BEAT's own schema, reference, mesh and capability checks still run afterwards.

The supported subset is one exterior air region, no coupled interfaces, rigid
and moving boundaries with empty material parameters, v1/v2 ideal sources,
normal/explicit signed axial motion, normal-velocity ports, WG's existing fixed
quadrature settings, and its separately represented symmetry or rigid ground.
Unknown physical options and source parameters are refused with their field
path. Descriptive graph metadata stays open.

In particular, exterior impedance/Robin boundaries and bulk losses are refused
instead of being sent into the current exterior Neumann path. Shaped/modal
sources, voltage-driven v3 components and FEM coupling are also refused by this
adapter until it explicitly adopts their input AND result semantics. This does
not mean those features are absent from BEAT: v3 drivers and bounded/coupled
physics already exist there. No unqualified solver fallback is selected.

The existing amplitude interpretation remains intact. The engine supplies unit
velocity bases; WG performs its existing unit-acceleration and client driver
calculations. Subsequent common-input work must explicitly declare the chosen
RMS convention and perform phase conversion once, without changing Boundary
Lab's legacy interpretation. Source/observation coordinates remain metres;
air speed, density and frequency retain m/s, kg/m^3 and Hz. Physical symmetry
copies and fictitious ground images keep their existing distinct loading rules.

### Compatibility evidence and subsequent engine changes

`fixtures/wg_request_compatibility.json` under `server/tests/beat_adapter` freezes
six complete canonical requests from WG `9ca875c6`, with CPU/Metal, normal/axial,
ground, Float64 and trace cases. Explicit cardinal observation points avoid
platform libm rounding in byte hashes; existing observation fixtures cover the
generator separately. These checks compare request bytes, not numerical solver
results. Production refusal tests prove that unsupported requests never reach
engine import, session creation or worker acquisition.

Before changing BEAT numerical code, freeze the affected legacy fixtures,
expected outputs, tolerances and default-path performance budgets. Include the
legacy source-request driver and compiled v1/v2, plus bounded/coupled consumers
and installed startup where shared code is touched. BEAT's
`scripts/compare_exterior_legacy.jl` compares quantities and bytes against
`4839c7e6` on `two_tetrahedra.msh`, with reversed ports, two frequencies, both
precisions/phasors/profiles. Its other numerical dependencies are shared with
the baseline: changing those requires independently frozen outputs or a complete
pre-change runtime. Opt-in new request fields alone do not isolate changes to
shared force integration, geometry, quadrature, phasors or worker code.

Existing BEAT matrix diagnostics use weighted `W*Z`, with signed effective-area
conversion and cancellation flags. Adopt and qualify those diagnostics instead
of rebuilding or symmetrizing the matrix. Complex source profiles additionally
need defined receiving-force dual, area and power semantics; complex RHS weights
alone are insufficient. These additions are separate, opt-in migration slices.

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

Agreement runners, review round 2, fixed directly:
- The official engine's observation angles and planes are an echo of WG's
  request layout, so they are no longer counted as observed.
- The engine snapshot is written as `<case>-engine.json`; `<case>.json` exists
  only once the comparison verdict is known.
- Wording: `evidence_binding: recorded` means the agreement is bound to both
  record hashes; the HBB record is assembled by the runner from its public API,
  and its mesh/case fields are not independent observations (HBB's observed mesh
  evidence is node/triangle counts and tag areas).

## PLAN slice 8 — Metal agreement runners

- **W2 / frozen-input PR:** `scripts/beat_conformance/{runners,run_agreement}.py`;
  `server/tests/beat_adapter/test_runners.py`. Optional CPU/Metal selection,
  unchanged CPU defaults, Metal Float32/fixed q4/s4 and resolved WG headroom.
- **W3 / native-result evidence PR:** `scripts/beat_conformance/settings.py`;
  `server/tests/beat_adapter/test_runners.py`. Every frequency must report the
  requested native backend; missing/conflicting/CPU fallback labels refuse.
- **W5 / managed-sweep qualification PR:** `scripts/beat_conformance/{runners,recorder}.py`;
  `server/tests/beat_adapter/test_{runners,conformance}.py`. Metal uses the wheel
  project through child manager/session; native device must match the existing
  independent dispatched-kernel probe. Both engines retain their linear-solver
  diagnostics. No numerical tolerance changes.
- **Shared W2/W3/W5:** this file and `scripts/beat_conformance/README.md`.

Official JWSound is the target. No caller, pin, requirement or engine changes.
The brokered evidence and commands remain in the external workspace report.
Source stays uncommitted as requested; the orchestrator will rerun from the
committed tree. Fake tests do not establish numerical or performance agreement.

Metal evidence exposed different BLAS policies: HBB sets BLAS to its Julia
thread count, while official Metal inherits the process BLAS count and reserves
one thread for multithreaded sweeps. W5's `runners.py` sets worker-only
`OPENBLAS_NUM_THREADS` to the frozen count plus that reservation (one for a
single-thread run), so actual sweep BLAS threads match HBB.
CPU and ambient settings remain unchanged. The managed-session fake covers both
backends, matching BLAS, preserved offline settings and cleanup on errors.
This application-side adaptation preserves the frozen threading gate; it does
not relax numerical limits or require engine changes.

Validation: 1303 passed / 1 existing optional schema skip (99.17 s), focused
127 passed (2.18 s), Ruff and whitespace checks passed. Broker job
`261006-035822-compute-9022` passes the refined 376-frequency Metal Float32
quarter case with native six Julia/BLAS threads, device proof, all resonance
and unchanged numerical gates, including actual-LU budgets. Earlier startup
and threading failures remain failed in the external report. The committed-tree
rerun belongs to the orchestrator; no startup/performance claim is made here.

## Metal support review round 1 fixes

- **P1/A — fixed, because probes could write before manager isolation:**
  validate projects and every depot with `julia_steps.julia_environment` before
  any Julia launch; pass its environment and explicit project to device probes.
  Fakes cover HBB projects/depots, relative paths, symlinks and environment forwarding.
- **P2/B — fixed, because operator assembly skips the BLAS reservation:**
  refuse all ambient `BLAB_*` overrides, including `pair_owned`, and explicit
  operator-matrix requests before probing/solving. Default direct Metal assembly
  retains the worker-only T+1 override (T=1 stays 1).
- **P2/C — fixed, because two missing observations compared equal:**
  require positive observed `blas_threads` and backend on every row from both
  engines. Agreement also refuses missing/declared aggregate evidence. Tests
  cover first/last rows, either engine, both backends and omissions on both sides.
- **P2/D — fixed, because failed retries could retain earlier passed records:**
  refuse nonempty output directories before startup; require a fresh retry
  directory. Regression covers a device-probe failure before solving and refusal
  to retry over existing final records.
- **P2/E — fixed, because HBB's Metal backend label is a request echo:**
  require a `metal_` regular assembly mode or `assembly=metal_fused_burton_miller`
  on every row. HBB emits `metal_pipeline=false` on CPU too, so presence alone
  is deliberately insufficient. Late CPU execution with Metal labels fails.
- **P2/F — fixed, because CPU diagnostics must keep their real wire spellings:**
  retain strict CPU validation; fixtures use official `bem_backend` plus
  `engine_provenance.execution.backend`, and HBB `backend`, CPU fused assembly
  and `metal_pipeline=false`. Both engines emit `blas_threads` on CPU.
- **P3 — fixed, because launch policy and input errors need explicit evidence:**
  record the worker-only `OPENBLAS_NUM_THREADS` override as declared launch
  settings, including probe failures. Invalid/empty `--threads` produces an
  argparse error before I/O. No parent environment mutation.

Files per PR / manifest row for this round:

- **W2 / frozen inputs and retry policy:**
  `scripts/beat_conformance/run_agreement.py`;
  `server/tests/beat_adapter/test_runners.py`.
- **W3 / observed results and agreement:**
  `scripts/beat_conformance/{settings,agreement}.py`;
  `server/tests/beat_adapter/test_{runners,agreement}.py`.
- **W5 / probe and worker launch evidence:**
  `scripts/beat_conformance/{verification,runners,recorder}.py`;
  `server/tests/beat_adapter/test_{verification,runners}.py`.
- **Shared W2/W3/W5:** this file; `scripts/beat_conformance/README.md`.

CPU qualification is affected by project/depot validation, required BLAS/backend
observations and fresh output directories; CPU worker launch policy is unchanged.
The separate CPU broker job `261006-040655-compute-93df` uses `74e17133`, so it does
not validate these changes. Official JWSound remains the target. Refusing overrides
and reused output directories selects the review's permitted conservative fixes;
requiring assembly evidence avoids treating HBB's CPU pipeline field as Metal proof.
No production caller, engine, pin, dependency or numerical budget changes.
Fake tests only; real reruns remain orchestrator work. Changes are uncommitted
as requested.

Metal support review round 2, fixed directly: HBB Metal evidence now requires
device assembly (`regular_assembly_mode` = `metal_fused_burton_miller` or
`metal_native_*`). Host-staged CPU assembly and the `metal_default` placeholder
no longer count, and the dead `assembly` alternative is gone.

## PLAN slice 9 — selected production provider

Files per review PR / manifest row:

- **W2 / production requests and routing:** `server/solver/{beat,beat_imported,
  official_beat}.py`; `server/engines/registry.py`; provider/readiness cases in
  `server/tests/test_official_beat_bridge.py` and
  `server/tests/beat_runtime/test_{readiness_facade,review_round1}.py`.
- **W3 / production results and loading:** shared response packaging in
  `server/solver/{beat,beat_imported,official_beat}.py`; driver/trace assertions
  in `server/tests/test_official_beat_bridge.py`;
  `server/tests/beat_adapter/fixtures/{hbb_production.json,README.md}`.
- **W5 / managed plain/adaptive sweeps:** `server/solver/beat_adapter/results.py`;
  session/sweep/cancellation helpers in `server/solver/official_beat.py` and
  their production callers; sweep tests in `server/tests/test_official_beat_bridge.py`.
- **Shared W2/W3/W5:** this file. These are review slices of shared files.

Exact `WG2_BEAT_PROVIDER=official` now routes CPU/Metal production solves through
request/result adapters and the default host manager used by warm-up. Registry
production rows follow matching official compiled readiness, including live
invalidation without HBB. The default path retains HBB; frozen pre-change
responses check identical JSON and binary artifact hashes with a fixed clock.
The prototype's duplicated request/decoder/standalone port is retired into the
shared production path; its explicit solve entry remains. No pins/dependencies,
engine checkout, HBB directories, CUDA/ROCm code or runtime namespace changes.

Official JWSound supersedes the design's fork destination. Existing ground,
infinite-baffle and imported frame/axis refusals remain; a TODO explicitly defers
official rigid-ground enablement. Cancelled multi-channel responses keep acquired
rows; bases/traces use the acquired channels' common axis so exported arrays
remain coherent. Generalized forces remain in acquired solver logs, separate
from historical mean-pressure driver loading; they are not interpolated as a
new product quantity. Tests use an in-process host transport fake and existing
runtime ownership machinery, never Julia. Installed/platform, numerical and
startup/performance qualification remain outside this code-only gate.

Validation (requested interpreter, targeted launcher, unpiped): common runtime/
adapter/solver check **1494 passed, 1 optional skip in 110.15 s**; requested
production/bridge/adaptive/registry/CPU/imported check plus geometry-contract and
changed readiness tests **304 passed in 34.70 s**. Runtime/adapter Ruff, Ruff on
all other changed Python files and `git diff --check` passed. All checks stayed
below two minutes. No Julia, downloads, frontend/full-suite jobs, donor edits,
user/HBB-directory writes or commits. Real CAD-ingestion companion tests were
identified but not run under the light-check constraint; imported solve tests
use recorded geometry and fake workers. All requested code work is complete;
real installed CPU/Metal and performance qualification remains a separate gate.

## PLAN slice 9 — Review round 1 (f0af3ea6)

- **P1-1 — fixed, because strict import-linter rejects stale ignores:** removed
  the deleted `official_beat -> jobs.models` edge. Added exact declarations for
  the existing request/capability adapters' job-model types; no wildcard rule.
  `lint-imports --no-cache` checks all six contracts.
- **P1-2 — fixed, because ephemeral monitor threads retained store connections:**
  `JobStore.cancellation_state` shares one checkpoint connection under the
  existing store lock, with `close()` owning its lifetime. This is smaller than
  a global session scheduler or solver-to-jobs cleanup coupling and retains
  cached write connections. Forty real-temp-store sessions assert stable
  `_connections` and open FD counts (macOS/Linux).
- **P2-a — fixed, because boot/legacy warm-up probed HBB when selected:** official
  selection reads `production_statuses`, the readiness source for registry/AUTO
  rows; OFF retains HBB probes. Tests cover CPU, Metal, unavailable and legacy
  family selection without starting any engine; an unavailable official legacy
  family skips warm-up while OFF retains its historical fallback.
- **P2-b — fixed, because transport startup status was dropped:** thread the
  solve's status callback through session admission/start/submit, preserving
  setup-stage initialization, Julia compile lines and ready messages.
- **P2-c — fixed, because callback failures could look like successful partial
  cancellation:** propagate the original callback exception after direct checks
  and monitor-driven interrupted reads, including terminal races. Production
  final checkpoints also run on cancelled results. Tests cover the job's real
  cancellation exception, SQLite OperationalError and KeyboardInterrupt, with
  zero/one rows, parametric/imported/plain/adaptive paths. Engine cancellation
  without a caller exception can still return an explicitly cancelled prefix.
- **P2-d — fixed, because official metadata was credited to HBB's pin:** Python
  and TypeScript recognize `engine="beat-engine"`; imported solver-engine blocks
  remain authoritative. Regressions require the matching provider pin for both
  result shapes and retain HBB pin resolution. Frontend test fixtures now use
  module-relative URLs so the requested npm command works from the repo root.
- **P3 symmetry wording — fixed, because official refusal named HBB:** use BEAT
  wording on that branch; OFF messages remain unchanged.
- **P3 request refusals — fixed, because request ValueErrors escaped:** official
  parametric/imported build errors and parametric baffle refusal become
  BeatUnavailable with the original message. Refusal tests pin type/message;
  a missing source-tag-2 frame refuses before submission.
- **P3 duplicate compilation — fixed, because response_config rebuilt topology:**
  build once per parametric solve/imported channel and reuse the frame, mesh,
  loading and observations; acquired batches replace only their frequency list.
  Plain/adaptive tests count builds and compare submitted compiled systems.
- **P3 ambient selector — fixed, because developer environment changed tests:**
  clear WG2_BEAT_PROVIDER before collection and in a root autouse fixture;
  tests needing official selection explicitly set it.
- **P3 lost guards — fixed (restored coverage), because the production bridge
  must preserve its boundary:** discovery/assets map to OfficialBeatUnavailable;
  millimetre input scales frame origin/area while radius stays metres; blocked
  zero-row cancellation rethrows the original callback exception. The first two
  implementation guards were already present; regressions now protect them.
- **P3 explicit mesh scale/precision — fixed, because retained traces used the
  unscaled mesh:** scale the response artifact's node coordinates to metres
  after building the request, retaining IDs/tags/order; precision metadata
  follows the actual negotiated float32/float64 request.
- **P3 cancelled channel lists — fixed, because missing channels were advertised:**
  filter channel_order and basis metadata to acquired channel results; a
  first-channel cancellation test includes an unacquired combined channel.
- **P3 registry version — fixed, because selected rows discarded engine version:**
  read lazy distribution metadata for beat-engine and retain it at detection and
  live refresh, including provisioning rows, without importing either engine.
  Regressions check detection, refresh and provisioning.
- **P3 architecture doc — fixed, because it described the deleted prototype:**
  document selected production routing, shared adapters, managed sessions and
  remaining installed/numerical qualification gates.
- **Notes — recorded, no change requested:** readiness remains recomputed per
  solve. Monitors still poll at 20 Hz; after P1-2 these checks reuse a bounded
  store connection rather than opening one per session. The pre-existing 0.25 s
  cancel retirement grace remains. No objections to the requested findings.

Files per PR / manifest row (shared files span the review slices):

- **W2 / request, frame and provider selection:**
  `server/solver/{beat,beat_imported,official_beat,warmup}.py`;
  `server/solver/beat_adapter/mesh.py`; `server/engines/registry.py`;
  `server/tests/beat_adapter/test_request.py`;
  `server/tests/{test_official_beat_bridge,test_beat_warmup_selection}.py`.
- **W3 / result provenance and artifact metadata:**
  `server/solver/{beat,beat_imported,power_qualification}.py`;
  `frontend/src/results/powerQualification{,.test}.ts`;
  `server/tests/{test_power_qualification,test_official_beat_bridge}.py`.
- **W5 / session, callback and sweep lifetime:**
  `server/solver/{official_beat,beat,beat_imported}.py`;
  `server/solver/beat_runtime/{session,manager}.py`; `server/jobs/store.py`;
  `server/tests/beat_runtime/test_session.py`;
  `server/tests/test_official_beat_bridge.py`.
- **Shared W2/W3/W5:** `.importlinter`; `conftest.py`; this file;
  `docs/architecture/OFFICIAL-BEAT-BRIDGE.md`.

Design deviations: official JWSound remains the target. Callback-raised real
job cancellation must reach the caller so the job transitions to cancelled;
retaining partial rows is allowed only when that exception remains observable.
The smallest resource fix is a store-owned checkpoint connection, not a new
monitor scheduler. Batch reuse changes only WG-owned request construction.
No engine-specific behaviour, pins, requirements, HBB files, CUDA/ROCm or
numerical solves changed. Changes remain uncommitted as explicitly requested.

Validation (requested interpreter, unpiped, every command below two minutes):
required runtime/adapter/production/imported/adaptive/registry/CPU selection plus
new warm-up and touched power-qualification files: **1827 passed, 1 skipped in
113.30 s**. After the final provisioning-version and unavailable-legacy-warm-up
edge checks, assets + bridge + warm-up + registry + startup-performance tests:
**253 passed in 12.75 s** (overlaps the larger run). Node **20.20.2**, requested
`npm --prefix frontend exec vitest run src/results/powerQualification.test.ts`:
**16 passed**. `ruff check server conftest.py`, `lint-imports --no-cache` (**6
contracts kept**) and `git diff --check` pass. The unchanged selector-OFF golden
fixture test passes. No requested review finding remains unfinished; real Julia,
installed-platform and numerical qualification were deliberately not run.

### Slice 9 review round 2 (final)

Two independent reviewers (Sonnet, Opus) approved 35b0ff58 with no P0-P2 findings. They checked the shared cancellation connection (every store use holds `self._lock`, so it is serialized; polling load is unchanged), exception propagation and selector-OFF parity. Last-round P3s were fixed directly:

- Object to moving `request.py`'s `DriveChannel` import under `TYPE_CHECKING`: import-linter counts type-checking imports, so the edge stays either way. Both edges are pre-existing (broken on 0a1b901d); their `.importlinter` comments now follow the "Legacy use: DriveChannel." convention.
- `test_backstop_interrupts_blocked_read_and_raises_the_callers_exception` is renamed after the behaviour it asserts.
- A duplicate `frequency_range` assignment is removed from `test_cancelled_prefix_is_packaged`.
- The registry refresh reads `official_selected()` once.

Notes kept open:

- `beat-engine` is not in `pins.json`, so official results are power-qualification "unknown" (missing `solver_pin`) until the 0.3.6 pin round.
  Resolved by the 0.3.6 pin: `beat-engine` is pinned to m3gnus/BEAT_Engine d0d624a0, so official results resolve their `solver_pin`.
- The official cancel monitor polls the store at 20 Hz, so a transient store error fails the job, as on HBB.

### Opt-in exterior transducer consumer

`transducers.build_transducer_request` and
`official_beat.solve_transducer_compiled` adopt the pinned engine's existing
compiled v3 voltage ports. This Python adapter entry point supports CPU Float64,
complete closed outward-wound solids, and symmetry off. It is explicitly called;
stored jobs, default provider routing and the existing unit-acceleration results
continue to use the v1/v2 ideal-source adapter. There is no engine or pin change.
Reduced solids and other backends remain outside this consumer's qualified scope.

Supply bare moving mass `mmd_kg` and the five other LEM scalars in SI units.
Measured `Mms` is not accepted as an alias or inferred. One driver may own multiple
disjoint moving tags with a common global rigid-translation axis. Outward mesh
normals supply the signed front/rear projection; the adapter adds no rear sign,
extra radiation mass, end correction or second electrical/mechanical solve.
Open/two-sided diaphragms and compression-driver throats are refused by the
engine's existing exterior topology checks. Optional engine chambers,
semi-inductance and driver symmetry accounting await separate consumer adoption.

Each requested voltage port is an independent `reference_voltage_rms_v` basis
(default 2.83 V); ideal ports remain independent 1 m/s RMS bases. Request order is
preserved and undriven transducers are shorted. Pressure, current and velocity
remain native RMS phasors in WG's negative-time convention. The client derives
`V/I` only for the driven driver's voltage port, displacement as `u/(-i omega)`,
and one-way peak excursion as `sqrt(2)*abs(displacement_rms)*1000` mm. Zero current
returns undefined (`None`) input impedance, never a fictitious finite value.
No acceleration normalization or legacy driver postprocessing is applied.

Matrix values retain mechanical `N*s/m` units and their component axis, separately
from excitation axes. WG retains and validates the engine's weighted reciprocity
and passivity diagnostics rather than symmetrizing or altering the matrix. Signed
volume-area conversion uses `D_S^-1 W Z D_S^-1` and refuses zero or near-cancelling
areas, including a translating closed sphere. Cancellation permits only an
explicitly acknowledged ordered prefix; malformed, truncated or failed streams
raise and close their managed session.
