# Solver result contract

Status: canonical current contract, version 1. Verified against
`server/solver/result_mapping.py` and frontend result/export consumers on 2026-08-20.
The original v1-mining document remains in Git history at `f51a23c`.

## Envelope and axes

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

Metadata declares `result_contract_version: 1`, `phase_quantity`, `phase_units`,
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

## Export alignment

The frequency CSV and summary join SPL, DI, and impedance by exact frequency key onto
the sorted union of their axes. A missing series sample produces an empty cell; no
interpolation is invented. Polar CSV retains its measured per-plane angles. FRD files
emit only complete finite frequency/level/phase triples.

Contract tests include `server/tests/test_engines_result_mapping.py`, the streamed-result
tests, frontend smoothing goldens, `frontend/src/results/exporters.test.ts`, and
`frontend/src/results/frd.test.ts`.
