import { beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import { preferencesStore } from '../prefs/preferences';
import type { ResultPayload } from './types';
import { buildChartRenderPayload, buildFrequencyCsv, buildFullResultsJson, buildImpedanceCsv, buildPolarCsv, buildSummaryText, downloadMeshArtifact, runExportBundle, runExportFormat, runWorkspaceExportBundle, saveMeshArtifactToWorkspace } from './exporters';

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
      jobStem: 'horn_1',
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
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText: vi.fn(),
    })).rejects.toThrow('Choose a drive channel');
    const saveText = vi.fn();
    await runExportFormat('csv', {
      result: wrapped,
      channelId: 'drive-b',
      jobStem: 'horn_1',
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
      jobStem: 'horn_1',
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
      if (path === '/api/design/save') return new Response(JSON.stringify({ text: 'cfg', suggestedFilename: 'horn_1_config.cfg' }), { status: 200 });
      return new Response('geometry', { status: 200, headers: { 'Content-Disposition': `attachment; filename="file.${path.endsWith('step') ? 'step' : 'stl'}"` } });
    });
    const context = { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), designRevision: 4, fetcher, saveText: vi.fn(), saveBlob: vi.fn() };
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
    const context = { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), fetcher: failedFetcher, saveBlob };

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
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => String(input) === '/api/render-directivity'
      ? new Response(JSON.stringify({ image: 'data:image/png;base64,Ag==' }), { status: 200 })
      : new Response(JSON.stringify({ charts: { spl: 'data:image/png;base64,AQ==' } }), { status: 200 }));
    const saveBlob = vi.fn();
    const context = { jobStem: 'horn_1', preferences: { ...preferencesStore.getSnapshot(), chartTheme: 'paper' }, result, fetcher, saveBlob };
    expect(await runExportFormat('png', context)).toEqual(['horn_1_spl.png', 'horn_1_directivity_map.png']);
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/render-directivity', '/api/render-charts']);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).theme).toBe('paper');
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).theme).toBe('paper');
    expect(saveBlob.mock.calls.map(([, filename]) => filename)).toEqual(['horn_1_spl.png', 'horn_1_directivity_map.png']);
    expect(buildChartRenderPayload({ ...result, metadata: { observation: { effective_distance_m: 1.5 }, phase_time_convention: 'e^-iwt' } }, context.preferences)).toMatchObject({ phase_reference_distance_m: 1.5, phase_time_convention: 'e^-iwt', impedance_units: 'Z/(rho*c)' });
    expect(buildChartRenderPayload(result, context.preferences, { result, label: 'reference horn' })).toMatchObject({ reference: { label: 'reference horn', frequencies: [100, 200], spl: [90, null], impedance_normalization: 'rho_c' } });
  });

  it('dispatches one on-axis FRD download per result channel', async () => {
    const wrapped: ResultPayload = {
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': result, 'drive-mf': result },
    };
    const saveText = vi.fn();
    const bundle = await runExportBundle({
      result: wrapped, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), saveText,
    }, ['on_axis_frd']);

    expect(bundle).toEqual({
      files: ['horn_1-drive-hf.frd', 'horn_1-drive-mf.frd'], failures: [],
    });
    expect(saveText.mock.calls.map(([, filename]) => filename)).toEqual([
      'horn_1-drive-hf.frd', 'horn_1-drive-mf.frd',
    ]);
  });

  it('dispatches a manual polar FRD set through the selected Workspace', async () => {
    const polar: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90], phase_degrees: [0] },
      directivity: {
        horizontal: [[[-30, -6], [0, 0], [30, -6]]],
        vertical: [[[-20, -5], [0, 0], [20, -5]]],
      },
    };
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path === '/api/workspace/path') return new Response(JSON.stringify({ selected: true, path: '/chosen' }), { status: 200 });
      return new Response(JSON.stringify({
        directory: '/chosen/horn_1',
        files: Array.from({ length: 6 }, (_, index) => `/chosen/horn_1/${index}.frd`),
      }), { status: 200 });
    });

    const files = await runExportFormat('polar_frd', {
      result: polar, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    });

    expect(files).toHaveLength(6);
    expect(requests.map(({ path }) => path)).toEqual(['/api/workspace/path', '/api/workspace/write-export']);
    const payload = JSON.parse(String(requests[1].init?.body));
    expect(payload.subdirectory).toBe('horn_1');
    expect(payload.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'hor/horn_1 -30.frd', 'hor/horn_1 0.frd', 'hor/horn_1 30.frd',
      'ver/horn_1 -20.frd', 'ver/horn_1 0.frd', 'ver/horn_1 20.frd',
    ]);
  });

  it('writes all polar result channels in one manual Workspace request', async () => {
    const channel: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90] },
      directivity: { horizontal: [[[0, 0]]] },
    };
    const wrapped: ResultPayload = {
      frequencies: [], channel_order: ['drive-hf', 'drive-mf'],
      channels: { 'drive-hf': channel, 'drive-mf': channel },
    };
    let members: Array<{ relative_path: string }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === '/api/workspace/path') return new Response(JSON.stringify({ selected: true }), { status: 200 });
      members = (JSON.parse(String(init?.body)) as { members: Array<{ relative_path: string }> }).members;
      return new Response(JSON.stringify({
        directory: '/chosen/horn_1', files: members.map(({ relative_path }) => `/chosen/horn_1/${relative_path}`),
      }), { status: 200 });
    });

    const bundle = await runExportBundle({
      result: wrapped, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    }, ['polar_frd']);

    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/workspace/path', '/api/workspace/write-export']);
    expect(members.map(({ relative_path }) => relative_path)).toEqual([
      'hor/horn_1-drive-hf 0.frd', 'hor/horn_1-drive-mf 0.frd',
    ]);
    expect(bundle.failures).toEqual([]);
  });

  it('preserves polar Workspace cancellation as a format failure without writing', async () => {
    const polar: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90] },
      directivity: { horizontal: [[[0, 0]]] },
    };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ selected: false }), { status: 200 }));
    const bundle = await runExportBundle({
      result: polar, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    }, ['polar_frd']);

    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/workspace/path', '/api/workspace/select']);
    expect(bundle).toEqual({
      files: [], failures: [{ format: 'polar_frd', reason: 'Workspace selection was cancelled. No files were written.' }],
    });
  });

  it('stages automatic polar FRDs with their plane directories in the common Workspace write', async () => {
    const polar: ResultPayload = {
      frequencies: [1000], spl_on_axis: { frequencies: [1000], spl: [90] },
      directivity: { horizontal: [[[-30, -6], [0, 0], [30, -6]]] },
    };
    let writePayload: { members: Array<{ relative_path: string }> } | undefined;
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toBe('/api/workspace/write-export');
      writePayload = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        directory: '/output/horn_1',
        files: writePayload!.members.map(({ relative_path }) => `/output/horn_1/${relative_path}`),
      }), { status: 200 });
    });

    const bundle = await runWorkspaceExportBundle({
      result: polar, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), fetcher,
    }, ['polar_frd']);

    expect(bundle.failures).toEqual([]);
    expect(writePayload?.members.map(({ relative_path }) => relative_path)).toEqual([
      'hor/horn_1 -30.frd', 'hor/horn_1 0.frd', 'hor/horn_1 30.frd',
    ]);
  });

  it('pins distinct config and summary suffixes, including the config fallback', async () => {
    const saveText = vi.fn();
    const preferences = preferencesStore.getSnapshot();
    const configFetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ text: 'cfg' }), { status: 200 }));
    expect(await runExportFormat('mwg_config', {
      jobStem: '123_asro68', preferences, design: designForFamily('OSSE'), fetcher: configFetcher, saveText,
    })).toEqual(['123_asro68_config.cfg']);
    expect(JSON.parse(String(configFetcher.mock.calls[0][1]?.body)).filename).toBe('123_asro68_config.cfg');

    expect(await runExportFormat('txt', {
      jobStem: '123_asro68', preferences, result, saveText,
    })).toEqual(['123_asro68_summary.txt']);
  });

  it('renames only the stored mesh download while preserving its bytes', async () => {
    const saveBlob = vi.fn();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('stored mesh bytes', {
      status: 200,
      headers: { 'Content-Disposition': 'attachment; filename="artifact-uuid.msh"' },
    }));
    const filename = await downloadMeshArtifact({
      id: 'artifact-uuid', run_number: 123, label: 'asro68', config_summary: {},
    }, fetcher, saveBlob);

    expect(fetcher).toHaveBeenCalledWith('/api/mesh-artifact/artifact-uuid');
    expect(filename).toBe('123_asro68.msh');
    expect(saveBlob.mock.calls[0][1]).toBe('123_asro68.msh');
    expect(await (saveBlob.mock.calls[0][0] as Blob).text()).toBe('stored mesh bytes');
  });

  it('writes automatic text and binary exports to the Workspace without browser downloads', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      requests.push({ path, init });
      if (path === '/api/render-charts') {
        return new Response(JSON.stringify({ charts: { spl: 'data:image/png;base64,AQID' } }), { status: 200 });
      }
      if (path === '/api/render-directivity') {
        return new Response(JSON.stringify({ image: 'data:image/png;base64,BAUG' }), { status: 200 });
      }
      if (path === '/api/workspace/write-export') {
        return new Response(JSON.stringify({
          directory: 'C:/output/horn_1',
          files: ['C:/output/horn_1/horn_1.csv', 'C:/output/horn_1/horn_1_spl.png', 'C:/output/horn_1/horn_1_directivity_map.png'],
        }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });

    const bundle = await runWorkspaceExportBundle({
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
    }, ['csv', 'png']);

    expect(bundle).toEqual({
      files: ['C:/output/horn_1/horn_1.csv', 'C:/output/horn_1/horn_1_spl.png', 'C:/output/horn_1/horn_1_directivity_map.png'],
      failures: [],
    });
    const write = requests.find(({ path }) => path === '/api/workspace/write-export')!;
    const payload = JSON.parse(String(write.init?.body));
    expect(payload).toMatchObject({ subdirectory: 'horn_1', existing: 'merge_identical' });
    expect(payload.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'horn_1.csv', 'horn_1_spl.png', 'horn_1_directivity_map.png',
    ]);
    expect(payload.members[1].content_base64).toBe('AQID');
    expect(payload.members[2].content_base64).toBe('BAUG');
  });

  it('auto-saves the mesh into the same per-run Workspace directory', async () => {
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      if (String(input) === '/api/mesh-artifact/artifact-uuid') return new Response('mesh bytes', { status: 200 });
      return new Response(JSON.stringify({
        directory: 'C:/output/123_asro68',
        files: ['C:/output/123_asro68/123_asro68.msh'],
      }), { status: 200 });
    });

    const saved = await saveMeshArtifactToWorkspace({
      id: 'artifact-uuid', run_number: 123, label: 'asro68', config_summary: {},
    }, fetcher);

    expect(saved).toBe('C:/output/123_asro68/123_asro68.msh');
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/mesh-artifact/artifact-uuid', '/api/workspace/write-export',
    ]);
  });
});
