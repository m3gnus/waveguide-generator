# Official BEAT conformance

Status: current developer tools for the pinned `beat-engine` route.

The HBB comparisons are historical. Their acquisition/performance commands
(`run_agreement`, `run_corpus`, `run_perf`, `perf_report` and wire-quantization
comparison) have been removed. The HBB agreement, corpus and performance
comparison evidence was recorded outside this repository. Those historical
records describe the revisions and commands used at the time; they are not
current instructions.

The retained tools exercise official compiled requests, backend capability
refusals, coordinate/phase/area controls, sweep protocol and independent analytic
references. `corpus.py` retains small frozen production requests; `runners.py`
provides the official child/managed runners.
Fake protocol tests do not claim numerical qualification.

Run the declared cases with the official runner through the compute broker:

```sh
python -m scripts.beat_conformance --evidence-mode real \
  --solve scripts.beat_conformance.runners:solve --backend cpu \
  --output-dir "$EVIDENCE_DIR"
```

The runner requires broker execution and independently verifies the selected
installed engine against a clean source tree matching `pins.json`, supplied as
`WG_BEAT_ENGINE_SRC`. Select Julia with `WG2_BEAT_JULIA`. Output directories must
be outside the repository and empty. Metal cases require matching hardware;
CPU/Metal route checks do not qualify CUDA/ROCm hardware.

Independent CPU Float64 references cover the pulsating sphere, its offset/radii,
complex self-load and propagation, plus a translating electrodynamic sphere with
a conforming FEM air shell. Both time conventions are checked; quantities are RMS.
Run them through the compute broker:

```sh
python -m scripts.beat_conformance --evidence-mode real --analytic-reference all \
  --julia "$JULIA" --output-dir "$EVIDENCE_DIR"
```

The native source driver is required for these analytic references; this does
not qualify an installed application candidate. Malformed, incomplete, reordered
or nonfinite results fail before scoring. Existing numerical budgets remain
unchanged. See `reference_qualification.py` for required references and record fields.
