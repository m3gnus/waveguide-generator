import { beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { runExportBundle, runExportFormat, runWorkspaceExportBundle } from './exporters';
import type { ResultPayload } from './types';
import { buildZma, hasElectricalImpedance } from './vituixcad';

function electricalResult(overrides: Partial<ResultPayload> = {}): ResultPayload {
  return {
    frequencies: [100, 500, 1000],
    spl_on_axis: {
      frequencies: [100, 500, 1000],
      spl: [80, 85, 90],
      phase_degrees: [10, 20, 30],
    },
    impedance: {
      frequencies: [100, 500, 1000],
      real: [3, -1, 0],
      imaginary: [4, 0, 2],
    },
    metadata: {
      impedance_units: 'ohms',
      impedance_quantity: 'electrical_input_impedance',
      impedance_phase_convention: 'engineering_exp_plus_jwt',
      phase_time_convention: 'exp(+ikr)',
    },
    ...overrides,
  };
}

function dataRows(text: string): string[][] {
  return text.trim().split('\n').filter((line) => !line.startsWith('*')).map((line) => line.split('\t'));
}

describe('VituixCAD exports', () => {
  beforeEach(() => preferencesStore.resetForTests());

  it('writes a sorted three-column ZMA with FRD-style headers and engineering phase', () => {
    const result = electricalResult({
      impedance: {
        frequencies: [1000, 100, 500],
        real: [0, 3, -1],
        imaginary: [2, 4, 0],
      },
    });

    const zma = buildZma(result);

    expect(zma).toContain('* HornLab electrical input impedance');
    expect(zma).toContain('* Freq(Hz)\tMagnitude(ohms)\tPhase(degrees)');
    expect(zma).toContain('engineering exp(+jωt), from impedance tag');
    expect(dataRows(zma)).toEqual([
      ['100.000000', '5.000000', '53.1301'],
      ['500.000000', '1.000000', '180.0000'],
      ['1000.000000', '2.000000', '90.0000'],
    ]);
  });

  it('uses FRD\'s phase-tag resolver when an impedance curve is explicitly solver-convention', () => {
    const zma = buildZma(electricalResult({
      frequencies: [100],
      impedance: { frequencies: [100], real: [0], imaginary: [2] },
      metadata: {
        impedance_units: 'ohms',
        impedance_quantity: 'electrical_input_impedance',
        impedance_phase_convention: 'solver_exp_plus_ikr',
      },
    }));

    expect(dataRows(zma)).toEqual([['100.000000', '2.000000', '-90.0000']]);
    expect(zma).toContain('converted to engineering exp(+jωt)');
  });

  it('refuses unit-drive acoustic impedance instead of labelling it as ohms', () => {
    expect(() => buildZma({
      frequencies: [100],
      impedance: { frequencies: [100], real: [1], imaginary: [2] },
      metadata: { impedance_units: 'Z/(rho*c)', phase_time_convention: 'exp(+ikr)' },
    })).toThrow('Only a channel with a driver model has electrical input impedance');
  });

  it.each([undefined, 'acoustic_input_impedance'])('refuses tag-absent ohmic impedance without the electrical-input quantity (%s)', (quantity) => {
    const result = electricalResult({
      metadata: {
        impedance_units: 'ohms',
        ...(quantity ? { impedance_quantity: quantity } : {}),
      },
    });

    expect(hasElectricalImpedance(result)).toBe(false);
    expect(() => buildZma(result)).toThrow('impedance_quantity "electrical_input_impedance"');
  });

  it('fans ZMA out only to driver-modelled channels with the established suffix', async () => {
    const acoustic = {
      ...electricalResult(),
      metadata: { impedance_units: 'Z/(rho*c)', phase_time_convention: 'exp(+ikr)' },
    };
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-hf', 'unit-basis', 'drive-mf', 'combined'],
      channels: {
        'drive-hf': electricalResult(),
        'unit-basis': acoustic,
        'drive-mf': electricalResult(),
        combined: { frequencies: [], metadata: { impedance_omitted: 'combined channel' } },
      },
    };
    const saveText = vi.fn();

    const bundle = await runExportBundle({
      result: wrapper,
      jobStem: 'horn_7',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    }, ['zma']);

    expect(bundle).toEqual({
      files: ['horn_7-drive-hf.zma', 'horn_7-drive-mf.zma'],
      failures: [],
    });
    expect(saveText.mock.calls.map(([, filename]) => filename)).toEqual([
      'horn_7-drive-hf.zma', 'horn_7-drive-mf.zma',
    ]);
  });

  it('keeps the channel suffix when exactly one channel in a wrapper has a driver model', async () => {
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-hf', 'combined'],
      channels: {
        'drive-hf': electricalResult(),
        combined: { frequencies: [], metadata: { impedance_omitted: 'combined channel' } },
      },
    };
    const saveText = vi.fn();

    const bundle = await runExportBundle({
      result: wrapper, jobStem: 'horn_7', preferences: preferencesStore.getSnapshot(), saveText,
    }, ['zma']);

    expect(bundle).toEqual({ files: ['horn_7-drive-hf.zma'], failures: [] });
    expect(saveText.mock.calls[0][1]).toBe('horn_7-drive-hf.zma');
  });

  it('builds a version-2 project that references every emitted FRD and ZMA', async () => {
    const combined: ResultPayload = {
      frequencies: [],
      metadata: {
        combine: {
          type: 'lr4_time_aligned_sum',
          members: ['drive-mf', 'drive-hf'],
          crossovers_hz: [1200],
          level_match: { gains_db: { 'drive-mf': -2, 'drive-hf': 1.5 } },
          delays_ms: { 'drive-mf': 0.25, 'drive-hf': 0 },
        },
      },
    };
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-hf', 'combined'],
      channels: {
        'drive-mf': electricalResult(),
        'drive-hf': electricalResult(),
        combined,
      },
    };
    const saveText = vi.fn();

    const files = await runExportFormat('vxp', {
      result: wrapper,
      jobStem: 'horn_7',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    });

    expect(files).toEqual([
      'horn_7-drive-mf.frd', 'horn_7-drive-mf.zma',
      'horn_7-drive-hf.frd', 'horn_7-drive-hf.zma',
      'horn_7.vxp',
    ]);
    const project = String(saveText.mock.calls.find(([, filename]) => filename === 'horn_7.vxp')?.[0]);
    const document = new DOMParser().parseFromString(project, 'application/xml');
    expect(document.querySelector('parsererror')).toBeNull();
    expect([...document.querySelectorAll('DRIVER > Model')].map((node) => node.textContent)).toEqual(['drive-mf', 'drive-hf']);
    expect([...document.querySelectorAll('DRIVER > RESPONSE > FileName')].map((node) => node.textContent)).toEqual([
      'horn_7-drive-mf.frd', 'horn_7-drive-hf.frd',
    ]);
    expect([...document.querySelectorAll('DRIVER > ImpedanceFile')].map((node) => node.textContent)).toEqual([
      'horn_7-drive-mf.zma', 'horn_7-drive-hf.zma',
    ]);
    expect([...document.querySelectorAll('CROSSOVER > PART > Type')].map((node) => node.textContent)).toEqual(expect.arrayContaining([
      'Active Low pass', 'Active High pass', 'Buffer', 'Driver',
    ]));
    const parameters = [...document.querySelectorAll('CROSSOVER PARAM')].map((node) => [
      node.querySelector('Name')?.textContent, node.querySelector('Value')?.textContent,
    ]);
    expect(parameters).toContainEqual(['f', '1200']);
    expect(parameters).toContainEqual(['dt', '250']);
  });

  it('allocates distinct portable dependency names for channel ids that sanitize alike', async () => {
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['mf/a', 'mf a'],
      channels: {
        'mf/a': electricalResult(),
        'mf a': electricalResult(),
      },
    };
    const saveText = vi.fn();

    const bundle = await runExportBundle({
      result: wrapper,
      jobStem: 'horn_7',
      preferences: preferencesStore.getSnapshot(),
      saveText,
    }, ['vxp', 'zma']);

    expect(bundle).toEqual({
      files: [
        'horn_7-mf-a.frd', 'horn_7-mf-a.zma',
        'horn_7-mf-a-2.frd', 'horn_7-mf-a-2.zma',
        'horn_7.vxp',
      ],
      failures: [],
    });
    const project = String(saveText.mock.calls.find(([, filename]) => filename === 'horn_7.vxp')?.[0]);
    const document = new DOMParser().parseFromString(project, 'application/xml');
    expect([...document.querySelectorAll('DRIVER > RESPONSE > FileName')].map((node) => node.textContent))
      .toEqual(['horn_7-mf-a.frd', 'horn_7-mf-a-2.frd']);
    expect([...document.querySelectorAll('DRIVER > ImpedanceFile')].map((node) => node.textContent))
      .toEqual(['horn_7-mf-a.zma', 'horn_7-mf-a-2.zma']);
    expect(new Set(saveText.mock.calls.map(([, filename]) => String(filename))).size).toBe(5);
  });

  it('writes VXP dependencies through the automatic workspace flow and de-duplicates selected ZMA', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      requests.push({ path: String(input), init });
      return new Response(JSON.stringify({
        directory: '/checkout/output/horn_7',
        files: [
          '/checkout/output/horn_7/horn_7.frd',
          '/checkout/output/horn_7/horn_7.zma',
          '/checkout/output/horn_7/horn_7.vxp',
        ],
      }), { status: 200 });
    });

    const bundle = await runWorkspaceExportBundle({
      result: electricalResult(),
      jobStem: 'horn_7',
      preferences: preferencesStore.getSnapshot(),
      fetcher,
    }, ['vxp', 'zma']);

    expect(bundle.failures).toEqual([]);
    const body = JSON.parse(String(requests[0].init?.body));
    expect(body).toMatchObject({ subdirectory: 'horn_7', existing: 'merge_identical' });
    expect(body.members.map((member: { relative_path: string }) => member.relative_path)).toEqual([
      'horn_7.frd', 'horn_7.zma', 'horn_7.vxp',
    ]);
  });
});
