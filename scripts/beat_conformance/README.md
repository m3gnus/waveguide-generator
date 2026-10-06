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
API and native backend diagnostics. Float64 and silent CPU fallback refuse;
failed records remain failures. Linear-solver diagnostics determine the existing
LU/reference budgets. No Metal-specific tolerance relaxation is permitted.
For matched Metal threading, the official child worker receives its supported
`OPENBLAS_NUM_THREADS` count equal to the frozen Julia count plus one when
Julia is multithreaded. The compiled Metal sweep reserves that extra thread;
HBB's Metal API already sets BLAS to the frozen Julia count. The override is scoped to the worker;
CPU launch settings and the parent environment are preserved. Native BLAS
observations must still agree before numerical gates can qualify.
