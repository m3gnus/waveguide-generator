import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';

const chartRender = vi.hoisted(() => vi.fn());

vi.mock('../results/EChart', () => ({
  EChart: (props: unknown) => {
    chartRender(props);
    return null;
  },
  useChartTokens: () => ({
    foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff',
    series: ['#0ff'], colormap: ['#000', '#fff'],
  }),
}));

const { ResultsChartGrid } = await import('./ResultsPanel');

const tokens: ChartTokens = {
  foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff',
  series: ['#0ff'], colormap: ['#000', '#fff'],
};
const named: NamedResult[] = [];
const result: ResultPayload = {
  frequencies: [500, 1_000],
  impedance: { frequencies: [500, 1_000], real: [1, 2], imaginary: [.2, .4] },
};

describe('live result repaint isolation', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    globalThis.ResizeObserver ??= class { observe() {} disconnect() {} unobserve() {} } as never;
    preferencesStore.resetForTests();
    chartRender.mockClear();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  function render(progress: number, payload = result) {
    const job = { id: 'live-job', progress } as JobItem;
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['impedance'], result: payload, named, tokens, live: true, job,
    })));
  }

  it('does not rebuild a plot for summary-only job progress changes', () => {
    render(.2);
    expect(chartRender).toHaveBeenCalledTimes(1);

    render(.4);
    render(.6);
    expect(chartRender).toHaveBeenCalledTimes(1);

    render(.6, {
      ...result,
      impedance: { frequencies: [500, 1_000], real: [1, 3], imaginary: [.2, .5] },
    });
    expect(chartRender).toHaveBeenCalledTimes(2);
  });
});
