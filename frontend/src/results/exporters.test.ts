import { beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import { preferencesStore } from '../prefs/preferences';
import type { ResultPayload } from './types';
import { buildChartRenderPayload, buildFrequencyCsv, buildFullResultsJson, buildImpedanceCsv, buildPolarCsv, buildSummaryText, runExportBundle, runExportFormat } from './exporters';

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
  it('joins DI and impedance onto their own frequencies instead of the SPL row index', () => {
    // DI and impedance may be solved on their own grids. Zipping them against the SPL
    // row index would label 175 Hz DI as 100 Hz and 150 Hz impedance as 200 Hz, with
    // nothing in the file revealing it.
    const offGrid: ResultPayload = {
      frequencies: [100, 200],
      spl_on_axis: { frequencies: [100, 200], spl: [90, 91], phase_degrees: [0, 10] },
      di: { frequencies: [100, 175], di: [3, 4] },
      impedance: { frequencies: [150, 200, 250], real: [1, 2, 3], imaginary: [0, -0.5, -0.25] },
    };
    const preferences = preferencesStore.getSnapshot();

    expect(buildFrequencyCsv(offGrid, preferences)).toBe([
      'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))',
      '100,90,3,,',
      '150,,,1,0',
      '175,,4,,',
      '200,91,,2,-0.5',
      '250,,,3,-0.25',
      '',
    ].join('\n'));

    const summary = buildSummaryText(offGrid, preferences, new Date('2026-08-10T10:00:00Z'));
    expect(summary).toContain('Frequency range: 100 - 250 Hz');
    expect(summary).toContain('Number of points: 5');
    expect(summary).toContain('175.00  n/a  4.00  n/a  n/a');
    expect(summary).toContain('200.00  91.00  n/a  2.00  -0.50');
  });
  it('leaves the frozen CSV schema untouched when every series shares one grid', () => {
    // The join must be a no-op for the shape the solver emits today, so an existing
    // consumer of the frozen schema sees byte-identical output.
    expect(buildFrequencyCsv(result, preferencesStore.getSnapshot())).toBe([
      'Frequency (Hz),SPL (dB),DI (dB),Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))',
      '100,90,3,1,0',
      '200,,4,,-0.5',
      '',
    ].join('\n'));
  });
  it('builds only the selected client-side format', async () => {
    const toJSON = vi.fn(() => { throw new Error('unselected JSON builder ran'); });
    const saveText = vi.fn();
    const csvResult = { ...result, toJSON };

    await expect(runExportFormat('csv', {
      result: csvResult,
      preferences: preferencesStore.getSnapshot(),
      saveText,
    })).resolves.toEqual(['horn_1.csv']);
    expect(toJSON).not.toHaveBeenCalled();
    expect(saveText).toHaveBeenCalledOnce();
  });
  it('exports only the selected CAD drive channel and refuses an ambiguous wrapper', async () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-a', 'drive-b'],
      channels: { 'drive-a': result, 'drive-b': { ...result, spl_on_axis: { frequencies: [100, 200], spl: [70, 71] } } },
    };
    await expect(runExportFormat('csv', {
      result: wrapped,
      preferences: preferencesStore.getSnapshot(),
      saveText: vi.fn(),
    })).rejects.toThrow('Choose a drive channel');
    const saveText = vi.fn();
    await runExportFormat('csv', {
      result: wrapped,
      channelId: 'drive-b',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    });
    expect(saveText.mock.calls[0][0]).toContain('100,70');
    expect(saveText.mock.calls[0][0]).not.toContain('100,90');
  });
  it('bundles every CAD drive channel with per-channel names and writes design formats once', async () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-hf'],
      channels: {
        'drive-mf': { ...result, spl_on_axis: { frequencies: [100, 200], spl: [70, 71] } },
        'drive-hf': result,
      },
    };
    const saveText = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async () => new Response('step', {
      status: 200,
      headers: { 'Content-Disposition': 'attachment; filename="horn_1.step"' },
    }));
    const bundle = await runExportBundle({
      result: wrapped,
      design: designForFamily('OSSE'),
      preferences: preferencesStore.getSnapshot(),
      saveText,
      saveBlob: vi.fn(),
      fetcher,
    }, ['csv', 'step']);

    expect(bundle).toEqual({
      files: ['horn_1-drive-hf.csv', 'horn_1-drive-mf.csv', 'horn_1.step'],
      failures: [],
    });
    expect(saveText.mock.calls.map(([, filename]) => filename)).toEqual([
      'horn_1-drive-hf.csv', 'horn_1-drive-mf.csv',
    ]);
    expect(saveText.mock.calls[0][0]).toContain('100,90');
    expect(saveText.mock.calls[1][0]).toContain('100,70');
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('posts config and geometry selectors to their existing endpoints', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/design/save') return new Response(JSON.stringify({ text: 'cfg', suggestedFilename: 'horn_1.txt' }), { status: 200 });
      return new Response('geometry', { status: 200, headers: { 'Content-Disposition': `attachment; filename="file.${path.endsWith('step') ? 'step' : 'stl'}"` } });
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
      return new Response('profiles', { status: 200 });
    });
    const context = { preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), fetcher: failedFetcher, saveBlob };

    await expect(runExportFormat('fusion_csv', context)).rejects.toThrow('500');
    expect(saveBlob).not.toHaveBeenCalled();

    const successfulFetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const kind = String(input).endsWith('kind=slices') ? 'slices' : 'profiles';
      return new Response(kind, { status: 200, headers: { 'Content-Disposition': `attachment; filename="horn_1_${kind}.csv"` } });
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
