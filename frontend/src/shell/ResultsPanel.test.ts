import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { preferencesStore, usePreferences, type ChartType } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import { ResultsChartGrid, resolvedPolarStepNotice, resultLayoutClass } from './ResultsPanel';

const tokens: ChartTokens = { foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff', series: ['#0ff'], colormap: ['#000', '#fff'] };
const result = { frequencies: [], metadata: {} };
function ResultsHarness() {
  const preferences = usePreferences();
  return createElement(ResultsChartGrid, { chartTypes: preferences.chartTypes, result, named: [], tokens });
}

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
});
