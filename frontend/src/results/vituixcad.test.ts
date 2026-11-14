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

  it('keeps exact decimal bytes for large signed frequencies, magnitudes and rounding boundaries', () => {
    const values = [1e21, -1e21, 1.23456789e25, -1.23456789e25, 1.2345675, 1.2345685, -0, -0.0000001];
    expect(buildZma(electricalResult({
      frequencies: values,
      impedance: { frequencies: values, real: values, imaginary: values.map(() => 0) },
    }))).toMatchSnapshot();
  });

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
    })).toThrow('only a driven channel has');
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
    // A legacy payload only ever meant LR4, and keeps saying so.
    expect([...document.querySelectorAll('CROSSOVER > PART > Shape')]
      .map((node) => node.textContent).filter(Boolean))
      .toEqual(['Linkwitz-Riley', 'Linkwitz-Riley']);
    expect([...document.querySelectorAll('CROSSOVER > PART > Order')].map((node) => node.textContent))
      .toEqual(['4', '4']);
  });

  it('writes FRD phase so a source 100 mm behind is late, and the project delay aligns it', async () => {
    // Two point sources on one axis, observed at 2 m: the woofer at 2.0 m and
    // the horn's acoustic centre 100 mm further back. Raw solver pressure is
    // exp(+ikr), so its stored angle is +360 f r / c.
    const c = 343;
    const frequencies = [200, 500, 1_000, 1_500, 2_000];
    const channel = (rangeM: number): ResultPayload => electricalResult({
      frequencies,
      spl_on_axis: {
        frequencies,
        spl: frequencies.map(() => 90),
        phase_degrees: frequencies.map((frequency) => ((360 * frequency * rangeM / c) % 360 + 540) % 360 - 180),
      },
      impedance: { frequencies, real: frequencies.map(() => 6), imaginary: frequencies.map(() => 0) },
      metadata: {
        ...electricalResult().metadata,
        observation: { effective_distance_m: 2, sound_speed_m_per_s: c },
      },
    });
    const gapS = 0.1 / c;
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-lf', 'drive-hf', 'combined'],
      channels: {
        'drive-lf': channel(2),
        'drive-hf': channel(2.1),
        combined: {
          frequencies: [],
          metadata: {
            combine: {
              type: 'lr4_time_aligned_sum',
              members: ['drive-lf', 'drive-hf'],
              crossovers_hz: [1_000],
              level_match: { gains_db: { 'drive-lf': 0, 'drive-hf': 0 } },
              // The front (woofer) channel is delayed to meet the late horn.
              delays_ms: { 'drive-lf': gapS * 1_000, 'drive-hf': 0 },
            },
          },
        },
      },
    };
    const saveText = vi.fn();
    await runExportFormat('vxp', { result: wrapper, jobStem: 'pair', preferences: preferencesStore.getSnapshot(), saveText });
    const saved = (name: string) => String(saveText.mock.calls.find(([, filename]) => filename === name)?.[0]);
    const phases = (name: string) => dataRows(saved(name)).map((row) => Number(row[2]));
    const lf = phases('pair-drive-lf.frd');
    const hf = phases('pair-drive-hf.frd');

    // After the common 2 m flight is removed the woofer is at zero phase and
    // the horn lags by 360 f * 0.1 / c: it is behind, as built.
    lf.forEach((phase) => expect(phase).toBeCloseTo(0, 3));
    hf.forEach((phase, index) => {
      const lag = ((-360 * frequencies[index] * gapS) % 360 + 540) % 360 - 180;
      expect(phase).toBeCloseTo(lag, 3);
    });

    // VituixCAD applies Buffer dt as exp(-j omega dt) to the FRD it was given.
    const document = new DOMParser().parseFromString(saved('pair.vxp'), 'application/xml');
    // Each schematic row is wires, filters, a Buffer, then its Driver part.
    const delayByModel: Record<string, number> = {};
    let pendingDelayUs: number | null = null;
    [...document.querySelectorAll('CROSSOVER > PART')].forEach((part) => {
      const type = part.querySelector('Type')?.textContent;
      if (type === 'Buffer') {
        pendingDelayUs = Number([...part.querySelectorAll('PARAM')]
          .find((parameter) => parameter.querySelector('Name')?.textContent === 'dt')?.querySelector('Value')?.textContent);
      } else if (type === 'Driver') {
        delayByModel[String(part.querySelector('Model')?.textContent)] = pendingDelayUs ?? Number.NaN;
        pendingDelayUs = null;
      }
    });
    const delaysUs = [delayByModel['drive-lf'], delayByModel['drive-hf']];
    expect(delaysUs[0]).toBeCloseTo(gapS * 1e6, 3);
    expect(delaysUs[1]).toBe(0);
    const aligned = (phase: number, index: number, delayUs: number) => {
      const shifted = phase - 360 * frequencies[index] * delayUs * 1e-6;
      return ((shifted % 360) + 540) % 360 - 180;
    };
    lf.forEach((phase, index) => {
      expect(aligned(phase, index, delaysUs[0])).toBeCloseTo(aligned(hf[index], index, delaysUs[1]), 2);
    });
  });

  it('maps the per-channel families, orders and polarity into Shape, Order and Inverted', async () => {
    const combined: ResultPayload = {
      frequencies: [],
      metadata: {
        combine: {
          type: 'filtered_time_aligned_sum',
          members: ['drive-mf', 'drive-hf'],
          reference: 'drive-hf',
          crossovers_hz: [null],
          channels: {
            'drive-mf': {
              hp: null, lp: { family: 'butterworth', order: 3, fc_hz: 900 },
              gain_db: -2, gain_mode: 'auto', delay_ms: 0.25, delay_mode: 'auto',
              inverted: true, invert_mode: 'auto',
            },
            'drive-hf': {
              hp: { family: 'bessel', order: 2, fc_hz: 1_100 }, lp: null,
              gain_db: 1.5, gain_mode: 'manual', delay_ms: 0, delay_mode: 'auto',
              inverted: false, invert_mode: 'auto',
            },
          },
        },
      },
    };
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-hf', 'combined'],
      channels: { 'drive-mf': electricalResult(), 'drive-hf': electricalResult(), combined },
    };
    const saveText = vi.fn();

    await runExportFormat('vxp', {
      result: wrapper, jobStem: 'horn_7', preferences: preferencesStore.getSnapshot(), saveText,
    });

    const project = String(saveText.mock.calls.find(([, filename]) => filename === 'horn_7.vxp')?.[0]);
    const document = new DOMParser().parseFromString(project, 'application/xml');
    // Written highest band first, so the HF Bessel high-pass leads.
    expect([...document.querySelectorAll('CROSSOVER > PART')]
      .filter((part) => part.querySelector('Type')?.textContent?.startsWith('Active'))
      .map((part) => [
        part.querySelector('Type')?.textContent,
        part.querySelector('Shape')?.textContent,
        part.querySelector('Order')?.textContent,
        part.querySelector('PARAM > Value')?.textContent,
      ])).toEqual([
      ['Active High pass', 'Bessel', '2', '1100'],
      ['Active Low pass', 'Butterworth', '3', '900'],
    ]);
    expect([...document.querySelectorAll('CROSSOVER > PART')]
      .filter((part) => part.querySelector('Type')?.textContent === 'Buffer')
      .map((part) => part.querySelector('Inverted')?.textContent)).toEqual(['False', 'True']);
  });

  it('exports a linear-phase section as Linkwitz-Riley and says so in the description', async () => {
    const combined: ResultPayload = {
      frequencies: [],
      metadata: {
        combine: {
          type: 'filtered_time_aligned_sum',
          members: ['drive-mf', 'drive-hf'],
          crossovers_hz: [1_000],
          channels: {
            'drive-mf': { hp: null, lp: { family: 'linear_phase', order: 8, fc_hz: 1_000 } },
            'drive-hf': { hp: { family: 'linear_phase', order: 8, fc_hz: 1_000 }, lp: null },
          },
        },
      },
    };
    const wrapper: ResultPayload = {
      frequencies: [],
      channel_order: ['drive-mf', 'drive-hf', 'combined'],
      channels: { 'drive-mf': electricalResult(), 'drive-hf': electricalResult(), combined },
    };
    const saveText = vi.fn();

    await runExportFormat('vxp', {
      result: wrapper, jobStem: 'horn_7', preferences: preferencesStore.getSnapshot(), saveText,
    });

    const project = String(saveText.mock.calls.find(([, filename]) => filename === 'horn_7.vxp')?.[0]);
    const document = new DOMParser().parseFromString(project, 'application/xml');
    expect([...document.querySelectorAll('CROSSOVER > PART > Shape')].filter((node) => node.textContent)
      .map((node) => node.textContent)).toEqual(['Linkwitz-Riley', 'Linkwitz-Riley']);
    expect(document.querySelector('SPEAKER > Description')?.textContent)
      .toContain('linear-phase sections exported as Linkwitz-Riley');
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
    const body = requests[0].init?.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(String(body.get('subdirectory'))).toBe('horn_7');
    expect(String(body.get('existing'))).toBe('merge_identical');
    expect(body.getAll('relative_path').map(String)).toEqual([
      'horn_7.frd', 'horn_7.zma', 'horn_7.vxp',
    ]);
  });
});
