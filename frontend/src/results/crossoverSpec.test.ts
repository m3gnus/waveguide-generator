import { describe, expect, it } from 'vitest';
import {
  driverXoMinNote,
  expandLegacy,
  FILTER_FAMILY_ORDERS,
  fromResult,
  isSimple,
  nearestOrder,
  pairsOf,
  parseSpec,
  parseWire,
  relinkPairs,
  resolvedChannels,
  sameSpec,
  sectionLabel,
  sharedDelayMode,
  sharedGainMode,
  slopeLabel,
  toWire,
  unlinkedPairNote,
  withChannel,
  withDelayMode,
  withGainMode,
  withPair,
  withReference,
} from './crossoverSpec';

const MEMBERS = ['lf', 'mf', 'hf'];

describe('the family and order table', () => {
  it('matches server/solver/filters.py FAMILY_ORDERS', () => {
    expect(FILTER_FAMILY_ORDERS).toEqual({
      lr: [2, 4, 6, 8],
      butterworth: [1, 2, 3, 4, 5, 6, 7, 8],
      bessel: [2, 3, 4],
      linear_phase: [2, 4, 8],
    });
  });

  it('states a slope as 6 dB per octave per order', () => {
    expect(slopeLabel(4)).toBe('24 dB/oct');
    expect(slopeLabel(1)).toBe('6 dB/oct');
  });

  it('moves an order to the nearest one the new family offers', () => {
    expect(nearestOrder('bessel', 8)).toBe(4);
    expect(nearestOrder('lr', 3)).toBe(2);
    expect(nearestOrder('butterworth', 3)).toBe(3);
  });

  it('names a section the way a designer reads one', () => {
    expect(sectionLabel({ family: 'butterworth', order: 3, fcHz: 900 })).toBe('900 Hz BW3');
    expect(sectionLabel({ family: 'lr', order: 4, fcHz: 1_000 })).toBe('1 kHz LR4');
  });
});

describe('expandLegacy', () => {
  it('is the LR4 chain with auto gain and auto delay', () => {
    const spec = expandLegacy(MEMBERS, [100, 1_000]);
    expect(spec.reference).toBe('hf');
    expect(spec.channels.lf).toEqual({
      hp: null,
      lp: { family: 'lr', order: 4, fcHz: 100 },
      gain: { mode: 'auto' },
      delay: { mode: 'auto' },
      invert: null,
    });
    expect(spec.channels.mf.hp).toEqual({ family: 'lr', order: 4, fcHz: 100 });
    expect(spec.channels.hf.lp).toBeNull();
    expect(isSimple(spec)).toBe(true);
  });

  it('turns level match off into a manual 0 dB and alignment off into 0 ms', () => {
    const spec = expandLegacy(MEMBERS, [100, 1_000], false, false);
    expect(spec.channels.mf.gain).toEqual({ mode: 'manual', db: 0 });
    expect(spec.channels.mf.delay).toEqual({ mode: 'manual', ms: 0 });
    expect(sharedGainMode(spec)).toBe('manual');
    expect(sharedDelayMode(spec)).toBe('manual');
  });

  it('round-trips legacy through the v2 wire and back', () => {
    const spec = expandLegacy(MEMBERS, [100, 1_000]);
    const wire = toWire(spec);
    expect(wire).not.toHaveProperty('crossovers_hz');
    expect(wire.channels.mf).toEqual({
      hp: { family: 'lr', order: 4, fc_hz: 100 },
      lp: { family: 'lr', order: 4, fc_hz: 1_000 },
      gain: { mode: 'auto' },
      delay: { mode: 'auto' },
      invert: null,
    });
    expect(parseSpec(JSON.parse(JSON.stringify(spec)))).toEqual(spec);
  });
});

describe('pairsOf', () => {
  it('links a pair only when frequency, family and order all agree', () => {
    const spec = expandLegacy(MEMBERS, [100, 1_000]);
    expect(pairsOf(spec).map((pair) => [pair.key, pair.linked, pair.hz]))
      .toEqual([['lf→mf', true, 100], ['mf→hf', true, 1_000]]);
    const unlinked = withChannel(spec, 'mf', { lp: { family: 'butterworth', order: 3, fcHz: 900 } });
    const [, pair] = pairsOf(unlinked);
    expect(pair.linked).toBe(false);
    expect(pair.hz).toBeNull();
    expect(unlinkedPairNote(pair)).toBe('LP 900 Hz BW3 / HP 1 kHz LR4 — edit in Advanced');
    expect(isSimple(unlinked)).toBe(false);
  });
});

describe('driverXoMinNote', () => {
  it('names the driver and states both frequencies the app\'s own way', () => {
    expect(driverXoMinNote('DE250', 1_600, 1_000)).toBe('DE250 minimum 1.6 kHz — current 1 kHz');
    expect(driverXoMinNote('Acme HD-1', 900, 850)).toBe('Acme HD-1 minimum 900 Hz — current 850 Hz');
  });
});

describe('editing', () => {
  it('sets a pair symmetrically and keeps it linked', () => {
    const spec = withPair(expandLegacy(MEMBERS, [100, 1_000]), 'mf→hf', { hz: 1_400, family: 'butterworth', order: 3 });
    expect(spec.channels.mf.lp).toEqual({ family: 'butterworth', order: 3, fcHz: 1_400 });
    expect(spec.channels.hf.hp).toEqual({ family: 'butterworth', order: 3, fcHz: 1_400 });
    expect(pairsOf(spec)[1].linked).toBe(true);
  });

  it('drops an order the new family does not offer', () => {
    const spec = withPair(expandLegacy(MEMBERS, [100, 1_000]), 'mf→hf', { family: 'bessel' });
    expect(spec.channels.hf.hp).toEqual({ family: 'bessel', order: 4, fcHz: 1_000 });
  });

  it('relinks every pair from the lower channel low-pass', () => {
    const spec = withChannel(expandLegacy(MEMBERS, [100, 1_000]), 'mf', {
      lp: { family: 'butterworth', order: 3, fcHz: 900 },
    });
    const relinked = relinkPairs(spec);
    expect(relinked.channels.hf.hp).toEqual({ family: 'butterworth', order: 3, fcHz: 900 });
    expect(pairsOf(relinked).every((pair) => pair.linked)).toBe(true);
  });

  it('starts a manual gain and delay at the auto value the result reported', () => {
    const spec = expandLegacy(MEMBERS, [100, 1_000]);
    const gains = withGainMode(spec, 'manual', { lf: -1.5, mf: 0, hf: 2 });
    expect(gains.channels.lf.gain).toEqual({ mode: 'manual', db: -1.5 });
    const delays = withDelayMode(gains, 'manual', { lf: 2.62, mf: 0.38, hf: 0 });
    expect(delays.channels.mf.delay).toEqual({ mode: 'manual', ms: 0.38 });
    expect(isSimple(delays)).toBe(true);
    expect(withGainMode(delays, 'auto').channels.lf.gain).toEqual({ mode: 'auto' });
  });

  it('only accepts a reference that is a member', () => {
    const spec = expandLegacy(MEMBERS, [100, 1_000]);
    expect(withReference(spec, 'mf').reference).toBe('mf');
    expect(withReference(spec, 'sub').reference).toBe('hf');
  });
});

describe('fromResult', () => {
  it('rebuilds a v2 payload, keeping auto modes auto and manual values verbatim', () => {
    const spec = fromResult({
      members: MEMBERS,
      reference: 'mf',
      crossovers_hz: [100, null],
      channels: {
        lf: { hp: null, lp: { family: 'lr', order: 4, fc_hz: 100 }, gain_db: -1.5, gain_mode: 'auto', gain_auto_db: -1.5, delay_ms: 2.62, delay_mode: 'auto', delay_auto_ms: 2.62, inverted: false, invert_mode: 'auto' },
        mf: { hp: { family: 'lr', order: 4, fc_hz: 100 }, lp: { family: 'butterworth', order: 3, fc_hz: 900 }, gain_db: 0.5, gain_mode: 'manual', gain_auto_db: 0.2, delay_ms: 0.45, delay_mode: 'manual', delay_auto_ms: 0.38, inverted: true, invert_mode: 'manual' },
        hf: { hp: { family: 'lr', order: 4, fc_hz: 1_100 }, lp: null, gain_db: 0, gain_mode: 'auto', gain_auto_db: 0, delay_ms: 0, delay_mode: 'auto', delay_auto_ms: 0, inverted: false, invert_mode: 'auto' },
      },
    })!;
    expect(spec.reference).toBe('mf');
    expect(spec.channels.lf.gain).toEqual({ mode: 'auto' });
    expect(spec.channels.mf.gain).toEqual({ mode: 'manual', db: 0.5 });
    expect(spec.channels.mf.delay).toEqual({ mode: 'manual', ms: 0.45 });
    expect(spec.channels.mf.invert).toBe(true);
    expect(spec.channels.hf.invert).toBeNull();
    expect(pairsOf(spec)[1].linked).toBe(false);
    expect(isSimple(spec)).toBe(false);
  });

  it('expands a legacy payload that carries no channels', () => {
    const spec = fromResult({
      members: MEMBERS,
      crossovers_hz: [100, 1_000],
      level_match: { enabled: false },
      align: true,
    })!;
    expect(sameSpec(spec, expandLegacy(MEMBERS, [100, 1_000], false, true))).toBe(true);
  });

  it('refuses a payload with fewer than two members or a null legacy crossover', () => {
    expect(fromResult({ members: ['hf'], crossovers_hz: [] })).toBeNull();
    expect(fromResult({ members: MEMBERS, crossovers_hz: [100, null] })).toBeNull();
    expect(fromResult(null)).toBeNull();
  });

  it('reads the resolved auto values a manual field starts from', () => {
    const resolved = resolvedChannels({
      members: MEMBERS,
      channels: { mf: { gain_auto_db: 0.2, delay_auto_ms: 0.38, delay_mode: 'manual', delay_ms: 0.45, inverted: true, invert_mode: 'auto' } },
    });
    expect(resolved.mf.gainAutoDb).toBe(0.2);
    expect(resolved.mf.delayMode).toBe('manual');
    expect(resolved.mf.inverted).toBe(true);
    expect(resolved.mf.invertMode).toBe('auto');
  });
});

describe('parseSpec', () => {
  const stored = () => JSON.parse(JSON.stringify(expandLegacy(MEMBERS, [100, 1_000]))) as {
    reference: string;
    channels: Record<string, { lp: { order: number } | null }>;
  };

  it('refuses an order the family does not offer', () => {
    const broken = stored();
    broken.channels.lf.lp!.order = 3;
    expect(parseSpec(broken)).toBeNull();
  });

  it('refuses a reference outside the members and a mismatched channel map', () => {
    expect(parseSpec({ ...stored(), reference: 'sub' })).toBeNull();
    expect(parseSpec({ ...stored(), channels: { lf: stored().channels.lf } })).toBeNull();
  });

  it('refuses the wire shape, which names its corner fc_hz', () => {
    expect(parseSpec(toWire(expandLegacy(MEMBERS, [100, 1_000])))).toBeNull();
  });
});

describe('parseWire', () => {
  it('round-trips a submitted spec, keeping manual values and explicit polarity', () => {
    const spec = withChannel(
      withPair(expandLegacy(MEMBERS, [100, 1_000]), 'mf→hf', { family: 'bessel', order: 3 }),
      'hf',
      { gain: { mode: 'manual', db: -1.5 }, delay: { mode: 'manual', ms: 0.45 }, invert: true },
    );
    expect(parseWire(toWire(spec))).toEqual(spec);
  });

  it('refuses the stored model shape and a legacy triple, which have no channels', () => {
    expect(parseWire(JSON.parse(JSON.stringify(expandLegacy(MEMBERS, [100, 1_000]))))).toBeNull();
    expect(parseWire({ members: MEMBERS, crossovers_hz: [100, 1_000], level_match: true })).toBeNull();
  });
});
