import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { EChartsOption } from 'echarts';
import {
  UNQUALIFIED_MESSAGE,
  channelQualificationSummary,
  chartUnqualifiedBands,
  formatUnqualifiedRanges,
  powerChipLabel,
  powerQualificationDetail,
  powerQualificationOf,
  unqualifiedBands,
  unqualifiedCaption,
  withUnqualifiedBands,
} from './powerQualification';
import type { ResultPayload } from './types';

/** The archived Speaker2 v4 run as stored before qualification existed. */
function archived(): ResultPayload {
  return JSON.parse(new TextDecoder().decode(readFileSync('../server/tests/data/power-qualification-speaker2-v4.json'))) as ResultPayload;
}

/** The same run as the server serves it when opened: read-time flags added. */
function served(): ResultPayload {
  return JSON.parse(new TextDecoder().decode(readFileSync('../server/tests/data/power-qualification-speaker2-v4.read-time.json'))) as ResultPayload;
}

function channel(wrapper: ResultPayload, id: string): ResultPayload {
  return wrapper.channels![id] as ResultPayload;
}

const tokens = { foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff', series: ['#0ff'], colormap: ['#000'] };

function flat(agreement: Array<number | null>, surface?: Array<number | null>, validityHz?: number): ResultPayload {
  const frequencies = agreement.map((_, index) => 100 * (index + 1));
  const faces = surface ?? agreement.map(() => 1e-5);
  return {
    frequencies,
    metadata: {
      source_ids: ['src'],
      ...(validityHz ? { per_source_frequency_validity: { src: { effective_max_valid_frequency_hz: validityHz } } } : {}),
      radiated_power: {
        surface_w: faces,
        sphere_w: agreement.map((value, index) => value === null ? 1e-9 : (faces[index] ?? 1e-5) * 10 ** (value / 10)),
        sphere_coverage_sr: 4 * Math.PI,
        definition: 'test',
        agreement_db: agreement,
      },
    },
  } as ResultPayload;
}

describe('power qualification', () => {
  it('reads the server flags for the archived Speaker2 run: LF from 200 Hz, HF clean, combined names LF', () => {
    const wrapper = served();
    const lf = powerQualificationOf(channel(wrapper, 'drive-lf'), wrapper)!;
    const hf = powerQualificationOf(channel(wrapper, 'drive-hf'), wrapper)!;
    const combined = powerQualificationOf(channel(wrapper, 'combined'), wrapper)!;

    expect(lf.status).toBe('unqualified');
    expect(lf.evaluated).toBe('read_time');
    expect(formatUnqualifiedRanges(lf.ranges)).toBe('200 Hz–1.21 kHz, 1.81 kHz');
    expect(lf.formulation).toBe('complex_k');
    expect(hf.status).toBe('qualified');
    expect(hf.ranges).toEqual([]);
    expect(combined.status).toBe('unqualified');
    expect(combined.unqualifiedChannels).toEqual(['drive-lf']);
  });

  it('evaluates an unflagged payload by the same rules the server applies', () => {
    const raw = archived();
    const flagged = served();
    for (const id of ['drive-lf', 'drive-hf', 'combined']) {
      const client = powerQualificationOf(channel(raw, id), raw)!;
      const server = powerQualificationOf(channel(flagged, id), flagged)!;
      expect(client.evaluated).toBe('client');
      expect(client.status).toBe(server.status);
      expect(client.frequencyStatus).toEqual(server.frequencyStatus);
      expect(client.ranges).toEqual(server.ranges);
    }
  });

  it('flags strictly above 0.5 dB, nonpositive or missing face power, and ignores frequencies past validity', () => {
    const edges = powerQualificationOf(flat([0.5, -0.5, 0.51, -0.51]))!;
    expect(edges.frequencyStatus).toEqual(['qualified', 'qualified', 'unqualified', 'unqualified']);

    const faces = powerQualificationOf(flat([0.1, null, null, 0.1], [1e-5, 0, null, 1e-5]))!;
    expect(faces.frequencyReasons).toEqual([null, 'nonpositive_face_power', 'nonfinite_face_power', null]);
    expect(faces.status).toBe('unqualified');

    const bounded = powerQualificationOf(flat([0.1, 0.2, -9], undefined, 250))!;
    expect(bounded.frequencyStatus).toEqual(['qualified', 'qualified', 'outside_validity']);
    expect(bounded.status).toBe('qualified');
  });

  it('says nothing for a payload that carries no power check and no server flags', () => {
    expect(powerQualificationOf({ frequencies: [100], metadata: {} } as ResultPayload)).toBeNull();
  });

  it('gives every band width, even a single flagged sample, halfway to its neighbours', () => {
    const wrapper = served();
    const lf = channel(wrapper, 'drive-lf');
    const bands = unqualifiedBands(lf, powerQualificationOf(lf, wrapper)!);
    const f = lf.frequencies;

    expect(bands).toHaveLength(2);
    expect(bands[0].fromHz).toBe(200);
    expect(bands[0].toHz).toBeCloseTo(Math.sqrt(f[9] * f[10]));
    expect(bands[1].fromHz).toBeCloseTo(Math.sqrt(f[10] * f[11]));
    expect(bands[1].toHz).toBeCloseTo(Math.sqrt(f[11] * f[12]));
  });

  it('hatches the bands on the first series only and changes no curve', () => {
    const option: EChartsOption = { series: [{ type: 'line', name: 'a', data: [[200, 1]] }, { type: 'line', name: 'b', data: [[200, 2]] }] };
    const marked = withUnqualifiedBands(option, [{ fromHz: 200, toHz: 400, label: '' }], tokens);
    const [first, second] = marked.series as Array<Record<string, unknown>>;

    expect((first.markArea as { data: unknown[] }).data).toEqual([[{ name: 'Unqualified', xAxis: 200 }, { xAxis: 400 }]]);
    expect(first.data).toEqual([[200, 1]]);
    expect(second.markArea).toBeUndefined();
    expect(withUnqualifiedBands(option, [], tokens)).toBe(option);
  });

  it('marks the combined view and its members from the sum, and names the run in a comparison', () => {
    const wrapper = served();
    const entries = [
      { id: 'a', label: 'Combined', result: channel(wrapper, 'combined'), wrapper, primary: true },
      { id: 'b', label: 'LF', result: channel(wrapper, 'drive-lf'), wrapper, secondary: true },
      { id: 'c', label: 'HF', result: channel(wrapper, 'drive-hf'), wrapper, secondary: true },
    ];
    expect(chartUnqualifiedBands(entries).map(({ label }) => label)).toEqual(['', '']);
    expect(unqualifiedCaption(entries)).toBe('Unqualified 200 Hz–1.21 kHz, 1.81 kHz');

    const hfOnly = [{ id: 'h', label: 'HF', result: channel(wrapper, 'drive-hf'), wrapper }];
    expect(chartUnqualifiedBands(hfOnly)).toEqual([]);
    expect(unqualifiedCaption(hfOnly)).toBeNull();

    const compared = [
      { id: 'x', label: '#1', result: channel(wrapper, 'drive-lf'), wrapper },
      { id: 'y', label: '#2', result: channel(wrapper, 'combined'), wrapper },
    ];
    expect(new Set(chartUnqualifiedBands(compared).map(({ label }) => label))).toEqual(new Set(['#1', '#2']));
  });

  it('labels the chip and explains each state', () => {
    const wrapper = served();
    const combined = powerQualificationOf(channel(wrapper, 'combined'), wrapper)!;
    expect(powerChipLabel(combined)).toBe('Power check: unqualified ⚠');
    const detail = powerQualificationDetail(combined).join(' ');
    expect(detail).toContain(UNQUALIFIED_MESSAGE);
    expect(detail).toContain('Unqualified contributing channel: drive-lf.');
    expect(detail).toContain('No level has been changed.');

    const hf = powerQualificationOf(channel(wrapper, 'drive-hf'), wrapper)!;
    expect(powerChipLabel(hf)).toBe('Power check ✓');
    expect(powerQualificationDetail(hf).join(' ')).toContain('agree within 0.5 dB');

    const unknown = powerQualificationOf({ frequencies: [100], metadata: { power_qualification: { status: 'unknown', unknown_reason: 'power_check_unavailable' } } } as unknown as ResultPayload)!;
    expect(powerChipLabel(unknown)).toBe('Power check: unknown');
    expect(powerQualificationDetail(unknown)[0]).toContain('carries no radiated-power check');
  });
  it('summarises every channel of a multi-channel run, and nothing for a single channel', () => {
    const wrapper = served();
    expect(channelQualificationSummary(wrapper)).toBe('Channels: drive-lf unqualified · drive-hf qualified · combined unqualified.');
    // An archived record the server never flagged reads the same on the client.
    expect(channelQualificationSummary(archived())).toBe('Channels: drive-lf unqualified · drive-hf qualified · combined unqualified.');
    expect(channelQualificationSummary(flat([0.1, -2]))).toBeNull();
  });
});
