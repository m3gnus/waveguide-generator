import { beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import { preferencesStore } from '../prefs/preferences';
import type { ResultPayload } from './types';
import { buildChartRenderPayload, buildFrequencyCsv, buildFullResultsJson, buildImpedanceCsv, buildPolarCsv, buildSummaryText, runExportFormat } from './exporters';

const result: ResultPayload = {
  frequencies: [100, 200],
  spl_on_axis: { frequencies: [100, 200], spl: [90, null], phase_degrees: [0, 10] },
  di: { frequencies: [100, 200], di: [3, 4] },
  impedance: { frequencies: [100, 200], real: [1, null], imaginary: [0, -.5] },
  directivity: { horizontal: [[[-90, -12], [0, 0]], [[-90, [0.1, 0]], [0, [1, 0]]]] },
};

describe('result exporters', () => {
  beforeEach(() => { preferencesStore.resetForTests(); });
  it('builds the five client-side audit formats with stable missing-value behavior', () => {
    const preferences = preferencesStore.getSnapshot();
    expect(buildFrequencyCsv(result, preferences)).toContain('200,,4,,-0.5');
    expect(JSON.parse(buildFullResultsJson(result, preferences, new Date('2026-08-04T10:00:00Z'))).results).toEqual(result);
    expect(buildSummaryText(result, preferences, new Date('2026-08-04T10:00:00Z'))).toContain('Average SPL: 90.00 dB');
    expect(buildPolarCsv(result)).toContain('200,horizontal,-90,-20');
    expect(buildImpedanceCsv(result)).toContain('200,,-0.5');
  });
  it('posts config and geometry selectors to their existing endpoints', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/design/save') return new Response(JSON.stringify({ text: 'cfg', suggestedFilename: 'horn_1.txt' }), { status: 200 });
      return new Response(new Blob(['geometry']), { status: 200, headers: { 'Content-Disposition': `attachment; filename="file.${path.endsWith('step') ? 'step' : 'stl'}"` } });
    });
    const context = { preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), designRevision: 4, fetcher, saveText: vi.fn(), saveBlob: vi.fn() };
    await runExportFormat('mwg_config', context);
    await runExportFormat('step', context);
    await runExportFormat('stl', context);
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/design/save', '/api/export/step', '/api/export/stl']);
  });
  it('stages both Fusion CSV responses before downloading either file', async () => {
    const saveBlob = vi.fn();
    const failedFetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path.endsWith('kind=slices')) return new Response('slices failed', { status: 500 });
      return new Response(new Blob(['profiles']), { status: 200 });
    });
    const context = { preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), fetcher: failedFetcher, saveBlob };

    await expect(runExportFormat('fusion_csv', context)).rejects.toThrow('500');
    expect(saveBlob).not.toHaveBeenCalled();

    const successfulFetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const kind = String(input).endsWith('kind=slices') ? 'slices' : 'profiles';
      return new Response(new Blob([kind]), { status: 200, headers: { 'Content-Disposition': `attachment; filename="horn_1_${kind}.csv"` } });
    });
    const files = await runExportFormat('fusion_csv', { ...context, fetcher: successfulFetcher });
    expect(files).toEqual(['horn_1_profiles.csv', 'horn_1_slices.csv']);
    expect(saveBlob).toHaveBeenCalledTimes(2);
  });
  it('sends the selected theme to the PNG renderer endpoint', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ charts: { spl: 'data:image/png;base64,AQ==' } }), { status: 200 }));
    const context = { preferences: { ...preferencesStore.getSnapshot(), chartTheme: 'paper' }, result, fetcher, saveBlob: vi.fn() };
    expect(await runExportFormat('png', context)).toEqual(['horn_1_spl.png']);
    expect(String(fetcher.mock.calls[0][0])).toBe('/api/render-charts');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).theme).toBe('paper');
    expect(buildChartRenderPayload({ ...result, metadata: { observation: { effective_distance_m: 1.5 }, phase_time_convention: 'e^-iwt' } }, context.preferences)).toMatchObject({ phase_reference_distance_m: 1.5, phase_time_convention: 'e^-iwt', impedance_units: 'Z/(rho*c)' });
    expect(buildChartRenderPayload(result, context.preferences, { result, label: 'reference horn' })).toMatchObject({ reference: { label: 'reference horn', frequencies: [100, 200], spl: [90, null], impedance_normalization: 'rho_c' } });
  });
});
