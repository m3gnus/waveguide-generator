import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { preferencesStore, type Preferences } from '../prefs/preferences';
import type { ResultPayload } from './types';
import { buildFrequencyCsv, buildFullResultsJson, buildImpedanceCsv, buildPolarCsv, buildSummaryText } from './exporters';
import { buildOnAxisFrd, buildPolarFrdSet } from './frd';
import { buildDerivedAcousticsCsv, buildDerivedAcousticsJson } from './derivedAcoustics';
import { buildRunReportHtml } from './runReport';
import { buildZma, buildVituixCadProjectFiles } from './vituixcad';

const input: ResultPayload = {
  frequencies: [100, 200, 400],
  spl_on_axis: { frequencies: [100, 200, 400], spl: [90, 91, 88], phase_degrees: [0, 20, 40] },
  di: { frequencies: [100, 200, 400], di: [3, 4, 5] },
  impedance: { frequencies: [100, 200, 400], real: [3, 4, 5], imaginary: [1, 2, 3] },
  directivity: { horizontal: [[[0, 0], [30, -3]], [[0, 0], [30, -6]], [[0, 0], [30, -9]]] },
  directivity_phase: { horizontal: [[[0, 0], [30, 10]], [[0, 20], [30, 30]], [[0, 40], [30, 50]]] },
  metadata: {
    impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance',
    impedance_phase_convention: 'engineering_exp_plus_jwt', phase_time_convention: 'exp(+ikr)',
    observation: { effective_distance_m: 2, sound_speed_m_per_s: 343 },
    directivity: { normalization_angle_degrees: 0 },
  },
};
const now = new Date('2026-08-04T10:00:00Z');
const fixturePath = 'src/results/fixtures/sampling-legacy-exports.json';

function outputs(result: ResultPayload, preferences: Preferences) {
  return {
    csv: buildFrequencyCsv(result, preferences),
    json: buildFullResultsJson(result, preferences, now),
    txt: buildSummaryText(result, preferences, now),
    polar_csv: buildPolarCsv(result),
    impedance_csv: buildImpedanceCsv(result),
    on_axis_frd: buildOnAxisFrd(result, preferences),
    polar_frd: buildPolarFrdSet(result, preferences, 'legacy').map(({ text }) => text).join('\n'),
    zma: buildZma(result),
    derived_csv: buildDerivedAcousticsCsv(result),
    derived_json: buildDerivedAcousticsJson(result),
    html: buildRunReportHtml(result, { title: 'Legacy', generatedAt: now }),
    vxp: buildVituixCadProjectFiles(result, preferences, 'legacy').map(({ text }) => text).join('\n'),
  };
}

describe('adaptive numeric export disclosure and base bytes', () => {
  it('preserves all unflagged numeric exports byte for byte against base 44226173', () => {
    const preferences = { ...preferencesStore.getSnapshot(), smoothing: 'none' as const };
    const smoothed = { ...preferences, smoothing: '1/6' as Preferences['smoothing'] };
    const fixture = JSON.parse(new TextDecoder().decode(readFileSync(fixturePath)));
    expect(fixture.base).toBe('44226173');
    expect(outputs(fixture.input, preferences)).toEqual(fixture.outputs);
    expect(outputs(fixture.input, smoothed)).toEqual(fixture.smoothed);
  });

  it('warns and includes counts and solved frequencies in every adaptive numeric export', () => {
    const result: ResultPayload = { ...input, frequency_status: ['solved', 'interpolated', 'solved'] };
    const exported = outputs(result, preferencesStore.getSnapshot());
    for (const [format, content] of Object.entries(exported)) {
      expect(content, format).toContain('narrow resonances can be missed');
      if (['json', 'derived_json'].includes(format)) {
        expect(content, format).toContain('"solved_count": 2');
        expect(content, format).toContain('"interpolated_count": 1');
        expect(content, format).toContain('"solved_frequencies_hz"');
      } else {
        expect(content, format).toContain('2 solved, 1 interpolated');
        expect(content, format).toContain('Solved requested frequencies (Hz): 100, 400');
      }
    }
  });
});
