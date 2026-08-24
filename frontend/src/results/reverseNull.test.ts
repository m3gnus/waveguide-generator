import { describe, expect, it } from 'vitest';
import { sectionResponse, type Complex } from './crossoverFilters';
import { reverseNullTraces } from './reverseNull';
import type { CombineMetadata, ResultPayload } from './types';

const FREQUENCIES = [500, 800, 1_000, 1_250, 2_000];
const LP = { family: 'lr' as const, order: 4, fcHz: 1_000 };
const HP = { family: 'lr' as const, order: 4, fcHz: 1_000 };
/** A level trim on the low band, so the null is deep but not exactly zero —
 * which is what a real pair looks like. */
const MF_GAIN_DB = -0.5;

function scale(value: Complex, factor: number): Complex {
  return [value[0] * factor, value[1] * factor];
}

/**
 * The two weighted members and their sum, in the engineering convention the
 * solver defines them in.
 *
 * `sectionResponse` is checked against `server/solver/filters.py` itself in
 * `crossoverFilters.test.ts`, so building the fixture with it states the same
 * numbers the solver would have produced rather than a second guess at them.
 */
function weighted(frequency: number): { mf: Complex; hf: Complex } {
  const gain = 10 ** (MF_GAIN_DB / 20);
  return {
    mf: scale(sectionResponse(LP, 'lp', frequency), gain),
    hf: sectionResponse(HP, 'hp', frequency),
  };
}

/** A member solved as an ideal coincident unit source: 0 dB, 0°. */
function unitMember(): ResultPayload {
  return {
    frequencies: [...FREQUENCIES],
    spl_on_axis: {
      frequencies: [...FREQUENCIES],
      spl: FREQUENCIES.map(() => 0),
      phase_degrees: FREQUENCIES.map(() => 0),
    },
  };
}

/** The sum those two members produce, in the raw convention the payload
 * carries: the engineering sum, conjugated once. */
function combinedChannel(combine: CombineMetadata): ResultPayload {
  return {
    frequencies: [...FREQUENCIES],
    spl_on_axis: {
      frequencies: [...FREQUENCIES],
      spl: FREQUENCIES.map((frequency) => {
        const { mf, hf } = weighted(frequency);
        return 20 * Math.log10(Math.hypot(mf[0] + hf[0], mf[1] + hf[1]));
      }),
      phase_degrees: FREQUENCIES.map((frequency) => {
        const { mf, hf } = weighted(frequency);
        return (-Math.atan2(mf[1] + hf[1], mf[0] + hf[0]) * 180) / Math.PI;
      }),
    },
    metadata: { combine: combine as unknown as Record<string, unknown> },
  };
}

function metadata(overrides: Partial<CombineMetadata> = {}): CombineMetadata {
  return {
    members: ['drive-mf', 'drive-hf'],
    member_roles: ['MF', 'HF'],
    reference: 'drive-hf',
    crossovers_hz: [1_000],
    channels: {
      'drive-mf': {
        hp: null,
        lp: { family: 'lr', order: 4, fc_hz: 1_000 },
        gain_db: MF_GAIN_DB, gain_mode: 'auto', delay_ms: 0, delay_mode: 'auto',
        inverted: false, invert_mode: 'auto',
      },
      'drive-hf': {
        hp: { family: 'lr', order: 4, fc_hz: 1_000 },
        lp: null,
        gain_db: 0, gain_mode: 'auto', delay_ms: 0, delay_mode: 'auto',
        inverted: false, invert_mode: 'auto',
      },
    },
    ...overrides,
  };
}

function wrapperFor(combined: ResultPayload): ResultPayload {
  return {
    frequencies: [...FREQUENCIES],
    channel_order: ['drive-mf', 'drive-hf', 'combined'],
    channels: { 'drive-mf': unitMember(), 'drive-hf': unitMember(), combined },
  };
}

describe('reverseNullTraces', () => {
  it('rebuilds the sum with the upper member inverted', () => {
    const combine = metadata();
    const combined = combinedChannel(combine);
    const traces = reverseNullTraces(wrapperFor(combined), combined, combine);

    expect(traces).toHaveLength(1);
    expect(traces[0].name).toBe('Reverse null · MF → HF');
    expect(traces[0].points).toHaveLength(FREQUENCIES.length);

    const atCrossover = traces[0].points.find(([frequency]) => frequency === 1_000)!;
    const { mf, hf } = weighted(1_000);
    expect(atCrossover[1]).toBeCloseTo(20 * Math.log10(Math.hypot(mf[0] - hf[0], mf[1] - hf[1])), 9);
    // The whole point of the check: inverting one member must cancel deeply.
    expect(atCrossover[1]).toBeLessThan(-25);
    // Every point is the same difference, not only the one at the corner.
    traces[0].points.forEach(([frequency, level]) => {
      const { mf: low, hf: high } = weighted(frequency);
      expect(level).toBeCloseTo(20 * Math.log10(Math.hypot(low[0] - high[0], low[1] - high[1])), 9);
    });
  });

  it('withholds the overlay when the rebuilt sum does not match the shipped one', () => {
    const combine = metadata();
    const combined = combinedChannel(combine);
    combined.spl_on_axis!.spl = FREQUENCIES.map(() => 20);
    expect(reverseNullTraces(wrapperFor(combined), combined, combine)).toEqual([]);
  });

  it('withholds the overlay when a member carries no phase', () => {
    const combine = metadata();
    const combined = combinedChannel(combine);
    const wrapper = wrapperFor(combined);
    delete (wrapper.channels!['drive-mf'] as ResultPayload).spl_on_axis!.phase_degrees;
    expect(reverseNullTraces(wrapper, combined, combine)).toEqual([]);
  });

  it('withholds the overlay for an order this build cannot evaluate', () => {
    const combine = metadata();
    combine.channels!['drive-mf'].lp = { family: 'lr', order: 3, fc_hz: 1_000 };
    const combined = combinedChannel(combine);
    expect(reverseNullTraces(wrapperFor(combined), combined, combine)).toEqual([]);
  });

  it('reads a legacy payload, which is the same LR4 pair without a channels map', () => {
    const combine = metadata();
    const combined = combinedChannel(combine);
    const wrapper = wrapperFor(combined);
    const legacy: CombineMetadata = {
      members: ['drive-mf', 'drive-hf'],
      member_roles: ['MF', 'HF'],
      crossovers_hz: [1_000],
      gains_db: { 'drive-mf': MF_GAIN_DB, 'drive-hf': 0 },
      delays_ms: { 'drive-mf': 0, 'drive-hf': 0 },
    };
    const traces = reverseNullTraces(wrapper, combined, legacy);
    expect(traces).toHaveLength(1);
    expect(traces[0].points.find(([frequency]) => frequency === 1_000)![1]).toBeLessThan(-25);
  });

  it('has nothing to draw without a combine record', () => {
    const combine = metadata();
    const combined = combinedChannel(combine);
    expect(reverseNullTraces(wrapperFor(combined), combined, null)).toEqual([]);
    expect(reverseNullTraces(undefined, combined, combine)).toEqual([]);
  });
});
