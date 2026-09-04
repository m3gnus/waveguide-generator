import { beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import { preferencesStore } from '../prefs/preferences';
import type { ResultPayload } from './types';
import type { RadiationImpedancePresentation } from '../api/results';
import { archiveRunToWorkspace, buildChartRenderPayload, buildFrequencyCsv, buildFullResultsJson, buildImpedanceCsv, buildPolarCsv, buildRadiationImpedanceCsv, buildSummaryText, buildVacs, downloadMeshArtifact, runExportBundle, runExportFormat, runWorkspaceExportBundle, saveMeshArtifactToWorkspace, writeWorkspaceFiles } from './exporters';
import type { CadIdentityProvenance, JobItem } from '../api/jobsSocket';

const cadIdentity: CadIdentityProvenance = {
  schema_version: 1,
  ingest_id: 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C',
  selected_instance_id: 'instance-a',
  solver_anchor_instance_id: 'instance-a',
  instances: [{
    instance_id: 'instance-a', design_id: 'wgd-shared', body_object_ids: ['body-a'],
    assembly_from_link: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    source_ids: ['source-a'], default_drive_channel_ids: ['left'],
  }],
  drive_channels: [{ drive_channel_id: 'left', source_ids: ['source-a'], instance_ids: ['instance-a'] }],
};

const result: ResultPayload = {
  frequencies: [100, 200],
  spl_on_axis: { frequencies: [100, 200], spl: [90, null], phase_degrees: [0, 10] },
  di: { frequencies: [100, 200], di: [3, 4] },
  impedance: { frequencies: [100, 200], real: [1, null], imaginary: [0, -.5] },
  directivity: { horizontal: [[[-90, -12], [0, 0]], [[-90, [0.1, 0]], [0, [1, 0]]]] },
};

const finalResult = {
  ...result,
  result_kind: 'parametric' as const,
  result_contract_version: 1 as const,
  client_request_id: null,
  client_metadata: {},
  provenance: {
    schema_version: 1,
    wg_version: 'test',
    dependency_shas: {},
    request_sha256: 'a'.repeat(64),
    geometry_sha256: 'a'.repeat(64),
    solve_options_sha256: 'a'.repeat(64),
    request_identity: 'execution',
    execution_request_sha256: 'a'.repeat(64),
    execution_geometry_sha256: 'a'.repeat(64),
    execution_solve_options_sha256: 'a'.repeat(64),
    effective_request_sha256: 'a'.repeat(64),
    effective_geometry_sha256: 'a'.repeat(64),
    effective_solve_options_sha256: 'a'.repeat(64),
    resolved_engine: 'test',
  },
  metadata: {},
};

const radiation: RadiationImpedancePresentation = {
  schema_version: 1,
  quantity: 'average_aperture_pressure_per_volume_velocity',
  units: 'Pa*s/m^3',
  phase_time_convention: 'engineering_exp_plus_jwt',
  frequencies_hz: [100],
  apertures: [
    { name: 'PORT,L', area_m2: 0.01, tag: 31 },
    { name: 'PORT_R', area_m2: 0.02, tag: 32 },
  ],
  engineering_matrix: {
    real: [[[1, 2], [3, 4]]],
    imaginary: [[[5, 6], [7, 8]]],
  },
  in_phase_termination: {
    aperture_names: ['PORT,L', 'PORT_R'],
    real: [[3, 7]],
    imaginary: [[11, 15]],
  },
};

function workspacePayload(init?: RequestInit) {
  expect(init?.body).toBeInstanceOf(FormData);
  const form = init?.body as FormData;
  const paths = form.getAll('relative_path').map(String);
  const files = form.getAll('file');
  return {
    subdirectory: String(form.get('subdirectory')),
    existing: String(form.get('existing')),
    members: paths.map((relative_path, index) => ({
      relative_path,
      blob: files[index] as Blob,
    })),
  };
}

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

  describe('polar frame/frequency alignment', () => {
    it('refuses fewer directivity frames than frequency labels', () => {
      const misaligned: ResultPayload = {
        frequencies: [100, 200, 300],
        directivity: { horizontal: [[[0, -1]], [[0, -2]]] },
      };
      expect(() => buildPolarCsv(misaligned)).toThrow(/2 frame\(s\).*3 point\(s\)/);
    });

    it('refuses a non-finite angle rather than emitting a NaN label', () => {
      const badAngle: ResultPayload = {
        frequencies: [100],
        directivity: { horizontal: [[[Number.NaN, -1]]] },
      };
      expect(() => buildPolarCsv(badAngle)).toThrow(/non-finite angle/);
    });

    it('exports normally when every plane is aligned with the frequency axis', () => {
      const aligned: ResultPayload = {
        frequencies: [100, 200],
        directivity: {
          horizontal: [[[0, -1], [30, -4]], [[0, -2], [30, -5]]],
          vertical: [[[0, -1.5]], [[0, -2.5]]],
        },
      };
      const csv = buildPolarCsv(aligned);
      expect(csv).toContain('100,horizontal,0,-1');
      expect(csv).toContain('200,horizontal,30,-5');
      expect(csv).toContain('200,vertical,0,-2.5');
    });
  });

  describe('buildVacs', () => {
    it('never claims Data_Format=Complex for the polar pressure block', () => {
      // A genuinely complex directivity sample (real, imaginary pressure): the
      // previous implementation wrote its magnitude next to a fabricated zero
      // under a Data_Format=Complex header, which is indistinguishable from a
      // real zero-phase measurement to any reader of this file.
      const withComplexSamples: ResultPayload = {
        frequencies: [100, 200],
        directivity: { horizontal: [[[0, [3, 4]]], [[0, [0, -2]]]] },
      };
      const text = buildVacs(withComplexSamples, new Date('2026-09-04T00:00:00Z'));
      expect(text).not.toContain('Data_Format=Complex\nData_LevelType=Peak');
      expect(text).toContain('Data_Format=Real');
      expect(text).toContain('Data_Legend="Polar normalized magnitude (no phase), horizontal, 0 deg"');
      // magnitude of (3,4) is 5; magnitude of (0,-2) is 2. No trailing zero column.
      expect(text).toMatch(/^100 {3}5(\.\d+)?$/m);
      expect(text).toMatch(/^200 {3}2(\.\d+)?$/m);
    });

    it('still exports impedance as genuine complex data', () => {
      const impedanceOnly: ResultPayload = {
        frequencies: [100, 200],
        impedance: { frequencies: [100, 200], real: [1, 2], imaginary: [0.5, -0.5] },
      };
      const text = buildVacs(impedanceOnly, new Date('2026-09-04T00:00:00Z'));
      expect(text).toContain('Data_Format=Complex\nData_LevelType=Impedance10');
      expect(text).toContain('100   1 0.5');
      expect(text).toContain('200   2 -0.5');
    });

    it('refuses a directivity frame count that disagrees with the frequency axis', () => {
      const misaligned: ResultPayload = {
        frequencies: [100, 200, 300],
        directivity: { horizontal: [[[0, -1]], [[0, -2]]] },
      };
      expect(() => buildVacs(misaligned)).toThrow(/2 frame\(s\).*3 point\(s\)/);
    });

    it('uses the polar grid even when SPL has a different equally sized grid', () => {
      const independent: ResultPayload = {
        frequencies: [100, 200],
        spl_on_axis: { frequencies: [110, 220], spl: [80, 81] },
        directivity: { horizontal: [[[0, 0]], [[0, 0]]] },
      };
      expect(buildPolarCsv(independent)).toContain('200,horizontal,0,0');
      expect(buildVacs(independent)).toMatch(/^200 {3}1$/m);
      expect(buildVacs(independent)).not.toMatch(/^220 /m);
    });

    it('refuses an extra frame rather than repeating the last frequency', () => {
      const extra: ResultPayload = { frequencies: [100], directivity: { horizontal: [[[0, 0]], [[0, -3]]] } };
      expect(() => buildPolarCsv(extra)).toThrow(/2 frame.*1 point/);
      expect(() => buildVacs(extra)).toThrow(/2 frame.*1 point/);
    });

    it('retains separate angle labels and refuses shifted angle grids', () => {
      const angular: ResultPayload = { frequencies: [100, 200], directivity: {
        horizontal: [[[0, 0], [30, -6]], [[0, 0], [30, -6]]],
      } };
      expect(buildVacs(angular)).toContain('horizontal, 30 deg');
      expect(buildVacs(angular).match(/Data_Format=Real/g)).toHaveLength(2);
      angular.directivity!.horizontal![1][1][0] = 40;
      expect(() => buildVacs(angular)).toThrow(/angle grids differ/);
      expect(buildPolarCsv(angular)).toContain('200,horizontal,40,-6');
    });

    it('refuses missing samples but preserves a genuine complex zero', () => {
      const missing: ResultPayload = { frequencies: [100], directivity: { horizontal: [[[0, null]]] } };
      expect(() => buildVacs(missing)).toThrow(/missing or non-finite magnitude/);
      expect(() => buildVacs({ frequencies: [100], impedance: { frequencies: [100], real: [1], imaginary: [null] } })).toThrow(/missing complex sample/);
      missing.directivity!.horizontal![0][0][1] = [0, 0];
      expect(buildVacs(missing)).toMatch(/^100 {3}0$/m);
    });

    it('produces no Data block at all when there is neither impedance nor directivity', () => {
      const empty: ResultPayload = { frequencies: [100, 200] };
      const text = buildVacs(empty, new Date('2026-09-04T00:00:00Z'));
      expect(text).not.toContain('Data_Format');
      expect(text).not.toContain('Data_End');
    });

    it('surfaces a mismatched axis as a bundle-level export failure, not a thrown crash', async () => {
      const misaligned: ResultPayload = {
        frequencies: [100, 200, 300],
        directivity: { horizontal: [[[0, -1]], [[0, -2]]] },
      };
      const saveText = vi.fn();
      const bundle = await runExportBundle({
        result: misaligned, jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), saveText,
      }, ['vacs']);
      expect(saveText).not.toHaveBeenCalled();
      expect(bundle.files).toEqual([]);
      expect(bundle.failures).toEqual([
        { format: 'vacs', reason: expect.stringMatching(/2 frame\(s\).*3 point\(s\)/) },
      ]);
    });
  });

  it('adds one versioned CAD identity sidecar to a manual result bundle', async () => {
    const saveText = vi.fn();
    const bundle = await runExportBundle({
      result,
      jobId: 'job-a',
      jobStem: 'horn_1',
      cadIdentity,
      preferences: preferencesStore.getSnapshot(),
      saveText,
    }, ['csv']);

    expect(bundle).toEqual({
      files: ['horn_1.csv', 'horn_1_cad_identity.json'], failures: [],
    });
    const sidecar = JSON.parse(String(saveText.mock.calls[1][0]));
    expect(saveText.mock.calls[1][1]).toBe('horn_1_cad_identity.json');
    expect(sidecar).toEqual({
      schema: 'waveguide-generator.cad-result-provenance',
      schema_version: 1,
      job_id: 'job-a',
      identity: cadIdentity,
    });
  });
  it('exports the engineering radiation matrix and reduced port loads with explicit semantics', () => {
    const csv = buildRadiationImpedanceCsv(radiation);
    expect(csv).toContain('# Units: Pa*s/m^3');
    expect(csv).toContain('# Phase time convention: engineering_exp_plus_jwt');
    expect(csv).toContain('100,engineering_matrix,"PORT,L",PORT_R,2,6');
    expect(csv).toContain('100,in_phase_termination,"PORT,L","PORT,L+PORT_R",3,11');
  });

  it('integrates lossless NPZ and curve CSV as one non-channel export family', async () => {
    const saveBlob = vi.fn();
    const saveText = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const path = String(input);
      if (path.endsWith('/presentation')) {
        return new Response(JSON.stringify(radiation), { status: 200 });
      }
      if (path === '/api/radiation-impedance/cardioid-job') {
        return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });
    const bundle = await runExportBundle({
      result: {
        frequencies: [],
        channel_order: ['left', 'right'],
        channels: { left: result, right: result },
      },
      jobId: 'cardioid-job',
      hasRadiationImpedanceArtifact: true,
      jobStem: 'cardioid_7',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      saveBlob,
      saveText,
    }, ['radiation_impedance_npz', 'radiation_impedance_csv']);

    expect(bundle).toEqual({
      files: ['cardioid_7_radiation_impedance.npz', 'cardioid_7_radiation_impedance.csv'],
      failures: [],
    });
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/radiation-impedance/cardioid-job',
      '/api/radiation-impedance/cardioid-job/presentation',
    ]);
    expect(saveBlob).toHaveBeenCalledOnce();
    expect(saveText).toHaveBeenCalledOnce();
  });

  it('refuses the optional radiation export cleanly when the artifact is absent', async () => {
    const bundle = await runExportBundle({
      jobId: 'ordinary-job',
      hasRadiationImpedanceArtifact: false,
      jobStem: 'ordinary_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher: vi.fn(),
    }, ['radiation_impedance_npz', 'radiation_impedance_csv']);
    expect(bundle).toEqual({
      files: [],
      failures: [
        { format: 'radiation_impedance_npz', reason: 'This run has no retained radiation-impedance matrix.' },
        { format: 'radiation_impedance_csv', reason: 'This run has no retained radiation-impedance matrix.' },
      ],
    });
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
  it('labels electrical impedance values as ohms in every text export', () => {
    const electrical: ResultPayload = {
      ...result,
      metadata: { impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance' },
    };
    const preferences = preferencesStore.getSnapshot();

    expect(buildFrequencyCsv(electrical, preferences).split('\n')[0])
      .toBe('Frequency (Hz),SPL (dB),DI (dB),Impedance Real (ohms),Impedance Imag (ohms)');
    expect(buildSummaryText(electrical, preferences)).toContain('Average Real Part ohms: 1.00');
    expect(buildSummaryText(electrical, preferences)).toContain('Z_Re(ohms)  Z_Im(ohms)');
    expect(buildImpedanceCsv(electrical).split('\n')[0]).toBe('Freq_Hz,Z_Real_Ohm,Z_Imag_Ohm');
  });
  it('preserves the normalized-acoustic labels for unloaded waveguide exports', () => {
    const preferences = preferencesStore.getSnapshot();

    expect(buildFrequencyCsv(result, preferences).split('\n')[0])
      .toContain('Impedance Real (Z/(rho*c)),Impedance Imag (Z/(rho*c))');
    expect(buildSummaryText(result, preferences)).toContain('Z_Re/(rho*c)  Z_Im/(rho*c)');
    expect(buildImpedanceCsv(result).split('\n')[0])
      .toBe('Freq_Hz,Z_Real_Z_over_rho_c,Z_Imag_Z_over_rho_c');
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
  it('downloads a selected retained pressure basis and stages it by server filename', async () => {
    const saveBlob = vi.fn();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      new Uint8Array([80, 75, 3, 4]),
      {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="drive-mf_pressure_basis.npz"' },
      },
    ));

    await expect(runExportFormat('pressure_basis', {
      result: { frequencies: [], channel_order: ['drive-mf'], channels: { 'drive-mf': result } },
      channelId: 'drive-mf',
      jobId: 'job/basis',
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      saveBlob,
    })).resolves.toEqual(['drive-mf_pressure_basis.npz']);

    expect(fetcher).toHaveBeenCalledWith('/api/pressure-basis/job%2Fbasis?channel_id=drive-mf');
    expect(saveBlob.mock.calls[0][1]).toBe('drive-mf_pressure_basis.npz');
    expect([...new Uint8Array(await (saveBlob.mock.calls[0][0] as Blob).arrayBuffer())])
      .toEqual([80, 75, 3, 4]);
  });
  it('exports retained drive bases without requesting solve-time derived channels', async () => {
    const wrapped: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-mf', 'combined'],
      channels: {
        'drive-mf': { ...result, metadata: { drive_channel_id: 'drive-mf' } },
        combined: { ...result, metadata: { derived_from_channels: ['drive-mf'] } },
      },
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(
      new Uint8Array([80, 75]),
      { status: 200, headers: { 'Content-Disposition': 'attachment; filename="drive-mf_pressure_basis.npz"' } },
    ));

    const bundle = await runExportBundle({
      result: wrapped,
      jobId: 'basis-job',
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
      saveBlob: vi.fn(),
    }, ['pressure_basis']);

    expect(bundle).toEqual({ files: ['drive-mf_pressure_basis.npz'], failures: [] });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith('/api/pressure-basis/basis-job?channel_id=drive-mf');
  });
  it('writes both derived-acoustics sidecars as one retryable format', async () => {
    const saveText = vi.fn();

    await expect(runExportFormat('derived_acoustics', {
      result,
      jobStem: 'horn_1',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    })).resolves.toEqual([
      'horn_1_derived_acoustics.csv',
      'horn_1_derived_acoustics.json',
    ]);

    expect(saveText).toHaveBeenCalledTimes(2);
    expect(saveText.mock.calls[0][0]).toContain('power_response_db_spl_avg');
    expect(saveText.mock.calls[0][0]).toContain('radiated_power_surface_w,radiated_power_sphere_w,power_agreement_db');
    expect(JSON.parse(saveText.mock.calls[1][0]).rows[0]).toMatchObject({
      frequency_hz: 100,
      power_response_db_spl_avg: 87,
    });
  });
  it('writes one self-contained report for the complete multi-channel run', async () => {
    const saveText = vi.fn();
    const wrapped: ResultPayload = {
      frequencies: [], channel_order: ['HF', 'MF'], channels: { HF: result, MF: result },
    };

    await expect(runExportFormat('html_report', {
      result: wrapped,
      jobStem: 'horn_1',
      designName: 'Horn One',
      preferences: preferencesStore.getSnapshot(),
      now: new Date('2026-08-20T12:00:00Z'),
      saveText,
    })).resolves.toEqual(['horn_1_report.html']);

    expect(saveText).toHaveBeenCalledOnce();
    expect(saveText.mock.calls[0][1]).toBe('horn_1_report.html');
    expect(saveText.mock.calls[0][2]).toBe('text/html;charset=utf-8');
    expect(saveText.mock.calls[0][0]).toContain('<h1>Horn One</h1>');
    expect(saveText.mock.calls[0][0]).toContain('<h2>HF</h2>');
    expect(saveText.mock.calls[0][0]).toContain('<h2>MF</h2>');
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
  it('posts config exports to the non-mutating serializer endpoint', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === '/api/design/serialize') return new Response(JSON.stringify({ text: 'cfg', suggestedFilename: 'horn_1_config.cfg' }), { status: 200 });
      return new Response('geometry', { status: 200, headers: { 'Content-Disposition': `attachment; filename="file.${path.endsWith('step') ? 'step' : 'stl'}"` } });
    });
    const context = { jobStem: 'horn_1', preferences: preferencesStore.getSnapshot(), design: designForFamily('OSSE'), designRevision: 4, fetcher, saveText: vi.fn(), saveBlob: vi.fn() };
    await runExportFormat('mwg_config', context);
    await runExportFormat('step', context);
    await runExportFormat('stl', context);
    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual(['/api/design/serialize', '/api/export/step', '/api/export/stl']);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).not.toHaveProperty('identity');
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
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).angle_guide_step).toBe(0);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).theme).toBe('paper');
    expect(saveBlob.mock.calls.map(([, filename]) => filename)).toEqual(['horn_1_spl.png', 'horn_1_directivity_map.png']);
    expect(buildChartRenderPayload({
      ...result,
      metadata: {
        observation: { effective_distance_m: 1.5, sound_speed_m_per_s: 343 },
        phase_time_convention: 'exp(+ikr)',
      },
    }, context.preferences)).toMatchObject({
      phase_reference_distance_m: 1.5,
      phase_time_convention: 'exp(+ikr)',
      sound_speed_m_per_s: 343,
      impedance_units: 'Z/(rho*c)',
    });
    expect(buildChartRenderPayload(result, context.preferences, { result, label: 'reference horn' })).toMatchObject({ reference: { label: 'reference horn', frequencies: [100, 200], spl: [90, null], impedance_normalization: 'rho_c' } });
  });

  it('preserves primary and reference sound speeds in rendered phase payloads', () => {
    const primary: ResultPayload = {
      ...result,
      metadata: {
        observation: { effective_distance_m: 2, sound_speed_m_per_s: 346 },
        phase_time_convention: 'exp(+ikr)',
      },
    };
    const reference: ResultPayload = {
      ...result,
      metadata: { observation: { sound_speed_m_per_s: 341 } },
    };

    expect(buildChartRenderPayload(
      primary,
      preferencesStore.getSnapshot(),
      { result: reference, label: 'reference horn' },
    )).toMatchObject({
      sound_speed_m_per_s: 346,
      reference: { sound_speed_m_per_s: 341 },
    });
  });

  it('omits all PNG propagation fields when the shared reference is incomplete', () => {
    const legacy: ResultPayload = {
      ...result,
      metadata: {
        observation: { effective_distance_m: 2 },
        phase_time_convention: 'exp(+ikr)',
      },
    };

    expect(buildChartRenderPayload(legacy, preferencesStore.getSnapshot())).toMatchObject({
      phase_reference_distance_m: null,
      phase_time_convention: null,
      sound_speed_m_per_s: null,
    });
  });

  it('omits phase samples from the PNG payload when the SPL phase preference is off', () => {
    expect(buildChartRenderPayload(result, {
      ...preferencesStore.getSnapshot(), splPhase: false,
    })).toMatchObject({ phase_degrees: [] });
  });

  describe('the rendered PNG is tagged with the unit the result declares', () => {
    const electrical: ResultPayload = {
      ...result,
      metadata: { impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance' },
    };

    it('sends ohms for a driver-coupled run rather than the normalized pair', () => {
      // Hardcoding Z/(rho*c) here put an ohms axis on screen and an acoustic
      // label in the file -- the disagreement the on-screen unit fix removes.
      expect(buildChartRenderPayload(electrical, preferencesStore.getSnapshot()))
        .toMatchObject({ impedance_units: 'ohms', impedance_normalization: 'absolute' });
    });

    it('keeps the normalized pair for an unloaded waveguide solve', () => {
      expect(buildChartRenderPayload(result, preferencesStore.getSnapshot()))
        .toMatchObject({ impedance_units: 'Z/(rho*c)', impedance_normalization: 'rho_c' });
    });

    it('tags the reference from the reference, so hornlab-plots can refuse the overlay', () => {
      // hornlab-plots skips a reference whose normalization differs. With both
      // pinned to rho_c an ohms primary silently accepted a Z/rho-c curve onto
      // its own scale, where it flatlines along the bottom and reads as a dead
      // run.
      const payload = buildChartRenderPayload(electrical, preferencesStore.getSnapshot(), { result, label: 'waveguide' });
      expect(payload).toMatchObject({ impedance_normalization: 'absolute' });
      expect(payload.reference).toMatchObject({ impedance_units: 'Z/(rho*c)', impedance_normalization: 'rho_c' });
    });
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
    const payload = workspacePayload(requests[1].init);
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
      members = workspacePayload(init).members;
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
      writePayload = workspacePayload(init);
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
      directory: 'C:/output/horn_1',
      files: ['C:/output/horn_1/horn_1.csv', 'C:/output/horn_1/horn_1_spl.png', 'C:/output/horn_1/horn_1_directivity_map.png'],
      failures: [],
    });
    const write = requests.find(({ path }) => path === '/api/workspace/write-export')!;
    const payload = workspacePayload(write.init);
    expect(payload).toMatchObject({ subdirectory: 'horn_1', existing: 'merge_identical' });
    expect(payload.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'horn_1.csv', 'horn_1_spl.png', 'horn_1_directivity_map.png',
    ]);
    expect(Array.from(new Uint8Array(await payload.members[1].blob.arrayBuffer()))).toEqual([1, 2, 3]);
    expect(Array.from(new Uint8Array(await payload.members[2].blob.arrayBuffer()))).toEqual([4, 5, 6]);
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

  it('adds both radiation artifacts to the permanent run archive only when retained', async () => {
    const job: JobItem = {
      id: 'archive-cardioid', run_number: 8, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z', config_summary: {},
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      has_radiation_impedance_artifact: true, radiation_impedance_artifact_bytes: 3, persistence_warnings: [],
      label: 'Cardioid', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    };
    const writes: Array<{ subdirectory: string; members: Array<{ relative_path: string }> }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const path = String(input);
      if (path === '/api/jobs/archive-cardioid/archive-snapshot') return new Response(JSON.stringify({
        schema_version: 1,
        results: finalResult,
        results_sha256: 'a'.repeat(64),
        mesh_artifact: null,
        pressure_bases: [{ channel_id: 'mf drive', content_base64: 'BAUG' }],
        radiation_impedance: { content_base64: 'AQID', presentation: radiation },
      }), { status: 200 });
      if (path === '/api/workspace/write-export') {
        const payload = workspacePayload(init);
        writes.push(payload);
        return new Response(JSON.stringify({
          directory: `/workspace/${payload.subdirectory}`,
          files: payload.members.map(({ relative_path }) => `/workspace/${payload.subdirectory}/${relative_path}`),
        }), { status: 200 });
      }
      return new Response('not found', { status: 404 });
    });

    await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);
    expect(writes[0].members.map(({ relative_path }) => relative_path)).toEqual([
      '8_Cardioid.json',
      '8_Cardioid.csv',
      '8_Cardioid_derived_acoustics.csv',
      '8_Cardioid_derived_acoustics.json',
      '8_Cardioid_report.html',
      'mf-drive_pressure_basis.npz',
      '8_Cardioid_radiation_impedance.npz',
      '8_Cardioid_radiation_impedance.csv',
    ]);
    expect(fetcher.mock.calls.map(([input]) => String(input)).filter((path) => (
      path.includes('archive-cardioid')
    ))).toEqual(['/api/jobs/archive-cardioid/archive-snapshot']);
  });

  it('writes the exact retained mesh copied into the archive snapshot', async () => {
    const job = {
      id: 'archive-mesh', run_number: 9, parent_job_id: null, status: 'complete', progress: 1,
      stage: null, stage_message: null, created_at: '2026-08-20T10:00:00Z', queued_at: '2026-08-20T10:00:00Z',
      started_at: '2026-08-20T10:00:01Z', completed_at: '2026-08-20T10:02:00Z', config_summary: {},
      solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false,
      label: 'Mesh Run', error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
      design_revision: 1, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
      auto_export_formats: {}, archived_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    } satisfies JobItem;
    const writes: ReturnType<typeof workspacePayload>[] = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (String(input) === '/api/jobs/archive-mesh/archive-snapshot') return new Response(JSON.stringify({
        schema_version: 1, results: finalResult, results_sha256: 'b'.repeat(64),
        mesh_artifact: 'exact mesh bytes', pressure_bases: [], radiation_impedance: null,
      }), { status: 200 });
      const payload = workspacePayload(init);
      writes.push(payload);
      return new Response(JSON.stringify({
        directory: `/workspace/${payload.subdirectory}`,
        files: payload.members.map(({ relative_path }: { relative_path: string }) => `/workspace/${payload.subdirectory}/${relative_path}`),
      }), { status: 200 });
    });

    await archiveRunToWorkspace(job, preferencesStore.getSnapshot(), fetcher);

    expect(writes[1].members.map(({ relative_path }) => relative_path)).toEqual([
      '9_Mesh_Run.msh',
    ]);
    expect(await writes[1].members[0].blob.text()).toBe('exact mesh bytes');
    expect(JSON.parse(await writes[2].members[0].blob.text()).artifacts.mesh)
      .toBe('9_Mesh_Run.msh');
  });

  it('sends a 64 MiB workspace member as multipart binary without base64 inflation', async () => {
    const content = new Uint8Array(64 * 1024 * 1024);
    content[0] = 0x89;
    content[content.length - 1] = 0xff;
    let requestBody: FormData | undefined;
    const fetcher = vi.fn<typeof fetch>(async (_input, init) => {
      requestBody = init?.body as FormData;
      return new Response(JSON.stringify({ directory: '/workspace/large', files: ['/workspace/large/member.bin'] }), { status: 200 });
    });

    await writeWorkspaceFiles('large', [{
      filename: 'member.bin', blob: new Blob([content]),
    }], fetcher);

    expect(requestBody).toBeInstanceOf(FormData);
    const upload = requestBody!.get('file') as File;
    expect(upload.size).toBe(content.byteLength);
    expect(Array.from(requestBody!.keys())).toEqual([
      'subdirectory', 'existing', 'relative_path', 'file',
    ]);
    expect(requestBody!.has('content_base64')).toBe(false);
    const uploaded = new Uint8Array(await upload.arrayBuffer());
    expect(uploaded[0]).toBe(0x89);
    expect(uploaded[uploaded.length - 1]).toBe(0xff);
  });
});
