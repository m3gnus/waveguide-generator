import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore, usePreferences, type ChartType } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { SummaryContext, SummaryGroup } from '../results/summary';
import type { ResultPayload } from '../results/types';
import { resultFrequencyValidity } from '../results/validity';
import { designForFamily, serializeDesign } from '../stores/design';
import { beamShapeMissingReason, COMPARABLE_CHARTS, comparisonContourPointToPixels, directivityIndexOption, directivityMapPanels, heatmapOption, impedanceOption, ResultsChartGrid, resolvedPolarStepNotice, resultExportSnapshot, resultLayoutClass } from './ResultsPanel';

const summaryMocks = vi.hoisted(() => ({
  groups: vi.fn<(context: SummaryContext) => SummaryGroup[]>(() => []),
  text: vi.fn<(groups: SummaryGroup[]) => string>(() => ''),
}));
vi.mock('../results/summary', () => ({ summaryGroups: summaryMocks.groups, summaryText: summaryMocks.text }));

const tokens: ChartTokens = { foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff', series: ['#0ff'], colormap: ['#000', '#fff'] };
const result = { frequencies: [], metadata: {} };
function named(id: string, label: string, payload: ResultPayload): NamedResult {
  return { id, label, result: payload };
}
function ResultsHarness() {
  const preferences = usePreferences();
  return createElement(ResultsChartGrid, { chartTypes: preferences.chartTypes, result, named: [], tokens });
}

describe('result comparison charts', () => {
  const comparisonTokens: ChartTokens = { ...tokens, series: ['#0ff', '#f90', '#f55'] };
  const primary = named('primary', 'Run A', {
    frequencies: [500, 1_000],
    directivity: {
      horizontal: [[[0, 0]], [[0, 0]]],
      vertical: [[[0, 0]], [[0, 0]]],
      diagonal: [[[0, 0]], [[0, 0]]],
    },
    di: { frequencies: [500, 1_000], di: { horizontal: [3, 5], vertical: [2, 4] } },
    impedance: { frequencies: [500, 1_000], real: [1, 2], imaginary: [.2, .4] },
  });
  const overlay = named('overlay', 'Run B', {
    frequencies: [500, 1_000],
    directivity: { horizontal: [[[0, 0]], [[0, 0]]] },
    di: { frequencies: [500, 1_000], di: { horizontal: [4, 6], vertical: [3, 5] } },
    impedance: { frequencies: [500, 1_000], real: [2, 3], imaginary: [.3, .5] },
  });
  const items = [primary, overlay];

  it('enables comparison for SPL, every directivity map, DI, and impedance', () => {
    expect([...COMPARABLE_CHARTS]).toEqual([
      'frequency_response', 'directivity_map_h', 'directivity_map_v',
      'directivity_map_d', 'directivity_map', 'directivity_index', 'impedance',
    ]);
  });

  it('builds one heatmap per plane with runs assigned as contour overlays', () => {
    expect(directivityMapPanels(items, 'directivity_map_h').map(({ plane, primaryLabel, references, hasData }) => ({ plane, primaryLabel, references: references.map(({ label }) => label), hasData }))).toEqual([
      { plane: 'horizontal', primaryLabel: 'Run A', references: ['Run B'], hasData: true },
    ]);
    expect(directivityMapPanels(items, 'directivity_map_d').map(({ plane, primaryLabel, references, hasData }) => ({ plane, primaryLabel, references: references.map(({ label }) => label), hasData }))).toEqual([
      { plane: 'diagonal', primaryLabel: 'Run A', references: [], hasData: true },
    ]);
    expect(directivityMapPanels(items, 'directivity_map').map(({ plane, primaryLabel, references, hasData }) => ({ plane, primaryLabel, references: references.map(({ label }) => label), hasData }))).toEqual([
      { plane: 'horizontal', primaryLabel: 'Run A', references: ['Run B'], hasData: true },
      { plane: 'vertical', primaryLabel: 'Run A', references: [], hasData: true },
      { plane: 'diagonal', primaryLabel: 'Run A', references: [], hasData: true },
    ]);
  });

  it('draws comparison runs in the primary heatmap as labelled contour series', () => {
    const map = (edge: number): ResultPayload => ({
      frequencies: [500, 1_000],
      directivity: { horizontal: [
        [[0, 0], [90, edge]],
        [[0, 0], [90, edge - 2]],
      ] },
    });
    const option = heatmapOption(map(-12), comparisonTokens, 'horizontal', -6, 'full', false, 10, {
      primaryLabel: 'Run A',
      references: [{ label: 'Run B', result: map(-10) }],
    });
    const series = option.series as Array<{ name?: string; type?: string }>;
    expect((option.legend as { data: string[] }).data).toEqual(['Run A', 'Run B']);
    expect(series.filter(({ name }) => name === 'Run A' || name === 'Run B').map(({ name, type }) => ({ name, type }))).toEqual([
      { name: 'Run A', type: 'custom' },
      { name: 'Run B', type: 'custom' },
    ]);
  });

  it('projects a comparison contour by physical frequency instead of stretching its range', () => {
    const display = { frequencies: [100, 1_000, 10_000], angles: [0, 45, 90], values: [], factor: 1 };
    const source = { frequencies: [1_000, 10_000], angles: [0, 90], values: [], factor: 1 };
    const [x, y] = comparisonContourPointToPixels([0, 0], source, display, { x: 0, y: 0, width: 300, height: 300 });
    expect(x).toBeCloseTo(150);
    expect(y).toBe(250);
  });

  it('overlays DI with metric colours and run-specific solid/dashed lines', () => {
    const series = directivityIndexOption(items, comparisonTokens, 'none', 'full').series as Array<{
      name: string; lineStyle: { color: string; type: string }; data: number[][];
    }>;
    expect(series.map(({ name }) => name)).toEqual([
      'Run A · horizontal', 'Run A · vertical', 'Run B · horizontal', 'Run B · vertical',
    ]);
    expect(series[0].lineStyle).toMatchObject({ color: '#0ff', type: 'solid' });
    expect(series[1].lineStyle).toMatchObject({ color: '#f90', type: 'solid' });
    expect(series[2].lineStyle).toMatchObject({ color: '#0ff', type: 'dashed' });
    expect(series[3].lineStyle).toMatchObject({ color: '#f90', type: 'dashed' });
    expect(series[2].data).toEqual([[500, 4], [1_000, 6]]);
  });

  it('overlays impedance with one colour per run and solid Re/dashed Im traces', () => {
    const series = impedanceOption(items, comparisonTokens, 'none', 'full').series as Array<{
      name: string; lineStyle: { color: string; type: string }; data: number[][];
    }>;
    expect(series.map(({ name }) => name)).toEqual(['Run A · Re', 'Run A · Im', 'Run B · Re', 'Run B · Im']);
    expect(series.map(({ lineStyle }) => lineStyle)).toEqual([
      expect.objectContaining({ color: '#0ff', type: 'solid' }),
      expect.objectContaining({ color: '#0ff', type: 'dashed' }),
      expect.objectContaining({ color: '#f90', type: 'solid' }),
      expect.objectContaining({ color: '#f90', type: 'dashed' }),
    ]);
  });

});

describe('result frequency validity joins', () => {
  it('uses missing channel membership only when the wrapper join is provably unambiguous', () => {
    const channel: ResultPayload = { frequencies: [100, 2_000] };
    const singleChannelWrapper: ResultPayload = {
      frequencies: [],
      channels: { drive: channel },
      metadata: { per_source_frequency_validity: {
        source: { effective_max_valid_frequency_hz: 1_000 },
        second: { effective_max_valid_frequency_hz: 1_500 },
      } },
    };
    const oneSourceWrapper: ResultPayload = {
      frequencies: [],
      channels: { drive: channel, other: { frequencies: [] } },
      metadata: { per_source_frequency_validity: { source: { effective_max_valid_frequency_hz: 1_000 } } },
    };
    const ambiguousWrapper: ResultPayload = {
      ...oneSourceWrapper,
      metadata: { per_source_frequency_validity: {
        source: { effective_max_valid_frequency_hz: 1_000 },
        other: { effective_max_valid_frequency_hz: 500 },
      } },
    };

    expect(resultFrequencyValidity(channel, singleChannelWrapper)?.sources.map(({ sourceId }) => sourceId))
      .toEqual(['source', 'second']);
    expect(resultFrequencyValidity(channel, oneSourceWrapper)?.sources.map(({ sourceId }) => sourceId))
      .toEqual(['source']);
    expect(resultFrequencyValidity(channel, ambiguousWrapper)).toBeNull();
  });
});

describe('Directivity Map resolved grid label', () => {
  it('shows a resolved step only when count-based sampling changed the request', () => {
    expect(resolvedPolarStepNotice({
      frequencies: [],
      metadata: { polar_grid: { requested_step: 7, resolved_step: 7.2 } },
    })).toBe('7.2° resolved (requested 7°)');
    expect(resolvedPolarStepNotice({
      frequencies: [],
      metadata: { polar_grid: { requested_step: 5, resolved_step: 5 } },
    })).toBeNull();
  });
});

describe('result export design snapshot', () => {
  it('uses the selected job snapshot and revision rather than the live editor design', () => {
    const solved = designForFamily('OSSE');
    solved.L = 321;
    const snapshot = resultExportSnapshot({
      design_revision: 47,
      script_snapshot: { version: 1, design: serializeDesign(solved) },
    });
    expect(snapshot.design?.formula).toBe('OSSE');
    expect(snapshot.design?.L).toBe(321);
    expect(snapshot.designRevision).toBe(47);
  });

  it('does not substitute the live design when an old job has no readable snapshot', () => {
    expect(resultExportSnapshot({ design_revision: 12, script_snapshot: null })).toEqual({
      design: undefined,
      designRevision: 12,
    });
  });
});

describe('results chart layouts', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    preferencesStore.resetForTests();
    summaryMocks.groups.mockReset().mockReturnValue([]);
    summaryMocks.text.mockReset().mockReturnValue('');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  it.each([1, 2, 3, 4, 6])('renders the %i-panel layout with the matching number of cards', (count) => {
    const chartTypes = Array.from({ length: count }, () => 'summary' as ChartType);
    act(() => root.render(createElement(ResultsChartGrid, { chartTypes, result, named: [], tokens })));
    expect(host.querySelector('.result-grid')?.classList.contains(resultLayoutClass(count))).toBe(true);
    expect(host.querySelectorAll('.result-card')).toHaveLength(count);
  });

  it('lets a panel select and persist a diagonal-only directivity heatmap', () => {
    preferencesStore.update({ chartTypes: ['summary'] });
    act(() => root.render(createElement(ResultsHarness)));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Panel 1 chart type"]')!;
    expect([...select.options].find(({ value }) => value === 'directivity_map_d')?.text).toBe('Directivity Map (Diagonal)');

    act(() => {
      select.value = 'directivity_map_d';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(preferencesStore.getSnapshot().chartTypes).toEqual(['directivity_map_d']);
    expect(host.querySelector('.result-title b')?.textContent).toBe('Directivity Diagonal');
    expect(host.querySelector('.chart-stub')?.textContent).toContain('diagonal polar plane');
  });

  it('renders summary groups, row titles, and a marked warning group', () => {
    const groups: SummaryGroup[] = [
      { title: 'Simulation', rows: [{ label: 'Engine', value: 'bempp-cl', title: 'Solver backend' }] },
      { title: 'Warnings', tone: 'warning', rows: [{ label: 'Mesh', value: 'Element quality fell below the requested threshold.' }] },
    ];
    const wrapper = { frequencies: [], channels: { hf: result } } as ResultPayload;
    summaryMocks.groups.mockReturnValue(groups);
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['summary'], result, named: [], tokens, wrapper, job: null, channelId: 'hf',
    })));

    expect([...host.querySelectorAll('.result-summary-group > h3')].map((heading) => heading.textContent)).toEqual(['Simulation', 'Warnings']);
    expect([...host.querySelectorAll('.result-summary dt')].map((label) => label.textContent)).toEqual(['Engine', 'Mesh']);
    expect([...host.querySelectorAll('.result-summary dd')].map((value) => value.textContent)).toEqual(['bempp-cl', 'Element quality fell below the requested threshold.']);
    expect(host.querySelector('.result-summary-row')?.getAttribute('title')).toBe('Solver backend');
    expect(host.querySelector('.result-summary-group[data-tone="warning"]')?.textContent).toContain('Warnings');
    expect(summaryMocks.groups).toHaveBeenCalledWith({ result, wrapper, job: null, channelId: 'hf' });
  });

  it('renders a quiet summary empty state when there are no groups', () => {
    act(() => root.render(createElement(ResultsChartGrid, { chartTypes: ['summary'], result, named: [], tokens })));
    expect(host.querySelector('.result-summary-empty')?.textContent).toBe('No summary details available.');
    expect(host.querySelector('.result-summary-copy')).toBeNull();
  });

  it('does not render validity caveats over result charts', () => {
    const above: ResultPayload = {
      frequencies: [100, 20_000],
      spl_on_axis: { frequencies: [100, 20_000], spl: [80, 85] },
      metadata: { source_ids: ['driver'] },
    };
    const wrapper = (channel: ResultPayload): ResultPayload => ({
      frequencies: [], channels: { drive: channel },
      metadata: { per_source_frequency_validity: { driver: { effective_max_valid_frequency_hz: 1_200 } } },
    });

    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['frequency_response'], result: above, named: [], tokens, wrapper: wrapper(above), channelId: 'drive',
    })));
    expect(host.querySelector('.result-caveat')).toBeNull();
  });

  it('copies the formatted summary text and confirms success', async () => {
    const groups: SummaryGroup[] = [{ title: 'Run', rows: [{ label: 'Name', value: 'OSSE v4' }] }];
    const writeText = vi.fn(async () => undefined);
    const originalClipboard = navigator.clipboard;
    summaryMocks.groups.mockReturnValue(groups);
    summaryMocks.text.mockReturnValue('Run\nName: OSSE v4');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    try {
      act(() => root.render(createElement(ResultsChartGrid, { chartTypes: ['summary'], result, named: [], tokens })));
      const copy = host.querySelector<HTMLButtonElement>('[aria-label="Copy simulation summary"]')!;
      await act(async () => { copy.click(); });
      expect(summaryMocks.text).toHaveBeenCalledWith(groups);
      expect(writeText).toHaveBeenCalledWith('Run\nName: OSSE v4');
      expect(copy.textContent).toBe('Copied');
      expect(copy.getAttribute('aria-label')).toBe('Simulation summary copied');
    } finally {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: originalClipboard });
    }
  });

  it('closes one card and persists the shorter chart list', () => {
    preferencesStore.update({ chartTypes: ['summary', 'impedance'] });
    act(() => root.render(createElement(ResultsHarness)));
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Close panel 1"]')!.click());
    expect(preferencesStore.getSnapshot().chartTypes).toEqual(['impedance']);
    expect(host.querySelectorAll('.result-card')).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem('waveguide-v2-g3-preferences') ?? '{}').preferences.chartTypes).toEqual(['impedance']);
  });

  it('offers an add-chart recovery when the last card is closed', () => {
    preferencesStore.update({ chartTypes: [] });
    act(() => root.render(createElement(ResultsHarness)));
    const add = host.querySelector<HTMLButtonElement>('.result-grid-empty button')!;
    expect(add.textContent).toContain('Add chart');
    act(() => add.click());
    expect(preferencesStore.getSnapshot().chartTypes).toHaveLength(1);
  });

  it('opens a full-size interactive detail view and closes it with Escape', () => {
    act(() => root.render(createElement(ResultsChartGrid, { chartTypes: ['summary'], result, named: [], tokens })));
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Expand panel 1"]')!.click());
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });

  it('names the active channel in a non-comparable chart badge', () => {
    const first = { frequencies: [500] };
    const active = { frequencies: [1_000] };
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['summary'],
      result: active,
      named: [named('job#hf', 'Run · drive-hf', first), named('job#mf', 'Run · drive-mf', active)],
      tokens,
    })));
    expect(host.querySelector('.result-single-run')?.getAttribute('title')).toContain('Showing Run · drive-mf.');
  });

  it('offers enable and rerun when beam shape is missing spherical sampling', () => {
    const onClick = vi.fn();
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['beam_shape'], result, named: [], tokens,
      beamShapeAction: { label: 'Enable & rerun', onClick },
    })));
    const button = host.querySelector<HTMLButtonElement>('.chart-stub button')!;
    expect(button.textContent).toBe('Enable & rerun');
    act(() => button.click());
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not offer a rerun when balloon data exists but the contour fit failed', () => {
    const balloonResult = {
      frequencies: [1_000], metadata: {},
      balloon: { frequencies: [1_000], theta_deg: [0, 90], phi_deg: [0, 120, 240], spl_norm_db: [[[0, 0, 0], [-2, -2, -2]]] },
    };
    expect(beamShapeMissingReason(balloonResult)).toEqual({
      reason: 'Spherical balloon data is available, but this result has no valid −6 dB contour fit.',
      canEnable: false,
    });
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['beam_shape'], result: balloonResult, named: [], tokens,
      beamShapeAction: { label: 'Enable & rerun', onClick: vi.fn() },
    })));
    expect(host.querySelector('.chart-stub button')).toBeNull();
  });
});
