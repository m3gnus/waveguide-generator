import { describe, expect, it } from 'vitest';
import type { EChartsOption } from 'echarts';
import { chartSpecFromOption, colorRole, type LineChartSpec, type PolarChartSpec } from './chartSpec';
import { screenAxisExtent } from './drawnCharts';
import { withUnqualifiedBands } from './powerQualification';
import { impedanceSeries, splSeries, type NamedResult } from './mappers';
import { SERIES_FIXTURES } from './series.fixtures';
import type { ChartTokens } from './EChart';
import type { ResultPayload } from './types';
import { impedanceOption, phaseOption, polarOption, splOption } from '../shell/ResultsPanel';

const tokens: ChartTokens = {
  background: '#211F1D', foreground: '#ECE8E0', muted: '#BBBBBB', spine: '#ECE8E0', grid: '#4A453D', gridMinor: '#333',
  accent: '#E0673F', series: ['#E0673F', '#5D9BD9', '#AD8400', '#00A6AD', '#CA90F3', '#60B374'], colormap: ['#000', '#fff'],
};
const fixture = SERIES_FIXTURES['shared grid, plane DI'];
const named = (label: string, result: ResultPayload, primary = false): NamedResult => ({ id: label, label, result, ...(primary ? { primary: true } : {}) });

describe('chart images take the series the card drew', () => {
  it('carries every SPL curve exactly as the series layer gave it, with axis titles at any card size', () => {
    const items = [named('A', fixture, true), named('B', SERIES_FIXTURES['separate grids, flat DI with gaps'])];
    const option = splOption(items, tokens, '1/3', 'compact');
    const spec = chartSpecFromOption(option, tokens, { title: 'Sound pressure level', subtitle: 'on axis' }) as LineChartSpec;
    expect(spec).toMatchObject({ kind: 'lines', title: 'Sound pressure level', subtitle: 'on axis', x: { label: 'Frequency [Hz]', scale: 'log' }, y: { label: 'dB SPL' } });
    const screen = splSeries(items, '1/3');
    expect(spec.series.map(({ label, x, y }) => ({ label, x, y }))).toEqual(screen.map(({ name, data }) => ({
      label: name, x: data.map(([frequency]) => frequency), y: data.map(([, value]) => value),
    })));
    // The primary run is solid in its palette slot; a comparison is dashed in its own.
    const slots = option.series as Array<{ lineStyle: { color: string } }>;
    expect(spec.series.map(({ color, style }) => ({ color, style }))).toEqual([
      { color: tokens.series.indexOf(slots[0].lineStyle.color), style: 'solid' },
      { color: tokens.series.indexOf(slots[1].lineStyle.color), style: 'dashed' },
    ]);
    expect(spec.x.min).toBe(Math.min(...screen.flatMap(({ data }) => data.map(([frequency]) => frequency as number))));
  });

  it('puts a companion quantity on the right-hand axis and keeps the screen extents', () => {
    const option = impedanceOption([named('A', fixture, true)], tokens, 'none', 'regular', 'magnitude_phase');
    const extent = (axis: 'x' | 'y', index: number): [number, number] | null => (axis === 'x' ? [200, 2_000] : index ? [-90, 90] : [0, 3]);
    const spec = chartSpecFromOption(option, tokens, { title: 'Impedance', extent }) as LineChartSpec;
    expect(spec.y2).toEqual({ label: 'Phase [°]', min: -90, max: 90 });
    expect(spec.y).toMatchObject({ min: 0, max: 3 });
    expect(spec.x).toMatchObject({ min: 200, max: 2_000 });
    const drawn = impedanceSeries(fixture, 'polar', 'none');
    expect(spec.series.map(({ axis, y }) => ({ axis, y }))).toEqual(drawn.map((entry) => ({
      axis: (entry as { yAxisIndex?: number }).yAxisIndex === 1 ? 1 : 0, y: entry.data.map(([, value]) => value),
    })));
  });

  it('keeps the zero reference and the hatched unqualified bands', () => {
    const option = withUnqualifiedBands(phaseOption([named('A', fixture, true)], tokens, 'full'), [{ fromHz: 400, toHz: 800, label: 'HF' }], tokens);
    const spec = chartSpecFromOption(option, tokens, { title: 'Pressure phase' }) as LineChartSpec;
    expect(spec.hlines).toEqual([{ y: 0, axis: 0 }]);
    expect(spec.bands).toEqual([{ x0: 400, x1: 800, label: 'Unqualified · HF' }]);
  });

  it('describes a polar cut with its slice, its rings and its unmodelled rear', () => {
    const angles = [-180, -150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150, 180];
    const pattern = angles.map((angle) => [angle, -Math.abs(angle) / 12] as [number, number]);
    const result = {
      frequencies: [1_000],
      directivity: { horizontal: [pattern] },
      metadata: { directivity_index: { rear_hemisphere: 'zero_radiation' } },
    } as unknown as ResultPayload;
    const option = polarOption([named('A', result, true)], tokens, 'horizontal', 1_000, -30, 'regular');
    const spec = chartSpecFromOption(option, tokens, { title: 'Polar response' }) as PolarChartSpec;
    expect(spec).toMatchObject({ kind: 'polar', title: 'Polar response', r: { min: -30, max: 0, step: 10, unit: 'dB' } });
    expect(spec.subtitle).toBe('Horizontal · 1.00 kHz');
    expect(spec.shade).toEqual([
      { from: 90, to: 180, label: 'outside modelled half-space' },
      { from: -180, to: -90, label: 'outside modelled half-space' },
    ]);
    expect(spec.series).toHaveLength(1);
    expect(spec.series[0].angle).toEqual(angles);
    // The rear of a zero-radiation run is a gap, not a trace at the centre.
    expect(spec.series[0].r[0]).toBeNull();
    expect(spec.series[0].r[6]).toBe(0);
  });

  it('leaves out a series switched off in the legend', () => {
    const items = [named('A', fixture, true), named('B', SERIES_FIXTURES['separate grids, flat DI with gaps'])];
    const option = splOption(items, tokens, 'none', 'full');
    const spec = chartSpecFromOption(option, tokens, { title: 'SPL', hidden: new Set(['B']) }) as LineChartSpec;
    expect(spec.series.map(({ label }) => label)).toEqual(['A']);
  });

  it('marks the reconstructed points the card marks', () => {
    const adaptive = { ...fixture, frequency_status: fixture.frequencies.map((_, index) => (index % 5 === 2 ? 'interpolated' : 'solved')) } as ResultPayload;
    const spec = chartSpecFromOption(splOption([named('A', adaptive, true)], tokens, 'none', 'full'), tokens, { title: 'SPL' }) as LineChartSpec;
    expect(spec.series[0].markers).toEqual(fixture.frequencies.flatMap((_, index) => (index % 5 === 2 ? [index] : [])));
    const solved = chartSpecFromOption(splOption([named('A', fixture, true)], tokens, 'none', 'full'), tokens, { title: 'SPL' }) as LineChartSpec;
    expect(solved.series[0].markers).toBeUndefined();
  });

  it('sends a missing polar angle as a gap, not as on-axis', () => {
    const option = {
      polar: {},
      radiusAxis: { min: -30, max: 0 },
      series: [{ name: 'A', type: 'line', coordinateSystem: 'polar', data: [[-6, -30], [-3, null], [0, 0]] }],
    } as unknown as EChartsOption;
    const spec = chartSpecFromOption(option, tokens, { title: 'Polar' }) as PolarChartSpec;
    expect(spec.series[0].angle).toEqual([-30, null, 0]);
  });

  it('names colours by role so the export theme owns the hue', () => {
    expect(colorRole(tokens.series[3], tokens, 0)).toBe(3);
    expect(colorRole(tokens.muted, tokens, 0)).toBe('muted');
    expect(colorRole(tokens.foreground, tokens, 0)).toBe('ink');
    expect(colorRole(undefined, tokens, 8)).toBe(2);
    expect(colorRole('#123456', tokens, 0)).toBe('muted');
  });
});

describe('screen axis extents', () => {
  const option = { grid: { left: 40, right: 10, top: 20, bottom: 22 } } as EChartsOption;
  it('reads the data range at the grid edges through the chart mapping', () => {
    const calls: unknown[] = [];
    const instance = {
      getWidth: () => 400,
      getHeight: () => 200,
      // Screen y runs down while values run up, as on a real chart.
      convertFromPixel: (finder: object, pixel: number) => { calls.push([finder, pixel]); return 'yAxisIndex' in finder ? (200 - pixel) * 10 : pixel * 10; },
    };
    expect(screenAxisExtent(instance, option)('x', 0)).toEqual([400, 3_900]);
    expect(screenAxisExtent(instance, option)('y', 1)).toEqual([220, 1_800]);
    expect(calls).toEqual([[{ xAxisIndex: 0 }, 40], [{ xAxisIndex: 0 }, 390], [{ yAxisIndex: 1 }, 178], [{ yAxisIndex: 1 }, 20]]);
  });

  it('falls back to the option when the chart cannot say', () => {
    expect(screenAxisExtent(null, option)('x', 0)).toBeNull();
    expect(screenAxisExtent({ getWidth: () => 1, getHeight: () => 1 }, option)('y', 0)).toBeNull();
    const broken = { getWidth: () => 400, getHeight: () => 200, convertFromPixel: () => Number.NaN };
    expect(screenAxisExtent(broken, option)('y', 0)).toBeNull();
    expect(screenAxisExtent(broken, { grid: { left: '10%' } } as EChartsOption)('x', 0)).toBeNull();
  });
});
