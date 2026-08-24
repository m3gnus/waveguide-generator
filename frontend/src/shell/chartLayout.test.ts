import { describe, expect, it } from 'vitest';
import type { ChartTokens } from '../results/EChart';
import { beamShapeSeries } from '../results/mappers';
import type { NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';
import { chartDensity, directivityIndexOption, heatmapOption, impedanceOption, lineOption, middleEllipsis, type ChartDensity } from './ResultsPanel';

/**
 * Layout locks for the result dock.
 *
 * Every chart in the dock is drawn by ECharts from an option object these
 * functions build, so the plot box, the legend band and the frequency domain
 * are all decided in TypeScript before any canvas exists. That makes them
 * assertable here, in jsdom, without rendering anything -- and it is the only
 * place they can be checked, because the cards these end up in are as short as
 * 100px and a chart whose legend has crept over its own plot still "renders".
 */

const tokens: ChartTokens = {
  foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff',
  series: ['#0ff', '#f90', '#f55'], colormap: ['#000', '#fff'],
};
const DENSITIES: ChartDensity[] = ['compact', 'regular', 'full'];

const payload: ResultPayload = {
  frequencies: [500, 1_000, 2_000],
  di: { frequencies: [500, 1_000, 2_000], di: { horizontal: [3, 5, 7] } },
  impedance: { frequencies: [500, 1_000, 2_000], real: [1, 2, 3], imaginary: [.2, .4, .6] },
  beam_shape: { frequencies: [500, 1_000, 2_000], horizontal_beamwidth_deg: [90, 80, 70], vertical_beamwidth_deg: [60, 55, 50] },
} as ResultPayload;
const items: NamedResult[] = [{ id: 'run', label: 'Run A', result: payload }];

type Inset = { left: number; right: number; top: number; bottom: number };
type LineOption = {
  grid: Inset & { containLabel: boolean };
  legend: { top: number; right: number; type?: string; width?: string; textStyle: { fontSize: number }; formatter: (name: string) => string };
  xAxis: { type: string; logBase: number; min?: number; max?: number };
};

function lineCharts(density: ChartDensity): LineOption[] {
  return [
    lineOption(beamShapeSeries(payload), tokens, 'Beam width [°]', density),
    directivityIndexOption(items, tokens, 'none', density),
    impedanceOption(items, tokens, 'none', density),
  ] as unknown as LineOption[];
}

function expectFunctionalFontsAtLeast11(value: unknown, path = 'option'): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => expectFunctionalFontsAtLeast11(item, `${path}[${index}]`));
    return;
  }
  if (!value || typeof value !== 'object') return;
  Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
    if (key === 'fontSize' && typeof item === 'number') expect(item, `${path}.${key}`).toBeGreaterThanOrEqual(11);
    if (key === 'font' && typeof item === 'string') {
      const pixels = item.match(/(?:^|\s)(\d+(?:\.\d+)?)px(?:[\/\s]|$)/)?.[1];
      if (pixels) expect(Number(pixels), `${path}.${key}=${item}`).toBeGreaterThanOrEqual(11);
    }
    expectFunctionalFontsAtLeast11(item, `${path}.${key}`);
  });
}

describe('result dock chart layout', () => {
  it.each(DENSITIES)('gives every %s line chart the same axes box', (density) => {
    const boxes = lineCharts(density).map(({ grid }) => grid);
    // Beam width, DI and impedance sit side by side in one grid of cards. A
    // margin that drifted on only one of them would misalign the whole row.
    expect(new Set(boxes.map((box) => JSON.stringify(box))).size).toBe(1);
    expect(boxes[0].containLabel).toBe(false);
  });

  it('spends more of the card on chrome only as the card grows', () => {
    const boxes = DENSITIES.map((density) => lineCharts(density)[0].grid);
    (['left', 'right', 'top', 'bottom'] as const).forEach((edge) => {
      expect(boxes[0][edge]).toBeLessThanOrEqual(boxes[1][edge]);
      expect(boxes[1][edge]).toBeLessThanOrEqual(boxes[2][edge]);
    });
    // The tick labels live in the left inset; nothing may reclaim it entirely.
    expect(boxes[0].left).toBeGreaterThan(0);
  });

  it.each(DENSITIES)('keeps the %s legend inside the band reserved above the plot', (density) => {
    const { grid, legend } = lineCharts(density)[0];
    expect(grid.top).toBeGreaterThanOrEqual(legend.top + legend.textStyle.fontSize);
  });

  it('holds the legend clear of the card chrome buttons until the chart is full size', () => {
    // Copy, download, expand and close float in the same top band on a card.
    expect(lineCharts('compact')[0].legend.right).toBe(88);
    expect(lineCharts('regular')[0].legend.right).toBe(88);
    // The detail view has no floating buttons over the plot.
    expect(lineCharts('full')[0].legend.right).toBe(8);
  });

  it('scrolls a compact legend in one row rather than wrapping it over the plot', () => {
    expect(lineCharts('compact')[0].legend).toMatchObject({ type: 'scroll', width: '46%' });
    expect(lineCharts('full')[0].legend.type).toBeUndefined();
  });

  it('truncates legend entries from the middle so the run version survives', () => {
    const format = lineCharts('compact')[0].legend.formatter;
    expect(format('260308tritonia-q_v02')).toBe(middleEllipsis('260308tritonia-q_v02', 12));
    expect(format('260308tritonia-q_v02')).toContain('v02');
    expect(format('260308tritonia-q_v03')).not.toBe(format('260308tritonia-q_v02'));
  });

  it('pins the log frequency axis to the swept range', () => {
    const { xAxis } = lineCharts('full')[0];
    expect(xAxis).toMatchObject({ type: 'log', logBase: 10, min: 500, max: 2_000 });
  });

  it('leaves the frequency axis unpinned when a chart has no plottable data', () => {
    const { xAxis } = lineOption([], tokens, 'dB', 'full') as unknown as LineOption;
    expect(xAxis.min).toBeUndefined();
    expect(xAxis.max).toBeUndefined();
  });

  it.each(DENSITIES)('keeps every functional %s ECharts label at the 11 px floor', (density) => {
    const heatmapResult = {
      frequencies: [500, 1_000, 2_000],
      directivity: {
        horizontal: [500, 1_000, 2_000].map(() => [0, 30, 60, 90].map((angle) => [angle, -angle / 10])),
      },
    } as unknown as ResultPayload;
    const heatmap = heatmapOption(heatmapResult, tokens, 'horizontal', -6, density);
    [...lineCharts(density), heatmap as unknown as LineOption]
      .forEach((option, index) => expectFunctionalFontsAtLeast11(option, `option[${index}]`));

    // Custom contour labels live in renderItem output rather than directly in
    // the option tree, so exercise those generated display objects too.
    (heatmap.series as Array<{
      type?: string;
      data?: number[][];
      renderItem?: (params: unknown, api: { value: (index: number) => unknown }) => unknown;
    }>).filter(({ renderItem, data }) => renderItem && data?.length).forEach((series, index) => {
      const datum = series.data![0];
      const rendered = series.renderItem!(
        { coordSys: { x: 0, y: 0, width: 400, height: 200 } },
        { value: (itemIndex) => datum[itemIndex] },
      );
      expectFunctionalFontsAtLeast11(rendered, `renderItem[${index}]`);
    });
  });
});

describe('chart density thresholds', () => {
  it('drops a short card to compact however wide it is', () => {
    // A six-panel dock card is barely 100px tall.
    expect(chartDensity(1_200, 100)).toBe('compact');
    expect(chartDensity(1_200, 171)).toBe('compact');
    expect(chartDensity(1_200, 172)).toBe('regular');
    expect(chartDensity(1_200, 299)).toBe('regular');
    expect(chartDensity(1_200, 300)).toBe('full');
  });

  it('drops a narrow card to compact however tall it is', () => {
    expect(chartDensity(299, 900)).toBe('compact');
    expect(chartDensity(300, 900)).toBe('regular');
    expect(chartDensity(439, 900)).toBe('regular');
    expect(chartDensity(440, 900)).toBe('full');
  });
});
