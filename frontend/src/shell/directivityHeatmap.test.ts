import * as echarts from 'echarts';
import { describe, expect, it } from 'vitest';
import { chartDensity, contourPointToPixels, contourPolylines, contourSegments, frequencyBounds, heatmapFrequencyLabel, heatmapOption, interpolateDirectivityGrid, lineOption, smoothContourShape } from './ResultsPanel';
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

  it('uses a bounded live grid, then restores the full-quality completed map', () => {
    const result = payload(60, 37);
    const live = heatmapOption(result, tokens, 'horizontal', -6, 'regular', true) as {
      animationDuration: number;
      xAxis: { data: string[] };
      yAxis: { data: string[] };
      series: Array<{ type: string; data: unknown[] }>;
    };
    const complete = heatmapOption(result, tokens, 'horizontal', -6) as {
      animationDuration: number;
      xAxis: { data: string[] };
      yAxis: { data: string[] };
      series: Array<{ type: string; data: unknown[] }>;
    };
    const liveCells = live.series.find(({ type }) => type === 'heatmap')!.data.length;
    const completeCells = complete.series.find(({ type }) => type === 'heatmap')!.data.length;

    expect(live.animationDuration).toBe(0);
    expect(live.xAxis.data).toHaveLength(119);
    expect(live.yAxis.data).toHaveLength(73);
    expect(liveCells).toBe(8_687);
    expect(complete.animationDuration).toBe(180);
    expect(completeCells).toBeGreaterThan(liveCells * 3);
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

  it('rounds contour paths without letting their control points overshoot the measured bounds', () => {
    expect(smoothContourShape([[12, 8], [30, 2], [48, 14]])).toEqual({
      points: [[12, 8], [30, 2], [48, 14]],
      smooth: 0.5,
      smoothConstraint: [[12, 2], [48, 14]],
    });
  });

  it('projects dense contours continuously instead of snapping them to category centres', () => {
    const contour = interpolateDirectivityGrid(payload(60, 37), 'horizontal', 12, 180_000);
    expect(contour.frequencies).toHaveLength(532);
    expect(contour.angles).toHaveLength(325);
    expect(contourPointToPixels([0, 0], contour, { x: 10, y: 20, width: 532, height: 325 })).toEqual([10.5, 344.5]);
    expect(contourPointToPixels([531, 324], contour, { x: 10, y: 20, width: 532, height: 325 })).toEqual([541.5, 20.5]);
    expect(contourPointToPixels([100.25, 200.75], contour, { x: 10, y: 20, width: 532, height: 325 })[0]).toBe(110.75);
  });

  it('renders contour paths without routing fractional vertices through the ordinal axis', () => {
    const option = heatmapOption(payload(12, 13), tokens, 'horizontal', -6) as { series: Array<Record<string, unknown>> };
    const contour = option.series.find((series) => series.name === '-6 dB contour') as {
      data: number[][];
      renderItem: (params: unknown, api: { value: (index: number) => unknown }) => { children: Array<{ shape: { points: number[][] } }> };
    };
    const datum = contour.data[0];
    // There is deliberately no `api.coord` here. Supplying it would hide the
    // category-axis snapping regression this test protects against.
    const group = contour.renderItem(
      { coordSys: { x: 10, y: 20, width: 400, height: 200 } },
      { value: (index) => datum[index] },
    );
    expect(group.children[0].shape.points.length).toBeGreaterThan(2);
    expect(group.children[0].shape.points.some(([x, y]) => !Number.isInteger(x) || !Number.isInteger(y))).toBe(true);
  });

  it('pins line charts to the exact positive frequencies present in the solve', () => {
    const series = [{ type: 'line' as const, data: [[100, 1], [1_000, 2], [20_000, 3]] }];
    expect(frequencyBounds(series)).toEqual([100, 20_000]);
    expect((lineOption(series, tokens, 'dB').xAxis as { min: number; max: number })).toMatchObject({ min: 100, max: 20_000 });
  });

  it('scales chart chrome to the panel so small cards keep their plot area', () => {
    expect(chartDensity(227, 96)).toBe('compact');
    expect(chartDensity(360, 220)).toBe('regular');
    expect(chartDensity(900, 560)).toBe('full');

    const series = [{ type: 'line' as const, data: [[100, 1], [20_000, 3]] }];
    const grids = (['compact', 'regular', 'full'] as const).map((density) => lineOption(series, tokens, 'dB', density).grid as { top: number; bottom: number; left: number });
    // Every inset grows with the density; none of them shrink.
    expect(grids[0].top + grids[0].bottom + grids[0].left).toBeLessThan(grids[1].top + grids[1].bottom + grids[1].left);
    expect(grids[1].top + grids[1].bottom + grids[1].left).toBeLessThan(grids[2].top + grids[2].bottom + grids[2].left);

    // Axis captions cost more than a compact card can spare, so only the
    // detail view carries them; the card's title chip states the unit instead.
    const compact = lineOption(series, tokens, 'dB', 'compact');
    expect((compact.xAxis as { name?: string }).name).toBeUndefined();
    expect((compact.yAxis as { name?: string }).name).toBeUndefined();
    const full = lineOption(series, tokens, 'dB', 'full');
    expect((full.xAxis as { name?: string }).name).toBe('Frequency [Hz]');
    expect((full.yAxis as { name?: string }).name).toBe('dB');
  });

  it('puts the full-size heatmap angle title in the bottom-left corner', () => {
    const full = heatmapOption(payload(12, 13), tokens, 'horizontal', -6, 'full');
    expect(full.yAxis).toMatchObject({
      name: 'Angle [°]',
      nameLocation: 'start',
      nameTextStyle: { align: 'right', verticalAlign: 'top' },
    });
    expect((heatmapOption(payload(12, 13), tokens, 'horizontal', -6, 'regular').yAxis as { name?: string }).name).toBeUndefined();
  });

  // The chart ground is the interface's panel colour in both themes: a chart
  // surface the app uses nowhere else is what made the plots read as pasted
  // into the window. These values mirror hornlab_plots CONSOLE_THEME and
  // VELLUM_THEME, and the export renders on the same two.
  it('draws on the interface panel colour in both app themes', () => {
    document.documentElement.dataset.theme = 'light';
    expect(readChartTokens()).toMatchObject({ background: '#F1F2ED', foreground: '#1D1F20', accent: '#A5391B' });
    document.documentElement.dataset.theme = 'dark';
    expect(readChartTokens()).toMatchObject({ background: '#211F1D', foreground: '#ECE8E0', accent: '#E0673F' });
    delete document.documentElement.dataset.theme;
  });

  it('reads the map on each theme\'s own ramp, ending on the accent side', () => {
    document.documentElement.dataset.theme = 'dark';
    const console_ = readChartTokens().colormap;
    expect(console_[0]).toBe('#050c18');
    expect(console_.at(-1)).toBe('#c84428');
    document.documentElement.dataset.theme = 'light';
    const vellum = readChartTokens().colormap;
    // Vellum's floor is the page itself, so the quietest part of the map
    // dissolves into it instead of becoming the heaviest thing on the sheet.
    expect(vellum[0]).toBe('#f1f2ed');
    expect(vellum.at(-1)).toBe('#e11414');
    delete document.documentElement.dataset.theme;
  });
});
