import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore, type ChartType } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { ResultPayload } from '../results/types';

const chartLabel = vi.hoisted(() => vi.fn());

vi.mock('../results/EChart', () => ({
  EChart: ({ label }: { label?: string }) => {
    chartLabel(label);
    return null;
  },
  useChartTokens: () => tokens,
}));

const { ResultsChartGrid } = await import('./ResultsPanel');

const tokens: ChartTokens = {
  foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff',
  series: ['#0ff'], colormap: ['#000', '#fff'],
};

/** A polar-bearing result over an arbitrary frequency grid. */
function polarResult(frequencies: number[]): NamedResult {
  return {
    id: 'run',
    label: 'Run A',
    result: {
      frequencies,
      directivity: { horizontal: frequencies.map(() => [[0, 0], [30, -6], [60, -12]]) },
    } as unknown as ResultPayload,
  };
}

function impedanceResult(id: string, electrical: boolean): NamedResult {
  return {
    id,
    label: id,
    result: {
      frequencies: [500],
      impedance: { frequencies: [500], real: [8], imaginary: [1] },
      metadata: electrical
        ? { impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance' }
        : { impedance_units: 'Z/(rho*c)', impedance_quantity: 'specific_acoustic_impedance' },
    } as unknown as ResultPayload,
  };
}

describe('results card chrome follows the data it is drawing', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    globalThis.ResizeObserver ??= class { observe() {} disconnect() {} unobserve() {} } as never;
    localStorage.clear();
    preferencesStore.resetForTests();
    chartLabel.mockClear();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => { act(() => root.unmount()); host.remove(); });

  function render(chartTypes: ChartType[], named: NamedResult[]) {
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes, result: named[0].result as ResultPayload, named, tokens,
    })));
  }

  const readout = () => host.querySelector('.frequency-scrub span')?.textContent;

  /**
   * Move the scrubber the way a user does.
   *
   * React patches the `value` setter to track what it last rendered, so a plain
   * `slider.value = x` updates that tracker too and the synthetic onChange is
   * deduped away. Going through the prototype setter leaves the tracker stale,
   * which is what makes React treat the following `input` as a real edit.
   */
  function scrubTo(value: number) {
    const slider = host.querySelector<HTMLInputElement>('[aria-label="Polar frequency"]')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(slider, String(value));
    act(() => { slider.dispatchEvent(new Event('input', { bubbles: true })); });
  }

  it('re-seeks 1 kHz once the provisional grid grows past it', () => {
    // A card mounted mid-solve seeks 1 kHz across whatever few frequencies have
    // landed, then keeps that index while the sweep fills in -- so it settles on
    // an arbitrary frequency and looks like a deliberate choice.
    render(['polar_response'], [polarResult([200, 400])]);
    expect(readout()).toBe('400 Hz');

    render(['polar_response'], [polarResult([200, 400, 1_000, 2_000])]);
    expect(readout()).toBe('1.00 kHz');
  });

  it('leaves a frequency the user picked alone when the grid grows', () => {
    render(['polar_response'], [polarResult([200, 400, 1_000, 2_000])]);
    expect(readout()).toBe('1.00 kHz');

    scrubTo(0);
    expect(readout()).toBe('200 Hz');

    render(['polar_response'], [polarResult([200, 400, 1_000, 2_000, 4_000, 8_000])]);
    expect(readout()).toBe('200 Hz');
  });

  it('clamps an index past the end of a shorter sweep', () => {
    render(['polar_response'], [polarResult([200, 400, 1_000, 2_000])]);
    scrubTo(3);
    expect(readout()).toBe('2.00 kHz');

    render(['polar_response'], [polarResult([200, 400])]);
    expect(readout()).toBe('400 Hz');
  });

  it('announces the impedance chart in the unit it is drawing', () => {
    render(['impedance'], [impedanceResult('Driver', true)]);
    expect(chartLabel).toHaveBeenCalledWith('Interactive HornLab electrical impedance by frequency');

    chartLabel.mockClear();
    render(['impedance'], [impedanceResult('Waveguide', false)]);
    expect(chartLabel).toHaveBeenCalledWith('Interactive HornLab normalized acoustic impedance by frequency');
  });

  it('names the runs the impedance axis had to leave off', () => {
    render(['impedance'], [impedanceResult('Driver', true), impedanceResult('Waveguide', false)]);
    expect(host.querySelector('.result-subtitle')?.textContent).toBe('1 Z/ρc run hidden · cannot share a Ω axis');
    expect(host.querySelector('.result-title em')?.textContent).toBe('Ω');
  });

  it('carries no impedance subtitle when every run shares the axis', () => {
    render(['impedance'], [impedanceResult('Driver', true), impedanceResult('Driver 2', true)]);
    expect(host.querySelector('.result-subtitle')).toBeNull();
  });
});
