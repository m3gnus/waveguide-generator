import * as echarts from 'echarts';
import { describe, expect, it } from 'vitest';
import { heatmapFrequencyLabel, heatmapOption } from './ResultsPanel';
import type { ChartTokens } from '../results/EChart';
import type { ResultPayload } from '../results/types';

const tokens: ChartTokens = {
  foreground: '#d8dde8', muted: '#7f8796', grid: '#1a212d', gridMinor: '#141a24', accent: '#4aa3df',
  series: ['#4aa3df', '#df8b4a'], colormap: ['#0b1d33', '#1a4673', '#2f7ab8', '#59a7d8', '#8fc9e8', '#bfe0f2', '#e2f1fa', '#ffffff'],
};

/** A solve-shaped payload: log-spaced sweep, 5° polar grid, one plane. */
function payload(): ResultPayload {
  const frequencies = Array.from({ length: 40 }, (_, index) => 400 * (40 ** (index / 39)));
  const angles = Array.from({ length: 37 }, (_, index) => index * 5);
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
  it('draws every polar cell instead of emitting empty paths', () => {
    const { drawn, empty } = renderedCells(heatmapOption(payload(), tokens, 'horizontal', -6));
    expect(empty).toBe(0);
    expect(drawn).toBe(40 * 37);
  });

  it('uses category axes on both dimensions, which is what echarts requires', () => {
    const option = heatmapOption(payload(), tokens, 'horizontal', -6) as {
      xAxis: { type: string; data: string[] }; yAxis: { type: string; data: string[] };
    };
    expect(option.xAxis.type).toBe('category');
    expect(option.yAxis.type).toBe('category');
    expect(option.xAxis.data).toHaveLength(40);
    expect(option.yAxis.data).toHaveLength(37);
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
});
