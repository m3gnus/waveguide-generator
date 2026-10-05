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
and dispatches/checks an integer Metal kernel for Metal cases. It then owns a
public `EngineWorker`, consumes its result/terminal stream through WG's mapper,
and terminates that worker. No private engine APIs, provisioning or downloads
are used. All launch/probe work belongs inside the scoped broker job.

`SolveEvidence` remains available for synthetic/precomputed contract tests;
its runtime fields are explicitly **attested** and cannot qualify in real mode,
even with `real_solves=True`. Qualification requires **observed** backend,
precision, device and terminal solve count. Terminal counts must equal decoded
rows and the complete declared frequency list. Device functionality without the
checked Metal kernel cannot pass. Installed/source classification is inspected
from distribution metadata or a clean tracked source tree, never a runner flag.
Installed VCS metadata is independently read; its `engine_revision` evidence
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
frozen tags/normals, axes, observation points, frequencies, quadrature, backend,
precision,
convention, medium and threading. Pass WG mean pressure per acceleration as
`impedance_per_acceleration`, DI in dB, and comparable acoustic power in watts.
`compare_results` normalizes impedance's velocity basis by `rho*c`. Declare the
dense `frequency_step_hz` and physical `resonance_prominence_db` before either run.
Identical unordered frequency axes are sorted together for scoring.
Revisions must be nonempty and different. Optional `recorder_record` /
`recorder_sha256` bindings must be supplied on both results and agree with the
record's revision and mesh hash (the recorder hashes canonical packed mesh bytes).
The binding hash uses `recorder.record_sha256`, excluding its own hash field.
Forced-LU limits derive from the reference record's `native_diagnostics.linear_solver`
(`cpu_dense_lu` or `metal_assembly_cpu_dense_lu`), never a caller boolean. Without
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
qualification. No real runs or broker submissions are part of this change.
