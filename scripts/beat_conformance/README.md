# WG official-BEAT conformance

This is additive tooling for `JWSound/BEAT_Engine` (`beat-engine`). It does not
select a production provider, provision Julia, or call the compute broker.
Tests fake subprocesses and public worker streams, never an engine process.

The declared HBB cases are `capability_refusals`,
`exterior_contract_two_frequencies` (two completed frequency solves), and
`analytic_pulsating_sphere_phase` (one Float64 solve, 1280 outward triangles).
`metal_exterior_float32` adds two Metal solves and is required for `--backend metal`.
The sphere scores pressure per unit acceleration under `exp(-i omega t)` against
its closed form: 0.05 dB / 0.6 degrees, with conjugation and sign controls at least
30 degrees away. The 0.6 degree band is HBB's faceted Python conformance band;
the engine's separate 0.5 degree analytic gate is not weakened by this tool.

Later, submit a **scoped compute-broker job** whose command, from the WG root, is:

```sh
python -m scripts.beat_conformance --output-dir <job-evidence-directory> \
  --evidence-mode real --solve <broker_runner_module>:solve
```

The job-owned `solve(CompiledRequest)` callback returns
`recorder.EngineRun(julia_executable, julia_threads="auto")`. This is a launch
selection: the recorder runs that executable to observe version/binary hash and
CPU identity, inspects the loaded `beat-engine` distribution and exact revision,
and dispatches/checks an integer Metal kernel for Metal cases. It then owns the solve and consumes its result/terminal stream through WG's
mapper. `runners.official_runner` selects `runtime_mode="child"`, using
`beat_runtime.manager.WorkerManager` and `SolveSession` for admission,
negotiation, staging and cleanup. Child mode bounds qualification ownership to
the broker job; it does not qualify detached-worker adoption. The default
`EngineRun` direct mode is retained for the original conformance callback API. No private engine APIs, provisioning or downloads
are used. All launch/probe work belongs inside the scoped broker job.

`SolveEvidence` remains available for synthetic/precomputed contract tests;
its runtime fields are explicitly **attested** and cannot qualify in real mode,
even with `real_solves=True`. Qualification requires **observed** backend,
precision, device and terminal solve count. Terminal counts must equal decoded
rows and the complete declared frequency list. Device functionality without the
checked Metal kernel cannot pass. Installed/source classification is inspected
from distribution metadata or a clean tracked source tree, never a runner flag.
A wheel with no VCS metadata can supply `EngineRun.engine_source`, a local
clean tracked engine checkout. The recorder compares the complete installed
package inventory and bytes to immutable Git blobs at a captured commit, then
confirms HEAD is unchanged before recording that revision.
This does not assume any WG-specific behaviour in BEAT. Otherwise, installed
VCS metadata is independently read; its `engine_revision` evidence
field is **attested** because byte correspondence to upstream is unverified; an independent engine content fingerprint is retained.
Worker temperature is unknown/attested, and no performance claim is made.

`--case` selects results to run, never the required qualification set. Missing
CPU cases (and the Metal case for a declared Metal run) are named and keep
`qualified=False`. Every case writes strict JSON with a canonical record hash,
including request and mesh hashes, complete decoded result samples, runtime facts, acceptance
metrics, failures and comparator limitations. WG policy fingerprints, UTC capture
time, interpreter version/hash, relevant environment names/value hashes and
load context accompany the record. `run_case` accepts an optional
comparator that writes metrics into its entry before raising on disagreement.
`run_cases` writes `summary.json`; no solve cases, zero actual frequency results,
or synthetic mode cannot pass qualification. Default mode is `synthetic` and the
CLI exits nonzero even when all synthetic contract checks succeed. No blanket
pytest skip policy is changed.

For PLAN §5 same-mesh evidence, construct `agreement.ResultSet` for the **current
WG HBB pin** and official exact revision, using identical original mesh bytes and
declared tags/normals, axes, observation points, frequencies, quadrature, backend,
precision, convention, medium and threading. The real agreement runner separately
extracts per-engine observed settings and labels unavailable settings declared.
Declared equality does not verify engine usage. Pass WG mean pressure per acceleration as
`impedance_per_acceleration`, DI in dB, and comparable acoustic power in watts.
`compare_results` normalizes impedance's velocity basis by `rho*c`. Declare the
dense `frequency_step_hz` and physical `resonance_prominence_db` before either run.
Identical unordered frequency axes are sorted together for scoring.
Revisions must be nonempty and different. Optional `recorder_record` /
`recorder_sha256` bindings must be supplied on both results and agree with the
record's revision and mesh hash (the recorder hashes canonical packed mesh bytes).
The binding hash uses `recorder.record_sha256`, excluding its own hash field.
Forced-LU limits derive from the reference record's actual `dense_solve_method`;
`linear_solver` is used only when the actual-method field is absent. Without
bindings, the report labels evidence attested and uses production limits.
Cross-backend comparisons refuse under the frozen settings gate.

Interior pressure and normalized-impedance peaks/dips use SciPy's topographic
prominence, including features crossed by smaller ripples. They must match in
count and be
within one step, with local spacing below 0.5% of resonance frequency. Endpoints
must bracket features; sampled extrema cannot prove unresolved resonances.
Resonance failure prevents norm scoring. Columns with no reference extrema are
reported. Corpus cases with expected structure must declare, for example,
`expected_resonance_columns={"pressure_complex": (1,)}`; those columns fail if
no reference extrema are detected. A monotone column without that declaration
is reported but may pass.

Named constants retain PLAN budgets: forced-LU L2 `1e-5` / SPL `0.001 dB`;
production L2 `1e-4` / SPL `0.01 dB` / phase `0.1 degrees`; normalized impedance
`0.01 dB` / `0.1 degrees`; DI/power `0.02 dB`. The pressure SPL/phase mask is
reference-defined at 30 dB **per frequency** across observation/channel samples;
null samples are reported separately and remain in complex L2. Zero reference,
undefined impedance phase, missing quantities or mismatched frozen inputs fail.
Persist the returned verdict with `recorder.write_record` alongside raw evidence.

The full migration corpus (OSSE, R-OSSE, resonant duct/chamber, imported sources,
loading, cuts/sphere/traces) and installed platform runs remain broker work.
This small declared-case tool does not claim corpus, startup/performance, or device parity
qualification. The first brokered CPU results (2026-10-06) are kept with the workspace evidence, outside this repository, because they record machine-local paths.


## Frozen real-solve runners

`runners.FrozenExterior` constructs the compiled request with WG's adapter and
one explicit thread count. `official_runner(julia, threads=..., engine_source=...)`
returns the recorder's launch selection; the recorder independently observes
runtime facts and terminal counts. `hbb_runner(inputs, julia_executable=...,
expected_revision=..., directory=...)` uses the installed current-pin HBB
`solve_frequencies` API and returns `agreement.ResultSet`. The HBB runner uses
one-shot mode, avoiding persistent-worker registry adoption and stale-PID cleanup.
It retains HBB's own solver/project; it never provisions either engine.

The first runner scope is CPU, one normal source, the canonical origin/+z/+x/+y
frame, full/yz/yz+xz domains, polar cuts and a full theta-major sphere. Other
source/frame layouts need a separate runner with exact frozen HBB equivalence.
`run_agreement.metre_mesh` creates a single shared metre artifact before either
solve, preserving triangle/node IDs, ordering, winding and tags. Both engines are supplied the same declared frequency order, precision, medium,
q4/s4 and CPU wavelength policy (p90, q1 threshold 0, q2 threshold 2).
The frequency axis must be exactly representable in Float32, including for
Float64 solves, because HBB casts frequencies to Float32 before solving. Inputs and raw samples persist in
strict JSON via `recorder.write_record`. DI uses WG's spherical integration;
power is the common solid-angle-weighted far-field estimate at the declared
radius, not a surface flux or a production power claim. HBB's installed revision
is checked against repo-root `pins.json` but remains a VCS-metadata attestation.
Both ResultSets bind their engine-only record/hash, case, mesh and runtime
revision; actual HBB LU diagnostics select the stricter reference limits.
Official real records still require independent probes and terminals. HBB solve
count is API-validated rows, not terminal events.

Submit `python -m scripts.beat_conformance.run_agreement --help`'s command through
the compute broker with `--lane compute --priority 3 --requester "Beat engine
switch"`. Set a fresh writable WG depot first and existing precompiled depots
later. The CLI does not submit itself, download packages or change pins.
Predeclare the explicit frequency axis, maximum dense step, prominence and
expected resonance columns. Each precision retains a failed agreement record
when launch, identity, decoding, or a gate fails. The CLI exits nonzero on any
failed case. These case records do not qualify the entire conformance corpus.


The original conformance CLI can use `--solve scripts.beat_conformance.runners:solve`.
Set `WG2_BEAT_JULIA` to the selected executable, `JULIA_NUM_THREADS` to the
explicit count (default 1), and `WG_BEAT_ENGINE_SRC` to the clean source-witness
repository root for an installed wheel without VCS metadata. This callback only
selects the managed child launch; run the real-mode CLI inside a broker job.


## Agreement runner review fixes

The runner compares diagnostic-observed backend, BLAS threads, symmetry and
per-frequency quadrature selections. Official also reports precision, phasor
convention and Julia threads through native diagnostics/provenance. HBB exposes
mesh counts and physical-tag areas through its own `SolveResult.mesh_info`; their
hash covers those parsed facts, not unavailable node/connectivity arrays. HBB
precision, Julia threads, phasor convention, normals, frame/Cartesian points,
medium and singular order remain declared when its API does not report them.
Returned polar-angle/plane axes are checked and recorded. `settings_observed_equal`
names only fields observed by both engines; `settings_declared` lists all others.

The final case record's `status` and `qualified` reflect agreement. Separate
engine-only records use `engine_status` and `engine_qualified`, so a failed
comparison never leaves an overall pass marker. `run_agreement` never supplies
`run_case(comparator=...)`; the optional callback remains for existing synthetic
conformance tests. Bound snapshots retain their hashes when the final case
record receives the agreement verdict/hash.

Output roots refuse both engines' source/package/distribution trees, including
symlink aliases, as well as HBB runtime/worker roots. Optional packages remain
lazy; no engine-specific extension is required.

`python -m scripts.beat_conformance.wire_quantization --official <official-run.json>
--hbb <hbb-run.json> --output <diagnostic.json>` reproduces HBB's Float32 pressure
rounding and shortest decimal wire representation after undoing acceleration
scaling. This diagnostic never changes gate inputs or tolerances. Float64
comparison against HBB is capped near 6e-8 by HBB's Float32 wire output.

Same-mesh `run_agreement` accepts `--backend cpu|metal` (default CPU). CPU keeps
its existing precision choices, one-thread default and wavelength quadrature.
Metal requires `--precision float32`, uses fixed q4/s4 and defaults to WG's
performance-core thread budget with Metal headroom. `--threads auto` or a
positive explicit count resolves once for both engines. Metal records use a
`metal-` case prefix to distinguish them from existing CPU artifacts.
Official runs use the installed wheel's `julia_metal` project through WG's child
manager/session and the recorder's `assert_metal_device.jl` kernel check. Every
native row must report the selected backend; official native device provenance
must match the independently probed device. HBB uses its public one-shot Metal
API and a Metal-specific native assembly mode, since its backend label echoes
the request and `metal_pipeline=false` is also emitted on CPU. Float64 and silent CPU fallback refuse;
failed records remain failures. Linear-solver diagnostics determine the existing
LU/reference budgets. No Metal-specific tolerance relaxation is permitted.
For matched Metal threading, the official child worker receives its supported
`OPENBLAS_NUM_THREADS` count equal to the frozen Julia count plus one when
Julia is multithreaded. The default direct Metal assembly reserves that extra thread;
HBB's Metal API already sets BLAS to the frozen Julia count. The override is scoped to the worker;
it is recorded under declared `launch_settings`. Metal agreement refuses all
ambient `BLAB_*` overrides (including `BLAB_METAL_REGULAR_KERNEL_MODE=pair_owned`)
and explicit operator-matrix assembly before probing or solving, since those
policies can bypass the reservation. CPU launch settings and the parent
environment are preserved. Both CPU and Metal agreement require observed
positive BLAS counts and backend labels on every frequency row from both engines.
Absent observations fail even when both sides omit the same field.

Independent Julia probes validate their project and all depot destinations with
the runtime's isolation checks before launching, and receive that validated
environment. Agreement runs require an empty output directory; retries must use
a fresh directory so an earlier passed record cannot stand for a failed retry.
Invalid `--threads` values are command-line errors.

## Production-route corpus

`corpus.py` declares nine small WG requests. `run_corpus.py` calls
`solve_beat_from_msh_text` or `solve_imported_beat_from_msh_text`, explicitly
with `_official=False` followed by `_official=True`. Each engine/part has a new
Python process. HBB uses `persistent_worker=False` and its own runtime/project
paths; official uses an owned `WorkerManager(mode="child")`, inheriting
`WG2_BEAT_RUNTIME_DIR` and `WG2_BEAT_WORKER_DIR` unchanged. Every Julia and BLAS
thread setting is explicitly 1. CPU accepts float32/float64; Metal accepts
float32. Prepare the runtimes separately before running this tool.

Official readiness is checked before meshing or launching either engine. Keep
the runtime root and Julia environment identical between provisioning, status
and the corpus. `WG2_BEAT_RUNTIME_DIR` is a **base**: the runtime appends
`wg-beat-engine`. CLI `--dir` is the **exact** directory. For example:

```sh
export WG2_BEAT_RUNTIME_DIR=/private/tmp/wg-beat-corpus/runtime
export WG2_BEAT_WORKER_DIR=/private/tmp/wg-beat-corpus/workers
export WG2_BEAT_JULIA="$JULIA"
export JULIA_DEPOT_PATH=/private/tmp/wg-beat-corpus/depot
export JULIA_PKG_OFFLINE=true
python -m server.solver.beat_runtime.cli provision --backend cpu --julia "$JULIA"
python -m server.solver.beat_runtime.cli status --backend cpu
```

Omitting `--dir` uses the same provider directory as production readiness. If
using `--dir`, supply `$WG2_BEAT_RUNTIME_DIR/wg-beat-engine`. A custom depot chain
must be the same for all three commands: omit `--depot` to inherit
`JULIA_DEPOT_PATH`, or repeat that complete chain. Provisioning with `--depot`
set to only the first entry proves a different identity from a later status or
solve inheriting the full chain. Default CLI threads (`auto`) match the
production readiness query; the corpus's solve children separately use one
thread. Readiness still requires matching source, executable and environment
identities and compiled completion evidence.

A provisioned explicit external Julia is rediscovered without repeating
`--julia` in status. External `version=null` is intentional: discovery and the
installer do not run a version probe; readiness uses the executable hash and
compiled proof. An installer-only explicit choice remains one-off until a
backend has been provisioned with it.

Run one case from the WG root, in the environment containing the current
non-editable HBB pin and the official engine distribution (including the
m3gnus fork while it is the installed candidate):

```sh
python -m scripts.beat_conformance.run_corpus \
  --case osse-quarter --backend cpu --precision float64 \
  --julia "$JULIA" --output-dir evidence/osse-quarter-coarse --phase coarse

python -m scripts.beat_conformance.run_corpus \
  --case osse-quarter --backend cpu --precision float64 \
  --julia "$JULIA" --output-dir evidence/osse-quarter-part-1 --phase refine \
  --coarse-dir evidence/osse-quarter-coarse --refine-part 1
```

`--phase both` meshes, runs coarse, and acquires refine part 1 in one invocation.
A coarse-only invocation reports `coarse_complete`, with `passed=false`; its
numerical score is retained in `coarse-score.json`. Output directories must be
empty or absent, including retries. A refinement reuses the coarse directory's
frozen bytes and ingestion record without meshing again. Backend, precision,
settings, mesh hash, engine identities and thread settings must remain the same.

`refine-plan.json` discovers peaks and inverted dips **only from HBB**, using
`agreement.py`'s 1 dB topographic prominence on every pressure column and
normalized acoustic impedance. Each feature at coarse frequency `f[k]` gets
the local window `[f[k-1], f[k+1]]`, clamped to the coarse axis ends.
Overlapping windows and windows sharing a boundary are merged. Each merged
window uses a dyadic step at most 0.25% of its **lowest feature frequency**,
exactly representable on HBB's Float32 wire. Frequencies stay inside the windows;
exact coarse bounds are retained even when they fall between dyadic grid points.

Parts use the measured coarse wall seconds per frequency for **both engines**.
The estimate is `count * (hbb_wall/count + official_wall/count) + startup`.
For fresh startup and serialization, each part conservatively reserves one
additional full coarse pair's wall time (the amortized rate already includes
coarse startup). `--max-part-minutes` sets the hard estimated part budget,
default **8 minutes**. Use the same budget on coarse and subsequent refine
invocations. A budget too small for three frequencies and startup is refused.
The plan records `part_count`, unique `count`, `acquired_frequency_count`
(including overlaps), per-part and total `estimated_minutes`, the measured
costs, and the startup policy. Timing is measurement context, never an agreement
gate. Actual runtime may vary from this estimate.

Each `coarse/{hbb,official}.json` and `refine/{hbb,official}.json` records
parent-process `wall_seconds`, average `wall_seconds_per_frequency`, and native
per-frequency component timings keyed by channel and frequency. Coarse scores,
part records, and final scores retain both engines' timing context. Legacy
captures use `metadata.performance.total_time_seconds`, explicitly labelled
`legacy_production_route_wall`; these exclude the surrounding Python child
startup, so their estimates have less timing coverage.

Larger acquisitions split into numbered parts with two-row overlaps. Complete
windows are scored after joining the parts, so a feature on a part boundary
remains interior to its scoring window. Every coarse feature must have a unique
reference peak or dip of the same quantity, column and kind inside its original
coarse bracket. If it falls below the prominence threshold in the merged local
window, the score records `feature_resolution.features[].status="not_resolved"`
with the feature identity and reference prominence. This is a gate failure;
matching reference and candidate curves cannot pass by losing that feature.

For subsequent parts use another empty output directory and `--refine-part K`.
On the last part, supply every earlier part using repeated `--refine-dir`:

```sh
python -m scripts.beat_conformance.run_corpus \
  --case osse-quarter --backend cpu --precision float64 --julia "$JULIA" \
  --output-dir evidence/osse-quarter-last --phase refine \
  --coarse-dir evidence/osse-quarter-coarse --refine-part 3 \
  --refine-dir evidence/osse-quarter-part-1 \
  --refine-dir evidence/osse-quarter-part-2
```

The number 3 is illustrative; use the recorded plan's part count. Missing parts
report `refine_incomplete`. Complete evidence joins acquired rows without
interpolation, checks the full sweep, and additionally scores each complete
local window against its own declared dense step and expected feature columns.
This preserves the one-step location limit without increasing it to the coarse
spacing. Norms run only after resonances. All PLAN §5 budgets and the reference
30 dB mask remain unchanged; nulls stay separate in the report.

The following CPU estimates include **both** engines, one thread, and cold
starts. They are planning estimates, not measured solve timings. Refinement
estimates are **per numbered part**; the total depends on HBB's detected features.
There is a 650-vertex refusal budget, plus owned-child timeouts of 240 s per
engine for coarse and 320 s per engine for refinement. No runtime, device or
startup performance is qualified by these estimates.

| Case | PLAN coverage / reused WG control | Coarse axis (Hz; count) | Estimated coarse | Estimated refine part |
| --- | --- | --- | --- | --- |
| `osse-full` | Real-pipeline OSSE, full mesh, no image copies | 500–3500 / 250; 13 | 2–5 min | 4–9 min |
| `osse-quarter` | Same OSSE, x0/y0 quarter and real symmetry copies | 500–3500 / 250; 13 | 1–3 min | 2–6 min |
| `rosse-half` | Mesh-child R-OSSE R=150/r0=12.7/a=60/a0=15.5, yz half | 500–2500 / 250; 9 | 2–6 min | 4–9 min |
| `narrow-resonance` | Existing 300 mm straight throat extension before OSSE termination; low radiation loss gives longitudinal duct resonances | 200–1400 / 50; 25 | 2–6 min | 4–10 min |
| `imported-two-sources` | Real CAD box fixture with two discs, independently driven channels and physical source IDs | 500–2500 / 250; 9 | 2–6 min | 4–10 min |
| `imported-tilted-rear` | Real curved source-sheet fixture, rigidly rotated 45° about y: tilted net normal with a rear-facing component; normal motion | 500–2500 / 250; 9 | 2–6 min | 4–9 min |
| `driver-loading` | Real CAD box with one 25 mm disc, existing driver-LEM spec, adapter `BoundaryLoading` and production driver coupling | 200–1400 / 100; 13 | 2–5 min | 4–9 min |
| `non-45-cut` | Quarter OSSE; 30° diagonal inclination with horizontal/vertical controls | 500–3500 / 250; 13 | 1–3 min | 2–6 min |
| `sphere-traces` | Quarter OSSE; full sphere plus retained P1 pressure / DP0 Neumann traces | 500–3500 / 250; 13 | 1–3 min | 2–6 min |

R-OSSE uses the permitted half alternative: the original full mesh and then the
initial half exceeded the CPU budget. Coarsening the regional resolutions to
25/50/50 mm produces a 643-vertex half here. Other measured mesh sizes are
429/133 vertices for full/quarter OSSE, 194 for the duct, 268 for two sources,
642 for the curved tilted/rear source, and 295 for driver loading. These are
small agreement controls, not spatial mesh-convergence studies. The narrow
case declares expected pressure resonance column 0: a monotone or unresolved
coarse reference fails and must be investigated, rather than qualifying an
empty resonance test. It uses a real WG duct feature; no bespoke geometry
format or chamber surrogate is introduced.

Every case requests horizontal/vertical/diagonal cuts and a 9×16 full sphere at
2 m. Only `sphere-traces` requests retained field traces. The CAD builders and
bundle/ingestion helpers come from `server/tests/test_cadlink_domain_automatic.py`;
therefore this developer tool needs WG's test dependencies as well as its mesher.
The production mesher is called once per frozen artifact. The tests measured
all nine builders below 5 s, compare two independent in-process meshes, and
also retain tiny-fixture catalogue/runner tests for environments without the
optional mesher. A changed mesher that breaks the size or time budget should
be investigated and the declared geometry budget revised before qualification.

No catalogue case is statically unsupported today: all nine mesh and the CAD
cases pass WG's BEAT geometry preflight without either engine. A production
refusal on both routes is recorded as `unsupported` with each exact refusal
string, rather than dropping the case. A refusal on only one route or an engine
identity failure remains a failed comparison. Arbitrary oblique **axial** motion
is not covered by the normal-motion curved case; WG still refuses such axial
motion on both BEAT routes. Ground and infinite-baffle combinations are outside
these exterior cases and retain their existing refusals.

Evidence includes `surface.msh`, its SHA-256, WG requests and ingestion records,
compiled settings, each route's complete production response and unrounded
native fields, distribution versions/direct_url/revisions, the HBB pin, WG
commit/worktree, Julia path/version, thread/runtime environment, and raw
`sysctl -n vm.loadavg` / `pmset -g batt` command output or an explicit unavailable
reason. Complex and binary arrays round-trip through tagged JSON. Native source
mean pressure feeds `ResultSet.impedance_per_acceleration`; electrical driver
impedance is scored separately with the same 0.01 dB / 0.1° budget. Complex
sphere and retained traces use production pressure budgets. DI and comparable
power use the same full-sphere estimator as the existing agreement runner;
native power remains raw and is labelled unavailable when absent. Missing
required pressure, loading, sphere or requested traces cannot pass. No rounded
SPL/balloon output is converted into fabricated complex samples.

All corpus artifacts, including frozen records and CLI verdict output, use the
same strict serializer. Native dataclasses are traversed without deep-copying
execution callbacks. Callable fields (including HBB's
`native.<channel>.config.progress_callback`) are omitted with a reason in the
containing object's `__omitted_fields__`; callable sequence entries retain an
explicit `__unavailable__` marker to preserve positions. Values are never
silently stringified. Paths become path strings, NumPy scalars become native
scalars, arrays retain dtype/shape, and nonfinite numbers become unavailable
nulls (decoded as NaN in numeric arrays/complex components). Unknown object
types fail with their evidence path and become child error records.

Imported HBB's native MeshInfo describes its production tag merge (rigid 1,
active source 2). Counts and those raw facts remain recorded; original CAD tag
areas remain declared rather than falsely presented as observed equality.

The production-route capture seams are optional internal overrides in `beat.py` and
`beat_imported.py`: capture native fields before display packaging discards
precision, pass explicit HBB execution controls, and give imported calls the
same selector/precision/Julia/manager overrides already supported parametrically.
Defaults preserve existing production callers and HBB golden responses. These
seams avoid replacing production functions with lower-level runner solves.
The runtime discovery production fix is recorded separately in
`server/solver/beat_runtime/CHANGES.md`.

`verdict.json` is written only at the end. A killed or incomplete invocation
cannot leave a successful verdict. `passed` scores this sampled production
agreement; `qualified` remains false because these captures do not replace the
recorder's installed/device/terminal-event qualification evidence. Declared
settings and installed VCS metadata remain labelled as such. No tolerances are
widened, and this implementation/test run launches no Julia, engine solve or broker.
