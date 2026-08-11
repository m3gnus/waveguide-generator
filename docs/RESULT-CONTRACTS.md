# Result contracts mined from v1

This is the v1 numerical and failure-state compatibility matrix. The primary mapper emits one common frequency vector plus SPL, phase, impedance, DI, directivity, metadata, optional balloon, and optional beam shape (`server/solver/result_mapping.py:295-391`). Presentation transforms are called out separately because they are not raw-result semantics (`server/solver/charts.py:187-486`).

## Imported multi-source result envelope

Parametric jobs retain the version-1 single-response object documented below. An
imported CAD-return job emits a version-2 envelope whose channel values are those
same version-1 response objects:

```jsonc
{
  "channels": {
    "drive-hf": { /* complete v1 SPL, phase, polar, DI and impedance response */ },
    "drive-lf": { /* complete v1 response */ }
  },
  "channel_order": ["drive-hf", "drive-lf"],
  "metadata": {
    "result_contract_version": 2,
    "geometry_type": "imported",
    "ingest_id": "wgi_...",
    "manifest_sha256": "sha256:...",
    "artifact_sha256": "sha256:..."
  }
}
```

`channels` is keyed only by the request's stable `drive_channel_id` and
`channel_order` preserves request order. A channel explicitly groups one or more
source IDs; their physical-tag velocities are summed into that channel's one
unit-drive basis. There is no crossover, delay, LEM, or channel summation in this
contract. Physical integers are artifact-local and never result addresses.
For a channel containing more than one source ID, `impedance` is omitted because
the native value belongs to one source patch rather than to the grouped channel;
the channel metadata carries `impedance_omitted: "multi-source channel: per-patch
impedance is not a channel impedance"`. Single-source channels retain the normal
v1 impedance object.

The envelope metadata carries the ingestion tag namespace and total tag map,
per-source effective mesh-frequency limits keyed by `source_id`, the conservative
global-frequency caveat when applicable, actual symmetry cut planes, pinned polar
derivation, ingestion hashes, and the exact report-hash acknowledgements. A solve
using `exterior_only=true` also records the excluded FEM-volume count and reason.
Each nested response keeps `result_contract_version: 1`; only the imported
multi-channel envelope is version 2.

Imported observation coordinates are always anchored to the ingestion record's
throat frame. Envelope metadata therefore records
`observation_origin_effective: "throat"` and the effective axis/u/v basis and
centres; a request's parametric `mouth` default never implies an imported mouth
origin. Each channel's `performance.total_time_seconds` measures the whole shared
multi-right-hand-side batch through the point that channel was packaged, not an
isolated per-channel solve.

## Global conventions and defaults

| Dimension | v1 contract | Evidence |
|---|---|---|
| Acoustic pressure reference | Absolute SPL uses `p_ref = 20e-6 Pa`. | `server/solver/result_mapping.py:20-24` |
| Characteristic impedance | v1 normalized impedance by a typed `rho*c = 1.21*343`. **v2 takes both from the native packages instead**: rho is the `AIR_DENSITY` the solve actually ran with (1.2041, since WG never passes a density), and c is the `SPEED_OF_SOUND` of the engine that produced the result. Reported values are therefore 0.49% larger than v1's. | `server/solver/result_mapping.py:20-24`; `server/solver/acoustics.py` |
| Time/spatial convention | Metal results declare spatial `exp(+ikr)`, consistent with temporal `e^{-iωt}`. The legacy alias resolver also knows a BEMPP-style `exp(-ikr)` convention. | `server/solver/metal_solver.py:371-395`; `server/contracts/__init__.py:39-72` |
| Observation defaults | Polar config defaults to 2 m, mouth origin, H/V/D planes, diagonal inclination 45°, spherical sampling off, 37 theta samples, and 72 phi samples. | `server/contracts/__init__.py:101-168` |
| Mapper fallback | If observation config is absent, the mapper falls back to mouth origin, 2 m, H/V arcs, and 37 angles from 0° through 180°. The backend may degrade a spherical request to arcs-only capability. | `server/solver/result_mapping.py:98-130` |
| Common frequency axis | Primary SPL, phase, impedance, DI, and polar patterns are built against the same rounded mapper frequency list. | `server/solver/result_mapping.py:295-355` |
| Numeric missing value | Mapper emits JSON `null` for invalid pressure-derived SPL/phase/directivity samples; it does not synthesize zero for those quantities. | `server/solver/result_mapping.py:133-182` |
| Partial success | Solver-log diagnostics can set `partial_success`, warnings, and counts without blanking numerical arrays. UI renders warnings and failed-frequency diagnostics separately. | `server/solver/result_mapping.py:202-249`; `src/ui/simulation/results.js:262-410` |

OPEN — the request model, mapper fallback, and directivity metadata helper do not expose one identical plane/default set in every call path. A v2 canonical request fixture must settle H/V versus H/V/D and normalization-angle defaults (`server/contracts/__init__.py:101-168`; `server/solver/contract.py:53-125`; `server/solver/result_mapping.py:98-130`).

## Numerical contract matrix

| Quantity | Units / normalization | Phase, origin, planes | Frequency alignment | Missing/NaN and partial display | Evidence |
|---|---|---|---|---|---|
| FR / on-axis SPL | dB SPL re 20 µPa: `20 log10(abs(p)/20e-6)` | Chooses the first observation plane and its sample closest to 0°; origin/distance are recorded in observation metadata | One value per mapper frequency | Zero, absent, or non-finite pressure becomes `null`; other frequencies remain available | `server/solver/result_mapping.py:149-164`; `server/solver/result_mapping.py:295-355` |
| Phase | Degrees from `angle(p)`, wrapped by NumPy to `[-180,180]` | Same first-plane, nearest-0° sample as SPL; spatial/time convention comes from result metadata | One raw value per mapper frequency; smoothing does not alter it | Invalid pressure becomes `null`; chart compensation only processes finite points | `server/solver/result_mapping.py:167-182`; `src/ui/simulation/chartRequests.js:81-119`; `server/solver/charts.py:94-152` |
| Normalized impedance | Dimensionless complex specific impedance `Z/(rho*c)`; output has real and imaginary arrays | Under `e^{-iωt}`, solver acceleration is converted via `v=a/(-iω)` and the engineering-sign conjugation is applied | One complex value per mapper frequency | Non-finite or unavailable complex values serialize through the mapper as missing; plots may independently skip invalid coordinates | `server/solver/result_mapping.py:185-200`; `server/solver/charts.py:367-419` |
| Plane DI | dB, clamped to at least 0; pressure is normalized to on-axis before integrating `p² sin(theta)` with an axisymmetric factor of two | Computed independently for each available polar plane | One DI value per frequency/pattern | Fewer than three finite samples yields `null`; non-positive integral yields 0 | `server/solver/directivity_index.py:11-75` |
| Spherical DI in beam shape | dB: `-10 log10(weighted mean power)` after peak-normalizing each spherical field | Uses the full theta/phi grid with `sin(theta)` weights | One optional value per balloon frequency | Any non-finite grid or non-positive energy yields `null` for that frequency | `server/solver/beam_shape.py:211-233`; `server/solver/beam_shape.py:236-312` |
| Directivity polar | Engine-provided dB or `null` samples paired with angle degrees; OPEN — the mapper performs no normalization, so the engine-level reference must be frozen separately | Named planes come from requested observation planes; canonical defaults define H=0°, V=90°, D=35° unless overridden | Patterns are arrays indexed by result frequency | Invalid samples become `null`; an unavailable plane is absent rather than fabricated | `server/solver/result_mapping.py:133-146`; `server/solver/contract.py:5-10`; `server/solver/contract.py:53-80` |
| Directivity map | Visualization of polar data; reference-level UI choices are -3, -6, -9, or -12 dB | Main map supports plane selection/filtering; dock variants explicitly request H or V | Uses result frequency/pattern ordering; compare payload preserves each job's own frequency vector | Invalid polar cells remain missing; server rendering derives warnings from usable data | `src/ui/simulation/viewResults.js:15-21`; `src/ui/simulation/chartRequests.js:9-73`; `src/ui/simulation/chartRequests.js:190-213` |
| Beamwidth | Degrees at the first -6 dB crossing from forward axis; reported separately for H and V when derivable | Derived from spherical balloon rays at phi 0°/90° | One optional H/V pair per balloon frequency | No crossing, a first-sample crossing, or an invalid ray yields no crossing; per-frequency beam shape may be `null` | `server/solver/beam_shape.py:27-33`; `server/solver/beam_shape.py:64-85`; `server/solver/beam_shape.py:236-312` |
| Balloon | Pressure is first converted to dB SPL, floored at -120 dB, then each frequency is made relative to its theta=0 first sample; published `dB_spl` is rounded to 0.01 dB | Theta-major grid; theta=0 is forward. Viewer maps x=H/phi0, y=V/phi90, z=forward/theta0 | Own frequency list follows available sphere-pressure frequencies | Four-state metadata distinguishes disabled, backend unsupported, requested-but-missing, and available | `server/solver/result_mapping.py:251-292`; `server/solver/result_mapping.py:357-391`; `src/ui/results/balloonPanel.js:1-13` |
| Beam shape | Superellipse fit, `-6 dB` contour, 144 rays, 181 samples, exponent constrained to `[0.75,8]`; includes H/V beamwidth, aspect, and spherical DI | Forward hemisphere only, derived from balloon | One object or `null` per balloon frequency | Requires at least 36 valid ray crossings; otherwise that frequency is `null` | `server/solver/beam_shape.py:27-33`; `server/solver/beam_shape.py:88-121`; `server/solver/beam_shape.py:161-208`; `server/solver/beam_shape.py:236-312` |
| Solver log / diagnostics | Structured strings plus convergence/condition metadata, warning and failure counts | Backend-specific; sphere-pressure payload is stripped from the exposed log | Diagnostics may identify individual frequencies, but v1 does not guarantee populated failure rows | Non-converged GMRES and non-zero LAPACK info add warnings and set partial success; suspect condition number warns without itself setting partial success | `server/solver/result_mapping.py:49-65`; `server/solver/result_mapping.py:202-249` |

## FR, raw phase, and presentation phase

The stored phase is raw pressure phase in degrees at the same on-axis sample as SPL (`server/solver/result_mapping.py:149-182`). A chart may then choose a spatial convention, subtract the expected propagation phase for the configured observation distance, and unwrap the result (`server/solver/charts.py:94-152`). Thus chart phase is a presentation product, not a replacement for stored raw phase.

The client resolves effective phase distance from explicit directivity distance first and observation distance second (`src/results/conventions.js:1-12`). Explicit `exp(+ikr)` maps to the Metal convention and `exp(-ikr)` to the legacy BEMPP convention; engine-name fallbacks can also select the convention (`src/results/conventions.js:14-69`). Current BEMPP solver metadata nevertheless declares `exp(+ikr)`, so consumers must honor explicit metadata before historical engine aliases (`server/solver/bempp_solver.py:323-348`; `src/results/conventions.js:14-69`).

OPEN — v2 must specify whether its public phase field remains raw wrapped phase, becomes delay-referenced/unwrapped phase, or exposes both. Golden fixtures need a known complex pressure, origin, distance, and both spatial-sign conventions (`server/solver/charts.py:94-152`).

## Impedance normalization and compatibility

The mapper produces normalized engineering-sign impedance directly (`server/solver/result_mapping.py:185-200`). Legacy chart/export consumers also contain a magnitude heuristic: if a value appears unnormalized (magnitude above roughly 20), divide it by `rho*c`; otherwise retain it (`server/solver/charts.py:160-184`; `src/ui/simulation/exports.js:234-275`). Explicit metadata is preferred by the client convention resolver (`src/results/conventions.js:72-93`).

OPEN — v2 needs a versioned field name/schema that eliminates magnitude guessing while retaining a compatibility importer for old jobs (`server/solver/charts.py:160-184`).

## Directivity, beamwidth, balloon, and beam shape

| Subject | v1 behavior | Evidence |
|---|---|---|
| Plane order | Export/UI normalization orders H, V, then D, followed by other named planes. | `src/ui/simulation/exports.js:131-151`; `src/ui/simulation/chartRequests.js:34-73` |
| Polar origin | Observation metadata records configured origin, with mouth as default. | `server/solver/result_mapping.py:98-130` |
| Balloon normalization | Raw sphere pressure becomes SPL with a -120 dB floor, then every frequency is normalized to its theta=0 first sample before publication. | `server/solver/result_mapping.py:251-292` |
| Hemisphere flag | Balloon is marked a hemisphere when its final theta is at most 90°. | `server/solver/result_mapping.py:251-292` |
| Viewer mapping | Balloon geometry uses H at phi 0°, V at phi 90°, and forward at theta 0°; radius/color display range is 0 to -30 dB. | `src/ui/results/balloonPanel.js:1-20` |
| Viewer missing cells | The balloon renderer substitutes -30 dB for a missing/non-finite cell for display geometry/color only. | `src/ui/results/balloonPanel.js:108-136` |
| Initial frequency | Balloon and forward-beam panels initially choose the available frequency closest to 1 kHz. | `src/ui/results/balloonPanel.js:286-300`; `src/ui/results/forwardBeamPanel.js:246-290` |
| Beam display | Forward-beam map shows -6, -12, and -18 dB contours and reads out H/V beamwidth and DI when present. | `src/ui/results/forwardBeamPanel.js:13-15`; `src/ui/results/forwardBeamPanel.js:219-230` |
| Four-state UI | UI distinguishes backend unsupported, requested but missing, and disabled messages; valid balloon data selects the available state. | `src/ui/results/resultsDock.js:208-230`; `server/solver/result_mapping.py:357-391` |

OPEN — the first theta=0, phi-index sample is used as the balloon normalization reference. A canonical grid contract must decide whether all phi samples at the pole must agree within tolerance and what happens if the reference is missing (`server/solver/result_mapping.py:251-292`).

## Frequency alignment and comparison

| Surface | v1 alignment rule | Evidence |
|---|---|---|
| Primary result arrays | Share mapper frequency ordering. | `server/solver/result_mapping.py:295-355` |
| FR/DI/impedance comparison | Each reference series retains its own frequency array; client request assembly does not resample onto the active job's grid. | `src/ui/simulation/chartRequests.js:130-187` |
| Directivity comparison | Reference directivity and reference frequencies are sent as their own pair. | `src/ui/simulation/chartRequests.js:190-213` |
| Polar CSV export | Iterates main SPL frequencies; if a plane has fewer pattern rows, it clamps to that plane's last pattern. Invalid dB becomes an empty field. | `src/ui/simulation/exports.js:552-588` |
| VACS export | Chooses horizontal plane if present, otherwise the first plane. It converts dB to linear magnitude; missing dB becomes zero magnitude. | `src/ui/simulation/exports.js:186-197`; `src/ui/simulation/exports.js:590-734` |
| Frequency CSV / summary text export | v1 zips DI and impedance against the SPL row index and emits only the SPL frequency, so a differing DI or impedance grid is silently mislabelled. **V2 does not port this** — see the union-grid join below. | `src/ui/simulation/exports.js:433-441`; `src/ui/simulation/exports.js:535-541` |

DECIDED 2026-08-10 — **exact-key union join**, for the v2 frequency CSV and summary text
only. Rows are the sorted union of the SPL, DI, and impedance frequency grids. A cell is
filled only where that series carries a sample at that exact frequency; otherwise it is
empty (CSV) or `n/a` (summary text), consistent with the missing-value rule above. No
value is interpolated, and no sample is dropped for lying outside another series' range.
The join is exact-key, not tolerance-based: two grids that differ by floating-point noise
produce separate rows rather than a silently merged one. Frequency is the row key, so a
grid that repeated a frequency — which no monotonic sweep produces — would yield one row
carrying that grid's first sample rather than two rows. When the grids agree — every
result the solver emits today — the union is the SPL grid and the output is byte-identical
to the pre-decision schema, so the frozen header is extended, not broken. Implemented at
`frontend/src/results/exporters.ts` (`joinSeries`), regression-tested in
`frontend/src/results/exporters.test.ts`.

OPEN — the same choice is still unmade for **compare overlays** and **polar CSV**. v1
comparison relies on plot-library handling of independent x arrays, and polar CSV silently
repeats the last available pattern; each still needs reject, exact-key join, tolerance
join, or interpolation selected per quantity (`src/ui/simulation/exports.js:552-588`).

## Missing values, failures, warnings, and partial success

| State | Mapper contract | Display/export contract | Evidence |
|---|---|---|---|
| Invalid scalar sample | Emit `null`; do not replace with 0. | Smoothing skips non-finite values; charts plot usable points. | `server/solver/result_mapping.py:133-182`; `src/results/smoothing.js:14-20` |
| GMRES non-convergence | Add warning and partial-success state. | Diagnostics panel lists run warnings while retaining available plots. | `server/solver/result_mapping.py:202-249`; `src/ui/simulation/results.js:262-410` |
| LAPACK non-zero info | Add warning and partial-success state. | Same partial diagnostics path. | `server/solver/result_mapping.py:202-249`; `src/ui/simulation/results.js:262-410` |
| Suspect condition number | Add warning only. | Warning is displayed; numeric arrays are not invalidated by mapper. | `server/solver/result_mapping.py:202-249`; `src/ui/simulation/results.js:262-410` |
| Per-frequency failure schema | Helper can describe frequency, stage, message, backend, recoverability, and details. | UI formats at most a bounded preview and reports total count. | `server/solver/contract.py:29-40`; `src/ui/simulation/results.js:262-410` |
| Missing result formats | Bundle records a per-format failure and continues other selected formats. | Mixed success is a warning, all-failed is an error. | `src/ui/simulation/exports.js:881-919` |
| Missing balloon | Explicit metadata status, not an empty fabricated grid. | Dock displays status-specific message. | `server/solver/result_mapping.py:357-391`; `src/ui/results/resultsDock.js:208-230` |

OPEN — no production caller of the `frequency_failure` helper was found in the bounded v1 result pipeline; the mapper defaults failure collections when absent. Evidence needed: a failing multi-frequency solver fixture proving which layer populates structured failures (`server/solver/contract.py:29-40`; `server/solver/result_mapping.py:295-355`).

OPEN — the declared `SimulationResults` Pydantic model is narrower than the actual mapper response: it lists only basic arrays/directivity while the mapper also emits metadata, balloon, and beam shape. Evidence needed: identify whether any runtime response validation uses this model, then replace it with one versioned authoritative schema (`server/contracts/__init__.py:382-388`; `server/solver/result_mapping.py:295-391`).

## Smoothing contract

V1 exposes `none` plus **ten** non-none algorithms, not nine: seven fixed fractional-octave modes, variable, psychoacoustic, and ERB (`src/ui/simulation/viewResults.js:64-84`; `src/results/smoothing.js:315-351`). Smoothing applies to SPL, DI, and impedance real/imaginary parts; raw phase is never smoothed (`src/ui/simulation/chartRequests.js:81-119`; `src/ui/simulation/exports.js:277-311`).

| Mode ID | Exact v1 rule | Missing/reference behavior | Evidence |
|---|---|---|---|
| `none` | Return source series without algorithmic smoothing. | Preserves original values. | `src/results/smoothing.js:315-351` |
| `1/1` | Gaussian fractional-octave window, `N=1`. | Positive-frequency neighbors only; missing y values skipped; original sample if total weight is zero. | `src/results/smoothing.js:22-25`; `src/results/smoothing.js:54-99` |
| `1/2` | Same algorithm, `N=2`. | Same. | `src/results/smoothing.js:54-99`; `src/results/smoothing.js:315-351` |
| `1/3` | Same algorithm, `N=3`. | Same. | `src/results/smoothing.js:54-99`; `src/results/smoothing.js:315-351` |
| `1/6` | Same algorithm, `N=6`. | Same. | `src/results/smoothing.js:54-99`; `src/results/smoothing.js:315-351` |
| `1/12` | Same algorithm, `N=12`. | Same. | `src/results/smoothing.js:54-99`; `src/results/smoothing.js:315-351` |
| `1/24` | Same algorithm, `N=24`. | Same. | `src/results/smoothing.js:54-99`; `src/results/smoothing.js:315-351` |
| `1/48` | Same algorithm, `N=48`. | Same. | `src/results/smoothing.js:54-99`; `src/results/smoothing.js:315-351` |
| `variable` | Effective fraction is 1/48 at/below 100 Hz, 1/3 at/above 10 kHz, and logarithmically interpolated between; at 1 kHz it is 1/12. | Per-target Gaussian log-frequency window; skips missing values and retains original if no weight. | `src/results/smoothing.js:102-169` |
| `psychoacoustic` | 1/3 octave at/below 100 Hz, 1/6 at/above 1 kHz, interpolated in log frequency between; combines signed values using a weighted signed-cubic mean. | Skips missing values; retains original if no usable weight. | `src/results/smoothing.js:175-252` |
| `erb` | Linear-Hz Gaussian with `ERB = 107.77*f_kHz + 24.673`; half-bandwidth is `ERB/2`, sigma is `ERB/4`. | Skips missing values; retains original if no usable weight. | `src/results/smoothing.js:254-313` |

For every fixed fractional mode, half bandwidth is `1/(2N)` octave and Gaussian sigma is half that half-bandwidth; only neighbors within the full half-bandwidth participate (`src/results/smoothing.js:54-99`). A frequency/value length mismatch returns the original value array unchanged (`src/results/smoothing.js:54-57`).

OPEN — “reference behavior” is code-defined but not fixture-defined. Freeze boundary frequencies, sparse/null samples, signed impedance, length mismatch, and all ten non-none modes in golden arrays (`src/results/smoothing.js:14-20`; `src/results/smoothing.js:315-351`).

## Export preservation

| Format | v1 result contract | Evidence |
|---|---|---|
| PNG/chart request | Includes phase-distance/time convention and impedance-unit metadata so renderer can reproduce presentation semantics. | `src/ui/simulation/exports.js:333-373` |
| Main CSV | v1 writes positionally-zipped result-series columns and leaves non-finite values empty. **v2 replaces the zip with the union-grid join above**; the header, column order, and empty-cell rule are unchanged, and extra rows appear only when a grid actually differs. | `src/ui/simulation/exports.js:424-452`; `frontend/src/results/exporters.ts` |
| Summary text | Same v1 zip in its DETAILED DATA table; v2 applies the same union-grid join, and its `Frequency range` / `Number of points` describe the joined rows it prints. | `src/ui/simulation/exports.js:535-541`; `frontend/src/results/exporters.ts` |
| JSON | Serializes `lastResults` as stored and adds the selected smoothing label; it does not replace raw arrays with smoothed arrays. | `src/ui/simulation/exports.js:454-473` |
| Polar CSV | Uses H/V/D ordering, main SPL frequency rows, and empty cells for invalid dB. | `src/ui/simulation/exports.js:552-588` |
| Impedance CSV | Writes normalized real and imaginary series. | `src/ui/simulation/exports.js:736-755` |
| VACS | Uses H if available else the first plane, converts dB to magnitude, and substitutes zero for missing magnitude. | `src/ui/simulation/exports.js:186-197`; `src/ui/simulation/exports.js:590-734` |

## v2 decisions required

1. Is the public phase contract raw wrapped phase, delay-referenced unwrapped phase, or both, and what single temporal/spatial sign convention is canonical (`server/solver/result_mapping.py:167-200`; `server/solver/charts.py:94-152`)?
2. How will explicit phase metadata override legacy backend-name aliases, especially because current BEMPP metadata declares `exp(+ikr)` (`server/solver/bempp_solver.py:323-348`; `src/results/conventions.js:14-69`)?
3. Will normalized impedance have a versioned, unambiguous complex field that removes v1's magnitude heuristic (`server/solver/charts.py:160-184`)?
4. Which observation defaults are canonical—H/V or H/V/D, 35° or configurable diagonal, mouth or another origin—and must every result store the resolved request (`server/contracts/__init__.py:101-168`; `server/solver/result_mapping.py:98-130`)?
5. What exact join/interpolation policy applies to compare overlays and exports when frequency or angle grids differ (`src/ui/simulation/chartRequests.js:130-213`; `src/ui/simulation/exports.js:552-588`)? — PARTLY DECIDED 2026-08-10: exact-key union join for the frequency CSV and summary text (see "Frequency alignment and comparison"). Compare overlays and polar CSV remain open.
6. Which samples become `null`, which entire frequencies become unreliable, and do non-convergence/condition warnings mask data or only annotate it (`server/solver/result_mapping.py:202-249`)?
7. Which layer must populate structured per-frequency failures, and what is the minimum schema retained in storage and export (`server/solver/contract.py:29-40`)?
8. Does v2 preserve all ten non-none smoothing modes despite the plan's “nine” wording, where does smoothing run, and are smoothed values presentation-only (`src/ui/simulation/viewResults.js:64-84`; `src/results/smoothing.js:315-351`)?
9. What golden fixture set freezes smoothing boundaries, null handling, signed impedance behavior, and reference outputs for every mode (`src/results/smoothing.js:14-20`; `src/results/smoothing.js:54-313`)?
10. Are plane DI and spherical beam-shape DI exposed as distinct named quantities, with separately specified integration domains and tolerances (`server/solver/directivity_index.py:11-75`; `server/solver/beam_shape.py:211-233`)?
11. What pole-consistency tolerance and missing-reference policy governs balloon normalization, and will balloons be stored eagerly or fetched lazily (`server/solver/result_mapping.py:251-292`)?
12. What constitutes requested-but-missing versus backend-unsupported balloon data after retries or partial failure (`server/solver/result_mapping.py:357-391`)?
13. Which solver-log fields and retention limits are public, and will v2 provide a raw-log panel in addition to derived warnings (`server/solver/result_mapping.py:49-65`; `src/ui/simulation/results.js:262-410`)?
14. What per-quantity `rtol/atol`, frequency tolerance, angular tolerance, phase-wrap tolerance, and missing-value equivalence define oracle parity (`server/solver/result_mapping.py:295-355`)?
