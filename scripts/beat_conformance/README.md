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

`corpus.py` declares nine small WG requests. `run_corpus.py` calls the actual
`solve_beat_from_msh_text` / `solve_imported_beat_from_msh_text` production routes,
with HBB first and official second in fresh sequential Python processes. HBB
uses one-shot execution; official uses an owned `WorkerManager(mode="child")`.
All Julia and BLAS thread settings are 1. CPU supports float32/float64; Metal
requires float32. This corpus scores agreement, and always reports
`qualified=false`; installed/device/terminal qualification remains separate.

Prepare both runtimes independently. `--hbb-depot DIR` is required and accepts
an explicit depot chain, with a fresh writable entry first. HBB does not inherit
`JULIA_DEPOT_PATH`, `JULIA_LOAD_PATH`, `JULIA_PROJECT`, the official
`WG2_BEAT_RUNTIME_DIR`, `WG2_BEAT_WORKER_DIR`, or `WG2_BEAT_JULIA`.
Its depot comes exclusively from this flag, and its Julia executable from
`--julia`. Official receives its own provisioned environment. Neither child
receives ambient `BLAB_*` overrides. The two effective depot chains must have
**no shared entries**, including read-only caches; use distinct chains. Resolved
paths (including symlink targets and the official default depot) are checked.
Both children record sorted allow-listed dumps of every `JULIA_*`, `WG2_BEAT_*`,
`HORNLAB_BEAT_*`, `BLAB_*`, `OPENBLAS_*`, `OMP_*`, and `MKL_*` variable, plus
`VECLIB_MAXIMUM_THREADS` and `BLIS_NUM_THREADS`. In particular,
`HORNLAB_BEAT_RUNTIME_DIR`, `HORNLAB_BEAT_WORKER_DIR`, and
`HORNLAB_BEAT_FORCE_CPU` are retained for HBB, recorded, and compared across
parts. Each child also records a SHA-256 of its full environment, excluding only
`_`, `PWD`, `OLDPWD`, `SHLVL`, `TMPDIR`, `TMP`, and `TEMP` (shell/process
bookkeeping and temporary locations). Non-allow-listed values are hashed, not
dumped. Merging compares the allow-listed dump, rather than the full hash.

Both engine distributions must be non-editable VCS installs with an exact
40-character commit in `direct_url.json`; editable and directory-only installs
refuse. HBB must additionally match the current WG pin.
`--output-dir`, `--coarse-dir`, every `--refine-dir`, and every `--hbb-depot`
entry must resolve outside the WG repository root, before any output is created.
This keeps acquisition evidence and depot writes from dirtying the required
clean tree.

Official readiness is checked before meshing or either engine launch. The
runtime root and complete Julia depot chain must match provisioning, status,
and the corpus. `WG2_BEAT_RUNTIME_DIR` is a **base** (`wg-beat-engine` is appended),
whereas CLI `--dir` is exact. For example, provision separately with:

```sh
export WG2_BEAT_RUNTIME_DIR=/private/tmp/wg-beat-corpus/runtime
export WG2_BEAT_WORKER_DIR=/private/tmp/wg-beat-corpus/workers
export WG2_BEAT_JULIA="$JULIA"
export JULIA_DEPOT_PATH=/private/tmp/wg-beat-corpus/official-depot
export JULIA_PKG_OFFLINE=true
python -m server.solver.beat_runtime.cli provision --backend cpu --julia "$JULIA"
python -m server.solver.beat_runtime.cli status --backend cpu
```

Omit `--dir` to use production readiness's directory. If supplying it, use
`$WG2_BEAT_RUNTIME_DIR/wg-beat-engine`. Do not provision only the first depot
entry and then query or solve with a different complete chain. Default CLI
threads (`auto`) match the readiness query; corpus solve children use one.
A provisioned external Julia is rediscovered without repeating `--julia` in
status. Its `version=null` is intentional: discovery does not launch a probe;
readiness checks the executable hash and compiled proof.

From a clean WG checkout containing the non-editable current HBB pin and an
exact-revision official distribution, run:

```sh
python -m scripts.beat_conformance.run_corpus \
  --case osse-quarter --backend cpu --precision float64 \
  --julia "$JULIA" --hbb-depot /private/tmp/wg-beat-corpus/hbb-depot \
  --output-dir /private/tmp/wg-beat-corpus/evidence/osse-quarter-dense --phase coarse

python -m scripts.beat_conformance.run_corpus \
  --case osse-quarter --backend cpu --precision float64 \
  --julia "$JULIA" --hbb-depot /private/tmp/wg-beat-corpus/hbb-depot \
  --output-dir /private/tmp/wg-beat-corpus/evidence/osse-quarter-part-1 --phase refine \
  --coarse-dir /private/tmp/wg-beat-corpus/evidence/osse-quarter-dense --refine-part 1
```

The legacy names `coarse_hz`, `--phase coarse`, `coarse/`, `coarse-score.json`,
and `coarse_complete` now describe the **predeclared dense uniform sweep**.
`--phase both` meshes once, acquires the dense sweep, and acquires refine part 1.
Dense-only evidence cannot pass. Output directories must be empty or absent.
Refinement reuses the exact frozen mesh bytes and ingestion record. Catalogue
parameters, dense axis, backend, precision, settings, mesh, WG commit, clean-tree
observation, engine identities, and effective environments must match the
original acquisition. Legacy 250 Hz evidence is refused, rather than relabelled
dense. Every raw acquisition, coarse score, part record, and verdict carries WG
commit and `git status --porcelain` evidence. Dirty WG trees refuse before Julia
probes, meshing, or solves; identity changes across parts also refuse.

Features are pressure, normalized acoustic impedance, and (for a driver)
electrical impedance peaks/dips at 1 dB topographic prominence. They are detected
in **both engines on the dense sweep**. `refine-plan.json` uses their union.
Each feature at `f[k]` contributes exactly `[f[k-1], f[k+1]]`. Only windows with
intersecting interiors merge; touching endpoints alone do not merge windows.
Each window's dyadic step is at most 0.25% of its own lowest feature frequency.
The narrow case additionally caps the dyadic step at 0.25 Hz so even a
grid-aligned Q=300 mode has multiple samples above -3 dB.
The grid and exact dense neighbours remain Float32-wire representable and never
extend outside the window. No reference features means
`status="no_features_observed", passed=false`, unless the case predeclares
`expect_no_features=True` with a reason. **No catalogue case declares this.**

Refinement uses measured paired wall cost `c = hbb_wall/N + official_wall/N`.
For each fresh part it additionally reserves the full measured dense pair wall
`S` as conservative startup/serialization allowance (the amortized rate already
includes startup). Its limit is
`min(MAX_EXPLICIT_FREQUENCIES, floor((60*budget-S)/c))`;
the request schema currently allows 401 rows. The default budget is **8 minutes
per paired part**, not a total refinement budget. A budget unable to fit three
rows and startup writes `status="refine_budget_too_small"`, the window plan,
and required minimum part minutes to `verdict.json`. Parts overlap by two rows. The recorded plan includes
unique/acquired counts, per-part counts, total estimated minutes, and both timing
records. Native logs are indexed by `frequency_hz`; overlapping rows must agree
on backend, precision and numerical settings, and every duplicate numeric row
must agree within relative complex difference 1e-12 for float64 or 1e-6 for
float32. Kernel timings are not settings. Array samples are joined verbatim
without interpolation; a conflicting duplicate refuses rather than replacing
an earlier sample. Refined coverage uses each engine's actual acquired axis,
which must exactly match the frequencies planned for its recorded part.

Each child timeout for the dense acquisition is the case's declared
`coarse_minutes[1] * 60` seconds. Each refinement child timeout is
`--max-part-minutes * 60` (default **480 seconds**). Only recorded process groups
are terminated by timeout handling. Runtime can vary from the estimates.

For later parts use fresh directories and `--refine-part K`. On the last part,
supply all earlier part directories through repeated `--refine-dir`. Missing
parts report `refine_incomplete`. Complete evidence scores the entire merged
band using the **declared dense step**, and each complete local window with its
own finer step. Prominence and feature resolution in a local window use the
merged full-band shoulders, so clipping a broad feature cannot erase it.
Every dense feature must resolve uniquely in its originating engine's bracket;
candidate-only features are retained and fail the agreement gate. Resonance
failure blocks error norms, including the electrical impedance gate. Pressure,
sphere/traces, DI/power, and impedance retain the existing PLAN budgets and
reference-defined 30 dB mask; nulls remain separately reported.

The narrow case uses the existing 300 mm straight throat extension with a 3 mm
radius, a 10 mm OSSE termination at 2 degrees, and a driven closed end. The
small open aperture reduces radiation loss, unlike the old broad flare. The
quarter-wave estimate includes the unflanged open-end correction `0.61*r`:
`r ~= 0.003 + 0.010*tan(2 deg) = 0.00335 m`,
`L_eff ~= 0.300 + 0.010 + 0.61*r = 0.31204 m`, and
`f ~= 343/(4*L_eff) = 274.8 Hz`; `ka ~= 0.017` there.
The declared `narrow_band_hz = [270, 285]` brackets termination uncertainty.
Only on-axis pressure (flattened column 0, horizontal plane at 0 degrees) and
normalized impedance (column 0) peaks inside this band count.
Scoring requires a refined reference peak with measured absolute -3 dB
crossings and `Q=f_peak/(f_right-f_left) >= 10`. A missing, unbracketed,
dense-only or broad peak fails `resonance_not_narrow`. The peak must be acquired
in refinement, with at least two refined samples above its -3 dB level;
single-sample spikes fail. Crossings use the merged dense+refined axis with
linear interpolation in dB between adjacent samples. Dense-resolved crossings
overestimate width, so Q errs low, conservatively.
No theoretical Q is substituted for that measurement. Actual reference Q
remains unverified in this review because numerical solves were prohibited.

`non-45-cut` now uses the non-axisymmetric CAD box, with 30 degree diagonal,
0 degree horizontal and 90 degree vertical cuts. The reference diagonal must
differ by **more than 0.5 dB from each control** somewhere within their common
30 dB mask, otherwise scoring fails `cut_not_discriminating`.
This sentinel proves asymmetry, not the exact 30 degree angle. The main
comparison covers the angle through identical declared observation layouts.
The two-disc fixture has unequal radii (10 and 12 mm) and unequal x/y offsets
(-28,-12) and (19,17) mm. A channel swap is no longer a mirror-image ambiguity:
source area/impedance and the vertical plane can discriminate it as well.
The driver fixture retains its 25 mm radius disc; `Sd=pi*2.5^2=19.635 cm²`.
Its 20–300 Hz band brackets the unloaded driver resonance (~73 Hz) and its air
load. Electrical impedance is read from `channels[id].impedance` only when
`metadata.impedance_quantity == "electrical_input_impedance"`; production has
already moved it out of `metadata.driver`. It participates in feature discovery,
resonance matching, and the 0.01 dB / 0.1 degree norm budget. `driver-loading`
declares `expected_resonance_columns["electrical_impedance_ohm"] = (0,)`:
a featureless electrical reference fails even if pressure has structure.

All cases request horizontal/vertical/diagonal cuts and a 9×16 full sphere at
2 m. Only `sphere-traces` requests retained traces. Original WG fixtures and
production ingestion/meshing are reused; the developer tool needs test and
mesher dependencies. The 650-vertex CPU refusal budget remains. Measured mesh
counts here: full/quarter OSSE 429/133, R-OSSE half 643, narrow duct 582,
asymmetric two-source/cut box 270, curved tilted/rear 642, driver 295.
All nine deterministic mesh builders remain below five seconds per invocation;
the changed cases measured 0.61–1.35 seconds. Meshing test outputs live under
system temporary directories and are deleted afterwards. These are agreement
controls, not spatial mesh-convergence studies. The normal-motion curved case
does not cover arbitrary oblique axial motion; ground and infinite baffles
remain outside this corpus.

Evidence includes exact original mesh bytes/hash, WG request/ingestion/settings,
complete production responses, unrounded native arrays, distribution VCS
metadata, HBB pin, WG commit/cleanliness, Julia path/version, environments,
load/battery context and both engines' timings. Installed VCS identities remain
attested rather than source-byte verified. Imported HBB MeshInfo describes its
production tag merge (rigid 1, active 2); original CAD tag areas stay declared.
DI/power use the sampled full-sphere far-field estimator; native powers stay raw.
Required missing pressure/loading/sphere/traces cannot pass. Both-route refusals
record `unsupported`; a one-engine error records `failed` even in `phase=both`.

Strict JSON preserves complex/bytes/array types, array dtype/shape, and distinct
+infinity, -infinity and NaN via tagged floats. User mappings containing reserved
tag keys are escaped so they cannot decode as arrays, complex numbers or bytes.
Callbacks are omitted with reasons (sequence positions retain explicit markers);
unknown objects fail with their evidence path and become child error records.
Capture occurs **after `apply_channel_driver`**, before display packaging. The
optional production hooks leave ordinary-call defaults unchanged; tests exercise
SolveConfig propagation, exactly one callback per channel, default precision,
and real driver coupling using stubbed engine packages. No test launches Julia.
`verdict.json` is written at the end; killed/incomplete work cannot leave a pass.

## Corpus review round 1

Disposition of the two independent reviews and orchestrator decisions:

- **A — fixed**, because every case now declares a dense uniform sweep; both
  engines contribute feature windows, only overlapping interiors merge, local
  dyadic refinement stays <=0.25%, full-band scoring retains the dense step,
  and empty reference structure fails explicitly. Local prominence uses full
  shoulders to avoid manufacturing feature disappearance by clipping.
- **B — fixed**, because acquisition parts read the 401-row bound from the WG
  request schema and also obey their measured paired time budget.
- **C — fixed**, because the production electrical-response field is captured
  and tested through the real imported route/driver channel on both providers;
  the band brackets driver resonance, Sd matches the CAD disc, and electrical
  features gate norms.
- **D — fixed in geometry/scoring**, because the small-aperture driven tube
  builds within the mesh/time budget and a measured refined Q>=10 is mandatory.
  Reference Q is still an acquisition check, not a claimed result: this review
  ran no numerical solves. A non-narrow future reference cannot pass.
- **E — fixed**, because the cut fixture is non-axisymmetric and masked
  reference differences from both control cuts must exceed 0.5 dB.
- **F — fixed**, because HBB receives only its explicit depot chain, official
  runtime variables are stripped from it, BLAB overrides are stripped from both,
  and effective environments are recorded and regression-tested.
- **G — fixed**, because log rows are frequency-keyed in merging and slicing,
  and overlapping backend/precision/settings disagreement refuses.
- **H — fixed**, because every acquisition/part/verdict records WG revision
  and porcelain status, dirty trees refuse before launch, and mixed commits,
  engine identities or environments cannot merge. Old coarse declarations refuse.
- **I — fixed**, because regressions exercise both production hooks/routes,
  unchanged default precision, single-engine failure in phase=both, schema cap,
  zero-feature failure, measured Q sentinel and masked cut sentinel.
- **J — fixed**, because reserved JSON keys round-trip as data, nonfinite signs
  survive distinctly, documented timeouts match case/part code, capture is
  correctly described after driver scaling, and unequal discs/offsets make
  source swaps visible beyond the horizontal plane.

### Declared density and cost arithmetic

Historical timing input: system-temp evidence
`/private/tmp/wg-beat-corpus-261006/corpus/*-float32-coarse/coarse/{hbb,official}.json`.
Use paired wall/count `c` and paired cold wall `S` from those records.
Conservative dense estimate `D=(N*c+S)/60` deliberately adds cold allowance to
an already amortized rate. For the changed tube (194 -> 582 vertices), scale
native assembly/field components by `(582/194)^2`, solve components by
`(582/194)^3`, and retain measured amortized non-kernel overhead. This gives
`c=1.1744 s/f` (old unscaled `0.4980`), `S=12.449 s`. The two-source box proxy
(268 -> 270 vertices) similarly gives `c=1.6497`, `S=14.813`; this also sizes
the cut case, replacing the axisymmetric quarter's cheaper timings.
These extrapolations are planning estimates, not new solve measurements.

The refine estimate below is an illustrative **one isolated feature window at
the lowest interior dense frequency**, a conservative step/count example.
Its count `M` includes both dense neighbours; `R=(M*c+60*D)/60` reserves a
whole dense pair, as the code does with actual measured timings. Actual total
refinement depends on the union of observed features and is recorded in
`refine-plan.json`. The **D+R table is a lower bound** based on one illustrative
window, not a complete case budget. Each
paired acquisition part remains capped at eight estimated minutes.

| Case | Dense band / step Hz | N | c s/f | S s | Dense D min | Example M | Refine R min | D+R min |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| osse-full | 500–3500 / 25 | 121 | 1.1474 | 14.915 | 2.56 | 51 | 3.54 | 6.10 |
| osse-quarter | 500–3500 / 10 | 301 | 0.8604 | 11.185 | 4.50 | 21 | 4.80 | 9.31 |
| rosse-half | 500–2500 / 10 | 201 | 1.7515 | 15.764 | 6.13 | 21 | 6.74 | 12.87 |
| narrow-resonance | 200–400 / 1 | 201 | 1.1744 | 12.449 | 4.14 | 9 | 4.32 | 8.46 |
| imported-two-sources | 500–2500 / 10 | 201 | 1.6497 | 14.813 | 5.77 | 21 | 6.35 | 12.12 |
| imported-tilted-rear | 500–2500 / 10 | 201 | 1.5596 | 14.036 | 5.46 | 21 | 6.00 | 11.46 |
| driver-loading | 20–300 / 2 | 141 | 1.0398 | 13.518 | 2.67 | 129 | 4.90 | 7.57 |
| non-45-cut | 500–2500 / 10 | 201 | 1.6497 | 14.813 | 5.77 | 21 | 6.35 | 12.12 |
| sphere-traces | 500–3500 / 10 | 301 | 0.7998 | 10.397 | 4.19 | 21 | 4.47 | 8.65 |

No finding is objected to. Changes are intentionally uncommitted for review.

Validation: the requested filtered suite, invoked through `scripts/run_tests.py`,
passed **133 tests** (3058 deselected). Additional comparator, conformance,
full runtime and official-bridge coverage passed **1202 tests**. `ruff check
scripts server` and `git diff --check` passed. No Julia, numerical solve, or
broker was run. The changed geometries were meshed in temporary directories
and removed after inspection.


## Corpus review round 2

Both reviews found no false-pass mechanism. All ten requests are fixed; none
is objected to. This round ran no Julia, numerical solves, or broker commands,
and leaves changes uncommitted as requested.

1. **Q sentinel — fixed**, because the declared 270–285 Hz quarter-wave mode
   and columns now bound eligible peaks; the peak and at least two samples
   above -3 dB must be refined, while merged-axis dB interpolation permits
   conservative dense-resolved crossings. Real `refine_windows` regression
   acquisitions cover aligned and off-grid Lorentzian Q=10, 30, 100, 300,
   with the narrow case capped at 0.25 Hz to resolve Q=300. Regressions also
   reject single-sample spikes, out-of-band peaks, and undeclared quantities/columns.
2. **Repository paths — fixed**, because output, coarse, refine, and all HBB
   depot entries resolve outside the repository before acquisition. Tests
   include symlink aliases; examples now use external evidence directories.
3. **Driver structure — fixed**, because electrical column 0 must have a
   reference resonance; pressure structure cannot substitute for it.
4. **Isolation/identity — fixed**, because any shared depot entry refuses,
   including official defaults and read-only aliases. Every requested runtime
   environment prefix is dumped in sorted order and compared across parts;
   the full environment hash and its exact volatile exclusions are documented
   above. HBB runtime/worker/force-CPU controls remain effective and recorded.
5. **Official distribution — fixed**, because non-editable exact VCS metadata
   is required; editable, directory-only, missing, and abbreviated commits refuse.
6. **Comparator inputs — fixed**, because one-sided or expected-but-missing
   electrical data raises, electrical/context arrays must be finite, and
   window samples must lie on the common increasing context axis.
7. **401 cap — fixed**, because `server.jobs.models.MAX_EXPLICIT_FREQUENCIES`
   supplies the explicit-list validator and corpus planner; the already-equal
   `num_frequencies` bounds share it with unchanged production semantics.
8. **Small budget — fixed**, because successful dense acquisition followed by
   an unusable part budget writes `refine_budget_too_small` in `verdict.json`,
   with the complete window plan and required minimum part minutes.
9. **Merge integrity — fixed**, because refined coverage comes from actual
   HBB/official part axes checked exactly against the numbered plan. Duplicate
   engine rows must agree elementwise within relative complex difference
   1e-12 (float64) or 1e-6 (float32), using the larger magnitude as denominator;
   zero pairs must match exactly. Conflicting values/presence refuse. The
   duplicated `no_features_observed` block was removed.
10. **Budget/cut documentation — fixed**, because the D+R table is explicitly
    a lower bound, measured plans are listed below, and the cut sentinel's
    asymmetry claim is distinguished from the layout's exact angle declaration.

Existing dense-phase evidence under
`/private/tmp/wg-beat-corpus-261006/corpus/*-coarse/refine-plan.json` reports:

| Case | Planned refine frequencies | Parts |
| --- | --- | --- |
| osse-full | 105 | 1 |
| osse-quarter | 76 | 1 |
| rosse-half | 346 | 1 |
| narrow-resonance | 5 | 1 |
| imported-two-sources | 112 | 1 |
| imported-tilted-rear | 104 | 1 |
| driver-loading | 33 | 1 |

These are measured dense-phase plan sizes (5–346 frequencies, all one part),
not new solve results or proof of completed refinement. The supplied evidence
has no `non-45-cut` or `sphere-traces` dense plan. Catalogue/identity changes in
this review require fresh evidence for subsequent acquisition.

Validation: the requested filtered suite, invoked through `scripts/run_tests.py`,
passed **283 tests** (3119 deselected). Since `server/tests/test_jobs_models.py`
is absent, the existing explicit-frequency validator coverage in
`server/tests/test_jobs_luna.py` passed **11 tests** (41 deselected). The full
comparator/runner tests passed **187 tests**. `ruff check scripts server` and
`git diff --check` passed. These test counts describe separate, overlapping runs.
