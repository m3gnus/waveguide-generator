import { describe, expect, it } from 'vitest';
import { powerAgreementHealth, radiatedPowerMetadata } from './radiatedPower';
import type { ResultPayload } from './types';

function result(agreement: Array<number | null>): ResultPayload {
  return {
    frequencies: [100, 1_000, 10_000],
    metadata: { radiated_power: {
      surface_w: [1, 2, 3],
      sphere_w: [1.01, 2.2, 4],
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
    expect(powerAgreementHealth(channel, wrapper)).toEqual({
      maxDifferenceDb: 0.6,
      frequencyHz: 1_000,
    });
    expect(powerAgreementHealth(result([0.1, -0.5, 0.2]))).toBeNull();
    expect(powerAgreementHealth(result([0.1, -0.50001, 0.2]))?.maxDifferenceDb).toBe(0.50001);
  });
});
