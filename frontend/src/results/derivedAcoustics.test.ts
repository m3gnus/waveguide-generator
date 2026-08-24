import { describe, expect, it } from 'vitest';
import {
  buildDerivedAcoustics,
  buildDerivedAcousticsCsv,
  buildDerivedAcousticsJson,
} from './derivedAcoustics';
import type { ResultPayload } from './types';

const result: ResultPayload = {
  frequencies: [100, 200, 400],
  spl_on_axis: {
    frequencies: [100, 200, 400],
    spl: [90, 92, 94],
    // exp(+ikr): +dphi/domega is positive delay. The common 1 m / 1000 m/s
    // propagation slope is removed, leaving the 1 ms excess slope below.
    phase_degrees: [72, 144, 288],
  },
  di: { frequencies: [100, 200, 400], di: [3, 4, 5] },
  beam_shape: {
    frequencies: [100, 200, 400],
    horizontal_beamwidth_deg: [100, 80, 60],
    vertical_beamwidth_deg: [90, 70, 50],
    spherical_di_db: [3, 4, 5],
    shape_exponent: [2, 3, 4],
    fit_residual_percent: [1, 2, 3],
  },
  metadata: {
    phase_time_convention: 'exp(+ikr)',
    observation: {
      effective_distance_m: 1,
      sound_speed_m_per_s: 1000,
    },
    directivity_index: { definition: 'test full-sphere definition' },
    radiated_power: {
      surface_w: [1, 2, 4],
      sphere_w: [1.01, 1.9, 4.5],
      sphere_coverage_sr: 12.566370614359172,
      definition: 'surface and sphere test definition',
      agreement_db: [0.0432137378, -0.2227639471, 0.5115252245],
    },
  },
};

describe('derived acoustics export', () => {
  it('exports power response, beam metrics, and de-embedded group delay', () => {
    const payload = buildDerivedAcoustics(result);

    expect(payload.rows).toHaveLength(3);
    expect(payload.rows[1]).toMatchObject({
      frequency_hz: 200,
      on_axis_spl_db: 92,
      directivity_index_db: 4,
      power_response_db_spl_avg: 88,
      radiated_power_surface_w: 2,
      radiated_power_sphere_w: 1.9,
      power_agreement_db: -0.2227639471,
      horizontal_beamwidth_deg: 80,
      vertical_beamwidth_deg: 70,
      spherical_di_db: 4,
      beam_shape_exponent: 3,
      beam_fit_residual_percent: 2,
    });
    expect(payload.rows[1].group_delay_ms).toBeCloseTo(1, 8);
    expect(payload.metadata).toMatchObject({
      directivity_index_definition: 'test full-sphere definition',
      power_response_formula: 'on_axis_spl_db - directivity_index_db',
      power_agreement_formula: '10 * log10(radiated_power_sphere_w / radiated_power_surface_w)',
      radiated_power_definition: 'surface and sphere test definition',
      radiated_power_sphere_coverage_sr: 12.566370614359172,
      group_delay_available: true,
      observation_distance_m: 1,
      sound_speed_m_per_s: 1000,
    });
  });

  it('keeps the established power-response export fields unchanged while appending the native power check', () => {
    const payload = buildDerivedAcoustics(result);
    expect(payload.rows.map(({ frequency_hz, power_response_db_spl_avg }) => ({
      frequency_hz,
      power_response_db_spl_avg,
    }))).toEqual([
      { frequency_hz: 100, power_response_db_spl_avg: 87 },
      { frequency_hz: 200, power_response_db_spl_avg: 88 },
      { frequency_hz: 400, power_response_db_spl_avg: 89 },
    ]);
    expect(payload.metadata.power_response_formula).toBe('on_axis_spl_db - directivity_index_db');
    expect(buildDerivedAcousticsCsv(result).split('\n')[0].split(',').slice(0, 4)).toEqual([
      'frequency_hz',
      'on_axis_spl_db',
      'directivity_index_db',
      'power_response_db_spl_avg',
    ]);
  });

  it('keeps missing quantities empty in CSV and explicit in JSON', () => {
    const sparse: ResultPayload = {
      frequencies: [100, 200],
      spl_on_axis: { spl: [90, null] },
      di: { frequencies: [150], di: [4] },
    };

    expect(buildDerivedAcousticsCsv(sparse)).toContain('100,90,,,,,,,,');
    const json = JSON.parse(buildDerivedAcousticsJson(sparse));
    expect(json.rows.map((row: { frequency_hz: number }) => row.frequency_hz))
      .toEqual([100, 150, 200]);
    expect(json.metadata.group_delay_available).toBe(false);
    expect(json.metadata.group_delay_unavailable_reason).toContain('sufficiently resolved');
  });
});
