import { describe, expect, it } from 'vitest';
import type { ChartTokens } from '../results/EChart';
import { hasZeroRadiationRear, OUTSIDE_HALF_SPACE_LABEL, type NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';
import { heatmapOption, maskRearHemisphere, interpolateDirectivityGrid, polarOption } from './ResultsPanel';

const tokens: ChartTokens = { foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff', series: ['#0ff'], colormap: ['#000', '#fff'] };

// A legacy infinite-baffle run: 0-180 samples, the rear rows holding the floor.
const ANGLES = [0, 30, 60, 90, 120, 150, 180];
const LEVELS = [0, -2, -6, -12, -120, -120, -120];
function payload(rear: 'zero_radiation' | 'sampled' | undefined, angles = ANGLES, levels = LEVELS): ResultPayload {
  const row = angles.map((angle, index) => [angle, 10 ** (levels[index] / 20)]);
  return {
    frequencies: [500, 1_000],
    directivity: { horizontal: [row, row] },
    metadata: rear ? { directivity_index: { rear_hemisphere: rear } } : {},
  } as unknown as ResultPayload;
}
const named = (result: ResultPayload): NamedResult => ({ id: 'a', label: 'Run A', result });

type Cell = [number, number, number, number, number, number];
const heatmapCells = (result: ResultPayload) => {
  const option = heatmapOption(result, tokens, 'horizontal', -6, 'full', false, 10);
  const series = option.series as Array<{ type: string; name?: string; data: unknown[] }>;
  return { series, cells: series[0].data as Cell[] };
};

describe('zero-radiation rear hemisphere', () => {
  it('reads the flag only from directivity_index.rear_hemisphere', () => {
    expect(hasZeroRadiationRear(payload('zero_radiation'))).toBe(true);
    expect(hasZeroRadiationRear(payload('sampled'))).toBe(false);
    expect(hasZeroRadiationRear(payload(undefined))).toBe(false);
    expect(hasZeroRadiationRear({ metadata: { rear_hemisphere: 'zero_radiation' } })).toBe(false);
  });

  it('draws no heatmap cell past 90 degrees and shades the band with its label', () => {
    const { series, cells } = heatmapCells(payload('zero_radiation'));
    expect(cells.length).toBeGreaterThan(0);
    // Column 5 of a cell is its angle.
    expect(Math.max(...cells.map((cell) => cell[5]))).toBeLessThanOrEqual(90);
    expect(series.some(({ name }) => name === OUTSIDE_HALF_SPACE_LABEL)).toBe(true);
  });

  it('leaves a sampled result untouched: floor cells drawn, no band', () => {
    const { series, cells } = heatmapCells(payload('sampled'));
    expect(Math.max(...cells.map((cell) => cell[5]))).toBe(180);
    expect(series.some(({ name }) => name === OUTSIDE_HALF_SPACE_LABEL)).toBe(false);
  });

  it('a new 0-90 result draws every cell and needs no band', () => {
    const { series, cells } = heatmapCells(payload('zero_radiation', [0, 30, 60, 90], [0, -2, -6, -12]));
    expect(Math.max(...cells.map((cell) => cell[5]))).toBe(90);
    expect(series.some(({ name }) => name === OUTSIDE_HALF_SPACE_LABEL)).toBe(false);
  });

  it('masks the rear rows of the contour grid so no false edge is contoured at 90 degrees', () => {
    const result = payload('zero_radiation');
    const raw = interpolateDirectivityGrid(result, 'horizontal', 4);
    const masked = maskRearHemisphere(raw, result);
    masked.angles.forEach((angle, row) => {
      if (angle > 90) expect(masked.values[row].every((value) => value === null)).toBe(true);
      else expect(masked.values[row]).toEqual(raw.values[row]);
    });
  });

  it('leaves a gap in the polar trace instead of collapsing it to the floor', () => {
    const series = polarOption([named(payload('zero_radiation'))], tokens, 'horizontal', 1_000, -30, 'full').series as Array<{ name: string; data: Array<[number | null, number]> }>;
    const trace = series.find(({ name }) => name === 'Run A')!;
    expect(trace.data.filter(([, angle]) => angle > 90).map(([radius]) => radius)).toEqual([null, null, null]);
    expect(trace.data.filter(([, angle]) => angle <= 90).every(([radius]) => radius !== null)).toBe(true);
  });

  it('shades both rear wedges and labels them in the polar chart', () => {
    const option = polarOption([named(payload('zero_radiation'))], tokens, 'horizontal', 1_000, -30, 'full');
    const wedges = (option.series as Array<{ name: string }>).filter(({ name }) => name === OUTSIDE_HALF_SPACE_LABEL);
    expect(wedges).toHaveLength(2);
    expect(JSON.stringify(option.graphic)).toContain(OUTSIDE_HALF_SPACE_LABEL);
    expect((option.legend as { data?: string[] }).data).toEqual(['Run A']);
  });

  it('draws a sampled result exactly as before', () => {
    const option = polarOption([named(payload('sampled'))], tokens, 'horizontal', 1_000, -30, 'full');
    const series = option.series as Array<{ name: string; data: Array<[number | null, number]> }>;
    expect(series).toHaveLength(1);
    expect(option.graphic).toBeUndefined();
    // Nothing is blanked: every rear sample keeps a plotted radius.
    expect(series[0].data.filter(([, angle]) => angle > 90).every(([radius]) => radius !== null)).toBe(true);
  });
});
