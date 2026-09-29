import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';
import { ResultsChartGrid } from './ResultsPanel';

const charts = vi.hoisted(() => ({ drawn: [] as Array<{ label: string; option: unknown }> }));
vi.mock('../results/EChart', async (importOriginal) => ({
  ...await importOriginal<typeof import('../results/EChart')>(),
  EChart: ({ option, label }: { option: unknown; label: string }) => {
    charts.drawn.push({ label, option });
    return <div data-chart-label={label}/>;
  },
}));

const tokens: ChartTokens = {
  foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222',
  accent: '#0ff', series: ['#0ff', '#f90', '#f55'], colormap: ['#000', '#fff'],
};
const planes = ['horizontal', 'vertical', 'diagonal'];
function run(id: string, label: string, edge: number): NamedResult {
  const result: ResultPayload = {
    frequencies: [500, 1_000],
    directivity: Object.fromEntries(planes.map((plane) => [plane, [
      [[0, 0], [90, edge]], [[0, 0], [90, edge - 2]],
    ]])),
  };
  return { id, label, result };
}

interface ContourSeries {
  name: string;
  data: number[][];
  renderItem: (
    params: { coordSys: { x: number; y: number; width: number; height: number } },
    api: { value: (index: number) => number },
  ) => { children: Array<{ type: string; style?: { text?: string } }> };
}
function labelText(option: unknown, name: string): string[] {
  const series = (option as { series: ContourSeries[] }).series.find((entry) => entry.name === name)!;
  expect(series).toBeDefined();
  return series.data.flatMap((datum) => series.renderItem(
    { coordSys: { x: 0, y: 0, width: 400, height: 300 } },
    { value: (index) => datum[index] },
  ).children.filter(({ type }) => type === 'text').map(({ style }) => style?.text ?? ''));
}

describe('rendered directivity comparison maps', () => {
  let host: HTMLDivElement;
  let root: Root;
  const named = [run('primary', 'Primary run', -12), run('reference-a', 'Reference A', -10), run('reference-b', 'Reference B', -11)];

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    preferencesStore.resetForTests();
    charts.drawn.length = 0;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it('passes the contour-name preference from the chart card to its map', () => {
    act(() => root.render(<ResultsChartGrid chartTypes={['directivity_map_h']} result={named[0].result} named={named} tokens={tokens}/>));
    const hidden = charts.drawn.at(-1)!.option;
    expect(labelText(hidden, 'Primary run')).toEqual(['-6 dB']);
    expect(labelText(hidden, 'Reference A')).toEqual([]);

    act(() => preferencesStore.update({ showContourNames: true }));
    const shown = charts.drawn.at(-1)!.option;
    expect(labelText(shown, 'Primary run')).toEqual(['Primary run']);
    expect(labelText(shown, 'Reference A')).toEqual(['Reference A']);
  });

  it('hides names in every panel, keeps level labels, and shows one legend on the first panel with references', () => {
    act(() => root.render(<ResultsChartGrid chartTypes={['directivity_map']} result={named[0].result} named={named} tokens={tokens}/>));
    expect(host.querySelectorAll('.directivity-multiplane > div')).toHaveLength(3);
    const drawn = charts.drawn.filter(({ label }) => label.startsWith('Interactive ') && label.includes('directivity heatmap'));
    expect(drawn.map(({ label }) => label)).toEqual(planes.map((plane) => `Interactive ${plane} directivity heatmap with comparison contours`));
    drawn.forEach(({ option }, index) => {
      expect((option as { legend?: { data: string[] } }).legend?.data).toEqual(index === 0 ? named.map(({ label }) => label) : undefined);
      expect(labelText(option, 'Primary run')).toEqual(['-6 dB']);
      expect(labelText(option, 'Reference A')).toEqual([]);
      expect(labelText(option, 'Reference B')).toEqual([]);
      expect(labelText(option, '-3 dB contour')).toEqual(['-3 dB']);
      expect(labelText(option, '-12 dB contour')).toEqual(['-12 dB']);
    });
  });
});
