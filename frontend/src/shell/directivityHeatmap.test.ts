import * as echarts from 'echarts';
import { describe, expect, it } from 'vitest';
import { contourPolylines, contourSegments, frequencyBounds, heatmapFrequencyLabel, heatmapOption, interpolateDirectivityGrid, lineOption } from './ResultsPanel';
import { readChartTokens, type ChartTokens } from '../results/EChart';
import type { ResultPayload } from '../results/types';

const tokens: ChartTokens = {
  foreground: '#d8dde8', muted: '#7f8796', grid: '#1a212d', gridMinor: '#141a24', accent: '#4aa3df',
  series: ['#4aa3df', '#df8b4a'], colormap: ['#0b1d33', '#1a4673', '#2f7ab8', '#59a7d8', '#8fc9e8', '#bfe0f2', '#e2f1fa', '#ffffff'],
};

/** A solve-shaped payload: log-spaced sweep, 5° polar grid, one plane. */
function payload(frequencyCount = 40, angleCount = 37): ResultPayload {
  const frequencies = Array.from({ length: frequencyCount }, (_, index) => 400 * (40 ** (index / Math.max(1, frequencyCount - 1))));
  const angles = Array.from({ length: angleCount }, (_, index) => index * (180 / Math.max(1, angleCount - 1)));
  return {
    frequencies,
    directivity: {
      horizontal: frequencies.map(() => angles.map((angle) => [angle, -(angle / 6)] as [number, number])),
    },
  } as unknown as ResultPayload;
}

/**
 * ECharts refuses to draw cartesian heatmap cells unless both axes are category
 * axes. It only says so in development: the guard is compiled out of production
 * builds, where every cell is instead emitted as an empty path and the chart
 * goes blank with nothing logged. Render the real option and count the cells
 * rather than trusting the option shape.
 */
function renderedCells(option: ReturnType<typeof heatmapOption>) {
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 289, height: 185 });
  chart.setOption(option);
  const svg = chart.renderToSVGString();
  chart.dispose();
  const paths = svg.match(/<path[^>]*>/g) ?? [];
  return {
    drawn: paths.filter((path) => /\sd="M/.test(path) && /ecmeta_series_index/.test(path)).length,
    empty: paths.filter((path) => /\sd=""/.test(path) && /ecmeta_series_index/.test(path)).length,
  };
}

describe('directivity heatmap', () => {
  it('draws a four-times denser interpolated field instead of empty paths', () => {
    const dense = interpolateDirectivityGrid(payload(12, 13), 'horizontal');
    const { drawn, empty } = renderedCells(heatmapOption(payload(12, 13), tokens, 'horizontal', -6));
    expect(empty).toBe(0);
    expect(dense.factor).toBe(4);
    expect(dense.frequencies).toHaveLength(45);
    expect(dense.angles).toHaveLength(49);
    expect(drawn).toBeGreaterThanOrEqual(dense.frequencies.length * dense.angles.length);
  });

  it('uses category axes on both dimensions, which is what echarts requires', () => {
    const option = heatmapOption(payload(), tokens, 'horizontal', -6) as {
      xAxis: { type: string; data: string[] }; yAxis: { type: string; data: string[] };
    };
    expect(option.xAxis.type).toBe('category');
    expect(option.yAxis.type).toBe('category');
    expect(option.xAxis.data).toHaveLength(157);
    expect(option.yAxis.data).toHaveLength(145);
  });

  it('renders nothing but still builds a valid option for a plane with no data', () => {
    const { drawn, empty } = renderedCells(heatmapOption(payload(), tokens, 'vertical', -6));
    expect(drawn + empty).toBe(0);
  });

  it('labels frequencies compactly on the category axis', () => {
    expect(heatmapFrequencyLabel(400)).toBe('400');
    expect(heatmapFrequencyLabel(1000)).toBe('1k');
    expect(heatmapFrequencyLabel(16000)).toBe('16k');
    expect(heatmapFrequencyLabel(1258.9)).toBe('1.26k');
  });

  it('builds contour segments and overlays labeled reference series', () => {
    expect(contourSegments([
      [0, -10],
      [0, -10],
    ], -6)).toHaveLength(1);
    const option = heatmapOption(payload(), tokens, 'horizontal', -6) as { series: Array<{ name?: string }> };
    expect(option.series.map((series) => series.name)).toEqual(expect.arrayContaining(['-3 dB contour', '-6 dB contour', '-12 dB contour']));
  });

  it('joins adjacent contour fragments into smoothable continuous paths', () => {
    expect(contourPolylines([
      [0, 0, 1, 1],
      [2, 1, 1, 1],
      [2, 1, 3, 0],
    ])).toEqual([[[0, 0], [1, 1], [2, 1], [3, 0]]]);
  });

  it('pins line charts to the exact positive frequencies present in the solve', () => {
    const series = [{ type: 'line' as const, data: [[100, 1], [1_000, 2], [20_000, 3]] }];
    expect(frequencyBounds(series)).toEqual([100, 20_000]);
    expect((lineOption(series, tokens, 'dB').xAxis as { min: number; max: number })).toMatchObject({ min: 100, max: 20_000 });
  });

  it('uses a light HornLab-compatible chart surface with the light app theme', () => {
    document.documentElement.dataset.theme = 'light';
    expect(readChartTokens()).toMatchObject({ background: '#FFFDF8', foreground: '#1A1A1A', accent: '#1F5FBF' });
    document.documentElement.dataset.theme = 'dark';
    expect(readChartTokens()).toMatchObject({ background: '#0F1927', foreground: '#C8D8EC', accent: '#4FC3F7' });
    delete document.documentElement.dataset.theme;
  });
});
