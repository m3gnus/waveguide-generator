import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import * as resultsApi from '../api/results';
import { resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
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
  useChartTokens: () => tokens,
}));

const { ResultsChartGrid, ResultsPanel } = await import('./ResultsPanel');

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
    resultsApi.compareSelection.clear();
    vi.restoreAllMocks();
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

  it('keeps mounted ResultsPanel charts stable while an unrelated run progresses', async () => {
    resetDesignStore();
    workspaceModeStore.setMode('parametric');
    preferencesStore.update({ chartTypes: ['impedance'] });
    const displayed = {
      id: 'shown', run_number: 1, label: 'Shown', status: 'complete', progress: 1,
      created_at: '2026-08-08T00:00:00Z', config_summary: {}, solve_options: {},
      has_results: true, script_snapshot: { version: 1, design: serializeDesign(useDesignStore.getState().design) },
      task_metadata: {}, exported_files: [], log_tail: [],
    } as unknown as JobItem;
    const background = { ...displayed, id: 'background', status: 'running', has_results: false } as JobItem;
    const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
    const publish = (progress: number, label = displayed.label) => {
      manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, error: null,
        jobs: [label === displayed.label ? displayed : { ...displayed, label }, { ...background, progress }] };
      manager.listeners.forEach(listener => listener());
    };
    vi.spyOn(resultsApi, 'fetchJobResults').mockResolvedValue(result as resultsApi.JobResults);
    resultsApi.compareSelection.setPrimary('shown');
    publish(0);
    await act(async () => root.render(createElement(ResultsPanel)));
    const initial = chartRender.mock.calls.length;
    expect(initial).toBeGreaterThan(0);
    for (let n = 1; n <= 10; n += 1) await act(async () => publish(n / 10));
    expect(chartRender).toHaveBeenCalledTimes(initial);
    await act(async () => publish(1, 'Renamed'));
    expect(chartRender.mock.calls.length).toBeGreaterThan(initial);
  });
});
