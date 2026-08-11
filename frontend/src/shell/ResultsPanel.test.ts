import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore, usePreferences, type ChartType } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';
import { designForFamily, serializeDesign } from '../stores/design';
import { beamShapeMissingReason, COMPARABLE_CHARTS, directivityIndexOption, directivityMapPanels, impedanceOption, ResultsChartGrid, resolvedPolarStepNotice, resultExportSnapshot, resultLayoutClass } from './ResultsPanel';

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
      'directivity_map', 'directivity_index', 'impedance',
    ]);
  });

  it('builds one labelled heatmap panel per run and plane, including missing-plane notices', () => {
    expect(directivityMapPanels(items, 'directivity_map_h').map(({ label, plane, hasData }) => ({ label, plane, hasData }))).toEqual([
      { label: 'Run A', plane: 'horizontal', hasData: true },
      { label: 'Run B', plane: 'horizontal', hasData: true },
    ]);
    expect(directivityMapPanels(items, 'directivity_map').map(({ label, hasData }) => ({ label, hasData }))).toEqual([
      { label: 'Run A · horizontal', hasData: true },
      { label: 'Run A · vertical', hasData: true },
      { label: 'Run B · horizontal', hasData: true },
      { label: 'Run B · vertical', hasData: false },
    ]);
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
