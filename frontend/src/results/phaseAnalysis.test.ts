import { describe, expect, it } from 'vitest';
import {
  deEmbeddedPhaseRadians,
  displayPhaseDegrees,
  groupDelayMilliseconds,
  hasOnAxisPhase,
  median,
  phaseUnwrapIsResolved,
  passbandWeights,
  propagationReference,
  weightedLinearFit,
} from './phaseAnalysis';
import type { ResultPayload } from './types';

const SPEED = 343;
const DISTANCE = 2;

/** A result whose observation metadata supports propagation de-embedding. */
function result(overrides: Partial<ResultPayload> = {}): ResultPayload {
  return {
    frequencies: [],
    metadata: {
      phase_time_convention: 'exp(+ikr)',
      observation: { effective_distance_m: DISTANCE, sound_speed_m_per_s: SPEED },
    },
    ...overrides,
  } as ResultPayload;
}

/**
 * Phase of a source at `distance` plus an extra `delaySeconds`, wrapped the way
 * the solver reports it. Under exp(+ikr) the phase grows as +kr.
 */
function wrappedPhaseDegrees(frequencies: number[], delaySeconds: number): number[] {
  return frequencies.map((frequency) => {
    const radians = 2 * Math.PI * frequency * (DISTANCE / SPEED + delaySeconds);
    const wrapped = ((radians + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    return wrapped * 180 / Math.PI;
  });
}

const SWEEP = Array.from({ length: 60 }, (_, index) => 200 * 1.06 ** index);

describe('propagation reference', () => {
  it('reads distance, sound speed and spatial sign from the result contract', () => {
    expect(propagationReference(result())).toEqual({ distanceM: DISTANCE, speedOfSoundMps: SPEED, spatialSign: 1 });
  });

  it('refuses a result whose convention tag is unknown, rather than guessing a sign', () => {
    expect(propagationReference(result({
      metadata: { phase_time_convention: 'something-new', observation: { effective_distance_m: 2, sound_speed_m_per_s: 343 } },
    } as Partial<ResultPayload>))).toBeNull();
  });

  it('has no reference without observation metadata', () => {
    expect(propagationReference({ frequencies: [] } as ResultPayload)).toBeNull();
  });
});

describe('phase de-embedding', () => {
  it('recovers a linear residual from wrapped phase', () => {
    const delay = 0.0004;
    const points = deEmbeddedPhaseRadians(
      { frequencies: SWEEP, phaseDegrees: wrappedPhaseDegrees(SWEEP, delay) },
      propagationReference(result()),
    );
    expect(points).toHaveLength(SWEEP.length);
    // The propagation term is gone, so what remains is 2*pi*f*delay, unwrapped
    // and monotone rather than sawtoothed.
    points.forEach(({ frequencyHz, value }) => {
      expect(value).toBeCloseTo(2 * Math.PI * frequencyHz * delay, 6);
    });
  });

  it('drops null phase samples rather than interpolating a branch across the gap', () => {
    const frequencies = [100, 200, 300];
    const points = deEmbeddedPhaseRadians({ frequencies, phaseDegrees: [10, null, 30] }, null);
    expect(points.map(({ frequencyHz }) => frequencyHz)).toEqual([100, 300]);
  });

  it('leaves the phase alone when no reference is available', () => {
    const points = deEmbeddedPhaseRadians({ frequencies: [1_000], phaseDegrees: [90] }, null);
    expect(points[0].value).toBeCloseTo(Math.PI / 2, 12);
  });
});

describe('group delay', () => {
  it('reports a pure excess delay in milliseconds, time-of-flight removed', () => {
    const delay = 0.00025;
    const points = groupDelayMilliseconds(
      { frequencies: SWEEP, phaseDegrees: wrappedPhaseDegrees(SWEEP, delay) },
      propagationReference(result()),
    );
    expect(points).toHaveLength(SWEEP.length);
    points.forEach(({ value }) => expect(value).toBeCloseTo(delay * 1_000, 6));
  });

  it('refuses a sweep too coarse to unwrap rather than drawing a fabricated curve', () => {
    // Without de-embedding, a 2 m path advances the phase by about two full
    // turns between adjacent log-spaced samples. The unwrap cannot recover the
    // branch, so a group delay derived from it would be confidently wrong.
    const aliased = deEmbeddedPhaseRadians({ frequencies: SWEEP, phaseDegrees: wrappedPhaseDegrees(SWEEP, 0) }, null);
    expect(phaseUnwrapIsResolved(aliased, null)).toBe(false);
    expect(groupDelayMilliseconds({ frequencies: SWEEP, phaseDegrees: wrappedPhaseDegrees(SWEEP, 0) }, null)).toEqual([]);
  });

  it('accepts the same sweep once the propagation term is de-embedded', () => {
    const resolved = deEmbeddedPhaseRadians(
      { frequencies: SWEEP, phaseDegrees: wrappedPhaseDegrees(SWEEP, 0.00025) },
      propagationReference(result()),
    );
    expect(phaseUnwrapIsResolved(resolved, propagationReference(result()))).toBe(true);
  });

  it('yields nothing from fewer than three samples', () => {
    expect(groupDelayMilliseconds({ frequencies: [100, 200], phaseDegrees: [0, 10] }, null)).toEqual([]);
  });

  it('does not catch a residual that aliases so badly it under-reports itself', () => {
    // A known and documented limitation, pinned so a future strengthening of
    // the check is a deliberate change rather than an accident. The residual
    // delay is estimated from the same unwrap it is meant to validate, so a
    // 20 ms delay sampled far too coarsely looks like a ~0 ms one and passes.
    // Requiring a propagation reference is what keeps this unreachable in
    // practice: real excess delays are a fraction of a millisecond.
    const coarse = [1_000, 1_400, 1_960, 2_744, 3_842];
    const points = groupDelayMilliseconds(
      { frequencies: coarse, phaseDegrees: wrappedPhaseDegrees(coarse, 0.02) },
      propagationReference(result()),
    );
    expect(points).not.toEqual([]);
    expect(Math.abs(points[0].value)).toBeLessThan(1);
  });
});

describe('display phase', () => {
  it('detrends a pure delay to a flat trace', () => {
    const frequencies = SWEEP;
    const points = displayPhaseDegrees(
      { frequencies, phaseDegrees: wrappedPhaseDegrees(frequencies, 0.0004) },
      frequencies.map(() => 90),
      propagationReference(result()),
    );
    // A constant group delay is exactly the slope the weighted fit removes, so
    // nothing should be left: this is what makes the curve readable.
    points.forEach(({ value }) => expect(Math.abs(value)).toBeLessThan(1e-6));
  });

  it('keeps a deviation that is not a constant delay', () => {
    const frequencies = SWEEP;
    const phase = wrappedPhaseDegrees(frequencies, 0.0004);
    const bumped = phase.map((value, index) => index === 30 ? value + 40 : value);
    const points = displayPhaseDegrees(
      { frequencies, phaseDegrees: bumped },
      frequencies.map(() => 90),
      propagationReference(result()),
    );
    expect(Math.abs(points[30].value)).toBeGreaterThan(20);
  });

  it('falls back to a median offset when SPL cannot weight the fit', () => {
    const frequencies = [100, 200, 300];
    const points = displayPhaseDegrees({ frequencies, phaseDegrees: [10, 20, 30] }, [], null);
    points.map(({ value }) => value).forEach((value, index) => expect(value).toBeCloseTo([-10, 0, 10][index], 9));
  });

  it('returns nothing when there is no phase at all', () => {
    expect(displayPhaseDegrees({ frequencies: [100], phaseDegrees: [null] }, [], null)).toEqual([]);
  });
});

describe('passband weighting', () => {
  it('weights only within 12 dB of the peak', () => {
    const weights = passbandWeights([90, 89, 40], 3);
    expect(weights).not.toBeNull();
    expect(weights![0]).toBeCloseTo(1, 12);
    expect(weights![1]).toBeGreaterThan(0);
    expect(weights![2]).toBe(0);
  });

  it('widens to every finite sample when the 12 dB band is too narrow to fit', () => {
    const weights = passbandWeights([90, 40, 20], 3);
    expect(weights!.every((weight) => weight > 0)).toBe(true);
  });

  it('declines to weight when fewer than two levels are present', () => {
    expect(passbandWeights([90], 1)).toBeNull();
    expect(passbandWeights([], 3)).toBeNull();
  });
});

describe('weighted linear fit', () => {
  it('recovers a known slope and intercept', () => {
    const x = [1, 2, 3, 4];
    const y = x.map((value) => 3 * value + 7);
    const { slope, intercept } = weightedLinearFit(x, y, [1, 1, 1, 1]);
    expect(slope).toBeCloseTo(3, 12);
    expect(intercept).toBeCloseTo(7, 12);
  });

  it('degenerates safely when every weight is zero', () => {
    expect(weightedLinearFit([1, 2], [1, 2], [0, 0])).toEqual({ slope: 0, intercept: 0 });
  });
});

describe('phase availability', () => {
  it('detects usable phase and rejects an all-null block', () => {
    expect(hasOnAxisPhase([null, 12])).toBe(true);
    expect(hasOnAxisPhase([null, null])).toBe(false);
    expect(hasOnAxisPhase(undefined)).toBe(false);
  });
});

describe('median matches numpy', () => {
  it('averages the two middle values on an even count', () => {
    // The detrending fallback subtracts this, so taking the upper middle
    // instead offsets the whole on-screen curve against the exported PNG.
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(median([4, 1, 3, 2])).toBe(2.5);
  });

  it('takes the middle value on an odd count, and zero on an empty one', () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([])).toBe(0);
  });
});

describe('group delay sign follows the spatial convention', () => {
  const sweep = Array.from({ length: 40 }, (_, index) => 300 * 1.06 ** index);

  it('reports a positive delay as positive under exp(-ikr) too', () => {
    // Latent: no server writer emits a negative-spatial tag today. But the sign
    // belongs to the tag, and under exp(-ikr) a delay is a *falling* phase, so
    // the raw dφ/dω would render a real +0.3 ms as -0.3 ms.
    const delaySeconds = 0.0003;
    const phaseDegrees = sweep.map((frequency) => {
      const radians = -2 * Math.PI * frequency * (DISTANCE / SPEED + delaySeconds);
      const wrapped = ((radians + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
      return wrapped * 180 / Math.PI;
    });
    const negative = result({ metadata: {
      phase_time_convention: 'exp(-ikr)',
      observation: { effective_distance_m: DISTANCE, sound_speed_m_per_s: SPEED },
    } } as Partial<ResultPayload>);
    const reference = propagationReference(negative);
    expect(reference?.spatialSign).toBe(-1);
    const delays = groupDelayMilliseconds({ frequencies: sweep, phaseDegrees }, reference);
    expect(delays.length).toBe(sweep.length);
    delays.forEach(({ value }) => expect(value).toBeCloseTo(0.3, 6));
  });

  it('is unchanged under exp(+ikr), which is what every current result tags', () => {
    const delays = groupDelayMilliseconds(
      { frequencies: sweep, phaseDegrees: wrappedPhaseDegrees(sweep, 0.0003) },
      propagationReference(result()),
    );
    delays.forEach(({ value }) => expect(value).toBeCloseTo(0.3, 6));
  });
});
