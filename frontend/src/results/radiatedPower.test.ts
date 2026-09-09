import { describe, expect, it } from 'vitest';
import { powerAgreementHealth, powerCheckMessage, radiatedPowerMetadata } from './radiatedPower';
import type { ResultPayload } from './types';

function result(agreement: Array<number | null>, frequencies = [100, 1_000, 10_000]): ResultPayload {
  return {
    frequencies,
    metadata: { radiated_power: {
      surface_w: frequencies.map(() => 1),
      sphere_w: frequencies.map(() => 1.01),
      sphere_coverage_sr: 12.566370614359172,
      definition: 'test power definition',
      agreement_db: agreement,
    } },
  };
}

describe('radiated power cross-check', () => {
  it('is absent when the optional native metadata block is absent', () => {
    const missing = { frequencies: [100] } as ResultPayload;
    expect(radiatedPowerMetadata(missing)).toBeNull();
    expect(powerAgreementHealth(missing)).toBeNull();
  });

  it('warns only above 0.5 dB and only inside the joined validity band', () => {
    const channel = {
      ...result([0.1, -0.6, 2.4]),
      metadata: {
        ...result([0.1, -0.6, 2.4]).metadata,
        source_ids: ['driver'],
      },
    };
    const wrapper: ResultPayload = {
      frequencies: [],
      channels: { drive: channel },
      metadata: { per_source_frequency_validity: {
        driver: { effective_max_valid_frequency_hz: 2_000 },
      } },
    };
    const health = powerAgreementHealth(channel, wrapper)!;
    expect(health.maxDifferenceDb).toBe(0.6);
    expect(health.frequencyHz).toBe(1_000);
    expect(health.checkedCount).toBe(2);
    expect(powerAgreementHealth(result([0.1, -0.5, 0.2]))).toBeNull();
    expect(powerAgreementHealth(result([0.1, -0.50001, 0.2]))?.maxDifferenceDb).toBe(0.50001);
  });

  it('names the worst frequency and keeps the direction of the mismatch', () => {
    const positive = powerAgreementHealth(result([0.1, 0.9, 0.2]))!;
    expect(positive.signedDifferenceDb).toBe(0.9);
    expect(powerCheckMessage(positive).label).toBe('Power check: +0.90 dB at 1.00 kHz');
    expect(powerCheckMessage(positive).title).toContain('sphere integral reads higher');

    const negative = powerAgreementHealth(result([0.1, -0.9, 0.2]))!;
    expect(negative.signedDifferenceDb).toBe(-0.9);
    expect(powerCheckMessage(negative).label).toBe('Power check: −0.90 dB at 1.00 kHz');
    expect(powerCheckMessage(negative).title).toContain('driven-surface flux reads higher');
  });

  it('reports an isolated mismatch without claiming to know its cause', () => {
    const frequencies = [50, 1_000, 14_747, 16_324, 18_069, 20_000];
    const health = powerAgreementHealth(
      result([-0.081, -0.158, 0.037, 1.346, -0.217, -0.145], frequencies),
    )!;
    expect(health).toMatchObject({
      spread: 'single',
      affectedCount: 1,
      checkedCount: 6,
      affectedSpanHz: [16_324, 16_324],
    });
    expect(health.baselineDb).toBeCloseTo(0.217, 6);

    const message = powerCheckMessage(health);
    expect(message.label).toBe('Power check: +1.35 dB at 16.3 kHz');
    expect(message.title).toContain('remaining checked frequencies agree within 0.22 dB');
    expect(message.title).toContain('re-solve a denser sweep');
    expect(message.title).toContain('if the mismatch persists');
  });

  it('reports the affected span when only a few sweep points disagree', () => {
    const frequencies = Array.from({ length: 60 }, (_, index) => 50 * 1.107 ** index);
    const agreement = frequencies.map(() => -0.08);
    frequencies.splice(50, 3, 15_900, 16_100, 16_300);
    agreement.splice(50, 3, 0.62, 1.08, 1.66);
    const health = powerAgreementHealth(result(agreement, frequencies))!;
    expect(health.spread).toBe('few');
    expect(health.affectedSpanHz).toEqual([15_900, 16_300]);
    expect(powerCheckMessage(health).title).toContain('spanning 15.9 kHz–16.3 kHz');
  });

  it('does not call one failing sample isolated when it is the entire check', () => {
    const health = powerAgreementHealth(result([0.9], [620]))!;
    expect(health.spread).toBe('broad');
    expect(health.baselineDb).toBeNull();
    expect(powerCheckMessage(health).label).toBe('Power check: +0.90 dB at 620 Hz');
    expect(powerCheckMessage(health).title).toContain('1 of 1 checked frequencies');
  });

  it('directs a sweep-wide mismatch to numerical and discretisation checks', () => {
    const health = powerAgreementHealth(
      result([-0.7, -0.9, -1.1, -1.4, -1.9, -2.6], [100, 200, 400, 800, 1_600, 3_200]),
    )!;
    expect(health.spread).toBe('broad');
    expect(health.baselineDb).toBeNull();
    const message = powerCheckMessage(health);
    expect(message.label).toBe('Power check: −2.60 dB at 3.20 kHz');
    expect(message.title).toContain('6 of 6 checked frequencies');
    expect(message.title).toContain('mesh resolution');
    expect(message.title).not.toContain('remaining checked frequencies');
  });
});
