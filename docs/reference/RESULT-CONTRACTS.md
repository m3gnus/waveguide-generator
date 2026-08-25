# Solver result contract

Status: canonical current contract family, parametric version 1 and multi-channel
version 2. Verified against native/imported result mapping, job persistence, and
frontend result/export consumers on 2026-08-20.
The original v1-mining document remains in Git history at `f51a23c`.

## Envelope identity

Every final result declares `result_kind` and `result_contract_version` at the top
level. The version remains duplicated in `metadata.result_contract_version` for older
consumers.

| `result_kind` | Version | Envelope |
|---|---:|---|
| `parametric` | 1 | quantities and axes described below live at the top level |
| `multi_channel` | 2 | `channels[id]` contains the quantity envelope for each drive/derived channel; `channel_order` is the presentation order and top-level `metadata` is shared |

Final results also echo `client_request_id` and bounded `client_metadata`. The
top-level `provenance` contract version 1 declares WG version, dependency SHAs,
resolved engine, and SHA-256 identities for two stages: the effective request durably
stored after host-dependent submission decisions, and the execution request after
symmetry-domain resolution. `request_identity: "execution"` and the explicit
`execution_*_sha256` and `effective_*_sha256` names define those scopes; the original
unqualified names remain aliases for the execution hashes. The result HTTP response
supplies an ETag and exact stored-byte SHA-256.

### Declared versus installed dependencies

| Field | Meaning |
|---|---|
| `dependency_shas` | what `pins.json` **declares** this release should run, one commit per module. A declaration only — it is read from the checkout and is true of the repository, not of the machine |
| `installed_dependency_shas` | what **actually ran**: the commit pip recorded in each distribution's `direct_url.json` (PEP 610). `null` for a module that is missing, was not installed from Git, or whose record could not be read |
| `dependency_drift` | sorted module names where the installed commit differs from the pin or is unknown |

`dependency_drift` is the trustworthy signal, and the only one. `[]` means every
pinned module was measured and every measurement matched. A non-empty list means the
result describes a stack the host was not running, so any conclusion drawn from it is
about the installed commits, not the pinned ones. A *missing* `dependency_drift` field
means the producer predates the measurement and made no claim either way — do not read
its absence as agreement. These modules do not encode their commit in their version
string (several sit at `0.1.0` indefinitely), so neither the version nor a capability
probe can substitute for this comparison.

Measurement never fails a solve. If the environment cannot be read at all, every entry
degrades to `null` and every module is listed as drifted, which is a reported unknown
rather than a silent claim of agreement. `scripts/check_backends.py` prints the same
comparison for a host, and `GET /api/capabilities` returns it as `dependencies`
(`pinned`, `installed`, `drift`).

## Parametric envelope and axes

Every completed native solve maps to one JSON shape before charts or exports consume
it. `frequencies` is the requested/generated axis in hertz. Quantity objects may repeat
their own axis (`spl_on_axis.frequencies`, `impedance.frequencies`, `di.frequencies`) so
downstream code never has to assume positional equality between independently produced
series.

Missing or non-finite numerical samples are JSON `null`, not zero, NaN, or an omitted
row. A native axis-length mismatch pads unavailable samples with `null`, records a
structured `native_axis_mismatch` failure, and sets `metadata.partial_success` while
retaining valid values.

## Quantities

| Field | Contract |
|---|---|
| `spl_on_axis.spl` | dB SPL relative to 20 µPa, from the first requested plane at the finite angle nearest 0° |
| `spl_on_axis.phase_degrees` | raw wrapped complex-pressure phase in degrees; zero/invalid amplitude becomes `null` |
| `directivity[plane]` | per-frequency `[angle_deg, normalized_level_db]` pairs; each row is shifted so the configured normalization angle is 0 dB |
| `directivity_phase[plane]` | raw wrapped pressure phase with the same plane/frequency/angle shape as directivity; it is never level-normalized |
| `impedance.real/imaginary` | either dimensionless specific acoustic impedance `Z/(rho*c)` from a unit-acceleration solve, or terminal electrical input impedance in ohms for a driver-coupled channel; `metadata.impedance_quantity` and `impedance_units` are authoritative |
| `di.di` | full-sphere power directivity index integrated from a complete spherical pressure grid; `null` when the backend cannot supply one — display cuts are never substituted |
| `balloon` | normalized spherical SPL grid with theta/phi axes, distance, and hemisphere flag when requested and supported |
| `beam_shape` | fitted forward-beam diagnostics; nullable per frequency and accompanied by validity/residual metadata |
| `metadata.driver.cone_excursion_mm` | direct-radiator one-way peak displacement in millimetres, converted from the RMS drive phasor before comparison with the driver's one-way peak Xmax rating |
| `passive_cardioid.cone_excursion_mm` | passive-cardioid one-way peak displacement on the result frequency grid; `cone_excursion_quantity` states the same peak convention |

## Radiation-impedance artifact view

Passive-cardioid radiation impedance is a separately retained lossless NPZ, not a
quantity silently inserted into each drive channel. `JobItem.has_radiation_impedance_artifact`
is authoritative for availability. `GET /api/radiation-impedance/{job_id}` returns the
exact NPZ; `GET /api/radiation-impedance/{job_id}/presentation` reads the following
no-pickle fields into a bounded JSON view:

- aperture name, physical tag, and area;
- the full `engineering_impedance_matrix` mapping source volume velocity to receiver
  average pressure; and
- the stored in-phase port termination reductions used by the passive-cardioid model.

The presentation quantity is `average_aperture_pressure_per_volume_velocity`, its unit
is Pa·s/m³, and its phase-time convention is engineering `exp(+jωt)`. It deliberately
does not expose the NPZ's solver-convention matrix as another display curve. The chart
uses in-phase reductions first and falls back to engineering diagonal/self terms if a
future compatible artifact has no reduction. It never applies result smoothing because
smoothing a complex termination can change passivity. A missing or unreadable optional
artifact does not hide the run's otherwise valid SPL/directivity results.

Parametric metadata declares `result_contract_version: 1`, `phase_quantity`, `phase_units`,
`impedance_quantity`, `impedance_units`, `impedance_drive`, observation origin/distance,
sound speed, symmetry, frequency source, and backend diagnostics. Consumers must use
these declarations rather than infer a convention from the backend name.

## Observation and phase

The observation frame comes from the authoritative Gmsh artifact: source-tagged
triangles define forward, and the configured mouth/source origin defines the reference
point. Requested and effective distance plus sound speed are stored under
`metadata.observation`.

Stored phase is raw and wrapped. FRD export may remove common propagation delay only
when distance, sound speed, and a supported spatial-phase convention are all present;
otherwise it emits raw phase and says so in the file header. Charts receive the same
reference metadata. Smoothing applies to magnitude/DI/impedance series, never to raw
phase.

The interactive phase chart de-embeds the declared propagation term before unwrapping,
then removes the residual output-weighted linear slope for display. Group delay is
derived only when that same propagation reference is present and the retained sweep can
resolve the residual unwrap; otherwise the card refuses to invent a curve and explains
which metadata or sampling is missing.

## Directivity index and balloon

DI is `10log10(reference-axis mean-square pressure / full-sphere mean-square pressure)`
with linear mean-square-pressure integration. Only a complete spherical pressure grid
is accepted: WG requests the sphere independently of the selected H/V/D display cuts,
and the balloon flag controls only whether that grid is retained in the public result.
`metadata.directivity_index` states the method and rear-hemisphere policy. Infinite
baffle results treat the physically absent rear hemisphere as zero radiation.

`metadata.balloon_sampling.status` is exactly one of `disabled`, `available`,
`backend_unsupported`, or `missing_result`. Requested-but-unavailable data must not be
represented by an empty-looking successful balloon.

## Failures and live results

Warnings, structured per-frequency failures, counts, and `partial_success` live in
metadata. GMRES non-convergence and non-zero LAPACK info warn without deleting usable
arrays. Solver logs exposed to the client omit duplicated raw sphere-pressure payloads.

Live `partialResult` messages use the same quantity names and conventions as the final
result. A revision gap is repaired from the authoritative partial-results snapshot; the
completed stored result remains the durable artifact.

Multi-channel partial results carry `result_kind: multi_channel`, contract version 2,
`channels`, and `channel_order`. A channel may omit impedance when its source topology
does not define one; its channel metadata explains the omission. Consumers must not
substitute another channel's impedance.

## Export alignment

The frequency CSV and summary join SPL, DI, and impedance by exact frequency key onto
the sorted union of their axes. A missing series sample produces an empty cell; no
interpolation is invented. Polar CSV retains its measured per-plane angles. FRD files
emit only complete finite frequency/level/phase triples.

Contract tests include `server/tests/test_engines_result_mapping.py`, the streamed-result
tests, frontend smoothing goldens, `frontend/src/results/exporters.test.ts`, and
`frontend/src/results/frd.test.ts`.
