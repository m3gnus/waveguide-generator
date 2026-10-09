# Qualifying installed legacy BEAT input facades

The opt-in `solve_compiled` APIs in old Metal and BEM++ accept a prescribed-source
exterior-pressure subset of official BEAT inputs. Their installed qualification
uses WG's existing request compiler, complete outward solids, signed normal/axial
sources, independent ordered velocity ports, metre coordinates, and WG polar and
spherical observation points. Each result is compared with an independently
constructed direct native call to the same backend. This establishes input
translation equivalence; it does not establish numerical equivalence to BEAT.

Install the exact candidate wheel non-editably into a private target directory,
with its `beat-contract` extra and required native dependencies available. Put
that target first on `PYTHONPATH`. Run this command through the compute broker
on the shared Mac, once for each backend:

```sh
python -m scripts.beat_conformance.legacy_compiled_inputs \
  --engine metal \
  --wheel candidate.whl \
  --wheel-sha256 EXPECTED_WHEEL_SHA256 \
  --installed-root installed-target \
  --contract-root installed-beat/beat_engine/beat_contract \
  --contract-sha256 EXPECTED_PINNED_CONTRACT_FINGERPRINT \
  --report installed-metal-report.json
```

Use `--engine bempp` for the CPU backend. Metal requires an available packaged
native helper on Apple Silicon. The check fails instead of skipping an unavailable
backend. It checks the expected wheel digest, every installed package member,
imported modules against wheel members, and the actual selected Metal helper.
Before solves, it also checks the imported BEAT contract root and its fingerprint
against the intended contract pin. Compute this fingerprint from that exact
pin's `beat_contract` resources: SHA256 of compact sorted JSON mapping every
`.py`/`.json` relative resource name to its content SHA256. Generated caches do
not contribute. The report records these hashes, contract identity, platform, pressure
comparison budgets and errors, and per-case request hashes. A stale success
report is removed before execution; a failure writes no success report.

WG production requests also require radiation impedance and boundary pressure,
and may require further traces. The qualification explicitly creates a copy
containing only exterior pressure outputs. Old Metal additionally requires
omitting BEAT order overrides because it uses its own fixed native quadrature;
the report lists that omission. BEM++ retains orders 4/4 as native BEM++ controls.
The complete production request must be refused by these pressure-only facades.
No production request, routing, default or Boundary Lab behavior is changed.

This evidence qualifies installed legacy adapters on the platform actually run.
It does not qualify an installed WG application, a remote consumer pin, an
official-default switch, or HBB retirement. Dependency-first publication,
consumer pin adoption, consumer integration checks and other platform evidence
remain separate landing work.
