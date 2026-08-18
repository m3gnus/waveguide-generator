import { beforeEach, describe, expect, it } from 'vitest';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';
import { parseMeasuredTrace } from '../results/measuredTrace';
import { MAX_MEASURED_OVERLAYS, useMeasuredOverlayStore, type MeasuredOverlay } from '../stores/measuredOverlays';
import { measuredSeriesName, splOption } from './ResultsPanel';

const tokens: ChartTokens = {
  foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff',
  series: ['#0ff', '#f90', '#f55'], colormap: ['#000', '#fff'],
};

const sweep = [500, 700, 1_000, 1_400, 2_000];
const payload: ResultPayload = {
  frequencies: sweep,
  spl_on_axis: { frequencies: sweep, spl: [90, 96, 84, 99, 88] },
} as ResultPayload;
const items: NamedResult[] = [{ id: 'run', label: 'Run A', result: payload }];

function overlay(update: Partial<MeasuredOverlay> = {}): MeasuredOverlay {
  return {
    id: 'measured-test',
    label: 'bench 1m',
    points: [
      { frequencyHz: 100, splDb: 80, phaseDeg: null },
      { frequencyHz: 1_000, splDb: 88, phaseDeg: null },
      { frequencyHz: 8_000, splDb: 82, phaseDeg: null },
    ],
    offsetDb: 0,
    visible: true,
    ...update,
  };
}

type SplOption = {
  xAxis: { min?: number; max?: number };
  series: Array<{ name: string; lineStyle: { color: string; type: string }; data: Array<Array<number | null>> }>;
};

function option(measured: MeasuredOverlay[], smoothing: 'none' | '1/1' = 'none'): SplOption {
  return splOption(items, tokens, smoothing, 'full', measured) as unknown as SplOption;
}

describe('splOption with measured overlays', () => {
  it('appends one dotted series per overlay, after the simulated runs', () => {
    const { series } = option([overlay()]);
    expect(series.map(({ name }) => name)).toEqual(['Run A', measuredSeriesName('bench 1m')]);
    expect(series[0].lineStyle.type).toBe('solid');
    expect(series[1].lineStyle.type).toBe('dotted');
  });

  it('adds the level offset to the drawn points without touching the parsed ones', () => {
    const loaded = overlay({ offsetDb: -6.5 });
    const { series } = option([loaded]);
    expect(series[1].data).toEqual([[100, 73.5], [1_000, 81.5], [8_000, 75.5]]);
    expect(loaded.points.map(({ splDb }) => splDb)).toEqual([80, 88, 82]);
  });

  it('keeps the frequency axis on the simulated sweep, not the measurement span', () => {
    // The measurement runs 100 Hz to 8 kHz; the solve covers 500 Hz to 2 kHz.
    // Extending the axis would shrink the result being validated to a third of
    // its own chart, so the measured curve is clipped instead.
    expect(option([overlay()]).xAxis).toMatchObject({ min: 500, max: 2_000 });
    expect(option([]).xAxis).toMatchObject({ min: 500, max: 2_000 });
  });

  it('never smooths a measurement, whatever the preference says', () => {
    const raw = option([overlay()], 'none');
    const smoothed = option([overlay()], '1/1');
    expect(smoothed.series[1].data).toEqual(raw.series[1].data);
    // The simulated curve does follow the preference, so the test above is not
    // passing because smoothing is inert at this length.
    expect(smoothed.series[0].data).not.toEqual(raw.series[0].data);
  });

  it('colours measurements from the same label-keyed palette as the runs', () => {
    // Three labels into a three-entry palette: the run and both measurements
    // must be visually separable, which is the palette guarantee the shared
    // label-keyed table provides.
    const { series } = option([overlay({ id: 'a', label: 'bench 1m' }), overlay({ id: 'b', label: 'bench 2m' })]);
    const colors = series.map(({ lineStyle }) => lineStyle.color);
    expect(new Set(colors).size).toBe(3);
    expect(colors.every((color) => tokens.series.includes(color))).toBe(true);
  });
});

describe('useMeasuredOverlayStore', () => {
  beforeEach(() => useMeasuredOverlayStore.getState().clear());

  it('adds a parsed trace as a visible overlay at 0 dB', () => {
    const trace = parseMeasuredTrace('100 90\n200 91\n', 'bench 1m.frd');
    const added = useMeasuredOverlayStore.getState().add(trace);
    expect(added).toMatchObject({ label: 'bench 1m', offsetDb: 0, visible: true });
    expect(useMeasuredOverlayStore.getState().overlays).toHaveLength(1);
  });

  it('refuses to add past the overlay limit', () => {
    const trace = parseMeasuredTrace('100 90\n200 91\n', 'bench.frd');
    for (let index = 0; index < MAX_MEASURED_OVERLAYS; index += 1) {
      expect(useMeasuredOverlayStore.getState().add(trace)).not.toBeNull();
    }
    expect(useMeasuredOverlayStore.getState().add(trace)).toBeNull();
    expect(useMeasuredOverlayStore.getState().overlays).toHaveLength(MAX_MEASURED_OVERLAYS);
  });

  it('toggles, offsets and removes by id', () => {
    const trace = parseMeasuredTrace('100 90\n200 91\n', 'bench.frd');
    const first = useMeasuredOverlayStore.getState().add(trace)!;
    const second = useMeasuredOverlayStore.getState().add(trace)!;
    expect(first.id).not.toBe(second.id);

    useMeasuredOverlayStore.getState().toggleVisible(first.id);
    useMeasuredOverlayStore.getState().setOffsetDb(second.id, 3.5);
    useMeasuredOverlayStore.getState().setOffsetDb(second.id, Number.NaN);
    const [a, b] = useMeasuredOverlayStore.getState().overlays;
    expect(a.visible).toBe(false);
    expect(b.offsetDb).toBe(3.5);

    useMeasuredOverlayStore.getState().remove(first.id);
    expect(useMeasuredOverlayStore.getState().overlays.map(({ id }) => id)).toEqual([second.id]);
  });
});
