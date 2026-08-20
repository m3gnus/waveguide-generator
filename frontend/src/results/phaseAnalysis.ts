/**
 * On-axis phase and group delay, derived from the phase the solver already ships.
 *
 * `spl_on_axis.phase_degrees` has been in the result contract, the FRD export
 * and the exported PNG for a long time; only the interactive charts never read
 * it, so the figure on screen and the figure in the exported file were not the
 * same picture. The display detrending here is a port of hornlab-plots'
 * `_flatten_phase_for_display`, deliberately step-for-step, because that is
 * what makes the two agree.
 *
 * Three known divergences from that function remain, none of which changes a
 * curve for any payload a WG server actually writes, but all of which would if
 * the inputs widened — so they are stated rather than implied away:
 *
 * 1. Python fits a slope once it has two or more nonzero weights; this fits
 *    with one. A single in-band point makes `sxx == 0`, so `weightedLinearFit`
 *    returns the weighted mean as a pure offset and no slope, which is what the
 *    Python median branch would have produced anyway.
 * 2. For an *unrecognised* convention tag Python assumes a −1 spatial sign and
 *    de-embeds anyway; here `propagationReference` returns null and the raw
 *    unwrap is drawn instead. Kept deliberately: guessing the sign on a tag
 *    nobody recognises flips the curve silently, and a visibly untrended phase
 *    is the honest failure. Both agree on every tag `phaseConvention.ts` knows.
 * 3. Python requires `len(spl) >= n`; `alignToFrequencies` here matches SPL to
 *    phase by frequency instead, so a short SPL array still weights the points
 *    it covers rather than dropping the fit entirely.
 *
 * Group delay is the same de-embedded phase differentiated against angular
 * frequency. WG reports it in milliseconds with the bulk time-of-flight
 * removed, which is the convention the FRD exporter already established for
 * this product; hornlab-plots' research script plots cycles *including*
 * propagation, which is the right choice for comparing shapes across sources
 * and the wrong one for reading a delay off an axis.
 */
import { phaseSpatialSign, propagationReference, type PropagationReference } from './phaseConvention';

export { propagationReference, type PropagationReference };

/** Signal-level input: the frequency grid paired with wrapped phase in degrees. */
export interface PhaseSamples {
  frequencies: number[];
  phaseDegrees: Array<number | null>;
}

export interface PhasePoint {
  frequencyHz: number;
  value: number;
}

const TWO_PI = 2 * Math.PI;
const DEG_PER_RAD = 180 / Math.PI;

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Wrap radians to [-π, π). */
function wrapRadians(value: number): number {
  const wrapped = ((value + Math.PI) % TWO_PI + TWO_PI) % TWO_PI;
  return wrapped - Math.PI;
}

/**
 * De-embed the propagation term, then unwrap.
 *
 * Returns radians on the frequencies that carried a finite phase sample, with
 * the flight time to the observation point removed. Points whose phase is null
 * are dropped rather than interpolated: an unwrap across a gap invents a branch
 * choice, and a fabricated branch is indistinguishable from a real one once it
 * is drawn.
 */
export function deEmbeddedPhaseRadians(
  { frequencies, phaseDegrees }: PhaseSamples,
  reference: PropagationReference | null,
): PhasePoint[] {
  const points: PhasePoint[] = [];
  const count = Math.min(frequencies.length, phaseDegrees.length);
  for (let index = 0; index < count; index += 1) {
    const frequencyHz = frequencies[index];
    const degrees = phaseDegrees[index];
    if (!finite(frequencyHz) || frequencyHz <= 0 || !finite(degrees)) continue;
    let radians = degrees / DEG_PER_RAD;
    if (reference) {
      const propagation = reference.spatialSign
        * (TWO_PI * frequencyHz * reference.distanceM) / reference.speedOfSoundMps;
      radians = wrapRadians(radians - propagation);
    }
    points.push({ frequencyHz, value: radians });
  }
  // Unwrap in place across the retained samples.
  for (let index = 1; index < points.length; index += 1) {
    const step = points[index].value - points[index - 1].value;
    points[index].value -= TWO_PI * Math.round(step / TWO_PI);
  }
  return points;
}

/**
 * Output-level weights emphasising the passband: within 12 dB of peak SPL.
 *
 * Returns null when SPL cannot weight the fit, which asks the caller to fall
 * back to a median offset rather than a slope.
 */
export function passbandWeights(splDb: Array<number | null>, count: number): number[] | null {
  if (splDb.length < count) return null;
  const levels = splDb.slice(0, count);
  const present = levels.filter(finite);
  if (present.length < 2) return null;
  const peak = Math.max(...present);
  const inBand = levels.map((value) => finite(value) && value >= peak - 12);
  const band = inBand.filter(Boolean).length >= 2 ? inBand : levels.map(finite);
  return levels.map((value, index) => band[index] && finite(value) ? 10 ** ((value - peak) / 20) : 0);
}

/**
 * `np.median`, including the even-count case.
 *
 * The fallback offset has to be the same number hornlab-plots subtracts or the
 * two pictures separate by a constant on any sweep with an even sample count
 * and no usable SPL weighting — taking the upper middle instead of averaging
 * the pair is a real, if small, divergence.
 */
export function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const upper = sorted.length >> 1;
  return sorted.length % 2 ? sorted[upper] : (sorted[upper - 1] + sorted[upper]) / 2;
}

/** Weighted least squares for `y ≈ intercept + slope * x`. */
export function weightedLinearFit(x: number[], y: number[], weights: number[]): { slope: number; intercept: number } {
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  if (!(total > 0)) return { slope: 0, intercept: 0 };
  const xMean = x.reduce((sum, value, index) => sum + weights[index] * value, 0) / total;
  const yMean = y.reduce((sum, value, index) => sum + weights[index] * value, 0) / total;
  const sxx = x.reduce((sum, value, index) => sum + weights[index] * (value - xMean) ** 2, 0);
  if (!(sxx > 0)) return { slope: 0, intercept: yMean };
  const slope = x.reduce((sum, value, index) => sum + weights[index] * (value - xMean) * (y[index] - yMean), 0) / sxx;
  return { slope, intercept: yMean - slope * xMean };
}

/**
 * Phase in degrees, referenced and detrended for display.
 *
 * Two steps, matching the exported PNG: remove the observer propagation delay
 * so the unwrap can track the curve, then remove the residual group-delay
 * slope weighted by output level, so the trace sits flat where the speaker
 * actually radiates and crossover and resonance deviations stay visible.
 *
 * Unlike `groupDelayMilliseconds` this deliberately does *not* fold in the
 * spatial sign. The PNG does not flip either, and phase is the one curve whose
 * whole purpose is to be read against that file; a sign applied on only one
 * side would make the screen and the export disagree to fix a convention the
 * detrending has already largely absorbed.
 */
export function displayPhaseDegrees(
  samples: PhaseSamples,
  splDb: Array<number | null>,
  reference: PropagationReference | null,
): PhasePoint[] {
  const unwrapped = deEmbeddedPhaseRadians(samples, reference);
  if (!unwrapped.length) return [];
  const degrees = unwrapped.map(({ value }) => value * DEG_PER_RAD);
  const frequencies = unwrapped.map(({ frequencyHz }) => frequencyHz);
  // The weights follow the retained samples, not the original grid, so a
  // dropped phase point cannot shift SPL against frequency by one index.
  const alignedSpl = alignToFrequencies(samples.frequencies, splDb, frequencies);
  const weights = alignedSpl && passbandWeights(alignedSpl, frequencies.length);
  if (weights && weights.some((weight) => weight > 0)) {
    const { slope, intercept } = weightedLinearFit(frequencies, degrees, weights);
    return frequencies.map((frequencyHz, index) => ({
      frequencyHz,
      value: degrees[index] - (intercept + slope * frequencyHz),
    }));
  }
  const offset = median(degrees);
  return frequencies.map((frequencyHz, index) => ({ frequencyHz, value: degrees[index] - offset }));
}

/** Pick the values whose frequencies survived phase de-embedding. */
function alignToFrequencies(
  sourceFrequencies: number[],
  values: Array<number | null>,
  retained: number[],
): Array<number | null> | null {
  if (values.length < Math.min(sourceFrequencies.length, 1)) return null;
  const byFrequency = new Map<number, number | null>();
  sourceFrequencies.forEach((frequency, index) => {
    if (index < values.length) byFrequency.set(frequency, values[index]);
  });
  if (!byFrequency.size) return null;
  return retained.map((frequency) => byFrequency.get(frequency) ?? null);
}

/**
 * Whether a group delay derived from these samples can be trusted.
 *
 * Two conditions, and the first is the one that actually bites.
 *
 * 1. **A propagation reference is required.** Without the observation distance
 *    and sound speed, the phase still carries the flight time to the
 *    microphone. That term advances the phase by roughly two full turns between
 *    adjacent points of an ordinary log sweep at 2 m, so the unwrap picks
 *    branches at random and the "delay" that comes out is a fiction. It is also
 *    meaningless even when it is right: a delay is only interesting relative to
 *    a known path.
 *
 * 2. **The residual must satisfy the sampling condition.** With the flight time
 *    removed, what remains is the excess delay, which for a waveguide is a
 *    fraction of a millisecond and unwraps comfortably. This checks the
 *    estimated residual delay against the coarsest step in the sweep. It is a
 *    best-effort test rather than a proof: it is estimated from the same
 *    unwrap it is checking, so a badly aliased residual would under-report its
 *    own delay and pass. Condition 1 is what makes that case unreachable in
 *    practice.
 *
 * The sharper limitation is that condition 2 compares an *endpoint average*
 * against the widest step, so it is blind to localized aliasing. A sharp HF
 * resonance that advances the phase by more than half a turn between two
 * adjacent samples corrupts the curve from that point on while the average
 * slope across the whole sweep stays small and the test still passes. Nothing
 * here can detect that: wrapped phase does not carry the branch it came from,
 * so the only fix is a finer sweep, not a cleverer check.
 */
export function phaseUnwrapIsResolved(
  phase: PhasePoint[],
  reference: PropagationReference | null,
): boolean {
  if (!reference || phase.length < 2) return false;
  const first = phase[0];
  const last = phase[phase.length - 1];
  const span = last.frequencyHz - first.frequencyHz;
  if (!(span > 0)) return false;
  const residualDelaySeconds = (last.value - first.value) / (TWO_PI * span);
  let widestStepHz = 0;
  for (let index = 1; index < phase.length; index += 1) {
    widestStepHz = Math.max(widestStepHz, phase[index].frequencyHz - phase[index - 1].frequencyHz);
  }
  return Math.abs(TWO_PI * widestStepHz * residualDelaySeconds) < Math.PI;
}

/**
 * Group delay in milliseconds, with the common time-of-flight removed.
 *
 * `GD = ±dφ/dω` on the de-embedded phase — sign from the spatial convention,
 * see below — by central differences over the irregular (log-spaced) sweep. The
 * first and last points use one-sided differences. Fewer than three retained
 * samples yields nothing: a two-point "curve" is a single slope drawn as if it
 * were a function of frequency.
 *
 * Nothing is returned when the sweep is too coarse to resolve the phase
 * unambiguously — see `phaseUnwrapIsResolved`. An empty result puts the chart's
 * stub on screen, which is the truthful outcome; a drawn curve would not be.
 */
export function groupDelayMilliseconds(
  samples: PhaseSamples,
  reference: PropagationReference | null,
): PhasePoint[] {
  const phase = deEmbeddedPhaseRadians(samples, reference);
  if (!reference || phase.length < 3 || !phaseUnwrapIsResolved(phase, reference)) return [];
  const omega = phase.map(({ frequencyHz }) => TWO_PI * frequencyHz);
  return phase.map(({ frequencyHz }, index) => {
    const lower = Math.max(0, index - 1);
    const upper = Math.min(phase.length - 1, index + 1);
    const span = omega[upper] - omega[lower];
    const slope = span > 0 ? (phase[upper].value - phase[lower].value) / span : 0;
    // dφ/dω is +τ only under a positive spatial convention: exp(-ikr) writes a
    // delay as a *falling* phase, so the same 0.3 ms would read as -0.3 ms.
    // Latent rather than live -- no current server writer emits a tag that maps
    // to -1 (`result_mapping.py` and the engines all tag exp(+ikr)-family
    // conventions) -- but the sign is a property of the tag, not of the writer,
    // and an imported or future result carrying one must not plot inverted.
    // Milliseconds because a waveguide's excess delay is a fraction of one.
    return { frequencyHz, value: reference.spatialSign * slope * 1_000 };
  });
}

/** Whether a result carries enough on-axis phase to derive these curves. */
export function hasOnAxisPhase(phaseDegrees: Array<number | null> | undefined): boolean {
  return Array.isArray(phaseDegrees) && phaseDegrees.some(finite);
}

export { phaseSpatialSign };
