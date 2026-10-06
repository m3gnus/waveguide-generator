# Adaptive BEAT frequency sampling

The Simulation controls have an application preference, **Adaptive frequency
sampling (experimental)**, stored alongside the solver preferences. It is off
by default. The request option `adaptive_frequency_sampling: true` enables it
for BEAT exterior sweeps with at least 24 requested frequencies, including
explicit lists. Accurate selects BEAT; an explicit BEAT backend also qualifies.
Other engines, smaller sweeps, and non-exterior formulations keep their existing
execution. No engine, dependency pin, or solver precision changes.

The planner starts at eight log-spaced acquisition frequencies, always including
both endpoints. It selects batches of four using rational fit disagreement and
log-gap coverage queries. Oversized gaps take priority. Completed acquisition
requires every gap between adjacent solved frequencies to be **<= 1/6 octave**
(`log2(f_right / f_left) <= 1/6`). The single constant
`MAX_SOLVED_GAP_OCTAVES` in `server/solver/adaptive_sweep.py` defines this floor.
If an explicit or linear requested grid already has wider adjacent gaps, the
native acquisition grid adds geometric coverage frequencies inside those gaps;
publication still uses exactly the original requested grid. Live coarse-to-fine
snapshots can precede completion of the density floor and remain provisional.

In-between frequencies are reconstructed with a **set-valued AAA barycentric
rational fit**, with shared supports and weights across channels: each channel
has its own numerator and all channels share one scalar denominator. This uses
complex pressure and impedance, spherical pressure when requested, and retained
boundary traces. Known observation propagation delay is removed before fitting
and restored afterward. Each channel is scaled separately. SciPy (already pinned)
provides generalized eigenvalues for pole diagnostics; NumPy handles fitting.
Chunked QR compresses large Loewner systems when boundary traces add thousands
of channels, keeping fit memory bounded.

The native contract uses `exp(-i omega t)` and outgoing `exp(+ikr)`. Froissart
cleanup removes weak vector residues only if refitting preserves sampled data.
In-band real-axis or growing-side poles are reflected into the damped half-plane
and all numerators refitted from solved rows. A fit is published only after its
poles and evaluated values pass the guard. Its sample residual, degree
comparison, previous-fit comparison and three leave-one-out comparisons all
participate in the stopping estimate. Two guarded refinement rounds below
0.1 dB equivalent disagreement, together with the density floor, end acquisition.
The per-frequency disagreement between complex vectors `a` and `b` is the
maximum over channels of `20*log10(1 + abs(a-b)/denominator)`, where the denominator
is `max(abs(a), abs(b), channel_peak*10**(-30/20), 1e-30)` and `channel_peak` is
the larger peak magnitude of the two predictions on the acquisition grid. The
stopping estimate is the maximum on unsolved acquisition rows over reduced-degree,
previous-fit and three leave-one-out disagreements, plus the largest solved-row
residual. The 30 dB floor avoids singular relative errors near response nulls.
These are fit consistency checks using only acquired data, not a comparison with
unsolved truth.

If a fit fails, becomes nonfinite, fails its pole guard, or cannot meet the
stopping criteria, more native rows are acquired. The fallback is the full
acquisition grid (requested rows plus any needed geometric coverage rows), with
exact native values and no interpolation. Unsafe fitted snapshots are withheld.
Solved requested rows are restored exactly before every publication.

**The disagreement tolerance is not a certified error bound.** A resonance
narrower than the largest gap between sampled frequencies can be missed, even
when all fits agree below 0.1 dB. With the density floor the largest gap in a
completed acquisition is <= 1/6 octave; this bounds sample spacing, not resonance
height, location, width or interpolation error. It does not guarantee detection
of every sharp resonance between samples. Mesh, quadrature and solver errors
are separate. This preference is suitable for exploratory dense sweeps; validate
features of interest with a full sweep. Batched requests can repeat native setup
cost, so fewer solves do not imply the same factor of wall-time improvement.
Retained surface traces participate in the fit and can increase solve counts.
Imported axial groups share each selected batch. Their signed native rows are
summed before fitting, so convergence and its pressure scale belong to the
returned channel even when the groups nearly cancel.

## Backward-compatible result extension

Adaptive results keep contract version 1 (or the existing multi-channel wrapper
version 2) and all requested frequency rows. Optional `frequency_status` is an
array parallel to `frequencies`, with entries `"solved"` or `"interpolated"`.
It is absent on the default execution path and historical results. Each imported
channel carries its own flags; the wrapper and combined channel mark a point
solved only when all contributing channels solved it. Persisted channel bases
retain the flags for later recombination; a signed drive-group sum is solved at a frequency
only if every contributing group solved that frequency. Driver transformations
use the reconstructed complex bases afterward.

`metadata.adaptive_sampling` records `solved_count`, `requested_count`,
`tolerance_db`, `estimate_db` and `stop_reason` (`estimated_convergence` or
`full_sweep`). For signed groups, `solved_count` counts common solved frequencies;
it is not the total number of native group solves. `native_solved_count` also
counts extra coverage frequencies on sparse requests;
`solved_frequencies_hz`, `max_gap_octaves` and `max_allowed_gap_octaves` describe
the complete native acquisition. Native timings and logs retain all batches.
Derived combinations report `derived: true` with their frequency
counts instead of a fit tolerance. The frontend marks interpolated on-axis SPL
points with small hollow
circles and labels that convention in the series name. Every result chart and
field-plane controls also disclose
adaptive reconstruction. Numeric CSV, summary, FRD, ZMA, derived acoustics,
full JSON, static reports, pressure-basis NPZ and portable radiation packages
carry a warning, solved/interpolated requested-row counts, and row flags or a
solved-frequency list. Historical/unflagged exports retain their exact bytes.
False/unset request options are omitted from model serialization, stored configs,
job responses and provenance digests for every engine.

Live adaptive messages use complete fitted frequency snapshots. The presence of
`frequency_status` replaces the channel's previous frequency-shaped rows rather
than appending duplicates. Other channels are preserved and revisions continue
increasing. Unflagged live messages retain their existing append behavior.
Unsafe fits are withheld while additional solves are acquired. Imported snapshots
carry the authoritative CAD observation frame and provisional solved/requested
frequency counts. Progress accumulates native rows across batches and groups.
Wrapper flags are recomputed on every snapshot as the intersection of all drive
channels; a channel that has not published yet contributes no solved rows.

The official BEAT bridge uses the same planner, but remains unregistered for
normal engine routing. Its existing per-request worker lifecycle is retained,
including cleanup and cancellation; each adaptive batch therefore starts and
closes its worker. This path needs persistent-worker integration before a timing
benefit can be assumed.

## Offline regression fixture

`server/tests/fixtures/adaptive-ref-S.npz` contains 193 reference frequencies,
37 complex arc pressures at 2 m (0–180 degrees, five-degree steps), and complex
mechanical radiation impedance for a 963-node quarter-symmetric OSSE horn,
200–1051 Hz. Values are the physical normal-velocity basis before WG's
unit-acceleration rescaling. The source reference used BEAT Metal float32,
Burton–Miller, four Julia threads, sound speed 343 m/s and density 1.2041 kg/m³.
Mesh SHA-256: `9f8ff975d49d492384f13e3bdeff3917719f03d4b6ddd89a61f32417fa09f864`.
The fixture is repository-relative and contains no machine or runtime paths.

## Density-floor replay (fix round 3)

Offline `../w4/measure.py` replay on 2026-10-01, using only acquired stored rows:

| Stored reference | Requested | Native solves, velocity | Native solves, acceleration | Largest gap (octaves) | Worst arc magnitude error within 30 dB of each angle's peak, velocity / acceleration |
| --- | ---: | ---: | ---: | ---: | ---: |
| S, quarter OSSE, 200–1051 Hz | 193 | 32 | 32 | 0.162073 | 0.000240 / 0.000208 dB |
| C, imported CAD, 200–2106 Hz | 193 | 44 | 44 | 0.123828 | 0.008870 / 0.018822 dB |

The 24-requested-row subsets each solve all 24 rows. These counts and errors
are fixture measurements, not guarantees for other geometry or a wall-time
speedup measurement. On C, worst unmasked arc errors are 1.8694 / 3.5555 dB,
including deep nulls, so the masked numbers must not be read as a universal
0.1 dB error bound. Retained surface traces are not in these S/C fixtures and
can increase acquisition counts. The new coverage query order can reduce the
count for one fixture while increasing it for another.

Platform-stable selection sorts finite scores largest first, then anchors each
tie group at its maximum. Scores within `1e-12` of that anchor prefer the lower
frequency/index; nearby neighbors do not chain into a wider group. Gap scores
use octaves (`log2` frequency differences), and disagreement scores use dB.
Midpoint distances within `1e-12` octaves of the minimum also prefer the lower
frequency. NaN and infinite scores stay last, in their original index order.
These ordering guards do not change convergence tests or reported disagreement,
which retain their unrounded values.
