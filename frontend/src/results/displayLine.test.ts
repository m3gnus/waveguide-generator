import type { EChartsOption } from 'echarts';
import { describe, expect, it } from 'vitest';
import { hermiteSamples, withDisplayLines } from './displayLine';

type Series = { name?: string; data: Array<Array<number | null>>; silent?: boolean; tooltip?: { show: boolean }; lineStyle?: Record<string, unknown>; markArea?: unknown };

const sweep = [100, 200, 400, 800, 1_600, 3_200];
const levels = [80, 84, 86, 85, 88, 83];

function option(series: unknown[], xAxis: Record<string, unknown> = { type: 'log' }): EChartsOption {
  return { xAxis, yAxis: { type: 'value' }, series } as EChartsOption;
}

describe('smooth display lines', () => {
  it('passes through every solved point and never through a corner', () => {
    const samples = hermiteSamples([0, 1, 2, 3], [0, 1, 0, 1], 8);
    expect(samples).toHaveLength(3 * 8 + 1);
    [0, 8, 16, 24].forEach((index, point) => expect(samples[index]).toEqual([point, [0, 1, 0, 1][point]]));
  });

  it('draws a visible curve and keeps the solved series, hidden, for the tooltip', () => {
    const solved = { name: 'Run A', type: 'line', data: sweep.map((frequency, index) => [frequency, levels[index]]), lineStyle: { color: '#f00', width: 2 }, markArea: { data: [] } };
    const [curve, twin] = withDisplayLines(option([solved])).series as Series[];
    // One name, so the legend toggles both; the curve first, so the legend key is visible.
    expect(curve.name).toBe('Run A');
    expect(twin.name).toBe('Run A');
    expect(curve).toMatchObject({ silent: true, tooltip: { show: false }, lineStyle: { color: '#f00', width: 2, cap: 'round', join: 'round' } });
    expect(curve.markArea).toBeUndefined();
    expect(curve.data.length).toBeGreaterThan(solved.data.length * 4);
    // Interpolated in log frequency: the midpoint of an octave sits at its geometric centre.
    expect(curve.data[4][0]).toBeCloseTo(Math.sqrt(100 * 200));
    expect(twin).toMatchObject({ data: solved.data, lineStyle: { opacity: 0 }, markArea: { data: [] } });
    expect(twin.tooltip).toBeUndefined();
    // Every solved point lies on the drawn curve.
    solved.data.forEach(([frequency, level]) => {
      const drawn = curve.data.find(([x]) => Math.abs((x as number) - (frequency as number)) < 1e-9);
      expect(drawn?.[1]).toBeCloseTo(level as number, 9);
    });
  });

  it('keeps a gap as a gap', () => {
    const data = [[100, 1], [200, 2], [300, 3], [400, null], [500, 5], [600, 6], [700, 7]];
    const [curve] = withDisplayLines(option([{ name: 'gap', type: 'line', data }])).series as Series[];
    expect(curve.data.filter(([, value]) => value === null)).toEqual([[400, null]]);
  });

  it('smooths a polar trace over angle and keeps it inside the dial', () => {
    const angles = Array.from({ length: 13 }, (_value, index) => -90 + index * 15);
    const data = angles.map((angle) => [Math.max(-30, -Math.abs(angle) / 2), angle]);
    const polar = { radiusAxis: { min: -30, max: 0 }, series: [{ name: 'Run A', type: 'line', coordinateSystem: 'polar', data }] } as EChartsOption;
    const [curve, twin] = withDisplayLines(polar).series as Series[];
    expect(twin.data).toBe(data);
    expect(curve.data.length).toBeGreaterThan(data.length * 4);
    expect(curve.data.every(([radius]) => (radius as number) <= 0 && (radius as number) >= -30)).toBe(true);
  });

  it('leaves alone what it must not smooth: areas, steps, linear and category axes, long traces', () => {
    const data = sweep.map((frequency, index) => [frequency, levels[index]]);
    const untouched = option([
      { name: 'area', type: 'line', areaStyle: {}, data },
      { name: 'step', type: 'line', step: 'end', data },
    ]);
    expect(withDisplayLines(untouched)).toBe(untouched);
    const category = option([{ name: 'c', type: 'line', data }], { type: 'category' });
    expect(withDisplayLines(category)).toBe(category);
    // A bore profile on a linear axis has real corners.
    const profile = option([{ name: 'H', type: 'line', data }], { type: 'value' });
    expect(withDisplayLines(profile)).toBe(profile);
    const long = option([{ name: 'measured', type: 'line', data: Array.from({ length: 2_000 }, (_value, index) => [20 + index * 10, index % 7]) }]);
    expect(withDisplayLines(long)).toBe(long);
  });
});
