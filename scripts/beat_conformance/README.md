# WG official-BEAT conformance

This is additive tooling for `JWSound/BEAT_Engine` (`beat-engine`). It does not
select a production provider, provision Julia, or call the compute broker.
Tests use synthetic wire results and attestations, never an engine process.

The declared HBB cases are `capability_refusals`,
`exterior_contract_two_frequencies` (two completed frequency solves), and
`analytic_pulsating_sphere_phase` (one Float64 solve, 1280 outward triangles).
The sphere scores pressure per unit acceleration under `exp(-i omega t)` against
its closed form: 0.05 dB / 0.6 degrees, with conjugation and sign controls at least
30 degrees away. The 0.6 degree band is HBB's faceted Python conformance band;
the engine's separate 0.5 degree analytic gate is not weakened by this tool.

Later, submit a **scoped compute-broker job** whose command, from the WG root, is:

```sh
python -m scripts.beat_conformance --output-dir <job-evidence-directory> \
  --evidence-mode real --solve <broker_runner_module>:solve
```

The job-owned injectable `solve(CompiledRequest) -> SolveEvidence` must use the
public official worker/session and WG result mapper. `RuntimeEvidence` must be
observed from the running executable, loaded package, completed results and
device, not copied from requested options. Supply the actual Julia path/version,
engine path/exact revision/distribution, backend, precision, device name/class,
and `real_solves=True` only after numerical results completed. Metal additionally
requires a dispatched and checked device kernel. Missing Julia/engine or required
results fails; it cannot skip. Source evidence has `artifact_kind="source"`;
installed non-editable qualification must separately record `"installed"` from
the job runner. The recorder never infers installed evidence from a source run.

`--case` selects declared cases. Every case writes strict JSON, including request
and mesh hashes, complete decoded result samples, runtime facts, acceptance
metrics, failures and comparator limitations. WG policy fingerprints, UTC capture
time, interpreter and relevant environment/load context accompany the record. `run_case` accepts an optional
comparator that writes metrics into its entry before raising on disagreement.
`run_cases` writes `summary.json`; no solve cases, zero actual frequency results,
or synthetic mode cannot pass qualification. Default mode is `synthetic` and the
CLI exits nonzero even when all synthetic contract checks succeed. No blanket
pytest skip policy is changed.

For PLAN §5 same-mesh evidence, construct `agreement.ResultSet` for the **current
WG HBB pin** and official exact revision, using identical original mesh bytes and
frozen tags/normals, axes, observation points, frequencies, quadrature, precision,
convention, medium and threading. Pass WG mean pressure per acceleration as
`impedance_per_acceleration`, DI in dB, and comparable acoustic power in watts.
`compare_results` normalizes impedance's velocity basis by `rho*c`. Declare the
dense `frequency_step_hz` and physical `resonance_prominence_db` before either run.
Identical unordered frequency axes are sorted together for scoring.
Interior pressure and normalized-impedance peaks/dips must match in count and be
within one step, with local spacing below 0.5% of resonance frequency. Endpoints
must bracket features; sampled extrema cannot prove unresolved resonances.
Resonance failure prevents norm scoring.

Named constants retain PLAN budgets: forced-LU L2 `1e-5` / SPL `0.001 dB`;
production L2 `1e-4` / SPL `0.01 dB` / phase `0.1 degrees`; normalized impedance
`0.01 dB` / `0.1 degrees`; DI/power `0.02 dB`. The pressure SPL/phase mask is
reference-defined at 30 dB **per frequency** across observation/channel samples;
null samples are reported separately and remain in complex L2. Zero reference,
undefined impedance phase, missing quantities or mismatched frozen inputs fail.
Persist the returned verdict with `recorder.write_record` alongside raw evidence.

The full migration corpus (OSSE, R-OSSE, resonant duct/chamber, imported sources,
loading, cuts/sphere/traces) and installed platform runs remain broker work.
This three-case tool does not claim corpus, startup/performance, or device parity
qualification. No real runs or broker submissions are part of this change.
