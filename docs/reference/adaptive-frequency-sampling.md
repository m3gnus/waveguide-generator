# Adaptive BEAT frequency sampling

The Simulation controls have an application preference, **Adaptive frequency
sampling (experimental)**, stored alongside the solver preferences. It is off
by default. The request option `adaptive_frequency_sampling: true` enables it
for BEAT exterior sweeps with at least 24 requested frequencies, including
explicit lists. Accurate selects BEAT; an explicit BEAT backend also qualifies.
Other engines, smaller sweeps, and non-exterior formulations keep their existing
execution. No engine, dependency pin, or solver precision changes.

The planner starts at eight log-spaced requested frequencies, always including
both endpoints. It selects batches of four using rational fit disagreement and
a largest log-gap coverage query. A shared-denominator, set-valued AAA fit uses
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
0.1 dB equivalent disagreement end acquisition. Otherwise acquisition continues
to the complete request. Solved rows are restored exactly before publication.

**The disagreement tolerance is not a certified error bound.** A narrow resonance
between sampled frequencies can be missed. Mesh, quadrature and solver errors
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
it is not the total number of native group solves. Native timings and logs retain
all batches. Derived combinations report `derived: true` with their frequency
counts instead of a fit tolerance. The frontend marks interpolated on-axis SPL points with small hollow
circles and labels that convention in the series name.

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
